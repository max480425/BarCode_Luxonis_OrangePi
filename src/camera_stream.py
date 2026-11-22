from __future__ import annotations

import logging
import math
import os
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import cv2
import depthai as dai
import numpy as np
from PySide6.QtCore import QMutex, QObject, QThread, Signal

logger = logging.getLogger("camera_stream")


@dataclass
class CameraDeviceInfo:
    """Informacje o wykrytym urządzeniu wideo (Luxonis lub USB UVC)."""
    device_type: str      # 'luxonis' lub 'opencv'
    device_id: str        # np. 'luxonis:auto', 'opencv:1'
    name: str             # Czytelna nazwa dla interfejsu użytkownika
    index: int = 0        # Indeks urządzenia dla OpenCV cv2.VideoCapture
    mxid: str = ""        # Identyfikator MxID dla modułów Luxonis
    is_available: bool = True
    device_path: str = ""  # Ścieżka urządzenia w Linuksie, np. '/dev/video0'


def _enumerate_linux_v4l2_cameras() -> List[CameraDeviceInfo]:
    """
    Bezpośrednie wykrywanie kamer V4L2 w Linuksie przez /dev/video* oraz ioctl VIDIOC_QUERYCAP.
    Odfiltrowuje węzły metadanych UVC oraz sprzętowe kodeki VPU/ISP (np. RK3588 hantro/rkvdec),
    zwracając wyłącznie rzeczywiste urządzenia przechwytujące obraz (V4L2_CAP_VIDEO_CAPTURE).
    """
    import fcntl

    VIDIOC_QUERYCAP = 0x80685600
    V4L2_CAP_VIDEO_CAPTURE = 0x00000001
    V4L2_CAP_DEVICE_CAPS = 0x80000000

    uvc_devices: List[CameraDeviceInfo] = []
    other_devices: List[CameraDeviceInfo] = []

    video_nodes = sorted(
        Path("/dev").glob("video[0-9]*"),
        key=lambda p: int(p.name[5:]) if p.name[5:].isdigit() else 9999
    )

    for node in video_nodes:
        idx_str = node.name[5:]
        if not idx_str.isdigit():
            continue
        idx = int(idx_str)
        dev_path = str(node)

        fd = None
        try:
            fd = os.open(dev_path, os.O_RDONLY | os.O_NONBLOCK)
            buf = bytearray(104)
            fcntl.ioctl(fd, VIDIOC_QUERYCAP, buf)
            driver_b, card_b, _bus_b, _ver, caps, dev_caps = struct.unpack("16s32s32sIII", buf[:92])
            eff_caps = dev_caps if (caps & V4L2_CAP_DEVICE_CAPS) else caps

            if not (eff_caps & V4L2_CAP_VIDEO_CAPTURE):
                continue

            driver = driver_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
            card = card_b.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip() or f"Kamera USB #{idx}"

            info = CameraDeviceInfo(
                device_type="opencv",
                device_id=f"opencv:{idx}",
                name=f"{card} [USB #{idx}]",
                index=idx,
                is_available=True,
                device_path=dev_path
            )
            if driver == "uvcvideo":
                uvc_devices.append(info)
            else:
                other_devices.append(info)
        except OSError as e:
            logger.debug("Pominięto węzeł %s podczas skanowania V4L2: %s", dev_path, e)
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

    return uvc_devices + other_devices


def enumerate_cameras() -> List[CameraDeviceInfo]:
    """
    Wykrywa wszystkie podłączone kamery:
    1. Kamery Luxonis OAK (przez DepthAI)
    2. Standardowe kamery USB / UVC (Logitech C922/C270, kamery wbudowane przez V4L2 na Linuksie lub Qt/DirectShow na Windows)
    """
    devices: List[CameraDeviceInfo] = []

    # 1. Wykrywanie kamer Luxonis OAK
    try:
        lux_devices = dai.Device.getAllAvailableDevices()
        if lux_devices:
            for dev in lux_devices:
                name_str = dev.name or "OAK-1"
                short_mxid = dev.deviceId[:8] if dev.deviceId else "OAK"
                devices.append(CameraDeviceInfo(
                    device_type="luxonis",
                    device_id=f"luxonis:{dev.deviceId}",
                    name=f"Luxonis {name_str} ({short_mxid})",
                    mxid=dev.deviceId,
                    is_available=True
                ))
        else:
            # Dodajemy opcję Luxonis Auto-Connect nawet jeśli urządzenie nie jest teraz podłączone
            devices.append(CameraDeviceInfo(
                device_type="luxonis",
                device_id="luxonis:auto",
                name="Luxonis OAK (DepthAI — Auto-Connect)",
                is_available=False
            ))
    except Exception as e:
        logger.warning("Błąd wykrywania urządzeń Luxonis DepthAI: %s", e)
        devices.append(CameraDeviceInfo(
            device_type="luxonis",
            device_id="luxonis:auto",
            name="Luxonis OAK (DepthAI — Auto-Connect)",
            is_available=False
        ))

    # 2. Wykrywanie kamer USB UVC
    if sys.platform.startswith("linux"):
        v4l2_cams = _enumerate_linux_v4l2_cameras()
        if v4l2_cams:
            devices.extend(v4l2_cams)
            return devices

    try:
        from PySide6.QtMultimedia import QMediaDevices
        qt_devices = QMediaDevices.videoInputs()
        for idx, dev in enumerate(qt_devices):
            desc = dev.description().strip() or f"Kamera USB #{idx}"
            dev_idx = idx
            dev_path = ""
            if sys.platform.startswith("linux"):
                raw_id = bytes(dev.id()).decode("utf-8", errors="ignore").strip()
                m = re.match(r"^/dev/video(\d+)$", raw_id)
                if m:
                    dev_idx = int(m.group(1))
                    dev_path = raw_id
            devices.append(CameraDeviceInfo(
                device_type="opencv",
                device_id=f"opencv:{dev_idx}",
                name=f"{desc} [USB #{dev_idx}]",
                index=dev_idx,
                is_available=True,
                device_path=dev_path
            ))
    except Exception as e:
        logger.warning("Błąd wykrywania kamer przez QMediaDevices (%s), sprawdzanie indeksów OpenCV...", e)
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else (cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY)
        for idx in range(4):
            cap = cv2.VideoCapture(idx, backend)
            if cap.isOpened():
                devices.append(CameraDeviceInfo(
                    device_type="opencv",
                    device_id=f"opencv:{idx}",
                    name=f"Kamera USB #{idx}",
                    index=idx,
                    is_available=True,
                    device_path=f"/dev/video{idx}" if sys.platform.startswith("linux") else ""
                ))
                cap.release()

    return devices


class CameraWorker(QThread):
    """
    Uniwersalny wątek roboczy akwizycji wideo obsługujący:
    - Przemysłowe kamery Luxonis OAK (DepthAI v3 API, matryca IMX378 12MP/4K)
    - Kamery internetowe USB UVC (np. Logitech C270, C310, C920, DirectShow/OpenCV)
    """
    # Sygnały do komunikacji z wątkiem głównym GUI
    frame_ready = Signal(object, object)          # (full_frame, preview_scaled_frame)
    status_changed = Signal(bool, str, str, str)  # (connected, model_name, device_id, backend_info)
    fps_updated = Signal(float)                    # Bieżący FPS kamery
    error_occurred = Signal(str)                  # Komunikat o błędzie

    def __init__(
        self,
        device_info: Optional[CameraDeviceInfo] = None,
        camera_type: str = "luxonis",
        device_index: int = 0,
        device_name: str = "",
        exposure_us: int = 20000,
        iso: int = 100,
        auto_exposure: bool = True,
        parent: Optional[QObject] = None
    ) -> None:
        super().__init__(parent)
        if device_info is not None:
            self.device_info = device_info
            self.camera_type = device_info.device_type
            self.device_index = device_info.index
            self.device_name = device_info.name
        else:
            self.camera_type = camera_type
            self.device_index = device_index
            self.device_name = device_name or ("Luxonis OAK" if camera_type == "luxonis" else f"Kamera USB #{device_index}")
            self.device_info = CameraDeviceInfo(
                device_type=camera_type,
                device_id=f"{camera_type}:{device_index}",
                name=self.device_name,
                index=device_index
            )

        self.exposure_us = exposure_us
        self.iso = iso
        self.auto_exposure = auto_exposure

        self._running = False
        self._mutex = QMutex()

        # Flagi zmian parametrów z GUI
        self._pending_exposure_update = False
        self._new_exposure_us = exposure_us
        self._new_iso = iso
        self._new_auto_exposure = auto_exposure

        # Ostatnia klatka w pamięci
        self._latest_full_frame: Optional[np.ndarray] = None
        self._latest_frame_mutex = QMutex()

        # Uchwyt kamery OpenCV do natychmiastowego zwalniania przy stop()
        self._cap: Optional[cv2.VideoCapture] = None

        # Zasoby Luxonis
        self._pipeline: Optional[dai.Pipeline] = None
        self._device: Optional[dai.Device] = None

    def get_latest_frame(self) -> Optional[np.ndarray]:
        """Bezpieczne pobranie kopii ostatniej pełnej klatki."""
        self._latest_frame_mutex.lock()
        try:
            if self._latest_full_frame is not None:
                return self._latest_full_frame.copy()
            return None
        finally:
            self._latest_frame_mutex.unlock()

    def _clear_latest_frame(self) -> None:
        """Czyści bufor ostatniej klatki po utracie połączenia z kamerą."""
        self._latest_frame_mutex.lock()
        try:
            self._latest_full_frame = None
        finally:
            self._latest_frame_mutex.unlock()

    def _sleep_interruptible(self, seconds: float) -> None:
        """Usypia wątek w krótkich krokach, aby stop() przerywał oczekiwanie natychmiast."""
        deadline = time.monotonic() + max(0.0, seconds)
        while self._running and time.monotonic() < deadline:
            time.sleep(min(0.1, max(0.01, deadline - time.monotonic())))

    def update_exposure(self, exposure_us: int, iso: int, auto_exposure: bool = False) -> None:
        """Asynchroniczne zlecenie aktualizacji parametrów naświetlania kamery."""
        self._mutex.lock()
        self._new_exposure_us = exposure_us
        self._new_iso = iso
        self._new_auto_exposure = auto_exposure
        self._pending_exposure_update = True
        self._mutex.unlock()

    def stop(self) -> None:
        """Bezpieczne zatrzymanie wątku akwizycji."""
        self._running = False
        self.wait(4000)

    def run(self) -> None:
        self._running = True
        if self.camera_type == "opencv":
            self._run_opencv()
        else:
            self._run_luxonis()

    # =========================================================================
    # POTOK: KAMERA USB UVC (np. Logitech C922 / C270, DirectShow / V4L2)
    # =========================================================================
    def _apply_opencv_exposure(self, cap: cv2.VideoCapture) -> None:
        """Sterowanie parametrami ekspozycji kamery USB (DirectShow na Windows / V4L2 na Linuksie)."""
        try:
            if sys.platform.startswith("linux"):
                if self.auto_exposure:
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
                    logger.info("Kamera USB V4L2 [%s]: Auto Exposure WŁĄCZONE", self.device_name)
                else:
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
                    v4l2_exp = max(1, round(self.exposure_us / 100.0))
                    cap.set(cv2.CAP_PROP_EXPOSURE, float(v4l2_exp))
                    logger.info(
                        "Kamera USB V4L2 [%s]: Manual Exp=%d µs -> CAP_PROP_EXPOSURE=%d (x100 µs)",
                        self.device_name, self.exposure_us, v4l2_exp
                    )
            else:
                if self.auto_exposure:
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)
                    logger.info("Kamera USB [%s]: Auto Exposure WŁĄCZONE", self.device_name)
                else:
                    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
                    sec = max(1e-6, self.exposure_us / 1_000_000.0)
                    exp_val = max(-13, min(-1, round(math.log2(sec))))
                    cap.set(cv2.CAP_PROP_EXPOSURE, float(exp_val))
                    logger.info(
                        "Kamera USB [%s]: Manual Exp=%d µs -> CAP_PROP_EXPOSURE=%.1f",
                        self.device_name, self.exposure_us, exp_val
                    )
        except Exception as e:
            logger.debug("Sterowanie ekspozycją USB niedostępne w sterowniku: %s", e)

    def _resolve_opencv_target(self) -> Optional[Tuple[object, int, str]]:
        """
        Wyznacza aktualny cel otwarcia dla kamery USB (OpenCV).
        W Linuksie po odpięciu i ponownym wpięciu kamera może otrzymać inny numer /dev/videoN —
        wtedy odszukujemy ją ponownie po nazwie karty V4L2.
        """
        if not sys.platform.startswith("linux"):
            return (self.device_index, self.device_index, self.device_name)

        base_name = self.device_name.split("[")[0].strip().lower()
        v4l2_cams = _enumerate_linux_v4l2_cameras()

        # 1. Dokładna ścieżka /dev/videoX o ile zgadza się nazwa urządzenia (lub brak nazwy)
        wanted_path = self.device_info.device_path or f"/dev/video{self.device_index}"
        for cam in v4l2_cams:
            if cam.device_path == wanted_path:
                cam_base = cam.name.split("[")[0].strip().lower()
                if not base_name or base_name.startswith("kamera usb") or base_name == cam_base:
                    return (cam.device_path, cam.index, cam.name)

        # 2. Kamera wróciła pod nowym numerem /dev/videoY po hot-plugu USB
        if base_name and not base_name.startswith("kamera usb"):
            for cam in v4l2_cams:
                cam_base = cam.name.split("[")[0].strip().lower()
                if base_name == cam_base or base_name in cam_base:
                    self.device_info.device_path = cam.device_path
                    self.device_info.index = cam.index
                    self.device_info.device_id = cam.device_id
                    self.device_info.name = cam.name
                    self.device_index = cam.index
                    self.device_name = cam.name
                    logger.info(
                        "Wykryto ponowne podłączenie kamery USB pod nowym węzłem: %s (%s)",
                        cam.name, cam.device_path
                    )
                    return (cam.device_path, cam.index, cam.name)
            return None

        if os.path.exists(wanted_path):
            return (wanted_path, self.device_index, self.device_name)
        return None

    def _run_opencv(self) -> None:
        is_linux = sys.platform.startswith("linux")
        logger.info("Uruchamianie pętli akwizycji OpenCV (z auto-reconnect) dla: %s...", self.device_name)

        while self._running:
            resolved = self._resolve_opencv_target()
            if resolved is None:
                self._clear_latest_frame()
                self.fps_updated.emit(0.0)
                self.status_changed.emit(
                    False,
                    self.device_name,
                    f"USB #{self.device_index}",
                    "Odłączono — oczekiwanie na ponowne podłączenie"
                )
                self._sleep_interruptible(1.5)
                continue

            target, active_idx, active_name = resolved
            logger.info("Próba otwarcia kamery USB: %s (target=%s)...", active_name, target)

            if is_linux:
                self._cap = cv2.VideoCapture(target, cv2.CAP_V4L2)
                if not self._cap.isOpened():
                    logger.warning("Nie udało się otworzyć z backendem CAP_V4L2, próba backendu domyślnego...")
                    self._cap = cv2.VideoCapture(target)
            elif sys.platform == "win32":
                self._cap = cv2.VideoCapture(active_idx, cv2.CAP_DSHOW)
                if not self._cap.isOpened():
                    logger.warning("Nie udało się otworzyć z backendem CAP_DSHOW, próba backendu domyślnego...")
                    self._cap = cv2.VideoCapture(active_idx)
            else:
                self._cap = cv2.VideoCapture(active_idx)

            cap = self._cap
            if not cap.isOpened():
                try:
                    cap.release()
                except Exception:
                    pass
                self._cap = None
                self._clear_latest_frame()
                self.fps_updated.emit(0.0)
                logger.warning("Nie można otworzyć kamery USB: %s (%s) — ponowna próba za 1.5 s.", active_name, target)
                self.status_changed.emit(False, active_name, f"USB #{active_idx}", "Oczekiwanie na kamerę...")
                self._sleep_interruptible(1.5)
                continue

            # Na Linuksie (V4L2) wymuszamy MJPG przed ustawieniem 1080p, inaczej kamery USB 2.0 (np. C922) spadają do 5 FPS w YUYV.
            if is_linux:
                try:
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                except Exception:
                    pass

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
            if is_linux:
                try:
                    cap.set(cv2.CAP_PROP_FPS, 30)
                except Exception:
                    pass

            try:
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)
            except Exception:
                pass

            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            # Ustawienie początkowej ekspozycji
            self._apply_opencv_exposure(cap)

            fps_start = time.monotonic()
            fps_counter = 0
            first_frame_seen = False
            consecutive_failures = 0
            fail_started_at: Optional[float] = None
            disconnected_reason = ""

            try:
                while self._running and cap.isOpened():
                    # Obsługa żądania zmiany ekspozycji z GUI
                    self._mutex.lock()
                    if self._pending_exposure_update:
                        self.exposure_us = self._new_exposure_us
                        self.iso = self._new_iso
                        self.auto_exposure = self._new_auto_exposure
                        self._pending_exposure_update = False
                        self._mutex.unlock()
                        self._apply_opencv_exposure(cap)
                    else:
                        self._mutex.unlock()

                    read_t0 = time.monotonic()
                    ret, full_frame = cap.read()
                    if not ret or full_frame is None or full_frame.size == 0:
                        now_fail = time.monotonic()
                        read_dt = now_fail - read_t0
                        if fail_started_at is None:
                            fail_started_at = now_fail
                        consecutive_failures += 1
                        fail_elapsed = now_fail - fail_started_at

                        node_missing = (
                            is_linux
                            and isinstance(target, str)
                            and target.startswith("/dev/")
                            and not os.path.exists(target)
                        )
                        lost_after_stream = first_frame_seen and (
                            consecutive_failures >= 15
                            or (consecutive_failures >= 3 and fail_elapsed >= 1.2)
                            or read_dt >= 2.0
                        )
                        failed_at_start = (not first_frame_seen) and (
                            consecutive_failures >= 30 or (consecutive_failures >= 5 and fail_elapsed >= 2.5)
                        )
                        if node_missing or lost_after_stream or failed_at_start:
                            disconnected_reason = "Utracono sygnał / kamera odłączona"
                            logger.warning(
                                "Utracono połączenie z kamerą USB %s (%s): failures=%d, elapsed=%.2fs, node_missing=%s",
                                active_name, target, consecutive_failures, fail_elapsed, node_missing
                            )
                            break

                        time.sleep(0.02)
                        continue

                    consecutive_failures = 0
                    fail_started_at = None

                    if not first_frame_seen:
                        first_frame_seen = True
                        logger.info("Kamera USB %s połączona. Rozdzielczość robocza: %dx%d", active_name, actual_w, actual_h)
                        self.status_changed.emit(True, active_name, f"USB #{active_idx}", f"{actual_w}×{actual_h}")
                        if is_linux and not self.auto_exposure:
                            self._apply_opencv_exposure(cap)

                    # Zapis do wewnętrznego bufora
                    self._latest_frame_mutex.lock()
                    self._latest_full_frame = full_frame
                    self._latest_frame_mutex.unlock()

                    # Skalowanie podglądu z zachowaniem proporcji dla płynnego renderowania UI
                    fh, fw = full_frame.shape[:2]
                    if fw > 1280:
                        prev_w = 1280
                        prev_h = int(round(prev_w * (fh / float(fw))))
                        preview_frame = cv2.resize(full_frame, (prev_w, prev_h), interpolation=cv2.INTER_LINEAR)
                    else:
                        preview_frame = full_frame.copy()

                    # Emisja sygnału klatki
                    self.frame_ready.emit(full_frame, preview_frame)

                    # Pomiar FPS
                    fps_counter += 1
                    elapsed = time.monotonic() - fps_start
                    if elapsed >= 1.0:
                        cur_fps = fps_counter / elapsed
                        self.fps_updated.emit(cur_fps)
                        fps_counter = 0
                        fps_start = time.monotonic()

            except Exception as e:
                disconnected_reason = f"Błąd strumienia: {str(e)}"
                logger.error("Błąd strumienia kamery USB %s: %s", active_name, e)
                self.error_occurred.emit(disconnected_reason)
            finally:
                if self._cap is not None:
                    try:
                        self._cap.release()
                    except Exception:
                        pass
                    self._cap = None
                self._clear_latest_frame()
                self.fps_updated.emit(0.0)
                status_msg = "Odłączono" if not self._running else (disconnected_reason or "Ponowne łączenie...")
                logger.info("Kamera USB %s została zwolniona (%s).", active_name, status_msg)
                self.status_changed.emit(False, active_name, f"USB #{active_idx}", status_msg)

            if self._running:
                self._sleep_interruptible(1.2)

        logger.info("Wątek akwizycji OpenCV dla %s został zatrzymany.", self.device_name)

    # =========================================================================
    # POTOK: LUXONIS OAK (DepthAI v3)
    # =========================================================================
    def _send_luxonis_camera_control(self, ctrl_queue: dai.InputQueue) -> None:
        """Wysyła obiekt CameraControl do kamery Luxonis."""
        ctrl = dai.CameraControl()
        try:
            ctrl.setSharpness(4)
            ctrl.setLumaDenoise(0)
        except Exception:
            pass

        if self.auto_exposure:
            ctrl.setAutoExposureEnable()
            logger.info("Luxonis CameraControl: Auto Exposure ENABLE (Sharpness=4, LumaDenoise=0)")
        else:
            ctrl.setManualExposure(int(self.exposure_us), int(self.iso))
            logger.info("Luxonis CameraControl: Manual Exp=%d µs, ISO=%d (Sharpness=4, LumaDenoise=0)", self.exposure_us, self.iso)
        ctrl_queue.send(ctrl)

    def _run_luxonis(self) -> None:
        logger.info("Uruchamianie wątku akwizycji kamery Luxonis DepthAI...")

        device: Optional[dai.Device] = None
        pipeline: Optional[dai.Pipeline] = None

        while self._running:
            devices = dai.Device.getAllAvailableDevices()
            if not devices:
                self.status_changed.emit(False, "Brak kamery Luxonis", "-", "-")
                for _ in range(10):
                    if not self._running:
                        break
                    time.sleep(0.1)
                continue

            wanted_mxid = getattr(self.device_info, "mxid", "")
            target_info = None
            if wanted_mxid:
                for d in devices:
                    if d.deviceId == wanted_mxid:
                        target_info = d
                        break
                if target_info is None:
                    self.status_changed.emit(False, f"Oczekiwanie na OAK ({wanted_mxid[:8]})", wanted_mxid, "-")
                    for _ in range(10):
                        if not self._running:
                            break
                        time.sleep(0.1)
                    continue
            else:
                target_info = devices[0]

            logger.info("Próba połączenia z urządzeniem Luxonis: %s (MxID: %s)", target_info.name, target_info.deviceId)

            try:
                device = dai.Device(target_info)
                self._device = device
                usb_speed_str = str(device.getUsbSpeed()).replace("UsbSpeed.", "")
                if "HIGH" in usb_speed_str and "SUPER" not in usb_speed_str:
                    logger.warning("Kamera OAK podłączona przez USB 2.0 (%s)! Dla 12MP zalecany jest port i kabel USB 3.0.", usb_speed_str)
                device_name = device.getDeviceName()
                mxid = target_info.deviceId

                logger.info("Połączono z %s (USB: %s, MxID: %s)", device_name, usb_speed_str, mxid)
                self.status_changed.emit(True, device_name, mxid, usb_speed_str)

                # Budowa potoku DepthAI v3
                pipeline = dai.Pipeline(device)
                self._pipeline = pipeline
                cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)

                # Pełna natywna rozdzielczość matrycy IMX378 (12 MP: 4000x3000) w formacie NV12
                out_stream = cam.requestFullResolutionOutput(type=dai.ImgFrame.Type.NV12, fps=15.0)
                video_queue = out_stream.createOutputQueue(maxSize=2, blocking=False)
                ctrl_queue = cam.inputControl.createInputQueue()

                pipeline.start()
                logger.info("Pipeline DepthAI v3 wystartował.")

                # Ustawienie początkowej ekspozycji
                self._send_luxonis_camera_control(ctrl_queue)

                fps_start = time.monotonic()
                fps_counter = 0
                total_frames = 0
                last_frame_mono = time.monotonic()

                while self._running and pipeline.isRunning():
                    if device.isClosed():
                        logger.warning("Urządzenie Luxonis OAK zostało zamknięte/odłączone.")
                        break

                    self._mutex.lock()
                    if self._pending_exposure_update:
                        self.exposure_us = self._new_exposure_us
                        self.iso = self._new_iso
                        self.auto_exposure = self._new_auto_exposure
                        self._pending_exposure_update = False
                        self._mutex.unlock()
                        self._send_luxonis_camera_control(ctrl_queue)
                    else:
                        self._mutex.unlock()

                    msg = video_queue.tryGet()
                    if msg is None:
                        if total_frames > 0 and (time.monotonic() - last_frame_mono) > 5.0:
                            logger.warning("Timeout strumienia Luxonis OAK (>5 s bez klatki) — restart połączenia...")
                            break
                        time.sleep(0.005)
                        continue

                    last_frame_mono = time.monotonic()
                    full_frame = msg.getCvFrame()
                    total_frames += 1

                    if total_frames in (2, 6):
                        self._send_luxonis_camera_control(ctrl_queue)

                    self._latest_frame_mutex.lock()
                    self._latest_full_frame = full_frame
                    self._latest_frame_mutex.unlock()

                    fh, fw = full_frame.shape[:2]
                    prev_w = 1280
                    prev_h = int(round(prev_w * (fh / float(fw))))
                    preview_frame = cv2.resize(full_frame, (prev_w, prev_h), interpolation=cv2.INTER_LINEAR)

                    self.frame_ready.emit(full_frame, preview_frame)

                    fps_counter += 1
                    elapsed = time.monotonic() - fps_start
                    if elapsed >= 1.0:
                        cur_fps = fps_counter / elapsed
                        self.fps_updated.emit(cur_fps)
                        fps_counter = 0
                        fps_start = time.monotonic()

            except Exception as e:
                err_msg = f"Błąd połączenia z kamerą Luxonis: {str(e)}"
                logger.error(err_msg)
                self.error_occurred.emit(err_msg)
                self.status_changed.emit(False, "Błąd Luxonis", "-", "-")
                time.sleep(2.0)

            finally:
                if pipeline is not None:
                    try:
                        pipeline.stop()
                    except Exception:
                        pass
                    pipeline = None
                    self._pipeline = None
                if device is not None:
                    try:
                        device.close()
                    except Exception:
                        pass
                    device = None
                    self._device = None
                self._clear_latest_frame()
                self.fps_updated.emit(0.0)
                self.status_changed.emit(False, self.device_name, "-", "Odłączono")

        logger.info("Wątek akwizycji Luxonis został zatrzymany.")
