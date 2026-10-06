"""UI-independent acquisition engine for Open Video Archiver.

The CLI and desktop GUI use this module for the same preservation workflow.
It deliberately contains no input(), print() or Qt code.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ova_acquisition import (
    selected_format_summary,
    subtitle_inventory,
    subtitle_type_label,
)
from ova_constants import (
    APP_NAME,
    APP_VERSION,
    REPO_URL,
    RIGHTS_STATEMENT,
)
from ova_core import (
    privacy_safe_executable,
    sha256_file,
    source_identity,
    verify_archive,
    write_json,
    write_manifest,
)

CHUNK_SIZE = 1024 * 1024
SUBTITLE_EXTENSIONS = {
    ".vtt", ".srt", ".ass", ".ssa", ".lrc", ".ttml", ".srv1", ".srv2", ".srv3", ".json3"
}
MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac"
}


class ArchiveEngineError(RuntimeError):
    pass


class ArchiveCancelled(ArchiveEngineError):
    pass


@dataclass(slots=True)
class CancellationToken:
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise ArchiveCancelled("Vorgang wurde vom Benutzer abgebrochen.")


@dataclass(slots=True)
class ArchiveCallbacks:
    status: Callable[[str, str], None] = field(default=lambda stage, message: None)
    progress: Callable[[dict[str, Any]], None] = field(default=lambda data: None)
    hash_progress: Callable[[int, int, str], None] = field(default=lambda index, total, path: None)
    zip_progress: Callable[[int, int], None] = field(default=lambda processed, total: None)


@dataclass(slots=True)
class RuntimeEnvironment:
    ffmpeg: dict[str, str]
    deno: dict[str, str]
    dependencies: dict[str, str]


@dataclass(slots=True)
class ResumeCandidate:
    folder: Path
    session: dict[str, Any]
    part_files: list[Path]


@dataclass(slots=True)
class ArchiveRequest:
    source_info: dict[str, Any]
    entered_url: str
    canonical_url: str
    output_root: Path
    mode: str
    quality_label: str
    format_selector: str
    subtitle_languages: list[str]
    subtitle_types: dict[str, str]
    rights_confirmed_at: str
    create_zip: bool = False
    resume_candidate: ResumeCandidate | None = None


@dataclass(slots=True)
class ArchiveResult:
    folder: Path
    capture_id: str
    checksum_file: Path
    files_count: int
    total_size_bytes: int
    download_elapsed_seconds: float
    resumed: bool
    subtitle_status: str
    zip_path: Path | None = None
    zip_checksum: Path | None = None
    zip_elapsed_seconds: float | None = None


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso_local(dt: datetime | None = None) -> str:
    return (dt or now_local()).isoformat(timespec="seconds")


def human_bytes(value: float | int | None) -> str:
    if value is None:
        return "?"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "?"
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def sanitize_component(text: str, max_len: int = 100) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", text)
    text = re.sub(r"\s+", " ", text).strip().strip(". ")
    text = re.sub(r"-{2,}", "-", text)
    return (text or "untitled")[:max_len].rstrip(". ")


def normalize_url(value: str) -> str:
    """Normalize and validate a single online-video source URL."""
    value = value.strip()

    # Be forgiving when a Markdown-style link was pasted into the field.
    markdown = re.fullmatch(r"\[(https?://[^\]]+)\]\((https?://[^)]+)\)", value)
    if markdown:
        value = markdown.group(2)

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ArchiveEngineError(
            "Bitte eine gültige Video-URL eingeben."
        )

    return value


def get_yt_dlp_version() -> str:
    try:
        from yt_dlp.version import __version__
        return __version__
    except Exception:
        try:
            import yt_dlp
            return getattr(yt_dlp, "__version__", "unknown")
        except Exception:
            return "unknown"


def package_version_safe(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return ""


def application_root() -> Path:
    """Return the application directory for source and frozen builds."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def portable_tool_directories(root: Path | None = None) -> list[Path]:
    """Return bundled tool directories beside a portable application build."""
    base = (root or application_root()).resolve()
    candidates = [
        base / "tools" / "ffmpeg",
        base / "tools" / "deno",
    ]
    return [path for path in candidates if path.is_dir()]


def configure_runtime_path(root: Path | None = None) -> list[Path]:
    """Prepend bundled runtime-tool folders to PATH.

    Source checkouts continue to use the normal system PATH. Portable Windows
    builds can place FFmpeg/FFprobe and Deno in tools/ beside the executable.
    """
    directories = portable_tool_directories(root)
    if not directories:
        return []

    existing = os.environ.get("PATH", "")
    existing_parts = [part for part in existing.split(os.pathsep) if part]
    existing_norm = {os.path.normcase(os.path.abspath(part)) for part in existing_parts}

    prepend: list[str] = []
    for directory in directories:
        text = str(directory)
        norm = os.path.normcase(os.path.abspath(text))
        if norm not in existing_norm:
            prepend.append(text)
            existing_norm.add(norm)

    if prepend:
        os.environ["PATH"] = os.pathsep.join(prepend + existing_parts)

    return directories


def ffmpeg_details() -> dict[str, str]:
    configure_runtime_path()
    executable = shutil.which("ffmpeg")
    if not executable:
        return {
            "available": "no",
            "path": "",
            "version": "",
            "configuration": "",
            "license": "",
        }

    proc = subprocess.run(
        [executable, "-version"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    lines = (proc.stdout or proc.stderr or "").splitlines()
    first = lines[0].strip() if lines else "ffmpeg (version unknown)"
    config = next((line.strip() for line in lines if line.startswith("configuration:")), "")

    if "--enable-gpl" in config:
        license_text = (
            "GPL v2 or later for GPL-enabled FFmpeg builds; exact effective terms can "
            "depend on enabled components/build configuration"
        )
    else:
        license_text = (
            "LGPL v2.1 or later by default; exact effective terms can depend on "
            "enabled components/build configuration"
        )

    return {
        "available": "yes",
        "path": executable,
        "version": first,
        "configuration": config,
        "license": license_text,
    }


def deno_details() -> dict[str, str]:
    configure_runtime_path()
    executable = shutil.which("deno")
    if not executable:
        return {"available": "no", "path": "", "version": ""}

    proc = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        errors="replace",
        check=False,
    )
    first = (proc.stdout or proc.stderr or "").splitlines()
    version_line = first[0].strip() if first else "deno (version unknown)"
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version_line)
    if match:
        major = int(match.group(1))
        minor = int(match.group(2))
        if (major, minor) < (2, 3):
            raise ArchiveEngineError(
                f"Deno ist vorhanden ({version_line}), aber yt-dlp benötigt für EJS "
                "mindestens Deno 2.3. Bitte Deno aktualisieren."
            )

    return {"available": "yes", "path": executable, "version": version_line}


def preflight() -> RuntimeEnvironment:
    configure_runtime_path()
    ffmpeg = ffmpeg_details()
    if ffmpeg["available"] != "yes":
        raise ArchiveEngineError(
            "FFmpeg wurde nicht gefunden. Bitte FFmpeg installieren und die Anwendung neu starten."
        )

    deno = deno_details()
    if deno["available"] != "yes":
        raise ArchiveEngineError(
            "Für vollständige Quellenunterstützung fehlt Deno 2.3 oder neuer."
        )

    dependencies = {
        "yt-dlp-ejs": package_version_safe("yt-dlp-ejs"),
        "curl-cffi": package_version_safe("curl-cffi"),
    }
    missing = [name for name, version in dependencies.items() if not version]
    if missing:
        raise ArchiveEngineError(
            "Erforderliche yt-dlp-Komponenten fehlen: " + ", ".join(missing)
        )

    return RuntimeEnvironment(ffmpeg=ffmpeg, deno=deno, dependencies=dependencies)


def session_candidates(
    root: Path,
    source_id: str,
    source_provider: str | None = None,
) -> list[ResumeCandidate]:
    candidates: list[tuple[float, ResumeCandidate]] = []
    if not root.exists():
        return []

    for session_file in root.glob("*/SESSION.json"):
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            if data.get("source_id") != source_id:
                continue
            if source_provider:
                candidate_provider = str(
                    data.get("source_provider") or data.get("extractor") or ""
                ).strip().lower()
                if candidate_provider and candidate_provider != source_provider.strip().lower():
                    continue
            if data.get("status") == "completed":
                continue
            folder = session_file.parent
            part_files = sorted(folder.rglob("*.part"))
            candidates.append(
                (
                    session_file.stat().st_mtime,
                    ResumeCandidate(folder=folder, session=data, part_files=part_files),
                )
            )
        except (OSError, json.JSONDecodeError):
            continue

    return [item for _, item in sorted(candidates, key=lambda pair: pair[0], reverse=True)]


def _prepare_session(request: ArchiveRequest) -> tuple[Path, dict[str, Any], bool]:
    info = request.source_info
    source_id = str(info["id"])

    if request.resume_candidate is not None:
        folder = request.resume_candidate.folder
        data = dict(request.resume_candidate.session)
        data["status"] = "resuming"
        data["resumed_at"] = iso_local()
        data.setdefault("capture_id", str(uuid.uuid4()))
        data.setdefault("source_id", source_id)
        data.setdefault("entered_url", request.entered_url)
        data["canonical_url"] = request.canonical_url
        data["resume_enabled"] = True
        data["rights_confirmed"] = True
        data["rights_statement"] = RIGHTS_STATEMENT
        data["rights_confirmed_at"] = request.rights_confirmed_at
        data["rights_confirmation_count"] = int(data.get("rights_confirmation_count") or 1) + 1

        # Resume always preserves the original acquisition selection.
        legacy_selector = {
            "video+audio": "bv*+ba/b",
            "video-only": "bv",
            "audio-only": "ba/b",
        }.get(str(data.get("mode") or request.mode), request.format_selector)
        data.setdefault("mode", request.mode)
        data.setdefault("quality", request.quality_label)
        data.setdefault("format_selector", legacy_selector)
        write_json(folder / "SESSION.json", data)
        return folder, data, True

    started = now_local()
    stamp = started.strftime("%Y%m%d_%H%M%S")
    title = sanitize_component(str(info.get("title") or "untitled"), 70)
    safe_source_id = sanitize_component(source_id, 50)
    folder_name = f"{stamp}_{safe_source_id}_{title}"
    folder = request.output_root / folder_name
    folder.mkdir(parents=True, exist_ok=False)

    session = {
        "application": APP_NAME,
        "application_version": APP_VERSION,
        "status": "prepared",
        "capture_id": str(uuid.uuid4()),
        "source_id": source_id,
        "entered_url": request.entered_url,
        "canonical_url": request.canonical_url,
        "url": request.canonical_url,
        "source_provider": info.get("extractor_key") or info.get("extractor") or "unknown",
        "extractor": info.get("extractor") or info.get("extractor_key") or "unknown",
        "title": info.get("title"),
        "started_at": iso_local(started),
        "mode": request.mode,
        "quality": request.quality_label,
        "format_selector": request.format_selector,
        "rights_confirmed": True,
        "rights_statement": RIGHTS_STATEMENT,
        "rights_confirmed_at": request.rights_confirmed_at,
        "rights_confirmation_count": 1,
        "resume_enabled": True,
        "folder_name": folder_name,
        "filename_prefix": f"{stamp}_{safe_source_id}",
    }
    write_json(folder / "SESSION.json", session)
    return folder, session, False


def _all_files(folder: Path, include_part: bool = False) -> list[Path]:
    files: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if not include_part and path.name.endswith(".part"):
            continue
        files.append(path)
    return sorted(files)


def _subtitle_files(folder: Path) -> list[Path]:
    return [
        path for path in _all_files(folder, include_part=True)
        if path.suffix.lower() in SUBTITLE_EXTENSIONS and not path.name.endswith(".part")
    ]


def _media_files(folder: Path) -> list[Path]:
    return [p for p in _all_files(folder) if p.suffix.lower() in MEDIA_EXTENSIONS]


def _total_size(paths: list[Path]) -> int:
    return sum(p.stat().st_size for p in paths if p.exists() and p.is_file())


def _software_markdown(
    environment: RuntimeEnvironment,
) -> str:
    python_version = platform.python_version()
    yt_version = get_yt_dlp_version()
    ffmpeg = environment.ffmpeg
    deno = environment.deno
    dependencies = environment.dependencies
    ffmpeg_config = ffmpeg.get("configuration") or "not reported"

    return f"""# Software provenance

This file records the software used to create this archive package.

| Component | Provider / project | Version used | License | Availability |
|---|---|---|---|---|
| {APP_NAME} | John G. Haas / repository contributors | {APP_VERSION} | MIT | {REPO_URL} |
| Python | Python Software Foundation and contributors | {python_version} | Python Software Foundation License Version 2 (plus historical compatible licenses) | https://www.python.org/ |
| yt-dlp | yt-dlp project and contributors | {yt_version} | The Unlicense | https://github.com/yt-dlp/yt-dlp |
| yt-dlp-ejs | yt-dlp project and contributors | {dependencies.get("yt-dlp-ejs") or "unknown"} | The Unlicense | https://github.com/yt-dlp/ejs |
| Deno | Deno authors / Deno Land Inc. | {deno.get("version") or "unknown"} | MIT | https://deno.com/ |
| curl_cffi | curl_cffi project and contributors | {dependencies.get("curl-cffi") or "unknown"} | MIT | https://github.com/lexiforest/curl_cffi |
| FFmpeg | FFmpeg project and contributors | {ffmpeg.get("version") or "unknown"} | {ffmpeg.get("license") or "see FFmpeg build"} | https://ffmpeg.org/ |

## FFmpeg build information

Executable: `{privacy_safe_executable(ffmpeg.get("path"))}`

Configuration:

```text
{ffmpeg_config}
```

FFmpeg is generally LGPL v2.1-or-later; builds configured with GPL components are
subject to GPL terms. The effective license of the exact binary depends on its
build configuration.

## Retrieval client

Browser: **not used**

JavaScript challenge runtime: **{deno.get("version") or "unknown"}**

HTTP impersonation support: **curl_cffi {dependencies.get("curl-cffi") or "unknown"}**

The network retrieval is performed by **yt-dlp**. A browser is not used unless a
future version explicitly imports browser cookies or browser-based authentication.

## Archiver license

The archiver is distributed under the MIT License. The copyright and permission
notice must be retained in copies or substantial portions of the software.
"""


def _write_source_url(path: Path, url: str) -> None:
    path.write_text(f"[InternetShortcut]\nURL={url}\n", encoding="utf-8")


def _download_subtitles_best_effort(
    request: ArchiveRequest,
    folder: Path,
    session: dict[str, Any],
    callbacks: ArchiveCallbacks,
    token: CancellationToken,
) -> list[Path]:
    token.raise_if_cancelled()
    subtitle_langs = request.subtitle_languages
    subtitle_types = session.get("subtitle_types") or subtitle_inventory(request.source_info)[0]

    if not subtitle_langs:
        session["subtitle_status"] = "unavailable"
        session["subtitle_successful_languages"] = []
        session["subtitle_failed_languages"] = {}
        session["subtitle_files"] = []
        write_json(folder / "SESSION.json", session)
        callbacks.status("subtitles", "Keine Quell-Untertitel sicher bestimmbar.")
        return []

    from yt_dlp import YoutubeDL

    prefix = str(session["filename_prefix"])
    output_template = str(folder / f"{prefix}_%(title).120B.%(ext)s")
    successful: list[str] = []
    failed: dict[str, str] = {}

    callbacks.status("subtitles", "Quell-Untertitel werden best effort gesichert.")

    for lang in subtitle_langs:
        token.raise_if_cancelled()
        track_type = str(subtitle_types.get(lang) or "unknown")
        is_manual = track_type == "manual"
        label = subtitle_type_label(track_type)

        options: dict[str, Any] = {
            "skip_download": True,
            "writesubtitles": is_manual,
            "writeautomaticsub": not is_manual,
            "subtitleslangs": [lang],
            "outtmpl": output_template,
            "noplaylist": True,
            "windowsfilenames": True,
            "overwrites": False,
            "retries": 1,
            "extractor_retries": 1,
            "quiet": True,
            "no_warnings": False,
        }

        before = set(_subtitle_files(folder))
        try:
            with YoutubeDL(options) as ydl:
                ydl.extract_info(request.canonical_url, download=True)

            after = set(_subtitle_files(folder))
            current_files = sorted(after - before)
            if not current_files:
                current_files = [
                    path for path in after
                    if f".{lang}." in path.name or path.name.endswith(f".{lang}")
                ]

            if current_files:
                successful.append(lang)
                callbacks.status("subtitles", f"{lang} — {label}: gesichert")
            else:
                failed[lang] = "keine passende Untertiteldatei gespeichert"
                callbacks.status("subtitles", f"{lang} — {label}: nicht gesichert")
        except ArchiveCancelled:
            raise
        except Exception as exc:
            message = str(exc)
            failed[lang] = message
            callbacks.status("subtitles", f"{lang} — {label}: nicht gesichert ({message})")

    downloaded = _subtitle_files(folder)
    if successful and failed:
        session["subtitle_status"] = "partial"
    elif successful:
        session["subtitle_status"] = "downloaded"
    elif failed:
        session["subtitle_status"] = "failed"
    else:
        session["subtitle_status"] = "unavailable"

    session["subtitle_attempts"] = 1 if subtitle_langs else 0
    session["subtitle_downloaded_at"] = iso_local()
    session["subtitle_successful_languages"] = successful
    session["subtitle_failed_languages"] = failed
    session["subtitle_types"] = {
        lang: subtitle_types.get(lang, "unknown") for lang in subtitle_langs
    }
    session["subtitle_files"] = [
        path.relative_to(folder).as_posix() for path in downloaded
    ]
    write_json(folder / "SESSION.json", session)
    return downloaded


def _downloader_options(
    folder: Path,
    session: dict[str, Any],
    format_selector: str,
    callbacks: ArchiveCallbacks,
    token: CancellationToken,
) -> dict[str, Any]:
    prefix = str(session["filename_prefix"])
    output_template = str(
        folder / f"{prefix}_%(resolution)s_%(title).120B.%(ext)s"
    )

    def progress_hook(data: dict[str, Any]) -> None:
        token.raise_if_cancelled()
        callbacks.progress(dict(data))

    return {
        "format": format_selector,
        "outtmpl": output_template,
        "noplaylist": True,
        "windowsfilenames": True,
        "continuedl": True,
        "nopart": False,
        "overwrites": False,
        "writeinfojson": True,
        "writedescription": True,
        "writethumbnail": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "subtitleslangs": [],
        "embedmetadata": True,
        "embedthumbnail": True,
        "merge_output_format": "mp4",
        "progress_hooks": [progress_hook],
        "quiet": True,
        "no_warnings": False,
    }


def _execute_download(
    request: ArchiveRequest,
    folder: Path,
    session: dict[str, Any],
    callbacks: ArchiveCallbacks,
    token: CancellationToken,
) -> tuple[dict[str, Any], float]:
    from yt_dlp import YoutubeDL

    token.raise_if_cancelled()
    selector = str(session.get("format_selector") or request.format_selector)
    options = _downloader_options(folder, session, selector, callbacks, token)
    began = time.monotonic()
    callbacks.status("download", "Download beginnt. Resume/Fortsetzen ist aktiviert.")

    try:
        with YoutubeDL(options) as ydl:
            result = ydl.extract_info(request.canonical_url, download=True)
    except ArchiveCancelled:
        session["status"] = "interrupted"
        session["interrupted_at"] = iso_local()
        write_json(folder / "SESSION.json", session)
        raise
    except KeyboardInterrupt as exc:
        session["status"] = "interrupted"
        session["interrupted_at"] = iso_local()
        write_json(folder / "SESSION.json", session)
        raise ArchiveCancelled(
            "Download wurde unterbrochen. Teildateien bleiben für Resume erhalten."
        ) from exc
    except Exception as exc:
        if token.cancelled:
            session["status"] = "interrupted"
            session["interrupted_at"] = iso_local()
            write_json(folder / "SESSION.json", session)
            raise ArchiveCancelled(
                "Vorgang wurde abgebrochen. Teildateien bleiben für Resume erhalten."
            ) from exc
        session["status"] = "failed"
        session["failed_at"] = iso_local()
        session["error"] = str(exc)
        write_json(folder / "SESSION.json", session)
        raise ArchiveEngineError(
            "Download fehlgeschlagen. Vorhandene .part-Dateien bleiben für Resume "
            f"erhalten: {exc}"
        ) from exc

    return result or {}, time.monotonic() - began


def _write_metadata_markdown(
    folder: Path,
    source_info: dict[str, Any],
    result: dict[str, Any],
    session: dict[str, Any],
    subtitle_langs: list[str],
    download_elapsed: float,
    environment: RuntimeEnvironment,
    resumed: bool,
) -> Path:
    from ova_acquisition import best_resolution, format_upload_date

    path = folder / "METADATA.md"
    files = [p for p in _all_files(folder) if p.name != "METADATA.md"]
    size = _total_size(files)
    uploader = source_info.get("channel") or source_info.get("uploader") or "unknown"
    page = source_info.get("webpage_url") or source_info.get("original_url") or session.get("url")
    entered_page = session.get("entered_url") or page
    canonical_page = session.get("canonical_url") or page
    description = (source_info.get("description") or "").strip()
    if len(description) > 12000:
        description = (
            description[:12000]
            + "\n\n[Description truncated in METADATA.md; full description retained separately.]"
        )

    md = f"""# Archive metadata

## Capture

- **Capture ID:** {session.get("capture_id") or "unknown"}
- **Open Video Archiver version:** {APP_VERSION}

## Source

- **Title:** {source_info.get("title") or "unknown"}
- **Provider:** {session.get("source_provider") or source_info.get("extractor_key") or "unknown"}
- **Extractor:** {session.get("extractor") or source_info.get("extractor") or "unknown"}
- **Source ID:** {source_info.get("id") or "unknown"}
- **Channel / uploader:** {uploader}
- **Entered URL:** {entered_page}
- **Canonical URL:** {canonical_page}
- **Upload date:** {format_upload_date(source_info.get("upload_date"))}
- **Video duration:** {human_duration(source_info.get("duration"))}
- **Best resolution observed before download:** {best_resolution(source_info)}

## Rights declaration

- **Confirmed by user:** yes
- **Latest confirmation time:** {session.get("rights_confirmed_at") or "unknown"}
- **Number of confirmations for this session:** {session.get("rights_confirmation_count") or 1}
- **Declaration:** {session.get("rights_statement") or RIGHTS_STATEMENT}
- **Note:** This is a user declaration. The archiver does not independently verify legal authorization.

## Retrieval

- **Archive session started:** {session.get("started_at") or "unknown"}
- **Download completed:** {session.get("download_completed_at") or "unknown"}
- **Download duration:** {human_duration(download_elapsed)}
- **Mode:** {session.get("mode") or "unknown"}
- **Requested quality:** {session.get("quality") or "unknown"}
- **Format selector:** `{session.get("format_selector") or "unknown"}`
- **Resume enabled:** yes
- **Existing session resumed:** {"yes" if resumed else "no"}
- **Selected format(s):** {selected_format_summary(result)}
- **Subtitle tracks requested:** {", ".join(subtitle_langs) if subtitle_langs else "none available"}
- **Subtitle policy:** best effort after media download; all manual source subtitles + original-language automatic captions; auto-translations excluded from the v1.x main workflow
- **Subtitle status:** {session.get("subtitle_status") or "unknown"}
- **Subtitle languages secured:** {", ".join(session.get("subtitle_successful_languages") or []) or "none"}
- **Subtitle languages failed:** {", ".join((session.get("subtitle_failed_languages") or {}).keys()) or "none"}
- **Subtitle files:** {", ".join(session.get("subtitle_files") or []) or "none"}

- **Browser:** not used
- **Retrieval client:** yt-dlp {get_yt_dlp_version()}
- **FFmpeg:** {environment.ffmpeg.get("version") or "unknown"}
- **Operating system:** {platform.platform()}
- **Python:** {platform.python_version()}

## Archive payload

- **Files before checksum manifest:** {len(files)}
- **Stored size before checksum manifest:** {human_bytes(size)}
- **Archive folder name:** `{folder.name}`

### Media files

"""

    media = _media_files(folder)
    if media:
        for item in media:
            md += f"- `{item.name}` — {human_bytes(item.stat().st_size)}\n"
    else:
        md += "- No final media file detected.\n"

    md += "\n## Description\n\n"
    md += description if description else "_No description supplied by source._"
    md += "\n"
    path.write_text(md, encoding="utf-8")
    return path


def _write_checksums(
    folder: Path,
    callbacks: ArchiveCallbacks,
    token: CancellationToken,
) -> Path:
    checksum_file = folder / "SHA256SUMS.txt"
    paths = [
        p for p in _all_files(folder)
        if p.name != "SHA256SUMS.txt" and not p.name.endswith(".tmp")
    ]
    lines: list[str] = []
    callbacks.status("integrity", "Erzeuge SHA-256-Prüfsummen.")

    for index, path in enumerate(paths, start=1):
        token.raise_if_cancelled()
        rel = path.relative_to(folder).as_posix()
        callbacks.hash_progress(index, len(paths), rel)
        lines.append(f"{sha256_file(path)}  {rel}")

    checksum_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return checksum_file


def _zip_archive(
    folder: Path,
    callbacks: ArchiveCallbacks,
    token: CancellationToken,
) -> tuple[Path, float]:
    files = [p for p in _all_files(folder) if not p.name.endswith(".part")]
    total = _total_size(files)
    zip_path = folder.parent / f"{folder.name}.zip"
    temp_zip = Path(str(zip_path) + ".tmp")
    if temp_zip.exists():
        temp_zip.unlink()

    processed = 0
    began = time.monotonic()
    callbacks.status("zip", "Erzeuge ZIP-Container ohne zusätzliche Kompression.")

    try:
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
            for path in files:
                token.raise_if_cancelled()
                arcname = (Path(folder.name) / path.relative_to(folder)).as_posix()
                zinfo = zipfile.ZipInfo.from_file(path, arcname)
                zinfo.compress_type = zipfile.ZIP_STORED
                with path.open("rb") as src, zf.open(zinfo, "w", force_zip64=True) as dst:
                    while chunk := src.read(CHUNK_SIZE):
                        token.raise_if_cancelled()
                        dst.write(chunk)
                        processed += len(chunk)
                        callbacks.zip_progress(processed, total)
        temp_zip.replace(zip_path)
    except Exception:
        if temp_zip.exists():
            temp_zip.unlink()
        raise

    verification = verify_archive(zip_path)
    if not verification.ok:
        raise ArchiveEngineError(
            "ZIP wurde erstellt, konnte aber nicht vollständig verifiziert werden."
        )
    return zip_path, time.monotonic() - began


def _write_zip_checksum(zip_path: Path) -> Path:
    checksum_path = Path(str(zip_path) + ".sha256")
    checksum_path.write_text(
        f"{sha256_file(zip_path)}  {zip_path.name}\n",
        encoding="utf-8",
    )
    return checksum_path


def archive_source(
    request: ArchiveRequest,
    *,
    callbacks: ArchiveCallbacks | None = None,
    token: CancellationToken | None = None,
    environment: RuntimeEnvironment | None = None,
) -> ArchiveResult:
    callbacks = callbacks or ArchiveCallbacks()
    token = token or CancellationToken()
    token.raise_if_cancelled()

    request.output_root.mkdir(parents=True, exist_ok=True)
    if not request.output_root.is_dir():
        raise ArchiveEngineError(f"Ziel ist kein Verzeichnis: {request.output_root}")

    environment = environment or preflight()
    folder, session, resumed = _prepare_session(request)

    # Resume preserves the acquisition mode/quality/selector from the original session.
    if resumed:
        request.mode = str(session.get("mode") or request.mode)
        request.quality_label = str(session.get("quality") or request.quality_label)
        request.format_selector = str(session.get("format_selector") or request.format_selector)

    session["subtitle_languages"] = request.subtitle_languages
    session["subtitle_types"] = {
        lang: request.subtitle_types.get(lang, "unknown")
        for lang in request.subtitle_languages
    }
    write_json(folder / "SESSION.json", session)

    _write_source_url(folder / "SOURCE.url", request.canonical_url)
    (folder / "SOFTWARE.md").write_text(
        _software_markdown(environment),
        encoding="utf-8",
    )

    callbacks.status(
        "prepared",
        f"Archiv vorbereitet: {folder.name}",
    )

    session["status"] = "downloading"
    session["download_started_at"] = iso_local()
    write_json(folder / "SESSION.json", session)

    result, download_elapsed = _execute_download(
        request,
        folder,
        session,
        callbacks,
        token,
    )

    part_files = list(folder.rglob("*.part"))
    if part_files:
        session["status"] = "incomplete"
        session["remaining_part_files"] = [p.name for p in part_files]
        write_json(folder / "SESSION.json", session)
        raise ArchiveEngineError(
            "Nach dem Download sind noch .part-Dateien vorhanden; Resume bleibt möglich."
        )

    session["media_download_completed_at"] = iso_local()
    session["download_elapsed_seconds"] = round(download_elapsed, 3)
    session["status"] = "subtitle_enrichment"
    write_json(folder / "SESSION.json", session)

    _download_subtitles_best_effort(
        request,
        folder,
        session,
        callbacks,
        token,
    )

    token.raise_if_cancelled()
    session["status"] = "completed"
    session["download_completed_at"] = iso_local()
    write_json(folder / "SESSION.json", session)

    _write_metadata_markdown(
        folder,
        request.source_info,
        result,
        session,
        request.subtitle_languages,
        download_elapsed,
        environment,
        resumed,
    )

    source_record = source_identity(
        request.source_info,
        entered_url=str(session.get("entered_url") or request.entered_url),
        canonical_url=str(session.get("canonical_url") or request.canonical_url),
    )
    write_manifest(
        folder,
        capture_id=str(session["capture_id"]),
        application_name=APP_NAME,
        application_version=APP_VERSION,
        source=source_record,
        session=session,
        selected_formats=selected_format_summary(result),
    )

    checksum_file = _write_checksums(folder, callbacks, token)
    verification = verify_archive(folder)
    if not verification.ok:
        raise ArchiveEngineError(
            "SHA-256-/Manifest-Prüfung des Archivverzeichnisses ist fehlgeschlagen."
        )

    zip_path: Path | None = None
    zip_checksum: Path | None = None
    zip_elapsed: float | None = None
    if request.create_zip:
        zip_path, zip_elapsed = _zip_archive(folder, callbacks, token)
        zip_checksum = _write_zip_checksum(zip_path)

    files = _all_files(folder)
    callbacks.status("completed", "Kernarchiv und Integritätsprüfung abgeschlossen.")

    return ArchiveResult(
        folder=folder,
        capture_id=str(session["capture_id"]),
        checksum_file=checksum_file,
        files_count=len(files),
        total_size_bytes=_total_size(files),
        download_elapsed_seconds=download_elapsed,
        resumed=resumed,
        subtitle_status=str(session.get("subtitle_status") or "unknown"),
        zip_path=zip_path,
        zip_checksum=zip_checksum,
        zip_elapsed_seconds=zip_elapsed,
    )


def create_verified_zip(
    folder: Path,
    *,
    callbacks: ArchiveCallbacks | None = None,
    token: CancellationToken | None = None,
) -> tuple[Path, Path, float]:
    callbacks = callbacks or ArchiveCallbacks()
    token = token or CancellationToken()
    zip_path, elapsed = _zip_archive(folder, callbacks, token)
    checksum = _write_zip_checksum(zip_path)
    return zip_path, checksum, elapsed


def delete_verified_source_folder(folder: Path, zip_path: Path) -> tuple[int, list[Path]]:
    """Delete only files proven to be members of a verified archive ZIP.

    Files that appeared in the source folder after ZIP creation are deliberately
    left untouched.
    """
    verification = verify_archive(zip_path)
    if not verification.ok:
        raise ArchiveEngineError(
            "Quelldateien werden nicht gelöscht, weil das ZIP nicht verifiziert werden konnte."
        )

    deleted = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        prefix = folder.name + "/"
        members = [
            name for name in zf.namelist()
            if not name.endswith("/") and name.startswith(prefix)
        ]

    root = folder.resolve()
    for member in members:
        rel_text = member[len(prefix):]
        if not rel_text:
            continue
        target = (folder / Path(rel_text)).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        if target.is_file():
            target.unlink()
            deleted += 1

    for directory in sorted(
        (p for p in folder.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass

    try:
        folder.rmdir()
    except OSError:
        pass

    leftovers = _all_files(folder, include_part=True) if folder.exists() else []
    return deleted, leftovers
