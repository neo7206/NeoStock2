"""
NeoStock2 內建策略 — RSI 反轉策略

邏輯：
- RSI < 超賣線 (30) → 買入訊號
- RSI > 超買線 (70) → 賣出訊號
"""

import logging
import pandas as pd
from collections import deque

from strategies.base_strategy import BaseStrategy, Signal, SignalAction

logger = logging.getLogger("neostock2.strategies.rsi_reversal")


class RSIReversalStrategy(BaseStrategy):
    """RSI 超買超賣反轉策略"""

    name = "RSI 反轉"
    description = "RSI 低於超賣線時買入，高於超買線時賣出"
    version = "1.0"

    default_params = {
        "period": 14,          # RSI 計算週期
        "overbought": 70,      # 超買線
        "oversold": 30,        # 超賣線
        "quantity": 1,
    }

    def __init__(self, symbols: list[str] = None, params: dict = None):
        super().__init__(symbols, params)
        self._price_history: dict[str, deque] = {}
        self._prev_rsi: dict[str, float] = {}
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        max_len = self.params["period"] + 10
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=max_len)
        super().initialize()

    def _calc_rsi(self, prices: list[float], period: int) -> float | None:
        """計算 RSI"""
        if len(prices) < period + 1:
            return None

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        recent = deltas[-period:]

        gains = [d for d in recent if d > 0]
        losses = [-d for d in recent if d < 0]

        avg_gain = sum(gains) / period if gains else 0
        avg_loss = sum(losses) / period if losses else 0

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def on_tick(self, tick_data: dict) -> Signal | None:
        code = tick_data.get("code", "")
        if code not in self.symbols:
            return None

        price = tick_data.get("close", 0)
        if price <= 0:
            return None

        period = self.params["period"]
        self._price_history.setdefault(code, deque(maxlen=period + 10))
        self._price_history[code].append(price)

        prices = list(self._price_history[code])
        rsi = self._calc_rsi(prices, period)

        if rsi is None:
            return None

        prev_rsi = self._prev_rsi.get(code, 50)
        self._prev_rsi[code] = rsi

        self._indicators[code] = {
            "rsi": round(rsi, 2),
            "overbought": self.params["overbought"],
            "oversold": self.params["oversold"],
        }

        oversold = self.params["oversold"]
        overbought = self.params["overbought"]

        # RSI 從下方穿越超賣線 → 買入
        if prev_rsi < oversold and rsi >= oversold:
            signal = Signal(
                action=SignalAction.BUY,
                symbol=code,
                price=price,
                quantity=self.params["quantity"],
                reason=f"RSI 超賣反轉: RSI={rsi:.1f} 上穿 {oversold}",
                confidence=0.65,
            )
            self._record_signal(signal)
            return signal

        # RSI 從上方穿越超買線 → 賣出
        elif prev_rsi > overbought and rsi <= overbought:
            signal = Signal(
                action=SignalAction.SELL,
                symbol=code,
                price=price,
                quantity=self.params["quantity"],
                reason=f"RSI 超買反轉: RSI={rsi:.1f} 下穿 {overbought}",
                confidence=0.65,
            )
            self._record_signal(signal)
            return signal

        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None

        period = self.params["period"]
        if len(bars) < period + 2:
            return None

        closes = bars["Close"].values
        rsi = self._calc_rsi(list(closes), period)
        prev_rsi_val = self._calc_rsi(list(closes[:-1]), period)

        if rsi is None or prev_rsi_val is None:
            return None

        price = closes[-1]
        self._indicators[symbol] = {"rsi": round(rsi, 2)}

        oversold = self.params["oversold"]
        overbought = self.params["overbought"]

        if prev_rsi_val < oversold and rsi >= oversold:
            signal = Signal(
                action=SignalAction.BUY, symbol=symbol, price=price,
                quantity=self.params["quantity"],
                reason=f"RSI 超賣反轉 (K棒): {rsi:.1f}", confidence=0.65,
            )
            self._record_signal(signal)
            return signal

        elif prev_rsi_val > overbought and rsi <= overbought:
            signal = Signal(
                action=SignalAction.SELL, symbol=symbol, price=price,
                quantity=self.params["quantity"],
                reason=f"RSI 超買反轉 (K棒): {rsi:.1f}", confidence=0.65,
            )
            self._record_signal(signal)
            return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators
