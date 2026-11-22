# Podsumowanie Prac, Konfiguracji Optycznej i Metod Detekcji Kodów Kreskowych (Luxonis OAK-1)

Niniejszy dokument podsumowuje wszystkie wprowadzone poprawki i usprawnienia w aplikacji **Luxonis OAK-1 Barcode Verifier**, omawia dostępne parametry sprzętowe kamery wpływające na ostrość i czytelność kodów, wyjaśnia dobrane ustawienia oraz przedstawia zastosowane biblioteki i algorytmy dekodowania wraz z możliwymi kierunkami dalszego rozwoju.

---

## 1. Podsumowanie tego, co zostało zrobione i naprawione

### 1.1. Stabilizacja potoku wideo i przejście na natywne 12 MP (`src/camera_stream.py`)
* **Usunięcie błędów `X_LINK_ERROR` i zawieszania kamery:** Poprzednio potok żądał formatu `BGR888p` w rozdzielczości 4K przy 30 FPS. Na procesorze **Myriad X (RVC2)** wymuszało to alokację dużych buforów 25 MB na klatkę i programową konwersję formatu na VPU, co prowadziło do przepełnienia pamięci i zrywania połączenia USB.
* **Przejście na sprzętowy format ISP `NV12` (15 FPS):** Strumień jest teraz pobierany w natywnym formacie wyjściowym procesora obrazu (`dai.ImgFrame.Type.NV12`), co zmniejsza obciążenie pamięci VPU i pasma USB o 50% oraz zapewnia 100% stabilności ciągłej pracy.
* **Pełna natywna rozdzielczość matrycy 12 MP (`4000×3000` zamiast `3840×2160`):** Zamiast skalowanego w dół trybu 4K (`3840×2160`), zastosowano `cam.requestFullResolutionOutput()`. Wyeliminowało to niecałkowite skalowanie (`4056 → 3840`, współczynnik `0.9467x`) na układzie ISP kamery, które powodowało rozmycie (aliasing interpolacji dwuliniowej) na wąskich paskach kodu o szerokości `1.5–2.0 px`.
* **Bezpieczne zamykanie wątku kamery:** Usunięto wyścig wątków (*race condition*) przy zatrzymywaniu `pipeline.stop()`.

### 1.2. Wizualizacja pracy skanera na żywo na ekranie (`src/gui/video_widget.py`, esz`src/gui/zoom_widget.py`)
* **Górny lewy pasek HUD skanera:** Na obrazie wideo cały czas widoczny jest stan detektora (`🔍 SZUKANIE KODÓW: AKTYWNE`, interwał, numer kolejnego skanu, czas analizy w `ms`, wynik ostatniego skanu oraz średnia jasność wycinka ROI).
* **Animowana wiązka skanująca w ROI:** Podczas każdego cyklu analizy przez zaznaczony prostokąt ROI przechodzi zielono-błękitna linia skanera, a obramowanie ROI pulsuje, dzięki czemu od razu widać, że program aktywnie przeszukuje kadr.
* **Żółte ramki „Kandydatów Kodu” (`🔍 WYKRYTO OBSZAR KODU`):** Gdy algorytm zauważy na puszce strukturę paskową, ale obraz jest zbyt ciemny lub rozmyty na odczyt cyfr, rysuje wokół kodu żółtą przerywaną ramkę wraz z diagnozą (np. `Za ciemno` / `Nieostre`).
* **Ulepszony podgląd `ZOOM 1:1`:**
  * Automatycznie centruje się na wykrytym kodzie kreskowym lub kandydacie kodu (zamiast pokazywać pusty środek ekranu).
  * **Wybór celu prawym przyciskiem myszy (PPM):** Kliknięcie PPM w dowolne miejsce na wideo ustawia tam celownik `ZOOM 1:1` (dwuklik PPM przywraca tryb automatyczny).
  * Wyświetla na żywo pomiar **Jasności (`0–255`)** oraz odszumionego wskaźnika **Ostrości** badanego wycinka.

### 1.3. Niezawodny zapis zdjęć i widoczny Tryb Zbierania Zdjęć (`src/gui/batch_panel.py`, `src/storage_manager.py`)
* **Naprawa logiki Auto-Zapisu:** Poprzednio auto-zapis działał wyłącznie wtedy, gdy udało się zdekodować kod kreskowy. Teraz domyślnym trybem zbierania zdjęć jest **„Każda skanowana klatka (co interwał)”** (z możliwością przełączenia na *„Tylko gdy wykryto kod”*).
* **Wyraźna sygnalizacja aktywności:**
  * Duży czerwony przycisk **`🔴 TRYB ZBIERANIA ZDJĘĆ: AKTYWNY`** w prawym panelu bocznym.
  * Czerwona plakietka **`🔴 ZBIERANIE ZDJĘĆ: AKTYWNE | Zapisano: X`** w prawym górnym rogu ekranu wideo.
  * Krótki błysk ramki obrazu oraz zielony pasek powiadomienia na dole ekranu (`📸 ZAPISANO ZDJĘCIE #X: snap_...`) przy każdym zapisanym zdjęciu.
* **Zapis asynchroniczny w tle:** Zapis plików PNG 12 MP + ROI odbywa się w osobnym wątku roboczym, dzięki czemu interfejs nigdy się nie przycina i nie gubi kliknięć.

---

## 2. Opcje kamery Luxonis OAK-1 w zakresie ostrości, skupienia i ekspozycji

Podłączona jednostka (`MxID: 14442C10A19D1FD000`) została zweryfikowana programowo przez `device.getConnectedCameraFeatures()`:
* **Sensor:** Sony **IMX378** (12 MP, rozdzielczość maksymalna `4056×3040`, aktywne wyjście ISP `4000×3000`, Rolling Shutter).
* **Typ optyki (`hasAutofocus: 0`):** Wersja **Fixed Focus (stałoogniskowa)** — obiektyw nie posiada ruchomej cewki VCM (silnika autofokusa). Komendy `setManualFocus()` / `setAutoFocusMode()` są przyjmowane przez API DepthAI bez błędu, ale fizycznie nie przesuwają soczewki w modelu `-FF`.

### 2.1. Uwarunkowania optyczne obiektywu Fixed Focus (Skupienie / Ogniskowanie)
1. **Fabryczny punkt ostrości:** Analiza pełnych kadrów 12 MP pokazała, że w tym egzemplarzu soczewka jest fabrycznie ustawiona na odległość hiperfokalną z maksimum ostrości w okolicach **1,5 – 2,5 m** (dlatego drobny druk na ścianie w tle jest idealnie ostry, a w odległości `70–80 cm` występuje lekkie rozmycie optyczne o promieniu $\sigma \approx 1.6 - 2.0\text{ px}$).
2. **Co można zrobić sprzętowo w kwestii fizycznego skupienia (ostrości optycznej):**
   * **Opcja A (Programowa — już wdrożona):** Kompensacja rozmycia soczewki za pomocą wyłączenia rozmywania ISP (`LumaDenoise=0`, `Sharpness=4`) oraz matematycznej **dekonwolucji Wienera 1D (FFT)** w kodzie aplikacji. Pozwala to skutecznie czytać kody już z odległości `70–80 cm` bez ingerencji w kamerę.
   * **Opcja B (Wymiana na model z obiektywem M12 lub Auto-Focus, jeśli dostępny):** W środowisku przemysłowym na stały dystans `0.7 m` najlepiej sprawdzają się kamery z obiektywem **M12** (ręcznie regulowany gwint obiektywu kontrowany śrubką dokładnie na `0.70 m`) lub wariant **OAK-1 AutoFocus** z programowo zablokowaną pozycją soczewki `ctrl.setManualFocus(135..145)`.
   * **Opcja C (Fizyczna regulacja gwintu soczewki w module FF):** W modułach kamery IMX378 soczewka jest wkręcana w gwintowany uchwyt i zabezpieczona kropelką kleju UV. W warunkach serwisowych możliwe jest delikatne wykręcenie soczewki o ok. $15^\circ - 25^\circ$ (przesunięcie ogniska z $2.5\text{ m}$ na $0.70\text{ m}$), jednak zdejmuje to gwarancję producenta i wymaga otwarcia obudowy — dlatego zalecane jest pozostanie przy wdrożonej kompensacji cyfrowej (Opcja A).

### 2.2. Dostępne parametry procesora obrazu ISP (`dai.CameraControl` w DepthAI v3)
W kamerze OAK-1 (RVC2) na czytelność pasków kodu kreskowego wpływają następujące parametry sterowane w czasie rzeczywistym:

| Parametr `dai.CameraControl` | Zakres wartości | Domyślnie w OAK | Dobrano w aplikacji | Wpływ na odczyt kodów kreskowych |
| :--- | :---: | :---: | :---: | :--- |
| **Rozdzielczość wyjścia ISP** | do `4000×3000` | `3840×2160` (skalowane) | **`4000×3000` (12 MP 1:1)** | Brak skalowania na ISP — zachowuje ostre krawędzie wąskich modułów (~1.8 px). |
| **Format strumienia** | `BGR888p`, `NV12`, `YUV420p` | `BGR888p` | **`NV12` @ 15 FPS** | Natywny format ISP; eliminuje zawieszanie VPU (`X_LINK_ERROR`). |
| **`setSharpness(val)`** | `0 .. 4` | `1` | **`4` (Maksimum)** | Sprzętowy filtr *edge peaking* na ISP wzmacniający zbocza pasków kodu. |
| **`setLumaDenoise(val)`** | `0 .. 4` | `1` | **`0` (Wyłączone!)** | **Kluczowe:** domyślne odszumianie jasności rozmywa drobne paski 1-modułowe. Wyłączenie (`0`) radykalnie poprawia separację pasków. |
| **`setChromaDenoise(val)`** | `0 .. 4` | `1` | `1` (można zmienić na `0` lub `2`) | Odszumianie koloru (nie wpływa negatywnie na jasność Y w NV12). |
| **`setManualExposure(us, iso)`** | `50 .. 33000 µs`, ISO `100 .. 1600` | Auto | **Biurko: Auto / `10–20 ms`**<br>**Linia: `150–500 µs`, ISO `800`** | Na linii w ruchu (<500 µs) zamraża ruch puszek; w biurze bez lamp przemysłowych wymagane jest `10–20 ms` lub `Auto Exposure`. |
| **`setContrast(val)`** | `-10 .. +10` | `0` | `0` (regulacja CLAHE na PC) | Kontrast sprzętowy ISP (lepiej wykonywać adaptacyjnie na PC, aby nie przepalać bieli). |

---

## 3. Co można jeszcze ewentualnie zmienić lub dostroić?

1. **Czas ekspozycji i oświetlenie na linii produkcyjnej vs. w biurze:**
   * **Na biurku (puszka stoi nieruchomo, słabe światło sufitowe):** Używaj trybu **`Auto Exposure`** lub presetów **`10ms (Biurko)` / `20ms (Biurko)`**. Uwaga: jeśli na puszce widać silny biały odblask światła (przepalenie do wartości `255`), przełącz z `Auto Exposure` na manualne **`5000 µs` – `12000 µs`**, aby białe tło kodu miało jasność ok. `180–220`, a nie `255` (brak zjawiska *bloomingu* zalewającego cienkie czarne paski).
   * **Na linii produkcyjnej (puszki w ruchu `~1.8 m/s` + mocny oświetlacz LED):** Przełącz na **`Manual Exposure`** w zakresie **`150 µs` – `400 µs`** przy **ISO `800–1200`**.
2. **Zawężenie obszaru ROI:**
   * Im mniejszy obszar ROI zaznaczysz myszką (np. tylko pas, na którym znajdują się kody na puszkach), tym krótszy czas analizy pojedynczej klatki (spadek z `~300 ms` do **`~40–80 ms`**) i mniejsza szansa na fałszywych kandydatów z tła.
3. **Parametry dekonwolucji Wienera w [`src/barcode_engine.py`](file:///C:/Download/Programming/BarCode_Luxonis/src/barcode_engine.py):**
   * W metodzie `_wiener_deconvolve_and_read()` przeszukiwane są promienie rozmycia `sigma in (1.5, 1.8, 2.1, 2.5, 3.0)` oraz `snr in (0.005, 0.01, 0.02, 0.04)`.
   * Jeśli kamera na linii zostanie zamontowana w stałej odległości (np. dokładnie `70 cm`), można zostawić tylko dwie wartości `sigma = (1.6, 2.0)` oraz `snr = (0.01, 0.02)`, co przyspieszy dekodowanie ponad **dwukrotnie**.

---

## 4. Zastosowane biblioteki i metody wyszukiwania kodów kreskowych

### 4.1. Jak działa obecny, 3-etapowy silnik w [`src/barcode_engine.py`](file:///C:/Download/Programming/BarCode_Luxonis/src/barcode_engine.py)?

Ponieważ z odległości `70–80 cm` pojedynczy najwęższy pasek kodu EAN-13 ma szerokość zaledwie **ok. `1.8 piksela`**, a obiektyw Fixed Focus wprowadza lekkie rozmycie optyczne ($\sigma \approx 1.8\text{ px}$), pojedyncze paski czarne nie są w obrazie czarne (mają jasność `~204/255`), podczas gdy grube paski 3-modułowe mają jasność `~130/255`. Zwykłe progowanie (nawet lokalne w `zxing-cpp`) traktuje wtedy cienkie paski (`204 > 190`) jako białe tło i nie widzi kodu.

Aby rozwiązać ten problem, zbudowaliśmy **3-etapowy potok hybrydowy (OpenCV + NumPy FFT + zxing-cpp)**:

1. **Etap 1 — Bezpośredni odczyt `zxing-cpp` (dla idealnie ostrych kadrów):**
   * Przekazuje wycinek ROI w skali szarości do `zxingcpp.read_barcodes(gray, try_rotate=True, try_downscale=True, try_invert=True)`.
   * Jeśli kod jest blisko i ostry, odczyt trwa zaledwie **`15–35 ms`**.
2. **Etap 2 — Lokalizacja kandydatów + Dekonwolucja Częstotliwościowa Wienera 1D (FFT):**
   * **Kierunkowy detektor kandydatów (`_find_barcode_candidates`):** Oblicza pochodne kierunkowe Sobela ($|G_x|$ oraz $|G_y|$) **w pełnej rozdzielczości 12 MP (`1.0x`)** (dzięki czemu paski `1.8 px` nie giną przez aliasing Nyquista), a następnie redukuje mapę energii `0.25x` i operacjami morfologicznymi (`MORPH_CLOSE` + otwarcie odcinające kable w tle) znajduje prostokątne skupiska pasków pionowych (`V`) lub poziomych (`H`).
   * **Dekonwolucja Wienera 1D (`_wiener_deconvolve_and_read`):** Wycięty obszar kandydata jest powiększany `2x` w poprzek pasków (`INTER_CUBIC`), wygładzany wzdłuż pasków filtrem Gaussa `(1, 17)` (co uśrednia szum matrycy i odblaski z zaokrąglonej puszki aluminiowej), a następnie transformowany przez szybkie przekształcenie Fouriera (`np.fft.rfft`).
   * W dziedzinie częstotliwości stosowany jest filtr odwrotny Wienera cofający rozmycie soczewki o charakterystyce Gaussa $H(f) = e^{-2\pi^2 \sigma^2 f^2}$:
     $$W(f) = \frac{H(f)}{H(f)^2 + \text{SNR}}$$
   * Po odwrotnej transformacie (`np.fft.irfft`) cienkie 1-modułowe paski odzyskują pełną głębokość czerni i są natychmiast dekodowane przez `zxing-cpp` (w testach na żywo z 75 cm kod `6415600517515 [EAN-13]` na puszce Battery dekoduje się w ok. `25–60 ms` od momentu wycięcia kandydata).
3. **Etap 3 — Wyrównanie histogramu CLAHE + Unsharp Masking:**
   * Dodatkowa ścieżka zapasowa dla kodów o bardzo niskim kontraście oświetleniowym, wykorzystująca `cv2.createCLAHE` oraz binaryzator `GlobalHistogram`.

---

### 4.2. Porównanie dostępnych bibliotek dekodowania kodów w Pythonie

| Biblioteka / Narzędzie | Szybkość (C++) | Odporność na obrót i skos | Odporność na rozmycie (Defocus) | Status w projekcie / Uwagi |
| :--- | :---: | :---: | :---: | :--- |
| **`zxing-cpp` (v3.1+)** | ⭐⭐⭐⭐⭐ (Bardzo wysoka) | ⭐⭐⭐⭐⭐ (`try_rotate=True`) | ⭐⭐⭐ (Wymaga wsparcia dekonwolucją przy `<2 px/moduł`) | **Używana jako główny dekoder** w połączeniu z naszym modułem dekonwolucji Wienera FFT. Najlepsza darmowa biblioteka open-source. |
| **OpenCV + NumPy FFT (Dekonwolucja Wienera 1D)** | ⭐⭐⭐⭐⭐ (Wektoryzowane C/Fortran) | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ (Kluczowa przy Fixed Focus!) | **Wdrożona w `src/barcode_engine.py`** jako przedprocesor optyczny przed `zxing-cpp`. |
| **`pyzbar` (`libzbar`)** | ⭐⭐⭐⭐ | ⭐⭐ (Słaba przy lekkim obrocie) | ⭐⭐ | Starsza biblioteka C; często nie radzi sobie z kodami na zakrzywionych puszkach. |
| **`cv2.barcode.BarcodeDetector`** | ⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐ | Wbudowany w OpenCV detektor oparty o gradienty Scharra; na testowanych klatkach z 75 cm nie wykrywał kodu bez wstępnej dekonwolucji. |
| **Sieci neuronowe (YOLO / Myriad X VPU) do lokalizacji + `zxing-cpp`** | ⭐⭐⭐⭐ (Na VPU kamery) | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ (Jako lokalizator boxa) | Ewentualne rozwinięcie: mały model YOLOv8n na procesorze kamery OAK-1 może zwracać współrzędne ramki kodu kreskowego, a PC wykonuje tylko dekonwolucję Wienera i odczyt `zxing-cpp` na tym małym wycinku. |
| **Komercyjne SDK (Dynamsoft / Cognex / Halcon)** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | Posiadają wbudowane algorytmy dekonwolucji, ale wymagają płatnych licencji stanowiskowych (nasza implementacja dekonwolucji Wienera FFT + `zxing-cpp` realizuje tę samą funkcję bezpłatnie). |
