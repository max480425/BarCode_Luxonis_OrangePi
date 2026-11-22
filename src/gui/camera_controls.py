from __future__ import annotations

import sys
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from src.camera_stream import CameraDeviceInfo


class CameraControls(QWidget):
    """
    Panel sterowania wyborem aktywnej kamery, czasem naświetlania, czułością ISO,
    częstotliwością dekodowania oraz współrzędnymi ROI w czasie rzeczywistym.
    """
    camera_source_changed = Signal(object)              # CameraDeviceInfo
    refresh_cameras_requested = Signal()                # Żądanie odświeżenia listy kamer
    exposure_changed = Signal(int, int, bool)          # (exposure_us, iso, auto_exposure)
    interval_changed = Signal(float)                    # interval in seconds
    trigger_detection_requested = Signal()              # manual scan request
    roi_inputs_changed = Signal(float, float, float, float)  # (x1, y1, x2, y2) in 0.0 - 1.0

    def __init__(
        self,
        available_cameras: Optional[List[CameraDeviceInfo]] = None,
        selected_camera_id: Optional[str] = None,
        initial_exp_us: int = 20000,
        initial_iso: int = 100,
        initial_auto_exp: bool = True,
        initial_interval_s: float = 1.0,
        initial_roi: Tuple[float, float, float, float] = (0.1, 0.2, 0.9, 0.8),
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.available_cameras: List[CameraDeviceInfo] = available_cameras or []
        self.selected_camera_id = selected_camera_id
        self.exposure_us = initial_exp_us
        self.iso = initial_iso
        self.auto_exp = initial_auto_exp
        self.interval_s = initial_interval_s
        self.roi = initial_roi

        self._block_signals = False
        self._preset_buttons: Dict[int, QPushButton] = {}
        self._init_ui()

    def _init_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("QScrollArea { background-color: #141414; border: none; }")
        scroll.viewport().setStyleSheet("background-color: #141414;")

        content = QWidget()
        content.setObjectName("cameraControlsContent")
        content.setStyleSheet("""
            #cameraControlsContent { background-color: #141414; }
            #cameraControlsContent QGroupBox {
                margin-top: 6px;
                padding-top: 5px;
            }
        """)
        main_layout = QVBoxLayout(content)
        main_layout.setContentsMargins(2, 0, 4, 2)
        main_layout.setSpacing(3)

        # ==========================================
        # 0. GRUPA: WYBÓR ŹRÓDŁA OBRAZU (KAMERA)
        # ==========================================
        cam_group = QGroupBox("Wybór Źródła Obrazu (Kamera)")
        cam_layout = QVBoxLayout(cam_group)
        cam_layout.setContentsMargins(8, 4, 8, 4)
        cam_layout.setSpacing(2)

        cam_row = QHBoxLayout()
        cam_row.setSpacing(6)
        self.combo_cameras = QComboBox()
        self.combo_cameras.setMinimumHeight(28)
        self.combo_cameras.setStyleSheet("""
            QComboBox {
                font-weight: bold;
                color: #00ddff;
                padding: 4px 8px;
                background-color: #1f242c;
                border: 1px solid #0088aa;
                border-radius: 4px;
            }
        """)
        self.combo_cameras.currentIndexChanged.connect(self._on_camera_combo_changed)

        self.btn_refresh_cameras = QPushButton("🔄 Odśwież")
        self.btn_refresh_cameras.setToolTip("Wyszukaj ponownie podłączone kamery USB i Luxonis OAK")
        self.btn_refresh_cameras.setMinimumHeight(28)
        self.btn_refresh_cameras.setStyleSheet("padding: 4px 10px; font-size: 11px; font-weight: bold;")
        self.btn_refresh_cameras.clicked.connect(self._on_refresh_clicked)

        cam_row.addWidget(self.combo_cameras, stretch=1)
        cam_row.addWidget(self.btn_refresh_cameras)
        cam_layout.addLayout(cam_row)

        self.lbl_camera_desc = QLabel("Aktywne urządzenie: -")
        self.lbl_camera_desc.setWordWrap(True)
        self.lbl_camera_desc.setStyleSheet("color: #9aa6b2; font-size: 11px;")
        cam_layout.addWidget(self.lbl_camera_desc)

        main_layout.addWidget(cam_group)

        # ==========================================
        # 1. GRUPA: PARAMETRY EKSPOZYCJI I SENSORA
        # ==========================================
        exp_group = QGroupBox("Sterowanie Kamerą i Ekspozycją (Na Żywo)")
        exp_layout = QVBoxLayout(exp_group)
        exp_layout.setContentsMargins(8, 4, 8, 4)
        exp_layout.setSpacing(3)

        # Tryb ekspozycji
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(14)
        self.radio_manual = QRadioButton("Manual Exposure (Kontrola µs)")
        self.radio_auto = QRadioButton("Auto Exposure (Biurko / Jasny obraz)")
        if self.auto_exp:
            self.radio_auto.setChecked(True)
        else:
            self.radio_manual.setChecked(True)

        self.radio_manual.toggled.connect(self._on_mode_toggled)
        mode_layout.addWidget(self.radio_manual)
        mode_layout.addWidget(self.radio_auto)
        mode_layout.addStretch(1)
        exp_layout.addLayout(mode_layout)

        # Czas ekspozycji (µs): Etykieta + Suwak + Pole liczbowe w jednym czytelnym rzędzie
        exp_val_layout = QHBoxLayout()
        exp_val_layout.setSpacing(8)
        lbl_exp = QLabel("Ekspozycja (µs):")
        lbl_exp.setMinimumWidth(98)
        exp_val_layout.addWidget(lbl_exp)

        self.slider_exp = QSlider(Qt.Orientation.Horizontal)
        self.slider_exp.setRange(50, 33000)
        self.slider_exp.setSingleStep(100)
        self.slider_exp.setValue(self.exposure_us)
        self.slider_exp.setMinimumHeight(22)
        self.slider_exp.valueChanged.connect(self._on_slider_exp_changed)
        exp_val_layout.addWidget(self.slider_exp, stretch=1)

        self.spin_exp = QSpinBox()
        self.spin_exp.setRange(50, 33000)
        self.spin_exp.setSingleStep(100)
        self.spin_exp.setValue(self.exposure_us)
        self.spin_exp.setSuffix(" µs")
        self.spin_exp.setMinimumWidth(114)
        self.spin_exp.setMinimumHeight(26)
        self.spin_exp.valueChanged.connect(self._on_spin_exp_changed)
        exp_val_layout.addWidget(self.spin_exp)
        exp_layout.addLayout(exp_val_layout)

        # Siatka 2x4 szybkich presetów ekspozycji (Linia + Biurko) — nic nie jest ściskane
        presets_grid = QGridLayout()
        presets_grid.setSpacing(5)
        presets_lbl = QLabel("Presety:")
        presets_lbl.setStyleSheet("color: #9aa6b2; font-size: 11px;")
        presets_grid.addWidget(presets_lbl, 0, 0, 2, 1, Qt.AlignmentFlag.AlignVCenter)

        preset_values = [100, 200, 500, 1000, 2000, 5000, 10000, 20000]
        for idx, val in enumerate(preset_values):
            lbl = f"{val} µs" if val < 5000 else f"{val // 1000} ms (Biurko)"
            btn = QPushButton(lbl)
            btn.setMinimumHeight(26)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.setToolTip(
                f"Ustaw ręczną ekspozycję {val} µs"
                + (" (tryb linii produkcyjnej)" if val <= 2000 else " (tryb biurkowy)")
            )
            btn.clicked.connect(lambda checked=False, v=val: self.set_exposure_us(v))
            self._preset_buttons[val] = btn
            row = idx // 4
            col = 1 + (idx % 4)
            presets_grid.addWidget(btn, row, col)
        exp_layout.addLayout(presets_grid)

        # Czułość ISO (dla matryc z ręcznym wzmocnieniem, np. Luxonis IMX378)
        self.iso_container = QWidget()
        iso_val_layout = QHBoxLayout(self.iso_container)
        iso_val_layout.setContentsMargins(0, 0, 0, 0)
        iso_val_layout.setSpacing(8)

        self.lbl_iso_title = QLabel("Czułość ISO (OAK-1):")
        self.lbl_iso_title.setMinimumWidth(145)
        iso_val_layout.addWidget(self.lbl_iso_title)

        self.slider_iso = QSlider(Qt.Orientation.Horizontal)
        self.slider_iso.setRange(100, 1600)
        self.slider_iso.setSingleStep(50)
        self.slider_iso.setValue(self.iso)
        self.slider_iso.setMinimumHeight(22)
        self.slider_iso.valueChanged.connect(self._on_slider_iso_changed)
        iso_val_layout.addWidget(self.slider_iso, stretch=1)

        self.spin_iso = QSpinBox()
        self.spin_iso.setRange(100, 1600)
        self.spin_iso.setSingleStep(100)
        self.spin_iso.setValue(self.iso)
        self.spin_iso.setMinimumWidth(86)
        self.spin_iso.setMinimumHeight(26)
        self.spin_iso.valueChanged.connect(self._on_spin_iso_changed)
        iso_val_layout.addWidget(self.spin_iso)

        exp_layout.addWidget(self.iso_container)

        self.slider_exp.setEnabled(not self.auto_exp)
        self.spin_exp.setEnabled(not self.auto_exp)
        self._update_preset_highlights()

        main_layout.addWidget(exp_group)

        # ==========================================
        # 2. GRUPA: CZĘSTOTLIWOŚĆ DEKODOWANIA
        # ==========================================
        scan_group = QGroupBox("Harmonogram Detekcji Kodów (zxing-cpp)")
        scan_layout = QHBoxLayout(scan_group)
        scan_layout.setContentsMargins(8, 4, 8, 4)
        scan_layout.setSpacing(8)

        scan_layout.addWidget(QLabel("Interwał:"))
        self.combo_interval = QComboBox()
        self.combo_interval.setMinimumHeight(25)
        self.intervals = [
            ("Co 0.2 s (5 FPS - Szybki)", 0.2),
            ("Co 0.5 s (2 FPS)", 0.5),
            ("Co 1.0 s (1 FPS - Zalecany)", 1.0),
            ("Co 2.0 s", 2.0),
            ("Co 5.0 s", 5.0),
            ("Tylko ręcznie", 0.0),
        ]
        for label, val in self.intervals:
            self.combo_interval.addItem(label, val)

        for idx, (_, val) in enumerate(self.intervals):
            if abs(val - self.interval_s) < 0.05:
                self.combo_interval.setCurrentIndex(idx)
                break

        self.combo_interval.currentIndexChanged.connect(self._on_interval_changed)
        scan_layout.addWidget(self.combo_interval, stretch=1)

        self.btn_scan_now = QPushButton("🔍 Szukaj kodów teraz")
        self.btn_scan_now.setMinimumHeight(26)
        self.btn_scan_now.setStyleSheet(
            "QPushButton { background-color: #007acc; border: 1px solid #33aaff; color: white; "
            "font-weight: bold; padding: 4px 12px; }"
            "QPushButton:hover { background-color: #008ce6; border-color: #66ccff; }"
            "QPushButton:pressed { background-color: #005f9e; }"
        )
        self.btn_scan_now.clicked.connect(self.trigger_detection_requested.emit)
        scan_layout.addWidget(self.btn_scan_now)

        main_layout.addWidget(scan_group)

        # ==========================================
        # 3. GRUPA: PRECYZYJNA KONTROLA OBSZARU ROI
        # ==========================================
        roi_group = QGroupBox("Granice Aktywnego Kadru (ROI %)")
        roi_layout = QHBoxLayout(roi_group)
        roi_layout.setContentsMargins(8, 4, 8, 4)
        roi_layout.setSpacing(6)

        rx1, ry1, rx2, ry2 = self.roi
        self.spin_x1 = self._create_roi_spin(rx1 * 100.0)
        self.spin_y1 = self._create_roi_spin(ry1 * 100.0)
        self.spin_x2 = self._create_roi_spin(rx2 * 100.0)
        self.spin_y2 = self._create_roi_spin(ry2 * 100.0)

        for lbl_txt, spin in [("X₁:", self.spin_x1), ("Y₁:", self.spin_y1), ("X₂:", self.spin_x2), ("Y₂:", self.spin_y2)]:
            roi_layout.addWidget(QLabel(lbl_txt))
            roi_layout.addWidget(spin, stretch=1)

        self.btn_reset_roi = QPushButton("↺ Domyślne")
        self.btn_reset_roi.setToolTip("Przywróć domyślne granice ROI (10%, 20% - 90%, 80%)")
        self.btn_reset_roi.setMinimumHeight(25)
        self.btn_reset_roi.setStyleSheet("padding: 3px 8px; font-size: 11px; font-weight: bold;")
        self.btn_reset_roi.clicked.connect(self._on_reset_roi_clicked)
        roi_layout.addWidget(self.btn_reset_roi)

        main_layout.addWidget(roi_group)
        main_layout.addStretch(1)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        # Inicjalne załadowanie listy kamer do ComboBox po utworzeniu wszystkich kontrolek
        self.set_camera_list(self.available_cameras, self.selected_camera_id)

    def _update_preset_highlights(self) -> None:
        for val, btn in self._preset_buttons.items():
            is_selected = (not self.auto_exp) and (abs(self.exposure_us - val) <= 10)
            if is_selected:
                btn.setStyleSheet(
                    "QPushButton { background-color: #0e4a66; border: 1px solid #00ddff; "
                    "color: #ffffff; font-weight: bold; padding: 3px 6px; font-size: 11px; }"
                    "QPushButton:hover { background-color: #146285; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background-color: #252b34; border: 1px solid #455162; "
                    "color: #e6edf3; padding: 3px 6px; font-size: 11px; }"
                    "QPushButton:hover { background-color: #303946; border-color: #00bbee; }"
                )

    # =========================================================================
    # OBSŁUGA WYBORU KAMERY
    # =========================================================================
    def set_camera_list(self, cameras: List[CameraDeviceInfo], selected_id: Optional[str] = None) -> None:
        """Uaktualnia listę kamer w ComboBox z zachowaniem wyboru."""
        self.available_cameras = cameras
        self._block_signals = True
        self.combo_cameras.clear()

        select_idx = 0
        for idx, dev in enumerate(cameras):
            icon_tag = "📷 " if dev.device_type == "opencv" else "🔷 "
            label = f"{icon_tag}{dev.name}"
            self.combo_cameras.addItem(label, dev)
            if selected_id and dev.device_id == selected_id:
                select_idx = idx

        if cameras:
            self.combo_cameras.setCurrentIndex(select_idx)
            current_dev = cameras[select_idx]
            self._update_camera_mode_ui(current_dev)

        self._block_signals = False

    def _update_camera_mode_ui(self, dev: CameraDeviceInfo) -> None:
        """Dostosowuje kontrolki do typu wybranej kamery (Luxonis vs USB)."""
        is_luxonis = (dev.device_type == "luxonis")
        self.iso_container.setEnabled(is_luxonis)
        if is_luxonis:
            self.lbl_camera_desc.setText(f"Przemysłowa OAK-1 (DepthAI, MxID: {dev.mxid or 'Auto'})")
            self.lbl_iso_title.setText("Czułość ISO (IMX378):")
            self.lbl_iso_title.setStyleSheet("color: #e0e0e0;")
        else:
            if sys.platform.startswith("linux"):
                backend_lbl = dev.device_path or f"/dev/video{dev.index}"
                self.lbl_camera_desc.setText(f"{dev.name} (V4L2 / OpenCV: {backend_lbl})")
            else:
                self.lbl_camera_desc.setText(f"{dev.name} (DirectShow / OpenCV #{dev.index})")
            self.lbl_iso_title.setText("Czułość ISO (tylko OAK-1):")
            self.lbl_iso_title.setStyleSheet("color: #6d7681;")

    def _on_camera_combo_changed(self, index: int) -> None:
        if self._block_signals or index < 0:
            return
        dev = self.combo_cameras.itemData(index)
        if isinstance(dev, CameraDeviceInfo):
            self.selected_camera_id = dev.device_id
            self._update_camera_mode_ui(dev)
            self.camera_source_changed.emit(dev)

    def _on_refresh_clicked(self) -> None:
        self.refresh_cameras_requested.emit()

    def get_selected_camera(self) -> Optional[CameraDeviceInfo]:
        return self.combo_cameras.currentData()

    # =========================================================================
    # POZOSTAŁE KONTROLKI
    # =========================================================================
    def _on_reset_roi_clicked(self) -> None:
        self.update_roi_values(0.1, 0.2, 0.9, 0.8)
        self.roi_inputs_changed.emit(0.1, 0.2, 0.9, 0.8)

    def _create_roi_spin(self, val: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.0, 100.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setValue(val)
        spin.setSuffix("%")
        spin.setMinimumWidth(72)
        spin.setMinimumHeight(25)
        spin.valueChanged.connect(self._on_roi_spin_changed)
        return spin

    def update_roi_values(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Aktualizacja pól ROI z zewnętrznego źródła."""
        self._block_signals = True
        self.spin_x1.setValue(x1 * 100.0)
        self.spin_y1.setValue(y1 * 100.0)
        self.spin_x2.setValue(x2 * 100.0)
        self.spin_y2.setValue(y2 * 100.0)
        self._block_signals = False
        self.roi = (x1, y1, x2, y2)

    def _on_roi_spin_changed(self) -> None:
        if self._block_signals:
            return
        x1 = self.spin_x1.value() / 100.0
        y1 = self.spin_y1.value() / 100.0
        x2 = self.spin_x2.value() / 100.0
        y2 = self.spin_y2.value() / 100.0
        self.roi = (x1, y1, x2, y2)
        self.roi_inputs_changed.emit(x1, y1, x2, y2)

    def set_exposure_us(self, val: int) -> None:
        self._block_signals = True
        self.exposure_us = val
        self.spin_exp.setValue(val)
        self.slider_exp.setValue(val)
        self.radio_manual.setChecked(True)
        self.auto_exp = False
        self.slider_exp.setEnabled(True)
        self.spin_exp.setEnabled(True)
        self._block_signals = False
        self._update_preset_highlights()
        self._emit_exposure()

    def _on_slider_exp_changed(self, val: int) -> None:
        if self._block_signals:
            return
        self._block_signals = True
        self.exposure_us = val
        self.spin_exp.setValue(val)
        self._block_signals = False
        self._update_preset_highlights()
        self._emit_exposure()

    def _on_spin_exp_changed(self, val: int) -> None:
        if self._block_signals:
            return
        self._block_signals = True
        self.exposure_us = val
        self.slider_exp.setValue(val)
        self._block_signals = False
        self._update_preset_highlights()
        self._emit_exposure()

    def _on_slider_iso_changed(self, val: int) -> None:
        if self._block_signals:
            return
        self._block_signals = True
        self.iso = val
        self.spin_iso.setValue(val)
        self._block_signals = False
        self._emit_exposure()

    def _on_spin_iso_changed(self, val: int) -> None:
        if self._block_signals:
            return
        self._block_signals = True
        self.iso = val
        self.slider_iso.setValue(val)
        self._block_signals = False
        self._emit_exposure()

    def _on_mode_toggled(self) -> None:
        if self._block_signals:
            return
        self.auto_exp = self.radio_auto.isChecked()
        self.slider_exp.setEnabled(not self.auto_exp)
        self.spin_exp.setEnabled(not self.auto_exp)
        self._update_preset_highlights()
        self._emit_exposure()

    def _on_interval_changed(self) -> None:
        val = self.combo_interval.currentData()
        self.interval_s = float(val)
        self.interval_changed.emit(self.interval_s)

    def _emit_exposure(self) -> None:
        self.exposure_changed.emit(self.exposure_us, self.iso, self.auto_exp)
