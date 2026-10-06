# Open Video Archiver architecture

This document defines the stable architectural boundary for Open Video Archiver 1.0.

## Product principle

Open Video Archiver is not intended to become a general-purpose media library, channel monitor or feature-heavy yt-dlp frontend.

Its core task is:

> Acquire a single audiovisual online source and create a self-documenting, integrity-verifiable preservation package.

The archive model is provider-neutral. Source support is delegated to yt-dlp while preservation, provenance and verification remain application responsibilities.

## Module boundary

### `ova.py`

CLI presentation and interactive decisions:

- prompts and confirmations
- rights declaration
- destination selection
- archive-mode and quality selection
- resume / ZIP / deletion decisions
- console progress presentation
- human-readable output

### `ova_acquisition.py`

UI-independent source inspection:

- metadata inspection
- best-resolution calculation
- subtitle inventory and source-track policy
- video quality inventory
- audio quality inventory
- selected-format summary

### `ova_engine.py`

UI-independent acquisition orchestration:

- runtime preflight checks
- session creation and resume input
- media acquisition through yt-dlp
- progress/status callbacks
- best-effort subtitle acquisition
- provenance sidecars
- manifest + checksum finalization
- verified ZIP creation
- cancellation support
- safe deletion limited to verified ZIP members

It contains no `input()`, `print()` or Qt code.

### `ova_core.py`

UI-independent preservation and verification:

- provider-neutral source identity
- Capture ID support
- `MANIFEST.json` schema `ova-manifest/1.0`
- completeness record
- semantic file roles
- SHA-256 helpers
- manifest/checksum inventory validation
- directory verification
- ZIP verification
- privacy-safe executable-name recording

### `ova_gui.py` / `ova_gui_app.py`

Optional PySide6 desktop layer:

- source analysis
- acquisition mode / quality / destination selection
- rights confirmation
- resume detection
- threaded acquisition with progress reporting and cancellation
- optional verified ZIP creation
- archive verification
- About / scope information

PySide6 remains optional so CLI users do not need Qt.

## Core feature scope

- single-source acquisition
- source metadata inspection
- video+audio, video-only and audio-only modes
- quality selection from formats reported by yt-dlp
- source metadata, description and thumbnail preservation
- best-effort source subtitles
- resume support and preservation of partial downloads
- explicit user rights declaration
- `SESSION.json`
- `METADATA.md`
- `SOFTWARE.md`
- `SOURCE.url`
- UUID-based Capture ID
- entered and canonical source URLs
- provider / extractor / source ID fields
- versioned `MANIFEST.json`
- completeness status
- semantic file roles
- SHA-256 integrity manifest
- immediate post-capture checksum verification
- optional verified ZIP container with CRC/member verification
- SHA-256 checksum for the final ZIP
- deletion only after successful verification and explicit confirmation
- `ova verify <directory-or-zip>`
- avoidance of unnecessary private absolute paths in generated provenance

## Deliberately out of scope

- media-library or playback features
- channel / playlist monitoring
- subscription management
- browser automation or generic DOM scraping
- DRM circumvention
- source modification
- comment archiving
- PDF capture certificates
- digital signatures / key management
- case-management database
- automatic capture comparison

## Desktop architecture

```text
                  ova_acquisition
                        |
                   ova_engine
                   /        \
             CLI (ova)    PySide6 GUI
                   \        /
                    ova_core
```

The GUI must not implement a second acquisition, manifest or verification engine.

The intended Windows distribution is portable: one extracted folder, no installer required, with a PyInstaller-built executable and bundled runtime components.

## Source expansion rule

The preferred rule is:

> If the installed yt-dlp can resolve a single online-video source, Open Video Archiver may preserve it.

The project should not become a browser-automation framework merely to support arbitrary websites.
