from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import cv2
import numpy as np
import zxingcpp


@dataclass
class BarcodeItem:
    """Reprezentacja pojedynczego wykrytego kodu kreskowego."""
    text: str
    format: str
    polygon_roi: List[Tuple[int, int]]       # Współrzędne wierzchołków wewnątrz wycinka ROI
    polygon_full: List[Tuple[int, int]]      # Współrzędne przeliczone na pełną klatkę
    rect_full: Tuple[int, int, int, int]     # (min_x, min_y, max_x, max_y) w pełnej klatce
    is_match: bool = True                    # Czy kod zgadza się z aktualną partią bazową


@dataclass
class CandidateBox:
    """Obszar w ROI posiadający silną strukturę paskową (kandydat na kod kreskowy, jeszcze nieodczytany)."""
    rect_roi: Tuple[int, int, int, int]      # (x1, y1, x2, y2) wewnątrz ROI
    rect_full: Tuple[int, int, int, int]     # (x1, y1, x2, y2) w pełnej klatce
    orientation: str = "V"                   # 'H' (paski poziome) lub 'V' (paski pionowe)
    score: float = 0.0
    brightness: float = 0.0                  # Średnia jasność (0-255) wewnątrz kandydata
    sharpness: float = 0.0                   # Wskaźnik ostrości wewnątrz kandydata


@dataclass
class DetectionResult:
    """Wynik analizy pojedynczej klatki przez dekoder zxing-cpp."""
    barcodes: List[BarcodeItem] = field(default_factory=list)
    candidates: List[CandidateBox] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: float = 0.0
    roi_crop: np.ndarray | None = None
    roi_bounds_px: Tuple[int, int, int, int] = (0, 0, 0, 0)
    roi_brightness: float = 0.0
    roi_sharpness: float = 0.0
    scan_id: int = 0


class BarcodeEngine:
    """
    Silnik detekcji i odczytu kodów kreskowych w natywnej rozdzielczości matrycy (12MP / 4K)
    wzbogacony o 1D dekonwolucję Wienera (FFT) kompensującą nieostrość optyczną obiektywu Fixed Focus.
    """

    def __init__(self, roi_normalized: Tuple[float, float, float, float] = (0.1, 0.2, 0.9, 0.8)):
        self.roi_normalized = self._validate_roi(roi_normalized)
        self._scan_counter = 0
        self._clahe = cv2.createCLAHE(clipLimit=3.5, tileGridSize=(8, 8))

    def _validate_roi(self, roi: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
        x1, y1, x2, y2 = roi
        x1 = max(0.0, min(1.0, float(x1)))
        y1 = max(0.0, min(1.0, float(y1)))
        x2 = max(0.0, min(1.0, float(x2)))
        y2 = max(0.0, min(1.0, float(y2)))

        if x2 <= x1 + 0.02:
            x2 = min(1.0, x1 + 0.02)
            if x2 <= x1 + 0.02:
                x1 = max(0.0, x2 - 0.02)
        if y2 <= y1 + 0.02:
            y2 = min(1.0, y1 + 0.02)
            if y2 <= y1 + 0.02:
                y1 = max(0.0, y2 - 0.02)

        return (x1, y1, x2, y2)

    def set_roi(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Aktualizacja aktywnego obszaru zainteresowania (ROI)."""
        self.roi_normalized = self._validate_roi((x1, y1, x2, y2))

    def get_roi_pixels(self, frame_w: int, frame_h: int) -> Tuple[int, int, int, int]:
        """Zwraca współrzędne pikselowe ROI (x1, y1, x2, y2)."""
        rx1, ry1, rx2, ry2 = self.roi_normalized
        px1 = int(rx1 * frame_w)
        py1 = int(ry1 * frame_h)
        px2 = int(rx2 * frame_w)
        py2 = int(ry2 * frame_h)
        return (px1, py1, px2, py2)

    def crop_roi(self, frame: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        """Wycina obszar ROI z podanej klatki w pełnej rozdzielczości."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = self.get_roi_pixels(w, h)
        roi_crop = frame[y1:y2, x1:x2].copy()
        return roi_crop, (x1, y1, x2, y2)

    def get_zoom_crop_with_bounds(
        self,
        frame: np.ndarray,
        zoom_w: int = 360,
        zoom_h: int = 240,
        focus_px: Optional[Tuple[int, int]] = None
    ) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
        """
        Zwraca wycinek 1:1 (bez skalowania w dół) oraz jego granice (zx1, zy1, zx2, zy2) w pełnej klatce.
        """
        h, w = frame.shape[:2]
        if focus_px is not None:
            cx, cy = focus_px
        else:
            x1, y1, x2, y2 = self.get_roi_pixels(w, h)
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2

        zx1 = max(0, cx - zoom_w // 2)
        zy1 = max(0, cy - zoom_h // 2)
        zx2 = min(w, zx1 + zoom_w)
        zy2 = min(h, zy1 + zoom_h)

        if zx2 - zx1 < zoom_w and zx1 > 0:
            zx1 = max(0, zx2 - zoom_w)
        if zy2 - zy1 < zoom_h and zy1 > 0:
            zy1 = max(0, zy2 - zoom_h)

        return frame[zy1:zy2, zx1:zx2].copy(), (zx1, zy1, zx2, zy2)

    def get_zoom_crop(self, frame: np.ndarray, zoom_w: int = 400, zoom_h: int = 300) -> np.ndarray:
        """Zwraca wycinek 1:1 ze środka ROI (zachowane dla kompatybilności wstecznej)."""
        crop, _ = self.get_zoom_crop_with_bounds(frame, zoom_w=zoom_w, zoom_h=zoom_h)
        return crop

    def _convert_zxing_results(
        self,
        raw_results: list,
        offset_roi_x: int,
        offset_roi_y: int,
        rx1: int,
        ry1: int,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        rotated_90_cw: bool = False,
        orig_sub_w: int = 0,
        orig_sub_h: int = 0
    ) -> List[BarcodeItem]:
        items: List[BarcodeItem] = []
        seen_texts = set()
        for r in raw_results:
            text = r.text
            if not text or text in seen_texts:
                continue
            seen_texts.add(text)
            fmt = str(r.format).replace("BarcodeFormat.", "")
            pos = r.position

            raw_pts = [
                (pos.top_left.x * scale_x, pos.top_left.y * scale_y),
                (pos.top_right.x * scale_x, pos.top_right.y * scale_y),
                (pos.bottom_right.x * scale_x, pos.bottom_right.y * scale_y),
                (pos.bottom_left.x * scale_x, pos.bottom_left.y * scale_y),
            ]

            pts_roi: List[Tuple[int, int]] = []
            for (px, py) in raw_pts:
                if rotated_90_cw:
                    # Odwrócenie obrotu ROTATE_90_CLOCKWISE: (x_rot, y_rot) -> (x_orig = y_rot, y_orig = orig_sub_h - 1 - x_rot)
                    ox = int(py)
                    oy = max(0, int(orig_sub_h - 1 - px))
                else:
                    ox = int(px)
                    oy = int(py)
                pts_roi.append((ox + offset_roi_x, oy + offset_roi_y))

            pts_full = [(x + rx1, y + ry1) for (x, y) in pts_roi]
            xs = [p[0] for p in pts_full]
            ys = [p[1] for p in pts_full]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            # Czytnik 1D w zxing-cpp potrafi zwrócić pojedynczą linię skanującą (wysokość 1-2 px).
            # Rozszerzamy ją do pełnego prostokąta 2D, aby ramka na ekranie wideo obejmowała cały kod.
            if (max_y - min_y) < 50:
                cy = (min_y + max_y) // 2
                half_h = max(45, (max_x - min_x) // 3)
                min_y = max(0, cy - half_h)
                max_y = cy + half_h
                pts_full = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]
            elif (max_x - min_x) < 50:
                cx = (min_x + max_x) // 2
                half_w = max(45, (max_y - min_y) // 3)
                min_x = max(0, cx - half_w)
                max_x = cx + half_w
                pts_full = [(min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y)]

            rect_full = (min_x, min_y, max_x, max_y)

            items.append(BarcodeItem(
                text=text,
                format=fmt,
                polygon_roi=pts_roi,
                polygon_full=pts_full,
                rect_full=rect_full,
                is_match=True
            ))
        return items

    def _find_barcode_candidates(
        self,
        gray_roi: np.ndarray,
        rx1: int,
        ry1: int
    ) -> List[CandidateBox]:
        """
        Wyszukuje w wycinku ROI obszary o silnej kierunkowej strukturze paskowej (kandydaty na kod 1D).
        Gradienty Sobela obliczane są w pełnej rozdzielczości 1.0x (aby paski o szerokości 1.5-2.0 px z 80 cm
        nie znikały przez aliasing Nyquista), a następnie mapa energii jest redukowana do szybkiej morfologii.
        """
        h, w = gray_roi.shape[:2]
        if h < 60 or w < 60:
            return []

        gx_full = np.abs(cv2.Sobel(gray_roi, cv2.CV_32F, 1, 0, ksize=3))
        gy_full = np.abs(cv2.Sobel(gray_roi, cv2.CV_32F, 0, 1, ksize=3))

        scale = 0.25 if max(h, w) > 1200 else (0.5 if max(h, w) > 600 else 1.0)
        if scale != 1.0:
            gx = cv2.resize(gx_full, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            gy = cv2.resize(gy_full, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        else:
            gx, gy = gx_full, gy_full

        raw_candidates: List[CandidateBox] = []
        min_dim = max(12, int(55 * scale))
        min_area = max(200, int(4200 * (scale ** 2)))
        max_area = int(650000 * (scale ** 2))
        inv_s = 1.0 / scale

        kw_long = max(9, int(52 * scale) | 1)
        kw_short = max(3, int(20 * scale) | 1)

        for orient, diff, ksize in [
            ("H", np.maximum(0, gy - gx), (kw_short, kw_long)),
            ("V", np.maximum(0, gx - gy), (kw_long, kw_short)),
        ]:
            diff_u8 = cv2.normalize(diff, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            blur = cv2.blur(diff_u8, (5, 5))
            thr_val = max(35.0, float(np.percentile(blur, 95)))
            _, thresh = cv2.threshold(blur, thr_val, 255, cv2.THRESH_BINARY)
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, ksize)
            closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel)
            closed = cv2.erode(closed, None, iterations=1)
            closed = cv2.dilate(closed, None, iterations=1)

            cnts, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in cnts:
                x, y, cw, ch = cv2.boundingRect(c)
                if cw < min_dim or ch < min_dim:
                    continue
                area = cw * ch
                if not (min_area <= area <= max_area):
                    continue
                ar = cw / float(ch)
                if not (0.28 <= ar <= 3.5):
                    continue
                extent = cv2.contourArea(c) / float(area)
                if extent < 0.42:
                    continue

                sub_gx = float(gx[y:y + ch, x:x + cw].mean())
                sub_gy = float(gy[y:y + ch, x:x + cw].mean())
                dom_grad = sub_gy if orient == "H" else sub_gx
                orth_grad = sub_gx if orient == "H" else sub_gy
                ratio = dom_grad / (orth_grad + 1e-5)

                if ratio > 1.45 and dom_grad > 12.0:
                    cx1 = int(x * inv_s)
                    cy1 = int(y * inv_s)
                    cx2 = min(w, int((x + cw) * inv_s))
                    cy2 = min(h, int((y + ch) * inv_s))
                    sub_full = gray_roi[cy1:cy2, cx1:cx2]
                    c_bright = float(sub_full.mean()) if sub_full.size > 0 else 0.0
                    if sub_full.size > 0:
                        sub_norm = cv2.normalize(sub_full, None, 0, 255, cv2.NORM_MINMAX)
                        sub_den = cv2.GaussianBlur(sub_norm, (5, 5), 1.2)
                        c_sharp = float(cv2.Laplacian(sub_den, cv2.CV_64F).var())
                    else:
                        c_sharp = 0.0

                    raw_candidates.append(CandidateBox(
                        rect_roi=(cx1, cy1, cx2, cy2),
                        rect_full=(cx1 + rx1, cy1 + ry1, cx2 + rx1, cy2 + ry1),
                        orientation=orient,
                        score=float(ratio * extent * dom_grad),
                        brightness=c_bright,
                        sharpness=c_sharp
                    ))

        raw_candidates.sort(key=lambda c: c.score, reverse=True)
        return raw_candidates[:3]

    def _wiener_deconvolve_and_read(
        self,
        sub_gray: np.ndarray,
        offset_x: int,
        offset_y: int,
        rx1: int,
        ry1: int,
        orient: str = "V"
    ) -> List[BarcodeItem]:
        """
        Wykonuje 1D dekonwolucję Wienera w dziedzinie częstotliwości (FFT) w poprzek pasków kodu
        wraz z wygładzaniem wzdłuż pasków. Pozwala odczytać kody rozmyte optycznie (Fixed Focus).
        """
        orig_h, orig_w = sub_gray.shape[:2]
        if orig_h < 25 or orig_w < 25:
            return []

        rot90 = (orient == "H")
        work = cv2.rotate(sub_gray, cv2.ROTATE_90_CLOCKWISE) if rot90 else sub_gray
        h, w = work.shape[:2]

        # Dwukrotny upscaling poziomy (aby wąskie moduły ~1.8 px miały ~3.6 px przed dekonwolucją)
        w2 = w * 2
        g2 = cv2.resize(work, (w2, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
        # Wygładzenie wzdłuż pionowych pasków redukujące szum matrycy i refleksy z aluminium
        k_vert = 17
        g2 = cv2.GaussianBlur(g2, (1, k_vert), 0)

        F = np.fft.rfft(g2, axis=1)
        freqs = np.fft.rfftfreq(w2)

        for sigma in (1.5, 1.8, 2.1, 2.5, 3.0):
            H = np.exp(-2.0 * (np.pi ** 2) * (sigma ** 2) * (freqs ** 2))
            for snr in (0.005, 0.01, 0.02, 0.04):
                W = H / (H ** 2 + snr)
                rec = np.fft.irfft(F * W[None, :], n=w2, axis=1)
                rec = np.clip(rec, 0, 255).astype(np.uint8)
                rec = cv2.normalize(rec, None, 0, 255, cv2.NORM_MINMAX)

                for binarizer in (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram):
                    res = zxingcpp.read_barcodes(
                        rec,
                        try_rotate=False,
                        try_downscale=True,
                        try_invert=False,
                        binarizer=binarizer
                    )
                    if res:
                        items = self._convert_zxing_results(
                            res,
                            offset_roi_x=offset_x,
                            offset_roi_y=offset_y,
                            rx1=rx1,
                            ry1=ry1,
                            scale_x=0.5,
                            scale_y=1.0,
                            rotated_90_cw=rot90,
                            orig_sub_w=orig_w,
                            orig_sub_h=orig_h
                        )
                        if items:
                            return items
        return []

    def detect(self, frame_full: np.ndarray) -> DetectionResult:
        """
        Główna funkcja wykonująca wieloetapową detekcję kodów kreskowych na wycinku ROI.
        """
        t_start = time.perf_counter()
        self._scan_counter += 1
        scan_id = self._scan_counter

        roi_crop, (rx1, ry1, rx2, ry2) = self.crop_roi(frame_full)

        if roi_crop.size == 0 or roi_crop.shape[0] < 10 or roi_crop.shape[1] < 10:
            return DetectionResult(
                barcodes=[],
                candidates=[],
                duration_ms=(time.perf_counter() - t_start) * 1000.0,
                timestamp=time.time(),
                roi_crop=roi_crop,
                roi_bounds_px=(rx1, ry1, rx2, ry2),
                scan_id=scan_id
            )

        gray = cv2.cvtColor(roi_crop, cv2.COLOR_BGR2GRAY) if len(roi_crop.shape) == 3 else roi_crop
        roi_brightness = float(gray.mean())

        small_sharp = cv2.resize(gray, (0, 0), fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA) if gray.shape[1] > 400 else gray
        norm_s = cv2.normalize(small_sharp, None, 0, 255, cv2.NORM_MINMAX)
        den_s = cv2.GaussianBlur(norm_s, (5, 5), 1.2)
        roi_sharpness = float(cv2.Laplacian(den_s, cv2.CV_64F).var())

        # ETAP 1: Szybki odczyt na natywnej skali szarości
        raw_results = zxingcpp.read_barcodes(
            gray,
            try_rotate=True,
            try_downscale=True,
            try_invert=True
        )
        items = self._convert_zxing_results(raw_results, 0, 0, rx1, ry1)

        # ETAP 2: Lokalizacja kandydatów paskowych + Dekonwolucja Wienera FFT oraz Unsharp Mask
        candidates: List[CandidateBox] = []
        if not items:
            candidates = self._find_barcode_candidates(gray, rx1, ry1)
            h_roi, w_roi = gray.shape[:2]

            for cand in candidates:
                cx1, cy1, cx2, cy2 = cand.rect_roi
                pad_x = max(90, int((cx2 - cx1) * 0.65))
                pad_y = max(90, int((cy2 - cy1) * 0.65))
                px1 = max(0, cx1 - pad_x)
                py1 = max(0, cy1 - pad_y)
                px2 = min(w_roi, cx2 + pad_x)
                py2 = min(h_roi, cy2 + pad_y)

                sub = gray[py1:py2, px1:px2]
                if sub.size == 0:
                    continue

                # 2A. 1D Dekonwolucja Wienera (FFT) — odzyskuje kody rozmyte przez obiektyw Fixed Focus
                wiener_items = self._wiener_deconvolve_and_read(sub, px1, py1, rx1, ry1, orient=cand.orientation)
                if wiener_items:
                    items.extend(wiener_items)
                    break

                # 2B. Klasyczne wyostrzanie Unsharp Mask + CLAHE
                norm = cv2.normalize(sub, None, 0, 255, cv2.NORM_MINMAX)
                norm_clahe = self._clahe.apply(norm)
                blur = cv2.GaussianBlur(norm_clahe, (0, 0), sigmaX=2.0, sigmaY=2.0)
                sharp = cv2.addWeighted(norm_clahe, 2.2, blur, -1.2, 0)

                for binarizer in (zxingcpp.Binarizer.LocalAverage, zxingcpp.Binarizer.GlobalHistogram):
                    sub_res = zxingcpp.read_barcodes(
                        sharp,
                        try_rotate=True,
                        try_downscale=True,
                        try_invert=True,
                        binarizer=binarizer
                    )
                    if sub_res:
                        items.extend(self._convert_zxing_results(sub_res, px1, py1, rx1, ry1))
                        break
                if items:
                    break

            # 2C. Jeśli użytkownik zaznaczył ciasne ROI bezpośrednio wokół kodu (np. < 900x900 px),
            # uruchom dekonwolucję Wienera bezpośrednio na całym wycinku ROI w obu orientacjach!
            if not items and max(h_roi, w_roi) < 950:
                for orient in ("V", "H"):
                    wiener_items = self._wiener_deconvolve_and_read(gray, 0, 0, rx1, ry1, orient=orient)
                    if wiener_items:
                        items.extend(wiener_items)
                        break

        # ETAP 3: Jeśli nadal brak odczytu, próba CLAHE + GlobalHistogram na przeskalowanym ROI
        if not items:
            scale_c = 0.6 if max(gray.shape[:2]) > 1400 else 1.0
            gray_scaled = cv2.resize(gray, (0, 0), fx=scale_c, fy=scale_c, interpolation=cv2.INTER_AREA) if scale_c != 1.0 else gray
            clahe_gray = self._clahe.apply(gray_scaled)
            raw_results = zxingcpp.read_barcodes(
                clahe_gray,
                try_rotate=True,
                try_downscale=True,
                try_invert=True,
                binarizer=zxingcpp.Binarizer.GlobalHistogram
            )
            inv_c = 1.0 / scale_c
            items = self._convert_zxing_results(raw_results, 0, 0, rx1, ry1, scale_x=inv_c, scale_y=inv_c)

        duration_ms = (time.perf_counter() - t_start) * 1000.0

        return DetectionResult(
            barcodes=items,
            candidates=candidates if not items else [],
            duration_ms=duration_ms,
            timestamp=time.time(),
            roi_crop=roi_crop,
            roi_bounds_px=(rx1, ry1, rx2, ry2),
            roi_brightness=roi_brightness,
            roi_sharpness=roi_sharpness,
            scan_id=scan_id
        )
