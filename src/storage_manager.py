from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import cv2
import numpy as np

logger = logging.getLogger("storage_manager")


class StorageManager:
    """Obsługuje zapis zdjęć (Snapshot) 4K i ROI oraz trwałość konfiguracji w config.json."""

    def __init__(self, output_dir: str | Path = "captures", config_path: str | Path = "config.json"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq_counter: int = 0
        self.session_saved_count: int = 0
        self.last_saved_name: str = "-"
        self.last_saved_time: str = "-"

    def get_total_files_in_dir(self) -> int:
        """Zwraca liczbę par zrzutów (_full.png) znajdujących się obecnie w katalogu captures/."""
        try:
            return len(list(self.output_dir.glob("*_full.png")))
        except Exception:
            return 0

    def save_snapshot(
        self,
        full_frame: np.ndarray,
        roi_crop: Optional[np.ndarray],
        exposure_us: int,
        iso: int,
        tag: str = "manual"
    ) -> Tuple[Path, Optional[Path]]:
        """
        Zapisuje pełną klatkę 4K oraz wycinek ROI do plików PNG w bezstratnej jakości.
        Bezpieczne wielowątkowo — gwarantuje unikalne nazwy plików nawet przy szybkim zapisie seryjnym.
        """
        with self._lock:
            self._seq_counter += 1
            seq_num = self._seq_counter
            now = datetime.now()
            ts_str = now.strftime("%Y%m%d_%H%M%S_") + f"{int(now.microsecond / 1000):03d}"
            time_hms = now.strftime("%H:%M:%S")

            base_name = f"snap_{ts_str}_n{seq_num:03d}_exp{exposure_us}us_iso{iso}_{tag}"
            full_path = self.output_dir / f"{base_name}_full.png"
            roi_path: Optional[Path] = None

        # Zapis pełnej klatki 4K w PNG (kompresja 1 dla dużej szybkości zapisu bezstratnego)
        ok_full = cv2.imwrite(str(full_path), full_frame, [int(cv2.IMWRITE_PNG_COMPRESSION), 1])
        if not ok_full:
            logger.error("Nie udało się zapisać pliku: %s", full_path)

        # Zapis wycinka ROI, jeśli dostępny
        if roi_crop is not None and roi_crop.size > 0:
            roi_path = self.output_dir / f"{base_name}_roi.png"
            ok_roi = cv2.imwrite(str(roi_path), roi_crop, [int(cv2.IMWRITE_PNG_COMPRESSION), 1])
            if not ok_roi:
                logger.error("Nie udało się zapisać pliku ROI: %s", roi_path)

        with self._lock:
            if ok_full:
                self.session_saved_count += 1
            self.last_saved_name = full_path.name
            self.last_saved_time = time_hms

        logger.info(
            "Zapisano snapshot #%d: full=%s, roi=%s",
            seq_num,
            full_path.name,
            roi_path.name if roi_path else "brak"
        )
        return full_path, roi_path

    def load_config(self) -> Dict[str, Any]:
        """Wczytuje konfigurację z config.json lub zwraca domyślne parametry."""
        defaults: Dict[str, Any] = {
            "roi": [0.1, 0.2, 0.9, 0.9],
            "exposure_us": 20000,
            "iso": 800,
            "auto_exposure": True,
            "detection_interval_s": 1.0,
            "confirmation_threshold": 5,
            "auto_save_on_detection": False,
            "auto_save_mode": "interval",  # 'interval' (każda skanowana klatka) lub 'detection_only' (tylko przy kodzie)
            "baseline_code": None
        }

        if not self.config_path.exists():
            return defaults

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
                logger.info("Załadowano konfigurację z: %s", self.config_path)
                return defaults
        except Exception as e:
            logger.warning("Błąd wczytywania config.json: %s. Użyto wartości domyślnych.", e)
            return defaults

    def save_config(self, config_data: Dict[str, Any]) -> None:
        """Zapisuje parametry konfiguracyjne do pliku config.json."""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4, ensure_ascii=False)
            logger.info("Zapisano konfigurację do: %s", self.config_path)
        except Exception as e:
            logger.error("Błąd zapisu config.json: %s", e)
