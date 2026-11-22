from __future__ import annotations

import logging
import threading
from typing import List, Optional, Tuple
import numpy as np
from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)
from src.barcode_engine import BarcodeEngine, DetectionResult
from src.batch_manager import BatchManager
from src.camera_stream import CameraDeviceInfo, CameraWorker, enumerate_cameras
from src.gui.batch_panel import BatchPanel
from src.gui.camera_controls import CameraControls
from src.gui.system_monitor_dialog import ResourceMonitorWindow
from src.gui.video_widget import VideoWidget
from src.gui.zoom_widget import ZoomWidget
from src.opc_server import OpcUaServerThread
from src.storage_manager import StorageManager
from src.system_monitor import SystemMonitor

logger = logging.getLogger("main_window")


class MainWindow(QMainWindow):
    """
    Główne okno aplikacji do kalibracji i monitorowania kodów kreskowych na liniach produkcyjnych
    z obsługą przemysłowych kamer Luxonis OAK oraz kamer internetowych USB (np. Logitech C270).
    """
    _sig_detection_done = Signal(object, object)          # (DetectionResult, np.ndarray)
    _sig_snapshot_saved = Signal(str, int, int, str, str) # (filename, session_cnt, dir_cnt, time_str, tag)

    def __init__(self, storage_manager: StorageManager):
        super().__init__()
        if QApplication.instance() is not None:
            QApplication.setStyle("Fusion")
        self.storage_manager = storage_manager
        self.config = self.storage_manager.load_config()

        self.setWindowTitle("System Weryfikacji Kodów Kreskowych — Luxonis OAK / USB WebCam")
        target_w, target_h = 1520, 920
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(1540, max(1160, avail.width() - 28))
            target_h = min(940, max(720, avail.height() - 42))
        self._initial_size = (target_w, target_h)
        self.resize(target_w, target_h)
        self._apply_dark_theme()

        # Inicjalizacja modułów logicznych
        initial_roi = tuple(self.config.get("roi", [0.1, 0.2, 0.9, 0.8]))
        self.barcode_engine = BarcodeEngine(roi_normalized=initial_roi)
        self.batch_manager = BatchManager(confirmation_threshold=self.config.get("confirmation_threshold", 5))

        if self.config.get("baseline_code"):
            self.batch_manager.set_baseline(self.config.get("baseline_code"))

        # Wykrywanie dostępnych kamer (Luxonis OAK oraz kamery USB)
        self.available_cameras: List[CameraDeviceInfo] = enumerate_cameras()
        saved_cam_id = self.config.get("camera_device_id", "")
        saved_cam_name = self.config.get("camera_name", "")
        self._current_device_info = self._resolve_initial_camera(saved_cam_id, saved_cam_name)

        # Stan klatek, analizy i zapisu
        self._latest_4k_frame: Optional[np.ndarray] = None
        self._latest_detection_result: Optional[DetectionResult] = None
        self._last_decode_ms: float = 0.0
        self._current_fps: float = 0.0
        self._last_resolution_str: str = "-"
        self._last_opc_status_str: str = "Inicjalizacja..."
        self._is_detecting: bool = False
        self._is_saving_auto: bool = False
        self._manual_zoom_norm: Optional[Tuple[float, float]] = None

        self._auto_save_active: bool = bool(self.config.get("auto_save_on_detection", False))
        self._auto_save_mode: str = str(self.config.get("auto_save_mode", "interval"))

        # Monitor zasobów systemowych (CPU, RAM, wątki)
        self.system_monitor = SystemMonitor(watch_path=self.storage_manager.output_dir)
        self.resource_monitor_window: Optional[ResourceMonitorWindow] = None

        # Utworzenie widżetów GUI
        self._init_ui()

        # Timer cyklicznej detekcji kodów
        self.detect_timer = QTimer(self)
        self.detect_timer.timeout.connect(self._on_detection_tick)

        # Timer lekkiej telemetrii systemowej (CPU / RAM / wątki)
        self.sys_mon_timer = QTimer(self)
        self.sys_mon_timer.setInterval(1500)
        self.sys_mon_timer.timeout.connect(self._on_sys_monitor_tick)

        # Inicjalizacja i uruchomienie wątku aktywnej kamery
        self.camera_worker: Optional[CameraWorker] = None
        self._start_camera_worker(self._current_device_info)
        self._connect_signals()

        # Odświeżenie początkowego stanu UI
        total_in_dir = self.storage_manager.get_total_files_in_dir()
        self.batch_panel.update_capture_status(0, total_in_dir, "-", "brak")
        self.video_widget.set_photo_collection_status(self._auto_save_active, self._auto_save_mode, 0)

        self._apply_detection_interval(self.config.get("detection_interval_s", 1.0))

        # Inicjalizacja i uruchomienie serwera OPC UA (Ignition 8.3)
        self.opc_enabled = bool(self.config.get("opc_ua_enabled", True))
        self.opc_port = int(self.config.get("opc_ua_port", 4840))
        self.opc_server: Optional[OpcUaServerThread] = None

        if self.opc_enabled:
            self.opc_server = OpcUaServerThread(port=self.opc_port, parent=None)
            self.opc_server.status_changed.connect(self._on_opc_status_changed)
            self.opc_server.reset_baseline_requested.connect(self._on_reset_baseline)
            self.opc_server.set_baseline_requested.connect(self._on_opc_set_baseline)
            self.opc_server.clear_alarm_requested.connect(self._on_clear_alarm)
            self.opc_server.start()
            self.opc_server.update_camera(self._current_device_info.name)
            self._sync_opc_batch_state()
        else:
            self._last_opc_status_str = "Wyłączony w konfiguracji"
            self.batch_panel.update_opc_status(False, "Wyłączony w konfiguracji")

        self.sys_mon_timer.start()
        self._on_sys_monitor_tick()

    def _resolve_initial_camera(self, saved_id: str, saved_name: str = "") -> CameraDeviceInfo:
        """Dobiera początkową kamerę na podstawie konfiguracji lub dostępności urządzeń."""
        if not self.available_cameras:
            return CameraDeviceInfo("luxonis", "luxonis:auto", "Luxonis OAK-1", is_available=False)

        clean_saved = saved_name.split("[")[0].strip().lower() if saved_name else ""

        # 1. Dokładne dopasowanie ID + nazwy z config.json
        if saved_id:
            for dev in self.available_cameras:
                if dev.device_id == saved_id and (not clean_saved or clean_saved in dev.name.lower()):
                    return dev

        # 1b. Jeśli indeks portu USB uległ zmianie (np. po przepięciu lub przeniesieniu na Orange Pi), dopasuj po nazwie
        if clean_saved:
            for dev in self.available_cameras:
                if clean_saved in dev.name.lower():
                    return dev

        # 2. Jeśli podłączony jest fizycznie Luxonis, preferuj Luxonis
        for dev in self.available_cameras:
            if dev.device_type == "luxonis" and dev.is_available:
                return dev

        # 3. Jeśli podłączona jest kamera zewnętrzna USB (np. Logitech C922 Pro, C920, C270), wybierz ją
        for dev in self.available_cameras:
            if dev.device_type == "opencv":
                name_l = dev.name.lower()
                if any(k in name_l for k in ["c922", "c920", "c270", "c310", "brio", "logi", "stream webcam"]) or dev.index == 1:
                    return dev

        # 4. Dowolna podłączona kamera USB
        for dev in self.available_cameras:
            if dev.device_type == "opencv" and dev.is_available:
                return dev

        # 5. Jeśli zapisano ID bez nazwy lub kamera nie ma zgodnej nazwy, sprawdź samo saved_id
        if saved_id:
            for dev in self.available_cameras:
                if dev.device_id == saved_id:
                    return dev

        # 6. Pierwsze urządzenie z listy
        return self.available_cameras[0]

    def _start_camera_worker(self, device_info: CameraDeviceInfo) -> None:
        """Czyste zatrzymanie starego i uruchomienie nowego wątku kamery."""
        if self.camera_worker is not None:
            try:
                self.camera_worker.frame_ready.disconnect(self._on_frame_ready)
                self.camera_worker.status_changed.disconnect(self._on_camera_status_changed)
                self.camera_worker.fps_updated.disconnect(self._on_fps_updated)
                self.camera_worker.error_occurred.disconnect(self._on_camera_error)
            except Exception:
                pass
            self.camera_worker.stop()
            self.camera_worker.deleteLater()
            self.camera_worker = None

        self._current_device_info = device_info
        self._latest_4k_frame = None

        initial_exp = self.config.get("exposure_us", 20000)
        initial_iso = self.config.get("iso", 100)
        initial_auto = self.config.get("auto_exposure", True)

        self.camera_worker = CameraWorker(
            device_info=device_info,
            exposure_us=initial_exp,
            iso=initial_iso,
            auto_exposure=initial_auto,
            parent=None
        )
        self.camera_worker.frame_ready.connect(self._on_frame_ready)
        self.camera_worker.status_changed.connect(self._on_camera_status_changed)
        self.camera_worker.fps_updated.connect(self._on_fps_updated)
        self.camera_worker.error_occurred.connect(self._on_camera_error)
        self.camera_worker.start()

    def _apply_dark_theme(self) -> None:
        """Aplikuje profesjonalny ciemny motyw przemysłowy o wysokim kontraście."""
        app = QApplication.instance()
        if app is not None:
            pal = QPalette()
            pal.setColor(QPalette.ColorRole.Window, QColor("#141414"))
            pal.setColor(QPalette.ColorRole.WindowText, QColor("#e6edf3"))
            pal.setColor(QPalette.ColorRole.Base, QColor("#1c2026"))
            pal.setColor(QPalette.ColorRole.AlternateBase, QColor("#222730"))
            pal.setColor(QPalette.ColorRole.ToolTipBase, QColor("#1f242c"))
            pal.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6edf3"))
            pal.setColor(QPalette.ColorRole.Text, QColor("#e6edf3"))
            pal.setColor(QPalette.ColorRole.Button, QColor("#252b34"))
            pal.setColor(QPalette.ColorRole.ButtonText, QColor("#e6edf3"))
            pal.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorRole.Highlight, QColor("#0088cc"))
            pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
            pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#6d7681"))
            pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#6d7681"))
            app.setPalette(pal)

        self.setStyleSheet("""
            QMainWindow {
                background-color: #141414;
                color: #e6edf3;
            }
            QWidget {
                color: #e6edf3;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QGroupBox {
                border: 1px solid #353d49;
                border-radius: 6px;
                margin-top: 8px;
                font-weight: bold;
                font-size: 12px;
                padding-top: 7px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #00ddff;
            }
            QPushButton {
                background-color: #252b34;
                border: 1px solid #455162;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 24px;
                color: #f0f4f8;
            }
            QPushButton:hover {
                background-color: #303946;
                border-color: #00bbee;
            }
            QPushButton:pressed {
                background-color: #181d24;
            }
            QPushButton:disabled {
                background-color: #1a1d22;
                border-color: #2b3038;
                color: #666e7a;
            }
            QRadioButton {
                spacing: 7px;
                color: #e8eef4;
                font-size: 12px;
                padding: 2px 0px;
            }
            QRadioButton::indicator {
                width: 15px;
                height: 15px;
                border-radius: 9px;
                border: 2px solid #8899aa;
                background-color: #1b2028;
            }
            QRadioButton::indicator:hover {
                border-color: #00ddff;
            }
            QRadioButton::indicator:checked {
                border: 2px solid #00ddff;
                background-color: qradialgradient(
                    cx: 0.5, cy: 0.5, radius: 0.5, fx: 0.5, fy: 0.5,
                    stop: 0 #00ddff, stop: 0.48 #00ddff,
                    stop: 0.56 #1b2028, stop: 1 #1b2028
                );
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #2b313a;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #00aaee;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal:disabled {
                background: #3b424c;
            }
            QSlider::handle:horizontal {
                background: #ffffff;
                border: 1px solid #555555;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:disabled {
                background: #6b7280;
                border-color: #374151;
            }
            QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #1f242c;
                border: 1px solid #455162;
                border-radius: 4px;
                padding: 2px 6px;
                min-height: 24px;
                color: #ffffff;
            }
            QSpinBox::up-button, QDoubleSpinBox::up-button {
                subcontrol-origin: border;
                subcontrol-position: top right;
                width: 18px;
                border-left: 1px solid #3c4654;
                border-bottom: 1px solid #3c4654;
                background-color: #2a323d;
                border-top-right-radius: 3px;
            }
            QSpinBox::down-button, QDoubleSpinBox::down-button {
                subcontrol-origin: border;
                subcontrol-position: bottom right;
                width: 18px;
                border-left: 1px solid #3c4654;
                background-color: #2a323d;
                border-bottom-right-radius: 3px;
            }
            QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
            QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {
                background-color: #364556;
            }
            QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-bottom: 5px solid #e6edf3;
            }
            QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
                border-left: 4px solid transparent;
                border-right: 4px solid transparent;
                border-top: 5px solid #e6edf3;
            }
            QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
                border-bottom-color: #5a6370;
            }
            QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
                border-top-color: #5a6370;
            }
            QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {
                border-color: #00aadd;
            }
            QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {
                background-color: #171a20;
                border-color: #2c323c;
                color: #6d7681;
            }
            QComboBox QAbstractItemView {
                background-color: #1f242c;
                color: #f0f4f8;
                border: 1px solid #0088aa;
                selection-background-color: #0e4a66;
                selection-color: #ffffff;
            }
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #16191e;
                width: 10px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: #38414e;
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: #0099cc;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QSplitter::handle {
                background-color: #252b34;
            }
            QSplitter::handle:hover {
                background-color: #00aadd;
            }
            QStatusBar {
                background-color: #0d0d0d;
                color: #cccccc;
                border-top: 1px solid #222222;
                font-weight: bold;
            }
        """)

    def _init_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # Lewy / Centralny kontener: Podgląd wideo + (Zoom + Sterowanie kamerą) w pionowym QSplitter
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.setChildrenCollapsible(False)
        left_splitter.setHandleWidth(5)

        # Główny widżet wideo
        self.video_widget = VideoWidget()
        self.video_widget.setMinimumSize(480, 250)
        rx1, ry1, rx2, ry2 = self.barcode_engine.roi_normalized
        self.video_widget.set_roi(rx1, ry1, rx2, ry2)
        left_splitter.addWidget(self.video_widget)

        # Dolny pasek lewej sekcji: Zoom 1:1 + Wybór i parametry kamery
        bottom_widget = QWidget()
        bottom_box = QHBoxLayout(bottom_widget)
        bottom_box.setContentsMargins(0, 4, 0, 0)
        bottom_box.setSpacing(8)

        self.zoom_widget = ZoomWidget()
        bottom_box.addWidget(self.zoom_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        self.camera_controls = CameraControls(
            available_cameras=self.available_cameras,
            selected_camera_id=self._current_device_info.device_id,
            initial_exp_us=self.config.get("exposure_us", 20000),
            initial_iso=self.config.get("iso", 100),
            initial_auto_exp=self.config.get("auto_exposure", True),
            initial_interval_s=self.config.get("detection_interval_s", 1.0),
            initial_roi=self.barcode_engine.roi_normalized
        )
        bottom_box.addWidget(self.camera_controls, stretch=1)
        left_splitter.addWidget(bottom_widget)

        target_w, target_h = self._initial_size
        bottom_h = min(400, max(370, int(target_h * 0.46)))
        video_h = max(250, target_h - bottom_h - 60)
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)
        left_splitter.setSizes([video_h, bottom_h])

        left_layout.addWidget(left_splitter)

        # Prawy panel: Partia, Live Log i Zrzuty
        self.batch_panel = BatchPanel(self.batch_manager)
        self.batch_panel.btn_autosave.setChecked(self._auto_save_active)
        self.batch_panel.set_autosave_mode(self._auto_save_mode)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(5)
        splitter.addWidget(left_container)
        splitter.addWidget(self.batch_panel)
        splitter.setStretchFactor(0, 7)
        splitter.setStretchFactor(1, 3)
        right_w = max(390, min(470, int(target_w * 0.30)))
        splitter.setSizes([max(760, target_w - right_w - 24), right_w])

        main_layout.addWidget(splitter)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(f"Inicjalizacja kamery: {self._current_device_info.name}...")

    def _connect_signals(self) -> None:
        # Sygnały wewnętrzne asynchronicznych zadań
        self._sig_detection_done.connect(self._on_detection_done)
        self._sig_snapshot_saved.connect(self._on_snapshot_saved_ui)

        # Sygnały z podglądu wideo (zmiana ROI myszką oraz punktu Zoom 1:1)
        self.video_widget.roi_changed.connect(self._on_roi_changed_from_video)
        self.video_widget.zoom_point_changed.connect(self._on_zoom_point_changed)

        # Sygnały z panelu wyboru i sterowania kamerą
        self.camera_controls.camera_source_changed.connect(self._on_camera_source_changed)
        self.camera_controls.refresh_cameras_requested.connect(self._on_refresh_cameras)
        self.camera_controls.exposure_changed.connect(self._on_exposure_changed)
        self.camera_controls.interval_changed.connect(self._apply_detection_interval)
        self.camera_controls.trigger_detection_requested.connect(self._on_detection_tick)
        self.camera_controls.roi_inputs_changed.connect(self._on_roi_changed_from_inputs)

        # Sygnały z panelu partii i zapisu
        self.batch_panel.reset_baseline_requested.connect(self._on_reset_baseline)
        self.batch_panel.accept_baseline_requested.connect(self._on_accept_baseline)
        self.batch_panel.clear_alarm_requested.connect(self._on_clear_alarm)
        self.batch_panel.clear_log_requested.connect(self._on_clear_log)
        self.batch_panel.open_system_monitor_requested.connect(self._on_open_system_monitor)
        self.batch_panel.snapshot_requested.connect(self._on_manual_snapshot)
        self.batch_panel.auto_save_toggled.connect(self._on_auto_save_toggled)
        self.batch_panel.auto_save_mode_changed.connect(self._on_auto_save_mode_changed)
        self.batch_panel.threshold_changed.connect(self._on_threshold_changed)

    # =========================================================================
    # OBSŁUGA ZMIANY I ODŚWIEŻANIA KAMERY
    # =========================================================================
    @Slot(object)
    def _on_camera_source_changed(self, device_info: CameraDeviceInfo) -> None:
        """Przełączenie aktywnego źródła wideo na inne urządzenie."""
        if self._current_device_info and self._current_device_info.device_id == device_info.device_id:
            return
        logger.info("Żądanie przełączenia kamery na: %s (%s)", device_info.name, device_info.device_id)
        self.status_bar.showMessage(f"Przełączanie na kamerę: {device_info.name}...", 3000)
        self._start_camera_worker(device_info)
        if self.opc_server:
            self.opc_server.update_camera(device_info.name)
        self._save_current_config()

    @Slot()
    def _on_refresh_cameras(self) -> None:
        """Ponowne wykrywanie podłączonych urządzeń wideo."""
        logger.info("Odświeżanie listy podłączonych kamer...")
        self.available_cameras = enumerate_cameras()
        cur_id = self._current_device_info.device_id if self._current_device_info else None
        self.camera_controls.set_camera_list(self.available_cameras, selected_id=cur_id)
        self.status_bar.showMessage(f"Wykryto {len(self.available_cameras)} dostępnych urządzeń wideo.", 3000)

    # =========================================================================
    # OBSŁUGA SERWERA OPC UA (IGNITION 8.3)
    # =========================================================================
    @Slot(bool, str)
    def _on_opc_status_changed(self, running: bool, endpoint_or_err: str) -> None:
        self._last_opc_status_str = f"Aktywny ({endpoint_or_err})" if running else endpoint_or_err
        self.batch_panel.update_opc_status(running, endpoint_or_err)
        if running:
            self.status_bar.showMessage(f"Serwer OPC UA aktywny dla Ignition ({endpoint_or_err})", 4000)
            self._sync_opc_batch_state()
        else:
            self.status_bar.showMessage(f"Serwer OPC UA: {endpoint_or_err}", 3000)

    @Slot(str)
    def _on_opc_set_baseline(self, new_code: str) -> None:
        """Odebranie polecenia ustawienia nowego kodu wzorcowego z Ignition."""
        logger.info("Odebrano nowy kod wzorcowy z Ignition: %s", new_code)
        self.batch_manager.set_baseline(new_code)
        self.batch_panel.update_batch_display()
        self._sync_opc_batch_state()
        self.status_bar.showMessage(f"Ignition ustawił nowy kod partii: {new_code}", 4000)

    def _sync_opc_batch_state(self) -> None:
        """Synchronizacja aktualnego stanu partii do OPC UA."""
        if self.opc_server:
            st = self.batch_manager.stats
            self.opc_server.update_batch_state(
                baseline_code=self.batch_manager.baseline_code,
                alarm=self.batch_manager.batch_change_alarm,
                mismatch_streak=self.batch_manager.mismatch_consecutive_count,
                total_scans=st.total_frames_analyzed,
                matched=st.matched_codes_count,
                mismatched=st.mismatched_codes_count
            )

    # =========================================================================
    # PRZECHWYTYWANIE I WIDOK ZOOM 1:1
    # =========================================================================
    def _determine_zoom_focus_px(self, frame_w: int, frame_h: int) -> Tuple[Optional[Tuple[int, int]], str]:
        """Wyznacza punkt centralny dla podglądu Zoom 1:1."""
        if self._manual_zoom_norm is not None:
            nx, ny = self._manual_zoom_norm
            return (int(nx * frame_w), int(ny * frame_h)), "Cel ręczny"

        if self._latest_detection_result is not None:
            if self._latest_detection_result.barcodes:
                b0 = self._latest_detection_result.barcodes[0]
                x1, y1, x2, y2 = b0.rect_full
                return ((x1 + x2) // 2, (y1 + y2) // 2), "Wykryty kod"
            if self._latest_detection_result.candidates:
                c0 = self._latest_detection_result.candidates[0]
                x1, y1, x2, y2 = c0.rect_full
                return ((x1 + x2) // 2, (y1 + y2) // 2), "Kandydat kodu"

        return None, "Środek ROI"

    @Slot(object, object)
    def _on_frame_ready(self, full_frame: np.ndarray, preview_frame: np.ndarray) -> None:
        """Odbiór nowej klatki z kamery (wywoływany w głównym wątku GUI)."""
        self._latest_4k_frame = full_frame
        h, w = full_frame.shape[:2]
        self._last_resolution_str = f"{w}×{h}"
        self.video_widget.set_full_resolution(w, h)

        focus_px, target_lbl = self._determine_zoom_focus_px(w, h)
        zoom_crop, zoom_bounds = self.barcode_engine.get_zoom_crop_with_bounds(
            full_frame, zoom_w=360, zoom_h=240, focus_px=focus_px
        )
        self.zoom_widget.update_zoom_frame(zoom_crop, target_label=target_lbl)
        self.video_widget.set_zoom_bounds_4k(zoom_bounds, pinned=(self._manual_zoom_norm is not None))

        # Aktualizacja głównego podglądu
        self.video_widget.update_frame(preview_frame, self._latest_detection_result)

    @Slot(object)
    def _on_zoom_point_changed(self, norm_pt: Optional[Tuple[float, float]]) -> None:
        self._manual_zoom_norm = norm_pt
        if norm_pt is None:
            self.status_bar.showMessage("Zoom 1:1: Przywrócono automatyczne śledzenie (kod / kandydat / środek ROI).", 3000)
        else:
            self.status_bar.showMessage(
                f"Zoom 1:1: Ustawiono ręczny punkt obserwacji ({norm_pt[0]*100:.1f}%, {norm_pt[1]*100:.1f}%). Podwójny PPM resetuje.",
                4000
            )

    @Slot(bool, str, str, str)
    def _on_camera_status_changed(self, connected: bool, name: str, id_or_mxid: str, extra_info: str) -> None:
        self.batch_panel.update_hardware_status(connected, name, id_or_mxid, extra_info)
        if connected:
            self.status_bar.showMessage(f"Aktywna kamera: {name} ({extra_info}, ID: {id_or_mxid})")
        else:
            self.status_bar.showMessage(f"Brak sygnału z kamery: {name} ({extra_info})")

    @Slot(float)
    def _on_fps_updated(self, fps: float) -> None:
        self._current_fps = fps
        self.batch_panel.update_performance(fps, self._last_decode_ms)

    @Slot(str)
    def _on_camera_error(self, err: str) -> None:
        self.status_bar.showMessage(f"Błąd kamery: {err}", 5000)

    @Slot(float, float, float, float)
    def _on_roi_changed_from_video(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.barcode_engine.set_roi(x1, y1, x2, y2)
        self.camera_controls.update_roi_values(x1, y1, x2, y2)
        self.status_bar.showMessage(
            f"Zaktualizowano ROI: [{x1*100:.1f}%, {y1*100:.1f}%] - [{x2*100:.1f}%, {y2*100:.1f}%]", 3000
        )

    @Slot(float, float, float, float)
    def _on_roi_changed_from_inputs(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.barcode_engine.set_roi(x1, y1, x2, y2)
        self.video_widget.set_roi(x1, y1, x2, y2)

    @Slot(int, int, bool)
    def _on_exposure_changed(self, exposure_us: int, iso: int, auto_exp: bool) -> None:
        if self.camera_worker:
            self.camera_worker.update_exposure(exposure_us, iso, auto_exp)
        if auto_exp:
            self.status_bar.showMessage("Przełączono kamerę na Auto Exposure (zalecane do pracy biurkowej)", 3000)
        else:
            self.status_bar.showMessage(f"Ustawiono ekspozycję manualną: {exposure_us} µs, ISO {iso}", 3000)

    @Slot(float)
    def _apply_detection_interval(self, interval_s: float) -> None:
        self.video_widget.set_scanner_schedule_info(interval_s)
        if interval_s <= 0.0:
            self.detect_timer.stop()
            self.batch_panel.update_scanner_live_status("⏸ Skanowanie automatyczne wyłączone (tryb ręczny)", "idle")
            self.status_bar.showMessage("Automatyczna detekcja wyłączona (tryb ręczny)", 3000)
        else:
            interval_ms = int(interval_s * 1000)
            self.detect_timer.start(interval_ms)
            self.status_bar.showMessage(f"Interwał detekcji: co {interval_s:.1f} s", 3000)

    @Slot()
    def _on_detection_tick(self) -> None:
        """Cykliczne lub ręczne uruchomienie analizy klatki w osobnym wątku roboczym."""
        if self._latest_4k_frame is None or self._is_detecting:
            return

        self._is_detecting = True
        frame_copy = self._latest_4k_frame.copy()

        self.video_widget.notify_scan_started()
        self.batch_panel.update_scanner_live_status("⏳ Analiza klatki (dekonwolucja Wienera + zxing)...", "scanning")

        thread = threading.Thread(
            target=self._worker_detect_and_verify,
            args=(frame_copy,),
            daemon=True,
            name="BarcodeWorker"
        )
        thread.start()

    def _worker_detect_and_verify(self, frame: np.ndarray) -> None:
        """Wątek roboczy detekcji kodów kreskowych."""
        try:
            result = self.barcode_engine.detect(frame)
            self._sig_detection_done.emit(result, frame)
        except Exception as e:
            logger.error("Błąd podczas detekcji kodów: %s", e)
            self._sig_detection_done.emit(DetectionResult(), frame)

    @Slot(object, object)
    def _on_detection_done(self, result: DetectionResult, analyzed_frame: np.ndarray) -> None:
        """Odbiór wyników detekcji w wątku głównym GUI."""
        self._is_detecting = False
        self._latest_detection_result = result
        self._last_decode_ms = result.duration_ms

        alarm = self.batch_manager.process_detection(result)
        self.video_widget.notify_scan_finished(result)
        detected_codes = [b.text for b in result.barcodes]
        is_match = any(b.is_match for b in result.barcodes)

        if detected_codes:
            first_code = result.barcodes[0]
            fmt_str = first_code.format
            txt_str = first_code.text

            if self.batch_manager.batch_change_alarm:
                scan_msg = f"🚨 ALARM! Wykryto niezgodny kod: {txt_str} [{fmt_str}]"
                state_str = "candidate"
            elif is_match:
                scan_msg = f"✓ Zgodny z partią: {txt_str} [{fmt_str}] ({result.duration_ms:.1f} ms)"
                state_str = "found"
            else:
                scan_msg = f"Wykryto kod: {txt_str} [{fmt_str}] ({result.duration_ms:.1f} ms)"
                state_str = "found"
        else:
            if result.candidates:
                c0 = result.candidates[0]
                hints = []
                if c0.brightness < 55:
                    hints.append(f"za ciemno: {c0.brightness:.0f}")
                if c0.sharpness < 60:
                    hints.append(f"ostrość: {c0.sharpness:.0f}")
                hint_str = f" ({', '.join(hints)})" if hints else ""
                scan_msg = f"🔍 Widoczna struktura paskowa, brak odczytu cyfr{hint_str}"
                state_str = "candidate"
            else:
                scan_msg = f"Brak kodu w ROI ({result.duration_ms:.1f} ms)"
                state_str = "idle"

        self.batch_panel.update_batch_display()
        self.batch_panel.update_performance(self._current_fps, self._last_decode_ms)
        self.batch_panel.update_scanner_live_status(scan_msg, state_str)

        # Publikacja wyników detekcji do serwera OPC UA (Ignition 8.3)
        if self.opc_server:
            first_text = detected_codes[0] if detected_codes else ""
            first_fmt = result.barcodes[0].format if result.barcodes else ""
            self.opc_server.publish_detection(
                code=first_text,
                format_str=first_fmt,
                is_match=is_match,
                duration_ms=result.duration_ms
            )
            self._sync_opc_batch_state()

        # Obsługa Trybu Zbierania Zdjęć (Captures)
        should_save = False
        save_tag = "auto"

        if self._auto_save_active:
            if self._auto_save_mode == "interval":
                should_save = True
                save_tag = "interval"
            elif self._auto_save_mode == "detection_only" and detected_codes:
                should_save = True
                save_tag = "detected"

        if should_save:
            roi_crop = result.roi_crop if result.roi_crop is not None else analyzed_frame
            self._save_snapshot_async(analyzed_frame, roi_crop, tag=save_tag)

    def _save_snapshot_async(self, full_frame: np.ndarray, roi_crop: np.ndarray, tag: str) -> None:
        """Asynchroniczny zapis klatki i ROI do folderu captures/."""
        is_auto = tag in ("interval", "detected")
        if is_auto and self._is_saving_auto:
            return

        if is_auto:
            self._is_saving_auto = True

        frame_copy = full_frame.copy()
        crop_copy = roi_crop.copy()
        exp_us = int(self.camera_worker.exposure_us) if self.camera_worker else int(self.config.get("exposure_us", 20000))
        iso = int(self.camera_worker.iso) if self.camera_worker else int(self.config.get("iso", 100))

        def _worker():
            try:
                full_path, _ = self.storage_manager.save_snapshot(
                    frame_copy,
                    crop_copy,
                    exposure_us=exp_us,
                    iso=iso,
                    tag=tag
                )
                dir_cnt = self.storage_manager.get_total_files_in_dir()
                self._sig_snapshot_saved.emit(
                    full_path.name,
                    self.storage_manager.session_saved_count,
                    dir_cnt,
                    self.storage_manager.last_saved_time,
                    tag
                )
            except Exception as e:
                logger.exception("Błąd podczas zapisu snapshotu: %s", e)
            finally:
                if is_auto:
                    self._is_saving_auto = False

        threading.Thread(target=_worker, daemon=True, name="SnapshotSaveWorker").start()

    @Slot(str, int, int, str, str)
    def _on_snapshot_saved_ui(self, filename: str, sess_cnt: int, dir_cnt: int, time_str: str, tag: str) -> None:
        self.batch_panel.update_capture_status(sess_cnt, dir_cnt, time_str, filename)
        self.video_widget.notify_snapshot_saved(filename, sess_cnt, tag)

        mode_str = "Ręczny" if tag == "manual" else ("Cykliczny" if tag == "interval" else "Detekcja")
        self.status_bar.showMessage(f"💾 [{mode_str}] Zapisano zdjęcie #{sess_cnt}: {filename}", 4000)

    @Slot()
    def _on_reset_baseline(self) -> None:
        self.batch_manager.reset_baseline()
        self.batch_panel.update_batch_display()
        self._sync_opc_batch_state()
        self.status_bar.showMessage("Zresetowano wzorzec partii. Auto-Lock oczekuje na kolejny odczytany kod.", 4000)

    @Slot()
    def _on_accept_baseline(self) -> None:
        self.batch_manager.accept_candidate_as_new_baseline()
        self.batch_panel.update_batch_display()
        self._sync_opc_batch_state()
        self.status_bar.showMessage(f"Ustawiono nowy kod wzorcowy partii: {self.batch_manager.baseline_code}", 4000)

    @Slot()
    def _on_clear_alarm(self) -> None:
        self.batch_manager.clear_alarm()
        self.batch_panel.update_batch_display()
        self._sync_opc_batch_state()
        self.status_bar.showMessage("Wyciszono alarm zmiany litografii.", 3000)

    @Slot()
    def _on_clear_log(self) -> None:
        """Natychmiastowe wyczyszczenie tabeli historii odczytów (Live Log)."""
        self.batch_manager.clear_history()
        self.batch_panel.update_batch_display()
        self.status_bar.showMessage("Wyczyszczono historię Live Log.", 3000)

    @Slot()
    def _on_open_system_monitor(self) -> None:
        """Otwiera lub przywołuje okno monitora zasobów systemowych (CPU, RAM, wątki)."""
        if self.resource_monitor_window is None:
            self.resource_monitor_window = ResourceMonitorWindow(parent=None)
            self.resource_monitor_window.refresh_requested.connect(self._refresh_resource_monitor)
        self.resource_monitor_window.show()
        self.resource_monitor_window.raise_()
        self.resource_monitor_window.activateWindow()
        self._refresh_resource_monitor()

    def _collect_app_telemetry(self) -> dict:
        return {
            "camera_name": self._current_device_info.name if self._current_device_info else "-",
            "fps": self._current_fps,
            "resolution": self._last_resolution_str,
            "decode_ms": self._last_decode_ms,
            "total_scans": self.batch_manager.stats.total_frames_analyzed,
            "interval_s": self.camera_controls.interval_s,
            "opc_status": self._last_opc_status_str,
            "captures_count": self.storage_manager.get_total_files_in_dir(),
        }

    @Slot()
    def _refresh_resource_monitor(self) -> None:
        snap = self.system_monitor.collect(
            extra_workers=[
                ("CameraWorker", self.camera_worker),
                ("OpcUaServerThread", self.opc_server),
            ]
        )
        self.batch_panel.update_system_summary(
            snap.cpu_system_percent,
            snap.process_ram_mb,
            snap.os_thread_count,
        )
        if self.resource_monitor_window is not None and self.resource_monitor_window.isVisible():
            self.resource_monitor_window.update_metrics(snap, app_info=self._collect_app_telemetry())

    @Slot()
    def _on_sys_monitor_tick(self) -> None:
        self._refresh_resource_monitor()

    @Slot()
    def _on_manual_snapshot(self) -> None:
        """Natychmiastowe ręczne zapisanie zdjęcia i ROI na dysk."""
        if self._latest_4k_frame is None:
            QMessageBox.warning(self, "Brak klatki", "Kamera nie dostarczyła jeszcze żadnej klatki do zapisu.")
            return

        roi_crop, _ = self.barcode_engine.crop_roi(self._latest_4k_frame)
        self._save_snapshot_async(self._latest_4k_frame, roi_crop, tag="manual")

    @Slot(bool)
    def _on_auto_save_toggled(self, active: bool) -> None:
        self._auto_save_active = active
        sess_cnt = self.storage_manager.session_saved_count
        self.video_widget.set_photo_collection_status(self._auto_save_active, self._auto_save_mode, sess_cnt)
        if active:
            mode_desc = "każda skanowana klatka" if self._auto_save_mode == "interval" else "tylko przy wykryciu kodu"
            self.status_bar.showMessage(f"🔴 WŁĄCZONO Tryb Zbierania Zdjęć ({mode_desc}).", 4000)
            if self._latest_4k_frame is not None:
                self._on_detection_tick()
        else:
            self.status_bar.showMessage("⚪ WYŁĄCZONO Tryb Zbierania Zdjęć.", 3000)

    @Slot(str)
    def _on_auto_save_mode_changed(self, mode: str) -> None:
        self._auto_save_mode = mode
        sess_cnt = self.storage_manager.session_saved_count
        self.video_widget.set_photo_collection_status(self._auto_save_active, self._auto_save_mode, sess_cnt)
        mode_desc = "każda skanowana klatka (co interwał)" if mode == "interval" else "tylko gdy wykryto kod"
        self.status_bar.showMessage(f"Zmieniono warunek auto-zapisu: {mode_desc}", 3000)

    @Slot(int)
    def _on_threshold_changed(self, threshold: int) -> None:
        self.batch_manager.confirmation_threshold = threshold
        self.status_bar.showMessage(f"Próg potwierdzeń zmiany litografii: {threshold}", 3000)

    def _save_current_config(self) -> None:
        """Zapisuje bieżącą konfigurację roboczą do pliku config.json."""
        exp_us = self.camera_worker.exposure_us if self.camera_worker else 20000
        iso = self.camera_worker.iso if self.camera_worker else 100
        auto_exp = self.camera_worker.auto_exposure if self.camera_worker else True

        cfg_to_save = {
            "camera_device_id": self._current_device_info.device_id if self._current_device_info else "",
            "camera_type": self._current_device_info.device_type if self._current_device_info else "",
            "camera_name": self._current_device_info.name if self._current_device_info else "",
            "roi": list(self.barcode_engine.roi_normalized),
            "exposure_us": exp_us,
            "iso": iso,
            "auto_exposure": auto_exp,
            "detection_interval_s": self.camera_controls.interval_s,
            "confirmation_threshold": self.batch_manager.confirmation_threshold,
            "auto_save_on_detection": self._auto_save_active,
            "auto_save_mode": self._auto_save_mode,
            "baseline_code": self.batch_manager.baseline_code,
            "opc_ua_enabled": self.opc_enabled,
            "opc_ua_port": self.opc_port
        }
        self.storage_manager.save_config(cfg_to_save)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Zapis konfiguracji i czyste zatrzymanie wątków przed zamknięciem aplikacji."""
        logger.info("Zamykanie aplikacji — zapisywanie konfiguracji...")
        self._save_current_config()
        self.detect_timer.stop()
        self.sys_mon_timer.stop()
        if self.resource_monitor_window is not None:
            self.resource_monitor_window.close()
        if self.camera_worker:
            self.camera_worker.stop()
        if self.opc_server:
            self.opc_server.stop()
        event.accept()
