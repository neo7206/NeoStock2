"""
NeoStock2 策略 — 基底類別

所有交易策略都必須繼承此類別。
定義策略的標準介面：
- on_tick: 收到 Tick 時的處理邏輯
- on_bar: 收到 K 棒時的處理邏輯
- Signal: 交易訊號資料結構
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

logger = logging.getLogger("neostock2.strategies.base_strategy")


class SignalAction(str, Enum):
    """交易訊號動作"""
    BUY = "Buy"
    SELL = "Sell"
    HOLD = "Hold"


@dataclass
class Signal:
    """交易訊號"""
    action: SignalAction
    symbol: str
    price: float = 0
    quantity: int = 1  # 張
    reason: str = ""
    confidence: float = 0.0  # 0~1
    strategy_name: str = ""
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "action": self.action.value,
            "symbol": self.symbol,
            "price": self.price,
            "quantity": self.quantity,
            "reason": self.reason,
            "confidence": self.confidence,
            "strategy_name": self.strategy_name,
            "timestamp": self.timestamp.isoformat(),
        }


class BaseStrategy(ABC):
    """
    策略抽象基底類別

    所有策略需實作：
    - on_tick(tick_data): 即時 Tick 到達時觸發
    - on_bar(bar_data):   K 棒到達時觸發

    可選覆寫：
    - initialize():      策略初始化
    - get_indicators():   回傳當前指標值（供儀表板顯示）
    """

    # === 策略基本屬性 ===
    name: str = "BaseStrategy"
    description: str = ""
    version: str = "1.0"

    # === 預設參數（子類別可覆寫） ===
    default_params: dict = {}

    def __init__(self, symbols: list[str] = None, params: dict = None):
        """
        Args:
            symbols: 監控的標的列表
            params: 策略參數（覆蓋 default_params）
        """
        self.symbols = symbols or []
        self.params = {**self.default_params, **(params or {})}
        self._is_initialized = False
        self._signals_history: list[Signal] = []

    def initialize(self):
        """策略初始化（可覆寫）"""
        self._is_initialized = True
        logger.info(f"策略 [{self.name}] 初始化完成, 監控: {self.symbols}")

    @abstractmethod
    def on_tick(self, tick_data: dict) -> Signal | None:
        """
        即時 Tick 到達時觸發

        Args:
            tick_data: Tick 資料 dict，包含 code, close, volume 等

        Returns:
            Signal 交易訊號，或 None 表示不動作
        """
        pass

    @abstractmethod
    def on_bar(self, symbol: str, bars: "pd.DataFrame") -> Signal | None:
        """
        K 棒到達時觸發

        Args:
            symbol: 股票代碼
            bars: 包含 OHLCV 的 DataFrame

        Returns:
            Signal 交易訊號，或 None 表示不動作
        """
        pass

    def get_indicators(self) -> dict:
        """
        取得當前指標數值（供儀表板顯示，可覆寫）

        Returns:
            {指標名: 數值} 的 dict
        """
        return {}

    def get_info(self) -> dict:
        """取得策略資訊"""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "symbols": self.symbols,
            "params": self.params,
            "is_initialized": self._is_initialized,
            "indicators": self.get_indicators(),
        }

    def _record_signal(self, signal: Signal):
        """記錄訊號歷史"""
        signal.strategy_name = self.name
        self._signals_history.append(signal)
        # 保留最近 100 筆
        if len(self._signals_history) > 100:
            self._signals_history = self._signals_history[-100:]

    def get_signal_history(self) -> list[dict]:
        """取得訊號歷史"""
        return [s.to_dict() for s in self._signals_history]
