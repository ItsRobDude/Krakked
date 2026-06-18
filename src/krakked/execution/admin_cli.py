"""Utility CLI for inspecting and managing execution state."""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from typing import Any, List, Optional
from uuid import uuid4

from krakked.bootstrap import bootstrap
from krakked.config import load_config
from krakked.execution.oms import SUBMIT_INTENT_STATUSES, ExecutionService
from krakked.execution.order_correlation import (
    CorrelationState,
    classify_client_order_id_matches,
)
from krakked.market_data.api import MarketDataAPI
from krakked.portfolio.store import SQLitePortfolioStore
from krakked.strategy.models import RiskStatus

logger = logging.getLogger(__name__)


def _admin_cli_risk_status() -> RiskStatus:
    """Risk provider stub for the admin CLI.

    The admin helpers do not execute new plans; they reconcile, list, and cancel
    orders. We still supply a provider to satisfy ``ExecutionService``'s live-mode
    requirement without depending on the full strategy/risk stack.
    """

    return RiskStatus(
        kill_switch_active=False,
        daily_drawdown_pct=0.0,
        drift_flag=False,
        total_exposure_pct=0.0,
        manual_exposure_pct=0.0,
        per_asset_exposure_pct={},
        per_strategy_exposure_pct={},
        drift_info={"source": "admin_cli"},
    )


def _build_service(db_path: str, allow_interactive_setup: bool) -> ExecutionService:
    config = load_config()
    client = None
    rate_limiter = None

    if config.execution.mode == "live" or not config.execution.validate_only:
        client, config, rate_limiter = bootstrap(
            allow_interactive_setup=allow_interactive_setup
        )

    store = SQLitePortfolioStore(
        db_path=db_path, auto_migrate_schema=config.portfolio.auto_migrate_schema
    )
    market_data = None
    if hasattr(config, "market_data"):
        market_data = MarketDataAPI(
            config, rest_client=client, rate_limiter=rate_limiter
        )
    service = ExecutionService(
        client=client,
        config=config.execution,
        store=store,
        market_data=market_data,
        rate_limiter=rate_limiter,
        risk_status_provider=_admin_cli_risk_status,
    )
    service.load_open_orders_from_store()
    return service


def _format_order(order) -> str:
    return (
        f"{order.local_id} | plan={order.plan_id} strategy={order.strategy_id} "
        f"pair={order.pair} side={order.side} status={order.status} "
        f"kraken_id={order.kraken_order_id or '-'}"
    )


def _client_order_id_for_order(order) -> Optional[str]:
    raw_request = getattr(order, "raw_request", None) or {}
    client_order_id = raw_request.get("cl_ord_id")
    if isinstance(client_order_id, str) and client_order_id.strip():
        return client_order_id.strip()
    return None


def _submit_intent_orders(service: ExecutionService, local_id: Optional[str] = None):
    orders = service.store.get_open_orders() if service.store else []  # type: ignore[call-arg]
    orders = [o for o in orders if o.status in SUBMIT_INTENT_STATUSES]
    if local_id:
        orders = [o for o in orders if o.local_id == local_id]
    return orders


def _correlate_remote(client: Any, client_order_id: str):
    """Classify both endpoints' lookups via the shared tri-state helper.

    Returns a list of ``(endpoint_name, CorrelationResult, is_closed)``.
    """
    results = []
    for endpoint_name, getter, result_key, is_closed in (
        ("OpenOrders", client.get_open_orders, "open", False),
        ("ClosedOrders", client.get_closed_orders, "closed", True),
    ):
        remote = getter({"cl_ord_id": client_order_id})
        matches = remote.get(result_key) or {}
        result = classify_client_order_id_matches(
            matches, expected_client_order_id=client_order_id
        )
        results.append((endpoint_name, result, is_closed))
    return results


def _raw_order_summary(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "(no payload)"
    descr_raw = payload.get("descr")
    descr = descr_raw if isinstance(descr_raw, dict) else {}
    return (
        f"status={payload.get('status', '-')} "
        f"pair={descr.get('pair', payload.get('pair', '-'))} "
        f"type={descr.get('type', payload.get('type', '-'))} "
        f"vol={payload.get('vol', '-')} "
        f"price={descr.get('price', payload.get('price', '-'))} "
        f"opentm={payload.get('opentm', '-')}"
    )


def list_open_orders(args: argparse.Namespace) -> int:
    service = _build_service(args.db_path, args.allow_interactive_setup)
    store_orders = service.store.get_open_orders() if service.store else []  # type: ignore[call-arg]
    memory_orders = service.get_open_orders()

    print("Persisted open orders:")
    if not store_orders:
        print("  (none)")
    else:
        for order in store_orders:
            print(f"  {_format_order(order)}")

    print("\nIn-memory open orders:")
    if not memory_orders:
        print("  (none)")
    else:
        for order in memory_orders:
            print(f"  {_format_order(order)}")

    return 0


def show_recent_executions(args: argparse.Namespace) -> int:
    service = _build_service(args.db_path, args.allow_interactive_setup)
    results = service.store.get_execution_results(limit=args.limit) if service.store else []  # type: ignore[call-arg]

    if not results:
        print("No execution results found.")
        return 0

    for result in results:
        status = "success" if result.success else "failed"
        error_text = "; ".join(result.errors)
        print(
            f"plan={result.plan_id} status={status} started={result.started_at} "
            f"completed={result.completed_at} errors={error_text or '-'}"
        )
    return 0


def reconcile_submit_intents(args: argparse.Namespace) -> int:
    service = _build_service(args.db_path, args.allow_interactive_setup)
    client = getattr(service.adapter, "client", None)
    if client is None:
        print("Cannot reconcile submit intents without a Kraken REST client.")
        return 1

    target_orders = _submit_intent_orders(service, args.local_id)
    if not target_orders:
        print("No matching submit_unknown/pending_submit orders found.")
        return 0

    exit_code = 0
    for order in target_orders:
        client_order_id = _client_order_id_for_order(order)
        if not client_order_id:
            print(f"{order.local_id}: missing cl_ord_id; left unresolved.")
            exit_code = 1
            continue

        try:
            correlations = _correlate_remote(client, client_order_id)
        except Exception as exc:  # noqa: BLE001
            print(f"{order.local_id}: reconciliation query failed: {exc}")
            exit_code = 1
            continue

        exact = next((c for c in correlations if c[1].is_exact), None)
        unsafe = next(
            (
                c
                for c in correlations
                if c[1].state
                in (CorrelationState.UNVERIFIED, CorrelationState.AMBIGUOUS)
            ),
            None,
        )

        if exact is not None:
            endpoint_name, result, is_closed = exact
            service._sync_remote_order(  # noqa: SLF001 - admin recovery command
                str(result.kraken_order_id),
                dict(result.payload or {}),
                is_closed=is_closed,
                client_order_id=client_order_id,
            )
            remote_status = (result.payload or {}).get("status") or (
                "closed" if is_closed else "open"
            )
            print(
                f"{order.local_id}: recovered via {endpoint_name}; "
                f"kraken_id={result.kraken_order_id} status={remote_status}."
            )
            continue

        if unsafe is not None:
            endpoint_name, result, _is_closed = unsafe
            print(
                f"{order.local_id}: {result.state.value} {endpoint_name} result "
                f"({result.reason}); left unresolved. After manual verification "
                f"use force-link-submit-unknown / force-clear-submit-unknown."
            )
            exit_code = 1
            continue

        print(f"{order.local_id}: no Kraken match for cl_ord_id={client_order_id}.")

    return exit_code


def clear_submit_unknown(args: argparse.Namespace) -> int:
    if not args.confirmed_absent:
        print("Refusing to clear without --confirmed-absent.")
        return 1

    service = _build_service(args.db_path, args.allow_interactive_setup)
    client = getattr(service.adapter, "client", None)
    if client is None:
        print("Cannot confirm absence without a Kraken REST client.")
        return 1

    target_orders = _submit_intent_orders(service, args.local_id)
    if not target_orders:
        print("No matching submit_unknown order found.")
        return 1
    order = target_orders[0]
    if order.status != "submit_unknown":
        print(f"Refusing to clear {order.local_id}: status is {order.status}.")
        return 1

    client_order_id = _client_order_id_for_order(order)
    if not client_order_id:
        print(f"Refusing to clear {order.local_id}: missing cl_ord_id.")
        return 1

    try:
        correlations = _correlate_remote(client, client_order_id)
    except Exception as exc:  # noqa: BLE001
        print(f"Absence check failed: {exc}")
        return 1

    for endpoint_name, result, _is_closed in correlations:
        if result.state is CorrelationState.EXACT:
            print(
                f"Refusing to clear {order.local_id}: {endpoint_name} has an exact "
                f"cl_ord_id match. Run reconcile-submit-intents instead."
            )
            return 1
        if result.state in (CorrelationState.UNVERIFIED, CorrelationState.AMBIGUOUS):
            print(
                f"Refusing to clear {order.local_id}: {endpoint_name} returned an "
                f"unverifiable candidate ({result.reason}). Inspect raw Kraken "
                f"state and use force-clear-submit-unknown if you accept the risk."
            )
            return 1

    message = (
        "Operator cleared submit_unknown after confirmed absence from Kraken "
        f"OpenOrders/ClosedOrders at {datetime.now(UTC).isoformat()}"
    )
    if service.store:
        service.store.update_order_status(
            local_id=order.local_id,
            status="submit_absent",
            last_error=message,
            event_message=message,
        )
    service.open_orders.pop(order.local_id, None)
    print(f"{order.local_id}: marked submit_absent after confirmed absence.")
    return 0


def _force_audit_dict(
    order: Any,
    *,
    command: str,
    reason: str,
    kraken_order_id: Optional[str],
    raw_summary: Optional[str],
    action_at: str,
) -> dict[str, Any]:
    """Build the structured force-resolve audit record shared by all force paths."""
    return {
        "force_resolve": {
            "command": command,
            "reason": reason,
            "operator_action_at": action_at,
            "local_id": order.local_id,
            "expected_cl_ord_id": _client_order_id_for_order(order),
            "kraken_order_id": kraken_order_id,
            "raw_candidate": raw_summary,
        }
    }


def _record_force_audit(
    service: ExecutionService,
    order: Any,
    *,
    command: str,
    reason: str,
    status: str,
    kraken_order_id: Optional[str] = None,
    raw_summary: Optional[str] = None,
) -> None:
    """Persist durable, operator-attributed evidence for a force action."""
    action_at = datetime.now(UTC).isoformat()
    audit = _force_audit_dict(
        order,
        command=command,
        reason=reason,
        kraken_order_id=kraken_order_id,
        raw_summary=raw_summary,
        action_at=action_at,
    )
    summary = f"FORCE {command} by operator at {action_at}: {reason}"
    if service.store:
        service.store.update_order_status(
            local_id=order.local_id,
            status=status,
            kraken_order_id=kraken_order_id,
            last_error=summary,
            raw_response=audit,
            event_message=summary,
        )
    if status not in {"open", "partially_filled"}:
        service.open_orders.pop(order.local_id, None)


def force_link_submit_unknown(args: argparse.Namespace) -> int:
    if not args.reason or not args.reason.strip():
        print("Refusing to force-link without --reason.")
        return 1

    service = _build_service(args.db_path, args.allow_interactive_setup)
    target_orders = _submit_intent_orders(service, args.local_id)
    if not target_orders:
        print(f"No submit intent order found for local-id={args.local_id}.")
        return 1
    order = target_orders[0]
    reason = args.reason.strip()

    # Locate the raw order so we can both verify the txid and import fill state.
    found_payload: Optional[dict] = None
    found_is_closed = False
    lookup_error: Optional[str] = None
    client = getattr(service.adapter, "client", None)
    if client is not None:
        try:
            for getter, key, is_closed in (
                (client.get_open_orders, "open", False),
                (client.get_closed_orders, "closed", True),
            ):
                payload = (getter() or {}).get(key, {}).get(args.kraken_id)
                if isinstance(payload, dict):
                    found_payload = payload
                    found_is_closed = is_closed
                    break
        except Exception as exc:  # noqa: BLE001
            lookup_error = str(exc)

    if found_payload is None:
        # Fail closed: never link to an unverifiable txid by default.
        if not getattr(args, "allow_unverified_txid", False):
            detail = (
                f"raw lookup failed: {lookup_error}"
                if lookup_error
                else "txid not found in current OpenOrders/ClosedOrders"
            )
            print(
                f"Refusing to force-link {order.local_id}: {detail}. "
                f"Re-run with --allow-unverified-txid only if you accept the risk."
            )
            return 1

        _record_force_audit(
            service,
            order,
            command="force-link-submit-unknown",
            reason=reason,
            status="open",
            kraken_order_id=args.kraken_id,
            raw_summary="(txid not found; operator accepted unverified link)",
        )
        print(
            f"{order.local_id}: force-linked (UNVERIFIED) to kraken_id="
            f"{args.kraken_id}; operator accepted risk."
        )
        return 0

    # Found: import the real status/fills via the normal sync path, then layer an
    # audit event on top without clobbering the synced fill fields.
    raw_summary = _raw_order_summary(found_payload)
    link_status = found_payload.get("status") or (
        "closed" if found_is_closed else "open"
    )
    service._sync_remote_order(  # noqa: SLF001 - admin recovery command
        args.kraken_id,
        found_payload,
        is_closed=found_is_closed,
        client_order_id=_client_order_id_for_order(order),
    )
    action_at = datetime.now(UTC).isoformat()
    audit = _force_audit_dict(
        order,
        command="force-link-submit-unknown",
        reason=reason,
        kraken_order_id=args.kraken_id,
        raw_summary=raw_summary,
        action_at=action_at,
    )
    # Preserve the synced remote payload AND attach structured audit evidence;
    # fill columns set by _sync_remote_order survive because they are not passed.
    merged_response = {**found_payload, **audit}
    summary = (
        f"FORCE force-link-submit-unknown by operator at {action_at}: {reason} "
        f"(kraken_id={args.kraken_id}; raw={raw_summary})"
    )
    if service.store:
        service.store.update_order_status(
            local_id=order.local_id,
            status=link_status,
            kraken_order_id=args.kraken_id,
            last_error=summary,
            raw_response=merged_response,
            event_message=summary,
        )
    print(
        f"{order.local_id}: force-linked to kraken_id={args.kraken_id} "
        f"status={link_status}; raw={raw_summary}"
    )
    return 0


def force_clear_submit_unknown(args: argparse.Namespace) -> int:
    if not args.reason or not args.reason.strip():
        print("Refusing to force-clear without --reason.")
        return 1

    service = _build_service(args.db_path, args.allow_interactive_setup)
    target_orders = _submit_intent_orders(service, args.local_id)
    if not target_orders:
        print(f"No submit intent order found for local-id={args.local_id}.")
        return 1
    order = target_orders[0]

    _record_force_audit(
        service,
        order,
        command="force-clear-submit-unknown",
        reason=args.reason.strip(),
        status="submit_absent",
    )
    print(
        f"{order.local_id}: force-cleared to submit_absent "
        f"(operator accepted risk): {args.reason.strip()}"
    )
    return 0


def probe_client_order_id(args: argparse.Namespace) -> int:
    service = _build_service(args.db_path, args.allow_interactive_setup)
    client = getattr(service.adapter, "client", None)
    if client is None:
        print("Cannot probe cl_ord_id support without a Kraken REST client.")
        return 1

    client_order_id = str(uuid4())
    payload = {
        "pair": args.pair,
        "type": "buy",
        "ordertype": "limit",
        "volume": str(args.volume),
        "price": str(args.price),
        "validate": 1,
        "cl_ord_id": client_order_id,
    }

    try:
        client.add_order(payload)
        client.get_open_orders({"cl_ord_id": client_order_id})
        client.get_closed_orders({"cl_ord_id": client_order_id})
    except Exception as exc:  # noqa: BLE001
        print(f"cl_ord_id validate-only probe failed: {exc}")
        return 1

    print(
        "cl_ord_id validate-only probe passed. This proves parameter acceptance "
        "only: AddOrder accepted cl_ord_id and the query endpoints accepted the "
        "parameter without error. It does NOT prove a live order is queryable by "
        "cl_ord_id or that Kraken echoes it back."
    )
    return 0


def _filter_orders(service: ExecutionService, args: argparse.Namespace) -> List:
    orders = service.store.get_open_orders(plan_id=args.plan_id, strategy_id=args.strategy_id) if service.store else []  # type: ignore[call-arg]

    if args.kraken_id:
        orders = [o for o in orders if o.kraken_order_id == args.kraken_id]
    if args.local_id:
        orders = [o for o in orders if o.local_id == args.local_id]

    return orders


def cancel_orders(args: argparse.Namespace) -> int:
    if not args.all and not any(
        [args.plan_id, args.strategy_id, args.kraken_id, args.local_id]
    ):
        print(
            "Refusing to cancel without a filter; pass --all to cancel every open order."
        )
        return 1

    service = _build_service(args.db_path, args.allow_interactive_setup)
    target_orders = _filter_orders(service, args)

    if args.all and service.store:
        target_orders = service.store.get_open_orders(plan_id=args.plan_id, strategy_id=args.strategy_id)  # type: ignore[call-arg]

    if not target_orders:
        print("No matching open orders found.")
        return 0

    service.cancel_orders(target_orders)
    print(f"Requested cancellation for {len(target_orders)} order(s).")
    return 0


def panic(args: argparse.Namespace) -> int:
    logger.error("PANIC: canceling all orders", extra={"event": "execution_panic"})

    service = _build_service(args.db_path, args.allow_interactive_setup)
    service.cancel_all()

    print("Panic cancel-all issued.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execution admin helpers")
    parser.add_argument(
        "--db-path", default="portfolio.db", help="Path to the SQLite portfolio store"
    )
    parser.add_argument(
        "--allow-interactive-setup",
        action="store_true",
        help="Allow credential prompts when bootstrapping REST clients",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    open_parser = sub.add_parser(
        "list-open", help="List open/pending orders from SQLite and memory"
    )
    open_parser.set_defaults(func=list_open_orders)

    executions_parser = sub.add_parser(
        "recent-executions", help="Show recent execution results"
    )
    executions_parser.add_argument(
        "--limit", type=int, default=10, help="Number of execution results to display"
    )
    executions_parser.set_defaults(func=show_recent_executions)

    reconcile_parser = sub.add_parser(
        "reconcile-submit-intents",
        help="Query Kraken by cl_ord_id and recover submit_unknown/pending_submit orders",
    )
    reconcile_parser.add_argument("--local-id", help="Restrict to one local order id")
    reconcile_parser.set_defaults(func=reconcile_submit_intents)

    clear_parser = sub.add_parser(
        "clear-submit-unknown",
        help="Mark one submit_unknown order absent after a confirmed Kraken no-match",
    )
    clear_parser.add_argument(
        "--local-id", required=True, help="Local order id to clear"
    )
    clear_parser.add_argument(
        "--confirmed-absent",
        action="store_true",
        help="Required acknowledgement after Kraken no-match verification",
    )
    clear_parser.set_defaults(func=clear_submit_unknown)

    force_link_parser = sub.add_parser(
        "force-link-submit-unknown",
        help="Operator-confirmed link of a submit intent to a Kraken txid (audited)",
    )
    force_link_parser.add_argument("--local-id", required=True, help="Local order id")
    force_link_parser.add_argument(
        "--kraken-id",
        required=True,
        help="Kraken txid to link after manual verification",
    )
    force_link_parser.add_argument(
        "--reason", required=True, help="Operator reason recorded in the audit trail"
    )
    force_link_parser.add_argument(
        "--allow-unverified-txid",
        action="store_true",
        help="Permit linking when the txid is not found in OpenOrders/ClosedOrders",
    )
    force_link_parser.set_defaults(func=force_link_submit_unknown)

    force_clear_parser = sub.add_parser(
        "force-clear-submit-unknown",
        help="Operator-confirmed clear of a submit intent to submit_absent (audited)",
    )
    force_clear_parser.add_argument("--local-id", required=True, help="Local order id")
    force_clear_parser.add_argument(
        "--reason", required=True, help="Operator reason recorded in the audit trail"
    )
    force_clear_parser.set_defaults(func=force_clear_submit_unknown)

    probe_parser = sub.add_parser(
        "probe-cl-ord-id",
        help="Check Kraken accepts the cl_ord_id parameter on validate-only AddOrder and queries (parameter acceptance only)",
    )
    probe_parser.add_argument("--pair", required=True, help="Kraken pair, e.g. XBTUSD")
    probe_parser.add_argument("--volume", required=True, help="Validate-only volume")
    probe_parser.add_argument(
        "--price", required=True, help="Validate-only limit price"
    )
    probe_parser.set_defaults(func=probe_client_order_id)

    cancel_parser = sub.add_parser(
        "cancel", help="Cancel open orders for a plan or strategy"
    )
    cancel_parser.add_argument(
        "--plan-id", help="Restrict cancellations to a specific plan id"
    )
    cancel_parser.add_argument(
        "--strategy-id", help="Restrict cancellations to a specific strategy id"
    )
    cancel_parser.add_argument("--kraken-id", help="Cancel a specific Kraken order id")
    cancel_parser.add_argument("--local-id", help="Cancel a specific local order id")
    cancel_parser.add_argument(
        "--all", action="store_true", help="Cancel all open orders that match filters"
    )
    cancel_parser.set_defaults(func=cancel_orders)

    panic_parser = sub.add_parser(
        "panic", help="Cancel all open orders after refreshing state"
    )
    panic_parser.set_defaults(func=panic)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(main())
