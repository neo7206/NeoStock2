"""
NeoStock2 內建策略 — 跨日波段策略適配器 (Swing Strategy Adapter)

將 research/strategies.py 中的 5 個跨日波段研究策略
包裝為可在策略引擎中即時執行的 BaseStrategy 子類別。

支援的研究策略：
- trend_ma: 均線趨勢交叉
- breakout: 通道突破
- pullback: 多頭回檔買進
- bollinger: 布林通道回歸
- macd: MACD 趨勢跟隨
"""

import logging
import numpy as np
import pandas as pd
from collections import deque

from strategies.base_strategy import BaseStrategy, Signal, SignalAction

logger = logging.getLogger("neostock2.strategies.swing_adapter")


def _calc_rsi(prices: list, window: int) -> float | None:
    """計算 RSI"""
    if len(prices) < window + 1:
        return None
    close = pd.Series(prices)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not pd.isna(val) else None


class SwingTrendMAStrategy(BaseStrategy):
    """跨日均線趨勢交叉策略"""

    name = "均線趨勢(跨日)"
    description = "快線突破慢線+股價站穩慢線時進場，反轉時出場。適合趨勢明確的標的。"
    version = "1.0"
    default_params = {
        "fast_ma": 20,
        "slow_ma": 60,
        "quantity": 1,
    }

    def __init__(self, symbols=None, params=None):
        super().__init__(symbols, params)
        self._price_history: dict[str, deque] = {}
        self._in_position: dict[str, bool] = {}
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        max_len = self.params["slow_ma"] + 5
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=max_len)
            self._in_position[sym] = False
        super().initialize()

    def on_tick(self, tick_data: dict) -> Signal | None:
        """跨日策略不在 tick 觸發，僅由 on_bar (盤前掃描) 觸發"""
        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None
        for _, row in bars.iterrows():
            self._price_history.setdefault(
                symbol, deque(maxlen=self.params["slow_ma"] + 5)
            )
            self._price_history[symbol].append(float(row["Close"]))
        price = float(bars["Close"].iloc[-1])
        return self._evaluate(symbol, price)

    def _evaluate(self, code: str, price: float) -> Signal | None:
        prices = list(self._price_history.get(code, []))
        fast_w = self.params["fast_ma"]
        slow_w = self.params["slow_ma"]

        if len(prices) < slow_w:
            return None

        fast_ma = sum(prices[-fast_w:]) / fast_w
        slow_ma = sum(prices[-slow_w:]) / slow_w

        self._indicators[code] = {
            "fast_ma": round(fast_ma, 2),
            "slow_ma": round(slow_ma, 2),
        }

        # 入場：快線 > 慢線 且 股價 > 慢線
        if fast_ma > slow_ma and price > slow_ma and not self._in_position.get(code):
            self._in_position[code] = True
            signal = Signal(
                action=SignalAction.BUY, symbol=code, price=price,
                quantity=self.params["quantity"],
                reason=f"均線趨勢入場: MA{fast_w}={fast_ma:.2f} > MA{slow_w}={slow_ma:.2f}",
                confidence=0.7,
            )
            self._record_signal(signal)
            return signal

        # 出場：快線 < 慢線
        if fast_ma < slow_ma and self._in_position.get(code):
            self._in_position[code] = False
            signal = Signal(
                action=SignalAction.SELL, symbol=code, price=price,
                quantity=self.params["quantity"],
                reason=f"均線趨勢出場: MA{fast_w}={fast_ma:.2f} < MA{slow_w}={slow_ma:.2f}",
                confidence=0.7,
            )
            self._record_signal(signal)
            return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators


class SwingBreakoutStrategy(BaseStrategy):
    """跨日通道突破策略"""

    name = "通道突破(跨日)"
    description = "股價突破 N 根 K 棒高點進場，跌破 M 根 K 棒低點出場。"
    version = "1.0"
    default_params = {
        "entry_window": 20,
        "exit_window": 10,
        "quantity": 1,
    }

    def __init__(self, symbols=None, params=None):
        super().__init__(symbols, params)
        max_len = max(
            self.params.get("entry_window", 20),
            self.params.get("exit_window", 10)
        ) + 5
        self._price_history: dict[str, deque] = {}
        self._in_position: dict[str, bool] = {}
        self._max_len = max_len
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=self._max_len)
            self._in_position[sym] = False
        super().initialize()

    def on_tick(self, tick_data: dict) -> Signal | None:
        """跨日策略不在 tick 觸發，僅由 on_bar (盤前掃描) 觸發"""
        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None
        for _, row in bars.iterrows():
            self._price_history.setdefault(symbol, deque(maxlen=self._max_len))
            self._price_history[symbol].append(float(row["Close"]))
        price = float(bars["Close"].iloc[-1])
        return self._evaluate(symbol, price)

    def _evaluate(self, code: str, price: float) -> Signal | None:
        prices = list(self._price_history.get(code, []))
        ew = self.params["entry_window"]
        xw = self.params["exit_window"]

        if len(prices) < ew + 1:
            return None

        # 不含當根
        rolling_high = max(prices[-(ew + 1):-1])
        rolling_low = min(prices[-(xw + 1):-1]) if len(prices) >= xw + 1 else None

        self._indicators[code] = {
            "rolling_high": round(rolling_high, 2),
            "rolling_low": round(rolling_low, 2) if rolling_low else None,
        }

        if price > rolling_high and not self._in_position.get(code):
            self._in_position[code] = True
            signal = Signal(
                action=SignalAction.BUY, symbol=code, price=price,
                quantity=self.params["quantity"],
                reason=f"通道突破: {price:.2f} > {rolling_high:.2f}(前{ew}根高點)",
                confidence=0.7,
            )
            self._record_signal(signal)
            return signal

        if rolling_low and price < rolling_low and self._in_position.get(code):
            self._in_position[code] = False
            signal = Signal(
                action=SignalAction.SELL, symbol=code, price=price,
                quantity=self.params["quantity"],
                reason=f"通道跌破: {price:.2f} < {rolling_low:.2f}(前{xw}根低點)",
                confidence=0.7,
            )
            self._record_signal(signal)
            return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators


class SwingPullbackStrategy(BaseStrategy):
    """跨日多頭回檔買進策略"""

    name = "回檔買進(跨日)"
    description = "在上升趨勢中（股價站穩長均線），等 RSI 過低時買進，RSI 反彈時出場。"
    version = "1.0"
    default_params = {
        "long_ma": 60,
        "rsi_window": 14,
        "rsi_entry": 30,
        "rsi_exit": 60,
        "quantity": 1,
    }

    def __init__(self, symbols=None, params=None):
        super().__init__(symbols, params)
        max_len = max(self.params.get("long_ma", 60), 60) + 20
        self._price_history: dict[str, deque] = {}
        self._in_position: dict[str, bool] = {}
        self._max_len = max_len
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=self._max_len)
            self._in_position[sym] = False
        super().initialize()

    def on_tick(self, tick_data: dict) -> Signal | None:
        """跨日策略不在 tick 觸發，僅由 on_bar (盤前掃描) 觸發"""
        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None
        for _, row in bars.iterrows():
            self._price_history.setdefault(symbol, deque(maxlen=self._max_len))
            self._price_history[symbol].append(float(row["Close"]))
        price = float(bars["Close"].iloc[-1])
        return self._evaluate(symbol, price)

    def _evaluate(self, code: str, price: float) -> Signal | None:
        prices = list(self._price_history.get(code, []))
        lma_w = self.params["long_ma"]
        rsi_w = self.params["rsi_window"]
        rsi_entry = self.params["rsi_entry"]
        rsi_exit = self.params["rsi_exit"]

        if len(prices) < lma_w:
            return None

        long_ma = sum(prices[-lma_w:]) / lma_w
        rsi = _calc_rsi(prices, rsi_w)

        self._indicators[code] = {
            "long_ma": round(long_ma, 2),
            "rsi": round(rsi, 2) if rsi else None,
        }

        if rsi is None:
            return None

        uptrend = price > long_ma

        if uptrend and rsi < rsi_entry and not self._in_position.get(code):
            self._in_position[code] = True
            signal = Signal(
                action=SignalAction.BUY, symbol=code, price=price,
                quantity=self.params["quantity"],
                reason=f"回檔買進: RSI={rsi:.1f}<{rsi_entry}, 趨勢多頭",
                confidence=0.65,
            )
            self._record_signal(signal)
            return signal

        if rsi > rsi_exit and self._in_position.get(code):
            self._in_position[code] = False
            signal = Signal(
                action=SignalAction.SELL, symbol=code, price=price,
                quantity=self.params["quantity"],
                reason=f"RSI反彈出場: RSI={rsi:.1f}>{rsi_exit}",
                confidence=0.65,
            )
            self._record_signal(signal)
            return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators


class SwingMACDStrategy(BaseStrategy):
    """跨日 MACD 趨勢跟隨策略"""

    name = "MACD趨勢(跨日)"
    description = "MACD 金叉且位於零軸之上時進場，死叉時出場。中長期趨勢追蹤。"
    version = "1.0"
    default_params = {
        "fast_period": 12,
        "slow_period": 26,
        "signal_period": 9,
        "quantity": 1,
    }

    def __init__(self, symbols=None, params=None):
        super().__init__(symbols, params)
        max_len = self.params.get("slow_period", 26) * 3
        self._price_history: dict[str, deque] = {}
        self._in_position: dict[str, bool] = {}
        self._max_len = max_len
        self._indicators: dict[str, dict] = {}

    def initialize(self):
        for sym in self.symbols:
            self._price_history[sym] = deque(maxlen=self._max_len)
            self._in_position[sym] = False
        super().initialize()

    def on_tick(self, tick_data: dict) -> Signal | None:
        """跨日策略不在 tick 觸發，僅由 on_bar (盤前掃描) 觸發"""
        return None

    def on_bar(self, symbol: str, bars: pd.DataFrame) -> Signal | None:
        if bars.empty or symbol not in self.symbols:
            return None
        for _, row in bars.iterrows():
            self._price_history.setdefault(symbol, deque(maxlen=self._max_len))
            self._price_history[symbol].append(float(row["Close"]))
        return self._evaluate(symbol)

    def _evaluate(self, code: str) -> Signal | None:
        prices = list(self._price_history.get(code, []))
        fast_p = self.params["fast_period"]
        slow_p = self.params["slow_period"]
        sig_p = self.params["signal_period"]

        if len(prices) < slow_p + sig_p:
            return None

        close = pd.Series(prices)
        ema_fast = close.ewm(span=fast_p, adjust=False).mean()
        ema_slow = close.ewm(span=slow_p, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=sig_p, adjust=False).mean()

        curr_macd = float(macd_line.iloc[-1])
        curr_signal = float(signal_line.iloc[-1])
        prev_macd = float(macd_line.iloc[-2])
        prev_signal = float(signal_line.iloc[-2])
        price = prices[-1]

        self._indicators[code] = {
            "macd": round(curr_macd, 4),
            "signal": round(curr_signal, 4),
            "histogram": round(curr_macd - curr_signal, 4),
        }

        # 金叉 + 位於零軸之上 → 買入
        if prev_macd <= prev_signal and curr_macd > curr_signal and curr_macd > 0:
            if not self._in_position.get(code):
                self._in_position[code] = True
                signal = Signal(
                    action=SignalAction.BUY, symbol=code, price=price,
                    quantity=self.params["quantity"],
                    reason=f"MACD金叉: MACD={curr_macd:.4f} > Signal={curr_signal:.4f}",
                    confidence=0.7,
                )
                self._record_signal(signal)
                return signal

        # 死叉 → 賣出
        if prev_macd >= prev_signal and curr_macd < curr_signal:
            if self._in_position.get(code):
                self._in_position[code] = False
                signal = Signal(
                    action=SignalAction.SELL, symbol=code, price=price,
                    quantity=self.params["quantity"],
                    reason=f"MACD死叉: MACD={curr_macd:.4f} < Signal={curr_signal:.4f}",
                    confidence=0.7,
                )
                self._record_signal(signal)
                return signal

        return None

    def get_indicators(self) -> dict:
        return self._indicators


# ======================================================================
# 跨日策略註冊表 — 供 strategy_engine.py 使用
# ======================================================================

SWING_STRATEGIES = {
    "swing_trend_ma": SwingTrendMAStrategy,
    "swing_breakout": SwingBreakoutStrategy,
    "swing_pullback": SwingPullbackStrategy,
    "swing_macd": SwingMACDStrategy,
}
