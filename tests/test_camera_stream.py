import os
import sys
import time
from pathlib import Path

# Dodajemy katalog główny projektu do sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from PySide6.QtWidgets import QApplication
from src.camera_stream import CameraWorker, CameraDeviceInfo, enumerate_cameras
from src.gui.main_window import MainWindow
from src.storage_manager import StorageManager


def test_camera_enumeration():
    print("Test wykrywania urządzeń wideo...")
    cameras = enumerate_cameras()
    assert len(cameras) > 0, "Lista kamer nie powinna być pusta"

    types = [c.device_type for c in cameras]
    print(f"Wykryte kamery ({len(cameras)}):")
    for c in cameras:
        print(f"  - [{c.device_type.upper()}] {c.name} (ID: {c.device_id}, Index: {c.index})")

    assert "luxonis" in types, "Opcja Luxonis powinna być dostępna na liście"
    assert "opencv" in types, "Przynajmniej jedna kamera USB/UVC powinna być dostępna"
    print("[OK] Test wykrywania urządzeń wideo powiódł się!\n")


def test_camera_switching_in_gui():
    print("Test przełączania kamer w MainWindow...")
    storage = StorageManager(output_dir="tests_captures", config_path="test_config.json")

    window = MainWindow(storage_manager=storage)
    assert window.camera_worker is not None, "CameraWorker powinien być zainicjalizowany"

    initial_cam = window._current_device_info
    print(f"Początkowa aktywna kamera: {initial_cam.name} ({initial_cam.device_id})")

    # Znajdź inną dostępną kamerę na liście do testu przełączenia
    other_cam = None
    for cam in window.available_cameras:
        if cam.device_id != initial_cam.device_id:
            other_cam = cam
            break

    if other_cam:
        print(f"Próba przełączenia na: {other_cam.name} ({other_cam.device_id})...")
        window._on_camera_source_changed(other_cam)
        app = QApplication.instance()
        if app:
            app.processEvents()
        assert window._current_device_info.device_id == other_cam.device_id, "Identyfikator aktywnej kamery nie zmienił się"
        assert window.camera_worker is not None, "Nowy CameraWorker powinien działać"
        print(f"[OK] Przełączono pomyślnie na: {window._current_device_info.name}")

    # Czyste zatrzymanie okna i wątków
    window.close()

    app = QApplication.instance()
    if app:
        app.processEvents()

    if os.path.exists("test_config.json"):
        os.remove("test_config.json")
    print("[OK] Test przełączania kamer w GUI powiódł się!\n")


def test_c922_stream_and_barcode():
    print("Test strumienia i rozdzielczości kamery USB (np. Logitech C922 Pro)...")
    cameras = enumerate_cameras()
    c922_dev = None
    for c in cameras:
        if "c922" in c.name.lower() or "logi" in c.name.lower() or "webcam" in c.name.lower():
            c922_dev = c
            break

    if not c922_dev:
        print("[INFO] Brak dedykowanej kamery C922 - pomijanie testu dedykowanego.")
        return

    print(f"Znaleziono kamerę: {c922_dev.name} (ID: {c922_dev.device_id})")
    worker = CameraWorker(device_info=c922_dev)

    received_frames = []
    worker.frame_ready.connect(lambda full, prev: received_frames.append(full))
    worker.start()

    # Czekamy na klatki z kamery
    for _ in range(40):
        app = QApplication.instance()
        if app:
            app.processEvents()
        if len(received_frames) >= 3:
            break
        time.sleep(0.1)

    worker.stop()
    worker.deleteLater()

    assert len(received_frames) > 0, "Kamera C922 powinna dostarczyć co najmniej jedną klatkę"
    frame = received_frames[-1]
    h, w = frame.shape[:2]
    print(f"[OK] Odebrano klatkę z {c922_dev.name}: {w}x{h}")
    assert w >= 1280 and h >= 720, f"Rozdzielczość powinna wynosić co najmniej HD 720p, otrzymano: {w}x{h}"

    from src.barcode_engine import BarcodeEngine
    engine = BarcodeEngine()
    res = engine.detect(frame)
    print(f"[OK] Silnik BarcodeEngine przetworzył klatkę w {res.duration_ms:.1f} ms (wykryto {len(res.barcodes)} kodów).")
    print("[OK] Test kamery C922 powiódł się!\n")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    test_camera_enumeration()
    test_camera_switching_in_gui()
    test_c922_stream_and_barcode()
    print("=== WSZYSTKIE TESTY KAMER ZAKOŃCZONE SUKCESEM! ===")
