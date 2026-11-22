from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional
from asyncua import Server, ua
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger("opc_server")


class OpcUaServerThread(QThread):
    """
    Wątek serwera OPC UA przeznaczony do integracji z systemem SCADA Ignition 8.3.
    Wystawia odczytane kody kreskowe, stan partii, alarmy litografii, czasy analizy
    oraz komendy sterujące (np. reset wzorca, ustawienie kodu partii z MES).
    """
    status_changed = Signal(bool, str)            # (running, endpoint_or_error)
    reset_baseline_requested = Signal()          # Zlecenie resetu wzorca z Ignition
    set_baseline_requested = Signal(str)         # Zlecenie ustawienia nowego kodu z Ignition
    clear_alarm_requested = Signal()             # Zlecenie wyciszenia alarmu z Ignition

    def __init__(
        self,
        port: int = 4840,
        endpoint_path: str = "/barcode/server",
        parent=None
    ) -> None:
        super().__init__(parent)
        self.port = port
        self.endpoint = f"opc.tcp://0.0.0.0:{self.port}{endpoint_path}"
        self.server_name = "CanLine Barcode Verifier OPC UA Server"
        self.namespace_uri = "http://canline.vision/barcode"

        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[Server] = None
        self._stop_event: Optional[asyncio.Event] = None

        # Węzły OPC UA
        self._node_code = None
        self._node_format = None
        self._node_is_match = None
        self._node_baseline = None
        self._node_alarm = None
        self._node_mismatch_streak = None
        self._node_duration = None
        self._node_total_scans = None
        self._node_matched_count = None
        self._node_mismatched_count = None
        self._node_camera = None
        self._node_trigger = None
        self._node_heartbeat = None
        self._node_timestamp = None

        # Węzły komend (Writable z poziomu Ignition)
        self._node_cmd_reset = None
        self._node_cmd_set_baseline = None
        self._node_cmd_clear_alarm = None

        # Liczniki wewnętrzne
        self._scan_trigger_counter = 0
        self._heartbeat_counter = 0

    def run(self) -> None:
        """Pętla główna wątku serwera OPC UA z własną pętlą zdarzeń asyncio."""
        self._running = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._loop.run_until_complete(self._run_server())
        except Exception as e:
            logger.error("Błąd krytyczny serwera OPC UA: %s", e)
            self.status_changed.emit(False, f"Błąd: {str(e)}")
        finally:
            self._cleanup_loop()
            self._running = False
            self.status_changed.emit(False, "Serwer zatrzymany")

    async def _run_server(self) -> None:
        """Asynchroniczna inicjalizacja, konfiguracja struktury węzłów i uruchomienie serwera."""
        logger.info("Inicjalizacja serwera OPC UA na porcie %d (%s)...", self.port, self.endpoint)

        self._server = Server()
        await self._server.init()
        self._server.set_endpoint(self.endpoint)
        self._server.set_server_name(self.server_name)

        # Rejestracja dedykowanej przestrzeni nazw dla czytnika linii puszkarskiej
        idx = await self._server.register_namespace(self.namespace_uri)

        # Utworzenie głównego obiektu nadrzędnego BarcodeScanner
        obj_scanner = await self._server.nodes.objects.add_object(idx, "BarcodeScanner")

        # ---------------------------------------------------------------------
        # 1. TAGI STATUSU I WYNIKÓW DETEKCJI (Read-Only dla Ignition)
        # ---------------------------------------------------------------------
        self._node_code = await obj_scanner.add_variable(idx, "CurrentCode", "", ua.VariantType.String)
        self._node_format = await obj_scanner.add_variable(idx, "Format", "", ua.VariantType.String)
        self._node_is_match = await obj_scanner.add_variable(idx, "IsMatch", False, ua.VariantType.Boolean)
        self._node_baseline = await obj_scanner.add_variable(idx, "BaselineCode", "", ua.VariantType.String)
        self._node_alarm = await obj_scanner.add_variable(idx, "BatchAlarm", False, ua.VariantType.Boolean)
        self._node_mismatch_streak = await obj_scanner.add_variable(idx, "MismatchStreak", 0, ua.VariantType.Int32)
        self._node_duration = await obj_scanner.add_variable(idx, "ScanDurationMs", 0.0, ua.VariantType.Double)
        self._node_total_scans = await obj_scanner.add_variable(idx, "TotalScans", 0, ua.VariantType.UInt32)
        self._node_matched_count = await obj_scanner.add_variable(idx, "MatchedCount", 0, ua.VariantType.UInt32)
        self._node_mismatched_count = await obj_scanner.add_variable(idx, "MismatchedCount", 0, ua.VariantType.UInt32)
        self._node_camera = await obj_scanner.add_variable(idx, "ActiveCamera", "Inicjalizacja...", ua.VariantType.String)
        self._node_trigger = await obj_scanner.add_variable(idx, "NewScanTrigger", 0, ua.VariantType.UInt32)
        self._node_heartbeat = await obj_scanner.add_variable(idx, "Heartbeat", 0, ua.VariantType.UInt32)
        self._node_timestamp = await obj_scanner.add_variable(idx, "LastScanTimestamp", "-", ua.VariantType.String)

        # ---------------------------------------------------------------------
        # 2. TAGI KOMEND STERUJĄCYCH (Writable dla Ignition / SCADA)
        # ---------------------------------------------------------------------
        self._node_cmd_reset = await obj_scanner.add_variable(idx, "Cmd_ResetBaseline", False, ua.VariantType.Boolean)
        await self._node_cmd_reset.set_writable()

        self._node_cmd_set_baseline = await obj_scanner.add_variable(idx, "Cmd_SetBaseline", "", ua.VariantType.String)
        await self._node_cmd_set_baseline.set_writable()

        self._node_cmd_clear_alarm = await obj_scanner.add_variable(idx, "Cmd_ClearAlarm", False, ua.VariantType.Boolean)
        await self._node_cmd_clear_alarm.set_writable()

        # Uruchomienie serwera
        await self._server.start()
        logger.info("Serwer OPC UA wystartował pomyślnie. Gotowy na połączenia z Ignition: %s", self.endpoint)
        self.status_changed.emit(True, self.endpoint)

        self._stop_event = asyncio.Event()

        # Pętla watchdoga (Heartbeat) oraz nasłuchu komend z Ignition
        while not self._stop_event.is_set():
            try:
                # 1. Zwiększenie licznika Heartbeat (Watchdog dla Ignition)
                self._heartbeat_counter = (self._heartbeat_counter + 1) % 4294967295
                await self._node_heartbeat.write_value(
                    ua.DataValue(ua.Variant(self._heartbeat_counter, ua.VariantType.UInt32))
                )

                # 2. Sprawdzenie, czy Ignition wydał rozkaz resetu wzorca
                val_reset = await self._node_cmd_reset.read_value()
                if val_reset:
                    logger.info("OPC UA: Odebrano rozkaz Cmd_ResetBaseline z Ignition!")
                    self.reset_baseline_requested.emit()
                    await self._node_cmd_reset.write_value(False)

                # 3. Sprawdzenie, czy Ignition wpisał nowy kod wzorcowy partii
                val_set_code = await self._node_cmd_set_baseline.read_value()
                if val_set_code and str(val_set_code).strip():
                    new_code = str(val_set_code).strip()
                    logger.info("OPC UA: Odebrano rozkaz Cmd_SetBaseline z Ignition: '%s'", new_code)
                    self.set_baseline_requested.emit(new_code)
                    await self._node_cmd_set_baseline.write_value("")

                # 4. Sprawdzenie, czy Ignition wyciszył alarm
                val_clear_alarm = await self._node_cmd_clear_alarm.read_value()
                if val_clear_alarm:
                    logger.info("OPC UA: Odebrano rozkaz Cmd_ClearAlarm z Ignition!")
                    self.clear_alarm_requested.emit()
                    await self._node_cmd_clear_alarm.write_value(False)

            except Exception as e:
                logger.warning("Błąd w pętli obsługi OPC UA: %s", e)

            # Czekamy 0.5 s (lub natychmiastowe przerwanie przy stopie)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

        # Zatrzymanie serwera OPC UA
        logger.info("Zamykanie serwera OPC UA...")
        await self._server.stop()
        logger.info("Serwer OPC UA został zatrzymany.")

    def _cleanup_loop(self) -> None:
        """Czyste zamknięcie pętli asyncio."""
        if self._loop is not None and not self._loop.is_closed():
            try:
                tasks = asyncio.all_tasks(self._loop)
                for t in tasks:
                    t.cancel()
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                self._loop.close()
            except Exception:
                pass

    def stop(self) -> None:
        """Bezpieczne zatrzymanie serwera z innego wątku (np. z wątku głównego Qt)."""
        logger.info("Żądanie zatrzymania serwera OPC UA...")
        self._running = False
        if self._loop is not None and not self._loop.is_closed() and self._stop_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._stop_event.set)
            except RuntimeError:
                pass
        self.wait(3000)

    # =========================================================================
    # METODY SYNCHRONIZACJI DANYCH Z APLIKACJI DO OPC UA (BEZPIECZNE WĄTKOWO)
    # =========================================================================
    def publish_detection(
        self,
        code: str,
        format_str: str,
        is_match: bool,
        duration_ms: float
    ) -> None:
        """Przekazuje wyniki ostatniej detekcji kodu do przestrzeni tagów OPC UA."""
        if not self._running or self._loop is None or self._node_code is None:
            return

        self._scan_trigger_counter = (self._scan_trigger_counter + 1) % 4294967295
        ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        async def _update():
            try:
                await self._node_code.write_value(str(code))
                await self._node_format.write_value(str(format_str))
                await self._node_is_match.write_value(bool(is_match))
                await self._node_duration.write_value(float(duration_ms))
                await self._node_timestamp.write_value(ts_now)
                await self._node_trigger.write_value(
                    ua.DataValue(ua.Variant(self._scan_trigger_counter, ua.VariantType.UInt32))
                )
            except Exception as e:
                logger.debug("Błąd zapisu wyników detekcji do OPC UA: %s", e)

        asyncio.run_coroutine_threadsafe(_update(), self._loop)

    def update_batch_state(
        self,
        baseline_code: Optional[str],
        alarm: bool,
        mismatch_streak: int,
        total_scans: int,
        matched: int,
        mismatched: int
    ) -> None:
        """Aktualizuje stan partii i statystyki w OPC UA."""
        if not self._running or self._loop is None or self._node_baseline is None:
            return

        b_code = str(baseline_code) if baseline_code else ""

        async def _update():
            try:
                await self._node_baseline.write_value(b_code)
                await self._node_alarm.write_value(bool(alarm))
                await self._node_mismatch_streak.write_value(
                    ua.DataValue(ua.Variant(int(mismatch_streak), ua.VariantType.Int32))
                )
                await self._node_total_scans.write_value(
                    ua.DataValue(ua.Variant(int(total_scans), ua.VariantType.UInt32))
                )
                await self._node_matched_count.write_value(
                    ua.DataValue(ua.Variant(int(matched), ua.VariantType.UInt32))
                )
                await self._node_mismatched_count.write_value(
                    ua.DataValue(ua.Variant(int(mismatched), ua.VariantType.UInt32))
                )
            except Exception as e:
                logger.debug("Błąd zapisu stanu partii do OPC UA: %s", e)

        asyncio.run_coroutine_threadsafe(_update(), self._loop)

    def update_camera(self, camera_name: str) -> None:
        """Aktualizuje nazwę aktualnie aktywnej kamery w OPC UA."""
        if not self._running or self._loop is None or self._node_camera is None:
            return

        async def _update():
            try:
                await self._node_camera.write_value(str(camera_name))
            except Exception as e:
                logger.debug("Błąd zapisu kamery do OPC UA: %s", e)

        asyncio.run_coroutine_threadsafe(_update(), self._loop)
