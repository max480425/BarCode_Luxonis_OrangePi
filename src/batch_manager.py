from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional
from src.barcode_engine import BarcodeItem, DetectionResult


@dataclass
class LogEntry:
    """Wpis w historii ostatnich odczytów (Live Log)."""
    timestamp_str: str
    code: str
    format: str
    is_match: bool
    duration_ms: float


@dataclass
class BatchStatistics:
    """Zbiorcze statystyki bieżącej sesji produkcyjnej."""
    total_frames_analyzed: int = 0
    frames_with_codes: int = 0
    total_barcodes_detected: int = 0
    matched_codes_count: int = 0
    mismatched_codes_count: int = 0


class BatchManager:
    """Zarządza kodem wzorcowym partii, weryfikacją zgodności oraz alarmem zmiany litografii."""

    def __init__(self, confirmation_threshold: int = 5, auto_switch_threshold: int = 10):
        """
        :param confirmation_threshold: Liczba wykryć z rzędu nowego kodu przed wywołaniem alarmu (domyślnie 5).
        :param auto_switch_threshold: Liczba wykryć z rzędu takiego samego nowego kodu, po której
                                      automatycznie staje się on nowym kodem bazowym (domyślnie 10, 0 = wyłączone).
        """
        self.confirmation_threshold = max(1, confirmation_threshold)
        self.auto_switch_threshold = max(0, auto_switch_threshold)
        self.baseline_code: Optional[str] = None
        self.baseline_format: Optional[str] = None

        # Stan śledzenia zmiany
        self.candidate_mismatch_code: Optional[str] = None
        self.mismatch_consecutive_count: int = 0
        self.batch_change_alarm: bool = False
        self.last_auto_switched_code: Optional[str] = None

        # Statystyki i historia
        self.stats = BatchStatistics()
        self.history: List[LogEntry] = []
        self.max_history_entries: int = 200

    def reset_baseline(self) -> None:
        """Resetuje kod wzorcowy partii — kolejny wykryty kod zostanie automatycznie zablokowany."""
        self.baseline_code = None
        self.baseline_format = None
        self.candidate_mismatch_code = None
        self.mismatch_consecutive_count = 0
        self.batch_change_alarm = False
        self.last_auto_switched_code = None

    def set_baseline(self, code: str, fmt: str = "") -> None:
        """Ręczne ustawienie kodu wzorcowego partii."""
        self.baseline_code = code.strip()
        self.baseline_format = fmt
        self.candidate_mismatch_code = None
        self.mismatch_consecutive_count = 0
        self.batch_change_alarm = False
        self.last_auto_switched_code = None

    def clear_alarm(self) -> None:
        """Potwierdzenie i wygaszenie alarmu zmiany partii przez operatora."""
        self.batch_change_alarm = False
        self.mismatch_consecutive_count = 0
        self.candidate_mismatch_code = None
        self.last_auto_switched_code = None

    def clear_history(self) -> None:
        """Natychmiastowo czyści listę historii ostatnich odczytów (Live Log)."""
        self.history.clear()

    def accept_candidate_as_new_baseline(self) -> None:
        """Zaakceptowanie wykrytego kandydata jako nowej partii bazowej."""
        if self.candidate_mismatch_code:
            self.baseline_code = self.candidate_mismatch_code
        self.clear_alarm()

    def process_detection(self, result: DetectionResult) -> bool:
        """
        Przetwarza wynik detekcji, aktualizuje flagi is_match w obiektach BarcodeItem,
        aktualizuje statystyki oraz sprawdza warunek alarmu zmiany litografii.
        Zwraca True, jeśli wystąpił alarm zmiany litografii.
        """
        self.stats.total_frames_analyzed += 1
        self.last_auto_switched_code = None
        now_dt = datetime.now()
        ts_str = now_dt.strftime("%H:%M:%S.") + f"{int(now_dt.microsecond / 1000):03d}"

        if not result.barcodes:
            return self.batch_change_alarm

        self.stats.frames_with_codes += 1
        self.stats.total_barcodes_detected += len(result.barcodes)

        # Sprawdzenie kodów
        for item in result.barcodes:
            code_text = item.text.strip()
            
            # Auto-Lock: pierwszy napotkany kod staje się wzorcem, jeśli brak bazowego
            if self.baseline_code is None:
                self.baseline_code = code_text
                self.baseline_format = item.format
                item.is_match = True
                self.stats.matched_codes_count += 1
                self.history.insert(0, LogEntry(
                    timestamp_str=ts_str,
                    code=code_text,
                    format=item.format,
                    is_match=True,
                    duration_ms=result.duration_ms
                ))
                continue

            # Weryfikacja kodu z bazowym
            if code_text == self.baseline_code:
                item.is_match = True
                self.stats.matched_codes_count += 1
                # Zgodny kod resetuje licznik kolejnych niezgodności
                self.mismatch_consecutive_count = 0
                self.candidate_mismatch_code = None
            else:
                # Zliczanie kolejnych powtórzeń nowego kodu
                if self.candidate_mismatch_code == code_text:
                    self.mismatch_consecutive_count += 1
                else:
                    self.candidate_mismatch_code = code_text
                    self.mismatch_consecutive_count = 1

                # Po osiągnięciu progu auto-zmiany (domyślnie 10 identycznych kodów z rzędu)
                # nowy kod automatycznie staje się nowym kodem bazowym
                if self.auto_switch_threshold > 0 and self.mismatch_consecutive_count >= self.auto_switch_threshold:
                    self.baseline_code = code_text
                    self.baseline_format = item.format
                    self.batch_change_alarm = False
                    self.mismatch_consecutive_count = 0
                    self.candidate_mismatch_code = None
                    self.last_auto_switched_code = code_text
                    item.is_match = True
                    self.stats.matched_codes_count += 1
                else:
                    item.is_match = False
                    self.stats.mismatched_codes_count += 1

                    # Czy osiągnięto próg potwierdzeń alarmu N?
                    if self.mismatch_consecutive_count >= self.confirmation_threshold:
                        self.batch_change_alarm = True

            # Dodanie do historii
            self.history.insert(0, LogEntry(
                timestamp_str=ts_str,
                code=code_text,
                format=item.format,
                is_match=item.is_match,
                duration_ms=result.duration_ms
            ))

        # Obcięcie historii
        if len(self.history) > self.max_history_entries:
            self.history = self.history[:self.max_history_entries]

        return self.batch_change_alarm
