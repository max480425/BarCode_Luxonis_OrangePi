import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import numpy as np
from PySide6.QtWidgets import QApplication

from src.barcode_engine import DetectionResult
from src.camera_stream import CameraDeviceInfo, CameraWorker
from src.gui.main_window import MainWindow
from src.storage_manager import StorageManager


def test_storage_manager_nested_config_path():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cfg_path = tmp_path / "nested_data" / "config.json"
        cap_dir = tmp_path / "captures"
        storage = StorageManager(output_dir=cap_dir, config_path=cfg_path)
        assert cfg_path.parent.is_dir(), "Katalog nadrzędny config.json powinien zostać utworzony"

        storage.save_config({"exposure_us": 500, "opc_ua_port": 4840})
        loaded = storage.load_config()
        assert loaded["exposure_us"] == 500
        assert loaded["opc_ua_port"] == 4840
    print("[OK] StorageManager poprawnie obsługuje zagnieżdżoną ścieżkę config.json.")


def test_mainwindow_snapshot_and_scan_finished_fixes():
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = StorageManager(output_dir=tmp_path / "captures", config_path=tmp_path / "data" / "config.json")
        storage.save_config({
            "opc_ua_enabled": False,
            "detection_interval_s": 0.0,
            "exposure_us": 1200,
            "iso": 400,
            "auto_exposure": False,
        })

        fake_cam = CameraDeviceInfo(
            device_type="opencv",
            device_id="opencv:2",
            name="C922 Pro Stream Webcam [USB #2]",
            index=2,
            is_available=True,
            device_path="/dev/video2",
        )

        with patch("src.gui.main_window.enumerate_cameras", return_value=[fake_cam]), \
             patch.object(CameraWorker, "start", return_value=None), \
             patch.object(CameraWorker, "stop", return_value=None):
            window = MainWindow(storage_manager=storage)

            # 1. Test naprawy notify_scan_finished w _on_detection_done
            window.video_widget.notify_scan_started()
            assert window.video_widget._is_scanning_now is True

            dummy_frame = np.full((1080, 1920, 3), 128, dtype=np.uint8)
            det_res = DetectionResult(
                duration_ms=23.5,
                timestamp=time.time(),
                roi_brightness=128.0,
                roi_sharpness=85.0,
                scan_id=7,
            )
            window._on_detection_done(det_res, dummy_frame)
            assert window.video_widget._is_scanning_now is False, "notify_scan_finished powinno wyłączyć flagę _is_scanning_now"
            assert window.video_widget._last_scan_id == 7
            assert abs(window.video_widget._last_scan_ms - 23.5) < 0.01

            # 2. Test naprawy _save_snapshot_async + notify_snapshot_saved
            window._latest_4k_frame = dummy_frame
            window._on_manual_snapshot()

            deadline = time.monotonic() + 5.0
            while window.video_widget._saved_count_session < 1 and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.02)
            app.processEvents()

            assert storage.session_saved_count == 1, "Snapshot powinien zostać zapisany na dysk"
            full_files = list((tmp_path / "captures").glob("*_full.png"))
            roi_files = list((tmp_path / "captures").glob("*_roi.png"))
            assert len(full_files) == 1 and len(roi_files) == 1
            assert "exp1200us_iso400_manual" in full_files[0].name
            assert window.video_widget._saved_count_session == 1
            assert "RĘCZNY" in window.video_widget._last_saved_toast_text

            # 3. Test dynamicznego portu OPC UA w BatchPanel
            window.batch_panel.update_opc_status(True, "opc.tcp://0.0.0.0:4855/barcode/server")
            assert "4855" in window.batch_panel.lbl_opc_status.text()

            window.close()
            app.processEvents()

    print("[OK] MainWindow poprawnie wykonuje asynchroniczny snapshot i notify_scan_finished.")


def test_camera_resolution_migration_from_windows_config():
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        storage = StorageManager(output_dir=tmp_path / "captures", config_path=tmp_path / "config.json")
        storage.save_config({
            "opc_ua_enabled": False,
            "detection_interval_s": 0.0,
            "camera_device_id": "opencv:0",
            "camera_name": "Integrated Camera [USB #0]",
        })

        opi_cams = [
            CameraDeviceInfo("luxonis", "luxonis:auto", "Luxonis OAK (Auto)", is_available=False),
            CameraDeviceInfo("opencv", "opencv:0", "rkisp_mainpath [USB #0]", index=0, device_path="/dev/video0"),
            CameraDeviceInfo("opencv", "opencv:2", "C922 Pro Stream Webcam [USB #2]", index=2, device_path="/dev/video2"),
        ]

        with patch("src.gui.main_window.enumerate_cameras", return_value=opi_cams), \
             patch.object(CameraWorker, "start", return_value=None), \
             patch.object(CameraWorker, "stop", return_value=None):
            window = MainWindow(storage_manager=storage)
            assert window._current_device_info.device_id == "opencv:2", (
                f"Powinien wybrać Logitech C922 zamiast obcego indeksu 0 z Windows, wybrano: {window._current_device_info}"
            )
            window.close()
            app.processEvents()

    print("[OK] Dobór kamery poprawnie ignoruje starą nazwę z Windows i wybiera C922.")


def test_linux_v4l2_exposure_calculation():
    cam = CameraDeviceInfo("opencv", "opencv:2", "C922 Pro Stream Webcam [USB #2]", index=2, device_path="/dev/video2")
    worker = CameraWorker(device_info=cam, exposure_us=10000, auto_exposure=False)
    fake_cap = MagicMock()

    with patch("src.camera_stream.sys.platform", "linux"):
        worker._apply_opencv_exposure(fake_cap)

    calls = [c.args for c in fake_cap.set.call_args_list]
    assert any(prop_val == 1.0 or prop_val == 1 for _, prop_val in calls)
    # 10000 us / 100 = 100 w jednostkach V4L2
    assert any(prop_val == 100.0 for _, prop_val in calls), f"Oczekiwano V4L2 exp=100.0, otrzymano: {calls}"

    fake_cap.reset_mock()
    with patch("src.camera_stream.sys.platform", "win32"):
        worker._apply_opencv_exposure(fake_cap)
    win_calls = [c.args for c in fake_cap.set.call_args_list]
    assert any(prop_val == -7.0 for _, prop_val in win_calls), f"Oczekiwano DirectShow exp=-7.0, otrzymano: {win_calls}"

    print("[OK] Sterowanie ekspozycją poprawnie rozróżnia V4L2 (Linux) i DirectShow (Windows).")


if __name__ == "__main__":
    test_storage_manager_nested_config_path()
    test_mainwindow_snapshot_and_scan_finished_fixes()
    test_camera_resolution_migration_from_windows_config()
    test_linux_v4l2_exposure_calculation()
    print("\n=== WSZYSTKIE TESTY NAPRAW I PRZENOŚNOŚCI ZAKOŃCZONE SUKCESEM! ===")
