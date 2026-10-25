"""Opaque RIFF chunk receipts, not a decoder or a BWF semantic validator."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
from collections import defaultdict
from pathlib import Path

SCHEMA = "riff-retain/1"
MAX_CHUNKS = 100_000
MAX_RECEIPT_BYTES = 32 * 1024 * 1024
BLOCK = 1024 * 1024
PROTECTED = {"fmt ", "data"}


class InputError(ValueError):
    """Unsupported, inconsistent, or malformed input."""


def _label(raw: bytes) -> str:
    if all(32 <= c <= 126 for c in raw) and b":" not in raw:
        return raw.decode("ascii")
    return "0x" + raw.hex()


def snapshot(path: str | Path) -> dict:
    """Stream every top-level payload; store no payload bytes or absolute paths."""
    chunks = []
    with Path(path).open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise InputError("input must be a regular file")
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise InputError("only classic little-endian RIFF/WAVE is supported (no RF64/RIFX)")
        declared = struct.unpack_from("<I", header, 4)[0] + 8
        if declared != before.st_size:
            raise InputError("RIFF size does not match file length; truncated/trailing data")
        offset = 12
        while offset < declared:
            if len(chunks) >= MAX_CHUNKS:
                raise InputError("chunk count exceeds limit")
            raw = stream.read(8)
            if len(raw) != 8:
                raise InputError(f"truncated chunk header at byte {offset}")
            chunk_id, size = struct.unpack("<4sI", raw)
            end = offset + 8 + size + (size & 1)
            if end > declared:
                raise InputError(f"chunk at byte {offset} exceeds RIFF boundary")
            key = _label(chunk_id)
            digest = hashlib.sha256()
            remaining = size
            if chunk_id == b"LIST":
                if size < 4:
                    raise InputError("LIST must contain a four-byte list type")
                form = stream.read(4)
                if len(form) != 4:
                    raise InputError("file changed during read")
                key += ":" + _label(form)
                digest.update(form)
                remaining -= 4
            while remaining:
                data = stream.read(min(remaining, BLOCK))
                if not data:
                    raise InputError("file truncated during read")
                digest.update(data)
                remaining -= len(data)
            if size & 1 and len(stream.read(1)) != 1:
                raise InputError("missing chunk padding byte")
            chunks.append(
                {"key": key, "offset": offset, "size": size, "sha256": digest.hexdigest()}
            )
            offset = end
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise InputError("file changed during scan; retry on an immutable copy")
    result = {"schema": SCHEMA, "size": declared, "chunks": chunks}
    validate_receipt(result)
    return result


def _integer(value: object) -> bool:
    return type(value) is int and value >= 0


def validate_receipt(receipt: object) -> dict:
    """Validate a receipt as untrusted data before using it for a contract."""
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise InputError("unknown receipt schema")
    if set(receipt) != {"schema", "size", "chunks"}:
        raise InputError("unexpected receipt fields")
    chunks = receipt["chunks"]
    if not _integer(receipt["size"]) or not 12 <= receipt["size"] <= 0xFFFFFFFF + 8:
        raise InputError("invalid receipt file size")
    if not isinstance(chunks, list) or not 2 <= len(chunks) <= MAX_CHUNKS:
        raise InputError("invalid receipt chunk count")
    counts: dict[str, list] = defaultdict(list)
    offset = 12
    for item in chunks:
        if not isinstance(item, dict) or set(item) != {"key", "offset", "size", "sha256"}:
            raise InputError("invalid chunk record")
        key = item["key"]
        if not isinstance(key, str) or not re.fullmatch(
            r"(?:[ -9;-~]{4}|0x[0-9a-f]{8})(?::(?:[ -9;-~]{4}|0x[0-9a-f]{8}))?", key
        ):
            raise InputError("invalid chunk key")
        if (":" in key) != key.startswith("LIST:") or key == "LIST":
            raise InputError("only LIST chunk keys contain a list type")
        if not _integer(item["offset"]) or item["offset"] != offset:
            raise InputError("receipt chunk offsets are not contiguous")
        if not _integer(item["size"]) or item["size"] > 0xFFFFFFFF:
            raise InputError("invalid chunk size")
        if key.startswith("LIST:") and item["size"] < 4:
            raise InputError("invalid LIST size")
        if not isinstance(item["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", item["sha256"]):
            raise InputError("invalid SHA-256")
        counts[key].append(item)
        offset += 8 + item["size"] + (item["size"] & 1)
    if offset != receipt["size"]:
        raise InputError("receipt chunk lengths do not match file size")
    if len(counts["fmt "]) != 1 or len(counts["data"]) != 1:
        raise InputError("exactly one fmt and one data chunk required")
    if counts["fmt "][0]["size"] < 16 or counts["fmt "][0]["offset"] > counts["data"][0]["offset"]:
        raise InputError("fmt must have at least 16 bytes and precede data")
    return receipt


def _unique(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate JSON object key")
        result[key] = value
    return result


def load_receipt(path: str | Path) -> dict:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if len(raw) > MAX_RECEIPT_BYTES:
        raise InputError("receipt exceeds 32 MiB limit")
    try:
        receipt = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputError(f"invalid receipt JSON: {exc}") from exc
    return validate_receipt(receipt)


def compare(before: dict, after: dict, allow: tuple[str, ...] = ()) -> dict:
    """Compare ordered occurrences per kind; changes in physical layout are ignored."""
    validate_receipt(before)
    validate_receipt(after)
    left: dict[str, list] = defaultdict(list)
    right: dict[str, list] = defaultdict(list)
    for receipt, groups in ((before, left), (after, right)):
        for chunk in receipt["chunks"]:
            groups[chunk["key"]].append({"size": chunk["size"], "sha256": chunk["sha256"]})
    keys = set(left) | set(right)
    for key in allow:
        if key in PROTECTED:
            raise InputError("fmt and data cannot be exempted")
        if key not in keys:
            raise InputError(f"allowed chunk key does not occur: {key!r}")
    changes = []
    for key in sorted(keys):
        if left[key] != right[key]:
            changes.append(
                {
                    "key": key,
                    "allowed": key in allow,
                    "kind": "added"
                    if not left[key]
                    else "removed"
                    if not right[key]
                    else "changed",
                    "before": left[key],
                    "after": right[key],
                }
            )
    return {
        "schema": "riff-retain-comparison/1",
        "ok": all(c["allowed"] for c in changes),
        "allowed_keys": sorted(set(allow)),
        "changes": changes,
    }
