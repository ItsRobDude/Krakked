"""Unit tests for the shared cl_ord_id attribution helper."""

from __future__ import annotations

from krakked.execution.order_correlation import (
    CorrelationState,
    classify_client_order_id_matches,
    payload_client_order_id,
)

EXPECTED = "local-123"


def test_zero_results_is_none() -> None:
    result = classify_client_order_id_matches({}, expected_client_order_id=EXPECTED)
    assert result.state is CorrelationState.NONE
    assert result.is_none
    assert result.raw_count == 0
    assert result.kraken_order_id is None


def test_single_exact_echo_is_exact() -> None:
    matches = {"OABC": {"cl_ord_id": EXPECTED, "status": "open"}}
    result = classify_client_order_id_matches(
        matches, expected_client_order_id=EXPECTED
    )
    assert result.state is CorrelationState.EXACT
    assert result.is_exact
    assert result.kraken_order_id == "OABC"
    assert result.payload == matches["OABC"]


def test_single_exact_echo_in_descr_is_exact() -> None:
    matches = {"OABC": {"descr": {"cl_ord_id": EXPECTED}, "status": "open"}}
    result = classify_client_order_id_matches(
        matches, expected_client_order_id=EXPECTED
    )
    assert result.state is CorrelationState.EXACT
    assert result.kraken_order_id == "OABC"


def test_single_missing_echo_is_unverified_not_none() -> None:
    matches = {"OABC": {"status": "open", "vol": "1.0"}}
    result = classify_client_order_id_matches(
        matches, expected_client_order_id=EXPECTED
    )
    assert result.state is CorrelationState.UNVERIFIED
    assert not result.is_none  # must be distinct from NONE for clear safety
    assert result.kraken_order_id is None


def test_single_mismatched_echo_is_unverified() -> None:
    matches = {"OABC": {"cl_ord_id": "someone-else", "status": "open"}}
    result = classify_client_order_id_matches(
        matches, expected_client_order_id=EXPECTED
    )
    assert result.state is CorrelationState.UNVERIFIED
    assert result.kraken_order_id is None


def test_multiple_results_is_ambiguous_even_if_one_matches() -> None:
    matches = {
        "OABC": {"cl_ord_id": EXPECTED, "status": "open"},
        "OXYZ": {"cl_ord_id": "other", "status": "open"},
    }
    result = classify_client_order_id_matches(
        matches, expected_client_order_id=EXPECTED
    )
    assert result.state is CorrelationState.AMBIGUOUS
    assert result.raw_count == 2
    assert result.kraken_order_id is None


def test_payload_client_order_id_variants() -> None:
    assert payload_client_order_id({"cl_ord_id": "x"}) == "x"
    assert payload_client_order_id({"clOrdId": "y"}) == "y"
    assert payload_client_order_id({"descr": {"cl_ord_id": "z"}}) == "z"
    assert payload_client_order_id({"status": "open"}) is None
    assert payload_client_order_id({"cl_ord_id": "  "}) is None
    assert payload_client_order_id("not-a-mapping") is None
