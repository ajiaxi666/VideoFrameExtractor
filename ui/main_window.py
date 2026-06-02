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
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.frame_selector import FrameSelector
from core.feature_cache import feature_cache_path
from core.image_saver import ImageSaver
from core.shot_detector import ShotDetector
from core.video_exporter import VideoSegmentExporter, ffmpeg_executable, format_timecode
from core.video_processor import VideoProcessor

APP_VERSION = "0.3.10"


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


class VideoExportThread(QThread):
    """Export shot videos away from the UI thread."""

    progress = pyqtSignal(int)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, video_path: str, shots: list, output_dir: str, mode: str):
        super().__init__()
        self.video_path = video_path
        self.shots = shots
        self.output_dir = output_dir
        self.mode = mode

    def run(self):
        try:
            exporter = VideoSegmentExporter(self.video_path, self.output_dir)
            metadata = exporter.export_segments(
                self.shots,
                mode=self.mode,
                progress_callback=lambda p: self.progress.emit(p),
            )
            self.progress.emit(100)
            self.finished.emit(metadata)
        except Exception as exc:
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("????????????")
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
        self.export_thread = None
        self.last_metrics = {}
        self.shortcuts = []

        self.init_ui()
        self._load_default_config()
        self.update_cache_label()
        self._install_shortcuts()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(self._build_top_bar())

        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.setHandleWidth(8)
        self.main_splitter.addWidget(self._build_controls())
        self.main_splitter.addWidget(self._build_shot_panel())
        self.main_splitter.addWidget(self._build_preview_panel())
        self.main_splitter.setSizes([380, 430, 610])
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 2)
        for index in range(3):
            self.main_splitter.setCollapsible(index, False)
        layout.addWidget(self.main_splitter, 1)

        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #eef2f7;
                color: #172033;
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 13px;
            }
            QWidget#topBar {
                background: #ffffff;
                border: 1px solid #d9dee8;
                border-radius: 8px;
            }
            QLabel#appTitleLabel {
                font-size: 18px;
                font-weight: 700;
                color: #111827;
            }
            QLabel#contextLabel {
                color: #5e6b80;
                font-size: 12px;
            }
            QLabel#sectionTitleLabel {
                color: #172033;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#summaryLabel {
                color: #1f3658;
                background: #eaf2ff;
                border: 1px solid #c9dbff;
                border-radius: 6px;
                padding: 6px 8px;
            }
            QLabel#badgeLabel {
                background: #eaf2ff;
                color: #1e3a8a;
                border: 1px solid #c9d8ff;
                border-radius: 10px;
                padding: 2px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QScrollArea {
                border: 0;
                background: transparent;
            }
            QSplitter::handle {
                background: #cfd8e6;
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
            QGroupBox {
                border: 1px solid #d5dde8;
                border-radius: 8px;
                margin-top: 12px;
                padding: 13px 10px 10px 10px;
                font-weight: 700;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #243047;
            }
            QLabel#titleLabel {
                font-size: 19px;
                font-weight: 700;
                color: #121826;
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
                padding: 6px;
            }
            QListWidget:focus {
                border: 1px solid #93b4ff;
            }
            QListWidget::item {
                border-radius: 6px;
                padding: 6px;
            }
            QListWidget::item:selected {
                background: #dce9ff;
                color: #0f172a;
            }
            QListWidget::item:hover {
                background: #eef4ff;
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
                border: 1px solid #263244;
                border-radius: 8px;
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

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(50)
        bar.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(12)

        title = QLabel("VideoFrameExtractor")
        title.setObjectName("appTitleLabel")
        self.top_context_label = QLabel("?????")
        self.top_context_label.setObjectName("contextLabel")
        self.top_context_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        version_label = QLabel(f"v{APP_VERSION}")
        version_label.setObjectName("badgeLabel")
        mode_label = QLabel("????")
        mode_label.setObjectName("badgeLabel")

        layout.addWidget(title)
        layout.addWidget(self.top_context_label, 1)
        layout.addWidget(version_label)
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

    def _build_controls(self) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(300)
        scroll.setMaximumWidth(540)

        panel = QWidget()
        panel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(4, 4, 8, 4)
        panel_layout.setSpacing(8)

        panel_layout.addWidget(self._build_file_group())
        panel_layout.addWidget(self._build_detection_group())
        panel_layout.addWidget(self._build_selection_group())
        panel_layout.addWidget(self._build_action_group())
        panel_layout.addWidget(self._build_export_group())
        panel_layout.addWidget(self._build_cache_group())
        panel_layout.addWidget(self._build_config_group())
        panel_layout.addStretch(1)

        scroll.setWidget(panel)
        return scroll

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox("????")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self.file_label = QLabel("?????")
        self.file_label.setWordWrap(True)
        self.file_label.setObjectName("fileDropLabel")
        self.file_label.setAlignment(Qt.AlignCenter)
        self.file_label.setMinimumHeight(52)
        self.file_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

        select_btn = QPushButton("??????")
        select_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        select_btn.clicked.connect(self.select_video)

        layout.addWidget(self.file_label)
        layout.addWidget(select_btn)

        self.video_meta_label = QLabel("????????????????")
        self.video_meta_label.setObjectName("mutedLabel")
        self.video_meta_label.setWordWrap(True)
        self.video_meta_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.video_meta_label)
        return group

    def _build_detection_group(self) -> QGroupBox:
        group = QGroupBox("????")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("??", "balanced")
        self.preset_combo.addItem("???", "sensitive")
        self.preset_combo.addItem("???", "conservative")
        self.preset_combo.currentIndexChanged.connect(self.apply_detection_preset)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("????????", "hybrid")
        self.mode_combo.addItem("??????", "content")
        self.mode_combo.addItem("?????", "adaptive")
        self.mode_combo.addItem("???????", "histogram")

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
        self.min_scene_len_spin.setSuffix(" ?")
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
        less_label = QLabel("??")
        less_label.setObjectName("mutedLabel")
        more_label = QLabel("??")
        more_label.setObjectName("mutedLabel")
        self.sensitivity_value_label = QLabel()
        self.sensitivity_value_label.setAlignment(Qt.AlignCenter)
        scale_row.addWidget(less_label)
        scale_row.addStretch(1)
        scale_row.addWidget(self.sensitivity_value_label)
        scale_row.addStretch(1)
        scale_row.addWidget(more_label)
        slider_layout.addLayout(scale_row)

        form.addRow("??", self.preset_combo)
        form.addRow("?????", slider_panel)

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
        off_label = QLabel("??")
        off_label.setObjectName("mutedLabel")
        strong_label = QLabel("??")
        strong_label.setObjectName("mutedLabel")
        self.merge_value_label = QLabel()
        self.merge_value_label.setAlignment(Qt.AlignCenter)
        merge_scale_row.addWidget(off_label)
        merge_scale_row.addStretch(1)
        merge_scale_row.addWidget(self.merge_value_label)
        merge_scale_row.addStretch(1)
        merge_scale_row.addWidget(strong_label)
        merge_layout.addLayout(merge_scale_row)

        form.addRow("??????", merge_panel)
        self.apply_sensitivity_from_slider(self.sensitivity_slider.value())
        self.update_merge_label(self.merge_slider.value())
        return group

    def _build_selection_group(self) -> QGroupBox:
        group = QGroupBox("?????")
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

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

        form.addRow("?????", self.frames_per_shot_spin)
        form.addRow("??????", self.edge_frame_offset_spin)
        return group

    def _build_config_group(self) -> QGroupBox:
        group = QGroupBox("????")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        save_btn = QPushButton("????")
        save_btn.setProperty("secondary", "true")
        save_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        save_btn.clicked.connect(self.save_current_config)
        default_btn = QPushButton("????")
        default_btn.setProperty("secondary", "true")
        default_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        default_btn.clicked.connect(self.set_default_config)
        first_row.addWidget(save_btn, 1)
        first_row.addWidget(default_btn, 1)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.setSpacing(8)
        import_btn = QPushButton("????")
        import_btn.setProperty("secondary", "true")
        import_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        import_btn.clicked.connect(self.import_config)
        export_btn = QPushButton("????")
        export_btn.setProperty("secondary", "true")
        export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        export_btn.clicked.connect(self.export_config)
        second_row.addWidget(import_btn, 1)
        second_row.addWidget(export_btn, 1)
        layout.addLayout(second_row)
        return group

    def _build_cache_group(self) -> QGroupBox:
        group = QGroupBox("????")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self.cache_label = QLabel("???????")
        self.cache_label.setObjectName("mutedLabel")
        self.cache_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.cache_label)

        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        clear_current_btn = QPushButton("???????")
        clear_current_btn.setProperty("secondary", "true")
        clear_current_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clear_current_btn.clicked.connect(self.clear_current_video_cache)
        clear_all_btn = QPushButton("?????")
        clear_all_btn.setProperty("secondary", "true")
        clear_all_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clear_all_btn.clicked.connect(self.clear_all_cache)
        first_row.addWidget(clear_current_btn, 1)
        first_row.addWidget(clear_all_btn, 1)
        layout.addLayout(first_row)

        second_row = QHBoxLayout()
        second_row.setSpacing(8)
        open_cache_btn = QPushButton("???????")
        open_cache_btn.setProperty("secondary", "true")
        open_cache_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        open_cache_btn.clicked.connect(self.open_cache_folder)
        clear_results_btn = QPushButton("??????")
        clear_results_btn.setProperty("secondary", "true")
        clear_results_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        clear_results_btn.clicked.connect(self.clear_current_results)
        second_row.addWidget(open_cache_btn, 1)
        second_row.addWidget(clear_results_btn, 1)
        layout.addLayout(second_row)

        return group

    def _build_action_group(self) -> QGroupBox:
        group = QGroupBox("??")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        self.process_btn = QPushButton("???????")
        self.process_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.process_btn.clicked.connect(self.process_video)
        layout.addWidget(self.process_btn)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("????")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addWidget(self.status_label)
        return group

    def _build_export_group(self) -> QGroupBox:
        group = QGroupBox("??")
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.addWidget(QLabel("??"))
        self.format_combo = QComboBox()
        self.format_combo.addItem("PNG????", "png")
        self.format_combo.addItem("JPG???/????", "jpg")
        row.addWidget(self.format_combo)
        layout.addLayout(row)

        export_btn = QPushButton("?????")
        export_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        export_btn.clicked.connect(self.export_dataset)
        layout.addWidget(export_btn)

        edge_row = QHBoxLayout()
        edge_row.setSpacing(8)
        self.export_current_edges_btn = QPushButton("???????")
        self.export_current_edges_btn.setProperty("secondary", "true")
        self.export_current_edges_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.export_current_edges_btn.clicked.connect(self.export_current_edge_frames)
        self.export_all_edges_btn = QPushButton("???????")
        self.export_all_edges_btn.setProperty("secondary", "true")
        self.export_all_edges_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.export_all_edges_btn.clicked.connect(self.export_all_edge_frames)
        edge_row.addWidget(self.export_current_edges_btn, 1)
        edge_row.addWidget(self.export_all_edges_btn, 1)
        layout.addLayout(edge_row)

        video_row = QHBoxLayout()
        video_row.addWidget(QLabel("??"))
        self.video_mode_combo = QComboBox()
        self.video_mode_combo.addItem("?????????", "precise")
        self.video_mode_combo.addItem("??????????", "copy")
        video_row.addWidget(self.video_mode_combo)
        layout.addLayout(video_row)

        self.export_segments_btn = QPushButton("??????")
        self.export_segments_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.export_segments_btn.clicked.connect(self.export_shot_videos)
        layout.addWidget(self.export_segments_btn)

        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        self.save_project_btn = QPushButton("??????")
        self.save_project_btn.setProperty("secondary", "true")
        self.save_project_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.save_project_btn.clicked.connect(self.export_project_file)
        self.import_project_btn = QPushButton("??????")
        self.import_project_btn.setProperty("secondary", "true")
        self.import_project_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.import_project_btn.clicked.connect(self.import_project_file)
        project_row.addWidget(self.save_project_btn, 1)
        project_row.addWidget(self.import_project_btn, 1)
        layout.addLayout(project_row)
        return group

    def _build_account_group(self) -> QGroupBox:
        group = QGroupBox("?????")
        layout = QVBoxLayout(group)

        self.account_label = QLabel("????")
        self.account_label.setObjectName("mutedLabel")
        layout.addWidget(self.account_label)

        row = QHBoxLayout()
        login_btn = QPushButton("??")
        login_btn.setProperty("secondary", "true")
        signup_btn = QPushButton("??")
        signup_btn.setProperty("secondary", "true")
        billing_btn = QPushButton("??")
        billing_btn.setProperty("secondary", "true")
        for button in (login_btn, signup_btn, billing_btn):
            button.clicked.connect(self.show_account_placeholder)
            row.addWidget(button)
        layout.addLayout(row)
        return group

    def _build_shot_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)
        title = QLabel("????")
        title.setObjectName("sectionTitleLabel")
        self.summary_label = QLabel("???????")
        self.summary_label.setObjectName("summaryLabel")
        self.summary_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header_layout.addWidget(title)
        header_layout.addStretch(1)
        header_layout.addWidget(self.summary_label, 1)
        layout.addWidget(header)

        shot_splitter = QSplitter(Qt.Vertical)

        shot_box = QGroupBox("????")
        shot_layout = QVBoxLayout(shot_box)
        self.shot_list = QListWidget()
        self.shot_list.setAlternatingRowColors(True)
        self.shot_list.itemClicked.connect(self.on_shot_selected)
        shot_layout.addWidget(self.shot_list)
        shot_splitter.addWidget(shot_box)

        frame_box = QGroupBox("??????")
        frame_layout = QVBoxLayout(frame_box)
        self.frame_grid = QListWidget()
        self.frame_grid.setViewMode(QListView.IconMode)
        self.frame_grid.setResizeMode(QListView.Adjust)
        self.frame_grid.setMovement(QListView.Static)
        self.frame_grid.setIconSize(QSize(160, 90))
        self.frame_grid.setGridSize(QSize(190, 132))
        self.frame_grid.setSpacing(8)
        self.frame_grid.itemClicked.connect(self.on_grid_frame_selected)
        frame_layout.addWidget(self.frame_grid)
        shot_splitter.addWidget(frame_box)
        shot_splitter.setSizes([320, 420])

        layout.addWidget(shot_splitter)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(8)

        preview_box = QGroupBox("???????????")
        preview_layout = QVBoxLayout(preview_box)
        preview_layout.setSpacing(8)

        self.preview_label = QLabel("?????????")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(520, 360)
        self.preview_label.setScaledContents(False)
        preview_layout.addWidget(self.preview_label, 1)

        info_row = QHBoxLayout()
        info_row.setSpacing(8)
        self.frame_info_label = QLabel("???")
        self.frame_info_label.setObjectName("mutedLabel")
        self.shot_range_label = QLabel("??????")
        self.shot_range_label.setObjectName("mutedLabel")
        self.shot_range_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        info_row.addWidget(self.frame_info_label, 1)
        info_row.addWidget(self.shot_range_label, 1)
        preview_layout.addLayout(info_row)

        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setEnabled(False)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self.on_frame_slider_changed)
        preview_layout.addWidget(self.frame_slider)

        nav_layout = QHBoxLayout()
        previous_btn = QToolButton()
        previous_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowLeft))
        previous_btn.setToolTip("???????? / ??? 4?")
        previous_btn.clicked.connect(lambda: self.nudge_frame(-1))
        next_btn = QToolButton()
        next_btn.setIcon(self.style().standardIcon(QStyle.SP_ArrowRight))
        next_btn.setToolTip("???????? / ??? 6?")
        next_btn.clicked.connect(lambda: self.nudge_frame(1))
        replace_btn = QPushButton("???????")
        replace_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        replace_btn.setToolTip("??????????? 5 / ???")
        replace_btn.clicked.connect(self.replace_active_keyframe)
        nav_layout.addWidget(previous_btn)
        nav_layout.addWidget(next_btn)
        nav_layout.addWidget(replace_btn)
        preview_layout.addLayout(nav_layout)

        layout.addWidget(preview_box)
        return panel

    def select_video(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "??????",
            "",
            "???? (*.mp4 *.avi *.mov *.mkv *.webm);;???? (*)",
        )

        if not filepath:
            return

        self.load_video_path(filepath)

    def load_video_path(self, filepath: str):
        if not filepath:
            return
        path = Path(filepath)
        if not path.exists():
            self._set_status(f"?????: {filepath}")
            return
        if not self._is_supported_video(path):
            self._set_status("??????????")
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
        separator = " " if prefix.endswith("?") else ": "
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
                f"{duration:.1f} ? | {probe.width}x{probe.height} | "
                f"{probe.fps:.2f} fps | {probe.total_frames} ?"
            )
            if hasattr(self, "top_context_label"):
                self._set_compact_label(
                    self.top_context_label,
                    (
                        f"{Path(filepath).name} | {duration:.1f} ? | "
                        f"{probe.width}x{probe.height} | {probe.fps:.2f} fps"
                    ),
                    120,
                )
            self._set_status("????????????")
        except Exception as exc:
            self._set_compact_label(self.video_meta_label, f"??????: {exc}")
            if hasattr(self, "top_context_label"):
                self._set_compact_label(self.top_context_label, "??????")
            self._set_status("??????")

    def process_video(self):
        if not self.video_path:
            self._set_status("????????")
            return

        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        self._set_status("????????????...")
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
            label = "???"
        elif value <= 40:
            label = "???"
        else:
            label = "??"
        self.sensitivity_value_label.setText(f"{label} {value}")

    def _merge_threshold_from_slider(self, value: int) -> float:
        if value <= 0:
            return 0.0
        return round(0.02 + max(0, min(100, int(value))) * 0.002, 3)

    def update_merge_label(self, value: int):
        value = max(0, min(100, int(value)))
        if value <= 0:
            text = "??"
        elif value <= 40:
            text = f"?? {value}"
        elif value <= 70:
            text = f"?? {value}"
        else:
            text = f"?? {value}"
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
        self.cache_label.setText(f"?????{self._format_bytes(total_size)}")

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
            self._set_status("??????")
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
            self._set_status(f"????????? {deleted} ???")
        except Exception as exc:
            QMessageBox.warning(self, "??????", str(exc))

    def clear_all_cache(self):
        reply = QMessageBox.question(
            self,
            "??????",
            "??????????????????????????????",
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
            self._set_status("???????")
        except Exception as exc:
            QMessageBox.warning(self, "??????", str(exc))

    def open_cache_folder(self):
        path = self._config_dir()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(str(path))
                self._set_path_status("????????", path)
            else:
                QMessageBox.information(self, "?????", str(path))
        except Exception as exc:
            QMessageBox.warning(self, "?????????", str(exc))

    def clear_current_results(self):
        self._clear_results(keep_video=True)
        gc.collect()
        self._set_status("?????????")

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
            self._set_status("??????????????")
            return

        video_name = self._safe_folder_name(Path(self.video_path).stem)
        default_name = f"{video_name}_shot_project_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "??????",
            default_name,
            "JSON ?? (*.json);;???? (*)",
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
            self._set_path_status("???????", path)
        except Exception as exc:
            QMessageBox.warning(self, "????????", str(exc))

    def import_project_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "??????",
            "",
            "JSON ?? (*.json);;???? (*)",
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
                    QMessageBox.warning(self, "????????", "?????????")
                    return

            if not self._project_matches_current_video(payload):
                QMessageBox.warning(self, "????????", "????????????")
                return

            self._apply_project_payload(payload)
            self._save_project_cache()
            self._set_path_status("???????", filepath)
        except Exception as exc:
            QMessageBox.warning(self, "????????", str(exc))

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
                f"????????{len(self.shots)} ????????????"
            )
            return True
        except Exception as exc:
            self._set_status(f"??????????????: {exc}")
            return False

    def _apply_project_payload(self, payload: dict):
        shots = payload.get("shots") or []
        selected_frames = payload.get("selected_frames") or []
        if not shots:
            raise ValueError("?????????")

        self.shots = [(int(start), int(end)) for start, end in shots]
        self.selected_frames = [
            [int(frame_idx) for frame_idx in frames]
            for frames in selected_frames
        ]
        while len(self.selected_frames) < len(self.shots):
            start, end = self.shots[len(self.selected_frames)]
            self.selected_frames.append([(start + end) // 2])

        self.last_metrics = payload.get("metrics") or {}
        cached_video_info = payload.get("video_info") or {}
        if cached_video_info:
            self.video_info.update(cached_video_info)

        self.progress_bar.setValue(100)
        self._populate_shot_list()
        self._populate_frame_grid()
        keyframe_count = sum(len(frames) for frames in self.selected_frames)
        self.summary_label.setText(
            f"??????{len(self.shots)} ????{keyframe_count} ?"
        )

        first_frame = self._first_selected_frame()
        if first_frame is not None:
            self.active_shot_idx = 0
            self.active_keyframe_idx = 0
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
                "video_export_mode": self.video_mode_combo.currentData(),
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

        fmt = settings.get("format")
        if fmt is not None:
            fmt_index = self.format_combo.findData(fmt)
            if fmt_index >= 0:
                self.format_combo.setCurrentIndex(fmt_index)

        video_mode = settings.get("video_export_mode")
        if video_mode is not None:
            video_mode_index = self.video_mode_combo.findData(video_mode)
            if video_mode_index >= 0:
                self.video_mode_combo.setCurrentIndex(video_mode_index)

    def _load_default_config(self):
        path = self._default_config_path()
        if not path.exists():
            return
        try:
            self._apply_config(self._read_config(path))
            self._set_status("???????")
        except Exception as exc:
            self._set_status(f"????????: {exc}")

    def save_current_config(self):
        try:
            path = self._saved_config_path()
            self._write_config(path)
            self._set_path_status("?????", path)
        except Exception as exc:
            QMessageBox.warning(self, "??????", str(exc))

    def set_default_config(self):
        try:
            self._write_config(self._default_config_path())
            self._set_status("?????????????????")
        except Exception as exc:
            QMessageBox.warning(self, "??????", str(exc))

    def export_config(self):
        default_name = f"VideoFrameExtractor-settings-{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath, _ = QFileDialog.getSaveFileName(
            self,
            "????",
            default_name,
            "JSON ?? (*.json);;???? (*)",
        )
        if not filepath:
            return
        try:
            path = Path(filepath)
            if path.suffix.lower() != ".json":
                path = path.with_suffix(".json")
            self._write_config(path)
            self._set_path_status("?????", path)
        except Exception as exc:
            QMessageBox.warning(self, "??????", str(exc))

    def import_config(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self,
            "????",
            "",
            "JSON ?? (*.json);;???? (*)",
        )
        if not filepath:
            return
        try:
            self._apply_config(self._read_config(Path(filepath)))
            self._set_path_status("?????", filepath)
        except Exception as exc:
            QMessageBox.warning(self, "??????", str(exc))

    def apply_detection_preset(self, _index=None):
        preset = self.preset_combo.currentData()
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
            self._set_status("???????")

    def on_processing_finished(self, shots, selected_frames, metrics):
        self.process_btn.setEnabled(True)
        self.shots = shots
        self.selected_frames = selected_frames
        self.last_metrics = metrics
        self.video_info.update(metrics)

        self._populate_shot_list()
        self._populate_frame_grid()

        source_counts = metrics.get("cut_sources", {})
        source_text = (
            f"?? {source_counts.get('content', 0)} | "
            f"??? {source_counts.get('adaptive', 0)} | "
            f"?? {source_counts.get('histogram', 0)}"
        )
        self.summary_label.setText(
            f"??? {len(shots)} ?????? {metrics.get('keyframe_count', 0)} ?"
        )
        status_text = f"?????????: {source_text}"
        if metrics.get("similar_merge_count", 0):
            status_text = (
                f"{status_text} | ??????? {metrics['similar_merge_count']} ?"
            )
        if metrics.get("feature_cache_used"):
            status_text = f"{status_text} | ???????"

        first_frame = self._first_selected_frame()
        if first_frame is not None:
            self.active_shot_idx = 0
            self.active_keyframe_idx = 0
            self.show_frame(first_frame)

        cache_path = self._save_project_cache()
        if cache_path:
            status_text = f"{status_text} | ???????"
        self._set_status(status_text)
        self.update_cache_label()

    def on_processing_error(self, error_msg):
        self.process_btn.setEnabled(True)
        self._set_status(f"????: {error_msg}")
        QMessageBox.warning(self, "????", error_msg)

    def _populate_shot_list(self):
        self.shot_list.clear()
        fps = float(self.video_info.get("fps") or 0)

        for index, (start, end) in enumerate(self.shots):
            frames = self.selected_frames[index] if index < len(self.selected_frames) else []
            duration = ((end - start + 1) / fps) if fps > 0 else 0
            frame_text = ", ".join(str(frame) for frame in frames)
            item = QListWidgetItem(
                f"{index + 1:03d} | {start}-{end} | {duration:.2f}s | ? {frame_text}"
            )
            item.setData(Qt.UserRole, index)
            self.shot_list.addItem(item)

    def _populate_frame_grid(self):
        self.frame_grid.clear()
        if not self.video_path or not self.selected_frames:
            return

        processor = VideoProcessor(self.video_path)
        processor.open()
        try:
            for shot_idx, frames in enumerate(self.selected_frames):
                for keyframe_idx, frame_idx in enumerate(frames):
                    frame = processor.get_frame(frame_idx)
                    pixmap = self._frame_to_pixmap(frame, 160, 90)
                    item = QListWidgetItem(
                        QIcon(pixmap),
                        f"?? {shot_idx + 1}\n? {frame_idx}",
                    )
                    item.setData(Qt.UserRole, (shot_idx, keyframe_idx, frame_idx))
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

    def on_grid_frame_selected(self, item):
        data = item.data(Qt.UserRole)
        if not data:
            return

        shot_idx, keyframe_idx, frame_idx = data
        self.active_shot_idx = shot_idx
        self.active_keyframe_idx = keyframe_idx
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
            label = f"?? {self.active_shot_idx + 1}: {start}-{end}"
        else:
            total = int(self.video_info.get("total_frames") or 0)
            if total <= 0:
                self.frame_slider.setEnabled(False)
                self.shot_range_label.setText("??????")
                return
            start, end = 0, total - 1
            label = f"??: {start}-{end}"

        self.frame_slider.blockSignals(True)
        self.frame_slider.setEnabled(True)
        self.frame_slider.setRange(start, end)
        self.frame_slider.setValue(max(start, min(end, frame_idx)))
        self.frame_slider.blockSignals(False)
        self.shot_range_label.setText(label)

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
            self._set_status("???????????????????")
            return

        self.selected_frames[self.active_shot_idx][self.active_keyframe_idx] = self.current_frame_idx
        self.selected_frames[self.active_shot_idx] = sorted(set(self.selected_frames[self.active_shot_idx]))
        self.active_keyframe_idx = self.selected_frames[self.active_shot_idx].index(self.current_frame_idx)
        self._populate_shot_list()
        self._populate_frame_grid()
        self._save_project_cache()
        self.update_cache_label()
        self._set_status(
            f"????? {self.active_shot_idx + 1} ????? {self.current_frame_idx}"
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
            self._set_status(f"????: {exc}")

    def _update_frame_info(self, frame_idx: int):
        fps = float(self.video_info.get("fps") or 0)
        seconds = frame_idx / fps if fps > 0 else 0
        shot_text = ""
        if self.active_shot_idx is not None and self.active_shot_idx < len(self.shots):
            start, end = self.shots[self.active_shot_idx]
            shot_text = f" | ?? {self.active_shot_idx + 1}: {start}-{end}"
        self.frame_info_label.setText(f"? {frame_idx} | {seconds:.2f} ?{shot_text}")

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
            return start, end
        max_offset = max(0, (end - start - 1) // 2)
        offset = min(self.edge_frame_offset_spin.value(), max_offset)
        return start + offset, end - offset

    def _edge_frame_filename(self, shot_idx: int, role: str, frame_idx: int, format_str: str) -> str:
        timecode = self._frame_timecode(frame_idx)
        return f"frames/shot_{shot_idx + 1:03d}_{role}_f{frame_idx:06d}_t{timecode}.{format_str}"

    def _active_or_selected_shot_idx(self):
        if self.active_shot_idx is not None and self.active_shot_idx < len(self.shots):
            return self.active_shot_idx
        item = self.shot_list.currentItem()
        if item is not None:
            shot_idx = item.data(Qt.UserRole)
            if shot_idx is not None and shot_idx < len(self.shots):
                return shot_idx
        return None

    def export_current_edge_frames(self):
        shot_idx = self._active_or_selected_shot_idx()
        if shot_idx is None:
            self._set_status("????????")
            return
        self._export_edge_frames([shot_idx], "current")

    def export_all_edge_frames(self):
        if not self.shots:
            self._set_status("??????")
            return
        self._export_edge_frames(list(range(len(self.shots))), "all")

    def _export_edge_frames(self, shot_indices, scope: str):
        if not self.video_path or not self.shots:
            self._set_status("??????")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "??????")
        if not parent_dir:
            return

        try:
            output_dir = self._make_export_dir(parent_dir, "shot_edges")
            format_str = self.format_combo.currentData() or self.format_combo.currentText().lower()
            saver = ImageSaver(output_dir, format=format_str)
            exported_shots = []

            processor = VideoProcessor(self.video_path)
            try:
                processor.open()
                for shot_idx in shot_indices:
                    start, end = self.shots[shot_idx]
                    first_frame, last_frame = self._edge_frame_indices(start, end)
                    shot_files = []
                    for role, frame_idx in (("start", first_frame), ("end", last_frame)):
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
                            "exported_end_frame": last_frame,
                            "files": shot_files,
                        }
                    )
            finally:
                processor.close()

            metadata = {
                "app": "VideoFrameExtractor",
                "version": APP_VERSION,
                "export_type": "shot_edge_frames",
                "scope": scope,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "video_path": self.video_path,
                "format": format_str,
                "edge_offset_frames": self.edge_frame_offset_spin.value(),
                "total_exported_shots": len(exported_shots),
                "shots": exported_shots,
            }
            saver.save_metadata(metadata)
            self._set_path_status("???????", output_dir)
        except Exception as exc:
            self._set_status(f"???????: {exc}")
            QMessageBox.warning(self, "???????", str(exc))

    def export_dataset(self):
        if not self.selected_frames:
            self._set_status("??????")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "??????")
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

            self._set_path_status("???????", output_dir)
        except Exception as exc:
            self._set_status(f"????: {exc}")
            QMessageBox.warning(self, "????", str(exc))

    def export_shot_videos(self):
        if not self.video_path or not self.shots:
            self._set_status("??????")
            return
        if self.export_thread is not None and self.export_thread.isRunning():
            self._set_status("????????")
            return

        parent_dir = QFileDialog.getExistingDirectory(self, "??????")
        if not parent_dir:
            return

        output_dir = self._make_export_dir(parent_dir, "shot_videos")
        mode = self.video_mode_combo.currentData() or "precise"
        self._last_video_output_dir = output_dir
        self.progress_bar.setValue(0)
        self._set_video_export_running(True)

        if mode == "copy" and not ffmpeg_executable():
            self._set_status("??? ffmpeg?????????????")
        else:
            self._set_status("????????...")

        self.export_thread = VideoExportThread(
            self.video_path,
            list(self.shots),
            output_dir,
            mode,
        )
        self.export_thread.progress.connect(self.progress_bar.setValue)
        self.export_thread.finished.connect(self.on_video_export_finished)
        self.export_thread.error.connect(self.on_video_export_error)
        self.export_thread.start()

    def _set_video_export_running(self, running: bool):
        enabled = not running
        self.process_btn.setEnabled(enabled)
        if hasattr(self, "export_current_edges_btn"):
            self.export_current_edges_btn.setEnabled(enabled)
        if hasattr(self, "export_all_edges_btn"):
            self.export_all_edges_btn.setEnabled(enabled)
        if hasattr(self, "export_segments_btn"):
            self.export_segments_btn.setEnabled(enabled)
        if hasattr(self, "save_project_btn"):
            self.save_project_btn.setEnabled(enabled)
        if hasattr(self, "import_project_btn"):
            self.import_project_btn.setEnabled(enabled)

    def on_video_export_finished(self, metadata: dict):
        self._set_video_export_running(False)
        self.export_thread = None
        output_dir = getattr(self, "_last_video_output_dir", "")
        count = metadata.get("total_shots", 0)
        actual_mode = metadata.get("actual_mode", "")
        if actual_mode == "opencv_reencode":
            mode_text = "?????"
        elif actual_mode == "copy":
            mode_text = "????"
        else:
            mode_text = "??????"
        self._set_path_status(f"??????? {count} ??{mode_text}??", output_dir)

    def on_video_export_error(self, error_msg: str):
        self._set_video_export_running(False)
        self.export_thread = None
        self._set_status(f"????????: {error_msg}")
        QMessageBox.warning(self, "????????", error_msg)

    def show_account_placeholder(self):
        QMessageBox.information(
            self,
            "?????",
            "?????????????????????????????????????????",
        )

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
        self.shot_list.clear()
        self.frame_grid.clear()
        self.summary_label.setText("???????")
        self.frame_info_label.setText("???")
        self.shot_range_label.setText("??????")
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.setEnabled(False)
        self.frame_slider.blockSignals(False)
        self.preview_label.setPixmap(QPixmap())
        self.preview_label.setText("?????????")
        if not keep_video:
            self.progress_bar.setValue(0)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
