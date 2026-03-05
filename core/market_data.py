"""
NeoStock2 核心 — 即時行情模組

負責：
- 即時 Tick / BidAsk 訂閱
- K 棒歷史數據取得
- 行情快照管理
"""

import logging
import threading
from datetime import datetime, date
from collections import defaultdict, deque
from typing import Callable

import pandas as pd

logger = logging.getLogger("neostock2.core.market_data")


class MarketDataManager:
    """即時行情管理器"""

    def __init__(self, api_client):
        """
        Args:
            api_client: ShioajiClient 實例
        """
        self.client = api_client
        self._tick_callbacks: list[Callable] = []
        self._bidask_callbacks: list[Callable] = []
        self._subscribed_symbols: set[str] = set()
        self._latest_ticks: dict[str, dict] = {}
        self._latest_bidasks: dict[str, dict] = {}
        self._quotes: dict[str, dict] = {}  # 整合的即時報價快取
        self._tick_buffer: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))
        self._lock = threading.Lock()
        self._is_callback_set = False

    def _ensure_callbacks(self):
        """確保已設定行情回呼"""
        if self._is_callback_set:
            return

        api = self.client.api

        @api.on_tick_fop_v1()
        @api.on_tick_stk_v1()
        def on_tick(exchange, tick):
            self._handle_tick(tick)

        @api.on_bidask_fop_v1()
        @api.on_bidask_stk_v1()
        def on_bidask(exchange, bidask):
            self._handle_bidask(bidask)

        self._is_callback_set = True
        logger.debug("行情回呼已設定")

    def _update_quote(self, code: str, data: dict):
        """更新報價快取（Thread-safe）"""
        with self._lock:
            if code not in self._quotes:
                self._quotes[code] = {}
            self._quotes[code].update(data)

            # 計算漲跌（如果有參考價）
            q = self._quotes[code]
            if "close" in q and "reference_price" in q:
                ref = q["reference_price"]
                if ref > 0:
                    q["change_price"] = q["close"] - ref
                    q["change_rate"] = (q["change_price"] / ref) * 100

    def _to_float(self, value):
        """轉換 Decimal 為 float"""
        if value is None:
            return 0.0
        try:
            return float(value)
        except:
            return 0.0

    def _handle_tick(self, tick):
        """處理即時 Tick"""
        tick_data = {
            "code": tick.code,
            "datetime": tick.datetime.isoformat() if hasattr(tick.datetime, "isoformat") else str(tick.datetime),
            "close": self._to_float(tick.close),
            "volume": self._to_float(tick.volume),
            "total_volume": self._to_float(tick.total_volume),
            "total_amount": self._to_float(tick.total_amount),
            "high": self._to_float(tick.high),
            "low": self._to_float(tick.low),
            "open": self._to_float(tick.open),
            "avg_price": self._to_float(tick.avg_price),
            "tick_type": tick.tick_type,
        }

        # 更新快取
        self._update_quote(tick.code, tick_data)

        # 寫入 Buffer（策略用）
        with self._lock:
            self._latest_ticks[tick.code] = tick_data
            self._tick_buffer[tick.code].append(tick_data)

        # 通知訂閱者
        for callback in self._tick_callbacks:
            try:
                callback(tick_data)
            except Exception as e:
                logger.error(f"Tick 回呼錯誤: {e}")

    def _handle_bidask(self, bidask):
        """處理即時五檔"""
        bidask_data = {
            "code": bidask.code,
            "datetime": bidask.datetime.isoformat() if hasattr(bidask.datetime, "isoformat") else str(bidask.datetime),
            "bid_price": [self._to_float(p) for p in bidask.bid_price] if bidask.bid_price else [],
            "bid_volume": [self._to_float(v) for v in bidask.bid_volume] if bidask.bid_volume else [],
            "ask_price": [self._to_float(p) for p in bidask.ask_price] if bidask.ask_price else [],
            "ask_volume": [self._to_float(v) for v in bidask.ask_volume] if bidask.ask_volume else [],
        }

        # 更新快取（取第一檔作為買賣價）
        quote_update = {
            "buy_price": bidask_data["bid_price"][0] if bidask_data["bid_price"] else 0,
            "sell_price": bidask_data["ask_price"][0] if bidask_data["ask_price"] else 0,
        }
        self._update_quote(bidask.code, quote_update)

        with self._lock:
            self._latest_bidasks[bidask.code] = bidask_data

        for callback in self._bidask_callbacks:
            try:
                callback(bidask_data)
            except Exception as e:
                logger.error(f"BidAsk 回呼錯誤: {e}")

    def subscribe(self, symbol: str, quote_type: str = "tick") -> bool:
        """訂閱即時行情"""
        self._ensure_callbacks()
        contract = self.client.get_contract(symbol)
        if contract is None:
            return False

        try:
            self.client.api.quote.subscribe(contract, quote_type=quote_type)
            self._subscribed_symbols.add(symbol)
            logger.debug(f"已訂閱 {symbol} ({quote_type})")
            return True
        except Exception as e:
            logger.error(f"訂閱失敗 ({symbol}): {e}")
            return False

    def init_quote_cache(self, symbols: list[str]):
        """初始化報價快取（一次性快照 + 訂閱）"""
        if not symbols:
            return

        # 1. 取得快照以建立基準資料（參考價、名稱等）
        logger.debug(f"正在初始化行情快取: {symbols}")
        snapshots = self.get_snapshot(symbols)  # 這是舊的 API 呼叫，但只在初始化用一次
        
        with self._lock:
            for s in snapshots:
                # 計算參考價（如果是當日漲跌，推算昨收） -> 這裡 snapshot 已經給了 change_price
                # 參考價 = close - change_price
                # 但 Shioaji snapshot 沒給 reference_price，我們自己算
                ref_price = s["close"] - s["change_price"] if s["close"] else 0
                s["reference_price"] = ref_price
                self._quotes[s["code"]] = s

        # 2. 全部訂閱 Stream
        for sym in symbols:
            self.subscribe(sym, "tick")
            self.subscribe(sym, "bidask")

    def unsubscribe(self, symbol: str, quote_type: str = "tick") -> bool:
        """取消訂閱"""
        contract = self.client.get_contract(symbol)
        if contract is None:
            return False

        try:
            self.client.api.quote.unsubscribe(contract, quote_type=quote_type)
            # 只有當 tick 和 bidask 都取消時才從 set 移除？先簡化
            if quote_type == "tick": # 假設主要用 tick 判斷
                 self._subscribed_symbols.discard(symbol)
            logger.debug(f"已取消訂閱 {symbol} ({quote_type})")
            return True
        except Exception as e:
            logger.error(f"取消訂閱失敗 ({symbol}): {e}")
            return False

    def get_latest_quotes(self, symbols: list[str]) -> list[dict]:
        """從快取取得即時報價（高效能，不打 API）"""
        result = []
        with self._lock:
            for s in symbols:
                q = self._quotes.get(s, {}).copy()  # 淺拷貝，避免外部修改內部快取
                # 補個 code 避免前端壞掉
                if "code" not in q:
                    q["code"] = s
                result.append(q)
        return result

    def get_snapshot(self, symbols: list[str]) -> list[dict]:
        """
        取得行情快照（透過 API）- 供初始化或 fallback 用
        """
        contracts = []
        name_map = {}
        for sym in symbols:
            c = self.client.get_contract(sym)
            if c:
                contracts.append(c)
                name_map[sym] = getattr(c, "name", "")

        if not contracts:
            return []

        try:
            snapshots = self.client.api.snapshots(contracts)
            return [
                {
                    "code": s.code,
                    "name": name_map.get(s.code, "") or (s.name if hasattr(s, "name") else ""),
                    "close": s.close,
                    "open": s.open,
                    "high": s.high,
                    "low": s.low,
                    "volume": s.volume,
                    "total_volume": s.total_volume,
                    "amount": s.amount,
                    "total_amount": s.total_amount,
                    "change_price": s.change_price,
                    "change_rate": s.change_rate,
                    "buy_price": s.buy_price,
                    "sell_price": s.sell_price,
                    "ts": s.ts,
                }
                for s in snapshots
            ]
        except Exception as e:
            logger.error(f"取得快照失敗: {e}")
            return []

    def get_kbars(
        self,
        symbol: str,
        start: str = None,
        end: str = None,
    ) -> pd.DataFrame:
        """
        取得 K 棒歷史數據

        Args:
            symbol: 股票代碼
            start: 起始日期 'YYYY-MM-DD'（預設今天）
            end: 結束日期 'YYYY-MM-DD'（預設今天）

        Returns:
            包含 OHLCV 的 DataFrame
        """
        contract = self.client.get_contract(symbol)
        if contract is None:
            return pd.DataFrame()

        today = date.today().isoformat()
        start = start or today
        end = end or today

        try:
            kbars = self.client.api.kbars(contract, start=start, end=end)
            df = pd.DataFrame({**kbars})
            if not df.empty:
                df.ts = pd.to_datetime(df.ts)
                df.set_index("ts", inplace=True)
            return df
        except Exception as e:
            logger.error(f"取得 K 棒失敗 ({symbol}): {e}")
            return pd.DataFrame()

    def get_latest_tick(self, symbol: str) -> dict | None:
        """取得最新 Tick"""
        with self._lock:
            return self._latest_ticks.get(symbol)

    def get_latest_bidask(self, symbol: str) -> dict | None:
        """取得最新五檔"""
        with self._lock:
            return self._latest_bidasks.get(symbol)

    def get_tick_buffer(self, symbol: str, clear: bool = False) -> list[dict]:
        """取得 Tick 緩衝（供策略使用）"""
        with self._lock:
            buf = list(self._tick_buffer.get(symbol, []))
            if clear:
                self._tick_buffer[symbol].clear()
            return buf

    def on_tick(self, callback: Callable):
        """註冊 Tick 回呼"""
        self._tick_callbacks.append(callback)

    def on_bidask(self, callback: Callable):
        """註冊 BidAsk 回呼"""
        self._bidask_callbacks.append(callback)

    def get_subscribed_symbols(self) -> set[str]:
        """取得已訂閱的標的"""
        return self._subscribed_symbols.copy()
