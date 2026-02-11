"""
NeoStock2 內建策略 — 布林通道突破策略

邏輯：
- 價格觸及下軌 → 買入（超賣反彈）
- 價格觸及上軌 → 賣出（超買回落）
"""

import logging
import math
import pandas as pd
from collections import deque

from strategies.base_strategy import BaseStrategy, Signal, SignalAction

logger = logging.getLogger("neostock2.strategies.bollinger_band")


class BollingerBandStrategy(BaseStrategy):
    """布林通道突破策略"""

    name = "布林通道"
    description = "價格觸及布林通道下軌時買入，觸及上軌時賣出"
    version = "1.0"

    default_params = {
        "period": 20,       # 均線週期
        "num_std": 2,       # 標準差倍數
        "quantity": 1,
    }

    def __init__(self, symbols: list[str] = None, params: dict = None):
        super().__init__(symbols, params)
        self._price_history: dict[str, deque] = {}
        self._prev_position: dict[str, str] = {}  # 上次相對位置
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        max_len = self.params["period"] + 5
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=max_len)
        super().initialize()

    def _calc_bollinger(self, prices: list[float]) -> tuple[float, float, float] | None:
        """
        計算布林通道

        Returns:
            (middle, upper, lower) 或 None
        """
        period = self.params["period"]
        if len(prices) < period:
            return None

        recent = prices[-period:]
        middle = sum(recent) / period
        variance = sum((p - middle) ** 2 for p in recent) / period
        std = math.sqrt(variance)

        num_std = self.params["num_std"]
        upper = middle + num_std * std
        lower = middle - num_std * std

        return middle, upper, lower

    def on_tick(self, tick_data: dict) -> Signal | None:
        code = tick_data.get("code", "")
        if code not in self.symbols:
            return None

        price = tick_data.get("close", 0)
        if price <= 0:
            return None

        period = self.params["period"]
        self._price_history.setdefault(code, deque(maxlen=period + 5))
        self._price_history[code].append(price)

        prices = list(self._price_history[code])
        result = self._calc_bollinger(prices)

        if result is None:
            return None

        middle, upper, lower = result
        band_width = upper - lower

        self._indicators[code] = {
            "middle": round(middle, 2),
            "upper": round(upper, 2),
            "lower": round(lower, 2),
            "bandwidth": round(band_width, 2),
            "%b": round((price - lower) / band_width, 4) if band_width > 0 else 0,
        }

        prev_pos = self._prev_position.get(code, "middle")

        # 價格觸及下軌 → 買入
        if price <= lower and prev_pos != "below":
            self._prev_position[code] = "below"
            signal = Signal(
                action=SignalAction.BUY,
                symbol=code,
                price=price,
                quantity=self.params["quantity"],
                reason=f"布林通道下軌突破: 價格={price:.2f} <= 下軌={lower:.2f}",
                confidence=0.6,
            )
            self._record_signal(signal)
            return signal

        # 價格觸及上軌 → 賣出
        elif price >= upper and prev_pos != "above":
            self._prev_position[code] = "above"
            signal = Signal(
                action=SignalAction.SELL,
                symbol=code,
                price=price,
                quantity=self.params["quantity"],
                reason=f"布林通道上軌突破: 價格={price:.2f} >= 上軌={upper:.2f}",
                confidence=0.6,
            )
            self._record_signal(signal)
            return signal

        else:
            if lower < price < upper:
                self._prev_position[code] = "middle"

        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None

        period = self.params["period"]
        if len(bars) < period + 1:
            return None

        closes = list(bars["Close"].values)
        result = self._calc_bollinger(closes)
        prev_result = self._calc_bollinger(closes[:-1])

        if result is None or prev_result is None:
            return None

        price = closes[-1]
        prev_price = closes[-2]
        middle, upper, lower = result
        _, prev_upper, prev_lower = prev_result

        self._indicators[symbol] = {
            "middle": round(middle, 2),
            "upper": round(upper, 2),
            "lower": round(lower, 2),
        }

        if prev_price > prev_lower and price <= lower:
            signal = Signal(
                action=SignalAction.BUY, symbol=symbol, price=price,
                quantity=self.params["quantity"],
                reason=f"布林通道下軌突破 (K棒)", confidence=0.6,
            )
            self._record_signal(signal)
            return signal

        elif prev_price < prev_upper and price >= upper:
            signal = Signal(
                action=SignalAction.SELL, symbol=symbol, price=price,
                quantity=self.params["quantity"],
                reason=f"布林通道上軌突破 (K棒)", confidence=0.6,
            )
            self._record_signal(signal)
            return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators
