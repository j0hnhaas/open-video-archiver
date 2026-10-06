"""PySide6 desktop interface for Open Video Archiver."""

from __future__ import annotations

import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPointF, QRectF, QThread, QUrl, Signal, Slot, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ova_acquisition import (
    AcquisitionError,
    audio_quality_options,
    best_resolution,
    choose_subtitle_languages,
    fetch_metadata,
    format_upload_date,
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
from ova_core import VerificationResult, verify_archive
from ova_engine import (
    ArchiveCallbacks,
    ArchiveRequest,
    ArchiveResult,
    CancellationToken,
    archive_source,
    configure_runtime_path,
    human_bytes,
    human_duration,
    iso_local,
    normalize_url,
    preflight,
    session_candidates,
)


ACCENT = "#1769aa"


class MinimalCheckBox(QCheckBox):
    """Neutral checkbox: white field, grey border, dark check only."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setMinimumHeight(25)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        box_size = 16.0
        box_x = 1.0
        box_y = (self.height() - box_size) / 2.0

        enabled = self.isEnabled()
        border = QColor("#bcc6d1" if enabled else "#d7dde4")
        background = QColor("#ffffff" if enabled else "#f7f8fa")
        text_color = QColor("#28384d" if enabled else "#98a4b1")

        painter.setPen(QPen(border, 1.0))
        painter.setBrush(QBrush(background))
        painter.drawRoundedRect(
            QRectF(box_x, box_y, box_size, box_size),
            3.0,
            3.0,
        )

        if self.isChecked():
            pen = QPen(QColor("#273444" if enabled else "#8d98a5"), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(
                QPointF(box_x + 3.5, box_y + 8.5),
                QPointF(box_x + 6.8, box_y + 11.0),
            )
            painter.drawLine(
                QPointF(box_x + 6.8, box_y + 11.0),
                QPointF(box_x + 13.0, box_y + 4.8),
            )

        painter.setPen(text_color)
        text_rect = QRectF(
            box_x + box_size + 9.0,
            0.0,
            max(0.0, self.width() - box_size - 11.0),
            float(self.height()),
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.text(),
        )


def make_app_icon(size: int = 64) -> QIcon:
    """Create a small in-memory OVA icon without requiring an external asset."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor(ACCENT)))
    painter.drawRoundedRect(4, 4, size - 8, size - 8, 11, 11)

    painter.setBrush(QBrush(QColor("white")))
    triangle = QPolygonF(
        [
            QPointF(size * 0.40, size * 0.30),
            QPointF(size * 0.40, size * 0.70),
            QPointF(size * 0.72, size * 0.50),
        ]
    )
    painter.drawPolygon(triangle)
    painter.end()
    return QIcon(pixmap)


def icon_pixmap(size: int = 28) -> QPixmap:
    return make_app_icon(size).pixmap(size, size)


def fetch_thumbnail_bytes(url: str | None) -> bytes | None:
    """Best-effort thumbnail retrieval used only for the source preview."""
    if not url:
        return None
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 OVA/1.0"},
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            data = response.read(6 * 1024 * 1024)
            return data or None
    except Exception:
        return None


class AnalysisWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, raw_url: str) -> None:
        super().__init__()
        self.raw_url = raw_url

    @Slot()
    def run(self) -> None:
        try:
            entered_url = normalize_url(self.raw_url)
            info = fetch_metadata(entered_url)
            canonical_url = str(
                info.get("webpage_url")
                or info.get("original_url")
                or entered_url
            )
            subtitle_languages, subtitle_types = choose_subtitle_languages(info)
            thumbnail_data = fetch_thumbnail_bytes(str(info.get("thumbnail") or ""))
            self.finished.emit(
                {
                    "entered_url": entered_url,
                    "canonical_url": canonical_url,
                    "info": info,
                    "subtitle_languages": subtitle_languages,
                    "subtitle_types": subtitle_types,
                    "thumbnail_data": thumbnail_data,
                }
            )
        except (AcquisitionError, Exception) as exc:
            self.failed.emit(str(exc))


class ArchiveWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)
    status = Signal(str, str)
    progress = Signal(dict)
    hash_progress = Signal(int, int, str)
    zip_progress = Signal(int, int)

    def __init__(self, request: ArchiveRequest) -> None:
        super().__init__()
        self.request = request
        self.token = CancellationToken()

    @Slot()
    def run(self) -> None:
        callbacks = ArchiveCallbacks(
            status=lambda stage, message: self.status.emit(stage, message),
            progress=lambda data: self.progress.emit(data),
            hash_progress=lambda index, total, path: self.hash_progress.emit(
                index, total, path
            ),
            zip_progress=lambda processed, total: self.zip_progress.emit(
                processed, total
            ),
        )
        try:
            self.status.emit(
                "preflight",
                "Checking FFmpeg, Deno and yt-dlp components.",
            )
            environment = preflight()
            result = archive_source(
                self.request,
                callbacks=callbacks,
                token=self.token,
                environment=environment,
            )
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.token.cancel()


class VerifyWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, target: Path) -> None:
        super().__init__()
        self.target = target

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(verify_archive(self.target))
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.analysis: dict[str, Any] | None = None
        self.current_archive: Path | None = None
        self.current_zip: Path | None = None
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self.status_rows: dict[str, tuple[QLabel, QLabel, QLabel]] = {}

        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(make_app_icon())

        screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(1360, max(900, available.width() - 40))
            height = min(880, max(640, available.height() - 8))
            self.resize(width, height)
            self.setMinimumSize(
                min(1080, max(900, available.width() - 20)),
                min(680, max(620, available.height() - 20)),
            )
            self.move(
                available.center().x() - width // 2,
                available.center().y() - height // 2,
            )
        else:
            self.resize(1360, 820)
            self.setMinimumSize(1080, 680)

        self._build_ui()
        self._apply_style()
        self._set_page(0)

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)

        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(14, 18, 14, 16)
        side_layout.setSpacing(8)

        wordmark = QWidget()
        wordmark_layout = QHBoxLayout(wordmark)
        wordmark_layout.setContentsMargins(5, 0, 0, 8)
        wordmark_layout.setSpacing(8)
        wordmark_icon = QLabel()
        wordmark_icon.setPixmap(icon_pixmap(22))
        wordmark_text = QLabel("Open Video Archiver")
        wordmark_text.setObjectName("sideWordmark")
        wordmark_layout.addWidget(wordmark_icon)
        wordmark_layout.addWidget(wordmark_text)
        wordmark_layout.addStretch(1)
        side_layout.addWidget(wordmark)

        self.nav_buttons = []

        create_nav = QPushButton("Create Archive")
        create_nav.setObjectName("navButton")
        create_nav.setCheckable(True)
        create_nav.clicked.connect(lambda checked=False: self._set_page(0))
        side_layout.addWidget(create_nav)
        self.nav_buttons.append(create_nav)

        verify_nav = QPushButton("Verify Archive")
        verify_nav.setObjectName("navButton")
        verify_nav.setCheckable(True)
        verify_nav.clicked.connect(lambda checked=False: self._set_page(1))
        side_layout.addWidget(verify_nav)
        self.nav_buttons.append(verify_nav)

        side_layout.addStretch(1)

        about_nav = QPushButton("About")
        about_nav.setObjectName("navButton")
        about_nav.setCheckable(True)
        about_nav.clicked.connect(lambda checked=False: self._set_page(2))
        side_layout.addWidget(about_nav)
        self.nav_buttons.append(about_nav)

        footer = QLabel(
            f"Version {APP_VERSION}\n"
            f"{COPYRIGHT}\n\n"
            "Preserve · Document · Verify"
        )
        footer.setObjectName("sideFooter")
        footer.setWordWrap(True)
        side_layout.addWidget(footer)

        self.stack = QStackedWidget()
        self.stack.setObjectName("contentStack")
        self.stack.addWidget(self._build_archive_page())
        self.stack.addWidget(self._build_verify_page())
        self.stack.addWidget(self._build_about_page())

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)

    def _page_shell(
        self,
        title: str,
        subtitle: str | None = None,
    ) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 8, 24, 8)
        layout.setSpacing(12)

        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)

        if subtitle:
            lead = QLabel(subtitle)
            lead.setObjectName("pageLead")
            lead.setWordWrap(True)
            layout.addWidget(lead)

        return page, layout

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(11)

        heading = QLabel(title)
        heading.setObjectName("cardTitle")
        layout.addWidget(heading)
        return card, layout

    def _build_archive_page(self) -> QWidget:
        page, layout = self._page_shell("Create Archive")

        # 1 · SOURCE
        source_box, source_layout = self._card("1  Source")

        source_input = QHBoxLayout()
        source_input.setSpacing(10)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Online video URL or 11-character video ID")
        self.url_edit.returnPressed.connect(self._analyze_source)

        self.analyze_button = QPushButton("Analyze Source")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.clicked.connect(self._analyze_source)

        source_input.addWidget(self.url_edit, 1)
        source_input.addWidget(self.analyze_button)
        source_layout.addLayout(source_input)

        source_body = QHBoxLayout()
        source_body.setSpacing(18)

        self.thumbnail_label = QLabel("Source preview")
        self.thumbnail_label.setObjectName("thumbnail")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setFixedSize(256, 144)
        self.thumbnail_label.setScaledContents(False)
        source_body.addWidget(
            self.thumbnail_label,
            0,
            Qt.AlignmentFlag.AlignTop,
        )

        metadata_widget = QWidget()
        metadata_layout = QVBoxLayout(metadata_widget)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(7)

        self.meta_title = QLabel("—")
        self.meta_title.setObjectName("videoTitle")
        self.meta_title.setWordWrap(True)
        self.meta_title.setMaximumHeight(42)
        metadata_layout.addWidget(self.meta_title)

        self.meta_channel = QLabel("—")
        self.meta_id = QLabel("—")
        self.meta_upload = QLabel("—")
        self.meta_duration = QLabel("—")
        self.meta_resolution = QLabel("—")
        self.meta_subtitles = QLabel("—")
        self.meta_subtitles.setWordWrap(True)

        meta_grid = QGridLayout()
        meta_grid.setContentsMargins(0, 0, 0, 0)
        meta_grid.setHorizontalSpacing(24)
        meta_grid.setVerticalSpacing(7)
        meta_grid.setColumnStretch(0, 1)
        meta_grid.setColumnStretch(1, 1)

        def meta_cell(label_text: str, value: QLabel) -> QWidget:
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(1)
            label = QLabel(label_text)
            label.setObjectName("metaLabel")
            value.setObjectName("metaValue")
            cell_layout.addWidget(label)
            cell_layout.addWidget(value)
            return cell

        cells = (
            ("CHANNEL / UPLOADER", self.meta_channel),
            ("SOURCE ID", self.meta_id),
            ("UPLOADED", self.meta_upload),
            ("DURATION", self.meta_duration),
            ("BEST QUALITY", self.meta_resolution),
            ("SUBTITLES", self.meta_subtitles),
        )
        for index, (label_text, value) in enumerate(cells):
            row = index // 2
            column = index % 2
            meta_grid.addWidget(meta_cell(label_text, value), row, column)

        metadata_layout.addLayout(meta_grid)
        metadata_layout.addStretch(1)

        source_body.addWidget(metadata_widget, 1)
        source_layout.addLayout(source_body)

        # 2 · DOWNLOAD & ARCHIVE
        download_box, download_layout = self._card("2  Download & Archive")

        self.download_hint = QLabel(
            "Analyze a source to configure download and archive options."
        )
        self.download_hint.setObjectName("mutedHint")
        download_layout.addWidget(self.download_hint)

        options_row = QHBoxLayout()
        options_row.setSpacing(16)

        mode_block = QWidget()
        mode_block_layout = QVBoxLayout(mode_block)
        mode_block_layout.setContentsMargins(0, 0, 0, 0)
        mode_block_layout.setSpacing(5)
        mode_label = QLabel("MODE")
        mode_label.setObjectName("fieldLabel")
        mode_block_layout.addWidget(mode_label)

        segmented = QWidget()
        segmented_layout = QHBoxLayout(segmented)
        segmented_layout.setContentsMargins(0, 0, 0, 0)
        segmented_layout.setSpacing(0)

        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.video_audio_radio = QPushButton("Video + Audio")
        self.video_only_radio = QPushButton("Video only")
        self.audio_only_radio = QPushButton("Audio only")

        for button, mode in (
            (self.video_audio_radio, "video+audio"),
            (self.video_only_radio, "video-only"),
            (self.audio_only_radio, "audio-only"),
        ):
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setProperty("mode", mode)
            self.mode_group.addButton(button)
            segmented_layout.addWidget(button)

        mode_block_layout.addWidget(segmented)

        quality_block = QWidget()
        quality_block_layout = QVBoxLayout(quality_block)
        quality_block_layout.setContentsMargins(0, 0, 0, 0)
        quality_block_layout.setSpacing(5)
        quality_label = QLabel("QUALITY")
        quality_label.setObjectName("fieldLabel")
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("Analyze source first")
        self.quality_combo.setEnabled(False)
        quality_block_layout.addWidget(quality_label)
        quality_block_layout.addWidget(self.quality_combo)

        for button in self.mode_group.buttons():
            button.toggled.connect(self._rebuild_quality_choices)
        self.video_audio_radio.setChecked(True)

        options_row.addWidget(mode_block, 1)
        options_row.addWidget(quality_block, 1)
        download_layout.addLayout(options_row)

        destination_label = QLabel("DESTINATION")
        destination_label.setObjectName("fieldLabel")
        download_layout.addWidget(destination_label)

        destination_row = QHBoxLayout()
        destination_row.setSpacing(8)
        self.destination_edit = QLineEdit(str(DEFAULT_ARCHIVE_ROOT))
        self.destination_button = QPushButton("Browse…")
        self.destination_button.clicked.connect(self._choose_destination)
        destination_row.addWidget(self.destination_edit, 1)
        destination_row.addWidget(self.destination_button)
        download_layout.addLayout(destination_row)

        footer_row = QHBoxLayout()
        footer_row.setSpacing(18)

        consent_widget = QWidget()
        consent_layout = QVBoxLayout(consent_widget)
        consent_layout.setContentsMargins(0, 1, 0, 0)
        consent_layout.setSpacing(4)

        self.zip_checkbox = MinimalCheckBox("Create verified ZIP container")
        self.zip_checkbox.setChecked(True)
        self.rights_checkbox = MinimalCheckBox(
            "I confirm that I am authorized to preserve this source material."
        )
        self.rights_checkbox.setToolTip(RIGHTS_STATEMENT)
        self.rights_checkbox.toggled.connect(self._update_create_enabled)

        consent_layout.addWidget(self.zip_checkbox)
        consent_layout.addWidget(self.rights_checkbox)
        footer_row.addWidget(consent_widget, 1)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._cancel_archive)

        self.create_button = QPushButton("Create Archive Package")
        self.create_button.setObjectName("primaryLargeButton")
        self.create_button.setEnabled(False)
        self.create_button.setToolTip(
            "Analyze the source and confirm authorization first."
        )
        self.create_button.clicked.connect(self._start_archive)

        footer_row.addWidget(
            self.cancel_button,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        footer_row.addWidget(
            self.create_button,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )
        download_layout.addLayout(footer_row)

        # 3 · STATUS
        status_box, status_layout = self._card("3  Status")

        progress_row = QHBoxLayout()
        progress_row.setSpacing(12)
        self.archive_progress = QProgressBar()
        self.archive_progress.setRange(0, 100)
        self.archive_progress.setValue(0)
        self.archive_progress.setTextVisible(True)

        self.progress_detail = QLabel("Ready")
        self.progress_detail.setObjectName("progressDetail")
        self.progress_detail.setMinimumWidth(125)
        self.progress_detail.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        progress_row.addWidget(self.archive_progress, 1)
        progress_row.addWidget(self.progress_detail)
        status_layout.addLayout(progress_row)

        self.timeline = QFrame()
        self.timeline.setObjectName("timeline")
        timeline_layout = QGridLayout(self.timeline)
        timeline_layout.setContentsMargins(12, 6, 12, 6)
        timeline_layout.setHorizontalSpacing(9)
        timeline_layout.setVerticalSpacing(2)
        timeline_layout.setColumnStretch(1, 1)

        steps = (
            ("source", "Source analyzed"),
            ("runtime", "Runtime checked"),
            ("media", "Media downloaded"),
            ("subtitles", "Subtitles processed"),
            ("integrity", "Integrity verified (SHA-256)"),
            ("zip", "Verified ZIP created"),
        )
        for row, (key, text) in enumerate(steps):
            icon = QLabel("○")
            icon.setObjectName("stepPending")
            label = QLabel(text)
            label.setObjectName("stepText")
            timestamp = QLabel("")
            timestamp.setObjectName("stepTime")
            timestamp.setAlignment(Qt.AlignmentFlag.AlignRight)
            icon.setFixedHeight(18)
            label.setFixedHeight(18)
            timestamp.setFixedHeight(18)
            timeline_layout.addWidget(icon, row, 0)
            timeline_layout.addWidget(label, row, 1)
            timeline_layout.addWidget(timestamp, row, 2)
            self.status_rows[key] = (icon, label, timestamp)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setObjectName("technicalLog")

        self.status_stack = QStackedWidget()
        self.status_stack.setObjectName("statusStack")
        self.status_stack.setMinimumHeight(118)
        self.status_stack.setMaximumHeight(124)

        details_row = QHBoxLayout()
        self.log_toggle = QPushButton("Details ▾")
        self.log_toggle.setObjectName("detailsButton")
        self.log_toggle.setCheckable(True)
        self.log_toggle.toggled.connect(self._toggle_log)
        details_row.addWidget(self.log_toggle)
        details_row.addStretch(1)
        status_layout.addLayout(details_row)

        self.complete_box = QFrame()
        self.complete_box.setObjectName("completeBox")
        complete_layout = QHBoxLayout(self.complete_box)
        complete_layout.setContentsMargins(16, 10, 16, 10)
        complete_layout.setSpacing(14)

        complete_mark = QLabel("✓")
        complete_mark.setObjectName("completeMark")

        complete_text_widget = QWidget()
        complete_text_layout = QVBoxLayout(complete_text_widget)
        complete_text_layout.setContentsMargins(0, 0, 0, 0)
        complete_text_layout.setSpacing(3)

        self.complete_title = QLabel("Archive complete")
        self.complete_title.setObjectName("completeTitle")
        self.complete_summary = QLabel("")
        self.complete_summary.setObjectName("completeSummary")
        self.complete_summary.setWordWrap(True)
        self.complete_capture = QLabel("")
        self.complete_capture.setObjectName("completeCapture")
        self.complete_capture.setWordWrap(True)

        complete_text_layout.addWidget(self.complete_title)
        complete_text_layout.addWidget(self.complete_summary)
        complete_text_layout.addWidget(self.complete_capture)

        complete_buttons = QHBoxLayout()
        self.open_folder_button = QPushButton("Open Folder")
        self.open_folder_button.clicked.connect(self._open_archive_folder)
        self.verify_result_button = QPushButton("Verify Archive")
        self.verify_result_button.clicked.connect(self._verify_current_archive)
        complete_buttons.addWidget(self.open_folder_button)
        complete_buttons.addWidget(self.verify_result_button)

        complete_layout.addWidget(complete_mark)
        complete_layout.addWidget(complete_text_widget, 1)
        complete_layout.addLayout(complete_buttons)

        self.status_stack.addWidget(self.timeline)
        self.status_stack.addWidget(self.log_view)
        self.status_stack.addWidget(self.complete_box)
        self.status_stack.setCurrentWidget(self.timeline)
        status_layout.insertWidget(1, self.status_stack)

        layout.addWidget(source_box)
        layout.addWidget(download_box)
        layout.addWidget(status_box)
        layout.addStretch(1)

        self._set_download_enabled(False)

        scroll = QScrollArea()
        scroll.setObjectName("archiveScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        scroll.setWidget(page)
        return scroll

    def _build_verify_page(self) -> QWidget:
        page, layout = self._page_shell(
            "Verify Archive",
            "Check a OVA archive directory or ZIP against its manifest and SHA-256 "
            "records without modifying it.",
        )

        target_box, target_layout = self._card("Archive")
        row = QHBoxLayout()
        row.setSpacing(8)

        self.verify_target_edit = QLineEdit()
        self.verify_target_edit.setPlaceholderText(
            "OVA archive directory or verified ZIP container"
        )

        browse_folder = QPushButton("Folder…")
        browse_folder.clicked.connect(self._choose_verify_folder)
        browse_zip = QPushButton("ZIP…")
        browse_zip.clicked.connect(self._choose_verify_zip)

        row.addWidget(self.verify_target_edit, 1)
        row.addWidget(browse_folder)
        row.addWidget(browse_zip)
        target_layout.addLayout(row)

        self.verify_button = QPushButton("Verify Archive")
        self.verify_button.setObjectName("primaryButton")
        self.verify_button.clicked.connect(self._start_verify)
        target_layout.addWidget(
            self.verify_button,
            0,
            Qt.AlignmentFlag.AlignLeft,
        )

        result_box, result_layout = self._card("Verification Result")
        self.verify_headline = QLabel("No archive checked yet.")
        self.verify_headline.setObjectName("verificationHeadline")

        self.verify_details = QPlainTextEdit()
        self.verify_details.setReadOnly(True)

        result_layout.addWidget(self.verify_headline)
        result_layout.addWidget(self.verify_details, 1)

        layout.addWidget(target_box)
        layout.addWidget(result_box, 1)
        return page

    def _build_about_page(self) -> QWidget:
        page, layout = self._page_shell(
            "About",
            "A focused preservation tool for documented acquisition of online "
            "audiovisual sources.",
        )

        box, box_layout = self._card(APP_NAME)

        icon_row = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(icon_pixmap(48))
        title_block = QLabel(
            f"<b>{APP_NAME}</b><br>"
            f"<span style='color:#687386'>OVA {APP_VERSION} · Desktop</span>"
        )
        title_block.setObjectName("aboutTitle")
        icon_row.addWidget(icon)
        icon_row.addWidget(title_block)
        icon_row.addStretch(1)
        box_layout.addLayout(icon_row)

        body = QLabel(
            f"<b>{POSITIONING}</b><br><br>"
            "OVA preserves media together with source metadata, acquisition provenance, "
            "a versioned manifest and SHA-256 integrity records.<br><br>"
            "The desktop application and command-line interface use the same Python "
            "acquisition and verification engine.<br><br>"
            f"<b>{COPYRIGHT}</b><br>"
            f'<a href="{REPO_URL}">{REPO_URL}</a>'
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        box_layout.addWidget(body)

        note = QLabel(
            "<b>Scope:</b> OVA documents acquisition and integrity. It does not "
            "independently certify authorship, authenticity or legal admissibility."
        )
        note.setWordWrap(True)
        note.setObjectName("note")
        box_layout.addWidget(note)

        layout.addWidget(box)
        layout.addStretch(1)
        return page

    def _apply_style(self) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setFont(QFont("Segoe UI", 10))

        self.setStyleSheet(
            f"""
            QMainWindow, #appRoot, #page, #contentStack {{
                background: #f4f7fb;
                color: #0f172a;
            }}

            QScrollArea#archiveScroll {{
                background: #f4f7fb;
                border: 0;
            }}
            QScrollArea#archiveScroll > QWidget > QWidget {{
                background: #f4f7fb;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #cbd5e1;
                border-radius: 4px;
                min-height: 32px;
            }}
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar::add-page:vertical,
            QScrollBar::sub-page:vertical {{
                background: transparent;
            }}

            #sidebar {{
                background: #f8fafc;
                border-right: 1px solid #e2e8f0;
            }}
            #sideWordmark {{
                color: #0f172a;
                font-size: 15px;
                font-weight: 700;
            }}
            #sideFooter {{
                color: #718096;
                font-size: 10px;
                padding: 8px 5px 2px 5px;
            }}
            #navButton {{
                text-align: left;
                padding: 10px 12px;
                border: 0;
                border-left: 3px solid transparent;
                border-radius: 6px;
                background: transparent;
                color: #27364a;
                font-size: 12px;
                font-weight: 600;
            }}
            #navButton:hover {{
                background: #f1f5f9;
            }}
            #navButton:checked {{
                background: #e8f2fc;
                border-left: 3px solid {ACCENT};
                color: #0f4f84;
            }}

            #pageTitle {{
                color: #0f172a;
                font-size: 20px;
                font-weight: 600;
            }}
            #pageLead {{
                color: #64748b;
                font-size: 12px;
            }}

            QFrame#card {{
                background: white;
                border: 1px solid #e2e8f0;
                border-radius: 9px;
            }}
            #cardTitle {{
                color: #0f172a;
                font-size: 14px;
                font-weight: 600;
            }}

            QLineEdit, QComboBox, QPlainTextEdit {{
                background: white;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 7px 9px;
                selection-background-color: {ACCENT};
                color: #0f172a;
                font-size: 12px;
            }}
            QLineEdit, QComboBox {{
                min-height: 20px;
            }}
            QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{
                border: 1px solid {ACCENT};
            }}
            QLineEdit:disabled, QComboBox:disabled {{
                background: #f8fafc;
                color: #94a3b8;
            }}

            QPushButton {{
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 7px 12px;
                min-height: 20px;
                color: #27364a;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: #f1f5f9;
            }}
            QPushButton:disabled {{
                color: #94a3b8;
                background: #f1f5f9;
                border-color: #e2e8f0;
            }}

            #primaryButton {{
                background: {ACCENT};
                color: white;
                border: 1px solid {ACCENT};
                padding: 7px 15px;
            }}
            #primaryButton:hover {{
                background: #12578e;
            }}
            #secondaryButton {{
                background: white;
                color: #1f4f78;
                border: 1px solid #aebdca;
                padding: 7px 15px;
            }}

            #primaryLargeButton {{
                background: {ACCENT};
                color: white;
                border: 1px solid {ACCENT};
                border-radius: 6px;
                padding: 9px 20px;
                min-width: 220px;
                min-height: 24px;
            }}
            #primaryLargeButton:hover {{
                background: #12578e;
            }}
            #primaryLargeButton:disabled {{
                background: #d9e4ee;
                border-color: #d9e4ee;
                color: #7f8f9f;
            }}

            #segmentButton {{
                background: white;
                color: #334155;
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                padding: 6px 12px;
                min-height: 20px;
                min-width: 92px;
            }}
            #segmentButton:checked {{
                background: #e8f2fc;
                color: #0f4f84;
                border-color: #7aaed5;
            }}
            #segmentButton:disabled {{
                background: #f8fafc;
                color: #a1aebb;
                border-color: #e2e8f0;
            }}

            #thumbnail {{
                background: #eef2f6;
                color: #8794a3;
                border: 1px solid #d8e0e8;
                border-radius: 7px;
                font-size: 11px;
            }}
            #videoTitle {{
                color: #0f172a;
                font-size: 15px;
                font-weight: 600;
            }}
            #metaLabel, #fieldLabel {{
                color: #64748b;
                font-size: 10px;
                font-weight: 600;
            }}
            #metaValue {{
                color: #0f172a;
                font-size: 12px;
                font-weight: 400;
            }}
            #mutedHint {{
                color: #64748b;
                font-size: 11px;
            }}

            QProgressBar {{
                border: 1px solid #cbd5e1;
                border-radius: 5px;
                text-align: center;
                background: #e9eef3;
                min-height: 17px;
                color: #334155;
                font-size: 10px;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: 4px;
            }}
            #progressDetail {{
                color: #64748b;
                font-size: 11px;
            }}

            #timeline {{
                background: #fbfcfd;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
            }}
            #stepPending {{
                color: #94a3b8;
                font-weight: 600;
                min-width: 17px;
            }}
            #stepText {{
                color: #394a60;
                font-size: 11px;
            }}
            #stepTime {{
                color: #8491a1;
                font-size: 10px;
            }}
            #technicalLog {{
                background: #fbfcfd;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                font-size: 10px;
            }}
            #detailsButton {{
                background: transparent;
                border: 0;
                color: #516173;
                padding: 3px 2px;
                min-height: 18px;
            }}
            #detailsButton:hover {{
                color: #0f4f84;
                background: transparent;
            }}

            #completeBox {{
                background: #effaf4;
                border: 1px solid #b9dfc8;
                border-radius: 7px;
            }}
            #completeMark {{
                color: #1f9d55;
                font-size: 22px;
                font-weight: 700;
            }}
            #completeTitle {{
                color: #176b3b;
                font-size: 13px;
                font-weight: 600;
            }}
            #completeSummary {{
                color: #2e6848;
                font-size: 10px;
            }}
            #completeCapture {{
                color: #567064;
                font-size: 10px;
            }}

            #verificationHeadline {{
                color: #243449;
                font-size: 16px;
                font-weight: 600;
            }}
            #note {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 12px;
                color: #4d5967;
            }}
            #aboutTitle {{
                color: #243449;
                font-size: 13px;
            }}
            """
        )

    def _set_page(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def _start_worker(
        self,
        worker: QObject,
        finished_signal: Signal,
        finished_slot: Any,
        failed_signal: Signal,
        failed_slot: Any,
    ) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        finished_signal.connect(finished_slot)
        failed_signal.connect(failed_slot)
        finished_signal.connect(worker.deleteLater)
        failed_signal.connect(worker.deleteLater)
        finished_signal.connect(thread.quit)
        failed_signal.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_worker)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot()
    def _clear_worker(self) -> None:
        self._thread = None
        self._worker = None
        self._update_create_enabled()

    def _current_mode(self) -> str:
        button = self.mode_group.checkedButton()
        if button is None:
            return "video+audio"
        return str(button.property("mode") or "video+audio")

    def _toggle_log(self, checked: bool) -> None:
        if checked:
            self.status_stack.setCurrentWidget(self.log_view)
        elif self.current_archive is not None:
            self.status_stack.setCurrentWidget(self.complete_box)
        else:
            self.status_stack.setCurrentWidget(self.timeline)
        self.log_toggle.setText("Details ▴" if checked else "Details ▾")

    def _set_step(
        self,
        key: str,
        state: str,
        detail: str | None = None,
    ) -> None:
        row = self.status_rows.get(key)
        if row is None:
            return
        icon, label, timestamp = row

        if state == "done":
            icon.setText("✓")
            icon.setStyleSheet("color:#1f9d55; font-weight:700;")
            timestamp.setText(datetime.now().strftime("%H:%M:%S"))
        elif state == "active":
            icon.setText("●")
            icon.setStyleSheet(f"color:{ACCENT}; font-weight:700;")
            timestamp.setText("")
        elif state == "skipped":
            icon.setText("—")
            icon.setStyleSheet("color:#9aa6b2; font-weight:700;")
            timestamp.setText("")
        else:
            icon.setText("○")
            icon.setStyleSheet("color:#9aa6b2; font-weight:700;")
            timestamp.setText("")

        if detail:
            label.setText(detail)

    def _reset_archive_status(self) -> None:
        defaults = {
            "runtime": "Runtime checked",
            "media": "Media downloaded",
            "subtitles": "Subtitles processed",
            "integrity": "Integrity verified (SHA-256)",
            "zip": "Verified ZIP created",
        }
        for key, text in defaults.items():
            row = self.status_rows.get(key)
            if row:
                row[1].setText(text)
            self._set_step(key, "pending")
        if self.analysis:
            self._set_step("source", "done")
        else:
            self._set_step("source", "pending")
        self.log_toggle.blockSignals(True)
        self.log_toggle.setChecked(False)
        self.log_toggle.setText("Details ▾")
        self.log_toggle.blockSignals(False)
        self.status_stack.setCurrentWidget(self.timeline)
        self.archive_progress.setRange(0, 100)
        self.archive_progress.setValue(0)
        self.progress_detail.setText("Ready")

    def _analyze_source(self) -> None:
        raw = self.url_edit.text().strip()
        if not raw:
            QMessageBox.warning(
                self,
                "Source required",
                "Enter a Online video URL or video ID.",
            )
            return
        if self._thread is not None:
            return

        self.analysis = None
        self.current_archive = None
        self.current_zip = None
        self.log_toggle.blockSignals(True)
        self.log_toggle.setChecked(False)
        self.log_toggle.setText("Details ▾")
        self.log_toggle.blockSignals(False)
        self.status_stack.setCurrentWidget(self.timeline)
        self._set_download_enabled(False)
        self._set_analyze_primary(True)
        self.analyze_button.setEnabled(False)
        self.archive_progress.setRange(0, 0)
        self.progress_detail.setText("Analyzing source…")
        self.thumbnail_label.setPixmap(QPixmap())
        self.thumbnail_label.setText("Analyzing source…")
        self._append_log("Inspecting source metadata.")

        for key in self.status_rows:
            self._set_step(key, "pending")
        self._set_step("source", "active")

        worker = AnalysisWorker(raw)
        self._start_worker(
            worker,
            worker.finished,
            self._analysis_finished,
            worker.failed,
            self._analysis_failed,
        )

    @Slot(dict)
    def _analysis_finished(self, data: dict) -> None:
        self.analysis = data
        info = data["info"]
        self.url_edit.setText(str(data.get("canonical_url") or data.get("entered_url") or ""))

        self.archive_progress.setRange(0, 100)
        self.archive_progress.setValue(0)
        self.analyze_button.setEnabled(True)

        self.meta_title.setText(str(info.get("title") or "unknown"))
        self.meta_channel.setText(
            str(info.get("channel") or info.get("uploader") or "unknown")
        )
        self.meta_id.setText(str(info.get("id") or "unknown"))
        self.meta_upload.setText(format_upload_date(info.get("upload_date")))
        self.meta_duration.setText(human_duration(info.get("duration")))
        self.meta_resolution.setText(best_resolution(info))

        subtitle_languages = data["subtitle_languages"]
        subtitle_types = data["subtitle_types"]
        if subtitle_languages:
            parts = [
                f"{lang} — "
                + (
                    "manual"
                    if subtitle_types.get(lang) == "manual"
                    else "automatic (original language)"
                )
                for lang in subtitle_languages
            ]
            self.meta_subtitles.setText("\n".join(parts))
        else:
            self.meta_subtitles.setText("None reliably identifiable")

        thumbnail_data = data.get("thumbnail_data")
        if thumbnail_data:
            pixmap = QPixmap()
            if pixmap.loadFromData(thumbnail_data):
                scaled = pixmap.scaled(
                    self.thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.thumbnail_label.setPixmap(scaled)
                self.thumbnail_label.setText("")
            else:
                self.thumbnail_label.setText("Preview unavailable")
        else:
            self.thumbnail_label.setText("Preview unavailable")

        self._set_download_enabled(True)
        self.rights_checkbox.setChecked(False)
        self._rebuild_quality_choices()
        self._set_analyze_primary(False)

        self._set_step("source", "done")
        self.progress_detail.setText("Source analyzed")
        self._append_log(
            f"Source identified: {info.get('title') or 'unknown'} "
            f"[{info.get('id') or '?'}]"
        )
        self._update_create_enabled()

    @Slot(str)
    def _analysis_failed(self, message: str) -> None:
        self.archive_progress.setRange(0, 100)
        self.archive_progress.setValue(0)
        self.analyze_button.setEnabled(True)
        self.thumbnail_label.setText("Preview unavailable")
        self._set_download_enabled(False)
        self._set_analyze_primary(True)
        self._set_step("source", "pending")
        self.progress_detail.setText("Source analysis failed")
        self._append_log("ERROR: " + message)
        QMessageBox.critical(self, "Source analysis failed", message)

    def _rebuild_quality_choices(self) -> None:
        self.quality_combo.clear()
        if not self.analysis:
            self.quality_combo.addItem("Analyze source first")
            return

        info = self.analysis["info"]
        mode = self._current_mode()

        if mode in {"video+audio", "video-only"}:
            best_label = (
                "Best available video + audio"
                if mode == "video+audio"
                else "Best available video"
            )
            best_selector = "bv*+ba/b" if mode == "video+audio" else "bv"
            self.quality_combo.addItem(
                best_label,
                (best_label, best_selector),
            )

            for item in video_quality_options(info):
                height = int(item["height"])
                width = int(item["width"])
                fps = f" · up to {item['fps']:g} fps" if item["fps"] else ""
                codecs = (
                    f" · {', '.join(item['codecs'])}"
                    if item["codecs"]
                    else ""
                )
                size = (
                    f" · video track ~{human_bytes(item['size_hint'])}"
                    if item.get("size_hint")
                    else ""
                )
                label = (
                    f"{width}×{height} ({height}p)"
                    f"{fps}{codecs}{size}"
                )
                selector = (
                    f"bv*[height={height}]+ba/b[height={height}]"
                    if mode == "video+audio"
                    else f"bv*[height={height}]"
                )
                self.quality_combo.addItem(
                    label,
                    (f"{width}x{height} ({height}p)", selector),
                )
        else:
            self.quality_combo.addItem(
                "Best available audio",
                ("Beste verfügbare Audioqualität", "ba/b"),
            )
            for item in audio_quality_options(info):
                abr = (
                    f"{item['abr']:.0f} kbit/s"
                    if item["abr"]
                    else "unknown bitrate"
                )
                label = f"{abr} · {item['acodec']} · {item['ext']}"
                if item.get("size"):
                    label += f" · ~{human_bytes(item['size'])}"
                self.quality_combo.addItem(
                    label,
                    (
                        f"{abr}, {item['acodec']}, {item['ext']} "
                        f"(Format {item['format_id']})",
                        item["format_id"],
                    ),
                )

    def _choose_destination(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose archive destination",
            self.destination_edit.text() or str(DEFAULT_ARCHIVE_ROOT),
        )
        if path:
            self.destination_edit.setText(path)

    def _set_download_enabled(self, enabled: bool) -> None:
        for button in self.mode_group.buttons():
            button.setEnabled(enabled)
        self.quality_combo.setEnabled(enabled)
        self.destination_edit.setEnabled(enabled)
        self.destination_button.setEnabled(enabled)
        self.zip_checkbox.setEnabled(enabled)
        self.rights_checkbox.setEnabled(enabled)
        self.download_hint.setVisible(not enabled)
        if not enabled:
            self.rights_checkbox.setChecked(False)
            self.quality_combo.clear()
            self.quality_combo.addItem("Analyze source first")

    def _set_analyze_primary(self, primary: bool) -> None:
        self.analyze_button.setObjectName(
            "primaryButton" if primary else "secondaryButton"
        )
        self.analyze_button.style().unpolish(self.analyze_button)
        self.analyze_button.style().polish(self.analyze_button)

    def _update_create_enabled(self) -> None:
        enabled = (
            self.analysis is not None
            and self.rights_checkbox.isChecked()
            and self._thread is None
        )
        self.create_button.setEnabled(enabled)
        if enabled:
            self.create_button.setToolTip("")
        elif self.analysis is None:
            self.create_button.setToolTip("Analyze the source first.")
        else:
            self.create_button.setToolTip("Confirm authorization to continue.")

    def _start_archive(self) -> None:
        if not self.analysis or self._thread is not None:
            return

        if not self.rights_checkbox.isChecked():
            QMessageBox.warning(
                self,
                "Rights confirmation",
                RIGHTS_STATEMENT,
            )
            return

        root_text = self.destination_edit.text().strip()
        if not root_text:
            QMessageBox.warning(
                self,
                "Archive destination",
                "Choose an archive destination.",
            )
            return
        root = Path(root_text).expanduser()

        info = self.analysis["info"]
        resume = None
        candidates = session_candidates(root, str(info.get("id") or ""))
        if candidates:
            candidate = candidates[0]
            answer = QMessageBox.question(
                self,
                "Incomplete archive found",
                "An incomplete earlier archive for this source was found:\n\n"
                f"{candidate.folder}\n\nResume that archive?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                resume = candidate

        quality_data = self.quality_combo.currentData()
        if not quality_data:
            QMessageBox.warning(
                self,
                "Quality",
                "Choose a download quality.",
            )
            return
        quality_label, format_selector = quality_data

        request = ArchiveRequest(
            source_info=info,
            entered_url=self.analysis["entered_url"],
            canonical_url=self.analysis["canonical_url"],
            output_root=root.resolve(),
            mode=self._current_mode(),
            quality_label=str(quality_label),
            format_selector=str(format_selector),
            subtitle_languages=list(self.analysis["subtitle_languages"]),
            subtitle_types=dict(self.analysis["subtitle_types"]),
            rights_confirmed_at=iso_local(),
            create_zip=self.zip_checkbox.isChecked(),
            resume_candidate=resume,
        )

        self.current_archive = None
        self.current_zip = None
        self._reset_archive_status()
        self._set_step("source", "done")
        self._set_step("runtime", "active")

        self.create_button.setEnabled(False)
        self.cancel_button.setVisible(True)
        self.progress_detail.setText("Starting archive…")
        self._append_log("Starting archive creation.")

        worker = ArchiveWorker(request)
        worker.status.connect(self._archive_status)
        worker.progress.connect(self._archive_progress_changed)
        worker.hash_progress.connect(self._hash_progress_changed)
        worker.zip_progress.connect(self._zip_progress_changed)

        self._start_worker(
            worker,
            worker.finished,
            self._archive_finished,
            worker.failed,
            self._archive_failed,
        )

    def _cancel_archive(self) -> None:
        worker = self._worker
        if isinstance(worker, ArchiveWorker):
            worker.cancel()
            self.progress_detail.setText("Cancellation requested…")
            self._append_log(
                "Cancellation requested; partial download data will be preserved."
            )

    @Slot(str, str)
    def _archive_status(self, stage: str, message: str) -> None:
        self._append_log(f"[{stage}] {message}")

        if stage == "preflight":
            self._set_step("runtime", "active")
            self.progress_detail.setText("Checking runtime environment…")
            self.archive_progress.setRange(0, 0)
        elif stage == "prepared":
            self._set_step("runtime", "done")
            self._set_step("media", "active")
            self.progress_detail.setText("Archive prepared")
            self.archive_progress.setRange(0, 0)
        elif stage == "download":
            self._set_step("media", "active")
        elif stage == "subtitles":
            self._set_step("media", "done")
            self._set_step("subtitles", "active")
            self.progress_detail.setText("Processing source subtitles…")
            self.archive_progress.setRange(0, 0)
        elif stage == "integrity":
            self._set_step("subtitles", "done")
            self._set_step("integrity", "active")
            self.progress_detail.setText("Creating and verifying integrity records…")
            self.archive_progress.setRange(0, 0)
        elif stage == "zip":
            self._set_step("integrity", "done")
            self._set_step("zip", "active")
            self.progress_detail.setText("Creating verified ZIP container…")
        elif stage == "completed":
            self._set_step("integrity", "done")
            if self.zip_checkbox.isChecked():
                self._set_step("zip", "done")
            else:
                self._set_step("zip", "skipped")
            self.progress_detail.setText("Archive complete")

    @Slot(dict)
    def _archive_progress_changed(self, data: dict) -> None:
        status = data.get("status")
        if status == "downloading":
            downloaded = int(data.get("downloaded_bytes") or 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            speed = data.get("speed")
            eta = data.get("eta")

            if total:
                self.archive_progress.setRange(0, 100)
                self.archive_progress.setValue(
                    int(min(100, downloaded / total * 100))
                )
            else:
                self.archive_progress.setRange(0, 0)

            self.progress_detail.setText(
                f"{human_bytes(downloaded)} / {human_bytes(total)} · "
                f"{human_bytes(speed)}/s · ETA {human_duration(eta)}"
            )
        elif status == "finished":
            self._set_step("media", "done")
            self.archive_progress.setRange(0, 0)
            self.progress_detail.setText("Media download complete; processing…")

    @Slot(int, int, str)
    def _hash_progress_changed(
        self,
        index: int,
        total: int,
        path: str,
    ) -> None:
        self.archive_progress.setRange(0, max(1, total))
        self.archive_progress.setValue(index)
        self.progress_detail.setText(
            f"Hashing {index}/{total} · {Path(path).name}"
        )

    @Slot(int, int)
    def _zip_progress_changed(
        self,
        processed: int,
        total: int,
    ) -> None:
        self.archive_progress.setRange(0, 100)
        value = int(processed / total * 100) if total else 100
        self.archive_progress.setValue(min(100, value))
        self.progress_detail.setText(
            f"{human_bytes(processed)} / {human_bytes(total)} · creating ZIP"
        )

    @Slot(object)
    def _archive_finished(self, result: ArchiveResult) -> None:
        self.archive_progress.setRange(0, 100)
        self.archive_progress.setValue(100)
        self.cancel_button.setVisible(False)

        self.current_archive = result.folder
        self.current_zip = result.zip_path

        self._set_step("media", "done")
        self._set_step("subtitles", "done")
        self._set_step("integrity", "done")
        if result.zip_path:
            self._set_step("zip", "done")
        else:
            self._set_step("zip", "skipped")

        try:
            checksum_count = len(
                [
                    line
                    for line in result.checksum_file.read_text(
                        encoding="utf-8"
                    ).splitlines()
                    if line.strip()
                ]
            )
        except Exception:
            checksum_count = max(0, result.files_count - 1)

        zip_text = " · verified ZIP created" if result.zip_path else ""
        self.complete_summary.setText(
            f"{result.files_count} files preserved · "
            f"{checksum_count}/{checksum_count} files verified · "
            f"archive integrity PASSED{zip_text}"
        )
        self.complete_capture.setText(
            f"Capture ID: {result.capture_id}"
        )
        self.log_toggle.blockSignals(True)
        self.log_toggle.setChecked(False)
        self.log_toggle.setText("Details ▾")
        self.log_toggle.blockSignals(False)
        self.status_stack.setCurrentWidget(self.complete_box)
        self.progress_detail.setText("Archive complete · integrity PASSED")

        self._append_log(
            f"COMPLETED · Capture {result.capture_id} · "
            f"{result.files_count} files · "
            f"{human_bytes(result.total_size_bytes)}"
        )
        if result.zip_path:
            self._append_log(f"Verified ZIP: {result.zip_path}")

        self._update_create_enabled()

    @Slot(str)
    def _archive_failed(self, message: str) -> None:
        self.archive_progress.setRange(0, 100)
        self.cancel_button.setVisible(False)
        self.progress_detail.setText("Archive stopped")
        self._append_log("ERROR: " + message)
        self._update_create_enabled()
        QMessageBox.critical(
            self,
            "Archive stopped",
            message,
        )

    def _append_log(self, text: str) -> None:
        self.log_view.appendPlainText(text)

    def _open_archive_folder(self) -> None:
        if self.current_archive:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self.current_archive))
            )

    def _verify_current_archive(self) -> None:
        if not self.current_archive:
            return
        self.verify_target_edit.setText(str(self.current_archive))
        self._set_page(1)
        self._start_verify()

    def _choose_verify_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "Choose Open Video Archiver archive directory",
        )
        if path:
            self.verify_target_edit.setText(path)

    def _choose_verify_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Open Video Archiver ZIP",
            "",
            "ZIP archives (*.zip);;All files (*)",
        )
        if path:
            self.verify_target_edit.setText(path)

    def _start_verify(self) -> None:
        if self._thread is not None:
            return

        raw = self.verify_target_edit.text().strip()
        if not raw:
            QMessageBox.warning(
                self,
                "Archive required",
                "Choose an archive directory or ZIP.",
            )
            return

        target = Path(raw).expanduser()
        self.verify_button.setEnabled(False)
        self.verify_headline.setText("Verifying…")
        self.verify_details.clear()

        worker = VerifyWorker(target)
        self._start_worker(
            worker,
            worker.finished,
            self._verify_finished,
            worker.failed,
            self._verify_failed,
        )

    @Slot(object)
    def _verify_finished(self, result: VerificationResult) -> None:
        self.verify_button.setEnabled(True)

        if result.ok:
            self.verify_headline.setText("✓ ARCHIVE VERIFIED")
            self.verify_headline.setStyleSheet(
                "color:#176b3b; font-weight:700; font-size:16px;"
            )
        else:
            self.verify_headline.setText("✗ VERIFICATION FAILED")
            self.verify_headline.setStyleSheet(
                "color:#a12a2a; font-weight:700; font-size:16px;"
            )

        lines = [
            f"Target: {result.target}",
            f"Type: {result.kind}",
            f"Capture ID: {result.capture_id or 'not available'}",
            f"Manifest schema: {result.schema or 'not available'}",
            f"Files verified: {result.verified_files}/{result.checked_files}",
            f"Archive structure: "
            f"{'OK' if result.archive_structure_valid else 'FAILED'}",
            f"Integrity: {'PASSED' if result.ok else 'FAILED'}",
        ]

        if result.issues:
            lines.append("")
            lines.append("Issues:")
            for issue in result.issues:
                lines.append(f"- {issue.path}: {issue.message}")
                if issue.expected:
                    lines.append(f"  expected: {issue.expected}")
                if issue.actual:
                    lines.append(f"  actual:   {issue.actual}")

        self.verify_details.setPlainText("\n".join(lines))

    @Slot(str)
    def _verify_failed(self, message: str) -> None:
        self.verify_button.setEnabled(True)
        self.verify_headline.setText("Verification error")
        self.verify_headline.setStyleSheet(
            "color:#a12a2a; font-weight:700; font-size:16px;"
        )
        self.verify_details.setPlainText(message)
        QMessageBox.critical(
            self,
            "Verification error",
            message,
        )


def main() -> None:
    configure_runtime_path()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("John G. Haas")
    app.setWindowIcon(make_app_icon())

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "JohnGHaas.OpenVideoArchiver.Desktop"
            )
        except Exception:
            pass

    window = MainWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
