from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ova_core import (
    MANIFEST_SCHEMA,
    file_role,
    privacy_safe_executable,
    source_identity,
    verify_archive,
    write_manifest,
    sha256_file,
)


def _write_checksums(folder: Path) -> None:
    lines = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.name != "SHA256SUMS.txt"):
        rel = path.relative_to(folder).as_posix()
        lines.append(f"{sha256_file(path)}  {rel}")
    (folder / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_source_identity_is_platform_neutral() -> None:
    info = {"id": "abc", "extractor_key": "ExampleVideo", "extractor": "example"}
    source = source_identity(
        info,
        entered_url="https://video.example/source/abc",
        canonical_url="https://video.example/source/abc",
    )
    assert source["provider"] == "ExampleVideo"
    assert source["source_id"] == "abc"
    assert source["entered_url"] == "https://video.example/source/abc"


def test_privacy_safe_executable_strips_path() -> None:
    assert privacy_safe_executable(r"C:\Users\John\bin\ffmpeg.exe") == "ffmpeg.exe"
    assert privacy_safe_executable("/usr/local/bin/ffmpeg") == "ffmpeg"


def test_manifest_and_folder_verification(tmp_path: Path) -> None:
    folder = tmp_path / "capture"
    folder.mkdir()
    (folder / "SESSION.json").write_text('{"status":"completed"}\n', encoding="utf-8")
    (folder / "SOFTWARE.md").write_text("# Software\n", encoding="utf-8")
    (folder / "clip.mp4").write_bytes(b"media")
    (folder / "clip.info.json").write_text('{"id":"abc"}\n', encoding="utf-8")
    (folder / "clip.description").write_text("description\n", encoding="utf-8")
    (folder / "clip.jpg").write_bytes(b"image")

    session = {
        "started_at": "2026-10-05T20:00:00+02:00",
        "download_completed_at": "2026-10-05T20:01:00+02:00",
        "mode": "video+audio",
        "quality": "best",
        "format_selector": "bv*+ba/b",
        "resume_enabled": True,
        "rights_confirmed": True,
        "subtitle_status": "unavailable",
    }
    source = {
        "provider": "ExampleVideo",
        "extractor": "example",
        "source_id": "abc",
        "entered_url": "https://video.example/source/abc",
        "canonical_url": "https://video.example/source/abc",
    }

    write_manifest(
        folder,
        capture_id="00000000-0000-4000-8000-000000000001",
        application_name="Open Video Archiver",
        application_version="1.0",
        source=source,
        session=session,
        selected_formats="137, 140",
    )
    manifest = json.loads((folder / "MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert any(item["role"] == "source-media" for item in manifest["files"])

    _write_checksums(folder)
    result = verify_archive(folder)
    assert result.ok
    assert result.checked_files == result.verified_files

    (folder / "clip.mp4").write_bytes(b"modified")
    tampered = verify_archive(folder)
    assert not tampered.ok
    assert any(issue.path == "clip.mp4" for issue in tampered.issues)


def test_zip_verification(tmp_path: Path) -> None:
    folder = tmp_path / "capture"
    folder.mkdir()
    (folder / "SESSION.json").write_text('{"status":"completed"}\n', encoding="utf-8")
    (folder / "SOFTWARE.md").write_text("# Software\n", encoding="utf-8")
    (folder / "clip.mp4").write_bytes(b"media")
    session = {
        "started_at": "2026-10-05T20:00:00+02:00",
        "download_completed_at": "2026-10-05T20:01:00+02:00",
        "mode": "video+audio",
        "quality": "best",
        "format_selector": "bv*+ba/b",
        "resume_enabled": True,
        "rights_confirmed": True,
        "subtitle_status": "unavailable",
    }
    source = {
        "provider": "ExampleVideo",
        "extractor": "example",
        "source_id": "abc",
        "entered_url": "https://video.example/source/abc",
        "canonical_url": "https://video.example/source/abc",
    }
    write_manifest(
        folder,
        capture_id="00000000-0000-4000-8000-000000000001",
        application_name="Open Video Archiver",
        application_version="1.0",
        source=source,
        session=session,
        selected_formats="137, 140",
    )
    _write_checksums(folder)

    zip_path = tmp_path / "capture.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for path in folder.rglob("*"):
            if path.is_file():
                zf.write(path, (Path(folder.name) / path.relative_to(folder)).as_posix())

    result = verify_archive(zip_path)
    assert result.ok


def test_file_roles() -> None:
    assert file_role(Path("x.info.json")) == "source-metadata"
    assert file_role(Path("x.mp4")) == "source-media"
    assert file_role(Path("MANIFEST.json")) == "ova-manifest"
