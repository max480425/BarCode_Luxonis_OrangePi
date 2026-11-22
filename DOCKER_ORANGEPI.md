# Wdrożenie na Orange Pi 5 (Joshua Riek Ubuntu 24.04) w Dockerze + noVNC

Ten przewodnik opisuje, jak najpierw zweryfikować aplikację na Windows, a następnie uruchomić ją w kontenerze Docker na **Orange Pi 5** (ARM64) z podglądem pełnego GUI w przeglądarce (**noVNC**), obsługą **Logitech C922 / USB UVC** oraz **Luxonis OAK-1-FF** i serwerem **OPC UA** dla Ignition 8.3.

---

## 1. Testy na Windows (przed przeniesieniem na Orange Pi 5)

### A. Natywne uruchomienie i testy w `.venv`
Uruchom testy automatyczne:
```powershell
.\.venv\Scripts\python.exe tests\test_components.py
.\.venv\Scripts\python.exe tests\test_opc_server.py
.\.venv\Scripts\python.exe tests\test_crossplatform_fixes.py
```
Następnie uruchom pełną aplikację z kamerą na Windows:
```powershell
.\run.bat
```
Sprawdź szczególnie:
- przycisk **`📸 Zapisz Zdjęcie Teraz`** (powinien zapisać pliki `*_full.png` i `*_roi.png` do `captures/` i wyświetlić toast na wideo),
- pasek statusu skanera w lewym górnym rogu wideo (licznik skanów, czas w `ms`, jasność ROI).

### B. Opcjonalny test samego kontenera na Windows (Docker Desktop — bez kamer USB)
*(Uwaga: Docker Desktop na Windows nie przekazuje bezpośrednio kamer USB do kontenera bez dodatkowego `usbipd`, ale pozwala sprawdzić sam interfejs noVNC i serwer OPC UA).*
```powershell
docker build -t barcode-verifier .
docker run --rm -p 4840:4840 -p 6080:6080 -e GUI_MODE=novnc barcode-verifier
```
Po uruchomieniu otwórz w przeglądarce:
`http://localhost:6080/vnc.html?autoconnect=true&resize=scale`

---

## 2. Przeniesienie i uruchomienie na Orange Pi 5 (Ubuntu 24.04)

1. Skopiuj folder projektu na Orange Pi 5 (bez `.venv` i bez starych `captures/*.png` — np. przez `scp`, `rsync` lub `git`).
2. Na Orange Pi 5 wejdź do katalogu projektu i uruchom skrypt przygotowujący hosta:
   ```bash
   chmod +x setup_orangepi.sh
   ./setup_orangepi.sh
   ```
   Skrypt instaluje regułę `udev` dla kamer Luxonis OAK (`/etc/udev/rules.d/80-movidius.rules`), tworzy katalogi `data/` oraz `captures/`, a także wyświetla wykryte kamery (`/dev/video*` i `lsusb`).
3. Zbuduj i uruchom kontener (natywnie na ARM64):
   ```bash
   docker compose up -d --build
   ```
4. Sprawdź logi startowe:
   ```bash
   docker compose logs -f
   ```

---

## 3. Korzystanie z aplikacji na Orange Pi 5

* **Zdalny podgląd GUI w przeglądarce (noVNC):**
  ```text
  http://<IP_ORANGE_PI>:6080/vnc.html?autoconnect=true&resize=scale
  ```
  *(Opcjonalnie: ustaw `VNC_PASSWORD=twojehaslo` w `docker-compose.yml`, aby zabezpieczyć dostęp hasłem).*
* **Połączenie z Ignition 8.3 (OPC UA):**
  ```text
  opc.tcp://<IP_ORANGE_PI>:4840/barcode/server
  ```
* **Trwałość danych:**
  * Konfiguracja aplikacji zapisuje się w `./data/config.json` na hoście.
  * Zrzuty PNG zapisują się w `./captures/` na hoście.

---

## 4. Wskazówki sprzętowe (Logitech C922 i Luxonis OAK-1)

* **Logitech C922 / USB UVC:** Kod na Linuksie automatycznie używa backendu `CAP_V4L2`, wymusza format **MJPG** dla płynnego **1920×1080 @ 30 FPS** oraz poprawnie przelicza ekspozycję manualną na jednostki V4L2 (`100 µs`).
* **Luxonis OAK-1-FF:** Podłączaj ją do **niebieskiego portu USB 3.0** na Orange Pi 5 przewodem USB 3.0 (w logach powinno pojawić się `UsbSpeed.SUPER`). Jeśli użyjesz portu USB 2.0, 12 MP strumień NV12 może zapchać magistralę.
* **Test samej kamery OAK w kontenerze:**
  ```bash
  docker compose run --rm barcode-verifier python verify_oak.py
  ```
