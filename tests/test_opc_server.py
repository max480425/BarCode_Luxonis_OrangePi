import asyncio
import os
import sys
import time
from pathlib import Path

# Dodajemy katalog główny projektu do sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from PySide6.QtWidgets import QApplication
from asyncua import Client, ua
from src.opc_server import OpcUaServerThread


def test_opc_ua_server_full_integration():
    print("=== Test serwera OPC UA (Ignition 8.3) ===")
    app = QApplication.instance() or QApplication(sys.argv)

    test_port = 4842
    server = OpcUaServerThread(port=test_port, endpoint_path="/barcode/test")
    server_started = [False]
    received_commands = []

    def on_status(running, msg):
        print(f"[OPC Server Status]: running={running}, msg='{msg}'")
        if running:
            server_started[0] = True

    def on_set_baseline(code):
        print(f"[OPC Server Signal]: odebrano set_baseline = '{code}'")
        received_commands.append(("set_baseline", code))

    def on_reset():
        print("[OPC Server Signal]: odebrano reset_baseline")
        received_commands.append(("reset_baseline", True))

    server.status_changed.connect(on_status)
    server.set_baseline_requested.connect(on_set_baseline)
    server.reset_baseline_requested.connect(on_reset)

    print(f"Uruchamianie serwera na porcie {test_port}...")
    server.start()

    # Czekamy na wystartowanie serwera
    t0 = time.time()
    while not server_started[0] and time.time() - t0 < 5.0:
        time.sleep(0.05)
        app.processEvents()

    assert server_started[0], "Serwer OPC UA nie wystartował w wyznaczonym czasie"
    print("[OK] Serwer OPC UA wystartował pomyślnie!")

    # Test asynchronicznego klienta OPC UA (emulacja Ignition 8.3)
    async def run_client_tests():
        url = f"opc.tcp://127.0.0.1:{test_port}/barcode/test"
        print(f"Łączenie klienta testowego (Ignition) do: {url}...")
        client = Client(url=url)
        async with client:
            print("[OK] Połączono klienta OPC UA!")
            ns_idx = await client.get_namespace_index("http://canline.vision/barcode")
            
            # Wyszukujemy węzły
            node_code = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:CurrentCode"])
            node_last_code = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:LastDetectedCode"])
            node_last_code_time = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:LastDetectedCodeTime"])
            node_last_scan_ts = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:LastScanTimestamp"])
            node_match = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:IsMatch"])
            node_alarm = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:BatchAlarm"])
            node_streak = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:MismatchStreak"])
            node_heartbeat = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:Heartbeat"])
            node_cmd_set = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:Cmd_SetBaseline"])
            node_cmd_reset = await client.nodes.objects.get_child([f"{ns_idx}:BarcodeScanner", f"{ns_idx}:Cmd_ResetBaseline"])

            # 1. Sprawdzenie początkowej wartości
            val_code = await node_code.read_value()
            val_last_code = await node_last_code.read_value()
            val_last_code_time = await node_last_code_time.read_value()
            assert val_last_code == ""
            assert val_last_code_time == "-"
            print(f"Odczytano CurrentCode = '{val_code}', LastDetectedCode = '{val_last_code}'")

            # 2. Publikacja wyniku detekcji z aplikacji
            print("Publikacja detekcji kodu z aplikacji do OPC UA...")
            server.publish_detection(
                code="5740700030567",
                format_str="EAN_13",
                is_match=True,
                duration_ms=45.2
            )
            server.update_batch_state(
                baseline_code="5740700030567",
                alarm=False,
                mismatch_streak=0,
                total_scans=150,
                matched=148,
                mismatched=2
            )

            # Czekamy chwilę na zapis do pętli asyncio
            await asyncio.sleep(0.3)

            read_code = await node_code.read_value()
            read_last_code = await node_last_code.read_value()
            read_last_code_time = await node_last_code_time.read_value()
            read_match = await node_match.read_value()
            assert read_code == "5740700030567", f"Błędny kod w OPC UA: {read_code}"
            assert read_last_code == "5740700030567", f"Błędny LastDetectedCode: {read_last_code}"
            assert read_last_code_time != "-" and len(read_last_code_time) >= 19
            assert read_match is True, f"Błędny status IsMatch: {read_match}"
            print(f"[OK] Klient odczytał zaktualizowany kod: '{read_code}', LastDetectedCode='{read_last_code}' ({read_last_code_time}), IsMatch={read_match}")

            # 2b. Pusta klatka (brak wykrytego kodu) zeruje CurrentCode, ale NIE nadpisuje LastDetectedCode ani LastDetectedCodeTime
            await asyncio.sleep(0.05)
            server.publish_detection(
                code="",
                format_str="",
                is_match=False,
                duration_ms=12.0
            )
            await asyncio.sleep(0.25)

            assert await node_code.read_value() == "", "Pusta klatka powinna ustawić CurrentCode na ''"
            assert await node_last_code.read_value() == "5740700030567", "LastDetectedCode nie może zniknąć po pustej klatce"
            assert await node_last_code_time.read_value() == read_last_code_time, "LastDetectedCodeTime nie może się zmienić po pustej klatce"
            assert await node_last_scan_ts.read_value() != read_last_code_time, "LastScanTimestamp powinien odświeżyć się po pustym skanie"
            print("[OK] Pusta klatka wyczyściła CurrentCode, zachowując LastDetectedCode i LastDetectedCodeTime.")

            # 2c. Kolejny wykryty kod aktualizuje LastDetectedCode oraz LastDetectedCodeTime
            await asyncio.sleep(0.05)
            server.publish_detection(
                code="5901234567893",
                format_str="EAN_13",
                is_match=False,
                duration_ms=19.4
            )
            await asyncio.sleep(0.25)
            assert await node_code.read_value() == "5901234567893"
            assert await node_last_code.read_value() == "5901234567893"
            assert await node_last_code_time.read_value() != read_last_code_time
            print("[OK] Kolejny odczyt zaktualizował LastDetectedCode i LastDetectedCodeTime.")

            # 3. Test Heartbeat (watchdog)
            hb1 = await node_heartbeat.read_value()
            await asyncio.sleep(0.7)
            hb2 = await node_heartbeat.read_value()
            print(f"[OK] Heartbeat watchdog rośnie: {hb1} -> {hb2}")
            assert hb2 >= hb1, "Heartbeat powinien inkrementować"

            # 4. Klient wysyła komendę zmiany wzorca z Ignition
            print("Klient zapisuje nowy kod wzorcowy do Cmd_SetBaseline...")
            await node_cmd_set.write_value("5901234567893")
            await asyncio.sleep(0.6)

    # Uruchamiamy testy klienta w asyncio
    asyncio.run(run_client_tests())

    # Przetwarzamy sygnały Qt
    for _ in range(10):
        time.sleep(0.05)
        app.processEvents()

    # Sprawdzamy czy odebrano sygnał
    assert len(received_commands) > 0, "Serwer powinien wyemitować sygnał po zapisie komendy przez klienta"
    cmd_name, cmd_val = received_commands[0]
    assert cmd_name == "set_baseline" and cmd_val == "5901234567893"
    print(f"[OK] Aplikacja poprawnie odebrała rozkaz z Ignition: {cmd_name}='{cmd_val}'")

    # Zatrzymanie serwera
    print("Zatrzymywanie serwera OPC UA...")
    server.stop()
    print("[OK] Serwer zatrzymany czysto.")
    print("=== TEST SERWERA OPC UA ZAKOŃCZONY SUKCESEM! ===")


if __name__ == "__main__":
    test_opc_ua_server_full_integration()
