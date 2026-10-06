"""Core preservation and verification helpers for Open Video Archiver.

This module is intentionally UI-agnostic.  The CLI and a future desktop GUI can
use the same manifest, integrity and verification logic.
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

MANIFEST_SCHEMA = "ova-manifest/1.0"
CHUNK_SIZE = 1024 * 1024

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".m4a", ".mp3", ".opus", ".ogg", ".aac", ".flac",
}
SUBTITLE_EXTENSIONS = {
    ".vtt", ".srt", ".ass", ".ssa", ".lrc", ".ttml", ".srv1", ".srv2", ".srv3", ".json3",
}


@dataclass(slots=True)
class VerificationIssue:
    path: str
    message: str
    expected: str | None = None
    actual: str | None = None


@dataclass(slots=True)
class VerificationResult:
    target: str
    kind: str
    capture_id: str | None = None
    schema: str | None = None
    manifest_valid: bool = False
    checksum_manifest_present: bool = False
    checked_files: int = 0
    verified_files: int = 0
    archive_structure_valid: bool = True
    issues: list[VerificationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.manifest_valid
            and self.checksum_manifest_present
            and self.archive_structure_valid
            and not self.issues
            and self.checked_files == self.verified_files
        )


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically write JSON."""
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sha256_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(CHUNK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    with path.open("rb") as handle:
        return sha256_stream(handle)


def source_identity(
    info: dict[str, Any],
    *,
    entered_url: str,
    canonical_url: str,
) -> dict[str, Any]:
    """Return a platform-neutral source descriptor.

    The schema deliberately uses provider-neutral fields so sources resolved
    by yt-dlp can be preserved without provider-specific archive structures.
    """
    extractor_key = str(info.get("extractor_key") or "").strip()
    extractor = str(info.get("extractor") or extractor_key or "").strip()
    source_id = str(info.get("id") or "").strip()

    provider = extractor_key or extractor
    if not provider:
        host = urlparse(canonical_url).hostname or urlparse(entered_url).hostname or ""
        provider = host.lower()

    return {
        "provider": provider or "unknown",
        "extractor": extractor or "unknown",
        "source_id": source_id or "unknown",
        "entered_url": entered_url,
        "canonical_url": canonical_url,
    }


def file_role(path: Path) -> str:
    """Assign a stable semantic role to an archive member."""
    name = path.name
    lower = name.lower()
    suffix = path.suffix.lower()

    if name == "MANIFEST.json":
        return "ova-manifest"
    if name == "SESSION.json":
        return "ova-session-record"
    if name == "METADATA.md":
        return "ova-metadata-record"
    if name == "SOFTWARE.md":
        return "ova-software-provenance"
    if name == "SOURCE.url":
        return "ova-source-link"
    if name == "SHA256SUMS.txt":
        return "ova-integrity-manifest"
    if lower.endswith(".info.json"):
        return "source-metadata"
    if ".description" in lower or suffix in {".description"}:
        return "source-description"
    if suffix in SUBTITLE_EXTENSIONS:
        return "source-subtitle"
    if suffix in MEDIA_EXTENSIONS:
        return "source-media"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}:
        return "source-thumbnail"
    return "archive-supporting-file"


def _relative_files(folder: Path) -> list[Path]:
    return sorted(
        path for path in folder.rglob("*")
        if path.is_file() and not path.name.endswith(".tmp") and not path.name.endswith(".part")
    )


def completeness_record(
    folder: Path,
    session: dict[str, Any],
) -> dict[str, str]:
    files = _relative_files(folder)
    roles = {file_role(path) for path in files}

    subtitle_status = str(session.get("subtitle_status") or "unknown")
    if subtitle_status == "downloaded":
        subtitles = "captured"
    elif subtitle_status == "partial":
        subtitles = "partial"
    elif subtitle_status in {"failed", "interrupted"}:
        subtitles = "failed"
    elif subtitle_status == "unavailable":
        subtitles = "not-available"
    else:
        subtitles = "unknown"

    return {
        "media": "captured" if "source-media" in roles else "missing",
        "metadata": "captured" if "source-metadata" in roles else "missing",
        "description": "captured" if "source-description" in roles else "missing",
        "thumbnail": "captured" if "source-thumbnail" in roles else "missing",
        "subtitles": subtitles,
        "software_provenance": "captured" if "ova-software-provenance" in roles else "missing",
        "session_record": "captured" if "ova-session-record" in roles else "missing",
    }


def build_manifest(
    folder: Path,
    *,
    capture_id: str,
    application_name: str,
    application_version: str,
    source: dict[str, Any],
    session: dict[str, Any],
    selected_formats: str,
) -> dict[str, Any]:
    files = []
    for path in _relative_files(folder):
        if path.name in {"MANIFEST.json", "SHA256SUMS.txt"}:
            continue
        rel = path.relative_to(folder).as_posix()
        files.append({
            "path": rel,
            "role": file_role(path),
            "size_bytes": path.stat().st_size,
        })

    return {
        "schema": MANIFEST_SCHEMA,
        "capture_id": capture_id,
        "application": {
            "name": application_name,
            "version": application_version,
        },
        "source": source,
        "acquisition": {
            "started_at": session.get("started_at"),
            "download_started_at": session.get("download_started_at"),
            "media_download_completed_at": session.get("media_download_completed_at"),
            "completed_at": session.get("download_completed_at"),
            "mode": session.get("mode"),
            "requested_quality": session.get("quality"),
            "format_selector": session.get("format_selector"),
            "selected_formats": selected_formats,
            "resume_enabled": bool(session.get("resume_enabled")),
            "resumed": bool(session.get("resumed_at")),
            "rights_confirmed": bool(session.get("rights_confirmed")),
        },
        "completeness": completeness_record(folder, session),
        "files": files,
        "integrity": {
            "algorithm": "SHA-256",
            "checksum_manifest": "SHA256SUMS.txt",
            "note": "The checksum manifest is written after MANIFEST.json and covers the manifest itself.",
        },
    }


def write_manifest(
    folder: Path,
    *,
    capture_id: str,
    application_name: str,
    application_version: str,
    source: dict[str, Any],
    session: dict[str, Any],
    selected_formats: str,
) -> Path:
    path = folder / "MANIFEST.json"
    write_json(
        path,
        build_manifest(
            folder,
            capture_id=capture_id,
            application_name=application_name,
            application_version=application_version,
            source=source,
            session=session,
            selected_formats=selected_formats,
        ),
    )
    return path


def parse_checksum_lines(text: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if "  " not in line:
            raise ValueError(f"Malformed checksum line {line_no}")
        expected, rel = line.split("  ", 1)
        expected = expected.strip().lower()
        rel = rel.strip().replace("\\", "/")
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise ValueError(f"Invalid SHA-256 digest on line {line_no}")
        if not rel:
            raise ValueError(f"Missing path on checksum line {line_no}")
        entries.append((expected, rel))
    return entries


def _safe_member_path(name: str) -> bool:
    path = Path(name.replace("\\", "/"))
    return (
        bool(path.parts)
        and not path.is_absolute()
        and ".." not in path.parts
        and not (os.name == "nt" and ":" in path.parts[0])
    )


def _load_manifest_json(text: str, result: VerificationResult) -> dict[str, Any] | None:
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as exc:
        result.issues.append(VerificationIssue("MANIFEST.json", f"Invalid JSON: {exc}"))
        return None
    if not isinstance(manifest, dict):
        result.issues.append(VerificationIssue("MANIFEST.json", "Manifest root is not an object"))
        return None

    result.schema = str(manifest.get("schema") or "")
    result.capture_id = str(manifest.get("capture_id") or "") or None
    if result.schema != MANIFEST_SCHEMA:
        result.issues.append(
            VerificationIssue(
                "MANIFEST.json",
                f"Unsupported or missing schema (expected {MANIFEST_SCHEMA}, got {result.schema or 'none'})",
            )
        )
        return manifest
    if not result.capture_id:
        result.issues.append(VerificationIssue("MANIFEST.json", "Missing capture_id"))
        return manifest

    source = manifest.get("source")
    files = manifest.get("files")
    if not isinstance(source, dict) or not isinstance(files, list):
        result.issues.append(VerificationIssue("MANIFEST.json", "Missing source/files structure"))
        return manifest

    result.manifest_valid = True
    return manifest


def _manifest_file_map(
    manifest: dict[str, Any] | None,
    result: VerificationResult,
) -> dict[str, dict[str, Any]]:
    if not manifest:
        return {}
    entries = manifest.get("files")
    if not isinstance(entries, list):
        return {}

    mapped: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            result.issues.append(
                VerificationIssue("MANIFEST.json", f"files[{index}] is not an object")
            )
            continue
        path = str(item.get("path") or "").replace("\\", "/")
        role = str(item.get("role") or "")
        size = item.get("size_bytes")
        if not path or not _safe_member_path(path):
            result.issues.append(
                VerificationIssue("MANIFEST.json", f"Invalid file path in files[{index}]")
            )
            continue
        if path in {"MANIFEST.json", "SHA256SUMS.txt"}:
            result.issues.append(
                VerificationIssue("MANIFEST.json", f"Reserved file listed in inventory: {path}")
            )
            continue
        if path in mapped:
            result.issues.append(
                VerificationIssue("MANIFEST.json", f"Duplicate file inventory entry: {path}")
            )
            continue
        if not role:
            result.issues.append(
                VerificationIssue("MANIFEST.json", f"Missing role for {path}")
            )
        if not isinstance(size, int) or size < 0:
            result.issues.append(
                VerificationIssue("MANIFEST.json", f"Invalid size_bytes for {path}")
            )
        mapped[path] = item
    return mapped


def verify_folder(folder: Path) -> VerificationResult:
    folder = folder.resolve()
    result = VerificationResult(target=str(folder), kind="directory")
    if not folder.is_dir():
        result.issues.append(VerificationIssue(str(folder), "Target is not a directory"))
        return result

    manifest_path = folder / "MANIFEST.json"
    checksum_path = folder / "SHA256SUMS.txt"
    manifest: dict[str, Any] | None = None
    if not manifest_path.exists():
        result.issues.append(VerificationIssue("MANIFEST.json", "Missing"))
    else:
        manifest = _load_manifest_json(manifest_path.read_text(encoding="utf-8"), result)
    manifest_files = _manifest_file_map(manifest, result)

    if not checksum_path.exists():
        result.issues.append(VerificationIssue("SHA256SUMS.txt", "Missing"))
        return result

    result.checksum_manifest_present = True
    try:
        entries = parse_checksum_lines(checksum_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result.issues.append(VerificationIssue("SHA256SUMS.txt", str(exc)))
        return result

    checksum_paths = {rel for _, rel in entries}
    expected_inventory = checksum_paths - {"MANIFEST.json"}
    if manifest_files and set(manifest_files) != expected_inventory:
        missing_from_manifest = sorted(expected_inventory - set(manifest_files))
        missing_from_checksums = sorted(set(manifest_files) - expected_inventory)
        if missing_from_manifest:
            result.issues.append(
                VerificationIssue(
                    "MANIFEST.json",
                    "Checksum entries missing from manifest inventory: " + ", ".join(missing_from_manifest),
                )
            )
        if missing_from_checksums:
            result.issues.append(
                VerificationIssue(
                    "SHA256SUMS.txt",
                    "Manifest inventory entries missing from checksums: " + ", ".join(missing_from_checksums),
                )
            )

    result.checked_files = len(entries)
    root = folder.resolve()
    for expected, rel in entries:
        if not _safe_member_path(rel):
            result.issues.append(VerificationIssue(rel, "Unsafe path in checksum manifest"))
            continue
        path = (folder / Path(rel)).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            result.issues.append(VerificationIssue(rel, "Path escapes archive root"))
            continue
        if not path.is_file():
            result.issues.append(VerificationIssue(rel, "Missing file", expected=expected))
            continue
        if rel in manifest_files:
            expected_size = manifest_files[rel].get("size_bytes")
            if isinstance(expected_size, int) and path.stat().st_size != expected_size:
                result.issues.append(
                    VerificationIssue(
                        rel,
                        "File size differs from manifest",
                        expected=str(expected_size),
                        actual=str(path.stat().st_size),
                    )
                )
                continue
        actual = sha256_file(path)
        if actual != expected:
            result.issues.append(
                VerificationIssue(rel, "SHA-256 mismatch", expected=expected, actual=actual)
            )
            continue
        result.verified_files += 1

    return result


def _find_zip_root(names: list[str]) -> str | None:
    candidates = [name for name in names if name.endswith("/MANIFEST.json") or name == "MANIFEST.json"]
    if len(candidates) != 1:
        return None
    manifest_name = candidates[0]
    return manifest_name[: -len("MANIFEST.json")]


def verify_zip(zip_path: Path) -> VerificationResult:
    zip_path = zip_path.resolve()
    result = VerificationResult(target=str(zip_path), kind="zip")
    if not zip_path.is_file():
        result.issues.append(VerificationIssue(str(zip_path), "Target is not a file"))
        return result

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                result.archive_structure_valid = False
                result.issues.append(VerificationIssue(bad, "ZIP CRC failure"))
                return result

            names = zf.namelist()
            if any(not _safe_member_path(name) for name in names if not name.endswith("/")):
                result.archive_structure_valid = False
                result.issues.append(VerificationIssue(str(zip_path), "ZIP contains unsafe member paths"))
                return result

            root = _find_zip_root(names)
            if root is None:
                result.archive_structure_valid = False
                result.issues.append(
                    VerificationIssue("MANIFEST.json", "Expected exactly one MANIFEST.json in ZIP")
                )
                return result

            manifest_name = root + "MANIFEST.json"
            checksum_name = root + "SHA256SUMS.txt"

            try:
                manifest_text = zf.read(manifest_name).decode("utf-8")
            except KeyError:
                result.issues.append(VerificationIssue("MANIFEST.json", "Missing"))
                return result
            except UnicodeDecodeError as exc:
                result.issues.append(VerificationIssue("MANIFEST.json", f"Invalid UTF-8: {exc}"))
                return result
            manifest = _load_manifest_json(manifest_text, result)
            manifest_files = _manifest_file_map(manifest, result)

            try:
                checksum_text = zf.read(checksum_name).decode("utf-8")
            except KeyError:
                result.issues.append(VerificationIssue("SHA256SUMS.txt", "Missing"))
                return result
            except UnicodeDecodeError as exc:
                result.issues.append(VerificationIssue("SHA256SUMS.txt", f"Invalid UTF-8: {exc}"))
                return result

            result.checksum_manifest_present = True
            try:
                entries = parse_checksum_lines(checksum_text)
            except ValueError as exc:
                result.issues.append(VerificationIssue("SHA256SUMS.txt", str(exc)))
                return result

            checksum_paths = {rel for _, rel in entries}
            expected_inventory = checksum_paths - {"MANIFEST.json"}
            if manifest_files and set(manifest_files) != expected_inventory:
                missing_from_manifest = sorted(expected_inventory - set(manifest_files))
                missing_from_checksums = sorted(set(manifest_files) - expected_inventory)
                if missing_from_manifest:
                    result.issues.append(
                        VerificationIssue(
                            "MANIFEST.json",
                            "Checksum entries missing from manifest inventory: " + ", ".join(missing_from_manifest),
                        )
                    )
                if missing_from_checksums:
                    result.issues.append(
                        VerificationIssue(
                            "SHA256SUMS.txt",
                            "Manifest inventory entries missing from checksums: " + ", ".join(missing_from_checksums),
                        )
                    )

            result.checked_files = len(entries)
            name_set = set(names)
            for expected, rel in entries:
                if not _safe_member_path(rel):
                    result.issues.append(VerificationIssue(rel, "Unsafe path in checksum manifest"))
                    continue
                member = root + rel
                if member not in name_set:
                    result.issues.append(VerificationIssue(rel, "Missing ZIP member", expected=expected))
                    continue
                if rel in manifest_files:
                    expected_size = manifest_files[rel].get("size_bytes")
                    actual_size = zf.getinfo(member).file_size
                    if isinstance(expected_size, int) and actual_size != expected_size:
                        result.issues.append(
                            VerificationIssue(
                                rel,
                                "ZIP member size differs from manifest",
                                expected=str(expected_size),
                                actual=str(actual_size),
                            )
                        )
                        continue
                with zf.open(member, "r") as handle:
                    actual = sha256_stream(handle)
                if actual != expected:
                    result.issues.append(
                        VerificationIssue(rel, "SHA-256 mismatch", expected=expected, actual=actual)
                    )
                    continue
                result.verified_files += 1
    except (OSError, zipfile.BadZipFile) as exc:
        result.archive_structure_valid = False
        result.issues.append(VerificationIssue(str(zip_path), f"Invalid ZIP: {exc}"))

    return result


def verify_archive(target: Path) -> VerificationResult:
    if target.is_dir():
        return verify_folder(target)
    if target.suffix.lower() == ".zip":
        return verify_zip(target)
    result = VerificationResult(target=str(target), kind="unknown")
    result.issues.append(VerificationIssue(str(target), "Expected an archive directory or .zip file"))
    return result


def privacy_safe_executable(value: str | None) -> str:
    """Return only an executable basename, never a private absolute local path."""
    if not value:
        return "unknown"
    normalized = value.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1] or "unknown"
