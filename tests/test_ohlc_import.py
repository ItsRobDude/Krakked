from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any, cast

from krakked.market_data.models import OHLCBar
from krakked.market_data.ohlc_import import (
    analyze_ohlc_continuity,
    parse_kraken_ohlcvt_files,
)


def _bar(timestamp: int) -> OHLCBar:
    return OHLCBar(
        timestamp=timestamp,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=10.0,
    )


def test_parse_kraken_ohlcvt_zip_filters_pair_interval_and_dates(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "ohlcvt.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "XBTUSD_240.csv",
            "\n".join(
                [
                    "timestamp,open,high,low,close,volume,trades",
                    "1764547200,100,110,90,105,1.5,10",
                    "1764561600,105,115,95,108,1.7,11",
                    "1764576000,108,116,99,112,1.9,12",
                ]
            ),
        )
        archive.writestr(
            "ETHUSD_240.csv",
            "1764561600,1,2,1,2,3,4",
        )
        archive.writestr(
            "XBTUSD_60.csv",
            "1764561600,1,2,1,2,3,4",
        )

    result = parse_kraken_ohlcvt_files(
        archive_path,
        canonical_pair="XBTUSD",
        timeframe="4h",
        start_timestamp=1764561600,
        end_timestamp=1764576000,
    )

    assert result.status == "ready"
    assert result.matched_files == ["XBTUSD_240.csv"]
    assert result.rows_read == 4
    assert result.rows_skipped_invalid == 1
    assert result.rows_skipped_filter == 2
    assert len(result.bars) == 1
    assert result.bars[0].timestamp == 1764561600
    assert result.bars[0].open == 105.0
    assert result.bars[0].close == 108.0
    continuity = cast(dict[str, Any], result.to_dict()["continuity"])
    assert continuity["status"] == "continuous"


def test_parse_kraken_ohlcvt_reports_no_matching_file(tmp_path: Path) -> None:
    csv_path = tmp_path / "ETHUSD_240.csv"
    csv_path.write_text("1764561600,1,2,1,2,3,4", encoding="utf-8")

    result = parse_kraken_ohlcvt_files(
        csv_path,
        canonical_pair="XBTUSD",
        timeframe="4h",
    )

    assert result.status == "failed"
    assert result.bars == []
    assert result.errors


def test_analyze_ohlc_continuity_reports_exact_missing_intervals() -> None:
    report = analyze_ohlc_continuity(
        [_bar(0), _bar(3600), _bar(10800), _bar(10800)],
        timeframe="1h",
        pair="XBTUSD",
    )

    payload = report.to_dict()
    assert payload["status"] == "gapped"
    assert payload["input_bar_count"] == 4
    assert payload["bar_count"] == 3
    assert payload["duplicate_timestamp_count"] == 1
    assert payload["expected_interval_seconds"] == 3600
    assert payload["expected_bar_count_between_first_last"] == 4
    assert payload["missing_interval_count"] == 1
    assert payload["gap_count"] == 1
    assert payload["gaps"] == [
        {
            "previous_bar_timestamp": 3600,
            "previous_bar_at": "1970-01-01T01:00:00+00:00",
            "next_bar_timestamp": 10800,
            "next_bar_at": "1970-01-01T03:00:00+00:00",
            "missing_start_timestamp": 7200,
            "missing_start_at": "1970-01-01T02:00:00+00:00",
            "missing_end_timestamp": 7200,
            "missing_end_at": "1970-01-01T02:00:00+00:00",
            "missing_interval_count": 1,
            "actual_delta_seconds": 7200,
            "expected_delta_seconds": 3600,
        }
    ]
