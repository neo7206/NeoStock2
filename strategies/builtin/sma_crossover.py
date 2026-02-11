"""
NeoStock2 內建策略 — 雙均線交叉 (SMA Crossover)

邏輯：
- 短期均線上穿長期均線 → 買入訊號
- 短期均線下穿長期均線 → 賣出訊號
"""

import logging
import pandas as pd
from collections import deque

from strategies.base_strategy import BaseStrategy, Signal, SignalAction

logger = logging.getLogger("neostock2.strategies.sma_crossover")


class SMACrossoverStrategy(BaseStrategy):
    """雙均線交叉策略"""

    name = "SMA 交叉"
    description = "短期均線上穿長期均線時買入，下穿時賣出"
    version = "1.0"

    default_params = {
        "short_period": 5,   # 短期均線週期
        "long_period": 20,   # 長期均線週期
        "quantity": 1,       # 每次下單張數
    }

    def __init__(self, symbols: list[str] = None, params: dict = None):
        super().__init__(symbols, params)
        # 每檔標的存一段價格歷史
        self._price_history: dict[str, deque] = {}
        self._prev_signal: dict[str, str] = {}  # 上一次訊號方向
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        max_len = self.params["long_period"] + 5
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=max_len)
            self._prev_signal[sym] = "Hold"
        super().initialize()

    def on_tick(self, tick_data: dict) -> Signal | None:
        code = tick_data.get("code", "")
        if code not in self.symbols:
            return None

        price = tick_data.get("close", 0)
        if price <= 0:
            return None

        self._price_history.setdefault(
            code, deque(maxlen=self.params["long_period"] + 5)
        )
        self._price_history[code].append(price)

        prices = list(self._price_history[code])
        long_p = self.params["long_period"]

        if len(prices) < long_p:
            return None

        short_p = self.params["short_period"]
        short_ma = sum(prices[-short_p:]) / short_p
        long_ma = sum(prices[-long_p:]) / long_p

        # 前一根的均線
        if len(prices) >= long_p + 1:
            prev_short = sum(prices[-(short_p + 1):-1]) / short_p
            prev_long = sum(prices[-(long_p + 1):-1]) / long_p
        else:
            self._indicators[code] = {"short_ma": short_ma, "long_ma": long_ma}
            return None

        self._indicators[code] = {
            "short_ma": round(short_ma, 2),
            "long_ma": round(long_ma, 2),
            "diff": round(short_ma - long_ma, 2),
        }

        # === 黃金交叉（買入）===
        if prev_short <= prev_long and short_ma > long_ma:
            if self._prev_signal.get(code) != "Buy":
                self._prev_signal[code] = "Buy"
                signal = Signal(
                    action=SignalAction.BUY,
                    symbol=code,
                    price=price,
                    quantity=self.params["quantity"],
                    reason=f"SMA 黃金交叉: MA{short_p}={short_ma:.2f} > MA{long_p}={long_ma:.2f}",
                    confidence=0.7,
                )
                self._record_signal(signal)
                return signal

        # === 死亡交叉（賣出）===
        elif prev_short >= prev_long and short_ma < long_ma:
            if self._prev_signal.get(code) != "Sell":
                self._prev_signal[code] = "Sell"
                signal = Signal(
                    action=SignalAction.SELL,
                    symbol=code,
                    price=price,
                    quantity=self.params["quantity"],
                    reason=f"SMA 死亡交叉: MA{short_p}={short_ma:.2f} < MA{long_p}={long_ma:.2f}",
                    confidence=0.7,
                )
                self._record_signal(signal)
                return signal

        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None

        short_p = self.params["short_period"]
        long_p = self.params["long_period"]

        if len(bars) < long_p + 1:
            return None

        bars = bars.copy()
        bars["sma_short"] = bars["Close"].rolling(short_p).mean()
        bars["sma_long"] = bars["Close"].rolling(long_p).mean()

        curr_short = bars["sma_short"].iloc[-1]
        curr_long = bars["sma_long"].iloc[-1]
        prev_short = bars["sma_short"].iloc[-2]
        prev_long = bars["sma_long"].iloc[-2]
        price = bars["Close"].iloc[-1]

        self._indicators[symbol] = {
            "short_ma": round(curr_short, 2),
            "long_ma": round(curr_long, 2),
        }

        if prev_short <= prev_long and curr_short > curr_long:
            signal = Signal(
                action=SignalAction.BUY,
                symbol=symbol,
                price=price,
                quantity=self.params["quantity"],
                reason=f"SMA 黃金交叉 (K棒)",
                confidence=0.7,
            )
            self._record_signal(signal)
            return signal

        elif prev_short >= prev_long and curr_short < curr_long:
            signal = Signal(
                action=SignalAction.SELL,
                symbol=symbol,
                price=price,
                quantity=self.params["quantity"],
                reason=f"SMA 死亡交叉 (K棒)",
                confidence=0.7,
            )
            self._record_signal(signal)
            return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators
