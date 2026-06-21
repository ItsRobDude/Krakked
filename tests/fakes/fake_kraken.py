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

from copy import deepcopy
from decimal import Decimal
from typing import Any, Optional

from krakked.connection.exceptions import RateLimitError, ServiceUnavailableError

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
        cancel_all_leaves_orders_open: bool = False,
    ) -> None:
        self.add_order_mode = add_order_mode
        self._add_order_modes = list(add_order_modes or [])
        self.reject_error = reject_error
        self.duplicate_client_order_matches = False
        # When False, the exchange still filters by cl_ord_id but does NOT echo
        # it back in the returned order payload (the unproven-echo case).
        self.echo_client_order_id = echo_client_order_id
        self.cancel_all_leaves_orders_open = cancel_all_leaves_orders_open

        # Simulated exchange state.
        self._open: dict[str, dict[str, Any]] = {}
        self._closed: dict[str, dict[str, Any]] = {}
        self._balances: dict[str, Decimal] = {
            "ZUSD": Decimal("10000.0"),
            "XXBT": Decimal("0.0"),
        }
        self._trades: dict[str, dict[str, Any]] = {}
        self._ledgers: dict[str, dict[str, Any]] = {}
        self._txid_counter = 0
        self._trade_counter = 0
        self._ledger_counter = 0
        self._clock = Decimal("1.0")
        self._balance_failures_remaining = 0
        self._stale_balance_reads_remaining = 0
        self._stale_balance_snapshot: Optional[dict[str, str]] = None
        self._trades_failures_remaining = 0
        self._stale_trades_reads_remaining = 0
        self._stale_trades_snapshot: Optional[dict[str, dict[str, Any]]] = None
        self._ledgers_failures_remaining = 0
        self._stale_ledger_reads_remaining = 0
        self._stale_ledger_snapshot: Optional[dict[str, dict[str, Any]]] = None

        self._record_seed_ledger("ZUSD", Decimal("10000.0"))

        # Observability for assertions.
        self.add_order_calls: list[dict[str, Any]] = []
        self.get_open_order_calls: list[dict[str, Any]] = []
        self.get_closed_order_calls: list[dict[str, Any]] = []
        self.balance_read_count = 0
        self.cancel_calls: list[str] = []
        self.cancel_all_calls = 0
        self.cancel_all_after_calls: list[int] = []
        self.call_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ helpers
    def _next_txid(self) -> str:
        self._txid_counter += 1
        return f"OFAKE-{self._txid_counter:06d}"

    def _next_trade_id(self) -> str:
        self._trade_counter += 1
        return f"TFAKE-{self._trade_counter:06d}"

    def _next_ledger_id(self) -> str:
        self._ledger_counter += 1
        return f"LFAKE-{self._ledger_counter:06d}"

    def _next_time(self) -> float:
        self._clock += Decimal("1.0")
        return float(self._clock)

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        return format(value.quantize(Decimal("0.00000001")), "f")

    @staticmethod
    def _asset_codes(pair: str) -> tuple[str, str]:
        pair = pair.replace("/", "")
        base = pair[:3]
        quote = pair[3:]
        base_code = "XXBT" if base == "XBT" else base
        quote_code = "ZUSD" if quote == "USD" else quote
        return base_code, quote_code

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

    def _balance_response(self) -> dict[str, str]:
        return {
            asset: self._format_decimal(amount)
            for asset, amount in self._balances.items()
        }

    def _record_seed_ledger(self, asset: str, amount: Decimal) -> None:
        if amount == 0:
            return
        ledger_id = self._next_ledger_id()
        self._ledgers[ledger_id] = {
            "refid": "seed",
            "time": self._next_time(),
            "type": "deposit",
            "subtype": "",
            "aclass": "currency",
            "asset": asset,
            "amount": self._format_decimal(amount),
            "fee": "0.00000000",
            "balance": self._format_decimal(self._balances[asset]),
        }

    def _echoed(self, detail: dict[str, Any]) -> dict[str, Any]:
        """Return the order detail as the exchange would surface it.

        When ``echo_client_order_id`` is False the exchange filtered correctly
        but does not surface ``cl_ord_id`` in the order payload.
        """
        if self.echo_client_order_id:
            return dict(detail)
        return {key: value for key, value in detail.items() if key != "cl_ord_id"}

    def _record_trade_and_ledgers(
        self,
        *,
        txid: str,
        detail: dict[str, Any],
        volume: Decimal,
        price: Decimal,
        fee: Decimal,
    ) -> None:
        pair = str(detail.get("descr", {}).get("pair") or "")
        side = str(detail.get("descr", {}).get("type") or "buy")
        base_asset, quote_asset = self._asset_codes(pair)
        cost = (volume * price).quantize(Decimal("0.00000001"))
        event_time = self._next_time()
        trade_id = self._next_trade_id()

        if side == "buy":
            base_delta = volume
            quote_delta = -cost
        else:
            base_delta = -volume
            quote_delta = cost

        self._balances[base_asset] = (
            self._balances.get(base_asset, Decimal("0")) + base_delta
        )
        self._balances[quote_asset] = (
            self._balances.get(quote_asset, Decimal("0")) + quote_delta - fee
        )

        self._trades[trade_id] = {
            "ordertxid": txid,
            "pair": pair,
            "time": event_time,
            "type": side,
            "ordertype": detail.get("descr", {}).get("ordertype") or "limit",
            "price": self._format_decimal(price),
            "cost": self._format_decimal(cost),
            "fee": self._format_decimal(fee),
            "vol": self._format_decimal(volume),
            "margin": "0.0",
            "misc": "",
            "posstatus": None,
        }

        base_ledger_id = self._next_ledger_id()
        self._ledgers[base_ledger_id] = {
            "refid": trade_id,
            "time": event_time,
            "type": "trade",
            "subtype": "",
            "aclass": "currency",
            "asset": base_asset,
            "amount": self._format_decimal(base_delta),
            "fee": "0.00000000",
            "balance": self._format_decimal(self._balances[base_asset]),
        }

        quote_ledger_id = self._next_ledger_id()
        self._ledgers[quote_ledger_id] = {
            "refid": trade_id,
            "time": event_time,
            "type": "trade",
            "subtype": "",
            "aclass": "currency",
            "asset": quote_asset,
            "amount": self._format_decimal(quote_delta),
            "fee": self._format_decimal(fee),
            "balance": self._format_decimal(self._balances[quote_asset]),
        }

    def _filter_by_start(
        self, records: dict[str, dict[str, Any]], params: Optional[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        start = (params or {}).get("start")
        if start is None:
            return deepcopy(records)
        try:
            start_value = float(start)
        except (TypeError, ValueError):
            return deepcopy(records)
        return {
            record_id: deepcopy(record)
            for record_id, record in records.items()
            if float(record.get("time", 0.0) or 0.0) >= start_value
        }

    # -------------------------------------------------- KrakenRESTClient surface
    def get_private(
        self, endpoint: str, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        self.call_log.append(
            {"event": "get_private", "endpoint": endpoint, "params": dict(params or {})}
        )
        if endpoint == "Balance":
            self.balance_read_count += 1
            if self._balance_failures_remaining > 0:
                self._balance_failures_remaining -= 1
                raise ServiceUnavailableError("fake: balance unavailable")
            if (
                self._stale_balance_reads_remaining > 0
                and self._stale_balance_snapshot is not None
            ):
                self._stale_balance_reads_remaining -= 1
                return dict(self._stale_balance_snapshot)
            return self._balance_response()
        if endpoint == "TradesHistory":
            if self._trades_failures_remaining > 0:
                self._trades_failures_remaining -= 1
                raise ServiceUnavailableError("fake: trade history unavailable")
            trades = self._trades
            if (
                self._stale_trades_reads_remaining > 0
                and self._stale_trades_snapshot is not None
            ):
                self._stale_trades_reads_remaining -= 1
                trades = self._stale_trades_snapshot
            return {
                "trades": self._filter_by_start(trades, params),
                "last": None,
            }
        if endpoint == "OpenOrders":
            return self.get_open_orders(params=params)
        if endpoint == "ClosedOrders":
            return self.get_closed_orders(params=params)
        raise ServiceUnavailableError(f"fake: unsupported private endpoint {endpoint}")

    def add_order(self, params: dict[str, Any]) -> dict[str, Any]:
        self.call_log.append({"event": "add_order", "params": dict(params)})
        self.add_order_calls.append(dict(params))
        mode = (
            self._add_order_modes.pop(0)
            if self._add_order_modes
            else self.add_order_mode
        )

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
        self.call_log.append({"event": "get_open_orders", "params": dict(params or {})})
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
        self.call_log.append(
            {"event": "get_closed_orders", "params": dict(params or {})}
        )
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

    def get_ledgers(self, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self.call_log.append({"event": "get_ledgers", "params": dict(params or {})})
        if self._ledgers_failures_remaining > 0:
            self._ledgers_failures_remaining -= 1
            raise ServiceUnavailableError("fake: ledgers unavailable")
        ledgers = self._ledgers
        if (
            self._stale_ledger_reads_remaining > 0
            and self._stale_ledger_snapshot is not None
        ):
            self._stale_ledger_reads_remaining -= 1
            ledgers = self._stale_ledger_snapshot
        return {"ledger": self._filter_by_start(ledgers, params)}

    def cancel_order(self, txid: str) -> dict[str, Any]:
        self.call_log.append({"event": "cancel_order", "txid": txid})
        self.cancel_calls.append(txid)
        detail = self._open.pop(txid, None)
        if detail is not None:
            detail["status"] = "canceled"
            self._closed[txid] = detail
        return {"error": [], "count": 1 if detail else 0}

    def cancel_all_orders(self) -> dict[str, Any]:
        self.call_log.append({"event": "cancel_all_orders"})
        self.cancel_all_calls += 1
        count = len(self._open)
        if self.cancel_all_leaves_orders_open:
            return {"error": [], "count": count}
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

    def fail_balance_reads(self, count: int = 1) -> None:
        self._balance_failures_remaining = max(int(count), 0)

    def stale_balance_reads(self, count: int = 1) -> None:
        self._stale_balance_snapshot = self._balance_response()
        self._stale_balance_reads_remaining = max(int(count), 0)

    def fail_trades_history_reads(self, count: int = 1) -> None:
        self._trades_failures_remaining = max(int(count), 0)

    def stale_trades_history_reads(self, count: int = 1) -> None:
        self._stale_trades_snapshot = deepcopy(self._trades)
        self._stale_trades_reads_remaining = max(int(count), 0)

    def fail_ledger_reads(self, count: int = 1) -> None:
        self._ledgers_failures_remaining = max(int(count), 0)

    def stale_ledger_reads(self, count: int = 1) -> None:
        self._stale_ledger_snapshot = deepcopy(self._ledgers)
        self._stale_ledger_reads_remaining = max(int(count), 0)

    def set_clock(self, timestamp: float) -> None:
        self._clock = Decimal(str(timestamp))

    def fill_order(
        self,
        txid: str,
        *,
        price: Optional[float] = None,
        volume: Optional[float] = None,
        fee: float = 0.0,
    ) -> dict[str, Any]:
        detail = self._open.get(txid)
        if detail is None:
            raise ValueError(f"No open fake order {txid}")

        total_volume = Decimal(str(detail.get("vol") or "0"))
        already_filled = Decimal(str(detail.get("vol_exec") or "0"))
        remaining = max(total_volume - already_filled, Decimal("0"))
        fill_volume = remaining if volume is None else Decimal(str(volume))
        if fill_volume <= 0 or fill_volume > remaining:
            raise ValueError("Invalid fake fill volume")

        fill_price = Decimal(
            str(
                price
                if price is not None
                else detail.get("descr", {}).get("price") or "0"
            )
        )
        fill_fee = Decimal(str(fee))
        self._record_trade_and_ledgers(
            txid=txid,
            detail=detail,
            volume=fill_volume,
            price=fill_price,
            fee=fill_fee,
        )

        new_filled = already_filled + fill_volume
        prior_avg_price = Decimal(str(detail.get("price_avg") or "0"))
        prior_notional = already_filled * prior_avg_price
        average_price = (
            (prior_notional + (fill_volume * fill_price)) / new_filled
            if new_filled > 0
            else fill_price
        )
        detail["vol_exec"] = self._format_decimal(new_filled)
        detail["price"] = self._format_decimal(fill_price)
        detail["price_avg"] = self._format_decimal(average_price)

        if new_filled >= total_volume:
            detail["status"] = "closed"
            self._closed[txid] = detail
            self._open.pop(txid, None)
        else:
            detail["status"] = "partially_filled"

        return dict(detail)

    def partial_fill_order(
        self,
        txid: str,
        *,
        volume: float,
        price: Optional[float] = None,
        fee: float = 0.0,
    ) -> dict[str, Any]:
        return self.fill_order(txid, price=price, volume=volume, fee=fee)

    def close_order(
        self, txid: str, *, price: Optional[float] = None
    ) -> dict[str, Any]:
        return self.fill_order(txid, price=price)
