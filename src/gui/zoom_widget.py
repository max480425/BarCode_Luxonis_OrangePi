from __future__ import annotations

from typing import Optional
import cv2
import numpy as np
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ZoomWidget(QWidget):
    """
    Panel podglądu wycinka 1:1 (Pixel-to-Pixel) z ROI bez żadnego skalowania.
    Umożliwia operatorowi natychmiastową ocenę ostrości pasków i braku rozmycia (motion blur)
    wraz z bieżącym wskaźnikiem ostrości i jasności.
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedSize(360, 240)
        self.setStyleSheet("""
            QWidget {
                background-color: #1a1a1a;
                border: 1px solid #333333;
                border-radius: 6px;
            }
        """)

        self._image: Optional[QImage] = None
        self._crop_shape = (0, 0)
        self._brightness: float = 0.0
        self._sharpness: float = 0.0
        self._target_label: str = "Środek ROI"

    def update_zoom_frame(self, crop_bgr: Optional[np.ndarray], target_label: str = "Środek ROI") -> None:
        """Aktualizacja wycinka 1:1 oraz obliczenie wskaźnika ostrości i jasności."""
        self._target_label = target_label
        if crop_bgr is None or crop_bgr.size == 0:
            self._image = None
            self.update()
            return

        h, w, ch = crop_bgr.shape
        self._crop_shape = (w, h)

        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        self._brightness = float(gray.mean())
        norm = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
        den = cv2.GaussianBlur(norm, (5, 5), 1.2)
        self._sharpness = float(cv2.Laplacian(den, cv2.CV_64F).var())

        crop_rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        bytes_per_line = ch * w
        self._image = QImage(crop_rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).copy()
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Tło i wycinek 1:1
        if self._image is not None and not self._image.isNull():
            img_w = self._image.width()
            img_h = self._image.height()
            ox = max(0, (self.width() - img_w) // 2)
            oy = max(0, (self.height() - img_h) // 2)
            dest_rect = QRect(ox, oy, img_w, img_h)
            painter.drawImage(dest_rect, self._image)

            # Siatka celownicza w centrum
            cx = self.width() // 2
            cy = self.height() // 2
            pen_cross = QPen(QColor(0, 255, 200, 110), 1, Qt.PenStyle.DashLine)
            painter.setPen(pen_cross)
            painter.drawLine(cx - 30, cy, cx + 30, cy)
            painter.drawLine(cx, cy - 30, cx, cy + 30)
        else:
            painter.setPen(QColor(100, 100, 100))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Brak wycinka 1:1 ROI")

        # 2. Nagłówek na górze
        header_rect = QRect(6, 6, self.width() - 12, 22)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(10, 12, 16, 215))
        painter.drawRoundedRect(header_rect, 4, 4)

        painter.setPen(QColor(0, 220, 255))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.drawText(
            header_rect,
            Qt.AlignmentFlag.AlignCenter,
            f"ZOOM 1:1 ({self._target_label}) — PPM na wideo zmienia cel"
        )

        # 3. Dolny pasek oceny ostrości i jasności
        if self._image is not None:
            footer_rect = QRect(6, self.height() - 26, self.width() - 12, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(10, 12, 16, 225))
            painter.drawRoundedRect(footer_rect, 4, 4)

            if self._brightness < 45:
                q_txt = f"Jasność: {self._brightness:.0f}/255 (ZA CIEMNO!) | Ostrość: {self._sharpness:.0f}"
                q_col = QColor(255, 180, 50)
            elif self._sharpness < 45:
                q_txt = f"Jasność: {self._brightness:.0f}/255 | Ostrość: {self._sharpness:.0f} (ROZMYTE <50cm)"
                q_col = QColor(255, 190, 60)
            else:
                q_txt = f"Jasność: {self._brightness:.0f}/255 (OK) | Ostrość: {self._sharpness:.0f} (DOBRA)"
                q_col = QColor(0, 240, 120)

            painter.setPen(q_col)
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.drawText(footer_rect, Qt.AlignmentFlag.AlignCenter, q_txt)
