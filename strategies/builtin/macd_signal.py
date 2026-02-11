"""
NeoStock2 內建策略 — MACD 訊號策略

邏輯：
- MACD 線上穿信號線 → 買入（金叉）
- MACD 線下穿信號線 → 賣出（死叉）
"""

import logging
import pandas as pd
from collections import deque

from strategies.base_strategy import BaseStrategy, Signal, SignalAction

logger = logging.getLogger("neostock2.strategies.macd_signal")


class MACDSignalStrategy(BaseStrategy):
    """MACD 金叉死叉策略"""

    name = "MACD 訊號"
    description = "MACD 線上穿信號線時買入（金叉），下穿時賣出（死叉）"
    version = "1.0"

    default_params = {
        "fast_period": 12,     # 快線 EMA 週期
        "slow_period": 26,     # 慢線 EMA 週期
        "signal_period": 9,    # 信號線 EMA 週期
        "quantity": 1,
    }

    def __init__(self, symbols: list[str] = None, params: dict = None):
        super().__init__(symbols, params)
        self._price_history: dict[str, deque] = {}
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        max_len = self.params["slow_period"] + self.params["signal_period"] + 10
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=max_len)
        super().initialize()

    def _calc_ema(self, prices: list[float], period: int) -> list[float]:
        """計算 EMA"""
        if len(prices) < period:
            return []

        multiplier = 2 / (period + 1)
        ema = [sum(prices[:period]) / period]

        for price in prices[period:]:
            ema.append((price - ema[-1]) * multiplier + ema[-1])
        return ema

    def _calc_macd(self, prices: list[float]) -> tuple[float, float, float] | None:
        """
        計算 MACD

        Returns:
            (macd_line, signal_line, histogram) 或 None
        """
        fast_p = self.params["fast_period"]
        slow_p = self.params["slow_period"]
        signal_p = self.params["signal_period"]

        if len(prices) < slow_p + signal_p:
            return None

        fast_ema = self._calc_ema(prices, fast_p)
        slow_ema = self._calc_ema(prices, slow_p)

        # 對齊：fast_ema 比 slow_ema 長
        offset = slow_p - fast_p
        macd_line_list = [
            fast_ema[i + offset] - slow_ema[i]
            for i in range(len(slow_ema))
        ]

        if len(macd_line_list) < signal_p:
            return None

        signal_ema = self._calc_ema(macd_line_list, signal_p)
        if not signal_ema:
            return None

        macd_val = macd_line_list[-1]
        signal_val = signal_ema[-1]
        histogram = macd_val - signal_val

        return macd_val, signal_val, histogram

    def on_tick(self, tick_data: dict) -> Signal | None:
        code = tick_data.get("code", "")
        if code not in self.symbols:
            return None

        price = tick_data.get("close", 0)
        if price <= 0:
            return None

        slow_p = self.params["slow_period"]
        signal_p = self.params["signal_period"]
        max_len = slow_p + signal_p + 10
        self._price_history.setdefault(code, deque(maxlen=max_len))
        self._price_history[code].append(price)

        prices = list(self._price_history[code])
        current = self._calc_macd(prices)
        prev = self._calc_macd(prices[:-1]) if len(prices) > 1 else None

        if current is None or prev is None:
            return None

        macd, signal_line, histogram = current
        prev_macd, prev_signal, prev_hist = prev

        self._indicators[code] = {
            "macd": round(macd, 4),
            "signal": round(signal_line, 4),
            "histogram": round(histogram, 4),
        }

        # MACD 上穿信號線（金叉）
        if prev_macd <= prev_signal and macd > signal_line:
            sig = Signal(
                action=SignalAction.BUY,
                symbol=code,
                price=price,
                quantity=self.params["quantity"],
                reason=f"MACD 金叉: MACD={macd:.4f} > Signal={signal_line:.4f}",
                confidence=0.7,
            )
            self._record_signal(sig)
            return sig

        # MACD 下穿信號線（死叉）
        elif prev_macd >= prev_signal and macd < signal_line:
            sig = Signal(
                action=SignalAction.SELL,
                symbol=code,
                price=price,
                quantity=self.params["quantity"],
                reason=f"MACD 死叉: MACD={macd:.4f} < Signal={signal_line:.4f}",
                confidence=0.7,
            )
            self._record_signal(sig)
            return sig

        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None

        slow_p = self.params["slow_period"]
        signal_p = self.params["signal_period"]

        if len(bars) < slow_p + signal_p + 1:
            return None

        closes = list(bars["Close"].values)
        current = self._calc_macd(closes)
        prev = self._calc_macd(closes[:-1])

        if current is None or prev is None:
            return None

        macd, signal_line, histogram = current
        prev_macd, prev_signal, _ = prev
        price = closes[-1]

        self._indicators[symbol] = {
            "macd": round(macd, 4),
            "signal": round(signal_line, 4),
            "histogram": round(histogram, 4),
        }

        if prev_macd <= prev_signal and macd > signal_line:
            sig = Signal(
                action=SignalAction.BUY, symbol=symbol, price=price,
                quantity=self.params["quantity"],
                reason=f"MACD 金叉 (K棒)", confidence=0.7,
            )
            self._record_signal(sig)
            return sig

        elif prev_macd >= prev_signal and macd < signal_line:
            sig = Signal(
                action=SignalAction.SELL, symbol=symbol, price=price,
                quantity=self.params["quantity"],
                reason=f"MACD 死叉 (K棒)", confidence=0.7,
            )
            self._record_signal(sig)
            return sig

        return None

    def get_indicators(self) -> dict:
        return self._indicators
