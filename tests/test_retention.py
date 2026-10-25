import copy
import io
import json
import random
import struct
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from riff_retain.cli import main
from riff_retain.core import InputError, compare, load_receipt, snapshot, validate_receipt


def chunk(kind, payload):
    return kind + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


FMT = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
PCM = struct.pack("<8h", 0, 1, -1, 32767, -32768, 200, -300, 0)


def riff(parts):
    body = b"WAVE" + b"".join(chunk(key, value) for key, value in parts)
    return b"RIFF" + struct.pack("<I", len(body)) + body


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def scan(self, parts, name="clip.wav"):
        path = self.root / name
        path.write_bytes(riff(parts))
        return snapshot(path)

    def base(self, extra=()):
        return self.scan([(b"fmt ", FMT), *extra, (b"data", PCM)])

    def test_standard_library_wave_producer(self):
        path = self.root / "producer.wav"
        with wave.open(str(path), "wb") as out:
            out.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            out.writeframes(PCM)
        self.assertEqual(snapshot(path), self.base())

    def test_metadata_edit_passes_only_when_explicit(self):
        before = self.base([(b"bext", b"old title")])
        after = self.base([(b"bext", b"longer new title")])
        self.assertFalse(compare(before, after)["ok"])
        self.assertTrue(compare(before, after, ("bext",))["ok"])

    def test_audio_and_format_never_exempt(self):
        before = self.base()
        for key in ("data", "fmt "):
            with self.subTest(key=key), self.assertRaises(InputError):
                compare(before, before, (key,))
        for parts in (
            [(b"fmt ", FMT), (b"data", PCM[:-2])],
            [(b"fmt ", FMT[:-1] + b"\1"), (b"data", PCM)],
        ):
            self.assertFalse(compare(before, self.scan(parts))["ok"])

    def test_opaque_metadata_loss_and_addition(self):
        before = self.base([(b"iXML", b"<take>secret title</take>")])
        result = compare(before, self.base())
        self.assertFalse(result["ok"])
        self.assertEqual(result["changes"][0]["kind"], "removed")
        self.assertNotIn("secret title", json.dumps(result))
        self.assertEqual(compare(self.base(), before)["changes"][0]["kind"], "added")

    def test_list_types_do_not_share_an_exemption(self):
        info = b"INFO" + chunk(b"INAM", b"Name")
        labels = b"adtl" + chunk(b"labl", b"\1\0\0\0Marker")
        before = self.base([(b"LIST", info), (b"LIST", labels)])
        after = self.base([(b"LIST", info + chunk(b"IART", b"Artist"))])
        result = compare(before, after, ("LIST:INFO",))
        self.assertFalse(result["ok"])
        self.assertIn("LIST:adtl", [c["key"] for c in result["changes"] if not c["allowed"]])

    def test_duplicate_occurrences_are_not_collapsed(self):
        before = self.base([(b"JUNK", b"aaa"), (b"JUNK", b"bbb")])
        reordered = self.base([(b"JUNK", b"bbb"), (b"JUNK", b"aaa")])
        self.assertFalse(compare(before, reordered)["ok"])
        self.assertFalse(compare(before, self.base([(b"JUNK", b"aaa")]))["ok"])

    def test_different_kind_order_and_padding_are_ignored(self):
        before = self.base([(b"iXML", b"a"), (b"JUNK", b"b")])
        after = self.base([(b"JUNK", b"b"), (b"iXML", b"a")])
        self.assertTrue(compare(before, after)["ok"])
        path = self.root / "clip.wav"
        raw = bytearray(path.read_bytes())
        raw[45] = 0x55  # pad after the one-byte JUNK payload
        path.write_bytes(raw)
        self.assertTrue(compare(before, snapshot(path))["ok"])

    def test_unknown_binary_fourcc(self):
        receipt = self.base([(b"\0:xx", b"opaque")])
        self.assertEqual(receipt["chunks"][1]["key"], "0x003a7878")
        self.assertTrue(compare(receipt, receipt)["ok"])

    def test_receipt_roundtrip_and_bom(self):
        receipt = self.base()
        path = self.root / "before.json"
        path.write_text(json.dumps(receipt), encoding="utf-8-sig")
        self.assertEqual(load_receipt(path), receipt)

    def test_malformed_receipts(self):
        good = self.base()
        bads = [
            None,
            [],
            {},
            {**good, "schema": "later"},
            {**good, "size": True},
            {**good, "size": good["size"] + 1},
            {**good, "extra": 1},
            {**good, "chunks": []},
        ]
        for field, value in [
            ("key", "x"),
            ("key", "xxxx:yyyy"),
            ("key", "LIST"),
            ("offset", True),
            ("offset", 13),
            ("size", -1),
            ("sha256", "z" * 64),
            ("sha256", 1),
        ]:
            bad = copy.deepcopy(good)
            bad["chunks"][0][field] = value
            bads.append(bad)
        for bad in bads:
            with self.subTest(bad=bad), self.assertRaises(InputError):
                validate_receipt(bad)

    def test_duplicate_json_keys_and_bad_encoding(self):
        path = self.root / "bad.json"
        for raw in (b'{"schema":1,"schema":2}', b"\xff", b"[]", b"["):
            path.write_bytes(raw)
            with self.assertRaises(InputError):
                load_receipt(path)

    def test_bounded_receipt_read(self):
        path = self.root / "big.json"
        path.write_bytes(b" " * 101)
        with patch("riff_retain.core.MAX_RECEIPT_BYTES", 100), self.assertRaises(InputError):
            load_receipt(path)

    def test_invalid_wave_layouts(self):
        cases = [
            [],
            [(b"fmt ", FMT)],
            [(b"data", PCM)],
            [(b"data", PCM), (b"fmt ", FMT)],
            [(b"fmt ", FMT), (b"fmt ", FMT), (b"data", PCM)],
            [(b"fmt ", b"x"), (b"data", PCM)],
            [(b"fmt ", FMT), (b"data", PCM), (b"LIST", b"bad")],
        ]
        for parts in cases:
            with self.subTest(parts=parts), self.assertRaises(InputError):
                self.scan(parts)

    def test_every_truncation_fails(self):
        raw = riff([(b"fmt ", FMT), (b"JUNK", b"odd"), (b"data", PCM)])
        path = self.root / "truncated.wav"
        for end in range(len(raw)):
            path.write_bytes(raw[:end])
            with self.subTest(end=end), self.assertRaises(InputError):
                snapshot(path)

    def test_unsupported_containers_trailing_bytes_and_oversize_chunk(self):
        raw = riff([(b"fmt ", FMT), (b"data", PCM)])
        cases = [
            b"RF64" + raw[4:],
            b"RIFX" + raw[4:],
            raw + b"extra",
            raw[:16] + b"\xff\xff\xff\xff" + raw[20:],
        ]
        path = self.root / "bad.wav"
        for value in cases:
            path.write_bytes(value)
            with self.assertRaises(InputError):
                snapshot(path)

    def test_partial_chunk_header_and_missing_pad(self):
        raw = riff([(b"fmt ", FMT), (b"data", PCM)]) + b"abc"
        raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
        path = self.root / "bad.wav"
        path.write_bytes(raw)
        with self.assertRaises(InputError):
            snapshot(path)
        raw = riff([(b"fmt ", FMT), (b"data", PCM), (b"JUNK", b"x")])[:-1]
        raw = raw[:4] + struct.pack("<I", len(raw) - 8) + raw[8:]
        path.write_bytes(raw)
        with self.assertRaises(InputError):
            snapshot(path)

    def test_chunk_resource_limit_and_multi_block_audio(self):
        with patch("riff_retain.core.MAX_CHUNKS", 2), self.assertRaises(InputError):
            self.base([(b"JUNK", b"x")])
        with patch("riff_retain.core.BLOCK", 3):
            self.assertEqual(self.base(), self.base())

    def test_unknown_allow_key_cannot_hide_a_typo(self):
        with self.assertRaises(InputError):
            compare(self.base(), self.base(), ("bexT",))

    def test_seeded_metadata_mutations_never_pass(self):
        rng = random.Random(35)
        for _ in range(40):
            value = rng.randbytes(24)
            before = self.base([(b"iXML", value)])
            pos = rng.randrange(len(value))
            changed = value[:pos] + bytes([value[pos] ^ 1]) + value[pos + 1 :]
            self.assertFalse(compare(before, self.base([(b"iXML", changed)]))["ok"])

    def test_cli_full_flow_and_exit_codes(self):
        before = self.base([(b"bext", b"old")])
        path = self.root / "before.json"
        path.write_text(json.dumps(before), encoding="utf-8")
        wav = self.root / "clip.wav"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(main(["snapshot", str(wav)]), 0)
            self.assertEqual(main(["compare", str(path), str(wav)]), 0)
            self.base([(b"bext", b"new")])
            self.assertEqual(main(["compare", str(path), str(wav)]), 1)
            self.assertEqual(main(["compare", str(path), str(wav), "--allow-chunk", "bext"]), 0)
            self.assertEqual(main(["snapshot", str(self.root / "missing.wav")]), 2)
        self.assertIn("error", json.loads(err.getvalue()))


if __name__ == "__main__":
    unittest.main()
