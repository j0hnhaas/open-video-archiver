# Open Video Archiver

> **Preserve online video. Document the source. Verify the integrity. Built for journalism, research, OSINT and digital investigations.**

**Open Video Archiver** creates documented, integrity-checked preservation packages from online video sources that the user is authorized to preserve.

It is designed for **journalists, researchers, OSINT investigators, fact-checkers, archivists, documentary teams and digital-investigation workflows** that need more than a downloaded media file. Open Video Archiver preserves source context, technical provenance and integrity information alongside the captured material.

```text
Online video URL
    ↓
source metadata + format inspection
    ↓
video/audio acquisition with resume
    ↓
best-effort source subtitles
    ↓
provenance + metadata record
    ↓
SHA-256 integrity manifest
    ↓
optional verified ZIP container
```

## Why Open Video Archiver?

A normal downloader answers: *Can I save this video?*

Open Video Archiver is built around a different question:

> *Can I preserve this material in a way that remains understandable and verifiable later?*

An archive package can contain:

- the selected video/audio material
- source metadata (`.info.json`)
- source description
- source thumbnail
- `SOURCE.url` with the preserved source URL
- `METADATA.md` with source, retrieval and session information
- `SOFTWARE.md` with the exact software environment and licenses
- available source subtitles on a best-effort basis
- `SESSION.json` for resume and audit context
- `MANIFEST.json` with Capture ID, source identity, completeness record and semantic file roles
- `SHA256SUMS.txt` for file-integrity verification
- optionally a verified ZIP container plus a separate SHA-256 checksum

## Source support

Open Video Archiver uses **yt-dlp** as its extraction and acquisition engine. Source support therefore follows the providers and extractors available in the installed yt-dlp version.

The application itself keeps its archive model provider-neutral: a capture records the provider/extractor, source ID, entered URL and canonical retrieval URL without binding the archive format to one platform.

Open Video Archiver intentionally focuses on **single online-video sources**. It is not a channel monitor, subscription manager or media-library application.

## Intended use

Open Video Archiver can support work such as:

- preserving publicly relevant online video for journalistic research
- documenting audiovisual sources used in investigations
- maintaining reproducible research or OSINT collections
- capturing source metadata before content changes or disappears
- creating integrity-checked working copies for fact-checking
- archiving your own or otherwise lawfully obtainable video material

**Open Video Archiver is not a legal-admissibility or authenticity certification system.** It documents acquisition provenance and archive integrity; it does not independently prove authorship, source authenticity, truthfulness of content or admissibility as evidence.

## Responsible use

The application requires the user to confirm that they are authorized to download and archive the selected material. The declaration is recorded but is not independently assessed.

Users remain responsible for copyright, contractual restrictions, privacy, data protection, source protection and any other applicable rules.

## Requirements

For source installation:

- Python 3.10+
- FFmpeg available in `PATH`
- Deno 2.3+ available in `PATH`
- Internet access

The Windows portable build bundles the required runtime components and does not require a separate Python installation on the target computer.

## Install from source

Clone the repository and open PowerShell in the project directory.

Recommended Windows setup:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The installer registers the command:

```powershell
ova
```

Verify the installation:

```powershell
ova --version
```

## Command-line usage

Interactive start:

```powershell
ova
```

Archive a source:

```powershell
ova "<VIDEO_URL>"
```

Use a custom archive root:

```powershell
ova "<VIDEO_URL>" -o "D:\Video-Archive"
```

Verify an existing archive directory or ZIP:

```powershell
ova verify "D:\Video-Archive\capture"
ova verify "D:\Video-Archive\capture.zip"
```

At any point:

```text
Press Ctrl+C at any time to cancel safely.
Partial downloads are preserved for resume.
```

## Desktop application

The optional PySide6 desktop interface uses the same acquisition, manifest and verification engine as the CLI.

Install the GUI extra:

```powershell
python -m pip install -e ".[gui]"
```

Start it with:

```powershell
python -m ova_gui
```

or:

```powershell
ova-gui
```

The desktop application includes:

- source inspection before acquisition
- mode and quality selection
- archive destination selection
- explicit rights confirmation
- resume detection for incomplete acquisitions
- threaded acquisition with progress and safe cancellation
- optional verified ZIP creation
- independent verification of archive folders and ZIP containers

## Windows portable build

Build a portable Windows application with:

```powershell
.\build-windows.ps1
```

The build creates a self-contained `--onedir` package:

```text
dist\Open-Video-Archiver-1.0-Windows-x64-Portable\
dist\Open-Video-Archiver-1.0-Windows-x64-Portable.zip
dist\Open-Video-Archiver-1.0-Windows-x64-Portable.zip.sha256
```

The portable folder contains `OpenVideoArchiver.exe`, the Python/Qt runtime, FFmpeg/FFprobe, Deno and the relevant project/license documentation.

Useful switches:

```powershell
.\build-windows.ps1 -SkipInstall
.\build-windows.ps1 -NoZip
```

## Resume and fail-safe behavior

Downloads use partial files and resume support. If a run is interrupted, Open Video Archiver preserves partial data and `SESSION.json`.

When the same source ID is encountered later, the application can resume an incomplete session rather than silently replacing it.

Automatic deletion never occurs after an error or interruption.

## Subtitles

Subtitles are **best-effort enrichment**, not a gate for the core archive.

The application first completes the media acquisition and then attempts to preserve:

- manually supplied source subtitle tracks
- an original-language automatic caption track when it can be identified reliably

Automatically translated variants are deliberately excluded from the main workflow.

## Integrity

After retrieval, Open Video Archiver generates SHA-256 hashes for the archive contents and verifies them immediately.

If ZIP packaging is requested, ZIP is used as a **container**, not as an additional media-compression step. The application verifies ZIP readability, CRC and the expected member list, then creates a separate SHA-256 checksum for the ZIP itself.

Existing archive directories and ZIP containers can later be re-verified with `ova verify`.

## Capture identity and manifest

Every new capture receives a random UUID-based **Capture ID**. It identifies the acquisition session; it is not an authenticity claim or cryptographic proof of source authorship.

`MANIFEST.json` uses the versioned schema `ova-manifest/1.0` and records:

- entered and canonical source URLs
- provider / extractor and source ID
- acquisition mode, quality and selected format information
- completeness status for media, metadata, description, thumbnail and subtitles
- semantic roles for archive files
- the SHA-256 integrity-manifest relationship

## Privacy-safe provenance

Generated archive records avoid unnecessary local identifiers. Open Video Archiver does not intentionally retain private absolute archive paths, credentials, cookies or authentication tokens in the manifest.

Because the entered source URL itself is preserved, users should not supply secret-bearing URLs unless they intentionally want that URL recorded.

## Software provenance

Every archive receives a `SOFTWARE.md` recording the software actually used, including Python, yt-dlp, yt-dlp-ejs, Deno, curl_cffi and FFmpeg.

## License

Open Video Archiver is released under the **MIT License**.

Copyright (c) 2026 John G. Haas.

See `LICENSE` and `SOFTWARE.md`.

## Author

**John G. Haas**

Source: https://github.com/j0hnhaas/open-video-archiver
