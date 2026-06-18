"""Deterministic fake Kraken REST client for money-safety lifecycle proofs.

This implements the subset of ``KrakenRESTClient`` that ``KrakenExecutionAdapter``
actually calls, with fault knobs that reproduce the dangerous real-world cases:

- ``REJECT``              -> exchange rejects the order (populated ``error`` list)
- ``ERROR_BEFORE_ACCEPT`` -> failure before the exchange records anything
- ``ACCEPT_THEN_LOST``    -> the *critical* one: the exchange accepts the order
  and assigns a txid (the order is now live and queryable), but the caller
  never receives the response (timeout / crash window)
- ``RATE_LIMIT`` / ``SERVICE_UNAVAILABLE`` -> transient errors the adapter retries

It is test-only and must never be imported by production code. Return shapes match
the real client contract: ``add_order`` returns ``{"error": [], "txid": [id]}`` on
success and ``{"error": [msg]}`` on rejection; open/closed queries return
``{"open": {...}}`` / ``{"closed": {...}}``.
"""

from __future__ import annotations

from typing import Any, Optional

from krakked.connection.exceptions import (
    RateLimitError,
    ServiceUnavailableError,
)

# add_order fault modes
ACCEPT = "accept"
REJECT = "reject"
ERROR_BEFORE_ACCEPT = "error_before_accept"
ACCEPT_THEN_LOST = "accept_then_lost"
RATE_LIMIT = "rate_limit"
SERVICE_UNAVAILABLE = "service_unavailable"


class FakeKrakenRESTClient:
    """A minimal deterministic stand-in for ``KrakenRESTClient``."""

    def __init__(
        self,
        *,
        add_order_mode: str = ACCEPT,
        add_order_modes: Optional[list[str]] = None,
        reject_error: str = "EOrder:Insufficient funds",
        echo_client_order_id: bool = True,
    ) -> None:
        self.add_order_mode = add_order_mode
        self._add_order_modes = list(add_order_modes or [])
        self.reject_error = reject_error
        self.duplicate_client_order_matches = False
        # When False, the exchange still filters by cl_ord_id but does NOT echo
        # it back in the returned order payload (the unproven-echo case).
        self.echo_client_order_id = echo_client_order_id

        # Simulated exchange state.
        self._open: dict[str, dict[str, Any]] = {}
        self._closed: dict[str, dict[str, Any]] = {}
        self._txid_counter = 0

        # Observability for assertions.
        self.add_order_calls: list[dict[str, Any]] = []
        self.get_open_order_calls: list[dict[str, Any]] = []
        self.get_closed_order_calls: list[dict[str, Any]] = []
        self.cancel_calls: list[str] = []
        self.cancel_all_calls = 0
        self.cancel_all_after_calls: list[int] = []

    # ------------------------------------------------------------------ helpers
    def _next_txid(self) -> str:
        self._txid_counter += 1
        return f"OFAKE-{self._txid_counter:06d}"

    def _record_open(self, params: dict[str, Any], txid: str) -> None:
        self._open[txid] = {
            "userref": params.get("userref"),
            "cl_ord_id": params.get("cl_ord_id"),
            "status": "open",
            "vol": params.get("volume"),
            "vol_exec": "0.0",
            "descr": {
                "pair": params.get("pair"),
                "type": params.get("type"),
                "ordertype": params.get("ordertype"),
                "price": params.get("price"),
            },
        }

    def _echoed(self, detail: dict[str, Any]) -> dict[str, Any]:
        """Return the order detail as the exchange would surface it.

        When ``echo_client_order_id`` is False the exchange filtered correctly
        but does not surface ``cl_ord_id`` in the order payload.
        """
        if self.echo_client_order_id:
            return dict(detail)
        return {key: value for key, value in detail.items() if key != "cl_ord_id"}

    # -------------------------------------------------- KrakenRESTClient surface
    def add_order(self, params: dict[str, Any]) -> dict[str, Any]:
        self.add_order_calls.append(dict(params))
        mode = self._add_order_modes.pop(0) if self._add_order_modes else self.add_order_mode

        if mode == REJECT:
            return {"error": [self.reject_error]}
        if mode == ERROR_BEFORE_ACCEPT:
            # Nothing is recorded on the exchange.
            raise ServiceUnavailableError("fake: error before acceptance")
        if mode == RATE_LIMIT:
            raise RateLimitError("fake: rate limited")
        if mode == SERVICE_UNAVAILABLE:
            raise ServiceUnavailableError("fake: service unavailable")

        # ACCEPT and ACCEPT_THEN_LOST both mean the exchange accepted the order
        # and assigned a txid -- the order is now live.
        txid = self._next_txid()
        self._record_open(params, txid)
        if mode == ACCEPT_THEN_LOST:
            # Exchange accepted + assigned txid, but the caller never sees it.
            raise ServiceUnavailableError(
                "fake: response lost after exchange acceptance"
            )
        return {"error": [], "txid": [txid]}

    def get_open_orders(
        self, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        self.get_open_order_calls.append(dict(params or {}))
        userref = (params or {}).get("userref")
        client_order_id = (params or {}).get("cl_ord_id")
        if userref is not None:
            open_orders = {
                txid: detail
                for txid, detail in self._open.items()
                if detail.get("userref") == userref
            }
        elif client_order_id is not None:
            open_orders = {
                txid: self._echoed(detail)
                for txid, detail in self._open.items()
                if detail.get("cl_ord_id") == client_order_id
            }
            if self.duplicate_client_order_matches and open_orders:
                first_detail = next(iter(open_orders.values()))
                open_orders["OFAKE-DUPLICATE"] = dict(first_detail)
        else:
            open_orders = dict(self._open)
        return {"open": open_orders}

    def get_closed_orders(
        self, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        self.get_closed_order_calls.append(dict(params or {}))
        userref = (params or {}).get("userref")
        client_order_id = (params or {}).get("cl_ord_id")
        if userref is not None:
            closed_orders = {
                txid: detail
                for txid, detail in self._closed.items()
                if detail.get("userref") == userref
            }
        elif client_order_id is not None:
            closed_orders = {
                txid: self._echoed(detail)
                for txid, detail in self._closed.items()
                if detail.get("cl_ord_id") == client_order_id
            }
        else:
            closed_orders = dict(self._closed)
        return {"closed": closed_orders}

    def cancel_order(self, txid: str) -> dict[str, Any]:
        self.cancel_calls.append(txid)
        detail = self._open.pop(txid, None)
        if detail is not None:
            detail["status"] = "canceled"
            self._closed[txid] = detail
        return {"error": [], "count": 1 if detail else 0}

    def cancel_all_orders(self) -> dict[str, Any]:
        self.cancel_all_calls += 1
        count = len(self._open)
        for txid, detail in list(self._open.items()):
            detail["status"] = "canceled"
            self._closed[txid] = detail
        self._open.clear()
        return {"error": [], "count": count}

    def cancel_all_orders_after(self, timeout_seconds: int) -> dict[str, Any]:
        self.cancel_all_after_calls.append(timeout_seconds)
        return {"error": []}

    # ----------------------------------------------------- test convenience API
    @property
    def open_count(self) -> int:
        return len(self._open)

    def open_orders_for_userref(self, userref: Optional[int]) -> list[str]:
        return [
            txid
            for txid, detail in self._open.items()
            if detail.get("userref") == userref
        ]
