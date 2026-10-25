# riff-retain

[![CI](https://github.com/KanadeK/riff-retain/actions/workflows/ci.yml/badge.svg)](https://github.com/KanadeK/riff-retain/actions/workflows/ci.yml)

Prove which WAVE chunks survived a metadata edit or export. `riff-retain` streams
SHA-256 receipts for audio and **opaque metadata**, then checks a new file against
the receipt. It reads your media locally and has no runtime dependencies.

Useful when a tagger changes `bext` correctly but silently drops `iXML`, sampler
metadata, or a second `LIST` chunk. Whole-file hashes cannot distinguish an approved
tag edit from this collateral loss. Audio-only hashes miss metadata loss.

## Install

Python 3.11+ on Windows, macOS, or Linux. The release is on GitHub, not PyPI:

```sh
python -m pip install "git+https://github.com/KanadeK/riff-retain.git@v0.1.0"
riff-retain --help
```

Alternatively clone this repository, run `python -m pip install .`, and use
`python -m riff_retain` if scripts are not on PATH.

## Complete demo

From a clone after installation:

```sh
python examples/demo.py
riff-retain compare .demo/before.json .demo/tagged.wav --allow-chunk bext
riff-retain compare .demo/before.json .demo/damaged.wav --allow-chunk bext
```

The first comparison exits **0**; the second exits **1** and identifies changed
`data`. The demo uses original synthetic PCM, not a recording. It refuses to
overwrite an existing `.demo` directory.

For your own file, save a receipt **before** editing:

```sh
riff-retain snapshot original.wav > before.json
# Run your existing editor/tagger to produce tagged.wav.
riff-retain compare before.json tagged.wav --allow-chunk bext --allow-chunk LIST:INFO
```

Windows PowerShell 5 users should save UTF-8 explicitly:
`riff-retain snapshot original.wav | Set-Content -Encoding utf8 before.json`.
Never redirect a report over a media file or your only baseline.

## Contract

- Exactly one `fmt ` chunk (at least 16 bytes) must precede exactly one `data`.
- All top-level payloads are hashed in bounded blocks; duplicate occurrences
  remain distinct. `LIST:INFO` and `LIST:adtl` are different kinds.
- Compare sizes and hashes in occurrence order **within each kind**. Moving
  different kinds around, changing RIFF sizes, offsets, or outer padding bytes is
  ignored. Changing the order of unequal same-kind chunks fails.
- By default added, removed, or changed kinds fail. Repeat `--allow-chunk` for
  exact kinds permitted to change. An unknown key is an error, catching typos.
- Audio (`data`) and format (`fmt `) can never be exempted. An exemption applies
  to **every occurrence** of that kind, including addition/removal.
- JSON contains only chunk keys, byte positions, sizes, and hashes, never payload
  text or absolute paths. Receipts are not signed authenticity proofs.

Exit status: **0** receipt created / contract satisfied; **1** unapproved change;
**2** malformed, unsupported, unreadable input or invalid options. JSON reports go
to stdout; operational errors go to stderr. Normal operation never writes media.

## Boundaries

v0.1.0 supports classic little-endian RIFF/WAVE only (the format's ~4 GiB limit).
RF64, Wave64, RIFX, multiple `data` chunks and incomplete streaming headers are
rejected. Up to 100,000 chunks and 32 MiB receipt JSON. It checks outer RIFF
boundaries, **not** codec validity, `LIST` children, BWF field rules or audible
equivalence. A short synthetic `bext` in the demo is intentionally opaque; it is
not a BWF-conformant metadata record. A valid receipt does not prove playability.
Scan immutable files: size/mtime changes are detected, but this is not a filesystem
snapshot or protection against a concurrent adversarial writer.

[BWF MetaEdit](https://mediaarea.net/BWFMetaEdit) is better for BWF field editing and
semantic validation; [wavdeets / former wavrw](https://github.com/briandorsey/wavrw)
is useful for inspecting parsed chunk contents. This tool focuses on repeatable
before/after acceptance contracts, including unknown chunks.

## Development and cleanup

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests cover standard-library-produced WAVs,
metadata loss, duplicate order, approved edits, payload mutations, all truncation
positions, malformed receipts, and the CLI workflow.

Uninstall: `python -m pip uninstall riff-retain`. Delete the demo directory and
receipts you created when no longer needed. There are no background services,
configuration folders, network requests, or caches created by the application.
MIT licensed; runtime implementation and test fixtures are original.

## 中文说明

这是 WAV 修改前后“保留了哪些块”的离线核验工具。先生成 JSON 基线，再修改标签，
最后比较：只有明确允许的元数据块可以变化，音频数据和格式块始终受到保护。
支持未知块、重复块和不同 LIST 类型；不读取元数据语义、不解码音频、不修改原文件。
当前仅支持普通 RIFF/WAVE，RF64 等格式会明确报错。退出码 0/1/2 分别表示通过、
发现未批准变化、输入错误；安装、演示和清理步骤见上文。
