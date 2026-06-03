import gc
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
from PyQt5.QtCore import QSize, Qt, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QKeySequence, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.frame_selector import FrameSelector
from core.feature_cache import feature_cache_path
from core.image_saver import ImageSaver
from core.shot_detector import ShotDetector
from core.video_processor import VideoProcessor

APP_VERSION = "0.3.15"


def resource_path(relative_path: str) -> Path:
    """Resolve bundled resources for source and PyInstaller one-folder builds."""
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidate = Path(bundle_root) / relative_path
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent.parent / relative_path


def load_app_icon() -> QIcon:
    for relative_path in ("assets/app_icon.ico", "assets/app_icon.png"):
        path = resource_path(relative_path)
        if path.exists():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def format_timecode(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole_secs = int(secs)
    millis = int(round((secs - whole_secs) * 1000))
    if millis >= 1000:
        whole_secs += 1
        millis -= 1000
    if whole_secs >= 60:
        minutes += 1
        whole_secs -= 60
    if minutes >= 60:
        hours += 1
        minutes -= 60
    return f"{hours:02d}-{minutes:02d}-{whole_secs:02d}.{millis:03d}"


class ProcessingThread(QThread):
    """Run detection and frame selection away from the UI thread."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(list, list, dict)
    error = pyqtSignal(str)

    def __init__(
        self,
        video_path: str,
        detection_settings: dict,
        selection_settings: dict,
    ):
        super().__init__()
        self.video_path = video_path
        self.detection_settings = detection_settings
        self.selection_settings = selection_settings

    def run(self):
        try:
            detector_settings = dict(self.detection_settings)
            detector_settings.pop("preset", None)
            detector = ShotDetector(**detector_settings)
            shots = detector.detect_shots(
                self.video_path,
                progress_callback=lambda p: self.progress.emit(p),
            )

            selector = FrameSelector(**self.selection_settings)
            selected_frames = selector.select_best_frames(
                self.video_path,
                shots,
                progress_callback=lambda p: self.progress.emit(p),
            )

            probe = VideoProcessor(self.video_path)
            probe.open()
            metrics = {
                "fps": probe.fps,
                "total_frames": probe.total_frames,
                "duration": probe.get_duration(),
                "keyframe_count": sum(len(frames) for frames in selected_frames),
                "similar_merge_count": detector.similar_merge_count,
                "feature_cache_used": detector.used_feature_cache,
                "feature_cache_path": detector.feature_cache_path,
                "cut_sources": {
                    name: len(cuts)
                    for name, cuts in detector.last_cut_candidates.items()
                },
            }
            probe.close()

            self.progress.emit(100)
            self.finished.emit(shots, selected_frames, metrics)
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self, app_icon: Optional[QIcon] = None):
        super().__init__()
        self.setWindowTitle("视频镜头与关键帧提取工具")
        if app_icon is not None and not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.app_icon = app_icon
        self.setGeometry(100, 100, 1420, 860)
        self.setAcceptDrops(True)

        self.video_path = None
        self.video_info = {}
        self.shots = []
        self.selected_frames = []
        self.active_shot_idx = None
        self.active_keyframe_idx = None
        self.current_frame_idx = None
        self.thread = None
        self.last_metrics = {}
        self.shortcuts = []
        self.anchor_jump_buttons = []
        self.preset_buttons = {}
        self._syncing_table_selection = False

        self.init_ui()
        self._load_default_config()
        self.update_cache_label()
        self._install_shortcuts()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self._build_top_bar())

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setObjectName("mainWorkspaceSplitter")
        self.main_splitter.setHandleWidth(8)
        self.main_splitter.addWidget(self._build_workspace())
        self.main_splitter.addWidget(self._build_controls())
        self.main_splitter.setSizes([1080, 360])
        self.main_splitter.setStretchFactor(0, 1)
        self.main_splitter.setStretchFactor(1, 0)
        for index in range(2):
            self.main_splitter.setCollapsible(index, False)
        layout.addWidget(self.main_splitter, 1)

        self._apply_theme()

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #f3f6fa;
                color: #172033;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QWidget#topBar {
                background: #ffffff;
                border: 1px solid #d9dee8;
                border-radius: 8px;
            }
            QLabel#appIconLabel {
                background: transparent;
            }
            QLabel#appTitleLabel {
                font-size: 17px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#contextLabel {
                color: #111827;
                font-weight: 600;
            }
            QLabel#metaLabel {
                color: #475569;
                font-size: 12px;
            }
            QLabel#sectionTitleLabel {
                color: #172033;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#sectionHeaderLabel {
                color: #172033;
                font-size: 13px;
                font-weight: 700;
            }
            QLabel#panelSubtitleLabel {
                color: #475569;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#summaryLabel {
                color: #1f3658;
                background: #f8fafc;
                border: 1px solid #dce5f0;
                border-radius: 6px;
                padding: 4px 8px;
            }
            QLabel#badgeLabel {
                background: #f8fafc;
                color: #1e3a8a;
                border: 1px solid #d8e2f1;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#statusDotLabel {
                color: #16a34a;
                background: #f0fdf4;
                border: 1px solid #bbf7d0;
                border-radius: 6px;
                padding: 3px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QScrollArea {
                border: 0;
                background: transparent;
            }
            QSplitter::handle {
                background: #d7e0ec;
                border-radius: 3px;
            }
            QSplitter::handle:horizontal {
                width: 8px;
                margin: 4px 2px;
            }
            QSplitter::handle:vertical {
                height: 4px;
                margin: 2px 4px;
            }
            QSplitter::handle:hover {
                background: #7aa2f8;
            }
            QLabel#mutedLabel {
                color: #5e6b80;
            }
            QLabel#statusLabel {
                color: #334155;
                background: #f7fafc;
                border: 1px solid #dfe7f1;
                border-radius: 6px;
                padding: 7px 8px;
            }
            QLabel#fileDropLabel {
                color: #334155;
                background: #f8fafc;
                border: 1px dashed #b9c4d4;
                border-radius: 8px;
                padding: 12px;
            }
            QFrame#surfacePanel, QFrame#inspectorRoot, QFrame#inspectorSection {
                background: #ffffff;
                border: 1px solid #d9e1ec;
                border-radius: 8px;
            }
            QFrame#previewPanel {
                background: #ffffff;
                border: 1px solid #d9e1ec;
                border-radius: 8px;
            }
            QFrame#previewStage {
                background: #111827;
                border: 1px solid #263244;
                border-radius: 8px;
            }
            QFrame#transportBar {
                background: #111827;
                border: 0;
                border-radius: 0;
            }
            QFrame#divider {
                background: #e3e9f1;
                border: 0;
                min-height: 1px;
                max-height: 1px;
            }
            QPushButton {
                background: #235ee8;
                color: white;
                border: 0;
                border-radius: 6px;
                padding: 7px 10px;
                min-height: 22px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
            QPushButton:focus {
                border: 1px solid #93b4ff;
                padding: 6px 9px;
            }
            QPushButton:disabled {
                background: #c8d1df;
                color: #f7f9fc;
            }
            QPushButton[secondary="true"] {
                background: #f6f8fb;
                color: #1f2937;
                border: 1px solid #d5dde8;
            }
            QPushButton[secondary="true"]:hover {
                background: #e9eef6;
            }
            QPushButton[segment="true"] {
                background: #f8fafc;
                color: #334155;
                border: 1px solid #d5dde8;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton[segment="true"]:checked {
                background: #2563eb;
                color: #ffffff;
                border-color: #2563eb;
            }
            QToolButton {
                background: #f6f8fb;
                border: 1px solid #d5dde8;
                border-radius: 6px;
                padding: 6px;
                min-width: 32px;
                min-height: 32px;
            }
            QToolButton:hover {
                background: #e6ecf4;
            }
            QToolButton:disabled {
                background: #eef2f7;
                color: #9aa7bb;
            }
            QToolButton#advancedToggle {
                background: #f8fafc;
                color: #334155;
                border: 1px solid #dce3ec;
                border-radius: 6px;
                padding: 5px 8px;
                min-height: 24px;
                font-weight: 600;
                text-align: left;
            }
            QToolButton#advancedToggle:hover {
                background: #eef4ff;
                border-color: #c7d7ff;
            }
            QToolButton[quickJump="true"] {
                background: #ffffff;
                color: #1f3658;
                border: 1px solid #cfd8e6;
                border-radius: 6px;
                padding: 5px 10px;
                min-height: 26px;
                font-weight: 600;
            }
            QToolButton[quickJump="true"]:hover {
                background: #eaf2ff;
                border-color: #9dbbff;
            }
            QToolButton[quickJump="true"]:disabled {
                color: #9aa7bb;
                background: #f4f7fb;
                border-color: #dce3ec;
            }
            QWidget#advancedPanel {
                background: #f8fafc;
                border: 1px solid #e1e7f0;
                border-radius: 8px;
                padding: 6px;
            }
            QComboBox, QSpinBox, QDoubleSpinBox {
                background: white;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 8px;
                min-height: 24px;
            }
            QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #7aa2f8;
            }
            QListWidget {
                background: #ffffff;
                border: 1px solid #d5dde8;
                border-radius: 8px;
                padding: 4px;
            }
            QListWidget:focus {
                border: 1px solid #93b4ff;
            }
            QListWidget::item {
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item:selected {
                background: #dbeafe;
                color: #0f172a;
            }
            QListWidget::item:hover {
                background: #eef4ff;
            }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #d9e1ec;
                border-radius: 8px;
                gridline-color: #edf1f6;
                alternate-background-color: #fbfdff;
                selection-background-color: #dbeafe;
                selection-color: #0f172a;
            }
            QHeaderView::section {
                background: #f8fafc;
                color: #334155;
                border: 0;
                border-bottom: 1px solid #d9e1ec;
                padding: 6px 8px;
                font-weight: 700;
            }
            QProgressBar {
                border: 1px solid #d5dde8;
                border-radius: 6px;
                background: white;
                text-align: center;
                min-height: 22px;
            }
            QProgressBar::chunk {
                border-radius: 5px;
                background: #16a34a;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #d9dee8;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #7aa2f8;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #2563eb;
                border: 0;
                width: 16px;
                margin: -5px 0;
                border-radius: 8px;
            }
            QLabel#previewLabel {
                background: #111827;
                border: 0;
                border-radius: 0;
                color: #cbd5e1;
                font-size: 14px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #c6d0df;
                border-radius: 4px;
                min-height: 28px;
            }
            QScrollBar::handle:vertical:hover {
                background: #9fb0c6;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )

    def _surface_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("surfacePanel")
        return panel

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        return line

    def _section_header(self, title: str, subtitle: str = "") -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitleLabel")
        layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("panelSubtitleLabel")
            layout.addWidget(subtitle_label)
        layout.addStretch(1)
        return header

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_preview_panel(), 5)
        layout.addWidget(self._build_thumbnail_panel(), 2)
        layout.addWidget(self._build_shot_panel(), 3)
        return workspace

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(50)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setObjectName("appIconLabel")
        icon_label.setFixedSize(24, 24)
        if self.app_icon is not None and not self.app_icon.isNull():
            icon_label.setPixmap(self.app_icon.pixmap(22, 22))

        title = QLabel("VideoFrameExtractor")
        title.setObjectName("appTitleLabel")
        self.top_context_label = QLabel("未选择视频")
        self.top_context_label.setObjectName("contextLabel")
        self.top_context_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        self.top_resolution_label = QLabel("分辨率")
        self.top_resolution_label.setObjectName("badgeLabel")
        self.top_fps_label = QLabel("fps")
        self.top_fps_label.setObjectName("badgeLabel")
        self.top_frames_label = QLabel("帧数")
        self.top_frames_label.setObjectName("badgeLabel")
        self.top_duration_label = QLabel("时长")
        self.top_duration_label.setObjectName("badgeLabel")
        version_label = QLabel(f"v{APP_VERSION}")
        version_label.setObjectName("badgeLabel")
        self.top_cache_label = QLabel("缓存 0 B")
        self.top_cache_label.setObjectName("statusDotLabel")
        mode_label = QLabel("本地模式")
        mode_label.setObjectName("badgeLabel")

        layout.addWidget(icon_label)
        layout.addWidget(title)
        layout.addWidget(self.top_context_label, 1)
        layout.addWidget(self.top_resolution_label)
        layout.addWidget(self.top_fps_label)
        layout.addWidget(self.top_frames_label)
        layout.addWidget(self.top_duration_label)
        layout.addWidget(version_label)
        layout.addWidget(self.top_cache_label)
        layout.addWidget(mode_label)
        return bar

    def _install_shortcuts(self):
        shortcuts = [
            (Qt.Key_Left, lambda: self.nudge_frame(-1)),
            (Qt.Key_Right, lambda: self.nudge_frame(1)),
            (Qt.Key_4, lambda: self.nudge_frame(-1)),
            (Qt.Key_6, lambda: self.nudge_frame(1)),
            (Qt.Key_5, self.replace_active_keyframe),
            (Qt.Key_Return, self.replace_active_keyframe),
            (Qt.Key_Enter, self.replace_active_keyframe),
        ]

        for key, handler in shortcuts:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(handler)
            self.shortcuts.append(shortcut)

    def _toggle_advanced_panel(
        self,
        button: QToolButton,
        panel: QWidget,
        checked: bool,
        base_text: str,
    ):
        panel.setVisible(checked)
        button.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        button.setText(f"{base_text}（已展开）" if checked else base_text)

    def _build_controls(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("inspectorScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(340)
        scroll.setMaximumWidth(430)

        panel = QFrame()
        panel.setObjectName("inspectorRoot")
        panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title = QLabel("参数与导出")
        title.setObjectName("sectionTitleLabel")
        title_row.addWidget(title)
        title_row.addStretch(1)
        panel_layout.addLayout(title_row)
        panel_layout.addWidget(self._build_file_group())
        panel_layout.addWidget(self._build_detection_group())
        panel_layout.addWidget(self._build_selection_group())
        panel_layout.addWidget(self._build_export_group())
        panel_layout.addWidget(self._build_action_group())
        panel_layout.addWidget(self._build_config_group())
        panel_layout.addWidget(self._build_cache_group())
        panel_layout.addStretch(1)

        scroll.setWidget(panel)
        return scroll

    def _build_inspector_section(self, title: str):
        section = QFrame()
        section.setObjectName("inspectorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        header = QLabel(title)
        header.setObjectName("sectionHeaderLabel")
        layout.addWidget(header)
        return section, layout

    def _build_file_group(self) -> QWidget:
        group, layout = self._build_inspector_section("视频文件")
        layout.setSpacing(8)

        self.file_label = QLabel("未选择视频")
        self.file_label.setWordWrap(True)
        self.file_label.setObjectName("fileDropLabel")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_label.setMinimumHeight(52)
        self.file_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        select_btn = QPushButton("选择视频文件")
        select_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        select_btn.clicked.connect(self.select_video)

        layout.addWidget(self.file_label)
        layout.addWidget(select_btn)

        self.video_meta_label = QLabel("时长、分辨率和帧率会在选择后显示")
        self.video_meta_label.setObjectName("mutedLabel")
        self.video_meta_label.setWordWrap(True)
        self.video_meta_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.video_meta_label)
        return group

    def _build_detection_group(self) -> QWidget:
        group, layout = self._build_inspector_section("镜头检测")
        layout.setSpacing(8)

        form_panel = QWidget()
        form = QFormLayout(form_panel)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("平衡", "balanced")
        self.preset_combo.addItem("更灵敏", "sensitive")
        self.preset_combo.addItem("更保守", "conservative")
        self.preset_combo.currentIndexChanged.connect(self.apply_detection_preset)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("混合增强（推荐）", "hybrid")
        self.mode_combo.addItem("标准内容检测", "content")
        self.mode_combo.addItem("自适应检测", "adaptive")
        self.mode_combo.addItem("直方图补充检测", "histogram")

        self.content_threshold_spin = QDoubleSpinBox()
        self.content_threshold_spin.setRange(5.0, 50.0)
        self.content_threshold_spin.setSingleStep(1.0)
        self.content_threshold_spin.setValue(12.0)

        self.adaptive_threshold_spin = QDoubleSpinBox()
        self.adaptive_threshold_spin.setRange(1.0, 8.0)
        self.adaptive_threshold_spin.setSingleStep(0.2)
        self.adaptive_threshold_spin.setValue(2.0)

        self.histogram_threshold_spin = QDoubleSpinBox()
        self.histogram_threshold_spin.setRange(0.05, 0.60)
        self.histogram_threshold_spin.setSingleStep(0.01)
        self.histogram_threshold_spin.setDecimals(2)
        self.histogram_threshold_spin.setValue(0.16)

        self.min_scene_len_spin = QDoubleSpinBox()
        self.min_scene_len_spin.setRange(0.10, 3.00)
        self.min_scene_len_spin.setSingleStep(0.05)
        self.min_scene_len_spin.setSuffix(" 秒")
        self.min_scene_len_spin.setValue(0.35)

        self.sensitivity_slider = QSlider(Qt.Horizontal)
        self.sensitivity_slider.setRange(0, 100)
        self.sensitivity_slider.setValue(65)
        self.sensitivity_slider.setTickPosition(QSlider.TicksBelow)
        self.sensitivity_slider.setTickInterval(10)
        self.sensitivity_slider.valueChanged.connect(self.apply_sensitivity_from_slider)

        slider_panel = QWidget()
        slider_layout = QVBoxLayout(slider_panel)
        slider_layout.setContentsMargins(0, 0, 0, 0)
        slider_layout.setSpacing(4)
        slider_layout.addWidget(self.sensitivity_slider)

        scale_row = QHBoxLayout()
        less_label = QLabel("切少")
        less_label.setObjectName("mutedLabel")
        more_label = QLabel("切多")
        more_label.setObjectName("mutedLabel")
        self.sensitivity_value_label = QLabel()
        self.sensitivity_value_label.setAlignment(Qt.AlignCenter)
        scale_row.addWidget(less_label)
        scale_row.addStretch(1)
        scale_row.addWidget(self.sensitivity_value_label)
        scale_row.addStretch(1)
        scale_row.addWidget(more_label)
        slider_layout.addLayout(scale_row)

        layout.addWidget(self._build_preset_segment())
        form.addRow("检测灵敏度", slider_panel)

        self.merge_slider = QSlider(Qt.Horizontal)
        self.merge_slider.setRange(0, 100)
        self.merge_slider.setValue(30)
        self.merge_slider.setTickPosition(QSlider.TicksBelow)
        self.merge_slider.setTickInterval(10)
        self.merge_slider.valueChanged.connect(self.update_merge_label)

        merge_panel = QWidget()
        merge_layout = QVBoxLayout(merge_panel)
        merge_layout.setContentsMargins(0, 0, 0, 0)
        merge_layout.setSpacing(4)
        merge_layout.addWidget(self.merge_slider)

        merge_scale_row = QHBoxLayout()
        off_label = QLabel("关闭")
        off_label.setObjectName("mutedLabel")
        strong_label = QLabel("更强")
        strong_label.setObjectName("mutedLabel")
        self.merge_value_label = QLabel()
        self.merge_value_label.setAlignment(Qt.AlignCenter)
        merge_scale_row.addWidget(off_label)
        merge_scale_row.addStretch(1)
        merge_scale_row.addWidget(self.merge_value_label)
        merge_scale_row.addStretch(1)
        merge_scale_row.addWidget(strong_label)
        merge_layout.addLayout(merge_scale_row)

        form.addRow("运镜误切修正", merge_panel)
        layout.addWidget(form_panel)

        self.detection_advanced_toggle = QToolButton()
        self.detection_advanced_toggle.setObjectName("advancedToggle")
        self.detection_advanced_toggle.setText("高级参数")
        self.detection_advanced_toggle.setCheckable(True)
        self.detection_advanced_toggle.setArrowType(Qt.RightArrow)
        self.detection_advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layout.addWidget(self.detection_advanced_toggle)

        self.detection_advanced_panel = QWidget()
        self.detection_advanced_panel.setObjectName("advancedPanel")
        advanced_form = QFormLayout(self.detection_advanced_panel)
        advanced_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        advanced_form.setHorizontalSpacing(12)
        advanced_form.setVerticalSpacing(8)
        advanced_form.addRow("检测模式", self.mode_combo)
        advanced_form.addRow("内容阈值", self.content_threshold_spin)
        advanced_form.addRow("自适应阈值", self.adaptive_threshold_spin)
        advanced_form.addRow("差异阈值", self.histogram_threshold_spin)
        advanced_form.addRow("最短镜头", self.min_scene_len_spin)
        self.detection_advanced_panel.setVisible(False)
        self.detection_advanced_toggle.toggled.connect(
            lambda checked: self._toggle_advanced_panel(
                self.detection_advanced_toggle,
                self.detection_advanced_panel,
                checked,
                "高级参数",
            )
        )
        layout.addWidget(self.detection_advanced_panel)

        self.apply_sensitivity_from_slider(self.sensitivity_slider.value())
        self.update_merge_label(self.merge_slider.value())
        return group

    def _build_preset_segment(self) -> QWidget:
        segment = QWidget()
        segment.setObjectName("segmentedControl")
        layout = QHBoxLayout(segment)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for text, data in (("切少", "conservative"), ("平衡", "balanced"), ("切多", "sensitive")):
            button = QPushButton(text)
            button.setProperty("segment", "true")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, preset=data: self._set_detection_preset(preset))
            self.preset_buttons[data] = button
            layout.addWidget(button, 1)
        self._sync_preset_buttons()
        return segment

    def _set_detection_preset(self, preset: str):
        index = self.preset_combo.findData(preset)
        if index >= 0:
            self.preset_combo.setCurrentIndex(index)
        self._sync_preset_buttons()

    def _sync_preset_buttons(self):
        current = self.preset_combo.currentData() if hasattr(self, "preset_combo") else None
        for preset, button in getattr(self, "preset_buttons", {}).items():
            button.setChecked(preset == current)

    def _build_selection_group(self) -> QWidget:
        group, layout = self._build_inspector_section("关键帧输出")
        layout.setSpacing(8)

        form_panel = QWidget()
        form = QFormLayout(form_panel)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.frames_per_shot_spin = QSpinBox()
        self.frames_per_shot_spin.setRange(1, 6)
        self.frames_per_shot_spin.setValue(2)

        self.max_samples_spin = QSpinBox()
        self.max_samples_spin.setRange(12, 240)
        self.max_samples_spin.setSingleStep(4)
        self.max_samples_spin.setValue(24)

        self.edge_margin_spin = QDoubleSpinBox()
        self.edge_margin_spin.setRange(0.0, 25.0)
        self.edge_margin_spin.setSingleStep(1.0)
        self.edge_margin_spin.setSuffix(" %")
        self.edge_margin_spin.setValue(8.0)

        self.edge_frame_offset_spin = QSpinBox()
        self.edge_frame_offset_spin.setRange(0, 12)
        self.edge_frame_offset_spin.setValue(1)

        form.addRow("每镜头帧数", self.frames_per_shot_spin)
        form.addRow("首尾避让帧数", self.edge_frame_offset_spin)
        layout.addWidget(form_panel)

        self.selection_advanced_toggle = QToolButton()
        self.selection_advanced_toggle.setObjectName("advancedToggle")
        self.selection_advanced_toggle.setText("高级选帧参数")
        self.selection_advanced_toggle.setCheckable(True)
        self.selection_advanced_toggle.setArrowType(Qt.RightArrow)
        self.selection_advanced_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layout.addWidget(self.selection_advanced_toggle)

        self.selection_advanced_panel = QWidget()
        self.selection_advanced_panel.setObjectName("advancedPanel")
        advanced_form = QFormLayout(self.selection_advanced_panel)
        advanced_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        advanced_form.setHorizontalSpacing(12)
        advanced_form.setVerticalSpacing(8)
        advanced_form.addRow("候选采样数", self.max_samples_spin)
        advanced_form.addRow("关键帧边缘避让", self.edge_margin_spin)
        self.selection_advanced_panel.setVisible(False)
        self.selection_advanced_toggle.toggled.connect(
            lambda checked: self._toggle_advanced_panel(
                self.selection_advanced_toggle,
                self.selection_advanced_panel,
                checked,
                "高级选帧参数",
            )
        )
        layout.addWidget(self.selection_advanced_panel)
        return group

    def _build_config_group(self) -> QWidget:
        group, layout = self._build_inspector_section("处理配置")
        layout.setSpacing(8)

        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        save_btn = QPushButton("保存参数")
        save_btn.setProperty("secondary", "true")
        save_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        save_btn.clicked.connect(self.save_current_config)
        default_btn = QPushButton("设为默认")
        default_btn.setProperty("secondary", "true")
        default_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        default_btn.clicked.connect(self.set_default_config)
        first_row.addWidget(save_btn, 1)
        first_row.addWidget(default_btn, 1)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.setSpacing(8)
        import_btn = QPushButton("导入参数")
        import_btn.setProperty("secondary", "true")
        import_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        import_btn.clicked.connect(self.import_config)
        export_btn = QPushButton("导出参数")
        export_btn.setProperty("secondary", "true")
        export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        export_btn.clicked.connect(self.export_config)
        second_row.addWidget(import_btn, 1)
        second_row.addWidget(export_btn, 1)
        layout.addLayout(second_row)
        return group

    def _build_cache_group(self) -> QWidget:
        group, layout = self._build_inspector_section("缓存管理")
        layout.setSpacing(8)

        self.cache_label = QLabel("缓存大小计算中")
        self.cache_label.setObjectName("mutedLabel")
        self.cache_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.cache_label)

        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        clear_current_btn = QPushButton("清当前视频缓存")
        clear_current_btn.setProperty("secondary", "true")
        clear_current_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clear_current_btn.clicked.connect(self.clear_current_video_cache)
        clear_all_btn = QPushButton("清全部缓存")
        clear_all_btn.setProperty("secondary", "true")
        clear_all_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clear_all_btn.clicked.connect(self.clear_all_cache)
        first_row.addWidget(clear_current_btn, 1)
        first_row.addWidget(clear_all_btn, 1)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.setSpacing(8)
        open_cache_btn = QPushButton("打开缓存文件夹")
        open_cache_btn.setProperty("secondary", "true")
        open_cache_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        open_cache_btn.clicked.connect(self.open_cache_folder)
        clear_results_btn = QPushButton("清空当前结果")
        clear_results_btn.setProperty("secondary", "true")
        clear_results_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clear_results_btn.clicked.connect(self.clear_current_results)
        second_row.addWidget(open_cache_btn, 1)
        second_row.addWidget(clear_results_btn, 1)
        layout.addLayout(second_row)

        return group

    def _build_action_group(self) -> QWidget:
        group, layout = self._build_inspector_section("处理")
        layout.setSpacing(8)

        self.process_btn = QPushButton("开始检测并选帧")
        self.process_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.process_btn.clicked.connect(self.process_video)
        layout.addWidget(self.process_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("准备就绪")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.status_label)
        return group

    def _build_export_group(self) -> QWidget:
        group, layout = self._build_inspector_section("导出")
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel("格式"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("PNG（无损）", "png")
        self.format_combo.addItem("JPG（有损/小文件）", "jpg")
        row.addWidget(self.format_combo)
        layout.addLayout(row)

        export_btn = QPushButton("导出数据集")
        export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        export_btn.clicked.connect(self.export_dataset)
        layout.addWidget(export_btn)

        edge_row = QHBoxLayout()
        edge_row.setSpacing(8)
        self.export_current_edges_btn = QPushButton("导出当前首中尾帧")
        self.export_current_edges_btn.setProperty("secondary", "true")
        self.export_current_edges_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.export_current_edges_btn.clicked.connect(self.export_current_edge_frames)
        self.export_all_edges_btn = QPushButton("批量导出首中尾帧")
        self.export_all_edges_btn.setProperty("secondary", "true")
        self.export_all_edges_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.export_all_edges_btn.clicked.connect(self.export_all_edge_frames)
        edge_row.addWidget(self.export_current_edges_btn, 1)
        edge_row.addWidget(self.export_all_edges_btn, 1)
        layout.addLayout(edge_row)

        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        self.save_project_btn = QPushButton("保存检测结果")
        self.save_project_btn.setProperty("secondary", "true")
        self.save_project_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_project_btn.clicked.connect(self.export_project_file)
        self.import_project_btn = QPushButton("导入检测结果")
        self.import_project_btn.setProperty("secondary", "true")
        self.import_project_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.import_project_btn.clicked.connect(self.import_project_file)
        project_row.addWidget(self.save_project_btn, 1)
        project_row.addWidget(self.import_project_btn, 1)
        layout.addLayout(project_row)
        return group

    def _build_shot_panel(self) -> QWidget:
        panel = self._surface_panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("镜头表格")
        title.setObjectName("sectionTitleLabel")
        self.summary_label = QLabel("镜头总数 0 | 关键帧 0 | 平均时长 0.00s | 缓存 -")
        self.summary_label.setObjectName("summaryLabel")
        self.summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.summary_label, 1)
        layout.addLayout(header)

        self.shot_table = QTableWidget(0, 5)
        self.shot_table.setObjectName("shotTable")
        self.shot_table.setHorizontalHeaderLabels(["#", "帧范围", "时长", "关键帧", "缓存状态"])
        self.shot_table.setAlternatingRowColors(True)
        self.shot_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.shot_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.shot_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.shot_table.verticalHeader().setVisible(False)
        self.shot_table.horizontalHeader().setStretchLastSection(True)
        self.shot_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.shot_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.shot_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.shot_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.shot_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.shot_table.cellClicked.connect(self.on_shot_table_cell_clicked)
        layout.addWidget(self.shot_table, 1)
        return panel

    def _build_thumbnail_panel(self) -> QWidget:
        panel = self._surface_panel()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("关键帧预览")
        title.setObjectName("sectionTitleLabel")
        self.thumbnail_context_label = QLabel("选择镜头后显示首中尾和其它关键帧")
        self.thumbnail_context_label.setObjectName("panelSubtitleLabel")
        filter_label = QLabel("显示")
        filter_label.setObjectName("mutedLabel")
        self.thumbnail_filter_combo = QComboBox()
        self.thumbnail_filter_combo.addItem("首中尾")
        self.thumbnail_filter_combo.addItem("全部关键帧")
        header.addWidget(title)
        header.addWidget(self.thumbnail_context_label)
        header.addStretch(1)
        header.addWidget(filter_label)
        header.addWidget(self.thumbnail_filter_combo)
        layout.addLayout(header)

        grids = QHBoxLayout()
        grids.setSpacing(10)

        anchor_block = QVBoxLayout()
        anchor_block.setSpacing(5)
        anchor_title = QLabel("当前镜头首中尾")
        anchor_title.setObjectName("sectionHeaderLabel")
        self.anchor_frame_grid = QListWidget()
        self.anchor_frame_grid.setObjectName("anchorFrameGrid")
        self.anchor_frame_grid.setViewMode(QListView.IconMode)
        self.anchor_frame_grid.setFlow(QListView.LeftToRight)
        self.anchor_frame_grid.setWrapping(False)
        self.anchor_frame_grid.setResizeMode(QListView.Adjust)
        self.anchor_frame_grid.setMovement(QListView.Static)
        self.anchor_frame_grid.setIconSize(QSize(170, 96))
        self.anchor_frame_grid.setGridSize(QSize(190, 132))
        self.anchor_frame_grid.setMinimumHeight(138)
        self.anchor_frame_grid.setMaximumHeight(150)
        self.anchor_frame_grid.itemClicked.connect(self.on_grid_frame_selected)
        anchor_block.addWidget(anchor_title)
        anchor_block.addWidget(self.anchor_frame_grid)

        other_block = QVBoxLayout()
        other_block.setSpacing(5)
        other_title = QLabel("其它镜头关键帧")
        other_title.setObjectName("sectionHeaderLabel")
        self.frame_grid = QListWidget()
        self.frame_grid.setObjectName("otherFrameGrid")
        self.frame_grid.setViewMode(QListView.IconMode)
        self.frame_grid.setFlow(QListView.LeftToRight)
        self.frame_grid.setWrapping(False)
        self.frame_grid.setResizeMode(QListView.Adjust)
        self.frame_grid.setMovement(QListView.Static)
        self.frame_grid.setIconSize(QSize(92, 56))
        self.frame_grid.setGridSize(QSize(104, 94))
        self.frame_grid.setMinimumHeight(138)
        self.frame_grid.setMaximumHeight(150)
        self.frame_grid.itemClicked.connect(self.on_grid_frame_selected)
        other_block.addWidget(other_title)
        other_block.addWidget(self.frame_grid)

        grids.addLayout(anchor_block, 3)
        grids.addLayout(other_block, 2)
        layout.addLayout(grids)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("previewPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self.preview_title_label = QLabel("当前镜头预览")
        self.preview_title_label.setObjectName("sectionTitleLabel")
        self.preview_subtitle_label = QLabel("未选择镜头")
        self.preview_subtitle_label.setObjectName("panelSubtitleLabel")
        header.addWidget(self.preview_title_label)
        header.addWidget(self.preview_subtitle_label)
        header.addStretch(1)
        layout.addLayout(header)

        preview_stage = QFrame()
        preview_stage.setObjectName("previewStage")
        preview_layout = QVBoxLayout(preview_stage)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        self.preview_label = QLabel("选择视频并开始检测")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(620, 330)
        self.preview_label.setScaledContents(False)
        preview_layout.addWidget(self.preview_label, 1)
        layout.addWidget(preview_stage, 1)

        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self.frame_info_label = QLabel("帧信息")
        self.frame_info_label.setObjectName("mutedLabel")
        self.shot_range_label = QLabel("当前镜头范围")
        self.shot_range_label.setObjectName("mutedLabel")
        self.shot_range_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        info_row.addWidget(self.frame_info_label, 1)
        info_row.addWidget(self.shot_range_label, 1)
        layout.addLayout(info_row)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setEnabled(False)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self.on_frame_slider_changed)
        layout.addWidget(self.frame_slider)

        nav_layout = QHBoxLayout()
        nav_layout.setSpacing(6)
        jump_start_btn = QToolButton()
        jump_start_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipBackward))
        jump_start_btn.setToolTip("跳到当前镜头首帧")
        jump_start_btn.clicked.connect(lambda: self.jump_to_active_shot_anchor("start"))
        previous_btn = QToolButton()
        previous_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowLeft))
        previous_btn.setToolTip("上一帧（左方向键 / 数字键 4）")
        previous_btn.clicked.connect(lambda: self.nudge_frame(-1))
        play_btn = QToolButton()
        play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        play_btn.setEnabled(False)
        play_btn.setToolTip("预留播放控制")
        next_btn = QToolButton()
        next_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowRight))
        next_btn.setToolTip("下一帧（右方向键 / 数字键 6）")
        next_btn.clicked.connect(lambda: self.nudge_frame(1))
        jump_end_btn = QToolButton()
        jump_end_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaSkipForward))
        jump_end_btn.setToolTip("跳到当前镜头尾帧")
        jump_end_btn.clicked.connect(lambda: self.jump_to_active_shot_anchor("end"))

        self.jump_frame_spin = QSpinBox()
        self.jump_frame_spin.setRange(0, 0)
        self.jump_frame_spin.setEnabled(False)
        self.jump_frame_spin.setMaximumWidth(86)
        self.jump_total_label = QLabel("/ 0")
        self.jump_total_label.setObjectName("mutedLabel")
        jump_btn = QPushButton("跳转")
        jump_btn.setProperty("secondary", "true")
        jump_btn.clicked.connect(self.jump_to_video_frame)

        for button in (jump_start_btn, previous_btn, play_btn, next_btn, jump_end_btn):
            nav_layout.addWidget(button)
        nav_layout.addSpacing(8)
        nav_layout.addWidget(self.jump_frame_spin)
        nav_layout.addWidget(self.jump_total_label)
        nav_layout.addWidget(jump_btn)
        nav_layout.addStretch(1)

        for role, text in (
            ("start", "首帧"),
            ("middle", "中间帧"),
            ("end", "尾帧"),
        ):
            button = QToolButton()
            button.setText(text)
            button.setProperty("quickJump", "true")
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, item=role: self.jump_to_active_shot_anchor(item))
            self.anchor_jump_buttons.append(button)
            nav_layout.addWidget(button)
        replace_btn = QPushButton("设为当前关键帧")
        replace_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        replace_btn.setToolTip("设为当前关键帧（数字键 5 / 回车）")
        replace_btn.clicked.connect(self.replace_active_keyframe)
        nav_layout.addWidget(replace_btn)
        layout.addLayout(nav_layout)
        return panel

    def select_video(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "选择视频文件",
            "",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.webm);;所有文件 (*)",
        )

        if not filepath:
            return

        self.load_video_path(filepath)

    def load_video_path(self, filepath: str):
        if not filepath:
            return
        path = Path(filepath)
        if not path.exists():
            self._set_status(f"视频不存在: {filepath}")
            return
        if not self._is_supported_video(path):
            self._set_status("请拖入或选择视频文件")
            return

        self.video_path = filepath
        self._set_compact_label(self.file_label, Path(filepath).name)
        if hasattr(self, "top_context_label"):
            self._set_compact_label(self.top_context_label, Path(filepath).name, 96)
        self._clear_results()
        self._load_video_info(filepath)
        self._try_load_project_cache()

    def dragEnterEvent(self, event):
        if self._drag_has_supported_video(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if self._is_supported_video(path):
                self.load_video_path(str(path))
                event.acceptProposedAction()
                return
        event.ignore()

    def _drag_has_supported_video(self, event) -> bool:
        if not event.mimeData().hasUrls():
            return False
        return any(
            url.isLocalFile() and self._is_supported_video(Path(url.toLocalFile()))
            for url in event.mimeData().urls()
        )

    def _is_supported_video(self, path: Path) -> bool:
        return path.suffix.lower() in {
            ".mp4",
            ".avi",
            ".mov",
            ".mkv",
            ".webm",
            ".m4v",
            ".mpg",
            ".mpeg",
            ".ts",
            ".m2ts",
            ".mts",
        }

    def _compact_text(self, text: object, limit: int = 78) -> str:
        text = str(text)
        if len(text) <= limit:
            return text
        head = max(18, limit // 2 - 2)
        tail = max(18, limit - head - 3)
        return f"{text[:head]}...{text[-tail:]}"

    def _set_compact_label(self, label: QLabel, text: object, limit: int = 60):
        full_text = str(text)
        compact_text = self._compact_text(full_text, limit)
        label.setText(compact_text)
        label.setToolTip(full_text if compact_text != full_text else "")

    def _set_status(self, text: object, tooltip: Optional[object] = None, limit: int = 78):
        full_text = str(text)
        compact_text = self._compact_text(full_text, limit)
        self.status_label.setText(compact_text)
        tooltip_text = str(tooltip) if tooltip is not None else full_text
        self.status_label.setToolTip(tooltip_text if compact_text != full_text else "")

    def _set_path_status(self, prefix: str, path: object):
        path_text = str(path)
        separator = " " if prefix.endswith("到") else ": "
        message = f"{prefix}{separator}{path_text}"
        self._set_status(message, tooltip=message, limit=58)

    def _load_video_info(self, filepath: str):
        try:
            probe = VideoProcessor(filepath)
            probe.open()
            duration = probe.get_duration()
            self.video_info = {
                "fps": probe.fps,
                "total_frames": probe.total_frames,
                "duration": duration,
                "width": probe.width,
                "height": probe.height,
            }
            probe.close()
            self.video_meta_label.setText(
                f"{duration:.1f} 秒 | {probe.width}x{probe.height} | "
                f"{probe.fps:.2f} fps | {probe.total_frames} 帧"
            )
            if hasattr(self, "top_resolution_label"):
                self.top_resolution_label.setText(f"{probe.width}×{probe.height}")
                self.top_fps_label.setText(f"{probe.fps:.2f} fps")
                self.top_frames_label.setText(f"{probe.total_frames} 帧")
                self.top_duration_label.setText(format_timecode(duration))
            if hasattr(self, "jump_frame_spin"):
                self.jump_frame_spin.setEnabled(True)
                self.jump_frame_spin.setRange(0, max(0, int(probe.total_frames) - 1))
                self.jump_total_label.setText(f"/ {max(0, int(probe.total_frames) - 1)}")
            if hasattr(self, "top_context_label"):
                self._set_compact_label(
                    self.top_context_label,
                    (
                        f"{Path(filepath).name} | {duration:.1f} 秒 | "
                        f"{probe.width}x{probe.height} | {probe.fps:.2f} fps"
                    ),
                    120,
                )
            self._set_status("视频已载入，可以开始检测")
        except Exception as exc:
            self._set_compact_label(self.video_meta_label, f"读取视频失败: {exc}")
            if hasattr(self, "top_context_label"):
                self._set_compact_label(self.top_context_label, "视频读取失败")
            self._set_status("视频读取失败")

    def process_video(self):
        if not self.video_path:
            self._set_status("请先选择视频文件")
            return

        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        self._set_status("正在检测镜头并筛选关键帧...")
        self._clear_results(keep_video=True)

        self.thread = ProcessingThread(
            self.video_path,
            self._detection_settings(),
            self._selection_settings(),
        )
        self.thread.progress.connect(self.progress_bar.setValue)
        self.thread.finished.connect(self.on_processing_finished)
        self.thread.error.connect(self.on_processing_error)
        self.thread.start()

    def _detection_settings(self) -> dict:
        merge_strength = self.merge_slider.value()
        return {
            "preset": self.preset_combo.currentData(),
            "mode": self.mode_combo.currentData(),
            "content_threshold": self.content_threshold_spin.value(),
            "adaptive_threshold": self.adaptive_threshold_spin.value(),
            "histogram_threshold": self.histogram_threshold_spin.value(),
            "min_scene_len_seconds": self.min_scene_len_spin.value(),
            "histogram_enabled": True,
            "analysis_width": 240,
            "analysis_frame_step": 5,
            "merge_similar_shots": merge_strength > 0,
            "merge_similarity_threshold": self._merge_threshold_from_slider(merge_strength),
            "merge_max_shot_seconds": 1.0,
            "guard_weak_motion_cuts": True,
            "feature_cache_enabled": True,
            "feature_cache_dir": str(self._feature_cache_dir()),
        }

    def _selection_settings(self) -> dict:
        return {
            "frames_per_shot": self.frames_per_shot_spin.value(),
            "max_samples_per_shot": self.max_samples_spin.value(),
            "edge_margin_ratio": self.edge_margin_spin.value() / 100.0,
        }

    def apply_sensitivity_from_slider(self, value: int):
        value = max(0, min(100, int(value)))
        content_threshold = max(6.0, min(28.0, 24.0 - value * 0.18))
        adaptive_threshold = max(1.2, min(4.5, 4.4 - value * 0.035))
        histogram_threshold = max(0.08, min(0.34, 0.34 - value * 0.0028))
        min_scene_len = max(0.20, min(0.90, 0.80 - value * 0.007))

        self.content_threshold_spin.setValue(round(content_threshold, 1))
        self.adaptive_threshold_spin.setValue(round(adaptive_threshold, 1))
        self.histogram_threshold_spin.setValue(round(histogram_threshold, 2))
        self.min_scene_len_spin.setValue(round(min_scene_len, 2))

        if value >= 75:
            label = "偏灵敏"
        elif value <= 40:
            label = "偏保守"
        else:
            label = "平衡"
        self.sensitivity_value_label.setText(f"{label} {value}")

    def _merge_threshold_from_slider(self, value: int) -> float:
        if value <= 0:
            return 0.0
        return round(0.02 + max(0, min(100, int(value))) * 0.002, 3)

    def update_merge_label(self, value: int):
        value = max(0, min(100, int(value)))
        if value <= 0:
            text = "关闭"
        elif value <= 40:
            text = f"标准 {value}"
        elif value <= 70:
            text = f"偏强 {value}"
        else:
            text = f"强力 {value}"
        self.merge_value_label.setText(text)

    def _config_dir(self) -> Path:
        base = Path(os.environ.get("APPDATA") or Path.home())
        return base / "VideoFrameExtractor"

    def _saved_config_path(self) -> Path:
        return self._config_dir() / "saved_settings.json"

    def _default_config_path(self) -> Path:
        return self._config_dir() / "default_settings.json"

    def _project_cache_dir(self) -> Path:
        return self._config_dir() / "projects"

    def _feature_cache_dir(self) -> Path:
        return self._config_dir() / "features"

    def update_cache_label(self):
        if not hasattr(self, "cache_label"):
            return
        total_size = self._directory_size(self._project_cache_dir()) + self._directory_size(
            self._feature_cache_dir()
        )
        formatted_size = self._format_bytes(total_size)
        self.cache_label.setText(f"缓存占用：{formatted_size}")
        if hasattr(self, "top_cache_label"):
            self.top_cache_label.setText(f"缓存 {formatted_size}")

    def _directory_size(self, path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total

    def _format_bytes(self, size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024.0:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024.0
        return f"{value:.1f} TB"

    def clear_current_video_cache(self):
        if not self.video_path:
            self._set_status("请先选择视频")
            return
        deleted = 0
        try:
            project_path = self._project_cache_path(self.video_path)
            if project_path.exists():
                project_path.unlink()
                deleted += 1
            feature_path = feature_cache_path(
                str(self._feature_cache_dir()),
                self.video_path,
                240,
                5,
            )
            if feature_path.exists():
                feature_path.unlink()
                deleted += 1
            self.update_cache_label()
            self._set_status(f"已清除当前视频缓存 {deleted} 个文件")
        except Exception as exc:
            QMessageBox.warning(self, "清除缓存失败", str(exc))

    def clear_all_cache(self):
        reply = QMessageBox.question(
            self,
            "清除全部缓存",
            "确定清除全部检测缓存和特征缓存吗？不会删除参数设置和原视频。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            for path in (self._project_cache_dir(), self._feature_cache_dir()):
                if path.exists():
                    shutil.rmtree(path)
                path.mkdir(parents=True, exist_ok=True)
            self.update_cache_label()
            self._set_status("全部缓存已清除")
        except Exception as exc:
            QMessageBox.warning(self, "清除缓存失败", str(exc))

    def open_cache_folder(self):
        path = self._config_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))
                self._set_path_status("已打开缓存文件夹", path)
            else:
                QMessageBox.information(self, "缓存文件夹", str(path))
        except Exception as exc:
            QMessageBox.warning(self, "打开缓存文件夹失败", str(exc))

    def clear_current_results(self):
        self._clear_results(keep_video=True)
        gc.collect()
        self._set_status("当前检测结果已清空")

    def _video_signature(self, filepath: str) -> dict:
        path = Path(filepath)
        stat = path.stat()
        return {
            "name": path.name,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sample_hash": self._video_sample_hash(path, stat.st_size),
        }

    def _video_sample_hash(self, path: Path, size: int) -> str:
        digest = hashlib.sha1()
        digest.update(str(size).encode("utf-8"))
        chunk_size = 1024 * 1024
        offsets = [0]
        if size > chunk_size * 2:
            offsets.append(max(0, size // 2 - chunk_size // 2))
        if size > chunk_size:
            offsets.append(max(0, size - chunk_size))

        with path.open("rb") as f:
            for offset in sorted(set(offsets)):
                f.seek(offset)
                digest.update(f.read(chunk_size))
        return digest.hexdigest()

    def _project_cache_key(self, signature: dict) -> str:
        key_payload = {
            "name": signature.get("name"),
            "size": signature.get("size"),
            "sample_hash": signature.get("sample_hash"),
        }
        raw = json.dumps(key_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def _project_cache_path(self, filepath: Optional[str] = None) -> Path:
        filepath = filepath or self.video_path
        signature = self._video_signature(filepath)
        return self._project_cache_dir() / f"{self._project_cache_key(signature)}.json"

    def _current_project_payload(self) -> dict:
        return {
            "app": "VideoFrameExtractor",
            "version": APP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "video_path": self.video_path,
            "video_signature": self._video_signature(self.video_path),
            "video_info": self.video_info,
            "detection_settings": self._detection_settings(),
            "selection_settings": self._selection_settings(),
            "shots": [[start, end] for start, end in self.shots],
            "selected_frames": self.selected_frames,
            "metrics": self.last_metrics,
        }

    def _save_project_cache(self):
        if not self.video_path or not self.shots:
            return None
        path = self._project_cache_path(self.video_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._current_project_payload(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def export_project_file(self):
        if not self.video_path or not self.shots:
            self._set_status("请先处理视频，再保存检测结果")
            return

        video_name = self._safe_folder_name(Path(self.video_path).stem)
        default_name = f"{video_name}_shot_project_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "保存检测结果",
            default_name,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not filepath:
            return
        try:
            path = Path(filepath)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")
            path.write_text(
                json.dumps(self._current_project_payload(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            self._set_path_status("检测结果已保存", path)
        except Exception as exc:
            QMessageBox.warning(self, "保存检测结果失败", str(exc))

    def import_project_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "导入检测结果",
            "",
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not filepath:
            return
        try:
            payload = json.loads(Path(filepath).read_text(encoding="utf-8"))
            payload_video_path = payload.get("video_path")
            if not self.video_path:
                if payload_video_path and Path(payload_video_path).exists():
                    self.video_path = payload_video_path
                    self._set_compact_label(self.file_label, Path(payload_video_path).name)
                    if hasattr(self, "top_context_label"):
                        self._set_compact_label(
                            self.top_context_label,
                            Path(payload_video_path).name,
                            96,
                        )
                    self._clear_results()
                    self._load_video_info(payload_video_path)
                else:
                    QMessageBox.warning(self, "导入检测结果失败", "请先选择原视频文件")
                    return

            if not self._project_matches_current_video(payload):
                QMessageBox.warning(self, "导入检测结果失败", "检测结果与当前视频不匹配")
                return

            self._apply_project_payload(payload)
            self._save_project_cache()
            self._set_path_status("检测结果已导入", filepath)
        except Exception as exc:
            QMessageBox.warning(self, "导入检测结果失败", str(exc))

    def _project_matches_current_video(self, payload: dict) -> bool:
        expected = payload.get("video_signature") or {}
        if not expected or not self.video_path:
            return True
        current = self._video_signature(self.video_path)
        return (
            int(expected.get("size", -1)) == int(current.get("size", -2))
            and expected.get("sample_hash") == current.get("sample_hash")
        )

    def _try_load_project_cache(self) -> bool:
        if not self.video_path:
            return False
        try:
            path = self._project_cache_path(self.video_path)
            if not path.exists():
                return False
            payload = json.loads(path.read_text(encoding="utf-8"))
            self._apply_project_payload(payload)
            self._set_status(
                f"已加载检测缓存：{len(self.shots)} 个镜头，可直接预览和导出"
            )
            return True
        except Exception as exc:
            self._set_status(f"检测缓存读取失败，可重新检测: {exc}")
            return False

    def _apply_project_payload(self, payload: dict):
        shots = payload.get("shots") or []
        selected_frames = payload.get("selected_frames") or []
        if not shots:
            raise ValueError("缓存中没有镜头数据")

        self.shots = [(int(start), int(end)) for start, end in shots]
        self.selected_frames = [
            [int(frame_idx) for frame_idx in frames]
            for frames in selected_frames
        ]
        while len(self.selected_frames) < len(self.shots):
            start, end = self.shots[len(self.selected_frames)]
            self.selected_frames.append([(start + end) // 2])

        self.active_shot_idx = 0
        self.active_keyframe_idx = 0 if self.selected_frames and self.selected_frames[0] else None
        self.last_metrics = payload.get("metrics") or {}
        cached_video_info = payload.get("video_info") or {}
        if cached_video_info:
            self.video_info.update(cached_video_info)

        self.progress_bar.setValue(100)
        self._populate_shot_list()
        self._populate_frame_grid()
        keyframe_count = sum(len(frames) for frames in self.selected_frames)
        self.summary_label.setText(
            f"已加载缓存：{len(self.shots)} 个镜头，{keyframe_count} 帧"
        )

        first_frame = self._first_selected_frame()
        if first_frame is not None:
            self.show_frame(first_frame)

    def _current_config(self) -> dict:
        return {
            "app": "VideoFrameExtractor",
            "version": APP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "settings": {
                "preset": self.preset_combo.currentData(),
                "mode": self.mode_combo.currentData(),
                "sensitivity": self.sensitivity_slider.value(),
                "merge_strength": self.merge_slider.value(),
                "frames_per_shot": self.frames_per_shot_spin.value(),
                "max_samples_per_shot": self.max_samples_spin.value(),
                "edge_margin_ratio": self.edge_margin_spin.value() / 100.0,
                "edge_frame_offset": self.edge_frame_offset_spin.value(),
                "format": self.format_combo.currentData(),
                "detection": self._detection_settings(),
                "selection": self._selection_settings(),
            },
        }

    def _write_config(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self._current_config(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _read_config(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _apply_config(self, payload: dict):
        settings = payload.get("settings", payload)

        preset = settings.get("preset")
        if preset is not None:
            preset_index = self.preset_combo.findData(preset)
            if preset_index >= 0:
                self.preset_combo.setCurrentIndex(preset_index)

        mode = settings.get("mode")
        if mode is not None:
            mode_index = self.mode_combo.findData(mode)
            if mode_index >= 0:
                self.mode_combo.setCurrentIndex(mode_index)

        if "sensitivity" in settings:
            value = int(settings["sensitivity"])
            self.sensitivity_slider.setValue(value)
            self.apply_sensitivity_from_slider(value)

        if "merge_strength" in settings:
            value = int(settings["merge_strength"])
            self.merge_slider.setValue(value)
            self.update_merge_label(value)

        if "frames_per_shot" in settings:
            self.frames_per_shot_spin.setValue(int(settings["frames_per_shot"]))
        if "max_samples_per_shot" in settings:
            self.max_samples_spin.setValue(int(settings["max_samples_per_shot"]))
        if "edge_margin_ratio" in settings:
            self.edge_margin_spin.setValue(float(settings["edge_margin_ratio"]) * 100.0)
        if "edge_frame_offset" in settings:
            self.edge_frame_offset_spin.setValue(int(settings["edge_frame_offset"]))

        detection_settings = settings.get("detection") or {}
        if "content_threshold" in detection_settings:
            self.content_threshold_spin.setValue(float(detection_settings["content_threshold"]))
        if "adaptive_threshold" in detection_settings:
            self.adaptive_threshold_spin.setValue(float(detection_settings["adaptive_threshold"]))
        if "histogram_threshold" in detection_settings:
            self.histogram_threshold_spin.setValue(float(detection_settings["histogram_threshold"]))
        if "min_scene_len_seconds" in detection_settings:
            self.min_scene_len_spin.setValue(float(detection_settings["min_scene_len_seconds"]))

        selection_settings = settings.get("selection") or {}
        if "max_samples_per_shot" in selection_settings:
            self.max_samples_spin.setValue(int(selection_settings["max_samples_per_shot"]))
        if "edge_margin_ratio" in selection_settings:
            self.edge_margin_spin.setValue(float(selection_settings["edge_margin_ratio"]) * 100.0)

        fmt = settings.get("format")
        if fmt is not None:
            fmt_index = self.format_combo.findData(fmt)
            if fmt_index >= 0:
                self.format_combo.setCurrentIndex(fmt_index)

    def _load_default_config(self):
        path = self._default_config_path()
        if not path.exists():
            return
        try:
            self._apply_config(self._read_config(path))
            self._set_status("已载入默认参数")
        except Exception as exc:
            self._set_status(f"默认参数读取失败: {exc}")

    def save_current_config(self):
        try:
            path = self._saved_config_path()
            self._write_config(path)
            self._set_path_status("参数已保存", path)
        except Exception as exc:
            QMessageBox.warning(self, "保存参数失败", str(exc))

    def set_default_config(self):
        try:
            self._write_config(self._default_config_path())
            self._set_status("已设为默认参数，下次启动会自动使用")
        except Exception as exc:
            QMessageBox.warning(self, "设置默认失败", str(exc))

    def export_config(self):
        default_name = f"VideoFrameExtractor-settings-{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "导出参数",
            default_name,
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not filepath:
            return
        try:
            path = Path(filepath)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")
            self._write_config(path)
            self._set_path_status("参数已导出", path)
        except Exception as exc:
            QMessageBox.warning(self, "导出参数失败", str(exc))

    def import_config(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "导入参数",
            "",
            "JSON 文件 (*.json);;所有文件 (*)",
        )
        if not filepath:
            return
        try:
            self._apply_config(self._read_config(Path(filepath)))
            self._set_path_status("参数已导入", filepath)
        except Exception as exc:
            QMessageBox.warning(self, "导入参数失败", str(exc))

    def apply_detection_preset(self, _index=None):
        preset = self.preset_combo.currentData()
        self._sync_preset_buttons()
        presets = {
            "sensitive": {
                "mode": "hybrid",
                "sensitivity": 80,
                "merge": 45,
            },
            "balanced": {
                "mode": "hybrid",
                "sensitivity": 65,
                "merge": 30,
            },
            "conservative": {
                "mode": "hybrid",
                "sensitivity": 30,
                "merge": 20,
            },
        }
        config = presets.get(preset)
        if not config:
            return

        mode_index = self.mode_combo.findData(config["mode"])
        if mode_index >= 0:
            self.mode_combo.setCurrentIndex(mode_index)
        self.sensitivity_slider.setValue(config["sensitivity"])
        self.apply_sensitivity_from_slider(config["sensitivity"])
        self.merge_slider.setValue(config["merge"])
        self.update_merge_label(config["merge"])
        if hasattr(self, "status_label"):
            self._set_status("已应用检测预设")

    def on_processing_finished(self, shots, selected_frames, metrics):
        self.process_btn.setEnabled(True)
        self.shots = shots
        self.selected_frames = selected_frames
        self.last_metrics = metrics
        self.video_info.update(metrics)
        self.active_shot_idx = 0 if self.shots else None
        self.active_keyframe_idx = 0 if self._first_selected_frame() is not None else None

        self._populate_shot_list()
        self._populate_frame_grid()

        source_counts = metrics.get("cut_sources", {})
        source_text = (
            f"内容 {source_counts.get('content', 0)} | "
            f"自适应 {source_counts.get('adaptive', 0)} | "
            f"差异 {source_counts.get('histogram', 0)}"
        )
        self.summary_label.setText(
            f"检测到 {len(shots)} 个镜头，选出 {metrics.get('keyframe_count', 0)} 帧"
        )
        status_text = f"处理完成。候选切点: {source_text}"
        if metrics.get("similar_merge_count", 0):
            status_text = (
                f"{status_text} | 已合并相似镜头 {metrics['similar_merge_count']} 个"
            )
        if metrics.get("feature_cache_used"):
            status_text = f"{status_text} | 已使用特征缓存"

        first_frame = self._first_selected_frame()
        if first_frame is not None:
            self.show_frame(first_frame)

        cache_path = self._save_project_cache()
        if cache_path:
            status_text = f"{status_text} | 检测结果已缓存"
        self._set_status(status_text)
        self.update_cache_label()

    def on_processing_error(self, error_msg):
        self.process_btn.setEnabled(True)
        self._set_status(f"处理失败: {error_msg}")
        QMessageBox.warning(self, "处理失败", error_msg)

    def _populate_shot_list(self):
        self.shot_table.setRowCount(0)
        fps = float(self.video_info.get("fps") or 0)
        self.shot_table.setRowCount(len(self.shots))
        total_duration = 0.0

        for index, (start, end) in enumerate(self.shots):
            frames = self.selected_frames[index] if index < len(self.selected_frames) else []
            duration = ((end - start + 1) / fps) if fps > 0 else 0
            total_duration += duration
            frame_text = ", ".join(str(frame) for frame in frames)
            values = [
                f"{index + 1:03d}",
                f"{start} - {end}",
                f"{duration:.2f}s",
                str(len(frames)),
                "● 本地",
            ]
            tooltip = (
                f"镜头 {index + 1:03d}\n范围: {start}-{end}\n"
                f"时长: {duration:.2f}s\n关键帧: {frame_text or '-'}"
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, index)
                item.setToolTip(tooltip)
                if column in (0, 2, 3, 4):
                    item.setTextAlignment(Qt.AlignCenter)
                self.shot_table.setItem(index, column, item)
            self.shot_table.setRowHeight(index, 26)

        keyframe_count = sum(len(frames) for frames in self.selected_frames)
        avg_duration = total_duration / len(self.shots) if self.shots else 0.0
        cache_text = "本地" if self.shots else "-"
        self.summary_label.setText(
            f"镜头总数 {len(self.shots)} | 关键帧 {keyframe_count} | "
            f"平均时长 {avg_duration:.2f}s | 缓存 {cache_text}"
        )
        if self.active_shot_idx is not None and self.active_shot_idx < len(self.shots):
            self._syncing_table_selection = True
            self.shot_table.selectRow(self.active_shot_idx)
            self._syncing_table_selection = False

    def _populate_frame_grid(self):
        self.anchor_frame_grid.clear()
        self.frame_grid.clear()
        if not self.video_path or not self.selected_frames:
            self.thumbnail_context_label.setText("选择镜头后显示首中尾和其它关键帧")
            return

        active_shot = self.active_shot_idx if self.active_shot_idx is not None else 0
        active_shot = max(0, min(active_shot, len(self.shots) - 1))
        start, end = self.shots[active_shot]
        first_frame, middle_frame, last_frame = self._edge_frame_indices(start, end)
        role_items = [
            ("01 首帧", first_frame),
            ("02 中间帧", middle_frame),
            ("03 尾帧", last_frame),
        ]
        self.thumbnail_context_label.setText(
            f"镜头 {active_shot + 1:03d}，共 {len(self.selected_frames[active_shot])} 张关键帧"
        )

        processor = VideoProcessor(self.video_path)
        processor.open()
        try:
            seen_anchor_frames = set()
            for role_label, frame_idx in role_items:
                if frame_idx in seen_anchor_frames:
                    continue
                seen_anchor_frames.add(frame_idx)
                frame = processor.get_frame(frame_idx)
                pixmap = self._frame_to_pixmap(frame, 170, 96)
                item = QListWidgetItem(
                    QIcon(pixmap),
                    f"{role_label}\n{self._frame_timecode(frame_idx)}",
                )
                item.setData(Qt.UserRole, (active_shot, 0, frame_idx))
                item.setToolTip(f"镜头 {active_shot + 1:03d} | {role_label} | 帧 {frame_idx}")
                self.anchor_frame_grid.addItem(item)

            for shot_idx, frames in enumerate(self.selected_frames):
                if shot_idx == active_shot:
                    continue
                for keyframe_idx, frame_idx in enumerate(frames):
                    frame = processor.get_frame(frame_idx)
                    pixmap = self._frame_to_pixmap(frame, 92, 56)
                    item = QListWidgetItem(
                        QIcon(pixmap),
                        f"{shot_idx + 1:03d}\n{self.shots[shot_idx][0]}-{self.shots[shot_idx][1]}",
                    )
                    item.setData(Qt.UserRole, (shot_idx, keyframe_idx, frame_idx))
                    item.setToolTip(f"镜头 {shot_idx + 1:03d} | 关键帧 {keyframe_idx + 1} | 帧 {frame_idx}")
                    self.frame_grid.addItem(item)
        finally:
            processor.close()

    def on_shot_selected(self, item):
        shot_idx = item.data(Qt.UserRole)
        if shot_idx is None or shot_idx >= len(self.selected_frames):
            return

        frames = self.selected_frames[shot_idx]
        if not frames:
            return

        self.active_shot_idx = shot_idx
        self.active_keyframe_idx = 0
        self.show_frame(frames[0])

    def on_shot_table_cell_clicked(self, row: int, _column: int):
        if self._syncing_table_selection or row < 0 or row >= len(self.shots):
            return

        frames = self.selected_frames[row] if row < len(self.selected_frames) else []
        self.active_shot_idx = row
        self.active_keyframe_idx = 0 if frames else None
        self._populate_frame_grid()
        if frames:
            self.show_frame(frames[0])

    def on_grid_frame_selected(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return

        shot_idx, keyframe_idx, frame_idx = data
        shot_changed = self.active_shot_idx != shot_idx
        self.active_shot_idx = shot_idx
        self.active_keyframe_idx = keyframe_idx
        if hasattr(self, "shot_table") and shot_idx < self.shot_table.rowCount():
            self._syncing_table_selection = True
            self.shot_table.selectRow(shot_idx)
            self._syncing_table_selection = False
        if shot_changed:
            self._populate_frame_grid()
        self.show_frame(frame_idx)

    def on_frame_slider_changed(self, frame_idx: int):
        if self.video_path and self.current_frame_idx != frame_idx:
            self.show_frame(frame_idx)

    def _active_shot_bounds(self):
        if self.active_shot_idx is not None and self.active_shot_idx < len(self.shots):
            return self.shots[self.active_shot_idx]
        return None

    def _sync_frame_slider(self, frame_idx: int):
        if not hasattr(self, "frame_slider"):
            return

        bounds = self._active_shot_bounds()
        if bounds:
            start, end = bounds
            label = f"镜头 {self.active_shot_idx + 1}: {start}-{end}"
            self._set_anchor_buttons_enabled(True)
        else:
            total = int(self.video_info.get("total_frames") or 0)
            if total <= 0:
                self.frame_slider.setEnabled(False)
                self.shot_range_label.setText("当前镜头范围")
                self._set_anchor_buttons_enabled(False)
                return
            start, end = 0, total - 1
            label = f"全片: {start}-{end}"
            self._set_anchor_buttons_enabled(False)

        self.frame_slider.blockSignals(True)
        self.frame_slider.setEnabled(True)
        self.frame_slider.setRange(start, end)
        self.frame_slider.setValue(max(start, min(end, frame_idx)))
        self.frame_slider.blockSignals(False)
        self.shot_range_label.setText(label)
        if hasattr(self, "jump_frame_spin"):
            total = int(self.video_info.get("total_frames") or 0)
            self.jump_frame_spin.blockSignals(True)
            self.jump_frame_spin.setEnabled(total > 0)
            self.jump_frame_spin.setRange(0, max(0, total - 1))
            self.jump_frame_spin.setValue(max(0, min(max(0, total - 1), frame_idx)))
            self.jump_frame_spin.blockSignals(False)
            self.jump_total_label.setText(f"/ {max(0, total - 1)}")

    def _set_anchor_buttons_enabled(self, enabled: bool):
        for button in getattr(self, "anchor_jump_buttons", []):
            button.setEnabled(enabled)

    def jump_to_active_shot_anchor(self, role: str):
        bounds = self._active_shot_bounds()
        if not bounds:
            self._set_status("请先选择一个镜头")
            return

        start, end = bounds
        first_frame, middle_frame, last_frame = self._edge_frame_indices(start, end)
        role_to_frame = {
            "start": first_frame,
            "middle": middle_frame,
            "end": last_frame,
        }
        role_to_label = {
            "start": "首帧",
            "middle": "中间帧",
            "end": "尾帧",
        }
        frame_idx = role_to_frame.get(role)
        if frame_idx is None:
            return

        self.show_frame(frame_idx)
        self._set_status(f"已跳到镜头 {self.active_shot_idx + 1} 的{role_to_label.get(role, '锚点帧')}")

    def jump_to_video_frame(self):
        if not hasattr(self, "jump_frame_spin"):
            return
        frame_idx = self.jump_frame_spin.value()
        bounds = self._active_shot_bounds()
        if bounds:
            start, end = bounds
            frame_idx = max(start, min(end, frame_idx))
        self.show_frame(frame_idx)

    def nudge_frame(self, offset: int):
        if self.current_frame_idx is None:
            return

        next_frame = self.current_frame_idx + offset
        bounds = self._active_shot_bounds()
        if bounds:
            start, end = bounds
            next_frame = max(start, min(end, next_frame))
        else:
            total = int(self.video_info.get("total_frames") or 0)
            if total > 0:
                next_frame = max(0, min(total - 1, next_frame))

        self.show_frame(next_frame)

    def replace_active_keyframe(self):
        if (
            self.current_frame_idx is None
            or self.active_shot_idx is None
            or self.active_keyframe_idx is None
            or self.active_shot_idx >= len(self.selected_frames)
            or self.active_keyframe_idx >= len(self.selected_frames[self.active_shot_idx])
        ):
            self._set_status("请先在缩略图或镜头列表中选择一个关键帧")
            return

        self.selected_frames[self.active_shot_idx][self.active_keyframe_idx] = self.current_frame_idx
        self.selected_frames[self.active_shot_idx] = sorted(set(self.selected_frames[self.active_shot_idx]))
        self.active_keyframe_idx = self.selected_frames[self.active_shot_idx].index(self.current_frame_idx)
        self._populate_shot_list()
        self._populate_frame_grid()
        self._save_project_cache()
        self.update_cache_label()
        self._set_status(
            f"已更新镜头 {self.active_shot_idx + 1} 的关键帧为 {self.current_frame_idx}"
        )

    def show_frame(self, frame_idx: int):
        if not self.video_path:
            return

        try:
            processor = VideoProcessor(self.video_path)
            try:
                processor.open()
                frame = processor.get_frame(frame_idx)
            finally:
                processor.close()

            pixmap = self._frame_to_pixmap(
                frame,
                max(520, self.preview_label.width() - 20),
                max(360, self.preview_label.height() - 20),
            )
            self.preview_label.setPixmap(pixmap)
            self.current_frame_idx = frame_idx
            self._sync_frame_slider(frame_idx)
            self._update_frame_info(frame_idx)
        except Exception as exc:
            self._set_status(f"预览失败: {exc}")

    def _update_frame_info(self, frame_idx: int):
        fps = float(self.video_info.get("fps") or 0)
        seconds = frame_idx / fps if fps > 0 else 0
        shot_text = ""
        if self.active_shot_idx is not None and self.active_shot_idx < len(self.shots):
            start, end = self.shots[self.active_shot_idx]
            shot_text = f" | 镜头 {self.active_shot_idx + 1}: {start}-{end}"
            shot_duration = ((end - start + 1) / fps) if fps > 0 else 0
            if hasattr(self, "preview_subtitle_label"):
                self.preview_subtitle_label.setText(
                    f"镜头 {self.active_shot_idx + 1:03d} · {start}-{end} · {shot_duration:.2f}s"
                )
        total_duration = float(self.video_info.get("duration") or 0)
        self.frame_info_label.setText(
            f"帧 {frame_idx} | 时间 {format_timecode(seconds)} / {format_timecode(total_duration)}{shot_text}"
        )

    def _frame_to_pixmap(self, frame, max_width: int, max_height: int) -> QPixmap:
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width = frame_rgb.shape[:2]
        scale = min(max_width / width, max_height / height)
        new_width = max(1, int(width * scale))
        new_height = max(1, int(height * scale))
        resized = cv2.resize(frame_rgb, (new_width, new_height), interpolation=cv2.INTER_AREA)

        h, w, channels = resized.shape
        bytes_per_line = channels * w
        qt_image = QImage(resized.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(qt_image)

    def _make_export_dir(self, parent_dir: str, suffix: str = "keyframes") -> str:
        video_name = Path(self.video_path).stem if self.video_path else "video"
        safe_name = self._safe_folder_name(video_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_dir = Path(parent_dir) / f"{safe_name}_{suffix}_{timestamp}"
        candidate = base_dir
        counter = 2
        while candidate.exists():
            candidate = Path(f"{base_dir}_{counter}")
            counter += 1
        return str(candidate)

    def _safe_folder_name(self, name: str) -> str:
        invalid_chars = '<>:"/\\|?*'
        cleaned = "".join("_" if char in invalid_chars else char for char in name)
        cleaned = cleaned.strip(" .")
        return cleaned or "video"

    def _frame_timecode(self, frame_idx: int) -> str:
        fps = float(self.video_info.get("fps") or 0)
        seconds = frame_idx / fps if fps > 0 else 0
        return format_timecode(seconds)

    def _edge_frame_indices(self, start: int, end: int):
        if end <= start:
            return start, start, end
        max_offset = max(0, (end - start - 1) // 2)
        offset = min(self.edge_frame_offset_spin.value(), max_offset)
        first_frame = start + offset
        last_frame = end - offset
        middle_frame = (first_frame + last_frame) // 2
        return first_frame, middle_frame, last_frame

    def _edge_frame_filename(self, shot_idx: int, role: str, frame_idx: int, format_str: str) -> str:
        timecode = self._frame_timecode(frame_idx)
        ordered_role = {
            "start": "01_start",
            "middle": "02_middle",
            "end": "03_end",
        }.get(role, role)
        return (
            f"frames/shot_{shot_idx + 1:03d}_{ordered_role}_"
            f"f{frame_idx:06d}_t{timecode}.{format_str}"
        )

    def _active_or_selected_shot_idx(self):
        if self.active_shot_idx is not None and self.active_shot_idx < len(self.shots):
            return self.active_shot_idx
        if hasattr(self, "shot_table"):
            row = self.shot_table.currentRow()
            if 0 <= row < len(self.shots):
                return row
        return None

    def export_current_edge_frames(self):
        shot_idx = self._active_or_selected_shot_idx()
        if shot_idx is None:
            self._set_status("请先选择一个镜头")
            return
        self._export_edge_frames([shot_idx], "current")

    def export_all_edge_frames(self):
        if not self.shots:
            self._set_status("请先处理视频")
            return
        self._export_edge_frames(list(range(len(self.shots))), "all")

    def _export_edge_frames(self, shot_indices, scope: str):
        if not self.video_path or not self.shots:
            self._set_status("请先处理视频")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "选择导出位置")
        if not parent_dir:
            return

        try:
            output_dir = self._make_export_dir(parent_dir, "shot_frames")
            format_str = self.format_combo.currentData() or self.format_combo.currentText().lower()
            saver = ImageSaver(output_dir, format=format_str)
            exported_shots = []

            processor = VideoProcessor(self.video_path)
            try:
                processor.open()
                for shot_idx in shot_indices:
                    start, end = self.shots[shot_idx]
                    first_frame, middle_frame, last_frame = self._edge_frame_indices(start, end)
                    shot_files = []
                    exported_frame_indices = set()
                    for role, frame_idx in (
                        ("start", first_frame),
                        ("middle", middle_frame),
                        ("end", last_frame),
                    ):
                        if frame_idx in exported_frame_indices:
                            continue
                        exported_frame_indices.add(frame_idx)
                        frame = processor.get_frame(frame_idx)
                        filename = self._edge_frame_filename(shot_idx, role, frame_idx, format_str)
                        filepath = saver.save_named_frame(
                            frame,
                            filename,
                            frame_idx,
                            shot_idx=shot_idx,
                            role=role,
                        )
                        shot_files.append(
                            {
                                "role": role,
                                "frame_idx": frame_idx,
                                "timecode": self._frame_timecode(frame_idx),
                                "filename": filename,
                                "filepath": filepath,
                            }
                        )
                    exported_shots.append(
                        {
                            "index": shot_idx + 1,
                            "start_frame": start,
                            "end_frame": end,
                            "exported_start_frame": first_frame,
                            "exported_middle_frame": middle_frame,
                            "exported_end_frame": last_frame,
                            "files": shot_files,
                        }
                    )
            finally:
                processor.close()

            metadata = {
                "app": "VideoFrameExtractor",
                "version": APP_VERSION,
                "export_type": "shot_anchor_frames",
                "scope": scope,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "video_path": self.video_path,
                "format": format_str,
                "edge_offset_frames": self.edge_frame_offset_spin.value(),
                "total_exported_shots": len(exported_shots),
                "shots": exported_shots,
            }
            saver.save_metadata(metadata)
            self._set_path_status("首中尾帧已导出到", output_dir)
        except Exception as exc:
            self._set_status(f"首中尾帧导出失败: {exc}")
            QMessageBox.warning(self, "首中尾帧导出失败", str(exc))

    def export_dataset(self):
        if not self.selected_frames:
            self._set_status("请先处理视频")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "选择导出位置")
        if not parent_dir:
            return

        try:
            output_dir = self._make_export_dir(parent_dir)
            format_str = self.format_combo.currentData() or self.format_combo.currentText().lower()
            saver = ImageSaver(output_dir, format=format_str)

            processor = VideoProcessor(self.video_path)
            try:
                processor.open()
                for shot_idx, frames in enumerate(self.selected_frames):
                    for keyframe_idx, frame_idx in enumerate(frames):
                        frame = processor.get_frame(frame_idx)
                        saver.save_frame(frame, frame_idx, shot_idx, keyframe_idx)
            finally:
                processor.close()

            metadata = {
                "app": "VideoFrameExtractor",
                "version": APP_VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "video_path": self.video_path,
                "total_shots": len(self.shots),
                "total_keyframes": sum(len(frames) for frames in self.selected_frames),
                "detection_settings": self._detection_settings(),
                "selection_settings": self._selection_settings(),
                "shots": [
                    {
                        "index": index + 1,
                        "start_frame": start,
                        "end_frame": end,
                        "keyframes": self.selected_frames[index],
                    }
                    for index, (start, end) in enumerate(self.shots)
                ],
            }
            saver.save_metadata(metadata)

            self._set_path_status("数据集已导出到", output_dir)
        except Exception as exc:
            self._set_status(f"导出失败: {exc}")
            QMessageBox.warning(self, "导出失败", str(exc))

    def _first_selected_frame(self):
        for frames in self.selected_frames:
            if frames:
                return frames[0]
        return None

    def _clear_results(self, keep_video: bool = False):
        self.shots = []
        self.selected_frames = []
        self.active_shot_idx = None
        self.active_keyframe_idx = None
        self.current_frame_idx = None
        if hasattr(self, "shot_table"):
            self.shot_table.setRowCount(0)
        if hasattr(self, "anchor_frame_grid"):
            self.anchor_frame_grid.clear()
        if hasattr(self, "frame_grid"):
            self.frame_grid.clear()
        self.summary_label.setText("镜头总数 0 | 关键帧 0 | 平均时长 0.00s | 缓存 -")
        if hasattr(self, "thumbnail_context_label"):
            self.thumbnail_context_label.setText("选择镜头后显示首中尾和其它关键帧")
        if hasattr(self, "preview_subtitle_label"):
            self.preview_subtitle_label.setText("未选择镜头")
        self.frame_info_label.setText("帧信息")
        self.shot_range_label.setText("当前镜头范围")
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setEnabled(False)
        self.frame_slider.blockSignals(False)
        if hasattr(self, "jump_frame_spin"):
            self.jump_frame_spin.setRange(0, 0)
            self.jump_frame_spin.setValue(0)
            self.jump_frame_spin.setEnabled(False)
            self.jump_total_label.setText("/ 0")
        self._set_anchor_buttons_enabled(False)
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("选择视频并开始检测")
        if not keep_video:
            self.progress_bar.setValue(0)


def main():
    app = QApplication(sys.argv)
    app_icon = load_app_icon()
    if not app_icon.isNull():
        app.setWindowIcon(app_icon)
    window = MainWindow(app_icon=app_icon)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
