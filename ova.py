#!/usr/bin/env python3
"""Command-line interface for Open Video Archiver."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from ova_acquisition import (
    AcquisitionError,
    audio_quality_options,
    best_resolution,
    choose_subtitle_languages,
    fetch_metadata,
    format_upload_date,
    subtitle_type_label,
    video_quality_options,
)
from ova_constants import (
    APP_NAME,
    APP_VERSION,
    COPYRIGHT,
    DEFAULT_ARCHIVE_ROOT,
    POSITIONING,
    REPO_URL,
    RIGHTS_STATEMENT,
)
from ova_core import verify_archive
from ova_engine import (
    ArchiveCallbacks,
    ArchiveCancelled,
    ArchiveEngineError,
    ArchiveRequest,
    CancellationToken,
    archive_source,
    create_verified_zip,
    delete_verified_source_folder,
    human_bytes,
    human_duration,
    iso_local,
    normalize_url,
    preflight,
    session_candidates,
)

COLOR_ENABLED = os.environ.get("NO_COLOR") is None and (
    sys.stdout.isatty()
    or os.environ.get("PYCHARM_HOSTED") == "1"
    or bool(os.environ.get("WT_SESSION"))
)


class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"


def paint(text: str, *codes: str) -> str:
    return "".join(codes) + text + Style.RESET if COLOR_ENABLED and codes else text


def section(title: str) -> None:
    print()
    print(paint(f"◆ {title}", Style.BOLD, Style.CYAN))
    print(paint("─" * 72, Style.DIM))


def info(message: str) -> None:
    print(f"{paint('›', Style.CYAN)} {message}")


def success(message: str) -> None:
    print(f"{paint('✓', Style.GREEN, Style.BOLD)} {message}")


def warning(message: str) -> None:
    print(f"{paint('⚠', Style.YELLOW, Style.BOLD)} {message}")


def print_banner() -> None:
    print()
    print(paint("╔" + "═" * 70 + "╗", Style.CYAN))
    print(
        paint("║", Style.CYAN)
        + paint(f"  {APP_NAME}  v{APP_VERSION}".ljust(70), Style.BOLD)
        + paint("║", Style.CYAN)
    )
    print(
        paint("║", Style.CYAN)
        + f"  {POSITIONING}".ljust(70)
        + paint("║", Style.CYAN)
    )
    print(paint("╚" + "═" * 70 + "╝", Style.CYAN))
    print()
    print(paint(COPYRIGHT, Style.BOLD))
    print(f"Source: {REPO_URL}")
    print()
    print(paint("Press Ctrl+C at any time to cancel safely.", Style.YELLOW, Style.BOLD))
    print("Partial downloads are preserved for resume.")
    print()


def prompt(message: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{message}{suffix}: ").strip()
    return answer if answer else (default or "")


def prompt_yes_no(message: str) -> bool:
    while True:
        value = input(f"{message} [j/n]: ").strip().lower()
        if value in {"j", "ja", "y", "yes"}:
            return True
        if value in {"n", "nein", "no"}:
            return False
        print("Bitte j oder n eingeben.")


def require_rights_confirmation() -> str:
    section("RECHTEBESTÄTIGUNG")
    print(RIGHTS_STATEMENT)
    print()
    print("Diese Bestätigung ist eine Erklärung des Benutzers; das Programm führt")
    print("keine rechtliche Prüfung der Berechtigung durch.")
    if not prompt_yes_no("Berechtigung bestätigen?"):
        raise ArchiveCancelled("Rechtebestätigung wurde nicht erteilt.")
    return iso_local()


def display_metadata(
    source_info: dict[str, Any],
    subtitle_languages: list[str],
    subtitle_types: dict[str, str],
) -> None:
    section("QUELLENINFORMATION")
    print(f"Titel:              {source_info.get('title') or 'unbekannt'}")
    print(
        "Kanal/Uploader:     "
        + str(source_info.get("channel") or source_info.get("uploader") or "unbekannt")
    )
    print(f"Source ID:          {source_info.get('id') or 'unbekannt'}")
    print(f"Upload-Datum:       {format_upload_date(source_info.get('upload_date'))}")
    print(f"Dauer:              {human_duration(source_info.get('duration'))}")
    print(f"Beste Auflösung:    {best_resolution(source_info)}")
    print(
        "Webseite:           "
        + str(source_info.get("webpage_url") or source_info.get("original_url") or "")
    )
    if subtitle_languages:
        print("Quell-Untertitel:   Best Effort nach dem Mediendownload")
        for lang in subtitle_languages:
            print(f"  - {lang}: {subtitle_type_label(subtitle_types.get(lang, ''))}")
    else:
        print("Quell-Untertitel:   keine sicher bestimmbar")
    print("Auto-Übersetzungen: nicht Teil des v1.x-Hauptworkflows")
    print("─" * 72)


def choose_mode() -> str:
    section("ARCHIVIERUNGSMODUS")
    print("  [1] Video + Audio  (empfohlen)")
    print("  [2] Nur Video")
    print("  [3] Nur Audio")
    print()
    print(paint("  Abbrechen: Ctrl+C", Style.DIM))
    while True:
        value = input("Auswahl [1-3]: ").strip()
        if value == "1":
            return "video+audio"
        if value == "2":
            return "video-only"
        if value == "3":
            return "audio-only"
        print("Bitte 1, 2 oder 3 eingeben.")


def choose_quality(source_info: dict[str, Any], mode: str) -> tuple[str, str]:
    section("QUALITÄT")
    if mode in {"video+audio", "video-only"}:
        qualities = video_quality_options(source_info)
        if not qualities:
            raise ArchiveEngineError("Keine auswählbaren Videoauflösungen gefunden.")

        print("  [1] Beste verfügbare Qualität  (empfohlen)")
        for index, item in enumerate(qualities, start=2):
            fps = f", bis {item['fps']:g} fps" if item["fps"] else ""
            codecs = f", {', '.join(item['codecs'])}" if item["codecs"] else ""
            size = (
                f", Videospur ca. {human_bytes(item['size_hint'])}"
                if item.get("size_hint")
                else ""
            )
            print(
                f"  [{index}] {item['width']}x{item['height']} "
                f"({item['height']}p){fps}{codecs}{size}"
            )

        print()
        print(paint("  Abbrechen: Ctrl+C", Style.DIM))
        while True:
            value = input(f"Auswahl [1-{len(qualities) + 1}]: ").strip()
            if value == "1":
                if mode == "video+audio":
                    return "Beste verfügbare Video- und Audioqualität", "bv*+ba/b"
                return "Beste verfügbare Videoqualität", "bv"

            if value.isdigit() and 2 <= int(value) <= len(qualities) + 1:
                item = qualities[int(value) - 2]
                height = int(item["height"])
                selector = (
                    f"bv*[height={height}]+ba/b[height={height}]"
                    if mode == "video+audio"
                    else f"bv*[height={height}]"
                )
                return f"{item['width']}x{height} ({height}p)", selector
            print(f"Bitte 1 bis {len(qualities) + 1} eingeben.")

    audio = audio_quality_options(source_info)
    if not audio:
        raise ArchiveEngineError("Keine auswählbaren Audioformate gefunden.")

    print("  [1] Beste verfügbare Audioqualität  (empfohlen)")
    for index, item in enumerate(audio, start=2):
        abr = f"{item['abr']:.0f} kbit/s" if item["abr"] else "Bitrate unbekannt"
        size = human_bytes(item["size"]) if item.get("size") else "Größe unbekannt"
        print(f"  [{index}] {abr}, {item['acodec']}, {item['ext']}, {size}")

    print()
    print(paint("  Abbrechen: Ctrl+C", Style.DIM))
    while True:
        value = input(f"Auswahl [1-{len(audio) + 1}]: ").strip()
        if value == "1":
            return "Beste verfügbare Audioqualität", "ba/b"
        if value.isdigit() and 2 <= int(value) <= len(audio) + 1:
            item = audio[int(value) - 2]
            abr = f"{item['abr']:.0f} kbit/s" if item["abr"] else "Bitrate unbekannt"
            return (
                f"{abr}, {item['acodec']}, {item['ext']} (Format {item['format_id']})",
                str(item["format_id"]),
            )
        print(f"Bitte 1 bis {len(audio) + 1} eingeben.")


class ConsoleCallbacks:
    def __init__(self) -> None:
        self.last_stage = ""
        self.last_line_len = 0

    def _clear_progress_line(self) -> None:
        if self.last_line_len:
            print()
            self.last_line_len = 0

    def status(self, stage: str, message: str) -> None:
        self._clear_progress_line()
        titles = {
            "download": "DOWNLOAD",
            "subtitles": "UNTERTITEL · BEST EFFORT",
            "integrity": "INTEGRITÄT",
            "zip": "ZIP",
        }
        if stage != self.last_stage and stage in titles:
            section(titles[stage])
        self.last_stage = stage
        info(message)

    def progress(self, data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            speed = data.get("speed")
            eta = data.get("eta")

            if total:
                pct = min(1.0, downloaded / total)
                width = 30
                filled = int(width * pct)
                bar = "█" * filled + "░" * (width - filled)
                pct_text = f"{pct * 100:5.1f}%"
            else:
                bar = "?" * 30
                pct_text = "  ?  %"

            line = (
                f"[{bar}] {pct_text}  "
                f"{human_bytes(downloaded)} / {human_bytes(total)}  "
                f"{human_bytes(speed)}/s  ETA {human_duration(eta)}"
            )
            padding = max(0, self.last_line_len - len(line))
            print("\r" + line + (" " * padding), end="", flush=True)
            self.last_line_len = len(line)
        elif status == "finished":
            line = "Download vollständig; Verarbeitung läuft ..."
            padding = max(0, self.last_line_len - len(line))
            print("\r" + line + (" " * padding), end="", flush=True)
            self.last_line_len = len(line)
            self._clear_progress_line()

    def hash_progress(self, index: int, total: int, path: str) -> None:
        line = f"  [{index}/{total}] {path[:55]:55}"
        padding = max(0, self.last_line_len - len(line))
        print("\r" + line + (" " * padding), end="", flush=True)
        self.last_line_len = len(line)
        if index == total:
            self._clear_progress_line()

    def zip_progress(self, processed: int, total: int) -> None:
        pct = (processed / total) if total else 1.0
        width = 30
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        line = (
            f"[{bar}] {pct * 100:5.1f}%  "
            f"{human_bytes(processed)} / {human_bytes(total)}"
        )
        padding = max(0, self.last_line_len - len(line))
        print("\r" + line + (" " * padding), end="", flush=True)
        self.last_line_len = len(line)
        if total and processed >= total:
            self._clear_progress_line()

    def as_callbacks(self) -> ArchiveCallbacks:
        return ArchiveCallbacks(
            status=self.status,
            progress=self.progress,
            hash_progress=self.hash_progress,
            zip_progress=self.zip_progress,
        )


def print_verification_result(target: Path) -> int:
    result = verify_archive(target)
    section("ARCHIVPRÜFUNG")
    print(f"Ziel:               {result.target}")
    print(f"Typ:                {result.kind}")
    print(f"Capture ID:         {result.capture_id or 'nicht verfügbar'}")
    print(f"Manifest-Schema:    {result.schema or 'nicht verfügbar'}")
    print(f"Dateien geprüft:    {result.verified_files}/{result.checked_files}")
    print(f"Archivstruktur:     {'OK' if result.archive_structure_valid else 'FEHLER'}")
    print(f"Integrität:         {'OK' if result.ok else 'FEHLER'}")
    if result.issues:
        print()
        print("Festgestellte Probleme:")
        for issue in result.issues:
            print(f"  - {issue.path}: {issue.message}")
            if issue.expected:
                print(f"    Erwartet: {issue.expected}")
            if issue.actual:
                print(f"    Tatsächlich: {issue.actual}")
    print("─" * 72)

    if result.ok:
        success("ARCHIV VERIFIZIERT")
        return 0

    print(paint("✗ ARCHIVPRÜFUNG FEHLGESCHLAGEN", Style.RED, Style.BOLD))
    return 3


def parse_args() -> argparse.Namespace:
    if len(sys.argv) > 1 and sys.argv[1].lower() == "verify":
        parser = argparse.ArgumentParser(
            prog="ova verify",
            description="Verify an Open Video Archiver directory or ZIP without modifying it.",
        )
        parser.add_argument("archive", help="Open Video Archiver directory or ZIP")
        args = parser.parse_args(sys.argv[2:])
        args.command = "verify"
        args.url = None
        args.output = None
        return args

    parser = argparse.ArgumentParser(
        description="Documented, integrity-checked online-video archiving workflow"
    )
    parser.add_argument("url", nargs="?", help="Online video URL")
    parser.add_argument("-o", "--output", help="Base archive directory")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args()
    args.command = "archive"
    args.archive = None
    return args


def run() -> int:
    args = parse_args()
    print_banner()

    if args.command == "verify":
        return print_verification_result(Path(args.archive).expanduser())

    environment = preflight()

    raw_url = args.url or prompt("Video-URL oder Source ID")
    entered_url = normalize_url(raw_url)
    info("Quelle wird geprüft und Metadaten werden gelesen (noch kein Download) ...")
    try:
        source_info = fetch_metadata(entered_url)
    except AcquisitionError as exc:
        raise ArchiveEngineError(f"Quelle konnte nicht gelesen werden: {exc}") from exc

    canonical_url = str(source_info.get("webpage_url") or entered_url)
    subtitle_languages, subtitle_types = choose_subtitle_languages(source_info)
    display_metadata(source_info, subtitle_languages, subtitle_types)

    if not prompt_yes_no("Diese Quelle für das Archiv vorbereiten?"):
        raise ArchiveCancelled("Vom Benutzer abgebrochen.")

    rights_confirmed_at = require_rights_confirmation()

    root_value = args.output or prompt("Archiv-Basisverzeichnis", str(DEFAULT_ARCHIVE_ROOT))
    root = Path(root_value).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir():
        raise ArchiveEngineError(f"Ziel ist kein Verzeichnis: {root}")

    mode = choose_mode()
    quality_label, format_selector = choose_quality(source_info, mode)

    resume = None
    candidates = session_candidates(
        root,
        str(source_info["id"]),
        str(source_info.get("extractor_key") or source_info.get("extractor") or ""),
    )
    if candidates:
        candidate = candidates[0]
        warning(f"Unvollständige frühere Sitzung gefunden:\n  {candidate.folder}")
        if candidate.part_files:
            print(f"  Fortsetzbare Teildateien: {len(candidate.part_files)}")
        if prompt_yes_no("Diese Sitzung fortsetzen?"):
            resume = candidate
            original_mode = str(candidate.session.get("mode") or mode)
            original_quality = str(candidate.session.get("quality") or quality_label)
            original_selector = str(
                candidate.session.get("format_selector") or format_selector
            )
            if (
                original_mode != mode
                or original_quality != quality_label
                or original_selector != format_selector
            ):
                print(
                    "Hinweis: Für Resume werden Modus und Qualität der ursprünglichen "
                    "Sitzung beibehalten."
                )
            mode = original_mode
            quality_label = original_quality
            format_selector = original_selector

    request = ArchiveRequest(
        source_info=source_info,
        entered_url=entered_url,
        canonical_url=canonical_url,
        output_root=root,
        mode=mode,
        quality_label=quality_label,
        format_selector=format_selector,
        subtitle_languages=subtitle_languages,
        subtitle_types=subtitle_types,
        rights_confirmed_at=rights_confirmed_at,
        create_zip=False,
        resume_candidate=resume,
    )

    print("\nZielverzeichnis:")
    if resume:
        print(f"  {resume.folder}")
    else:
        print(f"  {root}")
    print(f"Modus: {mode}")
    print(f"Qualität: {quality_label}")
    print("Untertitel: Best Effort nach dem Mediendownload")
    print("Resume: aktiviert")

    if not prompt_yes_no("Download jetzt starten?"):
        raise ArchiveCancelled("Vor dem Download abgebrochen.")

    console = ConsoleCallbacks()
    token = CancellationToken()
    result = archive_source(
        request,
        callbacks=console.as_callbacks(),
        token=token,
        environment=environment,
    )

    section("ARCHIVIERUNG ERFOLGREICH")
    success("Kernarchiv und Integritätsprüfung abgeschlossen.")
    print(f"Dateien:            {result.files_count}")
    print(f"Archivumfang:       {human_bytes(result.total_size_bytes)}")
    print(f"Downloaddauer:      {human_duration(result.download_elapsed_seconds)}")
    if result.download_elapsed_seconds > 0:
        print(
            "Ø Dateivolumen/Zeit:"
            f"{human_bytes(result.total_size_bytes / result.download_elapsed_seconds)}/s"
        )
    print("SHA-256:            OK")
    print(f"Capture ID:         {result.capture_id}")
    print(f"Ziel:               {result.folder}")
    print("─" * 72)

    if not prompt_yes_no("Archiv zusätzlich als ZIP-Container verpacken?"):
        print("\nFertig. Quelldateien bleiben unverändert erhalten.")
        return 0

    zip_path, zip_checksum, zip_elapsed = create_verified_zip(
        result.folder,
        callbacks=console.as_callbacks(),
        token=token,
    )
    print("ZIP-Prüfung:        OK")
    print(f"ZIP-Größe:          {human_bytes(zip_path.stat().st_size)}")
    print(f"Verpackungsdauer:   {human_duration(zip_elapsed)}")
    print(f"ZIP-SHA-256:        {zip_checksum}")
    print(f"ZIP-Datei:          {zip_path}")

    if not prompt_yes_no(
        "Verifizierte Quelldateien nach erfolgreichem ZIP jetzt löschen?"
    ):
        print("\nFertig. ZIP und Quelldateien bleiben erhalten.")
        return 0

    warning("Dieser Schritt löscht die verifizierten Quelldateien dauerhaft.")
    if not prompt_yes_no("Löschen wirklich ausführen?"):
        print("Löschen nicht bestätigt. Quelldateien bleiben erhalten.")
        return 0

    deleted, leftovers = delete_verified_source_folder(result.folder, zip_path)
    print(f"{deleted} verifizierte Quelldatei(en) gelöscht.")
    if leftovers:
        print("Nicht zum ZIP gehörende Dateien wurden NICHT gelöscht:")
        for item in leftovers:
            print(f"  {item}")
    else:
        print("Keine zusätzlichen Quelldateien verblieben.")
    print(f"ZIP bleibt erhalten: {zip_path}")
    print(f"Prüfsumme:           {zip_checksum}")
    return 0


def main() -> None:
    try:
        raise SystemExit(run())
    except ArchiveCancelled as exc:
        print(
            paint(f"\n⚠ ABBRUCH: {exc}", Style.YELLOW, Style.BOLD),
            file=sys.stderr,
        )
        raise SystemExit(130)
    except ArchiveEngineError as exc:
        print(
            paint(f"\n✗ ABBRUCH: {exc}", Style.RED, Style.BOLD),
            file=sys.stderr,
        )
        raise SystemExit(2)
    except KeyboardInterrupt:
        print(
            paint(
                "\n⚠ Abbruch mit Ctrl+C. Vorhandene Teildateien bleiben für Resume erhalten.",
                Style.YELLOW,
                Style.BOLD,
            ),
            file=sys.stderr,
        )
        raise SystemExit(130)


if __name__ == "__main__":
    main()
