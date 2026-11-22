import os
import shutil
import sys
from pathlib import Path

# Dodajemy katalog główny projektu do sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import cv2
import numpy as np
import zxingcpp

from src.barcode_engine import BarcodeEngine, DetectionResult
from src.batch_manager import BatchManager
from src.storage_manager import StorageManager


def test_barcode_engine_and_zxing_decoding():
    """Weryfikacja wycinania ROI, odczytu zxing-cpp oraz przeliczania współrzędnych."""
    engine = BarcodeEngine(roi_normalized=(0.2, 0.2, 0.8, 0.8))

    # Tworzymy pustą klatkę 4K (3840x2160)
    frame_4k = np.full((2160, 3840, 3), 255, dtype=np.uint8)

    # Generujemy syntetyczny kod kreskowy EAN-13: 5901234567893
    barcode_gen = zxingcpp.create_barcode("5901234567893", zxingcpp.BarcodeFormat.EAN13)
    barcode_img = np.array(barcode_gen.to_image(), dtype=np.uint8)
    if len(barcode_img.shape) == 2:
        barcode_img = cv2.cvtColor(barcode_img, cv2.COLOR_GRAY2BGR)

    # Skalujemy kod kreskowy do realistycznego rozmiaru (np. szerokość 300px, wysokość 120px)
    scaled_barcode = cv2.resize(barcode_img, (300, 120), interpolation=cv2.INTER_NEAREST)
    bh, bw = scaled_barcode.shape[:2]

    # Wklejamy kod w środku klatki 4K (wewnątrz ROI)
    center_y, center_x = 1080, 1920
    frame_4k[center_y : center_y + bh, center_x : center_x + bw] = scaled_barcode

    # Test get_zoom_crop (wycinek 1:1)
    zoom_crop = engine.get_zoom_crop(frame_4k, zoom_w=400, zoom_h=300)
    assert zoom_crop.shape == (300, 400, 3), f"Błędny rozmiar Zoom 1:1: {zoom_crop.shape}"

    # Wykonujemy detekcję
    result = engine.detect(frame_4k)
    assert len(result.barcodes) >= 1, "Nie wykryto syntetycznego kodu kreskowego w klatce 4K!"
    
    item = result.barcodes[0]
    assert item.text == "5901234567893", f"Niezgodny odczytany tekst: {item.text}"
    assert "EAN" in item.format.upper(), f"Niezgodny format: {item.format}"
    
    # Sprawdzamy współrzędne 4K
    min_x, min_y, max_x, max_y = item.rect_full
    assert 1800 <= min_x <= 2000, f"min_x poza zakresem: {min_x}"
    assert 1000 <= min_y <= 1200, f"min_y poza zakresem: {min_y}"
    print(f"[OK] BarcodeEngine poprawnie zdekodował kod: {item.text} w czasie {result.duration_ms:.2f} ms")

    # Test odczytu ciemnego, lekko rozmytego i obróconego o 90 stopni kodu (jak na stojącej puszce)
    frame_dark = np.full((2160, 3840, 3), 22, dtype=np.uint8)
    bc_rot = cv2.rotate(scaled_barcode, cv2.ROTATE_90_CLOCKWISE)
    bc_low_contrast = (20 + (bc_rot.astype(np.float32) / 255.0) * 35.0).astype(np.uint8)
    bc_low_contrast = cv2.GaussianBlur(bc_low_contrast, (5, 5), 1.3)
    rh, rw = bc_low_contrast.shape[:2]
    frame_dark[900 : 900 + rh, 1500 : 1500 + rw] = bc_low_contrast

    res_dark = engine.detect(frame_dark)
    assert len(res_dark.barcodes) >= 1, "Nie wykryto ciemnego obróconego kodu kreskowego!"
    assert res_dark.barcodes[0].text == "5901234567893"
    print(f"[OK] BarcodeEngine zdekodował ciemny obrócony kod 90° w czasie {res_dark.duration_ms:.2f} ms")



def test_batch_manager_logic():
    """Weryfikacja Auto-Lock partii, weryfikacji kodów i alarmu zmiany litografii po N powtórzeniach."""
    manager = BatchManager(confirmation_threshold=3)

    # 1. Początkowy stan
    assert manager.baseline_code is None

    # 2. Pierwsza klatka z kodem bazowym A
    res1 = DetectionResult(duration_ms=5.0)
    res1.barcodes.append(type("Item", (), {"text": "5901111111110", "format": "EAN-13", "is_match": True})())
    alarm = manager.process_detection(res1)

    assert not alarm
    assert manager.baseline_code == "5901111111110", "Auto-Lock nie ustawił pierwszego kodu jako bazowego!"
    assert manager.history[0].is_match is True

    # 3. Klatka z tym samym kodem A
    res2 = DetectionResult(duration_ms=4.0)
    res2.barcodes.append(type("Item", (), {"text": "5901111111110", "format": "EAN-13", "is_match": True})())
    alarm = manager.process_detection(res2)
    assert not alarm
    assert manager.stats.matched_codes_count == 2

    # 4. Klatka z nowym kodem B (1. raz)
    res3 = DetectionResult(duration_ms=4.5)
    res3.barcodes.append(type("Item", (), {"text": "5902222222220", "format": "EAN-13", "is_match": False})())
    alarm = manager.process_detection(res3)
    assert not alarm
    assert manager.mismatch_consecutive_count == 1

    # 5. Klatka z nowym kodem B (2. raz)
    res4 = DetectionResult(duration_ms=4.5)
    res4.barcodes.append(type("Item", (), {"text": "5902222222220", "format": "EAN-13", "is_match": False})())
    alarm = manager.process_detection(res4)
    assert not alarm
    assert manager.mismatch_consecutive_count == 2

    # 6. Klatka z nowym kodem B (3. raz -> osiągnięcie progu N=3)
    res5 = DetectionResult(duration_ms=4.5)
    res5.barcodes.append(type("Item", (), {"text": "5902222222220", "format": "EAN-13", "is_match": False})())
    alarm = manager.process_detection(res5)
    assert alarm is True, "Alarm zmiany litografii nie został wyzwolony po 3 kolejnych niezgodnościach!"
    assert manager.batch_change_alarm is True

    # 7. Akceptacja nowego kodu jako bazowego
    manager.accept_candidate_as_new_baseline()
    assert manager.baseline_code == "5902222222220"
    assert manager.batch_change_alarm is False
    print("[OK] BatchManager poprawnie obsłużył logikę partii i alarmu.")


def test_storage_manager(tmp_path: Path):
    """Weryfikacja zapisu snapshotów i konfiguracji JSON."""
    storage = StorageManager(output_dir=tmp_path / "captures", config_path=tmp_path / "config.json")

    # Test zapisu konfiguracji
    test_cfg = {
        "roi": [0.15, 0.25, 0.85, 0.75],
        "exposure_us": 250,
        "iso": 600,
        "auto_exposure": False,
        "detection_interval_s": 0.5,
        "confirmation_threshold": 4,
        "auto_save_on_detection": True,
        "baseline_code": "5901234567890"
    }
    storage.save_config(test_cfg)
    loaded_cfg = storage.load_config()
    assert loaded_cfg["exposure_us"] == 250
    assert loaded_cfg["baseline_code"] == "5901234567890"

    # Test zapisu snapshotu
    dummy_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    dummy_roi = np.zeros((200, 300, 3), dtype=np.uint8)
    full_path, roi_path = storage.save_snapshot(dummy_frame, dummy_roi, exposure_us=250, iso=600, tag="test")

    assert full_path.exists(), f"Plik {full_path} nie istnieje!"
    assert roi_path is not None and roi_path.exists(), f"Plik {roi_path} nie istnieje!"
    print("[OK] StorageManager poprawnie zapisał konfigurację i zrzuty PNG.")


if __name__ == "__main__":
    test_barcode_engine_and_zxing_decoding()
    test_batch_manager_logic()
    test_storage_manager(Path("./temp_test_captures"))
    shutil.rmtree("./temp_test_captures", ignore_errors=True)
    print("\n=== WSZYSTKIE TESTY ZAKOŃCZONE SUKCESEM! ===")
