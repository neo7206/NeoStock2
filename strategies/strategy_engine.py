"""
NeoStock2 策略 — 策略排程與執行引擎

負責：
- 管理多策略的生命週期
- 將行情數據分發給策略
- 收集策略訊號並執行下單
- 策略配置持久化（自動存檔/載入）
"""

import json
import importlib
import inspect
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

from strategies.base_strategy import BaseStrategy, Signal, SignalAction
from strategies.builtin.sma_crossover import SMACrossoverStrategy
from strategies.builtin.rsi_reversal import RSIReversalStrategy
from strategies.builtin.macd_signal import MACDSignalStrategy
from strategies.builtin.bollinger_band import BollingerBandStrategy
from strategies.builtin.swing_adapter import (
    SwingTrendMAStrategy,
    SwingBreakoutStrategy,
    SwingPullbackStrategy,
    SwingMACDStrategy,
)
from strategies.persistence import save_strategies, load_strategies

logger = logging.getLogger("neostock2.strategies.strategy_engine")

# 可用策略的註冊表（內建 + 跨日波段 + 自動描掃）
STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] = {
    # 盤中即時策略
    "sma_crossover": SMACrossoverStrategy,
    "rsi_reversal": RSIReversalStrategy,
    "macd_signal": MACDSignalStrategy,
    "bollinger_band": BollingerBandStrategy,
    # 跨日波段策略
    "swing_trend_ma": SwingTrendMAStrategy,
    "swing_breakout": SwingBreakoutStrategy,
    "swing_pullback": SwingPullbackStrategy,
    "swing_macd": SwingMACDStrategy,
}


def _discover_strategies(search_dirs: list[str] = None) -> dict[str, type[BaseStrategy]]:
    """
    自動描掃目錄中繼承 BaseStrategy 的策略類別
    
    Args:
        search_dirs: 要描掃的目錄列表，預設為 ['strategies/custom']
    """
    discovered = {}
    if search_dirs is None:
        search_dirs = ["strategies/custom"]
    
    for dir_path in search_dirs:
        path = Path(dir_path)
        if not path.exists():
            continue
        
        for py_file in path.glob("*.py"):
            if py_file.name.startswith("_"):
                continue
            
            module_name = f"{dir_path.replace('/', '.')}.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseStrategy) and obj is not BaseStrategy:
                        key = getattr(obj, "name", py_file.stem)
                        if isinstance(key, str):
                            key = key.lower().replace(" ", "_")
                        discovered[key] = obj
                        logger.info(f"🔍 發現自訂策略: {key} ({obj.__name__})")
            except Exception as e:
                logger.warning(f"載入策略 {py_file} 失敗: {e}")
    
    return discovered

# 啟動時自動描掃 custom 目錄
STRATEGY_REGISTRY.update(_discover_strategies())


class StrategyEngine:
    """策略排程與執行引擎"""

    def __init__(
        self,
        order_manager=None,
        portfolio=None,
        risk_manager=None,
        settings: dict = None,
    ):
        self.order_manager = order_manager
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self._settings = settings or {}

        self._strategies: dict[str, BaseStrategy] = {}  # name -> strategy
        self._strategy_types: dict[str, str] = {}  # name -> strategy_type key
        self._enabled: dict[str, bool] = {}
        self._signal_callbacks: list[Callable] = []
        self._running = False
        self._lock = threading.Lock()
        self.position_sizer = None  # 由 main.py 注入

    def register_strategy(
        self,
        name: str,
        strategy_type: str,
        symbols: list[str],
        params: dict = None,
        enabled: bool = False,
    ) -> bool:
        """
        註冊一個策略

        Args:
            name: 策略實例名稱（唯一）
            strategy_type: 策略類型（如 'sma_crossover'）
            symbols: 監控的標的列表
            params: 策略參數
            enabled: 是否啟用

        Returns:
            是否註冊成功
        """
        if strategy_type not in STRATEGY_REGISTRY:
            logger.error(f"未知策略類型: {strategy_type}")
            return False

        strategy_cls = STRATEGY_REGISTRY[strategy_type]
        strategy = strategy_cls(symbols=symbols, params=params)
        strategy.name = name
        strategy.initialize()

        with self._lock:
            self._strategies[name] = strategy
            self._strategy_types[name] = strategy_type
            self._enabled[name] = enabled

        logger.info(f"策略已註冊: [{name}] ({strategy_type}), 啟用={enabled}")
        self._auto_save()
        return True

    def enable_strategy(self, name: str) -> bool:
        """啟用策略"""
        with self._lock:
            if name in self._strategies:
                self._enabled[name] = True
                logger.info(f"策略已啟用: [{name}]")
                self._auto_save()
                return True
        return False

    def disable_strategy(self, name: str) -> bool:
        """停用策略"""
        with self._lock:
            if name in self._strategies:
                self._enabled[name] = False
                logger.info(f"策略已停用: [{name}]")
                self._auto_save()
                return True
        return False

    def remove_strategy(self, name: str) -> bool:
        """移除策略"""
        with self._lock:
            if name in self._strategies:
                del self._strategies[name]
                self._strategy_types.pop(name, None)
                del self._enabled[name]
                logger.info(f"策略已移除: [{name}]")
                self._auto_save()
                return True
        return False

    def process_tick(self, tick_data: dict):
        """
        處理 Tick 數據 — 分發給所有啟用的策略

        Args:
            tick_data: Tick 資料 dict
        """
        with self._lock:
            active = [
                (name, strat)
                for name, strat in self._strategies.items()
                if self._enabled.get(name, False)
            ]

        for name, strategy in active:
            try:
                signal = strategy.on_tick(tick_data)
                if signal:
                    self._handle_signal(signal)
            except Exception as e:
                logger.error(f"策略 [{name}] 處理 Tick 錯誤: {e}")

    def process_bar(self, symbol: str, bars):
        """
        處理 K 棒數據 — 分發給所有啟用的策略

        Args:
            symbol: 股票代碼
            bars: DataFrame
        """
        with self._lock:
            active = [
                (name, strat)
                for name, strat in self._strategies.items()
                if self._enabled.get(name, False)
            ]

        for name, strategy in active:
            try:
                signal = strategy.on_bar(symbol, bars)
                if signal:
                    self._handle_signal(signal)
            except Exception as e:
                logger.error(f"策略 [{name}] 處理 K棒 錯誤: {e}")

    def _handle_signal(self, signal: Signal):
        """處理策略產生的交易訊號"""
        # 取得策略實例與參數
        strategy = self._strategies.get(signal.strategy_name)
        strategy_params = strategy.params if strategy else {}

        # 應用策略設定的每筆張數 (若 Signal 未指定或為預設值 1)
        if signal.quantity == 1:
            lot_size = strategy_params.get("lot_size", 1)
            signal.quantity = int(lot_size)

        # === 部位管理器自動計算買入數量 ===
        if signal.action == SignalAction.BUY and self.position_sizer and self.portfolio:
            try:
                summary = self.portfolio.get_portfolio_summary()
                account_value = summary.get("total_asset", 0)
                stop_loss_pct = strategy_params.get("stop_loss_pct", 0.05)
                if account_value > 0 and signal.price > 0:
                    suggested = self.position_sizer.calculate(
                        account_value=account_value,
                        price=signal.price,
                        stop_loss_pct=stop_loss_pct,
                    )
                    if suggested > signal.quantity:
                        logger.info(
                            f"📐 部位管理器調整: {signal.symbol} "
                            f"{signal.quantity}張 → {suggested}張"
                        )
                        signal.quantity = suggested
            except Exception as e:
                logger.warning(f"部位管理器計算失敗: {e}")

        logger.info(
            f"📊 訊號: [{signal.strategy_name}] "
            f"{signal.action.value} {signal.symbol} "
            f"@ {signal.price} ({signal.reason}) "
            f"Qty={signal.quantity} "
            f"Params={json.dumps(strategy_params, ensure_ascii=False)}"
        )

        # 通知回呼
        for callback in self._signal_callbacks:
            try:
                callback(signal)
            except Exception as e:
                logger.error(f"訊號回呼錯誤: {e}")

        # 風險檢查
        if self.risk_manager:
            # 傳入策略參數以進行針對性風控
            allowed, reason = self.risk_manager.check_signal(signal, strategy_params)
            if not allowed:
                logger.warning(f"⚠️ 風險管理攔截: {reason}")
                return

        # 執行下單
        if self.order_manager and signal.action != SignalAction.HOLD:
            # === 賣出前檢查持倉 ===
            if signal.action == SignalAction.SELL and self.portfolio:
                positions = self.portfolio.get_positions()
                held = next((p for p in positions if p["code"] == signal.symbol), None)
                held_qty = held["quantity"] if held else 0
                if held_qty <= 0:
                    logger.warning(
                        f"⚠️ 攔截賣出: {signal.symbol} 持倉為 0，無法賣出"
                    )
                    return
                # 自動調整賣出數量不超過持倉
                if signal.quantity > held_qty:
                    logger.info(
                        f"📉 調整賣出數量: {signal.symbol} "
                        f"{signal.quantity}張 → {held_qty}張 (持倉上限)"
                    )
                    signal.quantity = held_qty

            # 取得策略設定的價格類型 (預設 LMT)
            order_type = strategy_params.get("order_type", "LMT")
            
            result = self.order_manager.place_order(
                symbol=signal.symbol,
                action=signal.action.value,
                quantity=signal.quantity,
                price=signal.price,
                price_type=order_type,
                strategy_name=signal.strategy_name,
            )

            if result.get("success"):
                logger.info(f"✅ 策略下單成功: {signal.symbol} {signal.action.value} {signal.quantity}張")
            else:
                logger.error(f"下單失敗: {result.get('error', '未知錯誤')}")

    def get_strategies_info(self) -> list[dict]:
        """取得所有策略資訊"""
        with self._lock:
            result = []
            for name, strategy in self._strategies.items():
                info = strategy.get_info()
                info["enabled"] = self._enabled.get(name, False)
                info["strategy_type"] = self._strategy_types.get(name, "")
                result.append(info)
            return result

    def get_strategy_info(self, name: str) -> dict | None:
        """取得單一策略資訊"""
        with self._lock:
            strategy = self._strategies.get(name)
            if strategy:
                info = strategy.get_info()
                info["enabled"] = self._enabled.get(name, False)
                return info
        return None

    def get_all_signals(self) -> list[dict]:
        """取得所有策略的訊號歷史"""
        with self._lock:
            signals = []
            for strategy in self._strategies.values():
                signals.extend(strategy.get_signal_history())
            signals.sort(key=lambda s: s["timestamp"], reverse=True)
            return signals[:100]

    def on_signal(self, callback: Callable):
        """註冊訊號回呼"""
        self._signal_callbacks.append(callback)

    @staticmethod
    def get_available_strategies() -> list[dict]:
        """取得可用的策略類型列表"""
        result = []
        for key, cls in STRATEGY_REGISTRY.items():
            result.append({
                "type": key,
                "name": cls.name,
                "description": cls.description,
                "default_params": cls.default_params,
            })
        return result

    @staticmethod
    def reload_strategies() -> int:
        """熱重載：重新描掃 custom 目錄並更新註冊表"""
        new = _discover_strategies()
        STRATEGY_REGISTRY.update(new)
        logger.info(f"策略熱重載完成，目前共 {len(STRATEGY_REGISTRY)} 個策略")
        return len(STRATEGY_REGISTRY)

    # === 盤前自動掃描 ===

    def run_daily_scan(self, market_data=None):
        """
        盤前自動掃描：遍歷所有啟用的策略，
        取每檔標的近 60 天 K 棒，呼叫 on_bar() 檢查訊號。
        
        用途：讓跨日波段策略（Swing）能在盤前根據昨日 K 棒自動產生訊號。
        
        Args:
            market_data: MarketDataManager 實例（用於取 K 棒）
        """
        md = market_data or getattr(self, '_market_data', None)
        if md is None:
            logger.warning("run_daily_scan: 無 market_data 模組，跳過")
            return

        from datetime import date, timedelta
        end_str = date.today().isoformat()
        start_str = (date.today() - timedelta(days=90)).isoformat()

        with self._lock:
            active = [
                (name, strat)
                for name, strat in self._strategies.items()
                if self._enabled.get(name, False)
            ]

        if not active:
            logger.info("📋 盤前掃描：無啟用策略，跳過")
            return

        logger.info(f"📋 盤前掃描開始：{len(active)} 個策略")
        signal_count = 0

        # 收集所有需要查詢的標的（去重）
        all_symbols = set()
        for _, strategy in active:
            all_symbols.update(strategy.symbols)

        # 批量取 K 棒（每檔只查一次）
        kbar_cache: dict = {}
        for symbol in all_symbols:
            try:
                df = md.get_kbars(symbol, start=start_str, end=end_str)
                if not df.empty:
                    kbar_cache[symbol] = df
                    logger.debug(f"  取得 {symbol} K 棒: {len(df)} 根")
                else:
                    logger.warning(f"  {symbol} 無 K 棒資料")
            except Exception as e:
                logger.error(f"  取得 {symbol} K 棒失敗: {e}")

        # 遍歷每個策略 × 每檔標的
        for name, strategy in active:
            for symbol in strategy.symbols:
                bars = kbar_cache.get(symbol)
                if bars is None or bars.empty:
                    continue
                try:
                    signal = strategy.on_bar(symbol, bars)
                    if signal:
                        signal_count += 1
                        logger.info(
                            f"🔔 盤前訊號: [{name}] "
                            f"{signal.action.value} {signal.symbol} "
                            f"@ {signal.price} ({signal.reason})"
                        )
                        self._handle_signal(signal)
                except Exception as e:
                    logger.error(f"策略 [{name}] 盤前掃描 {symbol} 錯誤: {e}")

        logger.info(f"📋 盤前掃描完成：產生 {signal_count} 個訊號")

    # === 持久化 ===

    def _auto_save(self):
        """自動存檔策略配置（lock 內複製資料，lock 外寫入磁碟）"""
        try:
            with self._lock:
                infos = []
                for name, strategy in self._strategies.items():
                    infos.append({
                        "name": name,
                        "strategy_type": self._strategy_types.get(name, ""),
                        "symbols": list(strategy.symbols),
                        "params": dict(strategy.params),
                        "enabled": self._enabled.get(name, False),
                    })
            # lock 外寫入磁碟，避免死鎖
            save_strategies(infos)
        except Exception as e:
            logger.error(f"策略自動存檔失敗: {e}")

    def load_saved_strategies(self) -> int:
        """
        從磁碟載入上次儲存的策略配置

        Returns:
            成功載入的策略數量
        """
        saved = load_strategies()
        count = 0
        for cfg in saved:
            name = cfg.get("name", "")
            strategy_type = cfg.get("strategy_type", "")
            symbols = cfg.get("symbols", [])
            params = cfg.get("params", {})
            enabled = cfg.get("enabled", False)

            if not name or not strategy_type:
                logger.warning(f"跳過無效策略配置: {cfg}")
                continue

            if strategy_type not in STRATEGY_REGISTRY:
                logger.warning(f"跳過未知策略類型: {strategy_type}")
                continue

            success = self.register_strategy(
                name=name,
                strategy_type=strategy_type,
                symbols=symbols,
                params=params,
                enabled=enabled,
            )
            if success:
                count += 1

        logger.info(f"✅ 從磁碟載入 {count}/{len(saved)} 個策略")
        return count
