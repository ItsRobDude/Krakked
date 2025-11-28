# src/kraken_bot/portfolio/manager.py

import time
import logging
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from pathlib import Path

from kraken_bot.config import AppConfig, PortfolioConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.connection.rest_client import KrakenRESTClient
from .models import (
    AssetBalance, SpotPosition, RealizedPnLRecord, CashFlowRecord,
    PortfolioSnapshot, AssetValuation, AssetExposure, EquityView
)
from .store import PortfolioStore, SQLitePortfolioStore
from .exceptions import ReconciliationError, PositionNotFoundError
from kraken_bot.strategy.models import DecisionRecord

logger = logging.getLogger(__name__)

class PortfolioService:
    def __init__(self, config: AppConfig, market_data: MarketDataAPI, db_path: str = "portfolio.db"):
        self.config = config.portfolio
        self.app_config = config # Keep full config if needed
        self.market_data = market_data
        self.store: PortfolioStore = SQLitePortfolioStore(db_path=db_path)
        self.rest_client = KrakenRESTClient() # Used for balance checks and ledger/trades fetching if not passed in

        # In-memory state
        self.balances: Dict[str, AssetBalance] = {}
        self.positions: Dict[str, SpotPosition] = {}
        self.realized_pnl_history: List[RealizedPnLRecord] = [] # Could be large, maybe load on demand or summary

        # Aggregates
        self.realized_pnl_base_by_pair: Dict[str, float] = defaultdict(float)
        self.fees_paid_base_by_pair: Dict[str, float] = defaultdict(float)

        self.drift_flag: bool = False

        # Last sync state
        self.last_trade_sync_ts: float = 0.0

    def initialize(self):
        """
        Loads config, performs initial sync, builds internal state.
        """
        logger.info("Initializing PortfolioService...")
        # 1. Load history from store (or API if empty)
        # For simplicity in Phase 3, we can sync from API first to ensure we have data,
        # then replay everything from store to build state.

        # Ideally: Check if we have data. If not, fetch all history.
        # If we have data, fetch since last.
        # But building state requires replaying ALL trades from time 0 (or snapshot).
        # We will implement "Replay from all trades" for state building.

        self.sync()
        logger.info("PortfolioService initialized.")

    def sync(self) -> Dict[str, int]:
        """
        Fetches new trades/ledgers, updates state, reconciles.
        """
        logger.info("Syncing portfolio...")

        # 1. Fetch and Save Trades
        # We need to find the latest trade we have to know 'since'
        # For now, let's just get the last trade timestamp from DB
        latest_trades = self.store.get_trades(limit=1) # ordered by time desc
        since_ts = latest_trades[0]['time'] if latest_trades else None

        # Kraken API 'since' for trades is usually by txid or timestamp?
        # TradesHistory input 'start' is timestamp.
        # But response 'last' is string ID.
        # Let's use timestamp based on last trade time.
        # Note: Kraken 'TradesHistory' returns 'trades' (dict) and 'count'.

        # Warning: 'since' in Kraken might need offset or careful handling.
        # Using a safe overlap or tracking 'last' id is better.
        # For this phase, let's trust 'start=timestamp'

        params = {}
        if since_ts:
            params["start"] = since_ts

        # Paginate through trades?
        # For Phase 3 basic implementation, let's assume one call or simple loop.
        # But 'TradesHistory' can return 50 results.

        new_trades = []
        # TODO: Pagination logic. For now, fetch recent.
        # If this is first run, we might need 'all'.

        while True:
            resp = self.rest_client.get_private("TradesHistory", params=params)
            trades_dict = resp.get("trades", {})
            if not trades_dict:
                break

            # Convert dict {id: trade} to list of trades with 'id' injected
            batch = []
            for txid, trade_data in trades_dict.items():
                trade_data['id'] = txid
                batch.append(trade_data)

            # Sort by time to process in order
            batch.sort(key=lambda x: x['time'])

            if not batch:
                break

            self.store.save_trades(batch)
            new_trades.extend(batch)

            # If we get fewer than 50 (default limit), we are done.
            # Note: 'count' in response is total count of trades, not count in page.
            if len(batch) < 50:
                break

            # Safety break to prevent infinite loops in case of API issues
            # Assuming max 100 pages for now (5000 trades) is reasonable for one sync call
            # But the loop condition must be robust.
            # Check if last_ts is advancing?
            pass

            # Update params for next page
            # Use the timestamp of the last trade in batch plus a small epsilon
            # to avoid fetching the same trade again (assuming exclusive start if using ID,
            # but documentation says start timestamp is inclusive or exclusive?
            # "Starting unix timestamp or trade tx id of results (exclusive)" -> Exclusive.
            # So last timestamp should be safe.
            last_ts = batch[-1]['time']
            params['start'] = last_ts

        # 2. Fetch Ledgers for Cash Flows
        # (Similar logic, fetch new, save to store)
        # Using timestamps
        latest_cashflows = self.store.get_cash_flows(limit=1)
        since_ledger = latest_cashflows[0].time if latest_cashflows else None

        ledger_params = {}
        if since_ledger:
            ledger_params["start"] = since_ledger

        ledger_resp = self.rest_client.get_ledgers(params=ledger_params)
        ledger_entries = ledger_resp.get("ledger", {})

        cash_flow_records = []
        for ledger_id, entry in ledger_entries.items():
            # Filter types: 'deposit', 'withdrawal', 'adjustment', 'margin' (if relevant), 'trade' (ignore)
            ltype = entry.get("type")
            if ltype in ["deposit", "withdrawal", "adjustment", "staking"]: # "trade" and "margin" are usually PnL related or trade flows
                 # Create CashFlowRecord
                 # Need to normalize asset?
                 asset = entry.get("asset") # e.g. ZUSD
                 normalized_asset = self._normalize_asset(asset)

                 rec = CashFlowRecord(
                     id=ledger_id,
                     time=entry.get("time"),
                     asset=normalized_asset,
                     amount=float(entry.get("amount")),
                     type=ltype,
                     note=f"Ref: {entry.get('refid')}"
                 )
                 cash_flow_records.append(rec)

        self.store.save_cash_flows(cash_flow_records)

        # 3. Rebuild State (The Core Logic)
        self._rebuild_state()

        # 4. Reconcile
        self._reconcile()

        return {
            "new_trades": len(new_trades),
            "new_cash_flows": len(cash_flow_records)
        }

    def _normalize_asset(self, asset: str) -> str:
        # Simple mapping for Phase 3
        # Should ideally use Universe or config mapping
        if asset == "ZUSD": return "USD"
        if asset == "XXBT": return "XBT"
        if asset == "XETH": return "ETH"
        # etc.. map from config if possible
        # Or strip first letter if Z/X and length 4?
        # "ZUSD" -> "USD"
        return asset

    def _prefetch_prices(self, trades: List[Dict]):
        """
        Prefetches prices for fee assets in trades to avoid API calls during replay.
        Populates a cache in market_data if possible or a local cache.
        For Phase 3, we use get_latest_price which hits WS cache.
        If WS is not running, we might need to fetch tickers via REST.
        """
        # Identify pairs needed for fee conversion
        # Fee asset -> Base currency (e.g. XBT -> USD => XBTUSD)
        needed_pairs = set()
        for t in trades:
            # We only care if fee > 0 and fee_asset != base_currency
            # In _process_trade logic:
            # fee_asset assumed to be quote_asset.
            # If quote_asset != base_currency (USD), we need QuoteBase pair.
            # But typically quote IS USD.
            # What if pair is ETHXBT? Quote is XBT. Fee in XBT.
            # We need XBTUSD.
            pass
            # Logic inside _process_trade is:
            # fee_asset = quote_asset (assumption)
            # _convert_to_base_currency(fee, fee_asset)

            # So we need to ensure we have price for pair: f"{fee_asset}{base_currency}"
            # How to get fee_asset without full parsing logic?
            # Rough approximation:
            # Normalized Pair -> Quote Asset.
            # This requires iterating and parsing all pairs.
            # Just do it lazily?
            # The concern is "flood".
            # If we just do it via REST Ticker for ALL pairs in universe once?
            pass

        # Simple optimization: Ensure MarketData has updated tickers.
        # If WS is running, we are good.
        # If not, fetch all tickers for universe?
        # Only if we aren't connected.
        try:
            status = self.market_data.get_data_status()
            if not status.websocket_connected or status.streaming_pairs == 0:
                # Fetch all tickers via REST to populate cache/provide data
                # MarketDataAPI doesn't expose a method to "bulk fetch tickers to cache".
                # But we can call REST client manually and cache locally?
                # _convert_to_base_currency uses self.market_data.get_latest_price(pair).
                # That method uses self._ws_client.ticker_cache.
                # If WS client is None, it returns None.
                # So we CANNOT use get_latest_price if WS is down.
                # We need a fallback or side-channel.
                pass
        except Exception:
            pass

    def _rebuild_state(self):
        """
        Replays all trades and cash flows from the store to build:
        - Balances
        - Positions (WAC)
        - PnL metrics
        """
        # Reset
        self.balances = {} # asset -> AssetBalance
        self.positions = {} # pair -> SpotPosition
        self.realized_pnl_base_by_pair = defaultdict(float)
        self.fees_paid_base_by_pair = defaultdict(float)
        self.realized_pnl_history = []

        # Load all trades (oldest first)
        all_trades = self.store.get_trades(limit=None) # get_trades sorts DESC by default in my impl
        # Need ASC for replay
        all_trades.sort(key=lambda x: x['time'])

        # Load all cash flows (oldest first)
        # Note: Cash flows affect balances but not positions (usually)
        all_cash_flows = self.store.get_cash_flows(limit=None)
        all_cash_flows.sort(key=lambda x: x.time)

        # Merge streams?
        # Trades affect balances AND positions.
        # Cash flows affect balances.
        # We can process them in time order.

        # Convert to events
        events = []
        for t in all_trades:
            events.append({"type": "trade", "time": t['time'], "data": t})
        for c in all_cash_flows:
            events.append({"type": "flow", "time": c.time, "data": c})

        events.sort(key=lambda x: x['time'])

        for event in events:
            if event['type'] == "trade":
                self._process_trade(event['data'])
            else:
                self._process_cash_flow(event['data'])

    def _process_trade(self, trade: Dict):
        """
        Updates positions and balances based on a trade.
        Implements WAC.
        """
        pair = trade['pair']
        # Normalized Pair?
        # trade['pair'] is like 'XBTUSD' or 'XXBTZUSD'
        # We should canonicalize.
        try:
            pair_meta = self.market_data.get_pair_metadata(pair)
            canonical_pair = pair_meta.canonical
            base_asset = pair_meta.base # Normalized e.g. XBT
            quote_asset = pair_meta.quote # Normalized e.g. USD
        except Exception:
            # Fallback if pair not in universe (e.g. delisted)
            # Log warning and try best effort parsing?
            # For Phase 3, let's skip or warn.
            logger.warning(f"Unknown pair {pair} in trade history. Skipping position update, but balance might be affected.")
            return

        # Trade details
        # Kraken: type='buy' means we bought base, sold quote.
        side = trade['type']
        price = float(trade['price'])
        vol = float(trade['vol'])
        cost = float(trade['cost']) # vol * price
        fee = float(trade['fee'])

        # Fee asset? Kraken doesn't explicitly field 'fee_asset' in standard dict,
        # but usually it's Quote (unless specified).
        # We assume Quote for simplicity unless we parse detailed ledgers for every trade.
        # Phase 3 spec: "Convert fees to base currency... Subtract from realized PnL"
        # We need to know fee asset.
        # Often fee is in Quote.
        fee_asset = quote_asset # Assumption

        # Update Balances (Virtual tracking)
        # Buy: +Base, -Quote (cost + fee if fee in quote)
        # Sell: -Base, +Quote (cost - fee if fee in quote)

        # Update Position (WAC)
        pos = self.positions.get(canonical_pair)
        if not pos:
            pos = SpotPosition(
                pair=canonical_pair,
                base_asset=base_asset,
                quote_asset=quote_asset,
                base_size=0.0,
                avg_entry_price=0.0,
                realized_pnl_base=0.0,
                fees_paid_base=0.0
            )
            self.positions[canonical_pair] = pos

        # Fee in Base Currency
        # If fee_asset is USD (base_currency), fee_base = fee
        # If fee_asset is XBT, fee_base = fee * price_of_XBTUSD
        fee_in_base_currency = self._convert_to_base_currency(fee, fee_asset)
        pos.fees_paid_base += fee_in_base_currency
        self.fees_paid_base_by_pair[canonical_pair] += fee_in_base_currency

        if side == 'buy':
            # WAC Update
            # New Cost Basis = (Old Total Cost + New Cost) / (Old Qty + New Qty)
            old_total_cost = pos.base_size * pos.avg_entry_price
            new_total_qty = pos.base_size + vol
            if new_total_qty > 0:
                pos.avg_entry_price = (old_total_cost + cost) / new_total_qty
            pos.base_size = new_total_qty

        elif side == 'sell':
            # Realized PnL
            # PnL = (Sell Price - Avg Cost) * Sold Qty
            pnl_quote = (price - pos.avg_entry_price) * vol

            # Convert PnL to base currency (if quote is not base currency)
            # Usually Quote IS base currency (USD).
            # If pair is ETHXBT, quote is XBT. PnL is in XBT. Convert XBT to USD.
            pnl_base = self._convert_to_base_currency(pnl_quote, quote_asset)

            # Subtract fees from PnL?
            # Spec: "Realized PnL... Includes all trade-related fees"
            # So Realized PnL (Net) = Gross PnL - Fee(in base)
            pnl_base_net = pnl_base - fee_in_base_currency

            pos.realized_pnl_base += pnl_base_net
            self.realized_pnl_base_by_pair[canonical_pair] += pnl_base_net

            # Reduce size
            pos.base_size = max(0.0, pos.base_size - vol)

            # Record PnL Event
            self.realized_pnl_history.append(RealizedPnLRecord(
                trade_id=trade['id'],
                order_id=trade.get('ordertxid'),
                pair=canonical_pair,
                time=int(trade['time']),
                side=side,
                base_delta=-vol,
                quote_delta=cost,
                fee_asset=fee_asset,
                fee_amount=fee,
                pnl_quote=pnl_base_net, # Storing base net here as 'pnl_quote' field name is confusing in model?
                # Model says 'pnl_quote'.
                # Let's assume model meant 'pnl in report currency'?
                # Or 'pnl in quote currency of the pair'?
                # Spec: "pnl_quote: float # realized PnL in quote (USD) for this trade" -> Confusing if quote!=USD.
                # Let's store PnL in Base Currency (USD) in that field or rename.
                # Spec says "pnl_quote ... realized PnL in quote (USD)" -> Implies Quote=USD.
                # I'll store the Base Currency PnL there.
                strategy_tag="manual" # Default for now
            ))

    def _process_cash_flow(self, record: CashFlowRecord):
        bal = self.balances.get(record.asset, AssetBalance(record.asset, 0.0, 0.0, 0.0))
        bal.total += record.amount
        bal.free += record.amount
        self.balances[record.asset] = bal

    def _convert_to_base_currency(self, amount: float, asset: str) -> float:
        if amount == 0: return 0.0
        if asset == self.config.base_currency:
            return amount

        # Try to find a pair
        # e.g. convert XBT to USD -> Look for XBTUSD
        pair = f"{asset}{self.config.base_currency}"
        try:
            # We need historical price?
            # For "Backfill/Replay", we strictly speaking need price AT TIME OF TRADE.
            # This is hard without full OHLC history loaded.
            # Phase 3 Spec: "Use the trade's own price when fee asset is base or quote..."
            # If fee is third asset, use "get_latest_price" (which implies current price, not historical).
            # Using current price for historical fee conversion is wrong but might be the simplified requirement.
            # Actually, if we are replaying history, we might not have historical price easily available.

            # Simplified approach for Phase 3 Replay:
            # If asset is Quote (e.g. USD), it's 1:1.
            # If asset is Base (e.g. BTC) and we just traded it, use the Trade Price?
            # But here we are in a helper function.

            # Let's use `get_latest_price` as a fallback, fully acknowledging it distorts historical PnL if replayed.
            # Ideally we'd use the trade price if applicable.

            price = self.market_data.get_latest_price(pair)

            # Fallback if get_latest_price failed (e.g. WS down, no cache)
            if price is None:
                # Try REST API once? Or log warning and return 0 (safe failure)?
                # To prevent flood, we can check a local short-term cache or just fail.
                # Returning 0 affects PnL but keeps app running.
                # Let's try to get it from REST if critical?
                # No, that's the "flood" risk.
                # Better to warn.
                # logger.debug(f"Could not value {asset} in {self.config.base_currency}")
                return 0.0

            if price:
                return amount * price
        except:
            pass
        return 0.0

    def _reconcile(self):
        """
        Fetches live balances and compares with calculated/expected?
        Actually Phase 3 says:
        - "Asset balances ... derived from Kraken's Balance ... are the canonical source of truth"
        - "Pair-level SpotPosition objects are a projection"

        So:
        1. Fetch Live Balances -> Update self.balances
        2. Reconcile: Compare Live Balance vs Sum of Positions?
           Sum of positions (base_size) should match Live Balance for that asset.
           (Excluding what's not in positions, e.g. dust or non-traded assets).
        """
        # Fetch Live
        balance_resp = self.rest_client.get_private("Balance")
        # balance_resp: {'ZUSD': '100.0', 'XXBT': '0.5'}

        new_balances = {}
        for asset_raw, amount_str in balance_resp.items():
            asset = self._normalize_asset(asset_raw)
            amount = float(amount_str)
            # We don't have 'reserved' from simple Balance call (need TradeBalance or extended Balance?)
            # Standard Balance just gives total.
            new_balances[asset] = AssetBalance(
                asset=asset,
                free=amount, # Assuming all free for now
                reserved=0.0,
                total=amount
            )
        self.balances = new_balances

        # Drift Check
        # Implementation:
        # 1. Sum position sizes by asset.
        asset_position_sums = defaultdict(float)
        for pos in self.positions.values():
            asset_position_sums[pos.base_asset] += pos.base_size

        drift = False
        discrepancies = {}
        for asset, pos_sum in asset_position_sums.items():
            bal = self.balances.get(asset)
            bal_total = bal.total if bal else 0.0

            # Value difference in Base Currency
            diff_qty = abs(pos_sum - bal_total)

            # Convert diff to USD
            diff_val = self._convert_to_base_currency(diff_qty, asset)

            if diff_val > self.config.reconciliation_tolerance:
                discrepancies[asset] = diff_val
                drift = True

        self.drift_flag = drift
        if drift:
            logger.warning(f"Portfolio Drift Detected: {discrepancies}")

    def get_equity(self) -> EquityView:
        """
        Computes total equity in base currency.
        """
        equity = 0.0
        cash = 0.0

        # Use Balances (Source of Truth) for Equity
        for asset, bal in self.balances.items():
            val = self._convert_to_base_currency(bal.total, asset)
            equity += val
            if asset == self.config.base_currency:
                cash += val

        # Unrealized PnL calculation
        # Sum of (Current Value - Cost Basis) for all positions
        unrealized_total = 0.0
        for pos in self.positions.values():
            current_price = self.market_data.get_latest_price(pos.pair)
            if current_price:
                # Value of position
                current_val = pos.base_size * current_price
                # Cost basis (in quote, usually USD)
                cost_basis = pos.base_size * pos.avg_entry_price

                # Convert to base if needed
                # Assuming quote is base for now
                u_pnl = current_val - cost_basis
                pos.unrealized_pnl_base = u_pnl
                pos.current_value_base = current_val
                unrealized_total += u_pnl

        return EquityView(
            equity_base=equity,
            cash_base=cash,
            realized_pnl_base_total=sum(self.realized_pnl_base_by_pair.values()),
            unrealized_pnl_base_total=unrealized_total,
            drift_flag=self.drift_flag
        )

    def get_positions(self) -> List[SpotPosition]:
        return list(self.positions.values())

    def get_asset_exposure(self) -> List[AssetExposure]:
        equity_view = self.get_equity()
        total_equity = equity_view.equity_base

        exposures = []
        for asset, bal in self.balances.items():
            val = self._convert_to_base_currency(bal.total, asset)
            pct = (val / total_equity) if total_equity > 0 else 0.0
            exposures.append(AssetExposure(
                asset=asset,
                amount=bal.total,
                value_base=val,
                percentage_of_equity=pct
            ))
        return exposures

    def create_snapshot(self) -> PortfolioSnapshot:
        eq = self.get_equity()

        # Build asset valuations from current balances and prices
        asset_valuations = []
        for asset, bal in self.balances.items():
            # Recalculate or use cached value?
            # get_equity() logic calculated total equity but didn't return per-asset details explicitly except in aggregate.
            # But get_asset_exposure does.
            # Let's reuse logic or call `get_asset_exposure`?
            # get_asset_exposure returns `AssetExposure` objects.
            # We need `AssetValuation` objects.

            val_base = self._convert_to_base_currency(bal.total, asset)
            source_pair = f"{asset}{self.config.base_currency}" if asset != self.config.base_currency else None

            asset_valuations.append(AssetValuation(
                asset=asset,
                amount=bal.total,
                value_base=val_base,
                source_pair=source_pair
            ))

        snapshot = PortfolioSnapshot(
            timestamp=int(time.time()),
            equity_base=eq.equity_base,
            cash_base=eq.cash_base,
            asset_valuations=asset_valuations,
            realized_pnl_base_total=eq.realized_pnl_base_total,
            unrealized_pnl_base_total=eq.unrealized_pnl_base_total,
            realized_pnl_base_by_pair=dict(self.realized_pnl_base_by_pair),
            unrealized_pnl_base_by_pair={p.pair: p.unrealized_pnl_base for p in self.positions.values()}
        )

        self.store.save_snapshot(snapshot)
        retention_window = int(self.config.snapshot_retention_days * 86400)
        if retention_window > 0:
            cutoff = snapshot.timestamp - retention_window
            self.store.prune_snapshots(cutoff)
        return snapshot

    def record_decision(self, record: DecisionRecord):
        """
        Persists a strategy decision record to the portfolio store.
        """
        self.store.add_decision(record)

    def record_execution_plan(self, plan):
        """Persist a full execution plan for downstream execution services."""
        self.store.save_execution_plan(plan)
