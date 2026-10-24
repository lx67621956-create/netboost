# netboost · Multi-Connection Segmented Downloader

> **Breaks through per-connection throttling.**
> Paste a link → it detects what the file is → picks the right folder → you just confirm.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D4.svg)]()
[![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)]()

**English** · [简体中文](README.md)

![Main window](docs/gui-screenshot.png)

---

## What it is

A **multi-connection segmented downloader**: it splits a file into many chunks, pulls them over several connections in parallel, then reassembles the complete file.

Many servers, cloud drives, and mirror sites **throttle per connection** (e.g. 300 KB/s per connection). Open N connections and the aggregate speed approaches N×, until your physical bandwidth is saturated.

**It is not a cracking tool, and it does not bypass account-level throttling** — see [Honest Limitations](#honest-limitations-what-it-cannot-do).

---

## Measured results

Local test server with **256 KB/s per-connection throttling**, downloading a 6 MB file:

| Connections | Measured speed | Time |
|---|---|---|
| 1 | 257 KB/s | 15.9s |
| **8** | **1.53 MB/s** | **2.6s** |

**6.1× speedup**, and the downloaded file's sha256 matches the source exactly.

> Real-world speed depends on your **physical bandwidth** and the server's **per-connection limit** — whichever caps first wins.
> On a 500 Mbps link (~62 MB/s), 8 connections saturate it as long as the server gives ≥8 MB/s per connection.

---

## Quick start

### Option 1: Download the exe (no Python required)

Get them from **[Releases](https://github.com/lx67621956-create/netboost/releases)**:

| File | Purpose |
|---|---|
| [`netboost-gui-windows-x64.exe`](https://github.com/lx67621956-create/netboost/releases/latest) | **GUI (recommended)**, double-click to run · 10.5 MB |
| [`netboost-cli-windows-x64.exe`](https://github.com/lx67621956-create/netboost/releases/latest) | Command line · 7.5 MB |

Single-file portable executables — copy them to any other Windows machine and run.

> First launch takes 1–3 seconds (single-file exes unpack their runtime), which is normal.

### Option 2: Run from source

```bash
git clone https://github.com/lx67621956-create/netboost.git
cd netboost
python app.py          # GUI
python cli.py URL      # CLI
```

Standard library + Tkinter only — **no `pip install` needed at all**.

---

## How to use it

The whole flow is three steps:

```
Paste link  →  auto-detect type  →  confirm
```

Paste something like `...Interstellar.2014.2160p.mkv` and it immediately tells you:

> **▶ Video** | Basis: URL filename extension `.mkv`
> Save to: `C:\Users\you\Downloads\Video`

Don't want to change anything? Just press Enter. Want a different type? Click another category and the folder follows instantly. Want a different location? Click "Choose…".

![Confirm dialog](docs/confirm-screenshot.png)

### The 5 auto-detected categories

| Type | Extensions (examples) | Default folder |
|---|---|---|
| ▶ Video | mp4 mkv avi mov wmv rmvb ts… | `Downloads\Video` |
| ♪ Music | mp3 flac wav aac m4a ogg ape… | `Downloads\Music` |
| ▤ Document | doc docx xls xlsx ppt pptx pdf txt csv epub… | `Downloads\Document` |
| ⚙ Program | exe msi apk dmg zip rar 7z iso… | `Downloads\Program` |
| ■ Other | jpg png bin dat… | `Downloads\Other` |

Detection order: **filename extension → server Content-Type → URL path → fallback**.

Change any category folder under **Settings → Category Folders**; it persists (stored in `%APPDATA%\netboost\config.json`).

![Settings](docs/settings-screenshot.png)

### Subtitle pairing for video

When a file is detected as video, the confirm dialog gains a **subtitle** row. Enter subtitle URLs (space-separated) or pick local subtitle files.

After download, subtitles are **automatically renamed to match the video** and placed in the same folder, so players load them automatically:

```
Downloads\Video\
├─ Interstellar.2014.1080p.mkv
└─ Interstellar.2014.1080p.srt      ← auto-loaded by the player
```

Multi-language subtitles get a language suffix and don't overwrite each other: `.chs.srt` / `.eng.srt`.
srt / ass / ssa / vtt / sub / idx / smi are all detected — **ASS is never mislabeled as SRT**.

### Command line

```bash
# Simplest
python cli.py https://example.com/big.iso

# Custom folder + more connections
python cli.py URL -o D:\Downloads -c 16 -m 64

# Rate limit 5 MB/s
python cli.py URL --rate-limit 5M

# Batch (one URL per line)
python cli.py -i urls.txt -o D:\Downloads

# Machine-readable output
python cli.py URL --json
```

| Flag | Meaning | Default |
|---|---|---|
| `-o, --out-dir` | Save directory | current dir |
| `-c, --connections` | Initial connections | 8 |
| `-m, --max-connections` | Adaptive ramp-up ceiling | 32 |
| `--rate-limit` | Total limit, e.g. `5M` / `800K` | unlimited |
| `--no-resume` | Ignore existing progress | resume on |
| `--no-adaptive` | Disable adaptive ramp-up | enabled |
| `--referer` / `--header` | Custom request headers | — |
| `--insecure` | Skip certificate validation | off |

---

## Feature details

### Multi-connection acceleration
- Starts at 8 connections, ceiling 32 (configurable)
- **Adaptive ramp-up**: samples speed every 1.5s, adds connections while speed is still climbing, and stops once adding more no longer helps — no blind connection hoarding
- Falls back to a single connection with a clear notice when the server doesn't support Range

### Resumable downloads
- Per-chunk progress is written to `<filename>.netboost.json` as it downloads
- After an interruption (window closed / network drop / Ctrl+C / cancel), re-adding the same URL resumes automatically
- Before resuming, total size and ETag are compared; if the source changed it restarts from scratch — it never assembles a corrupt file

### Integrity verification
When the server's `ETag` happens to be a 32-hex-char MD5 (common on CDNs), the file is verified after download and a mismatch is clearly reported.

### Pause / resume / rate limit
- Pause anytime; state is preserved and resumes where it left off
- Global token-bucket limiter shared across all connections

---

## Browser integration (optional, off by default)

**Settings → Browser Integration**:

- "Enable netboost:// link protocol" — **unchecked by default**; registering happens only when you check it, and unchecking deletes it immediately
- Writes only to `HKEY_CURRENT_USER`, **no admin rights required**, touches nothing else in the system
- Lists installed browsers and shows the integration options for each

![Browser integration](docs/browser-screenshot.png)

### About "set as default downloader"

To be honest: **Chrome, Edge, 360, and QQ Browser are all Chromium-based, and they do not expose an official API for changing the default downloader.**

Anything claiming to one-click replace them is really modifying file associations or installing an extension — heavy side effects, hard to undo, and often reset by browser updates.

So netboost does this instead:
- ✅ Provides a `netboost://` protocol so web links can launch the app directly
- ✅ Copy the link → paste into netboost
- ❌ Does not forcibly modify your browser configuration

**To fully revert**: uncheck it in Settings → Save, and the registry keys are deleted.

---

## Verification

Everything below was actually measured, not claimed. Reproduce with `python tests/verify.py` and friends:

| Test file | Coverage | Result |
|---|---|---|
| `tests/verify.py` | Speedup / integrity / resume / single-connection fallback / 404 / mid-transfer disconnect retry / pause-resume / small file / zero-byte file / CJK filename / rate limit / MD5 | **12/12** |
| `tests/test_auto_detect.py` | 5-category detection + auto folder + protocol reversibility | **30/30** |
| `tests/test_features.py` | Category folders + subtitle pairing + local import | **28/28** |
| `tests/gui_e2e.py` | Drives the real GUI to download and verifies the file | pass |
| `tests/test_exe.py` | Packaged exe launch check | pass |
| `tests/smoke.py` | Quick smoke test (throttled server + speedup) | pass |

```bash
python tests/verify.py            # engine, 12 checks
python tests/test_auto_detect.py  # detection + protocol, 30 checks
python tests/test_features.py     # categories + subtitles, 28 checks
```

---

## Honest limitations: what it cannot do

This section matters — it prevents misunderstandings.

### ❌ It does not bypass account-level throttling

If a cloud drive enforces **server-side throttling per account/IP** (e.g. 100 KB/s for free users), no client can change that — it's a paid-tier entitlement, not a technical problem. This tool does not crack it and doesn't intend to.

### ❌ There is no "one-click browser default downloader replacement"

See [Browser integration](#browser-integration-optional-off-by-default) above. This is a browser-vendor restriction, not laziness on this project's part.

### ❌ It is not a BitTorrent / magnet client

No P2P protocol is implemented.

### ❌ It is not a video-site ripper

No parsing, no watermark removal, no paywall bypass.

### ⚠️ Don't max out the connection count

Opening 64 connections against a small site is abusive and may get your IP temporarily blocked. The default of 8 is usually enough; don't blindly raise `-m`.

### ⚠️ Only download what you have the right to download

This is a neutral technical tool, suitable for open-source software, publicly licensed content, files on your own servers, and similar uses. **You** are responsible for ensuring your downloads are lawful.

---

## Project layout

```
netboost/
├─ core.py          Download engine: segmentation, scheduling, resume, ramp-up, rate limit, MD5
├─ detect.py        File-type detection (extension + MIME + URL, multi-layered)
├─ app.py           Tkinter GUI
├─ cli.py           Command line
├─ subtitle.py      Subtitle download, format sniffing, same-name renaming
├─ browser.py       Browser protocol registration (reversible, current user only)
├─ config.py        Config persistence
├─ app.ico          App icon
├─ docs/            Screenshots
├─ tests/           Test suite
├─ 启动.bat         Double-click to launch the GUI (from source)
├─ 打包exe.bat      One-click exe build
└─ PRODUCT.md       Product overview
```

About **2,400 lines** of source; the core engine `core.py` is ~780 lines.

### Key implementation notes

- Range requests with `Accept-Encoding: identity` for byte-exact chunks
- Chunks split in half automatically after repeated failures, easing per-request pressure
- Global token bucket shared across all connections
- Automatic MD5 verification when `ETag` is a 32-hex MD5
- RFC 5987 support for CJK filenames (`filename*=UTF-8''...`)

---

## Building the exe yourself

```bash
pip install pyinstaller
python 打包exe.bat
```

Or manually:

```bash
pyinstaller --onefile --windowed --icon app.ico --add-data "app.ico;." app.py
```

---

## Using it as a library

```python
from core import Downloader

d = Downloader("https://example.com/big.iso", out_dir="D:/Downloads",
               connections=8, max_connections=32)
d.probe()
print(d.filename, d.total, d.supports_ranges)

result = d.run(on_progress=lambda dl: print(dl.progress_line()))
print(result.path, result.avg_speed, result.md5_verified)
```

---

## Requirements

- **Windows 10/11** (the GUI uses the bundled Tkinter)
- **Python 3.9+** (source only; the exe needs no Python)
- No third-party packages required

The core engine `core.py` is cross-platform and can be used as a library on Linux / macOS.

---

## License

[MIT](LICENSE) — free to use, modify, and use commercially; keep the copyright notice.

---

## One-line summary

> It doesn't steal. It just uses the bandwidth you already paid for.
