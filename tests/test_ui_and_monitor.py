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


def test_batch_manager_auto_switch_after_10_identical_codes():
    mgr = BatchManager(confirmation_threshold=5, auto_switch_threshold=10)
    mgr.set_baseline("5901111111110", "EAN13")

    def _scan(code: str, fmt: str = "CODE128") -> DetectionResult:
        r = DetectionResult(
            barcodes=[BarcodeItem(code, fmt, [], [], (0, 0, 20, 20), False)],
            duration_ms=10.0,
            timestamp=time.time(),
        )
        mgr.process_detection(r)
        return r

    # 9 kolejnych innych kodów nie powinno jeszcze zmienić bazy
    for i in range(1, 10):
        r = _scan("5902222222220", "EAN13")
        assert mgr.baseline_code == "5901111111110"
        assert mgr.mismatch_consecutive_count == i
        assert r.barcodes[0].is_match is False
        assert mgr.last_auto_switched_code is None
        if i >= 5:
            assert mgr.batch_change_alarm is True

    # Przerwanie serii innym kodem resetuje licznik kandydata do 1
    _scan("5903333333330", "EAN13")
    assert mgr.baseline_code == "5901111111110"
    assert mgr.candidate_mismatch_code == "5903333333330"
    assert mgr.mismatch_consecutive_count == 1

    # 9 razy nowy kod 5904444444440 -> jeszcze nie zmienia, 10. raz -> automatycznie ustawia bazę!
    for _ in range(9):
        _scan("5904444444440", "CODE128")
        assert mgr.baseline_code == "5901111111110"

    r10 = _scan("5904444444440", "CODE128")
    assert mgr.baseline_code == "5904444444440"
    assert mgr.baseline_format == "CODE128"
    assert mgr.batch_change_alarm is False
    assert mgr.mismatch_consecutive_count == 0
    assert mgr.candidate_mismatch_code is None
    assert mgr.last_auto_switched_code == "5904444444440"
    assert r10.barcodes[0].is_match is True

    # Kolejny skan tego samego kodu jest już zwykłym dopasowaniem
    r11 = _scan("5904444444440", "CODE128")
    assert mgr.last_auto_switched_code is None
    assert r11.barcodes[0].is_match is True

    # Gdy auto_switch_threshold = 0, automatyczna zmiana jest wyłączona
    mgr.auto_switch_threshold = 0
    for _ in range(15):
        _scan("5905555555550", "EAN13")
    assert mgr.baseline_code == "5904444444440"
    assert mgr.batch_change_alarm is True
    print("[OK] BatchManager automatycznie zmienia kod bazowy po 10 takich samych wykrytych kodach.")


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

            # 3. Weryfikacja auto-zmiany kodu bazowego w MainWindow po 10 odczytach
            assert win.batch_panel.spin_auto_switch.value() == 10
            dummy_img = __import__("numpy").zeros((480, 640, 3), dtype="uint8")
            for idx in range(10):
                new_res = DetectionResult(
                    barcodes=[BarcodeItem("5909998887771", "EAN13", [], [], (0, 0, 20, 20), False)],
                    duration_ms=14.0,
                    timestamp=time.time(),
                    scan_id=idx + 10,
                )
                win._on_detection_done(new_res, dummy_img)
                app.processEvents()

            assert win.batch_manager.baseline_code == "5909998887771"
            assert "5909998887771" in win.batch_panel.lbl_batch_code.text()
            assert win.batch_panel.alarm_box.isVisible() is False

            # 4. Weryfikacja okna monitora zasobów (CPU / RAM / wątki)
            win.batch_panel.btn_system_monitor.click()
            app.processEvents()
            assert win.resource_monitor_window is not None
            assert win.resource_monitor_window.isVisible() is True
            assert win.resource_monitor_window.tbl_threads.rowCount() >= 1
            assert "MB" in win.resource_monitor_window.lbl_ram_proc.text()

            win.close()
            app.processEvents()
            assert win.resource_monitor_window.isVisible() is False

    print("[OK] Test GUI kasowania Live Log, auto-zmiany bazy (10x), monitora zasobów oraz wymiarów kontrolek zakończony pomyślnie.")


def test_usb_disconnect_clears_frame_stops_scanning_and_reconnects():
    """Weryfikuje czyszczenie zamrożonej klatki po odpięciu USB oraz automatyczne ponowne połączenie."""
    import numpy as np

    app = QApplication.instance() or QApplication(sys.argv)

    # 1. Test automatycznego ponownego połączenia w CameraWorker._run_opencv
    cam = CameraDeviceInfo("opencv", "opencv:0", "C922 Pro Stream Webcam [USB #0]", index=0)
    worker = CameraWorker(device_info=cam, auto_exposure=True)

    statuses = []
    frames_cnt = [0]
    worker.status_changed.connect(lambda conn, *_: statuses.append(conn))
    worker.frame_ready.connect(lambda *_: frames_cnt.__setitem__(0, frames_cnt[0] + 1))

    good_frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    attempt_num = [0]

    class FakeCap:
        def __init__(self, attempt: int):
            self.attempt = attempt
            self.reads = 0
            self.released = False

        def isOpened(self):
            return not self.released

        def set(self, *_args):
            return True

        def get(self, prop):
            import cv2
            if prop == cv2.CAP_PROP_FRAME_WIDTH:
                return 640.0
            if prop == cv2.CAP_PROP_FRAME_HEIGHT:
                return 480.0
            return 0.0

        def read(self):
            self.reads += 1
            if self.attempt == 1:
                # 3 dobre klatki, a potem symulacja odpięcia kamery (zwraca False)
                if self.reads <= 3:
                    return True, good_frame
                return False, None
            # Po ponownym podpięciu dostarcza z powrotem klatki, po 2 zatrzymujemy test
            if self.reads <= 2:
                return True, good_frame
            worker._running = False
            return True, good_frame

        def release(self):
            self.released = True

    def fake_video_capture(*_args):
        attempt_num[0] += 1
        return FakeCap(attempt_num[0])

    with patch("src.camera_stream.sys.platform", "win32"), \
         patch("src.camera_stream.cv2.VideoCapture", side_effect=fake_video_capture), \
         patch.object(worker, "_sleep_interruptible", lambda _s: time.sleep(0.05)):
        worker._running = True
        worker._run_opencv()

    app.processEvents()
    assert attempt_num[0] >= 2, f"CameraWorker powinien podjąć ponowną próbę połączenia, prób={attempt_num[0]}"
    assert True in statuses and False in statuses, f"Oczekiwano sekwencji True -> False -> True, było: {statuses}"
    assert statuses.count(True) >= 2, f"Po ponownym podpięciu powinien ponownie wyemitować connected=True: {statuses}"
    assert frames_cnt[0] >= 4

    # 2. Test zmiany numeru /dev/videoN po hot-plugu na Linuksie
    cam_linux = CameraDeviceInfo(
        "opencv", "opencv:2", "C922 Pro Stream Webcam [USB #2]", index=2, device_path="/dev/video2"
    )
    worker_linux = CameraWorker(device_info=cam_linux)
    replugged_list = [
        CameraDeviceInfo(
            "opencv", "opencv:4", "C922 Pro Stream Webcam [USB #4]", index=4, device_path="/dev/video4"
        )
    ]
    with patch("src.camera_stream.sys.platform", "linux"), \
         patch("src.camera_stream._enumerate_linux_v4l2_cameras", return_value=replugged_list):
        resolved = worker_linux._resolve_opencv_target()
        assert resolved is not None
        assert resolved[0] == "/dev/video4" and resolved[1] == 4

    # 3. Test w MainWindow: po odpięciu znika zamrożony kadr i detekcja NIE skanuje starej klatki
    with tempfile.TemporaryDirectory() as tmp:
        tp = Path(tmp)
        storage = StorageManager(output_dir=tp / "captures", config_path=tp / "config.json")
        storage.save_config({"opc_ua_enabled": False, "detection_interval_s": 0.0})
        with patch("src.gui.main_window.enumerate_cameras", return_value=[cam]), \
             patch.object(CameraWorker, "start", return_value=None), \
             patch.object(CameraWorker, "stop", return_value=None):
            win = MainWindow(storage_manager=storage)
            win._on_camera_status_changed(True, cam.name, "USB #0", "640×480")
            win._on_frame_ready(good_frame, good_frame)
            assert win._latest_4k_frame is not None
            assert win.video_widget._image is not None
            assert win.zoom_widget._image is not None

            # Symulacja odpięcia kamery
            win._on_camera_status_changed(False, cam.name, "USB #0", "Utracono sygnał")
            assert win._latest_4k_frame is None, "Po odłączeniu kamery ostatnia klatka musi zostać wyczyszczona"
            assert win.video_widget._image is None, "Podgląd wideo powinien zostać wyczyszczony"
            assert win.zoom_widget._image is None, "Podgląd Zoom 1:1 powinien zostać wyczyszczony"
            assert win._is_detecting is False

            # Wywołanie tyknięcia skanera nie powinno uruchamiać detekcji
            win._on_detection_tick()
            assert win._is_detecting is False

            # Ponowne podłączenie kamery przywraca strumień i możliwość skanowania
            win._on_camera_status_changed(True, cam.name, "USB #0", "640×480")
            win._on_frame_ready(good_frame, good_frame)
            assert win._latest_4k_frame is not None
            assert win.video_widget._image is not None

            win.close()
            app.processEvents()

    print("[OK] Test odpięcia/podpięcia kamery USB (czyszczenie klatki + auto-reconnect + zmiana /dev/videoN) zaliczony!")


def test_roi_and_settings_persist_immediately_without_close():
    """Weryfikuje, że zmiana ROI myszką lub polami X/Y zapisuje config.json od razu (nawet przed restartem bez close)."""
    app = QApplication.instance() or QApplication(sys.argv)
    cam = CameraDeviceInfo("opencv", "opencv:0", "Integrated Camera [USB #0]", index=0)

    with tempfile.TemporaryDirectory() as tmp:
        tp = Path(tmp)
        storage = StorageManager(output_dir=tp / "captures", config_path=tp / "data" / "config.json")
        storage.save_config({
            "opc_ua_enabled": False,
            "detection_interval_s": 0.0,
            "roi": [0.1, 0.2, 0.9, 0.8],
            "exposure_us": 5000,
            "auto_exposure": False,
        })

        with patch("src.gui.main_window.enumerate_cameras", return_value=[cam]), \
             patch.object(CameraWorker, "start", return_value=None), \
             patch.object(CameraWorker, "stop", return_value=None):
            win = MainWindow(storage_manager=storage)

            # 1. Zmiana ROI myszką -> natychmiastowy zapis do config.json bez wywoływania win.close()
            win.video_widget.roi_changed.emit(0.15, 0.30, 0.75, 0.85)
            app.processEvents()

            loaded1 = storage.load_config()
            assert [round(v, 3) for v in loaded1["roi"]] == [0.15, 0.30, 0.75, 0.85], (
                f"ROI z myszki nie zapisało się od razu w config.json: {loaded1['roi']}"
            )

            # 2. Zmiana ROI z kontrolek numerycznych + ekspozycji -> zapis po krótkim debounce (<0.5s)
            win.camera_controls.spin_x1.setValue(25.0)
            win.camera_controls.set_exposure_us(2000)
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                app.processEvents()
                loaded2 = storage.load_config()
                if round(loaded2["roi"][0], 3) == 0.25 and loaded2["exposure_us"] == 2000:
                    break
                time.sleep(0.03)

            loaded2 = storage.load_config()
            assert round(loaded2["roi"][0], 3) == 0.25, f"ROI ze spinboxa nie zapisało się: {loaded2['roi']}"
            assert loaded2["exposure_us"] == 2000, f"Ekspozycja nie zapisała się: {loaded2['exposure_us']}"

            win.close()
            app.processEvents()

    print("[OK] Zmiana ROI i parametrów zapisuje się natychmiast do config.json (odporne na twardy restart).")


if __name__ == "__main__":
    test_batch_manager_clear_history()
    test_batch_manager_auto_switch_after_10_identical_codes()
    test_system_monitor_metrics()
    test_ui_clear_log_resource_monitor_and_widget_visibility()
    test_usb_disconnect_clears_frame_stops_scanning_and_reconnects()
    test_roi_and_settings_persist_immediately_without_close()
    print("\n=== WSZYSTKIE NOWE TESTY UI I MONITORA ZASOBÓW ZAKOŃCZONE SUKCESEM! ===")
