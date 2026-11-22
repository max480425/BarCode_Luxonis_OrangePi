import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.gui.main_window import MainWindow
from src.storage_manager import StorageManager

print("Test inicjalizacji GUI...")
app = QApplication.instance()
if app is None:
    app = QApplication(sys.argv)

storage = StorageManager(output_dir="temp_captures", config_path="temp_config.json")
window = MainWindow(storage_manager=storage)
print("MainWindow utworzone pomyślnie!")
print(f"Tytuł okna: {window.windowTitle()}")
print(f"Wymiary okna: {window.width()}x{window.height()}")

# Zamykamy okno po 3.5 sekundy (po pełnym starcie potoku kamery OAK-1)
QTimer.singleShot(3500, lambda: (window.close(), app.quit()))
app.exec()
print("Test GUI zakończony pomyślnie!")

# Czyszczenie plików tymczasowych
Path("temp_config.json").unlink(missing_ok=True)
import shutil
shutil.rmtree("temp_captures", ignore_errors=True)
