from __future__ import annotations

from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from src.batch_manager import BatchManager


class BatchPanel(QWidget):
    """
    Panel boczny zarządzania partią produkcyjną, tabelą odczytów w czasie rzeczywistym
    oraz narzędziami do zapisu zrzutów Snapshot i Trybu Zbierania Zdjęć.
    """
    reset_baseline_requested = Signal()
    accept_baseline_requested = Signal()
    clear_alarm_requested = Signal()
    snapshot_requested = Signal()
    auto_save_toggled = Signal(bool)
    auto_save_mode_changed = Signal(str)  # 'interval' lub 'detection_only'
    threshold_changed = Signal(int)

    def __init__(self, batch_manager: BatchManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.batch_manager = batch_manager
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        # ==========================================
        # 1. KARTA STATUSU SPRZĘTU I SKANERA
        # ==========================================
        hw_group = QGroupBox("Status Aktywnej Kamery i Skanera")
        hw_layout = QVBoxLayout(hw_group)
        hw_layout.setSpacing(4)

        self.lbl_hw_status = QLabel("● Połączenie: Oczekiwanie...")
        self.lbl_hw_status.setStyleSheet("font-weight: bold; color: #ffaa00;")
        self.lbl_hw_details = QLabel("Model: - | Szczegóły: - | ID: -")
        self.lbl_hw_details.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        self.lbl_perf = QLabel("Strumień: 0.0 FPS | Analiza: 0.0 ms")
        self.lbl_perf.setStyleSheet("color: #00ddff; font-weight: bold; font-size: 11px;")
        self.lbl_opc_status = QLabel("● OPC UA (Ignition): Inicjalizacja...")
        self.lbl_opc_status.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold;")
        self.lbl_scanner_live = QLabel("🔍 Skaner: Oczekiwanie na pierwszą klatkę...")
        self.lbl_scanner_live.setStyleSheet(
            "background-color: #1c2430; color: #00ffcc; font-weight: bold; font-size: 11px; "
            "padding: 4px 6px; border-radius: 4px; border: 1px solid #0088aa;"
        )

        hw_layout.addWidget(self.lbl_hw_status)
        hw_layout.addWidget(self.lbl_hw_details)
        hw_layout.addWidget(self.lbl_perf)
        hw_layout.addWidget(self.lbl_opc_status)
        hw_layout.addWidget(self.lbl_scanner_live)
        layout.addWidget(hw_group)

        # ==========================================
        # 2. BANNER ALARMOWY ZMIANY LITOGRAFII
        # ==========================================
        self.alarm_box = QGroupBox("ALARM DETEKCJI")
        self.alarm_box.setStyleSheet("""
            QGroupBox {
                background-color: #770000;
                border: 2px solid #ff2222;
                border-radius: 6px;
                color: #ffffff;
                font-weight: bold;
            }
        """)
        alarm_layout = QVBoxLayout(self.alarm_box)
        self.lbl_alarm_title = QLabel("🚨 ZMIANA LITOGRAFII! 🚨")
        self.lbl_alarm_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_alarm_title.setStyleSheet("font-size: 16px; font-weight: bold; color: #ffffff;")
        self.lbl_alarm_desc = QLabel("Wykryto nową serię innego kodu na linii!")
        self.lbl_alarm_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_alarm_desc.setStyleSheet("font-size: 12px; color: #ffdddd;")

        btn_alarm_layout = QHBoxLayout()
        self.btn_accept_new = QPushButton("✓ Ustaw jako nowy wzorzec")
        self.btn_accept_new.setStyleSheet("background-color: #00aa44; color: white; font-weight: bold; padding: 4px;")
        self.btn_accept_new.clicked.connect(self.accept_baseline_requested.emit)

        self.btn_dismiss = QPushButton("✕ Wycisz alarm")
        self.btn_dismiss.setStyleSheet("background-color: #444444; color: white; padding: 4px;")
        self.btn_dismiss.clicked.connect(self.clear_alarm_requested.emit)

        btn_alarm_layout.addWidget(self.btn_accept_new)
        btn_alarm_layout.addWidget(self.btn_dismiss)

        alarm_layout.addWidget(self.lbl_alarm_title)
        alarm_layout.addWidget(self.lbl_alarm_desc)
        alarm_layout.addLayout(btn_alarm_layout)
        self.alarm_box.setVisible(False)
        layout.addWidget(self.alarm_box)

        # ==========================================
        # 3. KARTA AKTUALNEJ PARTII (AUTO-LOCK)
        # ==========================================
        batch_group = QGroupBox("Aktualna Partia (Auto-Lock)")
        batch_layout = QVBoxLayout(batch_group)
        batch_layout.setSpacing(6)

        self.lbl_batch_code = QLabel("[ BRAK WZORCA — AUTO-LOCK AKTYWNY ]")
        self.lbl_batch_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_batch_code.setStyleSheet("""
            background-color: #222222;
            color: #ffaa00;
            font-size: 13px;
            font-weight: bold;
            padding: 8px;
            border-radius: 4px;
            border: 1px dashed #ffaa00;
        """)
        batch_layout.addWidget(self.lbl_batch_code)

        sub_batch_layout = QHBoxLayout()
        self.btn_reset_batch = QPushButton("🔄 Resetuj / Ucz nowy kod")
        self.btn_reset_batch.setStyleSheet("background-color: #3a3a3a; padding: 5px;")
        self.btn_reset_batch.clicked.connect(self.reset_baseline_requested.emit)
        sub_batch_layout.addWidget(self.btn_reset_batch)

        sub_batch_layout.addWidget(QLabel("Próg (N):"))
        self.spin_threshold = QSpinBox()
        self.spin_threshold.setRange(1, 20)
        self.spin_threshold.setValue(self.batch_manager.confirmation_threshold)
        self.spin_threshold.valueChanged.connect(self.threshold_changed.emit)
        sub_batch_layout.addWidget(self.spin_threshold)

        batch_layout.addLayout(sub_batch_layout)
        layout.addWidget(batch_group)

        # ==========================================
        # 4. KARTA NARZĘDZI TESTOWYCH I TRYBU ZBIERANIA ZDJĘĆ (CAPTURES)
        # ==========================================
        snap_group = QGroupBox("Zapis Obrazów i Tryb Zbierania Zdjęć (Captures)")
        snap_layout = QVBoxLayout(snap_group)
        snap_layout.setSpacing(6)

        # Przycisk pojedynczego ręcznego zrzutu
        self.btn_snapshot = QPushButton("📸 Zapisz Zdjęcie Teraz (Snapshot 4K + ROI)")
        self.btn_snapshot.setStyleSheet(
            "background-color: #2b78e4; color: white; font-weight: bold; font-size: 12px; padding: 7px;"
        )
        self.btn_snapshot.clicked.connect(self.snapshot_requested.emit)
        snap_layout.addWidget(self.btn_snapshot)

        # Duży przycisk przełączający Tryb Zbierania Zdjęć (Auto-Zapis)
        self.btn_autosave = QPushButton("⚪ TRYB ZBIERANIA ZDJĘĆ: WYŁĄCZONY (Kliknij, aby włączyć)")
        self.btn_autosave.setCheckable(True)
        self.btn_autosave.toggled.connect(self._on_autosave_button_toggled)
        self._update_autosave_button_style(False)
        snap_layout.addWidget(self.btn_autosave)

        # Alias dla kompatybilności wstecznej
        self.chk_autosave = self.btn_autosave

        # Wybór warunku auto-zapisu
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Warunek auto-zapisu:"))
        self.combo_autosave_mode = QComboBox()
        self.combo_autosave_mode.addItem("Każda skanowana klatka (co interwał)", "interval")
        self.combo_autosave_mode.addItem("Tylko gdy wykryto kod kreskowy", "detection_only")
        self.combo_autosave_mode.currentIndexChanged.connect(self._on_autosave_mode_combo_changed)
        mode_row.addWidget(self.combo_autosave_mode, stretch=1)
        snap_layout.addLayout(mode_row)

        # Pasek statusu zapisanych zdjęć
        self.lbl_capture_status = QLabel("Zapisano w sesji: 0 szt. | Ostatni zapis: brak")
        self.lbl_capture_status.setStyleSheet(
            "background-color: #1a1a1a; color: #cccccc; font-size: 11px; "
            "padding: 4px 6px; border-radius: 4px; border: 1px solid #333333;"
        )
        snap_layout.addWidget(self.lbl_capture_status)

        layout.addWidget(snap_group)

        # ==========================================
        # 5. TABELA OSTATNICH ODCZYTÓW (LIVE LOG)
        # ==========================================
        log_group = QGroupBox("Historia Ostatnich Odczytów (Live Log)")
        log_layout = QVBoxLayout(log_group)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Czas", "Odczytany Kod", "Format", "Status", "Czas (ms)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #161616;
                alternate-background-color: #1e1e1e;
                gridline-color: #333333;
                font-size: 11px;
            }
        """)
        self.table.setAlternatingRowColors(True)
        log_layout.addWidget(self.table)

        # Statystyki sesji na dole tabeli
        self.lbl_stats = QLabel("Przeskanowane klatki: 0 | Klatki z kodem: 0 | Łącznie kodów: 0")
        self.lbl_stats.setStyleSheet("color: #aaaaaa; font-size: 11px; font-weight: bold;")
        log_layout.addWidget(self.lbl_stats)

        layout.addWidget(log_group, stretch=1)

    def _on_autosave_button_toggled(self, checked: bool) -> None:
        self._update_autosave_button_style(checked)
        self.auto_save_toggled.emit(checked)

    def _update_autosave_button_style(self, active: bool) -> None:
        if active:
            self.btn_autosave.setText("🔴 TRYB ZBIERANIA ZDJĘĆ: AKTYWNY (Kliknij, aby zatrzymać)")
            self.btn_autosave.setStyleSheet(
                "background-color: #cc1111; color: #ffffff; font-weight: bold; font-size: 12px; "
                "padding: 7px; border: 2px solid #ff6666; border-radius: 4px;"
            )
        else:
            self.btn_autosave.setText("⚪ TRYB ZBIERANIA ZDJĘĆ: WYŁĄCZONY (Kliknij, aby włączyć)")
            self.btn_autosave.setStyleSheet(
                "background-color: #2b2b2b; color: #dddddd; font-weight: bold; font-size: 12px; "
                "padding: 7px; border: 1px solid #555555; border-radius: 4px;"
            )

    def set_autosave_mode(self, mode: str) -> None:
        for i in range(self.combo_autosave_mode.count()):
            if self.combo_autosave_mode.itemData(i) == mode:
                self.combo_autosave_mode.setCurrentIndex(i)
                break

    def _on_autosave_mode_combo_changed(self) -> None:
        mode = str(self.combo_autosave_mode.currentData())
        self.auto_save_mode_changed.emit(mode)

    def update_capture_status(self, session_count: int, total_dir_count: int, last_time: str, last_file: str) -> None:
        """Aktualizuje pasek stanu zapisanych zdjęć w panelu bocznym."""
        short_file = last_file if len(last_file) <= 34 else ("..." + last_file[-31:])
        self.lbl_capture_status.setText(
            f"Zapisano w sesji: {session_count} (W folderze: {total_dir_count}) | Ostatni [{last_time}]: {short_file}"
        )

    def update_scanner_live_status(self, status_text: str, state: str = "idle") -> None:
        """Aktualizuje widoczny status pracy skanera zxing-cpp w panelu bocznym."""
        self.lbl_scanner_live.setText(status_text)
        if state == "scanning":
            self.lbl_scanner_live.setStyleSheet(
                "background-color: #103040; color: #00ffff; font-weight: bold; font-size: 11px; "
                "padding: 4px 6px; border-radius: 4px; border: 1px solid #00ffff;"
            )
        elif state == "found":
            self.lbl_scanner_live.setStyleSheet(
                "background-color: #0d3518; color: #00ff88; font-weight: bold; font-size: 11px; "
                "padding: 4px 6px; border-radius: 4px; border: 1px solid #00dd66;"
            )
        elif state == "candidate":
            self.lbl_scanner_live.setStyleSheet(
                "background-color: #382805; color: #ffcc33; font-weight: bold; font-size: 11px; "
                "padding: 4px 6px; border-radius: 4px; border: 1px solid #ffaa00;"
            )
        else:
            self.lbl_scanner_live.setStyleSheet(
                "background-color: #1c2430; color: #cccccc; font-weight: bold; font-size: 11px; "
                "padding: 4px 6px; border-radius: 4px; border: 1px solid #445566;"
            )

    def update_hardware_status(self, connected: bool, name: str, mxid: str, usb_speed: str) -> None:
        """Aktualizacja informacji o połączeniu z kamerą."""
        if connected:
            self.lbl_hw_status.setText(f"● Połączono: {name}")
            self.lbl_hw_status.setStyleSheet("font-weight: bold; color: #00ff66;")
            self.lbl_hw_details.setText(f"Parametry: {usb_speed} | Identyfikator: {mxid}")
        else:
            self.lbl_hw_status.setText(f"● Brak sygnału: {name}")
            self.lbl_hw_status.setStyleSheet("font-weight: bold; color: #ff3333;")
            self.lbl_hw_details.setText("Podłącz wybraną kamerę lub wybierz inne urządzenie z listy...")

    def update_performance(self, fps: float, decode_ms: float) -> None:
        """Aktualizacja metryk wydajnościowych."""
        self.lbl_perf.setText(f"Strumień: {fps:.1f} FPS | Analiza: {decode_ms:.1f} ms")

    def update_opc_status(self, running: bool, endpoint_or_msg: str) -> None:
        """Aktualizacja statusu serwera OPC UA (Ignition 8.3)."""
        if running:
            port_label = "4840"
            if "://" in endpoint_or_msg:
                try:
                    host_port = endpoint_or_msg.split("://", 1)[1].split("/", 1)[0]
                    if ":" in host_port:
                        port_label = host_port.rsplit(":", 1)[1]
                except Exception:
                    pass
            self.lbl_opc_status.setText(f"● OPC UA: Port {port_label} [Aktywny]")
            self.lbl_opc_status.setStyleSheet("color: #00ff88; font-size: 11px; font-weight: bold;")
            self.lbl_opc_status.setToolTip(f"Serwer OPC UA gotowy na połączenia z Ignition:\n{endpoint_or_msg}")
        else:
            self.lbl_opc_status.setText(f"● OPC UA: {endpoint_or_msg}")
            self.lbl_opc_status.setStyleSheet("color: #ff4444; font-size: 11px;")
            self.lbl_opc_status.setToolTip("Serwer OPC UA wyłączony lub błąd uruchomienia")

    def update_batch_display(self) -> None:
        """Odświeża wyświetlanie wzorca partii, tabeli logów oraz stanu alarmu."""
        if self.batch_manager.baseline_code:
            self.lbl_batch_code.setText(f"{self.batch_manager.baseline_code} ({self.batch_manager.baseline_format})")
            self.lbl_batch_code.setStyleSheet("""
                background-color: #102615;
                color: #00ff88;
                font-size: 15px;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                border: 2px solid #00bb55;
            """)
        else:
            self.lbl_batch_code.setText("[ BRAK WZORCA — AUTO-LOCK AKTYWNY ]")
            self.lbl_batch_code.setStyleSheet("""
                background-color: #222222;
                color: #ffaa00;
                font-size: 13px;
                font-weight: bold;
                padding: 8px;
                border-radius: 4px;
                border: 1px dashed #ffaa00;
            """)

        if self.batch_manager.batch_change_alarm:
            cand = self.batch_manager.candidate_mismatch_code or "NOWY KOD"
            cnt = self.batch_manager.mismatch_consecutive_count
            self.lbl_alarm_desc.setText(f"Nowy kod '{cand}' wykryty {cnt}x z rzędu!")
            self.alarm_box.setVisible(True)
        else:
            self.alarm_box.setVisible(False)

        st = self.batch_manager.stats
        self.lbl_stats.setText(
            f"Przeskanowane klatki: {st.total_frames_analyzed} | Z kodami: {st.frames_with_codes} | "
            f"Kody: {st.total_barcodes_detected} (Zgodne: {st.matched_codes_count}, Inne: {st.mismatched_codes_count})"
        )

        history = self.batch_manager.history[:40]
        self.table.setRowCount(len(history))
        for row, entry in enumerate(history):
            item_time = QTableWidgetItem(entry.timestamp_str)
            item_code = QTableWidgetItem(entry.code)
            item_fmt = QTableWidgetItem(entry.format)

            if entry.is_match:
                item_status = QTableWidgetItem("ZGODNY")
                item_status.setForeground(QColor(0, 230, 80))
            else:
                item_status = QTableWidgetItem("⚠ INNY KOD")
                item_status.setForeground(QColor(255, 60, 60))

            item_dur = QTableWidgetItem(f"{entry.duration_ms:.1f}")

            self.table.setItem(row, 0, item_time)
            self.table.setItem(row, 1, item_code)
            self.table.setItem(row, 2, item_fmt)
            self.table.setItem(row, 3, item_status)
            self.table.setItem(row, 4, item_dur)
