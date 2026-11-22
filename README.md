# Luxonis OAK-1 & USB WebCam Barcode Verifier — System Detekcji i Weryfikacji Partii Puszek

Aplikacja desktopowa z interfejsem graficznym (PySide6 / Qt) dedykowana do testów polowych oraz biurkowych, obsługująca:
* Przemysłowe kamery **Luxonis OAK-1-FF** (sensor Sony IMX378 12 MP w natywnej rozdzielczości 4K przez potok DepthAI v3),
* Dowolne kamery internetowe **USB UVC** (np. **Logitech C270**, C310, C920, kamery wbudowane przez DirectShow / OpenCV).

---

## 🚀 Szybki start (Uruchomienie jednym kliknięciem)

* **Windows:**
  1. Podłącz kamerę **Luxonis OAK-1** lub kamerę USB (np. **Logitech C922 / C270**).
  2. Kliknij dwukrotnie w plik `run.bat` lub uruchom:
     ```powershell
     .venv\Scripts\python.exe -m src.app
     ```
* **Orange Pi 5 (Ubuntu 24.04 + Docker + noVNC):** zobacz instrukcję w [DOCKER_ORANGEPI.md](DOCKER_ORANGEPI.md).

---

## ⚙️ Kluczowe Funkcjonalności

### 1. Wybór Źródła Obrazu (Kamera na Żywo)
* **Lista rozwijana w panelu:** Wybór pomiędzy kamerą przemysłową Luxonis OAK a dowolną podłączoną kamerą USB (np. `Logi C270 HD WebCam`).
* **Przełączanie bez restartu:** Zmiana kamery w locie płynnie zwalnia poprzednie urządzenie i rozpoczyna strumieniowanie z nowo wybranego.
* **Przycisk „🔄 Odśwież”:** Dynamicznie skanuje porty USB i wykrywa podłączone lub odłączone urządzenia.
* **Automatyczne zapamiętywanie:** Wybrana kamera jest zapisywana w `config.json` i przywracana przy kolejnym starcie.

### 2. Podgląd wideo i Interaktywne ROI
* **Główny ekran wideo:** Płynne wyświetlanie strumienia z kamery z naniesioną ramką aktywnego obszaru analizy (ROI).
* **Zaznaczanie ROI myszką:** Kliknij i przeciągnij lewym przyciskiem myszy na podglądzie wideo, aby wyznaczyć obszar przeszukiwania kodów kreskowych na transporterze.
* **Precyzyjna regulacja numeryczna:** Pola $X_1, Y_1, X_2, Y_2$ wyrażone w procentach ($0 - 100\%$) w dolnym panelu.

### 3. Eliminacja rozmycia ruchu (Motion Blur) — Manual Exposure i Presety Biurkowe
* Puszki na linii przemieszczają się z prędkością ok. $1500\text{ szt./min}$ ($\approx 1.65 - 2.0\text{ m/s}$).
* **Manual Exposure (50 – 33000 µs):** Działa zarówno dla kamery Luxonis OAK, jak i kamer USB wspierających kontrolę ekspozycji w DirectShow.
* **Szybkie presety:** Przyciski dla linii (`100µs`, `200µs`, `500µs`, `1000µs`, `2000µs`) oraz dla prób stacjonarnych w biurze bez lamp przemysłowych (`5ms (Biurko)`, `10ms (Biurko)`, `20ms (Biurko)`) i przełącznik `Auto Exposure`.
* **Uwaga optyczna OAK-1-FF:** Obiektyw stałoogniskowy (*Fixed Focus*) ostrzy od ok. **50 cm** do nieskończoności. Kamery internetowe (np. Logitech C270) ostrzą z bliska (od 20–40 cm).

### 4. Podgląd powiększenia 1:1 (Zoom Check) z wyborem celu
* Wycinek piksel-w-piksel w natywnej rozdzielczości (bez skalowania w dół) wraz z pomiarem **Jasności (0–255)** i **Ostrości** na żywo.
* **Automatyczne śledzenie:** Automatycznie centruje się na wykrytym kodzie kreskowym lub kandydacie kodu.
* **Wybór ręczny (PPM):** Kliknij **prawym przyciskiem myszy (PPM)** w dowolne miejsce na podglądzie wideo, aby wycentrować tam okno Zoom 1:1. Podwójne kliknięcie PPM przywraca tryb automatyczny.

### 5. Wieloetapowy odczyt kodów kreskowych (`zxing-cpp`) i Wizualizacja Skanowania (HUD)
* Wieloetapowe dekodowanie w pełnej rozdzielczości klatki: odczyt bezpośredni, adaptacyjne wyrównanie kontrastu **CLAHE**, kierunkowa lokalizacja kandydatów paskowych i wyostrzanie (*Unsharp Mask* + odszumianie kierunkowe 1D).
* **Widoczna praca skanera na ekranie:**
  * Górny lewy pasek HUD pokazuje status skanera (`🔍 SZUKANIE KODÓW: AKTYWNE`, numer skanu, czas w ms oraz jasność ROI).
  * Podczas każdego skanu przez obszar ROI przechodzi animowana zielono-błękitna wiązka skanująca.
  * 🟩 **Zielona ramka:** Kod odczytany i zgodny z bieżącą partią produkcyjną.
  * 🟥 **Czerwona ramka:** Kod odczytany, ale niezgodny (inna litografia).
  * 🟨 **Żółta przerywana ramka (`KANDYDAT KODU`):** Wykryto strukturę paskową kodu na puszce, ale obraz jest zbyt ciemny lub nieostry, aby odczytać cyfry.

### 6. Integracja z Ignition 8.3 przez OPC UA (Ethernet / LAN)
Aplikacja zawiera wbudowany serwer **OPC UA** działający w osobnym asynchronicznym wątku, gotowy do natychmiastowego połączenia z Ignition 8.3 (i starszymi).

* **Adres Endpointu:** `opc.tcp://<IP_KOMPUTERA>:4840/barcode/server`
* **Polityka bezpieczeństwa:** `None` (Anonymous / No Security na sieci przemysłowej)
* **Przestrzeń nazw (Namespace):** `http://canline.vision/barcode`
* **Folder w przestrzeni adresowej:** `Objects/BarcodeScanner`

#### Wystawione tagi (do odczytu w SCADA / Perspective):
| Nazwa tagu w OPC UA | Typ danych | Opis |
| :--- | :--- | :--- |
| `CurrentCode` | String | Kod kreskowy z ostatnio analizowanej klatki (lub `""`, jeśli brak) |
| `LastDetectedCode` | String | Ostatni niepusty wykryty kod kreskowy (zachowywany przy pustych klatkach) |
| `LastDetectedCodeTime` | String | Data i godzina ostatniego wykrycia kodu (`YYYY-MM-DD HH:MM:SS.mmm`) |
| `Format` | String | Format kodu (np. `EAN_13`, `CODE_128`) |
| `IsMatch` | Boolean | `True` = kod zgodny z partią, `False` = błąd litografii |
| `BaselineCode` | String | Aktualny kod wzorcowy partii produkcyjnej |
| `BatchAlarm` | Boolean | `True` gdy wykryto $N$ kolejnych niezgodnych puszek |
| `MismatchStreak` | Int32 | Bieżący licznik kolejnych niezgodnych puszek |
| `ScanDurationMs` | Double | Czas analizy ostatniej klatki w milisekundach |
| `TotalScans` | UInt32 | Łączna liczba przeanalizowanych klatek |
| `MatchedCount` | UInt32 | Liczba puszek zgodnych ze wzorcem |
| `MismatchedCount` | UInt32 | Liczba puszek z odmiennym kodem |
| `ActiveCamera` | String | Nazwa podłączonej kamery (`Logi C270` / `OAK-1`) |
| `NewScanTrigger` | UInt32 | Licznik rosnący przy każdym skanie (dla skryptów `tagChange` w Ignition) |
| `Heartbeat` | UInt32 | Watchdog rosnący co 1 s (do monitorowania stanu połączenia) |
| `LastScanTimestamp` | String | Dokładna data i godzina ostatniego skanu |

#### Węzły sterujące (Writable z poziomu Ignition / SCADA):
* `Cmd_ResetBaseline` (`Boolean`): Wpisanie `True` w Ignition resetuje wzorzec partii (Auto-Lock zablokuje kolejny odczytany kod). Aplikacja automatycznie zeruje tag z powrotem na `False`.
* `Cmd_SetBaseline` (`String`): Wpisanie kodu z poziomu SCADA / MES natychmiast ustawia go jako wzorzec partii w aplikacji.
* `Cmd_ClearAlarm` (`Boolean`): Wpisanie `True` w Ignition wycisza alarm w aplikacji.

#### 🛠️ Jak połączyć w Ignition Gateway:
1. Otwórz w przeglądarce **Ignition Gateway** (`http://localhost:8088`).
2. Przejdź do: **Config → OPC Connections → OPC UA**.
3. Kliknij **Create new OPC UA Connection**.
4. Wpisz Endpoint URL: `opc.tcp://<IP_KOMPUTERA_Z_KAMERĄ>:4840/barcode/server` (Security: `None`).
5. Kliknij **Save** — status zmieni się na **Connected**.
6. W **Ignition Designer** w drzewie *Tag Browser* wybierz *OPC Browser*, rozwiń `BarcodeScanner` i przeciągnij tagi do projektu!

---

### 7. Tryb Zbierania Zdjęć (Captures) i Konfiguracja
* **Przycisk Snapshot:** Asynchronicznie (bez przycinania GUI) zapisuje do folderu `captures/` bezstratną klatkę oraz wycinek ROI w formacie PNG.
* **Wyraźny Tryb Zbierania Zdjęć (Auto-Zapis):**
  * Duży czerwony przycisk **`🔴 TRYB ZBIERANIA ZDJĘĆ: AKTYWNY`** w panelu bocznym oraz plakietka **`🔴 ZBIERANIE ZDJĘĆ: AKTYWNE`** w prawym górnym rogu ekranu wideo.
  * Dwa tryby do wyboru: **„Każda skanowana klatka (co interwał)”** lub **„Tylko gdy wykryto kod kreskowy”**.
* **Trwałość parametrów:** Plik `config.json` automatycznie zapamiętuje wybraną kamerę, ustawienia ROI, ekspozycji, ISO i interwałów między uruchomieniami programu.

---

## 📁 Struktura Projektu

```text
BarCode_Luxonis/
│
├── .venv/                      # Środowisko wirtualne Python 3.12 (DepthAI 3.11.0, zxing-cpp, PySide6)
├── captures/                   # Folder ze zrzutami bezstratnymi PNG z testów polowych
├── config.json                 # Zapisane parametry operacyjne
├── requirements.txt            # Zależności biblioteczne
├── run.bat                     # Skrypt uruchamiający aplikację
├── PLAN.md                     # Szczegółowy plan techniczny wdrożenia
├── README.md                   # Niniejsza dokumentacja
│
├── src/
│   ├── app.py                  # Punkt startowy aplikacji GUI
│   ├── camera_stream.py        # Wątek kamery (Luxonis DepthAI v3 oraz USB UVC / OpenCV)
│   ├── opc_server.py           # Serwer OPC UA dla integracji z Ignition 8.3
│   ├── barcode_engine.py       # Silnik zxing-cpp, kadrowanie i zoom 1:1
│   ├── batch_manager.py        # Menedżer partii (Auto-Lock, weryfikacja, alarm)
│   ├── storage_manager.py      # Zapis snapshotów PNG i config.json
│   │
│   └── gui/
│       ├── main_window.py      # Główne okno aplikacji
│       ├── video_widget.py     # Interaktywny podgląd wideo i rysowanie ROI/kodów
│       ├── zoom_widget.py      # Podgląd powiększenia 1:1 (Zoom Check)
│       ├── camera_controls.py  # Wybór kamery, suwaki ekspozycji, ISO, interwału
│       └── batch_panel.py      # Panel partii, alarmów, statusu OPC UA i snapshotów
│
└── tests/
    ├── test_components.py      # Testy jednostkowe zxing-cpp, BatchManager, StorageManager
    ├── test_camera_stream.py   # Testy wykrywania i przełączania kamer w locie
    ├── test_opc_server.py      # Testy integracyjne serwera OPC UA i klienta Ignition
    └── test_gui_init.py        # Test poprawnej inicjalizacji interfejsu graficznego
```
