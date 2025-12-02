"""Utility CLI for inspecting and managing execution state."""

from __future__ import annotations

import argparse
import logging
from typing import List, Optional

from kraken_bot.bootstrap import bootstrap
from kraken_bot.config import load_config
from kraken_bot.execution.oms import ExecutionService
from kraken_bot.portfolio.store import SQLitePortfolioStore

logger = logging.getLogger(__name__)


def _build_service(db_path: str, allow_interactive_setup: bool) -> ExecutionService:
    config = load_config()
    client = None
    rate_limiter = None

    if config.execution.mode == "live" or not config.execution.validate_only:
        client, config, rate_limiter = bootstrap(allow_interactive_setup=allow_interactive_setup)

    store = SQLitePortfolioStore(db_path=db_path, auto_migrate_schema=config.portfolio.auto_migrate_schema)
    service = ExecutionService(
        client=client,
        config=config.execution,
        store=store,
        rate_limiter=rate_limiter,
    )
    service.load_open_orders_from_store()
    return service


def _format_order(order) -> str:
    return (
        f"{order.local_id} | plan={order.plan_id} strategy={order.strategy_id} "
        f"pair={order.pair} side={order.side} status={order.status} "
        f"kraken_id={order.kraken_order_id or '-'}"
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


def _filter_orders(service: ExecutionService, args: argparse.Namespace) -> List:
    orders = service.store.get_open_orders(plan_id=args.plan_id, strategy_id=args.strategy_id) if service.store else []  # type: ignore[call-arg]

    if args.kraken_id:
        orders = [o for o in orders if o.kraken_order_id == args.kraken_id]
    if args.local_id:
        orders = [o for o in orders if o.local_id == args.local_id]

    return orders


def cancel_orders(args: argparse.Namespace) -> int:
    if not args.all and not any([args.plan_id, args.strategy_id, args.kraken_id, args.local_id]):
        print("Refusing to cancel without a filter; pass --all to cancel every open order.")
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
    parser.add_argument("--db-path", default="portfolio.db", help="Path to the SQLite portfolio store")
    parser.add_argument(
        "--allow-interactive-setup",
        action="store_true",
        help="Allow credential prompts when bootstrapping REST clients",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    open_parser = sub.add_parser("list-open", help="List open/pending orders from SQLite and memory")
    open_parser.set_defaults(func=list_open_orders)

    executions_parser = sub.add_parser("recent-executions", help="Show recent execution results")
    executions_parser.add_argument("--limit", type=int, default=10, help="Number of execution results to display")
    executions_parser.set_defaults(func=show_recent_executions)

    cancel_parser = sub.add_parser("cancel", help="Cancel open orders for a plan or strategy")
    cancel_parser.add_argument("--plan-id", help="Restrict cancellations to a specific plan id")
    cancel_parser.add_argument("--strategy-id", help="Restrict cancellations to a specific strategy id")
    cancel_parser.add_argument("--kraken-id", help="Cancel a specific Kraken order id")
    cancel_parser.add_argument("--local-id", help="Cancel a specific local order id")
    cancel_parser.add_argument("--all", action="store_true", help="Cancel all open orders that match filters")
    cancel_parser.set_defaults(func=cancel_orders)

    panic_parser = sub.add_parser("panic", help="Cancel all open orders after refreshing state")
    panic_parser.set_defaults(func=panic)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(main())
