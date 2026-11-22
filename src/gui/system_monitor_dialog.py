from __future__ import annotations

from collections import deque
from typing import Deque, Dict, Optional
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from src.system_monitor import SystemMetricsSnapshot


class TelemetryChartWidget(QWidget):
    """Lekki wykres liniowy w czasie rzeczywistym dla CPU systemu, CPU procesu i RAM."""

    def __init__(self, max_points: int = 60, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(115)
        self.setMaximumHeight(150)
        self._max_points = max_points
        self._sys_cpu: Deque[float] = deque(maxlen=max_points)
        self._proc_cpu: Deque[float] = deque(maxlen=max_points)
        self._ram_pct: Deque[float] = deque(maxlen=max_points)

    def push_sample(self, sys_cpu: float, proc_cpu: float, ram_pct: float) -> None:
        self._sys_cpu.append(max(0.0, min(100.0, sys_cpu)))
        self._proc_cpu.append(max(0.0, min(100.0, proc_cpu)))
        self._ram_pct.append(max(0.0, min(100.0, ram_pct)))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(4, 4, -4, -4)
        painter.setPen(QPen(QColor("#2c3440"), 1))
        painter.setBrush(QColor("#10141a"))
        painter.drawRoundedRect(rect, 6, 6)

        plot = rect.adjusted(34, 20, -10, -10)
        if plot.width() < 20 or plot.height() < 20:
            return

        # Siatka pozioma (0%, 25%, 50%, 75%, 100%)
        painter.setFont(QFont("Segoe UI", 8))
        for pct in (0, 25, 50, 75, 100):
            y = int(plot.bottom() - (pct / 100.0) * plot.height())
            painter.setPen(QPen(QColor("#1e2530"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(plot.left(), y, plot.right(), y)
            painter.setPen(QColor("#6d7b8c"))
            painter.drawText(rect.left() + 4, y + 4, f"{pct}%")

        # Legenda na górze
        legend_items = [
            ("CPU System", QColor("#00ddff")),
            ("CPU Proces", QColor("#00ee88")),
            ("RAM System", QColor("#ffaa33")),
        ]
        lx = plot.left()
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        for label, col in legend_items:
            painter.setPen(col)
            painter.drawText(lx, rect.top() + 14, f"● {label}")
            lx += painter.fontMetrics().horizontalAdvance(f"● {label}") + 16

        def draw_series(series: Deque[float], color: QColor, width: float = 2.0) -> None:
            if len(series) < 2:
                return
            path = QPainterPath()
            n = len(series)
            step_x = plot.width() / float(max(1, self._max_points - 1))
            start_idx = self._max_points - n
            for i, val in enumerate(series):
                x = plot.left() + (start_idx + i) * step_x
                y = plot.bottom() - (val / 100.0) * plot.height()
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            painter.setPen(QPen(color, width))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

        draw_series(self._ram_pct, QColor("#ffaa33"), 1.8)
        draw_series(self._sys_cpu, QColor("#00ddff"), 2.0)
        draw_series(self._proc_cpu, QColor("#00ee88"), 2.2)


def _format_uptime(seconds: float) -> str:
    secs = int(max(0.0, seconds))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _make_progress_bar(accent_hex: str) -> QProgressBar:
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(True)
    bar.setMinimumHeight(20)
    bar.setStyleSheet(
        f"""
        QProgressBar {{
            background-color: #161b22;
            border: 1px solid #303946;
            border-radius: 4px;
            color: #f0f4f8;
            font-weight: bold;
            font-size: 11px;
            text-align: center;
        }}
        QProgressBar::chunk {{
            background-color: {accent_hex};
            border-radius: 3px;
        }}
        """
    )
    return bar


class ResourceMonitorWindow(QWidget):
    """
    Niezależne okno monitora zasobów systemowych i wydajności aplikacji:
    - zajętość CPU (system, proces, opcjonalnie per-core + wykres historii)
    - obciążenie pamięci RAM (proces RSS/VMS, system, swap) oraz dysk
    - aktywne wątki (liczba wątków OS, wątków Pythona i szczegółowa tabela wątków)
    - metryki potoku wizyjnego (kamera, FPS, czasy analizy, OPC UA)
    """

    refresh_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Monitor Zasobów Systemu i Wątków — Barcode Verifier")
        self.resize(820, 720)
        self.setMinimumSize(680, 540)
        self._paused = False
        self._init_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._on_timer_tick)

    def _init_ui(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background-color: #14171c;
                color: #e6edf3;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 12px;
            }
            QGroupBox {
                border: 1px solid #2e3642;
                border-radius: 6px;
                margin-top: 10px;
                padding-top: 10px;
                font-weight: bold;
                font-size: 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #00ddff;
            }
            QPushButton {
                background-color: #252b34;
                border: 1px solid #455162;
                border-radius: 4px;
                padding: 4px 10px;
                min-height: 26px;
                color: #f0f4f8;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #303946;
                border-color: #00bbff;
            }
            QPushButton:checked {
                background-color: #7c4a0a;
                border-color: #ffaa22;
                color: #ffffff;
            }
            QComboBox {
                background-color: #1e232b;
                border: 1px solid #3f4a59;
                border-radius: 4px;
                padding: 3px 8px;
                min-height: 26px;
                color: #ffffff;
            }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Górny pasek sterowania monitora
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self.lbl_platform = QLabel("Platforma: - | Uptime: 00:00")
        self.lbl_platform.setStyleSheet("color: #9fb0c2; font-weight: bold;")
        top_bar.addWidget(self.lbl_platform, stretch=1)

        top_bar.addWidget(QLabel("Odświeżanie:"))
        self.combo_rate = QComboBox()
        for label, ms in [("0.5 s", 500), ("1.0 s", 1000), ("2.0 s", 2000), ("5.0 s", 5000)]:
            self.combo_rate.addItem(label, ms)
        self.combo_rate.setCurrentIndex(1)
        self.combo_rate.currentIndexChanged.connect(self._on_rate_changed)
        top_bar.addWidget(self.combo_rate)

        self.btn_pause = QPushButton("⏸ Pauza")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._on_pause_toggled)
        top_bar.addWidget(self.btn_pause)

        self.btn_refresh_now = QPushButton("🔄 Odśwież")
        self.btn_refresh_now.clicked.connect(self.refresh_requested.emit)
        top_bar.addWidget(self.btn_refresh_now)

        root.addLayout(top_bar)

        # Sekcja CPU + Wykres
        cpu_group = QGroupBox("Procesor (CPU) i Historia Obciążenia")
        cpu_layout = QVBoxLayout(cpu_group)
        cpu_layout.setSpacing(6)

        cpu_grid = QGridLayout()
        cpu_grid.setSpacing(6)

        cpu_grid.addWidget(QLabel("CPU System (całkowite):"), 0, 0)
        self.bar_cpu_sys = _make_progress_bar("#00aadd")
        cpu_grid.addWidget(self.bar_cpu_sys, 0, 1)

        cpu_grid.addWidget(QLabel("CPU Proces (ta aplikacja):"), 1, 0)
        self.bar_cpu_proc = _make_progress_bar("#00b86b")
        cpu_grid.addWidget(self.bar_cpu_proc, 1, 1)

        cpu_layout.addLayout(cpu_grid)

        self.lbl_cpu_details = QLabel("Rdzenie: - | Proces (1 rdzeń): -% | Load / Temp: -")
        self.lbl_cpu_details.setStyleSheet("color: #b7c4d1; font-size: 11px;")
        cpu_layout.addWidget(self.lbl_cpu_details)

        self.lbl_cpu_cores = QLabel("")
        self.lbl_cpu_cores.setWordWrap(True)
        self.lbl_cpu_cores.setStyleSheet("color: #82d9ff; font-size: 11px;")
        self.lbl_cpu_cores.setVisible(False)
        cpu_layout.addWidget(self.lbl_cpu_cores)

        self.chart = TelemetryChartWidget()
        cpu_layout.addWidget(self.chart)

        root.addWidget(cpu_group)

        # Rząd środkowy: RAM/Dysk oraz Potok Aplikacji
        mid_row = QHBoxLayout()
        mid_row.setSpacing(8)

        mem_group = QGroupBox("Pamięć RAM i Przestrzeń Dyskowa")
        mem_layout = QVBoxLayout(mem_group)
        mem_layout.setSpacing(6)

        self.lbl_ram_sys = QLabel("RAM System: - / - MB")
        self.lbl_ram_sys.setStyleSheet("font-weight: bold;")
        mem_layout.addWidget(self.lbl_ram_sys)
        self.bar_ram_sys = _make_progress_bar("#d98218")
        mem_layout.addWidget(self.bar_ram_sys)

        self.lbl_ram_proc = QLabel("RAM Aplikacji (RSS): - MB | Wirtualna: - MB")
        self.lbl_ram_proc.setStyleSheet("color: #7ee787; font-weight: bold;")
        mem_layout.addWidget(self.lbl_ram_proc)

        self.lbl_swap_disk = QLabel("Swap: - | Dysk (captures): -")
        self.lbl_swap_disk.setWordWrap(True)
        self.lbl_swap_disk.setStyleSheet("color: #b7c4d1; font-size: 11px;")
        mem_layout.addWidget(self.lbl_swap_disk)
        mem_layout.addStretch(1)

        mid_row.addWidget(mem_group, stretch=1)

        app_group = QGroupBox("Potok Wizyjny i Usługi Aplikacji")
        app_layout = QVBoxLayout(app_group)
        app_layout.setSpacing(5)

        self.lbl_app_cam = QLabel("Kamera: - | FPS: 0.0")
        self.lbl_app_scan = QLabel("Dekoder (zxing-cpp): - ms | Przeanalizowano: 0")
        self.lbl_app_opc = QLabel("Serwer OPC UA: -")
        self.lbl_app_gc = QLabel("Pliki captures: 0 | Python GC: -")

        for lbl in (self.lbl_app_cam, self.lbl_app_scan, self.lbl_app_opc, self.lbl_app_gc):
            lbl.setWordWrap(True)
            lbl.setStyleSheet("color: #d0dae4; font-size: 11px;")
            app_layout.addWidget(lbl)
        app_layout.addStretch(1)

        mid_row.addWidget(app_group, stretch=1)
        root.addLayout(mid_row)

        # Sekcja wątków
        thr_group = QGroupBox("Aktywne Wątki Procesu (Threads)")
        thr_layout = QVBoxLayout(thr_group)
        thr_layout.setSpacing(6)

        self.lbl_threads_summary = QLabel(
            "Wątki OS (cały proces): - | Wątki Pythona / QThread: - | Natywne C++/Qt/DepthAI: -"
        )
        self.lbl_threads_summary.setStyleSheet("color: #00ddff; font-weight: bold; font-size: 11px;")
        thr_layout.addWidget(self.lbl_threads_summary)

        self.tbl_threads = QTableWidget(0, 4)
        self.tbl_threads.setHorizontalHeaderLabels(["Nazwa wątku", "Typ", "ID (TID)", "Rola w aplikacji"])
        self.tbl_threads.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_threads.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_threads.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tbl_threads.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tbl_threads.verticalHeader().setVisible(False)
        self.tbl_threads.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_threads.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_threads.setAlternatingRowColors(True)
        self.tbl_threads.setStyleSheet(
            """
            QTableWidget {
                background-color: #10141a;
                alternate-background-color: #161b22;
                gridline-color: #29313d;
                font-size: 11px;
            }
            QHeaderView::section {
                background-color: #1f252e;
                color: #cfd8e3;
                border: 1px solid #2e3744;
                padding: 4px;
                font-weight: bold;
            }
            """
        )
        thr_layout.addWidget(self.tbl_threads, stretch=1)

        root.addWidget(thr_group, stretch=1)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._paused:
            self._refresh_timer.start()
        self.refresh_requested.emit()

    def hideEvent(self, event) -> None:
        self._refresh_timer.stop()
        super().hideEvent(event)

    def _on_timer_tick(self) -> None:
        if not self._paused and self.isVisible():
            self.refresh_requested.emit()

    def _on_rate_changed(self) -> None:
        ms = int(self.combo_rate.currentData() or 1000)
        self._refresh_timer.setInterval(ms)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = paused
        self.btn_pause.setText("▶ Wznów" if paused else "⏸ Pauza")
        if paused:
            self._refresh_timer.stop()
        elif self.isVisible():
            self._refresh_timer.start()
            self.refresh_requested.emit()

    def update_metrics(self, snap: SystemMetricsSnapshot, app_info: Optional[Dict[str, object]] = None) -> None:
        """Aktualizuje wszystkie wskaźniki i wykres w oknie monitora zasobów."""
        self.lbl_platform.setText(
            f"System: {snap.platform_label} | Telemetria: {snap.backend_name} | Czas pracy: {_format_uptime(snap.uptime_s)}"
        )

        # CPU
        self.bar_cpu_sys.setValue(int(round(snap.cpu_system_percent)))
        self.bar_cpu_sys.setFormat(f"{snap.cpu_system_percent:.1f}%")

        self.bar_cpu_proc.setValue(int(round(min(100.0, snap.cpu_process_percent))))
        self.bar_cpu_proc.setFormat(
            f"{snap.cpu_process_percent:.1f}% systemu ({snap.cpu_process_raw_percent:.1f}% z 1 rdzenia)"
        )

        core_desc = f"Rdzenie logiczne: {snap.cpu_count_logical}"
        if snap.cpu_count_physical:
            core_desc += f" (fizyczne: {snap.cpu_count_physical})"
        extra_parts = [core_desc]
        if snap.load_avg is not None:
            extra_parts.append(
                f"Load Avg: {snap.load_avg[0]:.2f}, {snap.load_avg[1]:.2f}, {snap.load_avg[2]:.2f}"
            )
        if snap.cpu_temp_c is not None:
            extra_parts.append(f"Temp CPU: {snap.cpu_temp_c:.1f} °C")
        self.lbl_cpu_details.setText(" | ".join(extra_parts))

        if snap.cpu_per_core:
            cores_txt = "  ".join(f"CPU{idx}: {val:4.1f}%" for idx, val in enumerate(snap.cpu_per_core))
            self.lbl_cpu_cores.setText(cores_txt)
            self.lbl_cpu_cores.setVisible(True)
        else:
            self.lbl_cpu_cores.setVisible(False)

        self.chart.push_sample(snap.cpu_system_percent, snap.cpu_process_percent, snap.ram_percent)

        # RAM i dysk
        ram_used_gb = snap.ram_used_mb / 1024.0
        ram_tot_gb = snap.ram_total_mb / 1024.0
        ram_avail_gb = snap.ram_available_mb / 1024.0
        self.lbl_ram_sys.setText(
            f"RAM System: {ram_used_gb:.2f} / {ram_tot_gb:.2f} GB (Wolne: {ram_avail_gb:.2f} GB)"
        )
        self.bar_ram_sys.setValue(int(round(snap.ram_percent)))
        self.bar_ram_sys.setFormat(f"{snap.ram_percent:.1f}% ({snap.ram_used_mb:.0f} MB)")

        self.lbl_ram_proc.setText(
            f"RAM Procesu (RSS): {snap.process_ram_mb:.1f} MB | Commit / VMS: {snap.process_vms_mb:.1f} MB"
        )

        swap_txt = (
            f"Swap: {snap.swap_used_mb:.0f}/{snap.swap_total_mb:.0f} MB ({snap.swap_percent:.0f}%)"
            if snap.swap_total_mb > 0
            else "Swap: brak / nieużywany"
        )
        disk_txt = f"Dysk (captures): wolne {snap.disk_free_gb:.1f} GB z {snap.disk_total_gb:.1f} GB ({snap.disk_used_percent:.0f}%)"
        self.lbl_swap_disk.setText(f"{swap_txt}\n{disk_txt}")

        # Potok aplikacji
        info = app_info or {}
        cam_name = info.get("camera_name", "-")
        fps = float(info.get("fps", 0.0))
        res_str = str(info.get("resolution", "-"))
        decode_ms = float(info.get("decode_ms", 0.0))
        total_scans = int(info.get("total_scans", 0))
        interval_s = float(info.get("interval_s", 0.0))
        opc_txt = str(info.get("opc_status", "-"))
        captures_cnt = int(info.get("captures_count", 0))

        self.lbl_app_cam.setText(f"Kamera: {cam_name} | Rozdzielczość: {res_str} | {fps:.1f} FPS")
        sched_lbl = f"co {interval_s:.1f} s" if interval_s > 0 else "ręczny"
        self.lbl_app_scan.setText(
            f"Skaner zxing-cpp: {decode_ms:.1f} ms | Interwał: {sched_lbl} | Skany: {total_scans}"
        )
        self.lbl_app_opc.setText(f"OPC UA: {opc_txt}")
        self.lbl_app_gc.setText(
            f"Pliki PNG (captures): {captures_cnt} szt. | Python GC: {snap.gc_counts}"
        )

        # Wątki
        native_extra = max(0, snap.os_thread_count - len(snap.threads))
        self.lbl_threads_summary.setText(
            f"Wątki OS (cały proces): {snap.os_thread_count}  |  "
            f"Wątki Pythona: {snap.python_thread_count}  |  "
            f"Wątki natywne C++/Qt/OpenCV/DepthAI: {native_extra}"
        )

        self.tbl_threads.setRowCount(len(snap.threads))
        for row, thr in enumerate(snap.threads):
            tid_val = thr.native_id if thr.native_id else thr.ident
            tid_str = str(tid_val) if tid_val else "Qt-Native"
            item_name = QTableWidgetItem(thr.name)
            item_kind = QTableWidgetItem(thr.kind)
            item_tid = QTableWidgetItem(tid_str)
            item_role = QTableWidgetItem(thr.role)

            if thr.kind == "Główny":
                item_name.setForeground(QColor("#00ddff"))
            elif thr.kind == "QThread":
                item_name.setForeground(QColor("#00ee88"))

            self.tbl_threads.setItem(row, 0, item_name)
            self.tbl_threads.setItem(row, 1, item_kind)
            self.tbl_threads.setItem(row, 2, item_tid)
            self.tbl_threads.setItem(row, 3, item_role)
