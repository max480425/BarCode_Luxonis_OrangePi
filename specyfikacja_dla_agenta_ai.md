# AGENT.md — System Testowy Detekcji Kodów Kreskowych (Luxonis OAK-1-FF)

## 1. Cel projektu
Zbudowanie desktopowej aplikacji testowo-kalibracyjnej (z interfejsem graficznym GUI) wykorzystującej kamerę **Luxonis OAK-1-FF** do wykrywania i dekodowania kodów kreskowych na puszkach aluminiowych przemieszczających się na transporterze liniowym.

Docelowym zadaniem systemu na linii produkcyjnej nie jest odczyt każdej pojedynczej puszki, lecz **wykrycie zmiany litografii (zmiany partii produktu)**, która zdarza się raz na kilka godzin. Ponieważ w zakładzie występuje ponad 1000 różnych wzorów litografii, identyfikacja opiera się na odczycie kodu kreskowego (np. EAN-13 / UPC), który jednoznacznie określa dany produkt.

Pierwsza wersja programu służy do **testów polowych bezpośrednio przy linii produkcyjnej**: ustawienia kadru (ROI), dobrania czasu ekspozycji eliminującego rozmycie ruchu (*motion blur*), podglądu odczytywanych kodów w panelu bocznym oraz zapisywania próbek zdjęć z linii na dysk.

---

## 2. Kontekst sprzętowy i środowiskowy (Ważne!)

Przed napisaniem kodu uwzględnij aktualną konfigurację środowiska:

* **System operacyjny:** Windows (AMD64)
* **Środowisko Python:** Python 3.12.10 w lokalnym środowisku wirtualnym `.venv` (używaj `.venv\Scripts\python.exe` oraz `.venv\Scripts\pip.exe`).
* **Wersja DepthAI:** **`3.11.0` (DepthAI v3 API)** – **UWAGA:** W środowisku zainstalowane jest nowe API DepthAI v3 (węzeł `dai.node.Camera`), a nie stare API v2 (`ColorCamera` / `XLinkOut`). Przed napisaniem potoku zweryfikuj dostępne metody w `.venv\Scripts\python.exe`.
* **Podłączona kamera docelowa:**
  * **Model:** **OAK-1-FF** (Fixed Focus – stała ogniskowa od ok. 0,5 m do nieskończoności), architektura **RVC2** (Intel Movidius Myriad X).
  * **Sensor:** Sony IMX378 12 MP (`CAM_A`, max 4056×3040, 4K: 3840×2160, Rolling Shutter, Color RGB).
  * **MxID (ostatnio widziany):** `14442C10A19D1FD000` (nie wpisuj MxID na sztywno – polegaj na automatycznym wykrywaniu urządzenia, chyba że podłączono wiele kamer).
  * **Połączenie:** USB 3.0 (`UsbSpeed.SUPER`, 5 Gbps).
* *(Uwaga: W systemie bywa też podłączana druga kamera `OAK-D-PRO-FF` o MxID `14442C10F1D0D2D600` – aplikacja powinna działać na głównym sensorze RGB `CAM_A` niezależnie od tego, która z tych dwóch jednostek zostanie podpięta).*

---

## 3. Założenia fizyczne i wizyjne na linii produkcyjnej

1. **Odległość i pole widzenia:** Kamera jest zamontowana z boku transportera w odległości ok. **$0.7\text{ m}$** od puszek. W tym dystansie obiektyw Fixed Focus zapewnia ostry obraz, a poziome pole widzenia obejmuje ok. $950\text{ mm}$ (w kadrze mieści się jednocześnie rząd ok. 12–14 puszek).
2. **Rozdzielczość (4K):** Aby paski kodu kreskowego (szerokość kodu ok. $37\text{ mm}$) miały wystarczającą gęstość pikseli (min. $1.5\text{ px}$ na najwęższy moduł), kamera musi pracować w wysokiej rozdzielczości (minimum **4K: 3840×2160**).
3. **Prędkość puszek i czas ekspozycji (Kluczowe!):** Prędkość linii wynosi ok. **1500 puszek na minutę** ($25\text{ szt./s}$, prędkość liniowa ok. $1.65 - 2.0\text{ m/s}$).
   * Automatyczna ekspozycja całkowicie rozmyje obraz.
   * Konieczne jest sterowanie **Manual Exposure** w zakresie mikrosekundowym (domyślnie ok. **$200\text{ }\mu\text{s}$**, zakres regulacji w UI: od $50\text{ }\mu\text{s}$ do $10000\text{ }\mu\text{s}$) oraz czułością **ISO** ($100 - 1600$, domyślnie $800$).
4. **Dekodowanie deterministyczne (`zxing-cpp`):** Nie używamy sieci neuronowych na VPU do detekcji kodów (skalowanie obrazu do rozmiaru wejściowego sieci $640\times640$ niszczy paski kodu z odległości $0.7\text{ m}$). Wykorzystujemy bibliotekę **`zxing-cpp`** bezpośrednio na wyciętym obszarze zainteresowania (ROI) w natywnej rozdzielczości 4K.
5. **Interwał przetwarzania:** Ponieważ na jednym zdjęciu widać kilkanaście puszek, a zmiana partii może zostać wykryta po kilkunastu/kilkuset puszkach, dekodowanie kodów nie musi działać w 30 FPS. Wystarczy płynny podgląd do ustawienia kamery oraz wyzwalanie dekodera `zxing-cpp` np. **raz na sekundę (1 FPS)** lub z konfigurowalnym interwałem ($0.1\text{ s} - 5.0\text{ s}$).

---

## 4. Wymagania funkcjonalne aplikacji

Zbuduj aplikację okienkową w Pythonie (zalecane **PySide6 / PyQt6** lub **CustomTkinter** dla czytelnego układu panelowego), która posiada następujące moduły:

### A. Panel podglądu wideo i kontroli obszaru (ROI)
1. **Podgląd główny:** Wyświetla przeskalowany strumień z kamery, aby mieścił się na ekranie laptopa, z narysowanym prostokątem aktywnego obszaru **ROI (Region of Interest)**.
2. **Konfiguracja ROI (Zakres widzenia/przetwarzania):**
   * Możliwość zawężenia obszaru przeszukiwania (np. suwaki lub pola numeryczne dla `X_min`, `Y_min`, `X_max`, `Y_max` wyrażone w procentach $0-100\%$ lub pikselach, albo zaznaczanie prostokąta myszką).
   * Tylko wycięty obszar ROI (w pełnej, natywnej rozdzielczości z matrycy 4K!) jest przekazywany do dekodera `zxing-cpp`.
3. **Podgląd powiększenia 1:1 (Zoom Check):**
   * Dodatkowe mniejsze okienko lub przełącznik pozwalający podejrzeć wycinek 1:1 (bez skalowania w dół) ze środka ROI. Pozwoli to operatorowi na linii produkcyjnej ocenić gołym okiem, czy paski kodu są ostre i czy czas ekspozycji nie jest za długi.
4. **Rysowanie wykrytych kodów:** Na podglądzie wideo wokół każdego wykrytego kodu kreskowego rysowana jest zielona (lub czerwona, jeśli kod jest inny niż bazowy) ramka *bounding box* wraz z odczytaną wartością tekstową.

### B. Panel sterowania parametrami kamery (Na żywo)
1. **Tryb ekspozycji:** Przełącznik `Manual Exposure` (domyślny) / `Auto Exposure` (pomocniczy przy montażu kamery w ciemnym miejscu przed włączeniem lamp).
2. **Suwak / pole Czasu Ekspozycji (µs):** Zakres od $50\text{ }\mu\text{s}$ do $10000\text{ }\mu\text{s}$ (z szybkimi przyciskami/presetami: `100 µs`, `200 µs`, `300 µs`, `500 µs`, `1000 µs`). Zmiana wartości natychmiast wysyła komendę sterującą (`CameraControl`) do kamery OAK-1 bez restartowania potoku.
3. **Suwak / pole Czułości ISO:** Zakres od $100$ do $1600$ (domyślnie $800$).
4. **Częstotliwość dekodowania kodów:** Wybór jak często klatka jest analizowana przez `zxing-cpp` (np. co $1.0\text{ s}$ domyślnie, z możliwością zmiany na $0.2\text{ s}$, $0.5\text{ s}$, $2.0\text{ s}$ lub przycisk ręczny „Szukaj kodów teraz”).

### C. Panel detekcji kodów i logiki partii (Boczny panel informacyjny)
1. **Sekcja „Aktualna Partia (Auto-Lock)”:**
   * Wyświetla aktualnie zablokowany kod wzorcowy bieżącej produkcji (np. `5901234567890`).
   * Przycisk `Resetuj / Ucz nowy kod`, który czyści wzorzec i ustawia pierwszy kolejny odczytany kod jako nowy kod bazowy.
   * Licznik potwierdzeń zmiany (np. wykrycie $N$ razy z rzędu nowego kodu wyzwala wizualny alarm `ZMIANA LITOGRAFII!`). Liczba $N$ konfigurowalna w UI (domyślnie np. $5$).
2. **Tabela / Lista ostatnich odczytów (Live Log):**
   * Wyświetla listę wykrytych kodów od najnowszego:
     * `Czas (HH:MM:SS.ms)`
     * `Odczytany kod`
     * `Typ kodu` (np. EAN_13, CODE_128)
     * `Status` (ZGODNY / INNY KOD)
     * `Czas analizy klatki (ms)`
3. **Statystyki sesji:** Liczba przeanalizowanych klatek, liczba klatek z przynajmniej jednym wykrytym kodem, łączna liczba odczytanych kodów.

### D. Narzędzia testowe i zapis danych na linii
1. **Przycisk „Zapisz zdjęcie (Snapshot)”:** Zapisuje do folderu `captures/` zarówno pełną klatkę 4K, jak i wycięte ROI w formacie bezstratnym `.png`. Nazwa pliku powinna zawierać znacznik czasu oraz aktualne parametry (np. `snap_20261101_143012_exp200us_iso800.png`).
2. **Opcja „Auto-zapis przy wykryciu / co X sekund”:** Checkbox pozwalający na automatyczne zapisywanie zdjęć na dysk podczas testów na linii produkcyjnej w celu późniejszej analizy w biurze.
3. **Zapis / Odczyt konfiguracji (`config.json`):** Automatyczne zapamiętywanie ustawionego ROI, czasu ekspozycji, ISO oraz interwału dekodowania między uruchomieniami programu.

---

## 5. Wytyczne implementacyjne dla Agenta krok po kroku

1. **Krok 1 — Inspekcja środowiska:**
   * Sprawdź pakiety zainstalowane w `.venv` (`.venv\Scripts\pip.exe list`).
   * Doinstaluj brakujące biblioteki, np. `zxing-cpp`, `opencv-python`, `PySide6` (lub wybraną bibliotekę GUI).
   * Przetestuj krótki skrypt weryfikujący API **DepthAI 3.11.0** dla węzła `Camera` oraz dynamicznego wysyłania `CameraControl` (`setManualExposure(exposure_us, iso)`), aby upewnić się, że składnia pasuje do wersji v3.
2. **Krok 2 — Architektura wielowątkowa:**
   * Odbiór klatek z kamery OAK-1 oraz dekodowanie `zxing-cpp` umieść w osobnym wątku roboczym (`QThread` lub `threading.Thread`), aby interfejs graficzny (suwaki ekspozycji i ROI) działał płynnie i nigdy się nie zawieszał.
3. **Krok 3 — Obsługa błędów USB:**
   * Wyświetl w GUI czytelny status połączenia z kamerą (np. nazwę modelu `OAK-1-FF`, MxID oraz wynegocjowaną prędkość USB, np. `SUPER`). Jeśli kamera nie jest podłączona, aplikacja nie powinna się wyłączać z błędem krytycznym, lecz wyświetlić komunikat i przycisk „Połącz ponownie”.
4. **Krok 4 — Test i weryfikacja:**
   * Po napisaniu kodu uruchom aplikację przez `.venv\Scripts\python.exe`, sprawdź czy okno otwiera się poprawnie, czy strumień 4K z kamery inicjalizuje się bez błędów oraz czy zmiana suwaka ekspozycji faktycznie zmienia jasność obrazu w czasie rzeczywistym.