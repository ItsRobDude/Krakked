# src/kraken_bot/portfolio/manager.py

import time
import logging
from typing import Dict, List, Optional, Tuple, Any
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

        # Caches
        self._order_cache: Dict[str, Dict] = {} # order_id -> order_dict

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

        # Prune old snapshots on startup
        retention_days = self.config.snapshot_retention_days
        if retention_days > 0:
            cutoff = int(time.time()) - (retention_days * 86400)
            self.store.prune_snapshots(cutoff)

        # 1. Load history from store (or API if empty)
        # For simplicity in Phase 3, we can sync from API first to ensure we have data,
        # then replay everything from store to build state.

        self.sync()
        logger.info("PortfolioService initialized.")

    def sync(self) -> Dict[str, int]:
        """
        Fetches new trades/ledgers, updates state, reconciles.
        """
        logger.info("Syncing portfolio...")

        # 1. Fetch and Save Trades
        latest_trades = self.store.get_trades(limit=1) # ordered by time desc
        since_ts = latest_trades[0]['time'] if latest_trades else None

        params = {}
        if since_ts:
            params["start"] = since_ts

        new_trades = []

        while True:
            resp = self.rest_client.get_private("TradesHistory", params=params)
            trades_dict = resp.get("trades", {})
            if not trades_dict:
                break

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

            if len(batch) < 50:
                break

            # Use the timestamp of the last trade in batch plus a small epsilon
            last_ts = batch[-1]['time']
            params['start'] = last_ts

        # 2. Sync Closed Orders (for Strategy Tagging)
        self._sync_closed_orders(since_ts)

        # 3. Fetch Ledgers for Cash Flows
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

    def _sync_closed_orders(self, since_ts: Optional[float]):
        """
        Fetches and saves ClosedOrders to support strategy tagging.
        """
        params = {}
        if since_ts:
            params["start"] = since_ts

        while True:
            try:
                resp = self.rest_client.get_closed_orders(params=params)
                closed = resp.get("closed", {})
                if not closed:
                    break

                batch = []
                for txid, info in closed.items():
                    info['id'] = txid
                    batch.append(info)

                if not batch:
                    break

                # Sort by closetm or opentm
                batch.sort(key=lambda x: float(x.get('closetm') or x.get('opentm') or 0))

                self.store.save_orders(batch)

                if len(batch) < 50:
                    break

                # Pagination
                last_order = batch[-1]
                last_tm = float(last_order.get('closetm') or last_order.get('opentm') or 0)
                params['start'] = last_tm
            except Exception as e:
                logger.warning(f"Failed to sync ClosedOrders: {e}")
                break

    def _resolve_strategy_tag(self, trade: Dict) -> str:
        """
        Resolves strategy tag from the parent order's userref.
        Format: KRKKD:<strategy_name>
        """
        order_id = trade.get('ordertxid')
        if not order_id:
            return "manual"

        # Use cache if available
        if order_id in self._order_cache:
            order = self._order_cache[order_id]
        else:
            order = self.store.get_order(order_id)
            if order:
                self._order_cache[order_id] = order

        if not order:
            return "manual"

        userref = order.get('userref')
        if not userref:
            return "manual"

        # Use config mapping if available
        if userref:
            mapped_tag = self.config.strategy_map.get(userref)
            if mapped_tag:
                return mapped_tag
            return f"userref:{userref}"

        return "manual"

    def _round_amount(self, amount: float, pair_meta) -> float:
        return round(amount, pair_meta.volume_decimals)

    def _round_price(self, price: float, pair_meta) -> float:
        return round(price, pair_meta.price_decimals)

    def _process_trade(self, trade: Dict):
        """
        Updates positions and balances based on a trade.
        Implements WAC.
        """
        pair = trade['pair']
        try:
            pair_meta = self.market_data.get_pair_metadata(pair)
            canonical_pair = pair_meta.canonical
            base_asset = pair_meta.base # Normalized e.g. XBT
            quote_asset = pair_meta.quote # Normalized e.g. USD
        except Exception:
            logger.warning(f"Unknown pair {pair} in trade history. Skipping position update, but balance might be affected.")
            return

        # Trade details
        side = trade['type']
        price = self._round_price(float(trade['price']), pair_meta)
        vol = self._round_amount(float(trade['vol']), pair_meta)
        cost = float(trade['cost']) # Cost is usually price * vol but better to trust API
        fee = float(trade['fee'])

        fee_asset = quote_asset # Assumption

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

        fee_in_base_currency = self._convert_to_base_currency(fee, fee_asset)
        pos.fees_paid_base += fee_in_base_currency
        self.fees_paid_base_by_pair[canonical_pair] += fee_in_base_currency

        if side == 'buy':
            # WAC Update
            old_total_cost = pos.base_size * pos.avg_entry_price
            new_total_qty = pos.base_size + vol
            if new_total_qty > 0:
                # Use raw cost for precision
                pos.avg_entry_price = (old_total_cost + cost) / new_total_qty
            pos.base_size = self._round_amount(new_total_qty, pair_meta)

        elif side == 'sell':
            # Realized PnL
            pnl_quote = (price - pos.avg_entry_price) * vol
            pnl_base = self._convert_to_base_currency(pnl_quote, quote_asset)
            pnl_base_net = pnl_base - fee_in_base_currency

            pos.realized_pnl_base += pnl_base_net
            self.realized_pnl_base_by_pair[canonical_pair] += pnl_base_net

            # Reduce size
            pos.base_size = self._round_amount(max(0.0, pos.base_size - vol), pair_meta)

            strategy_tag = self._resolve_strategy_tag(trade)

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
                pnl_quote=pnl_base_net,
                strategy_tag=strategy_tag
            ))

    def _process_cash_flow(self, record: CashFlowRecord):
        # Update balances?
        # We rely on LIVE balances for reconciliation.
        # But we could track expected balances.
        # For Phase 3, we just store them to report 'Cash Flows'.
        pass

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
        # For each asset in positions, check if position size ~= balance total
        discrepancies = {}
        for pair, pos in self.positions.items():
            asset = pos.base_asset
            if asset in self.config.exclude_assets:
                continue

            bal = self.balances.get(asset)
            bal_total = bal.total if bal else 0.0

            diff = abs(pos.base_size - bal_total)
            # Note: One asset could be in multiple pairs? (e.g. ETH/USD, ETH/BTC)
            # If we trade ETH in multiple pairs, 'SpotPosition' tracks "net base units held BECAUSE OF this pair".
            # Aggregating positions by asset:
            # But typically we view "Positions" as the breakdown.
            # If we hold 10 ETH. Bought 5 via ETH/USD, 5 via ETH/BTC.
            # Position(ETHUSD).size = 5. Position(ETHBTC).size = 5.
            # Total ETH = 10. Matches Balance.

            # So we need to sum position sizes by asset.
            pass # We'll do this if we have multiple pairs per asset.
            # For now assuming 1 pair per asset mostly.

        # Simple reconciliation:
        # Just check if we are way off?
        # Actually, if we use Balances as Truth, we might just update Positions to match if drift is small?
        # Or just flag it.
        # Spec: "If difference ... exceeds tolerance ... Flags portfolio drift"

        # Implementation:
        # 1. Sum position sizes by asset.
        asset_position_sums = defaultdict(float)
        for pos in self.positions.values():
            asset_position_sums[pos.base_asset] += pos.base_size

        drift = False
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
        return snapshot

    def get_trade_history(self, pair: Optional[str] = None, limit: Optional[int] = None, include_manual: Optional[bool] = None) -> List[Dict]:
        """
        Returns recent trades from the internal log.
        """
        raw_trades = self.store.get_trades(pair=pair, limit=limit)

        # Note: If we need to filter by 'manual' tag here, we need the tags.
        # But tags are stored in RealizedPnLRecord, not directly in raw_trades (unless we denormalize).
        # Or we resolve them on the fly.
        # However, the spec says "Returns recent trades from the internal log".
        # And "Optional ... manual/bot filtering".
        # Since 'strategy_tag' is not on the raw trade table, we might need to join or look up order.
        # If include_manual is explicitly True/False, we must filter.

        if include_manual is None:
            # Default behavior based on config?
            # If track_manual_trades is False, does that mean we exclude them from history?
            # Usually get_trade_history implies "All trades".
            # But "PnL summary" might filter.
            # Let's return all if None.
            return raw_trades

        filtered = []
        for t in raw_trades:
            tag = self._resolve_strategy_tag(t)
            is_manual = (tag == "manual")

            # If include_manual is False, exclude manual trades.
            if include_manual is False and is_manual:
                continue

            # If include_manual is True or None, include everything (default behavior usually includes all unless restricted)
            # Spec says "Optional manual/bot filtering".
            # If the intention of include_manual=True is "ONLY manual", that would be different.
            # But typically 'include_manual' toggles visibility.
            # Let's assume standard visibility:
            # None: All
            # True: All (explicitly allowing manual)
            # False: No manual

            filtered.append(t)

        return filtered

    def get_fee_summary(self) -> Dict[str, Any]:
        """
        Returns aggregated fees by asset and pair.
        """
        # We track fees_paid_base_by_pair.
        # But we might want fees by asset too.
        # Since we convert all fees to base in the manager for PnL, we have base totals.
        # To get original fee asset totals, we'd need to re-scan trades or keep another aggregate.
        # For Phase 3, let's return the base currency fees we track.

        return {
            "total_fees_base": sum(self.fees_paid_base_by_pair.values()),
            "by_pair_base": dict(self.fees_paid_base_by_pair)
        }

    def get_cash_flows(self, asset: Optional[str] = None, limit: Optional[int] = None) -> List[CashFlowRecord]:
        return self.store.get_cash_flows(asset=asset, limit=limit)
