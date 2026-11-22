from __future__ import annotations

import logging
import os
import signal
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.gui.main_window import MainWindow
from src.storage_manager import StorageManager

# Konfiguracja logowania
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("app")


def main() -> None:
    # Upewniamy się, że wyjście konsoli działa poprawnie w UTF-8
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    logger.info("Inicjalizacja aplikacji Luxonis OAK-1 / USB Barcode Verifier...")

    # Uruchomienie aplikacji Qt
    app = QApplication(sys.argv)
    app.setApplicationName("Luxonis OAK-1 Barcode Verifier")
    app.setOrganizationName("Luxonis Production Vision")

    captures_dir = os.environ.get("CAPTURES_DIR", "captures")
    config_path = os.environ.get("CONFIG_PATH", "config.json")

    # Menedżer pamięci dyskowej
    storage = StorageManager(output_dir=captures_dir, config_path=config_path)

    # Utworzenie i wyświetlenie głównego okna
    window = MainWindow(storage_manager=storage)
    start_max = os.environ.get("START_MAXIMIZED", "0").strip().lower() in ("1", "true", "yes")
    if start_max:
        window.showMaximized()
    else:
        window.show()

    # Czyste zamykanie przy SIGINT / SIGTERM (np. docker stop) z zapisem config.json
    def _handle_exit_signal(signum, _frame):
        logger.info("Odebrano sygnał zakończenia (%s) — zamykanie aplikacji...", signum)
        QTimer.singleShot(0, window.close)

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            signal.signal(sig, _handle_exit_signal)

    # Krótki impuls do pętli Pythona, aby obsługa sygnałów POSIX/Win działała podczas app.exec()
    signal_pump = QTimer()
    signal_pump.start(250)
    signal_pump.timeout.connect(lambda: None)

    logger.info("Główne okno aplikacji wyświetlone. Oczekiwanie na interakcję...")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
