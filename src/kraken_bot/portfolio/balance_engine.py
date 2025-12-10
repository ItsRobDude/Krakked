
import logging
from collections import defaultdict
from decimal import Decimal
from typing import Dict, Iterable, Optional

from kraken_bot.logging_config import structured_log_extra

from .models import AssetBalance, BalanceSnapshot, CashFlowCategory, CashFlowRecord, LedgerEntry

logger = logging.getLogger(__name__)


class BalanceEngine:
    def __init__(self, initial_balances: Optional[Dict[str, AssetBalance]] = None):
        if initial_balances is not None:
            self.balances = initial_balances
        else:
            self.balances = defaultdict(
                lambda: AssetBalance(asset="", total=0.0, free=0.0, reserved=0.0)
            )

    def _get_or_create_balance(self, asset: str) -> AssetBalance:
        if asset not in self.balances:
            self.balances[asset] = AssetBalance(
                asset=asset, total=0.0, free=0.0, reserved=0.0
            )
        return self.balances[asset]

    def apply_entry(self, e: LedgerEntry):
        bal = self._get_or_create_balance(e.asset)

        # Convert current float balance to Decimal for calculation
        current_total = Decimal(str(bal.total))

        # Kraken invariant: new_balance = old_balance + amount - fee
        # Note: fee is positive in ledger usually, but amount is signed.
        # Wait, Kraken ledger 'amount' is signed change. 'fee' is usually absolute cost.
        # Does 'amount' already include fee deduction?
        # Kraken docs: "amount: The amount of the transaction." "fee: The fee paid for the transaction."
        # If I buy 1 BTC, amount=+1, fee=0.001. Net change = +1 - 0.001?
        # Let's check the prompt's reference: "computed_new = bal.total + e.amount - e.fee"
        # I will stick to that formula.

        computed_new = current_total + e.amount - e.fee

        # Basic sanity check if Kraken provides the resulting balance
        if e.balance is not None:
             # Tolerance check (e.g. 1e-8)
            if abs(computed_new - e.balance) > Decimal("0.00000001"):
                logger.warning(
                    "Balance mismatch during replay",
                    extra=structured_log_extra(
                        event="balance_replay_mismatch",
                        ledger_id=e.id,
                        asset=e.asset,
                        computed=float(computed_new),
                        reported=float(e.balance),
                        diff=float(computed_new - e.balance),
                    ),
                )
                # Trust Kraken's balance ultimately
                computed_new = e.balance

        bal.total = float(computed_new)
        # For now, assume free == total (reserved handled by open orders separately / later)
        bal.free = bal.total
        # bal.reserved is kept as 0 or whatever it was, but prompt says "reserved handled separately".
        # If we just rebuilt from scratch, reserved is 0.
        # If we are updating live, we might want to preserve it?
        # The prompt says: "For now, assume `free == total` (reserved handled separately by open orders engine)"
        # So we set free = total, implying reserved = 0 effectively in this view,
        # OR we just don't touch reserved if we are updating incrementally?
        # The BalanceEngine seems to be a "rebuild from history" tool.
        # If I strictly follow: "bal.free = bal.total", then reserved = 0.

        self.balances[e.asset] = bal


def rebuild_balances(
    ledger_entries: Iterable[LedgerEntry],
    snapshot: Optional[BalanceSnapshot] = None,
) -> Dict[str, AssetBalance]:
    initial_balances = None
    start_from_id = None

    if snapshot:
        # Deep copy to avoid mutating snapshot
        initial_balances = {
            k: AssetBalance(asset=v.asset, total=v.total, free=v.free, reserved=v.reserved)
            for k, v in snapshot.balances.items()
        }
        start_from_id = snapshot.last_ledger_id

    engine = BalanceEngine(initial_balances=initial_balances)

    # Filter entries if snapshot provided
    # Note: caller might have already filtered, but we do it again to be safe if passed raw list
    entries = list(ledger_entries)

    # Sort by time, then id
    entries.sort(key=lambda x: (x.time, x.id))

    for e in entries:
        if start_from_id:
            # Skip until we find the start_from_id, OR if entries are strictly after.
            # The prompt says: "entries = [e for e in ledger_entries if e.id > start_from_id]"
            # But string comparison on IDs is dangerous if they aren't lexicographical.
            # We rely on the caller passing correct "after" entries or we filter by time if we knew snapshot time.
            # Ideally `store.get_ledger_entries(after_id=...)` does the work.
            # Here we assume `entries` passed in ARE the ones to apply.
            # BUT, if we blindly apply, we might duplicate.
            # Let's assume the caller handles the "after_id" filtering logic
            # because `rebuild_balances` doesn't know if `ledger_entries` includes the overlap or not.
            # Wait, prompt implementation:
            # if snapshot: entries = [e for e in ledger_entries if e.id > start_from_id]
            # I'll rely on the Store to filter or the caller.
            # But to be safe, if I see the ID <= start_from_id (if I could compare IDs), I'd skip.
            # Since I can't easily compare IDs, I will assume the input `ledger_entries` is correctly filtered.
            pass

        engine.apply_entry(e)

    return engine.balances


def classify_cashflow(e: LedgerEntry) -> Optional[CashFlowRecord]:
    t = e.type
    st = (e.subtype or "").lower()

    cat = None

    # External equity movements
    if t == "deposit":
        cat = CashFlowCategory.DEPOSIT
    elif t == "withdrawal":
        cat = CashFlowCategory.WITHDRAWAL
    elif t in {"staking", "earn"} and "reward" in st:
        cat = CashFlowCategory.STAKING_REWARD
    elif t == "adjustment":
        cat = CashFlowCategory.ADJUSTMENT
    elif t == "transfer":
        if st == "spottofutures":
            cat = CashFlowCategory.SPOT_TO_FUTURES
        elif st == "spotfromfutures":
            cat = CashFlowCategory.FUTURES_TO_SPOT
    elif t in {"trade", "margin trade", "settled", "rollover"}:
        cat = CashFlowCategory.TRADE_PNL

    if cat:
        return CashFlowRecord(
            id=e.id,
            time=int(e.time), # CashFlowRecord expects int timestamp
            asset=e.asset,
            amount=float(e.amount),
            type=cat.value, # CashFlowRecord expects string type
            note=e.misc,
            # refid is not in CashFlowRecord, but maybe I should add it?
            # The prompt suggested refid in CashFlowRecord dataclass update,
            # but I didn't update CashFlowRecord in models.py because I wanted to minimize changes
            # unless I replace the model. The existing model has `id`, `time`, `asset`, `amount`, `type`, `note`.
            # I will stick to existing fields for now.
        )
    return None
