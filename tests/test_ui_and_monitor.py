import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from PySide6.QtWidgets import QApplication

from src.barcode_engine import BarcodeItem, DetectionResult
from src.batch_manager import BatchManager
from src.camera_stream import CameraDeviceInfo, CameraWorker
from src.gui.main_window import MainWindow
from src.storage_manager import StorageManager
from src.system_monitor import SystemMonitor


def test_batch_manager_clear_history():
    mgr = BatchManager(confirmation_threshold=3)
    res = DetectionResult(
        barcodes=[BarcodeItem("5901234567893", "EAN13", [], [], (0, 0, 10, 10), True)],
        duration_ms=12.5,
        timestamp=time.time(),
    )
    mgr.process_detection(res)
    mgr.process_detection(res)
    assert len(mgr.history) == 2
    assert mgr.baseline_code == "5901234567893"

    mgr.clear_history()
    assert len(mgr.history) == 0, "clear_history() powinno wyczyścić historię Live Log"
    assert mgr.baseline_code == "5901234567893", "clear_history() nie powinno kasować wzorca partii"
    assert mgr.stats.total_frames_analyzed == 2
    print("[OK] BatchManager.clear_history() czyści Live Log bez naruszania wzorca partii.")


def test_system_monitor_metrics():
    mon = SystemMonitor(watch_path=".")
    stop_evt = threading.Event()

    def _dummy():
        stop_evt.wait(2.0)

    t = threading.Thread(target=_dummy, name="BarcodeWorker", daemon=True)
    t.start()
    try:
        time.sleep(0.15)
        snap = mon.collect()
        assert snap.cpu_count_logical >= 1
        assert 0.0 <= snap.cpu_system_percent <= 100.0
        assert snap.ram_total_mb > 0.0
        assert snap.process_ram_mb > 0.0
        assert snap.os_thread_count >= snap.python_thread_count >= 2
        assert snap.disk_total_gb > 0.0
        names = [ti.name for ti in snap.threads]
        assert "MainThread" in names
        assert "BarcodeWorker" in names
        print(
            f"[OK] SystemMonitor ({snap.backend_name}): CPU={snap.cpu_system_percent:.1f}%, "
            f"ProcRAM={snap.process_ram_mb:.1f} MB, OSThreads={snap.os_thread_count}"
        )
    finally:
        stop_evt.set()
        t.join(timeout=1.0)


def test_ui_clear_log_resource_monitor_and_widget_visibility():
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as tmp:
        tp = Path(tmp)
        storage = StorageManager(output_dir=tp / "captures", config_path=tp / "config.json")
        storage.save_config({
            "opc_ua_enabled": False,
            "detection_interval_s": 0.0,
            "exposure_us": 10000,
            "iso": 100,
            "auto_exposure": False,
            "baseline_code": "5701598026926",
        })
        fake_cam = CameraDeviceInfo(
            device_type="opencv",
            device_id="opencv:0",
            name="Integrated Camera [USB #0]",
            index=0,
            is_available=True,
        )

        with patch("src.gui.main_window.enumerate_cameras", return_value=[fake_cam]), \
             patch.object(CameraWorker, "start", return_value=None), \
             patch.object(CameraWorker, "stop", return_value=None):
            win = MainWindow(storage_manager=storage)
            win.resize(1420, 820)
            win.show()
            app.processEvents()

            # 1. Weryfikacja, że kontrolki nie są zgniecione pionowo
            cc = win.camera_controls
            for widget_name, w in [
                ("combo_cameras", cc.combo_cameras),
                ("btn_refresh_cameras", cc.btn_refresh_cameras),
                ("radio_manual", cc.radio_manual),
                ("radio_auto", cc.radio_auto),
                ("spin_exp", cc.spin_exp),
                ("spin_iso", cc.spin_iso),
                ("combo_interval", cc.combo_interval),
                ("btn_scan_now", cc.btn_scan_now),
                ("spin_x1", cc.spin_x1),
                ("btn_reset_roi", cc.btn_reset_roi),
                ("btn_clear_log", win.batch_panel.btn_clear_log),
                ("btn_system_monitor", win.batch_panel.btn_system_monitor),
            ]:
                assert w.height() >= 20, f"{widget_name} ma zbyt małą wysokość: {w.height()}"
                assert w.width() >= 40, f"{widget_name} ma zbyt małą szerokość: {w.width()}"

            for val, btn in cc._preset_buttons.items():
                assert btn.height() >= 22, f"Przycisk presetu {val} zgnieciony: h={btn.height()}"

            # 2. Weryfikacja natychmiastowego kasowania Live Log
            res = DetectionResult(
                barcodes=[BarcodeItem("5701598026926", "EAN13", [], [], (0, 0, 20, 20), True)],
                duration_ms=18.2,
                timestamp=time.time(),
            )
            win._on_detection_done(res, __import__("numpy").zeros((480, 640, 3), dtype="uint8"))
            app.processEvents()
            assert win.batch_panel.table.rowCount() == 1
            assert len(win.batch_manager.history) == 1

            win.batch_panel.btn_clear_log.click()
            app.processEvents()
            assert len(win.batch_manager.history) == 0, "Kliknięcie przycisku powinno od razu skasować Live Log"
            assert win.batch_panel.table.rowCount() == 0, "Tabela Live Log powinna być pusta po skasowaniu"

            # 3. Weryfikacja okna monitora zasobów (CPU / RAM / wątki)
            win.batch_panel.btn_system_monitor.click()
            app.processEvents()
            assert win.resource_monitor_window is not None
            assert win.resource_monitor_window.isVisible() is True
            assert win.resource_monitor_window.tbl_threads.rowCount() >= 1
            assert "MB" in win.resource_monitor_window.lbl_ram_proc.text()

            win.close()
            app.processEvents()
            assert win.resource_monitor_window.isVisible() is False

    print("[OK] Test GUI kasowania Live Log, monitora zasobów oraz wymiarów kontrolek zakończony pomyślnie.")


if __name__ == "__main__":
    test_batch_manager_clear_history()
    test_system_monitor_metrics()
    test_ui_clear_log_resource_monitor_and_widget_visibility()
    print("\n=== WSZYSTKIE NOWE TESTY UI I MONITORA ZASOBÓW ZAKOŃCZONE SUKCESEM! ===")
