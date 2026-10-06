from __future__ import annotations

from pathlib import Path

import pytest

import ova_engine
from ova_core import verify_archive
from ova_engine import (
    ArchiveCallbacks,
    ArchiveCancelled,
    ArchiveRequest,
    CancellationToken,
    RuntimeEnvironment,
    archive_source,
    configure_runtime_path,
    delete_verified_source_folder,
    normalize_url,
    portable_tool_directories,
    sanitize_component,
    session_candidates,
)


def _environment() -> RuntimeEnvironment:
    return RuntimeEnvironment(
        ffmpeg={
            "available": "yes",
            "path": "ffmpeg.exe",
            "version": "ffmpeg test",
            "configuration": "",
            "license": "test",
        },
        deno={"available": "yes", "path": "deno.exe", "version": "deno 2.3.0"},
        dependencies={"yt-dlp-ejs": "test", "curl-cffi": "test"},
    )


def _request(root: Path, *, create_zip: bool = False) -> ArchiveRequest:
    return ArchiveRequest(
        source_info={
            "id": "AbC123xYz90",
            "title": "Example",
            "channel": "Channel",
            "upload_date": "20261005",
            "duration": 10,
            "extractor_key": "ExampleVideo",
            "extractor": "example",
            "description": "Example description",
            "formats": [],
        },
        entered_url="https://video.example/source/AbC123xYz90",
        canonical_url="https://video.example/source/AbC123xYz90",
        output_root=root,
        mode="video+audio",
        quality_label="Best",
        format_selector="bv*+ba/b",
        subtitle_languages=[],
        subtitle_types={},
        rights_confirmed_at="2026-10-06T00:00:00+02:00",
        create_zip=create_zip,
    )


def test_normalize_url() -> None:
    url = "https://video.example/source/AbC123xYz90"
    assert normalize_url(url) == url
    assert normalize_url(f"  {url}  ") == url
    assert normalize_url(f"[{url}]({url})") == url

    with pytest.raises(ova_engine.ArchiveEngineError):
        normalize_url("not a url")
    with pytest.raises(ova_engine.ArchiveEngineError):
        normalize_url("AbC123xYz90")
    with pytest.raises(ova_engine.ArchiveEngineError):
        normalize_url("ftp://video.example/source")


def test_cancel_token() -> None:
    token = CancellationToken()
    token.cancel()
    with pytest.raises(ArchiveCancelled):
        token.raise_if_cancelled()


def test_source_id_is_safe_for_paths() -> None:
    assert sanitize_component("provider:item/42", 50) == "provider-item-42"


def test_resume_candidates_are_provider_aware(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    (first / "SESSION.json").write_text(
        '{"source_id":"same-id","source_provider":"ProviderA","status":"downloading"}\n',
        encoding="utf-8",
    )
    (second / "SESSION.json").write_text(
        '{"source_id":"same-id","source_provider":"ProviderB","status":"downloading"}\n',
        encoding="utf-8",
    )

    matches = session_candidates(tmp_path, "same-id", "ProviderB")
    assert len(matches) == 1
    assert matches[0].folder == second


def test_portable_runtime_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ffmpeg_dir = tmp_path / "tools" / "ffmpeg"
    deno_dir = tmp_path / "tools" / "deno"
    ffmpeg_dir.mkdir(parents=True)
    deno_dir.mkdir(parents=True)

    monkeypatch.setenv("PATH", str(tmp_path / "system"))
    directories = portable_tool_directories(tmp_path)
    assert directories == [ffmpeg_dir, deno_dir]

    configured = configure_runtime_path(tmp_path)
    assert configured == [ffmpeg_dir, deno_dir]
    path_parts = __import__("os").environ["PATH"].split(__import__("os").pathsep)
    assert path_parts[:2] == [str(ffmpeg_dir), str(deno_dir)]


def test_archive_engine_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download(request, folder, session, callbacks, token):
        (folder / "clip.mp4").write_bytes(b"media")
        (folder / "clip.info.json").write_text('{"id":"AbC123xYz90"}\n', encoding="utf-8")
        (folder / "clip.description").write_text("description\n", encoding="utf-8")
        (folder / "clip.jpg").write_bytes(b"image")
        return (
            {
                "requested_formats": [
                    {
                        "format_id": "137",
                        "width": 1920,
                        "height": 1080,
                        "vcodec": "avc1",
                        "acodec": "none",
                    },
                    {
                        "format_id": "140",
                        "resolution": "audio only",
                        "vcodec": "none",
                        "acodec": "mp4a",
                    },
                ]
            },
            1.0,
        )

    def fake_subtitles(request, folder, session, callbacks, token):
        session["subtitle_status"] = "unavailable"
        session["subtitle_successful_languages"] = []
        session["subtitle_failed_languages"] = {}
        session["subtitle_files"] = []
        ova_engine.write_json(folder / "SESSION.json", session)
        return []

    monkeypatch.setattr(ova_engine, "_execute_download", fake_download)
    monkeypatch.setattr(ova_engine, "_download_subtitles_best_effort", fake_subtitles)

    result = archive_source(
        _request(tmp_path, create_zip=True),
        callbacks=ArchiveCallbacks(),
        environment=_environment(),
    )

    assert result.folder.exists()
    assert result.capture_id
    assert result.zip_path is not None and result.zip_path.exists()
    assert result.zip_checksum is not None and result.zip_checksum.exists()
    assert verify_archive(result.folder).ok
    assert verify_archive(result.zip_path).ok


def test_verified_delete_preserves_unpackaged_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_download(request, folder, session, callbacks, token):
        (folder / "clip.mp4").write_bytes(b"media")
        (folder / "clip.info.json").write_text('{"id":"AbC123xYz90"}\n', encoding="utf-8")
        (folder / "clip.description").write_text("description\n", encoding="utf-8")
        (folder / "clip.jpg").write_bytes(b"image")
        return ({"format_id": "18"}, 1.0)

    def fake_subtitles(request, folder, session, callbacks, token):
        session["subtitle_status"] = "unavailable"
        session["subtitle_successful_languages"] = []
        session["subtitle_failed_languages"] = {}
        session["subtitle_files"] = []
        ova_engine.write_json(folder / "SESSION.json", session)
        return []

    monkeypatch.setattr(ova_engine, "_execute_download", fake_download)
    monkeypatch.setattr(ova_engine, "_download_subtitles_best_effort", fake_subtitles)

    result = archive_source(
        _request(tmp_path, create_zip=True),
        environment=_environment(),
    )
    assert result.zip_path is not None

    extra = result.folder / "added-after-zip.txt"
    extra.write_text("keep me", encoding="utf-8")

    deleted, leftovers = delete_verified_source_folder(result.folder, result.zip_path)
    assert deleted > 0
    assert extra.exists()
    assert extra in leftovers


def test_gui_launcher_has_no_required_qt_import() -> None:
    import ova_gui

    assert callable(ova_gui.main)
