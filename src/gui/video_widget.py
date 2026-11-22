from __future__ import annotations

import time
from typing import List, Optional, Tuple
import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget
from src.barcode_engine import BarcodeItem, CandidateBox, DetectionResult


class VideoWidget(QWidget):
    """
    Interaktywny widżet wyświetlający skalowany podgląd wideo z kamery,
    z animacją skanowania kodów, wskaźnikiem HUD trybu zbierania zdjęć,
    zaznaczaniem myszką obszaru ROI (LPM) oraz wyborem punktu Zoom 1:1 (PPM).
    """
    roi_changed = Signal(float, float, float, float)  # (x1, y1, x2, y2) w zakresie 0.0 - 1.0
    zoom_point_changed = Signal(object)               # Tuple[float, float] (norm_x, norm_y) lub None (auto)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumSize(640, 360)
        self.setStyleSheet("background-color: #121212; border-radius: 6px;")

        self._image: Optional[QImage] = None
        self._raw_w = 1280
        self._raw_h = 960
        self._full_w = 4000
        self._full_h = 3000
        self._barcodes: List[BarcodeItem] = []
        self._candidates: List[CandidateBox] = []

        # Znormalizowane współrzędne ROI (0.0 - 1.0)
        self.roi_norm = (0.1, 0.2, 0.9, 0.8)

        # Granice wycinka Zoom 1:1 w klatce 4K (zx1, zy1, zx2, zy2)
        self._zoom_bounds_4k: Optional[Tuple[int, int, int, int]] = None
        self._zoom_pinned_manually: bool = False

        # Stan interakcji myszką
        self._is_drawing_roi = False
        self._drag_start_pos: Optional[QPoint] = None
        self._current_mouse_pos: Optional[QPoint] = None

        # Stan HUD skanera i diagnostyki
        self._is_scanning_now: bool = False
        self._scan_interval_s: float = 1.0
        self._last_scan_id: int = 0
        self._last_scan_ms: float = 0.0
        self._last_scan_time_str: str = "-"
        self._roi_brightness: float = 0.0
        self._roi_sharpness: float = 0.0

        # Animacja linii skanującej w ROI
        self._scan_line_progress: float = -1.0  # -1.0 = brak animacji, 0.0..1.0 = ruch wiązki
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(25)
        self._anim_timer.timeout.connect(self._on_anim_step)

        # Stan HUD trybu zbierania zdjęć (Auto-Zapis / Snapshot)
        self._auto_save_active: bool = False
        self._auto_save_mode: str = "interval"
        self._saved_count_session: int = 0
        self._last_saved_toast_text: str = ""
        self._last_saved_toast_until: float = 0.0
        self._flash_until: float = 0.0

    def set_roi(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Ustawia ROI z poziomu kodu (np. z suwaków konfiguracji)."""
        self.roi_norm = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.update()

    def set_full_resolution(self, full_w: int, full_h: int) -> None:
        """Ustawia rzeczywisty wymiar pełnej klatki matrycy (np. 4000x3000 lub 3840x2160)."""
        if full_w > 0 and full_h > 0:
            self._full_w = full_w
            self._full_h = full_h

    def set_zoom_bounds_4k(self, bounds: Tuple[int, int, int, int], pinned: bool = False) -> None:
        """Ustawia granice prostokąta Zoom 1:1 (w układzie 3840x2160) do narysowania na podglądzie."""
        self._zoom_bounds_4k = bounds
        self._zoom_pinned_manually = pinned

    def set_scanner_schedule_info(self, interval_s: float) -> None:
        """Aktualizuje informację o interwale skanowania w HUD."""
        self._scan_interval_s = interval_s
        self.update()

    def notify_scan_started(self) -> None:
        """Wywoływane w momencie rozpoczęcia analizy klatki przez zxing-cpp."""
        self._is_scanning_now = True
        self._scan_line_progress = 0.0
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def notify_scan_finished(self, result: DetectionResult) -> None:
        """Wywoływane po zakończeniu analizy klatki przez zxing-cpp."""
        self._is_scanning_now = False
        self._barcodes = result.barcodes
        self._candidates = result.candidates
        self._last_scan_id = result.scan_id
        self._last_scan_ms = result.duration_ms
        self._roi_brightness = result.roi_brightness
        self._roi_sharpness = result.roi_sharpness
        self._last_scan_time_str = time.strftime("%H:%M:%S", time.localtime(result.timestamp))
        if self._scan_line_progress < 0.0:
            self._scan_line_progress = 0.0
            self._anim_timer.start()
        self.update()

    def set_photo_collection_status(self, active: bool, mode: str, saved_count: int) -> None:
        """Aktualizuje widoczny na ekranie status trybu zbierania zdjęć."""
        self._auto_save_active = active
        self._auto_save_mode = mode
        self._saved_count_session = saved_count
        self.update()

    def notify_snapshot_saved(self, filename: str, saved_count: int, tag: str = "manual") -> None:
        """Wyświetla powiadomienie i błysk na ekranie po zapisaniu zdjęcia na dysk."""
        self._saved_count_session = saved_count
        now = time.monotonic()
        if tag == "manual":
            mode_lbl = "RĘCZNY"
        elif tag == "interval":
            mode_lbl = "AUTO-INTERWAŁ"
        elif tag == "detected":
            mode_lbl = "AUTO-KOD"
        else:
            mode_lbl = "AUTO"
        self._last_saved_toast_text = f"📸 ZAPISANO ZDJĘCIE #{saved_count} [{mode_lbl}]: {filename}"
        self._last_saved_toast_until = now + 2.2
        self._flash_until = now + 0.18
        if not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def _on_anim_step(self) -> None:
        if self._scan_line_progress >= 0.0:
            self._scan_line_progress += 0.08
            if self._scan_line_progress > 1.0:
                self._scan_line_progress = -1.0
                if time.monotonic() > self._last_saved_toast_until:
                    self._anim_timer.stop()
        elif time.monotonic() > self._last_saved_toast_until:
            self._anim_timer.stop()
        self.update()

    def update_frame(self, frame_bgr: np.ndarray, detection_result: Optional[DetectionResult] = None) -> None:
        """Aktualizacja wyświetlanej klatki wideo."""
        h, w, ch = frame_bgr.shape
        self._raw_w = w
        self._raw_h = h

        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        bytes_per_line = ch * w
        self._image = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()

        if detection_result is not None:
            self._barcodes = detection_result.barcodes
            self._candidates = detection_result.candidates

        self.update()

    def _get_target_rect(self) -> QRect:
        """Oblicza prostokąt obrazu z zachowaniem proporcji 16:9."""
        if self._image is None:
            return self.rect()

        widget_w = self.width()
        widget_h = self.height()
        aspect_img = self._raw_w / self._raw_h
        aspect_widget = widget_w / widget_h

        if aspect_widget > aspect_img:
            target_h = widget_h
            target_w = int(widget_h * aspect_img)
            offset_x = (widget_w - target_w) // 2
            offset_y = 0
        else:
            target_w = widget_w
            target_h = int(widget_w / aspect_img)
            offset_x = 0
            offset_y = (widget_h - target_h) // 2

        return QRect(offset_x, offset_y, target_w, target_h)

    def _widget_to_norm(self, pt: QPoint, target_rect: QRect) -> Tuple[float, float]:
        if target_rect.width() == 0 or target_rect.height() == 0:
            return 0.0, 0.0
        norm_x = (pt.x() - target_rect.left()) / target_rect.width()
        norm_y = (pt.y() - target_rect.top()) / target_rect.height()
        return max(0.0, min(1.0, norm_x)), max(0.0, min(1.0, norm_y))

    def _norm_to_target(self, norm_x: float, norm_y: float, target_rect: QRect) -> QPoint:
        px = int(target_rect.left() + norm_x * target_rect.width())
        py = int(target_rect.top() + norm_y * target_rect.height())
        return QPoint(px, py)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        target_rect = self._get_target_rect()
        pt = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton:
            if target_rect.contains(pt):
                self._is_drawing_roi = True
                self._drag_start_pos = pt
                self._current_mouse_pos = pt
        elif event.button() == Qt.MouseButton.RightButton:
            # Prawy przycisk myszy ustawia środek podglądu Zoom 1:1 w klikniętym miejscu
            if target_rect.contains(pt):
                nx, ny = self._widget_to_norm(pt, target_rect)
                self.zoom_point_changed.emit((nx, ny))
                self.update()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            # Podwójne kliknięcie PPM przywraca automatyczne centrowanie Zoom 1:1
            self.zoom_point_changed.emit(None)
            self.update()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._is_drawing_roi:
            self._current_mouse_pos = event.position().toPoint()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_drawing_roi:
            self._is_drawing_roi = False
            target_rect = self._get_target_rect()
            if self._drag_start_pos and self._current_mouse_pos:
                nx1, ny1 = self._widget_to_norm(self._drag_start_pos, target_rect)
                nx2, ny2 = self._widget_to_norm(self._current_mouse_pos, target_rect)
                rx1 = min(nx1, nx2)
                ry1 = min(ny1, ny2)
                rx2 = max(nx1, nx2)
                ry2 = max(ny1, ny2)

                if (rx2 - rx1) > 0.03 and (ry2 - ry1) > 0.03:
                    self.roi_norm = (rx1, ry1, rx2, ry2)
                    self.roi_changed.emit(rx1, ry1, rx2, ry2)
            self._drag_start_pos = None
            self._current_mouse_pos = None
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        target_rect = self._get_target_rect()

        # 1. Rysowanie klatki wideo
        if self._image is not None and not self._image.isNull():
            painter.drawImage(target_rect, self._image)
        else:
            painter.setPen(QColor(150, 150, 150))
            painter.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Oczekiwanie na strumień z wybranej kamery...")
            return

        now_mono = time.monotonic()

        # 2. Rysowanie aktywnej ramki ROI
        rx1, ry1, rx2, ry2 = self.roi_norm
        p1 = self._norm_to_target(rx1, ry1, target_rect)
        p2 = self._norm_to_target(rx2, ry2, target_rect)
        roi_rect = QRect(p1, p2)

        # Kolor ramki ROI pulsuje podczas skanowania
        is_animating = (self._scan_line_progress >= 0.0) or self._is_scanning_now
        if self._barcodes:
            roi_color = QColor(0, 255, 110)
            roi_pen = QPen(roi_color, 3, Qt.PenStyle.SolidLine)
        elif is_animating:
            roi_color = QColor(0, 255, 255)
            roi_pen = QPen(roi_color, 3, Qt.PenStyle.SolidLine)
        else:
            roi_color = QColor(0, 210, 255)
            roi_pen = QPen(roi_color, 2, Qt.PenStyle.DashLine)

        painter.setPen(roi_pen)
        painter.setBrush(QColor(0, 220, 255, 18))
        painter.drawRect(roi_rect)

        # Animowana wiązka skanera wewnątrz ROI (pokazuje wizualnie, że kod jest szukany)
        if 0.0 <= self._scan_line_progress <= 1.0:
            scan_y = int(roi_rect.top() + self._scan_line_progress * roi_rect.height())
            painter.setPen(QPen(QColor(0, 255, 180, 230), 3, Qt.PenStyle.SolidLine))
            painter.drawLine(roi_rect.left() + 2, scan_y, roi_rect.right() - 2, scan_y)
            # Poświata wiązki
            band_rect = QRect(roi_rect.left() + 2, max(roi_rect.top(), scan_y - 14), roi_rect.width() - 4, 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 255, 180, 55))
            painter.drawRect(band_rect)

        # Etykieta nagłówkowa ROI
        roi_w_px = int((rx2 - rx1) * self._full_w)
        roi_h_px = int((ry2 - ry1) * self._full_h)
        roi_header_text = f"ROI: {roi_w_px}×{roi_h_px} px ({self._full_w}×{self._full_h}) (LPM: zmień ROI | PPM: Zoom 1:1)"
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        hm = painter.fontMetrics()
        hw = hm.horizontalAdvance(roi_header_text) + 14
        hh = hm.height() + 6
        roi_badge = QRect(roi_rect.left(), max(target_rect.top() + 58, roi_rect.top() - hh - 2), hw, hh)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 20, 28, 210))
        painter.drawRoundedRect(roi_badge, 4, 4)
        painter.setPen(roi_color)
        painter.drawText(roi_badge.left() + 7, roi_badge.bottom() - 5, roi_header_text)

        # 3. Rysowanie prostokąta Zoom 1:1 wewnątrz podglądu
        if self._zoom_bounds_4k is not None:
            zx1, zy1, zx2, zy2 = self._zoom_bounds_4k
            zp1 = self._norm_to_target(zx1 / float(self._full_w), zy1 / float(self._full_h), target_rect)
            zp2 = self._norm_to_target(zx2 / float(self._full_w), zy2 / float(self._full_h), target_rect)
            z_rect = QRect(zp1, zp2)
            z_color = QColor(255, 110, 255) if self._zoom_pinned_manually else QColor(180, 180, 255, 180)
            painter.setPen(QPen(z_color, 1, Qt.PenStyle.DotLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(z_rect)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            z_lbl = "ZOOM 1:1 (Ręczny - 2xPPM reset)" if self._zoom_pinned_manually else "ZOOM 1:1"
            painter.drawText(z_rect.left() + 4, z_rect.bottom() - 4, z_lbl)

        # 4. Rysowanie tymczasowej ramki podczas przeciągania myszką
        if self._is_drawing_roi and self._drag_start_pos and self._current_mouse_pos:
            drag_rect = QRect(self._drag_start_pos, self._current_mouse_pos).normalized()
            painter.setPen(QPen(QColor(255, 200, 0), 2, Qt.PenStyle.SolidLine))
            painter.setBrush(QColor(255, 200, 0, 40))
            painter.drawRect(drag_rect)

        # 5. Rysowanie kandydatów na kody kreskowe (gdy zxing-cpp nie mógł jeszcze odczytać cyfr)
        if not self._barcodes and self._candidates:
            for idx, cand in enumerate(self._candidates):
                cx1, cy1, cx2, cy2 = cand.rect_full
                cp1 = self._norm_to_target(cx1 / float(self._full_w), cy1 / float(self._full_h), target_rect)
                cp2 = self._norm_to_target(cx2 / float(self._full_w), cy2 / float(self._full_h), target_rect)
                c_rect = QRect(cp1, cp2)

                cand_color = QColor(255, 180, 0)
                painter.setPen(QPen(cand_color, 2, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(255, 180, 0, 35))
                painter.drawRect(c_rect)

                if idx == 0:
                    hints = []
                    if cand.brightness < 55:
                        hints.append(f"Za ciemno ({cand.brightness:.0f}/255)")
                    if cand.sharpness < 60:
                        hints.append("Nieostre")
                    hint_str = " | ".join(hints) if hints else "Popraw kąt/oświetlenie"
                    c_text = f"🔍 WYKRYTO OBSZAR KODU — {hint_str}"

                    painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                    cm = painter.fontMetrics()
                    ctw = cm.horizontalAdvance(c_text) + 14
                    cth = cm.height() + 6
                    c_badge = QRect(c_rect.left(), max(target_rect.top() + 60, c_rect.top() - cth - 4), ctw, cth)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(30, 20, 0, 225))
                    painter.drawRoundedRect(c_badge, 4, 4)
                    painter.setPen(cand_color)
                    painter.drawText(c_badge.left() + 7, c_badge.bottom() - 5, c_text)

        # 6. Rysowanie odczytanych kodów kreskowych
        for item in self._barcodes:
            color = QColor(0, 235, 90) if item.is_match else QColor(255, 50, 50)
            poly_points = []
            for (fx, fy) in item.polygon_full:
                nx = fx / float(self._full_w)
                ny = fy / float(self._full_h)
                poly_points.append(self._norm_to_target(nx, ny, target_rect))

            if len(poly_points) >= 4:
                qpoly = QPolygonF([QPoint(p.x(), p.y()) for p in poly_points])
                painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine))
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 55))
                painter.drawPolygon(qpoly)

                top_pt = poly_points[0]
                label_text = f"✓ {item.text} [{item.format}]"
                if not item.is_match:
                    label_text = f"⚠ NIEZGODNY: {item.text} [{item.format}]"

                painter.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
                metrics = painter.fontMetrics()
                tw = metrics.horizontalAdvance(label_text) + 16
                th = metrics.height() + 8

                badge_rect = QRect(top_pt.x(), max(target_rect.top() + 60, top_pt.y() - th - 4), tw, th)
                painter.setPen(QPen(color, 1))
                painter.setBrush(QColor(15, 15, 15, 230))
                painter.drawRoundedRect(badge_rect, 4, 4)

                painter.setPen(color)
                painter.drawText(badge_rect.left() + 8, badge_rect.bottom() - 6, label_text)

        # =====================================================================
        # 7. GÓRNY PASEK HUD NA EKRANIE WIDEO (STATUS SZUKANIA KODU + ZBIERANIA ZDJĘĆ)
        # =====================================================================
        hud_margin = 10
        hud_top = target_rect.top() + hud_margin

        # 7A. Lewy banner HUD: Status szukania kodów kreskowych
        if self._is_scanning_now or (0.0 <= self._scan_line_progress <= 0.4):
            scan_dot_color = QColor(0, 255, 255)
            scan_state_txt = f"🔍 SZUKANIE KODU W TOKU... (Skan #{self._last_scan_id + 1})"
        elif self._scan_interval_s > 0:
            scan_dot_color = QColor(0, 230, 100)
            scan_state_txt = (
                f"🔍 SZUKANIE KODÓW: AKTYWNE (co {self._scan_interval_s:.1f} s)  |  "
                f"Skan #{self._last_scan_id} ({self._last_scan_ms:.0f} ms)"
            )
        else:
            scan_dot_color = QColor(255, 170, 0)
            scan_state_txt = f"⏸ SZUKANIE KODÓW: TRYB RĘCZNY  |  Wykonano skanów: {self._last_scan_id}"

        if self._barcodes:
            codes_str = ", ".join(f"{b.text} ({b.format})" for b in self._barcodes)
            sub_txt = f"✅ ODCZYTANO: {codes_str}"
            sub_color = QColor(0, 255, 120)
        elif self._candidates:
            c0 = self._candidates[0]
            sub_txt = (
                f"⚠ WYKRYTO KANDYDATA KODU (Jasność: {c0.brightness:.0f}/255, Ostrość: {c0.sharpness:.0f}) "
                f"— Zwiększ ekspozycję lub odsuń puszkę na >50 cm!"
            )
            sub_color = QColor(255, 200, 50)
        else:
            bright_warn = " (ZA CIEMNO! Zwiększ µs lub włącz Auto Exposure)" if 0 < self._roi_brightness < 55 else ""
            sub_txt = (
                f"Ostatni skan ({self._last_scan_time_str}): Brak kodu w ROI  |  "
                f"Jasność ROI: {self._roi_brightness:.0f}/255{bright_warn}"
            )
            sub_color = QColor(255, 190, 70) if bright_warn else QColor(200, 210, 220)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        fm_main = painter.fontMetrics()
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        fm_sub = painter.fontMetrics()

        left_box_w = max(fm_main.horizontalAdvance(scan_state_txt), fm_sub.horizontalAdvance(sub_txt)) + 26
        left_box_h = 46
        left_hud_rect = QRect(target_rect.left() + hud_margin, hud_top, left_box_w, left_box_h)

        painter.setPen(QPen(scan_dot_color, 1))
        painter.setBrush(QColor(12, 16, 22, 220))
        painter.drawRoundedRect(left_hud_rect, 6, 6)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.setPen(scan_dot_color)
        painter.drawText(left_hud_rect.left() + 12, left_hud_rect.top() + 19, scan_state_txt)

        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        painter.setPen(sub_color)
        painter.drawText(left_hud_rect.left() + 12, left_hud_rect.top() + 38, sub_txt)

        # 7B. Prawy banner HUD: Status Trybu Zbierania Zdjęć (Auto-Zapis)
        if self._auto_save_active:
            mode_desc = "co skan" if self._auto_save_mode == "interval" else "przy kodzie"
            rec_txt = f"🔴 ZBIERANIE ZDJĘĆ: AKTYWNE ({mode_desc}) | Zapisano: {self._saved_count_session}"
            rec_bg = QColor(140, 15, 15, 230)
            rec_border = QColor(255, 70, 70)
            rec_fg = QColor(255, 255, 255)
        else:
            rec_txt = f"⚪ ZBIERANIE ZDJĘĆ: WYŁĄCZONE | Zapisano w sesji: {self._saved_count_session}"
            rec_bg = QColor(20, 24, 30, 210)
            rec_border = QColor(90, 95, 105)
            rec_fg = QColor(180, 185, 195)

        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        fm_rec = painter.fontMetrics()
        rec_w = fm_rec.horizontalAdvance(rec_txt) + 24
        rec_h = 34
        right_hud_rect = QRect(target_rect.right() - hud_margin - rec_w, hud_top, rec_w, rec_h)

        painter.setPen(QPen(rec_border, 2 if self._auto_save_active else 1))
        painter.setBrush(rec_bg)
        painter.drawRoundedRect(right_hud_rect, 6, 6)
        painter.setPen(rec_fg)
        painter.drawText(right_hud_rect, Qt.AlignmentFlag.AlignCenter, rec_txt)

        # 8. Powiadomienie Toast na dole ekranu po zapisaniu zdjęcia + krótki błysk ramki
        if now_mono < self._flash_until:
            painter.setPen(QPen(QColor(255, 255, 255, 200), 6))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(target_rect.adjusted(3, 3, -3, -3))

        if now_mono < self._last_saved_toast_until and self._last_saved_toast_text:
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            tm = painter.fontMetrics()
            tw = tm.horizontalAdvance(self._last_saved_toast_text) + 28
            th = 34
            toast_rect = QRect(
                target_rect.center().x() - tw // 2,
                target_rect.bottom() - th - 14,
                tw,
                th
            )
            painter.setPen(QPen(QColor(0, 230, 140), 2))
            painter.setBrush(QColor(10, 35, 22, 235))
            painter.drawRoundedRect(toast_rect, 6, 6)
            painter.setPen(QColor(180, 255, 210))
            painter.drawText(toast_rect, Qt.AlignmentFlag.AlignCenter, self._last_saved_toast_text)
