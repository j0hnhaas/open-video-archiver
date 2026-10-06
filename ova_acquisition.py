"""UI-agnostic source inspection helpers for Open Video Archiver.

The module contains the parts of acquisition that a CLI and a future desktop GUI
need in common: source inspection, subtitle inventory and format/quality models.
It intentionally does not prompt, print or make policy decisions for the user.
"""

from __future__ import annotations

from typing import Any


class AcquisitionError(RuntimeError):
    pass


def fetch_metadata(url: str) -> dict[str, Any]:
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise AcquisitionError(str(exc)) from exc
    if not info or not info.get("id"):
        raise AcquisitionError("Keine verwertbaren Videometadaten erhalten.")
    return info


def best_resolution(info: dict[str, Any]) -> str:
    best = None
    for fmt in info.get("formats") or []:
        width = fmt.get("width")
        height = fmt.get("height")
        if not width or not height or fmt.get("vcodec") == "none":
            continue
        area = int(width) * int(height)
        if best is None or area > best[0]:
            best = (area, int(width), int(height), fmt.get("fps"))
    if not best:
        return "unbekannt"
    _, width, height, fps = best
    return f"{width}x{height}" + (
        f" @ {fps:g} fps" if isinstance(fps, (int, float)) else ""
    )


def format_upload_date(value: str | None) -> str:
    if value and len(value) == 8 and value.isdigit():
        return f"{value[6:8]}.{value[4:6]}.{value[0:4]}"
    return value or "unbekannt"


def subtitle_type_label(kind: str) -> str:
    return {
        "manual": "manuell",
        "auto-original": "automatisch – Originalsprache",
        "auto-translation": "automatisch übersetzt",
    }.get(kind, kind or "unbekannt")


def subtitle_inventory(
    info: dict[str, Any],
) -> tuple[dict[str, str], str | None]:
    manual = sorted((info.get("subtitles") or {}).keys())
    automatic = sorted((info.get("automatic_captions") or {}).keys())
    source_language = (info.get("language") or "").strip().lower()

    original_auto: str | None = None
    auto_lookup = {lang.lower(): lang for lang in automatic}

    if automatic and source_language:
        for candidate in (f"{source_language}-orig", source_language):
            if candidate in auto_lookup:
                original_auto = auto_lookup[candidate]
                break

    if original_auto is None:
        explicit_originals = [
            lang for lang in automatic if lang.lower().endswith("-orig")
        ]
        if len(explicit_originals) == 1:
            original_auto = explicit_originals[0]

    kinds: dict[str, str] = {}
    for lang in automatic:
        kinds[lang] = (
            "auto-original" if lang == original_auto else "auto-translation"
        )
    for lang in manual:
        kinds[lang] = "manual"

    return kinds, original_auto


def choose_subtitle_languages(
    info: dict[str, Any],
) -> tuple[list[str], dict[str, str]]:
    """Select source-provided tracks and the original automatic track only."""
    kinds, original_auto = subtitle_inventory(info)
    selected = {lang for lang, kind in kinds.items() if kind == "manual"}
    if original_auto and kinds.get(original_auto) == "auto-original":
        selected.add(original_auto)
    return sorted(selected), kinds


def video_quality_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[int, dict[str, Any]] = {}
    for fmt in info.get("formats") or []:
        if fmt.get("vcodec") in {None, "none"}:
            continue
        height = fmt.get("height")
        width = fmt.get("width")
        if not isinstance(height, (int, float)) or not isinstance(width, (int, float)):
            continue
        h = int(height)
        w = int(width)
        entry = grouped.setdefault(
            h,
            {
                "height": h,
                "width": w,
                "fps": 0.0,
                "codecs": set(),
                "sizes": [],
            },
        )
        entry["width"] = max(int(entry["width"]), w)
        fps = fmt.get("fps")
        if isinstance(fps, (int, float)):
            entry["fps"] = max(float(entry["fps"]), float(fps))
        codec = fmt.get("vcodec")
        if codec and codec != "none":
            entry["codecs"].add(str(codec).split(".")[0])
        size = fmt.get("filesize") or fmt.get("filesize_approx")
        if isinstance(size, (int, float)):
            entry["sizes"].append(int(size))

    options: list[dict[str, Any]] = []
    for height in sorted(grouped.keys(), reverse=True):
        entry = grouped[height]
        sizes = entry.pop("sizes")
        entry["size_hint"] = max(sizes) if sizes else None
        entry["codecs"] = sorted(entry["codecs"])
        options.append(entry)
    return options


def audio_quality_options(info: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = []
    seen: set[str] = set()
    for fmt in info.get("formats") or []:
        if fmt.get("acodec") in {None, "none"} or fmt.get("vcodec") not in {None, "none"}:
            continue
        format_id = str(fmt.get("format_id") or "")
        if not format_id or format_id in seen:
            continue
        seen.add(format_id)
        candidates.append(
            {
                "format_id": format_id,
                "abr": float(fmt.get("abr") or fmt.get("tbr") or 0.0),
                "acodec": str(fmt.get("acodec") or "unknown"),
                "ext": str(fmt.get("ext") or "unknown"),
                "size": fmt.get("filesize") or fmt.get("filesize_approx"),
            }
        )
    candidates.sort(key=lambda item: (item["abr"], item["format_id"]), reverse=True)
    return candidates[:12]


def selected_format_summary(result: dict[str, Any]) -> str:
    requested = result.get("requested_formats") or []
    if requested:
        parts = []
        for fmt in requested:
            label = fmt.get("format_id") or "?"
            res = fmt.get("resolution") or (
                f"{fmt.get('width')}x{fmt.get('height')}"
                if fmt.get("width") and fmt.get("height")
                else "audio"
            )
            codec = "/".join(
                x for x in [fmt.get("vcodec"), fmt.get("acodec")]
                if x and x != "none"
            )
            parts.append(f"{label} ({res}; {codec or 'codec unknown'})")
        return ", ".join(parts)
    return str(result.get("format") or result.get("format_id") or "unknown")
