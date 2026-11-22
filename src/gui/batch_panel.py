from __future__ import annotations

from typing import Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
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
    Panel boczny zarządzania partią produkcyjną, tabelą odczytów w czasie rzeczywistym,
    narzędziami zapisu Snapshot i Trybu Zbierania Zdjęć oraz skrótem do monitora zasobów.
    """
    reset_baseline_requested = Signal()
    accept_baseline_requested = Signal()
    clear_alarm_requested = Signal()
    clear_log_requested = Signal()
    open_system_monitor_requested = Signal()
    snapshot_requested = Signal()
    auto_save_toggled = Signal(bool)
    auto_save_mode_changed = Signal(str)  # 'interval' lub 'detection_only'
    threshold_changed = Signal(int)
    auto_switch_threshold_changed = Signal(int)

    def __init__(self, batch_manager: BatchManager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.batch_manager = batch_manager
        self.setMinimumWidth(360)
        self._init_ui()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(7)

        # ==========================================
        # 1. KARTA STATUSU SPRZĘTU, SKANERA I ZASOBÓW
        # ==========================================
        hw_group = QGroupBox("Status Aktywnej Kamery i Skanera")
        hw_layout = QVBoxLayout(hw_group)
        hw_layout.setContentsMargins(8, 8, 8, 6)
        hw_layout.setSpacing(4)

        self.lbl_hw_status = QLabel("● Połączenie: Oczekiwanie...")
        self.lbl_hw_status.setWordWrap(True)
        self.lbl_hw_status.setStyleSheet("font-weight: bold; color: #ffaa00;")

        self.lbl_hw_details = QLabel("Model: - | Szczegóły: - | ID: -")
        self.lbl_hw_details.setWordWrap(True)
        self.lbl_hw_details.setStyleSheet("color: #aaaaaa; font-size: 11px;")

        self.lbl_perf = QLabel("Strumień: 0.0 FPS | Analiza: 0.0 ms")
        self.lbl_perf.setStyleSheet("color: #00ddff; font-weight: bold; font-size: 11px;")

        self.lbl_opc_status = QLabel("● OPC UA (Ignition): Inicjalizacja...")
        self.lbl_opc_status.setWordWrap(True)
        self.lbl_opc_status.setStyleSheet("color: #ffaa00; font-size: 11px; font-weight: bold;")

        sys_row = QHBoxLayout()
        sys_row.setSpacing(6)
        self.lbl_sys_perf = QLabel("CPU: -% | RAM: - MB | Wątki: -")
        self.lbl_sys_perf.setStyleSheet("color: #9bb8d3; font-size: 11px; font-weight: bold;")
        self.btn_system_monitor = QPushButton("📊 Monitor zasobów")
        self.btn_system_monitor.setMinimumHeight(26)
        self.btn_system_monitor.setToolTip(
            "Otwiera okno z obciążeniem CPU, zajętością pamięci RAM, listą wątków i telemetrią"
        )
        self.btn_system_monitor.setStyleSheet(
            "QPushButton { background-color: #1c2c3d; border: 1px solid #2b6cb0; color: #cce7ff; "
            "font-weight: bold; font-size: 11px; padding: 3px 8px; }"
            "QPushButton:hover { background-color: #24405c; border-color: #00ccff; color: #ffffff; }"
        )
        self.btn_system_monitor.clicked.connect(self.open_system_monitor_requested.emit)
        sys_row.addWidget(self.lbl_sys_perf, stretch=1)
        sys_row.addWidget(self.btn_system_monitor)

        self.lbl_scanner_live = QLabel("🔍 Skaner: Oczekiwanie na pierwszą klatkę...")
        self.lbl_scanner_live.setWordWrap(True)
        self.lbl_scanner_live.setStyleSheet(
            "background-color: #1c2430; color: #00ffcc; font-weight: bold; font-size: 11px; "
            "padding: 4px 6px; border-radius: 4px; border: 1px solid #0088aa;"
        )

        hw_layout.addWidget(self.lbl_hw_status)
        hw_layout.addWidget(self.lbl_hw_details)
        hw_layout.addWidget(self.lbl_perf)
        hw_layout.addWidget(self.lbl_opc_status)
        hw_layout.addLayout(sys_row)
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
        self.lbl_alarm_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        self.lbl_alarm_desc = QLabel("Wykryto nową serię innego kodu na linii!")
        self.lbl_alarm_desc.setWordWrap(True)
        self.lbl_alarm_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_alarm_desc.setStyleSheet("font-size: 12px; color: #ffdddd;")

        btn_alarm_layout = QHBoxLayout()
        self.btn_accept_new = QPushButton("✓ Ustaw jako nowy wzorzec")
        self.btn_accept_new.setMinimumHeight(28)
        self.btn_accept_new.setStyleSheet(
            "QPushButton { background-color: #009944; border: 1px solid #22dd66; color: white; font-weight: bold; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #00b852; }"
        )
        self.btn_accept_new.clicked.connect(self.accept_baseline_requested.emit)

        self.btn_dismiss = QPushButton("✕ Wycisz alarm")
        self.btn_dismiss.setMinimumHeight(28)
        self.btn_dismiss.setStyleSheet(
            "QPushButton { background-color: #444444; border: 1px solid #777777; color: white; font-weight: bold; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #5a5a5a; }"
        )
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
        batch_layout.setContentsMargins(8, 6, 8, 5)
        batch_layout.setSpacing(5)

        self.lbl_batch_code = QLabel("[ BRAK WZORCA — AUTO-LOCK AKTYWNY ]")
        self.lbl_batch_code.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_batch_code.setStyleSheet("""
            background-color: #222222;
            color: #ffaa00;
            font-size: 13px;
            font-weight: bold;
            padding: 6px;
            border-radius: 4px;
            border: 1px dashed #ffaa00;
        """)
        batch_layout.addWidget(self.lbl_batch_code)

        self.lbl_batch_progress = QLabel("")
        self.lbl_batch_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_batch_progress.setWordWrap(True)
        self.lbl_batch_progress.setVisible(False)
        batch_layout.addWidget(self.lbl_batch_progress)

        self.btn_reset_batch = QPushButton("🔄 Resetuj / Ucz nowy kod")
        self.btn_reset_batch.setMinimumHeight(26)
        self.btn_reset_batch.setStyleSheet(
            "QPushButton { background-color: #323842; border: 1px solid #525d6e; color: #f0f4f8; font-weight: bold; padding: 3px 8px; }"
            "QPushButton:hover { background-color: #3f4754; border-color: #00bbff; }"
        )
        self.btn_reset_batch.clicked.connect(self.reset_baseline_requested.emit)
        batch_layout.addWidget(self.btn_reset_batch)

        sub_batch_layout = QHBoxLayout()
        sub_batch_layout.setSpacing(8)

        sub_batch_layout.addWidget(QLabel("Alarm (N):"))
        self.spin_threshold = QSpinBox()
        self.spin_threshold.setRange(1, 20)
        self.spin_threshold.setMinimumWidth(58)
        self.spin_threshold.setMinimumHeight(25)
        self.spin_threshold.setToolTip("Liczba kolejnych innych kodów wywołująca alarm zmiany litografii")
        self.spin_threshold.setValue(self.batch_manager.confirmation_threshold)
        self.spin_threshold.valueChanged.connect(self.threshold_changed.emit)
        sub_batch_layout.addWidget(self.spin_threshold)

        sub_batch_layout.addWidget(QLabel("Auto-baza (N):"))
        self.spin_auto_switch = QSpinBox()
        self.spin_auto_switch.setRange(0, 100)
        self.spin_auto_switch.setSpecialValueText("Wył.")
        self.spin_auto_switch.setMinimumWidth(62)
        self.spin_auto_switch.setMinimumHeight(25)
        self.spin_auto_switch.setToolTip(
            "Po ilu kolejnych wykryciach takiego samego nowego kodu automatycznie ustawić go jako nowy kod bazowy (0 = wyłączone)"
        )
        self.spin_auto_switch.setValue(self.batch_manager.auto_switch_threshold)
        self.spin_auto_switch.valueChanged.connect(self.auto_switch_threshold_changed.emit)
        sub_batch_layout.addWidget(self.spin_auto_switch)
        sub_batch_layout.addStretch(1)

        batch_layout.addLayout(sub_batch_layout)
        layout.addWidget(batch_group)

        # ==========================================
        # 4. KARTA NARZĘDZI TESTOWYCH I TRYBU ZBIERANIA ZDJĘĆ (CAPTURES)
        # ==========================================
        snap_group = QGroupBox("Zapis Obrazów i Tryb Zbierania Zdjęć (Captures)")
        snap_layout = QVBoxLayout(snap_group)
        snap_layout.setContentsMargins(8, 8, 8, 6)
        snap_layout.setSpacing(6)

        self.btn_snapshot = QPushButton("📸 Zapisz Zdjęcie Teraz (Snapshot 4K + ROI)")
        self.btn_snapshot.setMinimumHeight(30)
        self.btn_snapshot.setStyleSheet(
            "QPushButton { background-color: #2563eb; border: 1px solid #60a5fa; color: white; "
            "font-weight: bold; font-size: 12px; padding: 5px 10px; }"
            "QPushButton:hover { background-color: #1d4ed8; border-color: #93c5fd; }"
        )
        self.btn_snapshot.clicked.connect(self.snapshot_requested.emit)
        snap_layout.addWidget(self.btn_snapshot)

        self.btn_autosave = QPushButton("⚪ ZBIERANIE ZDJĘĆ: WYŁĄCZONE (Włącz)")
        self.btn_autosave.setMinimumHeight(30)
        self.btn_autosave.setCheckable(True)
        self.btn_autosave.toggled.connect(self._on_autosave_button_toggled)
        self._update_autosave_button_style(False)
        snap_layout.addWidget(self.btn_autosave)

        # Alias dla kompatybilności wstecznej
        self.chk_autosave = self.btn_autosave

        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_row.addWidget(QLabel("Warunek:"))
        self.combo_autosave_mode = QComboBox()
        self.combo_autosave_mode.setMinimumHeight(26)
        self.combo_autosave_mode.addItem("Każda skanowana klatka (co interwał)", "interval")
        self.combo_autosave_mode.addItem("Tylko gdy wykryto kod kreskowy", "detection_only")
        self.combo_autosave_mode.currentIndexChanged.connect(self._on_autosave_mode_combo_changed)
        mode_row.addWidget(self.combo_autosave_mode, stretch=1)
        snap_layout.addLayout(mode_row)

        self.lbl_capture_status = QLabel("Zapisano w sesji: 0 szt. | Ostatni zapis: brak")
        self.lbl_capture_status.setWordWrap(True)
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
        log_layout.setContentsMargins(8, 8, 8, 6)
        log_layout.setSpacing(5)

        log_header_row = QHBoxLayout()
        log_header_row.setSpacing(6)
        self.lbl_log_count = QLabel("Ostatnie odczyty (max 40):")
        self.lbl_log_count.setStyleSheet("color: #9aa6b2; font-size: 11px;")
        log_header_row.addWidget(self.lbl_log_count, stretch=1)

        self.btn_clear_log = QPushButton("🗑 Wyczyść Live Log")
        self.btn_clear_log.setMinimumHeight(26)
        self.btn_clear_log.setToolTip("Natychmiast kasuje całą historię tabeli Live Log")
        self.btn_clear_log.setStyleSheet(
            "QPushButton { background-color: #3b2226; border: 1px solid #99444c; color: #ffd9dc; "
            "font-weight: bold; font-size: 11px; padding: 3px 10px; }"
            "QPushButton:hover { background-color: #542c32; border-color: #ff6670; color: #ffffff; }"
            "QPushButton:pressed { background-color: #2b181b; }"
        )
        self.btn_clear_log.clicked.connect(self.clear_log_requested.emit)
        log_header_row.addWidget(self.btn_clear_log)
        log_layout.addLayout(log_header_row)

        self.table = QTableWidget(0, 5)
        self.table.setMinimumHeight(130)
        self.table.setHorizontalHeaderLabels(["Czas", "Odczytany Kod", "Format", "Status", "ms"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setStyleSheet("""
            QTableWidget {
                background-color: #161616;
                alternate-background-color: #1e1e1e;
                gridline-color: #333333;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #222730;
                color: #d0d8e0;
                border: 1px solid #333a45;
                padding: 4px;
                font-weight: bold;
            }
        """)
        self.table.setAlternatingRowColors(True)
        log_layout.addWidget(self.table, stretch=1)

        self.lbl_stats = QLabel("Przeskanowane klatki: 0 | Klatki z kodem: 0 | Łącznie kodów: 0")
        self.lbl_stats.setWordWrap(True)
        self.lbl_stats.setStyleSheet("color: #aaaaaa; font-size: 11px; font-weight: bold;")
        log_layout.addWidget(self.lbl_stats)

        layout.addWidget(log_group, stretch=1)

    def _on_autosave_button_toggled(self, checked: bool) -> None:
        self._update_autosave_button_style(checked)
        self.auto_save_toggled.emit(checked)

    def _update_autosave_button_style(self, active: bool) -> None:
        if active:
            self.btn_autosave.setText("🔴 ZBIERANIE ZDJĘĆ: AKTYWNE (Zatrzymaj)")
            self.btn_autosave.setStyleSheet(
                "QPushButton { background-color: #cc1111; color: #ffffff; font-weight: bold; font-size: 12px; "
                "padding: 5px 8px; border: 2px solid #ff6666; border-radius: 4px; }"
                "QPushButton:hover { background-color: #e01818; }"
            )
        else:
            self.btn_autosave.setText("⚪ ZBIERANIE ZDJĘĆ: WYŁĄCZONE (Włącz)")
            self.btn_autosave.setStyleSheet(
                "QPushButton { background-color: #2b313a; color: #e2e8f0; font-weight: bold; font-size: 12px; "
                "padding: 5px 8px; border: 1px solid #556070; border-radius: 4px; }"
                "QPushButton:hover { background-color: #363e4a; border-color: #00bbee; }"
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

    def update_system_summary(self, cpu_pct: float, proc_ram_mb: float, os_threads: int) -> None:
        """Aktualizuje skrótowy podgląd obciążenia CPU, RAM i wątków w panelu bocznym."""
        self.lbl_sys_perf.setText(
            f"CPU: {cpu_pct:.0f}% | RAM proc.: {proc_ram_mb:.0f} MB | Wątki: {os_threads}"
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
            self.lbl_hw_details.setText(f"Parametry: {usb_speed} | ID: {mxid}")
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
            fmt = self.batch_manager.baseline_format
            badge_txt = f"{self.batch_manager.baseline_code} ({fmt})" if fmt else self.batch_manager.baseline_code
            self.lbl_batch_code.setText(badge_txt)
            self.lbl_batch_code.setStyleSheet("""
                background-color: #102615;
                color: #00ff88;
                font-size: 14px;
                font-weight: bold;
                padding: 7px;
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
                padding: 7px;
                border-radius: 4px;
                border: 1px dashed #ffaa00;
            """)

        auto_thr = self.batch_manager.auto_switch_threshold
        if self.batch_manager.batch_change_alarm:
            cand = self.batch_manager.candidate_mismatch_code or "NOWY KOD"
            cnt = self.batch_manager.mismatch_consecutive_count
            auto_hint = f" (auto-baza przy {auto_thr}x)" if auto_thr > 0 else ""
            self.lbl_alarm_desc.setText(f"Nowy kod '{cand}' wykryty {cnt}x z rzędu{auto_hint}!")
            self.alarm_box.setVisible(True)
        else:
            self.alarm_box.setVisible(False)

        if self.batch_manager.last_auto_switched_code:
            self.lbl_batch_progress.setText(
                f"🔄 Auto-zmiana bazy ({auto_thr}/{auto_thr}): {self.batch_manager.last_auto_switched_code}"
            )
            self.lbl_batch_progress.setStyleSheet("color: #00ee88; font-size: 11px; font-weight: bold;")
            self.lbl_batch_progress.setVisible(True)
        elif self.batch_manager.mismatch_consecutive_count > 0 and self.batch_manager.candidate_mismatch_code:
            cand = self.batch_manager.candidate_mismatch_code
            cnt = self.batch_manager.mismatch_consecutive_count
            if auto_thr > 0:
                self.lbl_batch_progress.setText(f"Kandydat zmiany bazy: {cand} ({cnt}/{auto_thr})")
            else:
                self.lbl_batch_progress.setText(f"Inny kod z rzędu: {cand} ({cnt}x)")
            self.lbl_batch_progress.setStyleSheet("color: #ffbb44; font-size: 11px; font-weight: bold;")
            self.lbl_batch_progress.setVisible(True)
        else:
            self.lbl_batch_progress.setVisible(False)

        st = self.batch_manager.stats
        self.lbl_stats.setText(
            f"Przeskanowane klatki: {st.total_frames_analyzed} | Z kodami: {st.frames_with_codes} | "
            f"Kody: {st.total_barcodes_detected} (Zgodne: {st.matched_codes_count}, Inne: {st.mismatched_codes_count})"
        )

        total_hist = len(self.batch_manager.history)
        history = self.batch_manager.history[:40]
        self.lbl_log_count.setText(f"Wpisy w logu: {total_hist} (widoczne maks. 40):")
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
