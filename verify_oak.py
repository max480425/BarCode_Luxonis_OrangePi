import sys
import time

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
import cv2
import depthai as dai
import numpy as np
import zxingcpp
from PySide6 import QtCore

print("=== WERYFIKACJA ŚRODOWISKA ===")
print(f"Python: {sys.version}")
print(f"DepthAI: {dai.__version__}")
import importlib.metadata
print(f"zxing-cpp: {importlib.metadata.version('zxing-cpp')}")
print(f"OpenCV: {cv2.__version__}")
print(f"PySide6 (Qt): {QtCore.__version__}")

print("\n=== WYKRYWANIE URZĄDZEŃ LUXONIS ===")
devices = dai.Device.getAllAvailableDevices()
print(f"Liczba wykrytych urządzeń: {len(devices)}")
for i, dev in enumerate(devices):
    print(f"  [{i}] Nazwa: {dev.name}, MxID: {dev.deviceId}, Stan: {dev.state}")

if not devices:
    print("OSTRZEŻENIE: Brak dostępnych urządzeń OAK w tej chwili.")
    sys.exit(0)

target_dev = devices[0]
print(f"\nŁączenie z urządzeniem: {target_dev.name} ({target_dev.deviceId})...")

try:
    with dai.Device(target_dev) as device:
        usb_speed = device.getUsbSpeed()
        print(f"Połączono! USB Speed: {usb_speed}")
        
        # Test DepthAI v3 Camera node
        pipeline = dai.Pipeline(device)
        cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
        
        # Strumień 4K (3840x2160) w natywnym formacie ISP NV12
        out_stream = cam.requestOutput((3840, 2160), type=dai.ImgFrame.Type.NV12, fps=20.0)
        video_queue = out_stream.createOutputQueue(maxSize=2, blocking=False)
        ctrl_queue = cam.inputControl.createInputQueue()
        
        pipeline.start()
        print("Pipeline DepthAI v3 uruchomiony pomyślnie!")
        
        # Przetestuj wysłanie Manual Exposure (200 us, ISO 800)
        ctrl = dai.CameraControl()
        ctrl.setManualExposure(200, 800)
        ctrl_queue.send(ctrl)
        print("Wysłano CameraControl: Manual Exposure = 200 us, ISO = 800")
        
        start_time = time.time()
        frame_received = False
        while time.time() - start_time < 5.0:
            frame_msg = video_queue.tryGet()
            if frame_msg is not None:
                frame = frame_msg.getCvFrame()
                h, w = frame.shape[:2]
                print(f"Otrzymano pierwszą klatkę 4K: {w}x{h}, shape={frame.shape}")
                frame_received = True
                break
            time.sleep(0.05)
            
        if not frame_received:
            print("Uwaga: Nie odebrano klatki w ciągu 5 sekund.")
        else:
            print("Weryfikacja DepthAI v3 i kamery OAK zakończona SUKCESEM!")
            
except Exception as e:
    print(f"Błąd podczas testu kamery: {e}")
    import traceback
    traceback.print_exc()
