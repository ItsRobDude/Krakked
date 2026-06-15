"""Import local OHLCVT history files into the Krakked OHLC store."""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, TextIO

from krakked.market_data.models import OHLCBar

KRAKEN_INTERVAL_BY_TIMEFRAME: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "12h": 720,
    "1d": 1440,
}


@dataclass
class OHLCImportParseResult:
    input_path: str
    canonical_pair: str
    timeframe: str
    interval_minutes: int
    start_timestamp: int | None = None
    end_timestamp: int | None = None
    rows_read: int = 0
    rows_skipped_filter: int = 0
    rows_skipped_invalid: int = 0
    bars: list[OHLCBar] = field(default_factory=list)
    matched_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors:
            return "failed"
        if not self.matched_files:
            return "no_matching_files"
        if not self.bars:
            return "empty"
        return "ready"

    def to_dict(self) -> dict[str, object]:
        first = min((bar.timestamp for bar in self.bars), default=None)
        last = max((bar.timestamp for bar in self.bars), default=None)
        return {
            "input_path": self.input_path,
            "canonical_pair": self.canonical_pair,
            "timeframe": self.timeframe,
            "interval_minutes": self.interval_minutes,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "matched_files": list(self.matched_files),
            "rows_read": self.rows_read,
            "rows_skipped_filter": self.rows_skipped_filter,
            "rows_skipped_invalid": self.rows_skipped_invalid,
            "bars_ready": len(self.bars),
            "first_bar_timestamp": first,
            "first_bar_at": _timestamp_to_text(first),
            "last_bar_timestamp": last,
            "last_bar_at": _timestamp_to_text(last),
            "status": self.status,
            "errors": list(self.errors),
        }


def parse_kraken_ohlcvt_files(
    input_path: str | Path,
    *,
    canonical_pair: str,
    timeframe: str,
    start_timestamp: int | None = None,
    end_timestamp: int | None = None,
) -> OHLCImportParseResult:
    """Parse Kraken OHLCVT CSV/ZIP input and return closed-candle OHLC bars.

    Kraken OHLCVT rows are expected as:
    timestamp, open, high, low, close, volume, trades.
    The trades column is intentionally ignored because Krakked's OHLC store only
    persists timestamp/OHLC/volume.
    """

    path = Path(input_path).expanduser().resolve()
    interval = KRAKEN_INTERVAL_BY_TIMEFRAME.get(timeframe)
    if interval is None:
        raise ValueError(
            f"Unsupported timeframe for Kraken OHLCVT import: {timeframe}. "
            f"Supported: {sorted(KRAKEN_INTERVAL_BY_TIMEFRAME)}"
        )

    result = OHLCImportParseResult(
        input_path=str(path),
        canonical_pair=str(canonical_pair),
        timeframe=str(timeframe),
        interval_minutes=int(interval),
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )

    if not path.exists():
        result.errors.append(f"Input path does not exist: {path}")
        return result

    try:
        if path.is_dir():
            _parse_directory(path, result)
        elif path.suffix.lower() == ".zip":
            _parse_zip(path, result)
        else:
            if _is_matching_kraken_ohlcvt_name(path.name, result):
                with path.open("r", encoding="utf-8", newline="") as handle:
                    _parse_csv_rows(path.name, handle, result)
            else:
                result.errors.append(
                    f"Input file does not match {canonical_pair}_{interval}*.csv"
                )
    except Exception as exc:  # noqa: BLE001 - surfaced in import report
        result.errors.append(str(exc))

    result.bars = _dedupe_sort_bars(result.bars)
    return result


def _parse_directory(path: Path, result: OHLCImportParseResult) -> None:
    for child in sorted(path.rglob("*.csv")):
        if not _is_matching_kraken_ohlcvt_name(child.name, result):
            continue
        with child.open("r", encoding="utf-8", newline="") as handle:
            _parse_csv_rows(str(child.relative_to(path)), handle, result)


def _parse_zip(path: Path, result: OHLCImportParseResult) -> None:
    with zipfile.ZipFile(path) as archive:
        for name in sorted(archive.namelist()):
            if name.endswith("/") or not _is_matching_kraken_ohlcvt_name(name, result):
                continue
            with archive.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                _parse_csv_rows(name, text, result)


def _is_matching_kraken_ohlcvt_name(
    filename: str,
    result: OHLCImportParseResult,
) -> bool:
    name = Path(filename).name.lower()
    if not name.endswith(".csv"):
        return False
    stem_parts = Path(name).stem.replace("-", "_").split("_")
    pair = result.canonical_pair.lower()
    interval = str(result.interval_minutes)
    return pair in stem_parts and interval in stem_parts


def _parse_csv_rows(
    source_name: str,
    handle: TextIO,
    result: OHLCImportParseResult,
) -> None:
    result.matched_files.append(source_name)
    reader = csv.reader(handle)
    for row in reader:
        result.rows_read += 1
        bar = _parse_kraken_row(row)
        if bar is None:
            result.rows_skipped_invalid += 1
            continue
        if (
            result.start_timestamp is not None
            and bar.timestamp < result.start_timestamp
        ):
            result.rows_skipped_filter += 1
            continue
        if result.end_timestamp is not None and bar.timestamp >= result.end_timestamp:
            result.rows_skipped_filter += 1
            continue
        result.bars.append(bar)


def _parse_kraken_row(row: Iterable[str]) -> OHLCBar | None:
    values = [str(value).strip() for value in row]
    if len(values) < 6:
        return None
    try:
        timestamp = int(float(values[0]))
        if timestamp > 10_000_000_000:
            timestamp //= 1000
        return OHLCBar(
            timestamp=timestamp,
            open=float(values[1]),
            high=float(values[2]),
            low=float(values[3]),
            close=float(values[4]),
            volume=float(values[5]),
        )
    except (TypeError, ValueError):
        return None


def _dedupe_sort_bars(bars: list[OHLCBar]) -> list[OHLCBar]:
    by_timestamp = {int(bar.timestamp): bar for bar in bars}
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def _timestamp_to_text(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(int(timestamp), tz=UTC).isoformat()
