# PLAN.md — Plan Realizacji Systemu Detekcji Kodów Kreskowych (Luxonis OAK-1-FF)

Niniejszy dokument przedstawia kompleksowy, etapowy plan budowy aplikacji desktopowej z interfejsem graficznym (GUI) do detekcji kodów kreskowych i monitorowania partii puszek aluminiowych na transporterze produkcyjnym, opracowany na podstawie analizy pliku [specyfikacja_dla_agenta_ai.md](file:///C:/Download/Programming/BarCode_Luxonis/specyfikacja_dla_agenta_ai.md).

---

## 1. Podsumowanie i wnioski z analizy specyfikacji

### 1.1. Cel biznesowy i operacyjny
* **Główne zadanie:** Wykrywanie zmiany litografii (zmiany partii produkcyjnej spośród ponad 1000 wzorów) na linii puszkarskiej poprzez odczyt kodów kreskowych (EAN-13, UPC, CODE-128 itp.).
* **Rola wersji testowej:** Kalibracja polowa przy linii produkcyjnej — precyzyjne ustawienie kadru (ROI), wyeliminowanie rozmycia ruchu (*motion blur*) poprzez regulację mikrosekundowego czasu naświetlania (*Manual Exposure*), podgląd odczytywanych kodów na żywo oraz zbieranie próbek zdjęć 4K na dysk.

### 1.2. Uwarunkowania fizyczne i optyczne
* **Prędkość linii:** ok. 1500 puszek/min (ok. $1.65 - 2.0\text{ m/s}$).
* **Konieczność Manual Exposure:** Czas naświetlania musi wynosić rzędu **$100 - 300\text{ }\mu\text{s}$** (zakres $50 - 10000\text{ }\mu\text{s}$), ISO $100 - 1600$ (domyślnie $800$). Automatyczna ekspozycja z uwagi na dużą prędkość puszek spowoduje rozmycie pasków kodu kreskowego.
* **Rozdzielczość 4K (3840×2160):** W odległości ok. $0.7\text{ m}$ z obiektywem Fixed Focus (pole widzenia ok. $950\text{ mm}$), paski kodu mają zaledwie kilkanaście milimetrów. Do pewnego odczytu przez biblioteki dekodujące wymagane jest minimum $1.5\text{ px}$ na najwęższy moduł kodu.
* **Brak sieci neuronowych do dekodowania:** Skalowanie 4K do rozdzielczości wejściowych sieci (np. 640×640) degraduje drobne paski kodów. Stosujemy deterministyczne, szybkie dekodowanie algorytmiczne biblioteką **`zxing-cpp`** na wyciętym wycinku ROI w pełnej rozdzielczości 4K.
* **Rozdzielenie strumienia wideo od detekcji:** Płynny podgląd (np. 30 FPS) działa niezależnie od dekodera `zxing-cpp`, który jest wyzwalany asynchronicznie z konfigurowalnym interwałem (np. 1 FPS / co 1.0 s).

### 1.3. Środowisko techniczne
* **System operacyjny:** Windows 11 (AMD64).
* **Python:** Python 3.12 (użycie środowiska wirtualnego `.venv`).
* **API DepthAI:** **Wersja 3.11.0 (DepthAI v3)** — nowoczesne węzły `dai.node.Camera`, `cam.requestOutput()`, `cam.inputControl` i dynamiczne sterowanie kamerą przez `dai.CameraControl.setManualExposure(exposure_us, iso)`.
* **Kamera:** Luxonis OAK-1-FF (sensor Sony IMX378 12 MP, `CAM_A`, USB 3.0 SUPER).

---

## 2. Architektura systemu i struktura projektu

Aplikacja zostanie zaimplementowana w oparciu o framework **PySide6** (Qt for Python), co zapewni wysoką wydajność renderowania wideo, stabilność wielowątkową oraz nowoczesny interfejs graficzny dopasowany do warunków przemysłowych.

### 2.1. Proponowana struktura katalogów
```text
BarCode_Luxonis/
│
├── .venv/                          # Środowisko wirtualne Python 3.12
├── captures/                       # Katalog na zapisywane zrzuty 4K i wycinki ROI
├── config.json                     # Zapisane parametry (ROI, ekspozycja, ISO, interwał)
├── requirements.txt                # Zależności projektu
├── run.bat                         # Skrypt uruchamiający aplikację jednym kliknięciem
├── specyfikacja_dla_agenta_ai.md  # Wejściowa specyfikacja
├── PLAN.md                         # Niniejszy plan działania
│
└── src/
    ├── __init__.py
    ├── app.py                      # Punkt wejścia do aplikacji GUI
    ├── camera_stream.py            # Moduł kamery DepthAI v3 (wątek akwizycji, CameraControl)
    ├── barcode_engine.py           # Moduł dekodowania kodów kreskowych (zxing-cpp + ROI)
    ├── batch_manager.py            # Logika partii wzorcowej (Auto-Lock, detekcja zmian, alarmy)
    ├── storage_manager.py          # Zapisywanie zdjęć PNG/JPG, auto-save, obsługa config.json
    │
    └── gui/
        ├── __init__.py
        ├── main_window.py          # Główne okno aplikacji i układ paneli
        ├── video_widget.py         # Skalowany podgląd z interaktywną ramką ROI i bounding-boxami
        ├── zoom_widget.py          # Podgląd powiększenia 1:1 wycinka środka ROI (Zoom Check)
        ├── camera_controls.py      # Panel sterowania ekspozycją, ISO, presetami i interwałem
        └── batch_panel.py          # Panel informacji o partii, tabela Live Log i statystyki
```

---

## 3. Szczegółowy plan wdrożenia krok po kroku

### Krok 1: Inicjalizacja środowiska wirtualnego i weryfikacja DepthAI v3
1. **Utworzenie środowiska wirtualnego:**
   * Utworzenie `.venv` w folderze projektu za pomocą `python -m uv venv .venv` lub `python -m venv .venv`.
   * Przygotowanie pliku [requirements.txt](file:///C:/Download/Programming/BarCode_Luxonis/requirements.txt):
     * `depthai==3.11.0`
     * `zxing-cpp>=3.0.0`
     * `PySide6>=6.8.0`
     * `opencv-python>=4.10.0`
     * `numpy>=1.26.0`
   * Instalacja zależności do `.venv`.
2. **Skrypt diagnostyczno-weryfikacyjny (`verify_oak.py`):**
   * Wykrycie podłączonej kamery (MxID, prędkość USB: `UsbSpeed.SUPER`).
   * Zbudowanie minimalnego potoku DepthAI v3:
     ```python
     pipeline = dai.Pipeline(device)
     cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
     out_stream = cam.requestOutput((3840, 2160), type=dai.ImgFrame.Type.BGR888p)
     ctrl_queue = cam.inputControl.createInputQueue()
     ```
   * Sprawdzenie dynamicznej zmiany czasu naświetlania za pomocą `CameraControl.setManualExposure(200, 800)`.

### Krok 2: Silnik przetwarzania wizyjnego i dekodowania (`barcode_engine.py`)
1. **Zarządzanie obszarem ROI:**
   * Przechowywanie znormalizowanych współrzędnych ROI ($X_{min}, Y_{min}, X_{max}, Y_{max}$ w zakresie $0.0 - 1.0$).
   * Wycinanie podobszaru z natywnej klatki 4K (3840×2160) bez straty jakości.
2. **Integracja z `zxing-cpp`:**
   * Wywołanie `zxingcpp.read_barcodes(roi_image)`.
   * Odczyt danych: format (np. `EAN-13`, `CODE-128`), odkodowany tekst, współrzędne wierzchołków (*points/position*).
   * Przeliczenie współrzędnych z układu współrzędnych ROI do układu klatki 4K oraz do skalowanego podglądu UI.
   * Pomiar czasu analizy klatki w milisekundach.

### Krok 3: Logika kontroli partii produkcyjnej (`batch_manager.py`)
1. **Tryb Auto-Lock / Wzorzec partii:**
   * Stan początkowy: oczekiwanie na pierwszy odczyt lub załadowanie wzorca z konfiguracji.
   * Po pierwszym odczycie — automatyczne zablokowanie jako **Kod Wzorcowy** (np. `5901234567890`).
   * Przycisk ręcznego uczenia / resetowania wzorca partii.
2. **Weryfikacja i logika alarmu zmiany litografii:**
   * Każdy odczytany kod jest porównywany z kodem bazowym.
   * Bufor potwierdzeń: wykrycie $N$ razy z rzędu kodu innego niż bazowy (gdzie $N$ jest konfigurowalne w UI, domyślnie $N=5$) wyzwala zdarzenie **`ZMIANA LITOGRAFII!`** (wizualny pulsujący banner ostrzegawczy w GUI).
3. **Statystyki sesji i historia (Live Log):**
   * Agregacja danych: łączna liczba przetworzonych klatek, liczba klatek z kodem, odczytane kody.
   * Struktura pojedynczego wpisu logu: `timestamp`, `code`, `format`, `status (ZGODNY/INNY)`, `duration_ms`.

### Krok 4: Interfejs graficzny w PySide6 (`gui/`)
1. **Wielowątkowość (Brak zawieszania GUI):**
   * `CameraWorker (QThread)`: odbiera klatki z kamery z pełną prędkością matrycy, przekazuje skalowaną klatkę do podglądu UI przez sygnał `frame_ready`.
   * `DetectorWorker (QRunnable / QThread)`: wyzwalany timerem co $T$ sekund (domyślnie 1.0 s) lub przyciskiem — pobiera ostatnią klatkę 4K, wycina ROI, odpala `zxing-cpp` i emituje sygnał `results_ready`.
2. **Główny panel wideo (`video_widget.py`):**
   * Płynne wyświetlanie obrazu z kamery przeskalowanego z zachowaniem proporcji (Aspect Ratio).
   * Rysowanie nakładki graficznej (HUD):
     * Półprzezroczysty prostokąt aktywnego ROI (możliwość regulacji uchwytami lub suwakami).
     * Ramki detekcji wokół kodów: **zielone** dla kodu zgodnego z bazowym, **czerwone** dla nowego kodu, wraz z czytelnym podpisem tekstu i formatu.
3. **Panel podglądu 1:1 (`zoom_widget.py` — Zoom Check):**
   * Niezależne okienko / podgląd boczny wycinka o rozmiarze np. $400\times300$ px ze środka ROI bez żadnego skalowania.
   * Umożliwia operatorowi natychmiastową ocenę ostrości pasków i brak rozmycia krawędzi (*motion blur*).
4. **Panel sterowania parametrami kamery (`camera_controls.py`):**
   * Przełącznik `Tryb manualny` / `Auto Exposure`.
   * Suwak i pole liczbowe czasu ekspozycji ($50 - 10000\text{ }\mu\text{s}$) ze skrótami presetów (`100 µs`, `200 µs`, `300 µs`, `500 µs`, `1000 µs`).
   * Zmiana natychmiast wysyła komendę `CameraControl` bez restartowania potoku.
   * Suwak i pole ISO ($100 - 1600$, domyślnie $800$).
   * Wybór interwału detekcji: `0.1s`, `0.2s`, `0.5s`, `1.0s`, `2.0s`, `5.0s` oraz przycisk „Sprawdź kody teraz”.
5. **Panel boczny partii i logów (`batch_panel.py`):**
   * Wskaźnik statusu urządzenia: Model kamery, MxID, Prędkość USB (`SUPER`), FPS wideo, czas przetwarzania `zxing-cpp`.
   * Sekcja aktualnej partii z dużym kodem bazowym, licznikiem zgodności i przyciskiem resetu.
   * Tabela Live Log z automatycznym przewijaniem do najnowszego wpisu.
   * Alarm wizualny zmiany litografii.

### Krok 5: Zapis danych, auto-save i persystencja konfiguracji (`storage_manager.py`)
1. **Obsługa zrzutów ekranu (Snapshot):**
   * Przycisk „Zapisz zdjęcie (Snapshot)” zapisuje do `captures/`:
     * Pełną klatkę 4K w formacie bezstratnym PNG: `snap_YYYYMMDD_HHMMSS_full_exp{exp}us_iso{iso}.png`
     * Wycinek ROI: `snap_YYYYMMDD_HHMMSS_roi_exp{exp}us_iso{iso}.png`
2. **Opcja automatycznego zapisu:**
   * Checkbox „Auto-zapis przy wykryciu / co interwał”.
3. **Konfiguracja (`config.json`):**
   * Automatyczny zapis przy zamykaniu i wczytanie przy starcie:
     * Współrzędne ROI ($X_{min}, Y_{min}, X_{max}, Y_{max}$)
     * Czas ekspozycji i ISO
     * Interwał detekcji
     * Wartość progu $N$ potwierdzeń zmiany

### Krok 6: Odporność na błędy, skrypty uruchomieniowe i testy
1. **Obsługa błędów sprzętowych:**
   * Brak kamery przy starcie nie powoduje błędu krytycznego programu — wyświetla czytelny komunikat o braku urządzenia i przycisk „Ponów połączenie”.
   * Obsługa nagłego odłączenia kabla USB podczas pracy (detekcja utraty strumienia, bezpieczne zatrzymanie wątków).
2. **Skrypt uruchomieniowy `run.bat`:**
   * Skrypt wsadowy dla Windows, który automatycznie sprawdza obecność `.venv`, w razie potrzeby instaluje pakiety i uruchamia aplikację za pomocą `.venv\Scripts\python.exe -m src.app`.
3. **Weryfikacja działania na żywo:**
   * Test uruchomienia aplikacji z podłączoną kamerą OAK-1-FF.
   * Weryfikacja działania regulacji ekspozycji w czasie rzeczywistym.
   * Weryfikacja detekcji kodów na fizycznych próbkach puszek.

---

## 4. Kryteria odbiorcze (Checklista sukcesu)

- [ ] **Środowisko:** Utworzone `.venv` z zainstalowanymi `depthai==3.11.0`, `zxing-cpp`, `PySide6`, `opencv-python`.
- [ ] **Strumień 4K:** Kamera OAK-1-FF poprawnie streamuje w natywnej rozdzielczości 4K przez USB 3.0 bez zacięć.
- [ ] **Sterowanie ekspozycją na żywo:** Zmiana suwaka czasu naświetlania ($50 - 10000\text{ }\mu\text{s}$) natychmiastowo zmienia jasność obrazu bez zatrzymywania potoku wideo.
- [ ] **Wielowątkowość:** Interfejs GUI zachowuje pełną responsywność (suwaki, przyciski reagują natychmiastowo) podczas ciągłego odbioru klatek i dekodowania `zxing-cpp`.
- [ ] **Detekcja w wycinku ROI:** Kody kreskowe są odczytywane z wyciętego obszaru ROI w pełnej rozdzielczości 4K.
- [ ] **Podgląd powiększenia 1:1:** Operator widzi powiększony wycinek ROI bez skalowania w dół do weryfikacji ostrości.
- [ ] **Weryfikacja partii:** System poprawnie blokuje pierwszy kod, zlicza powtórzenia i alarmuje przy wykryciu zmiany kodu $N$ razy z rzędu.
- [ ] **Zapis i zrzuty:** Działa manualny snapshot do bezstratnego PNG (pełna klatka + ROI) oraz zapamiętywanie konfiguracji w `config.json`.
- [ ] **Start jednym kliknięciem:** Plik `run.bat` uruchamia całe środowisko bez konieczności ręcznego wpisywania komend w terminalu.
