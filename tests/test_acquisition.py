from ova_acquisition import (
    audio_quality_options,
    best_resolution,
    choose_subtitle_languages,
    format_upload_date,
    selected_format_summary,
    video_quality_options,
)


def test_subtitle_policy_prefers_manual_and_original_auto() -> None:
    info = {
        "language": "de",
        "subtitles": {"en": [{}]},
        "automatic_captions": {"de-orig": [{}], "fr": [{}]},
    }
    selected, kinds = choose_subtitle_languages(info)
    assert selected == ["de-orig", "en"]
    assert kinds["de-orig"] == "auto-original"
    assert kinds["fr"] == "auto-translation"
    assert kinds["en"] == "manual"


def test_quality_models() -> None:
    info = {
        "formats": [
            {"format_id": "v1", "vcodec": "av1", "acodec": "none", "width": 1920, "height": 1080, "fps": 30, "filesize": 1000},
            {"format_id": "v2", "vcodec": "vp9", "acodec": "none", "width": 1280, "height": 720, "fps": 60, "filesize": 800},
            {"format_id": "a1", "vcodec": "none", "acodec": "opus", "abr": 160, "ext": "webm", "filesize": 200},
        ]
    }
    video = video_quality_options(info)
    audio = audio_quality_options(info)
    assert video[0]["height"] == 1080
    assert audio[0]["format_id"] == "a1"
    assert best_resolution(info) == "1920x1080 @ 30 fps"


def test_selected_format_summary() -> None:
    result = {
        "requested_formats": [
            {"format_id": "137", "width": 1920, "height": 1080, "vcodec": "avc1", "acodec": "none"},
            {"format_id": "140", "vcodec": "none", "acodec": "mp4a", "resolution": "audio only"},
        ]
    }
    text = selected_format_summary(result)
    assert "137" in text
    assert "140" in text


def test_upload_date() -> None:
    assert format_upload_date("20261005") == "05.10.2026"
