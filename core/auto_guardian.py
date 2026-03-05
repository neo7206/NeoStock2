"""
NeoStock2 核心 — 盤中自動停損停利監控 (AutoGuardian)

負責：
- 盤中定期掃描所有持倉
- 觸發停損 / 停利 / 移動停利 時自動下賣單
- 整合 Telegram 通知
"""

import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger("neostock2.core.auto_guardian")


class AutoGuardian:
    """盤中自動停損停利監控器"""

    def __init__(
        self,
        portfolio,
        risk_manager,
        order_manager,
        strategy_engine=None,
        notifier=None,
        settings: dict = None,
    ):
        self.portfolio = portfolio
        self.risk_manager = risk_manager
        self.order_manager = order_manager
        self.strategy_engine = strategy_engine
        self.notifier = notifier
        self._settings = settings or {}

        cfg = self._settings.get("auto_guardian", {})
        self.enabled = cfg.get("enabled", True)
        self.check_interval = cfg.get("check_interval", 30)  # 秒
        self.trailing_stop_enabled = cfg.get("trailing_stop_enabled", True)
        self.trailing_stop_pct = cfg.get("trailing_stop_pct", 0.03)  # 3%

        # 移動停利追蹤：{symbol: highest_price}
        self._trailing_highs: dict[str, float] = {}

        # 已觸發但未完成的訂單（避免重複下單）
        self._pending_exits: set[str] = set()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_check: str = ""
        self._stats = {
            "stop_loss_triggered": 0,
            "take_profit_triggered": 0,
            "trailing_stop_triggered": 0,
            "total_checks": 0,
        }

    def start(self):
        """啟動監控"""
        if not self.enabled:
            logger.info("⏸️ AutoGuardian 已停用（設定 enabled=false）")
            return
        if self._running:
            logger.warning("AutoGuardian 已在運行中")
            return

        self._stop_event.clear()
        self._running = True
        self._pending_exits.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="AutoGuardian",
        )
        self._thread.start()
        logger.info(
            f"🛡️ AutoGuardian 已啟動"
            f"（間隔 {self.check_interval}s, "
            f"移動停利={'開' if self.trailing_stop_enabled else '關'}）"
        )

    def stop(self):
        """停止監控"""
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("🛡️ AutoGuardian 已停止")

    def _monitor_loop(self):
        """監控主迴圈"""
        while not self._stop_event.is_set():
            try:
                self._check_positions()
            except Exception as e:
                logger.error(f"AutoGuardian 掃描錯誤: {e}")

            self._stop_event.wait(self.check_interval)

    def _check_positions(self):
        """掃描所有持倉，檢查停損/停利/移動停利"""
        positions = self.portfolio.get_positions()
        if not positions:
            return

        self._stats["total_checks"] += 1
        self._last_check = datetime.now().strftime("%H:%M:%S")

        # 取得策略參數對照表
        strategy_map = self._build_strategy_map()

        # === 1. 停損檢查 ===
        stop_loss_list = self.risk_manager.check_stop_loss(positions, strategy_map)
        for pos in stop_loss_list:
            self._execute_exit(pos, "stop_loss")

        # === 2. 停利檢查 ===
        take_profit_list = self.risk_manager.check_take_profit(positions, strategy_map)
        for pos in take_profit_list:
            self._execute_exit(pos, "take_profit")

        # === 3. 移動停利檢查 ===
        if self.trailing_stop_enabled:
            self._check_trailing_stop(positions, strategy_map)

    def _check_trailing_stop(self, positions: list[dict], strategy_map: dict):
        """
        移動停利邏輯：

        1. 追蹤每檔持倉的最高市價
        2. 當市價從最高點回落超過 trailing_stop_pct 時觸發賣出
        3. 只有獲利中的持倉才啟用移動停利
        """
        for pos in positions:
            code = pos.get("code", "")
            market_price = pos.get("market_price", 0)
            avg_cost = pos.get("avg_cost", 0)
            pnl_pct = pos.get("unrealized_pnl_pct", 0) / 100

            if market_price <= 0 or avg_cost <= 0:
                continue

            # 取得策略層級的移動停利設定
            strat_name = pos.get("strategy_name")
            strat_params = strategy_map.get(strat_name, {})
            trailing_pct = strat_params.get(
                "trailing_stop_pct", self.trailing_stop_pct
            )

            # 只有獲利中的持倉才啟用移動停利
            if pnl_pct <= 0:
                # 虧損中，清除追蹤
                self._trailing_highs.pop(code, None)
                continue

            # 更新最高價
            current_high = self._trailing_highs.get(code, market_price)
            if market_price > current_high:
                self._trailing_highs[code] = market_price
                current_high = market_price

            # 計算從最高點的回落幅度
            if current_high > 0:
                drawdown = (current_high - market_price) / current_high
                if drawdown >= trailing_pct:
                    pos["trigger"] = "trailing_stop"
                    pos["threshold"] = f"回撤 {trailing_pct:.0%}"
                    pos["current_pnl_pct"] = f"{pnl_pct:.2%}"
                    pos["trailing_high"] = current_high
                    self._execute_exit(pos, "trailing_stop")

    def _execute_exit(self, pos: dict, trigger_type: str):
        """
        執行出場下單

        Args:
            pos: 持倉 dict
            trigger_type: 'stop_loss' / 'take_profit' / 'trailing_stop'
        """
        code = pos.get("code", "")
        quantity = pos.get("quantity", 0)

        if not code or quantity <= 0:
            return

        # 避免重複下單
        exit_key = f"{code}_{trigger_type}"
        if exit_key in self._pending_exits:
            return
        self._pending_exits.add(exit_key)

        # 組合出場原因
        trigger_names = {
            "stop_loss": "停損",
            "take_profit": "停利",
            "trailing_stop": "移動停利",
        }
        trigger_label = trigger_names.get(trigger_type, trigger_type)
        pnl_pct = pos.get("current_pnl_pct", "N/A")
        threshold = pos.get("threshold", "N/A")

        logger.warning(
            f"🚨 [{trigger_label}] 自動出場: {code} "
            f"{quantity}張, 損益 {pnl_pct}, 門檻 {threshold}"
        )

        try:
            result = self.order_manager.place_order(
                symbol=code,
                action="Sell",
                quantity=quantity,
                price=0,  # 市價單
                auto_price=True,  # 使用五檔自動取價
                strategy_name=f"auto_guardian_{trigger_type}",
            )

            if result.get("success"):
                logger.info(f"✅ [{trigger_label}] 下單成功: {code} {quantity}張")
                self._stats[f"{trigger_type}_triggered"] += 1

                # 清除移動停利追蹤
                self._trailing_highs.pop(code, None)

                # Telegram 推播
                self._notify_exit(code, quantity, trigger_label, pnl_pct, threshold)
            else:
                logger.error(
                    f"❌ [{trigger_label}] 下單失敗: {code} - {result.get('error')}"
                )
                # 下單失敗，移除 pending 標記以便重試
                self._pending_exits.discard(exit_key)

        except Exception as e:
            logger.error(f"❌ [{trigger_label}] 下單異常: {code} - {e}")
            self._pending_exits.discard(exit_key)

    def _notify_exit(
        self, code: str, quantity: int, trigger: str, pnl_pct: str, threshold: str
    ):
        """推播出場通知"""
        if not self.notifier or not self.notifier.enabled:
            return
        try:
            msg = (
                f"🛡️ *AutoGuardian {trigger}通知*\n"
                f"標的: `{code}`\n"
                f"數量: {quantity} 張\n"
                f"損益: {pnl_pct}\n"
                f"觸發門檻: {threshold}"
            )
            self.notifier.send(msg)
        except Exception as e:
            logger.error(f"推播通知失敗: {e}")

    def _build_strategy_map(self) -> dict:
        """建立策略參數對照表 {strategy_name: params}"""
        if not self.strategy_engine:
            return {}
        strategy_map = {}
        for info in self.strategy_engine.get_strategies_info():
            name = info.get("name", "")
            params = info.get("params", {})
            strategy_map[name] = params
        return strategy_map

    def clear_pending(self, symbol: str = None):
        """
        清除已觸發的出場記錄（供成交回報後重新啟用監控）

        Args:
            symbol: 指定標的，None = 全部清除
        """
        if symbol:
            self._pending_exits = {
                k for k in self._pending_exits if not k.startswith(f"{symbol}_")
            }
        else:
            self._pending_exits.clear()

    def get_status(self) -> dict:
        """取得 AutoGuardian 狀態"""
        return {
            "running": self._running,
            "enabled": self.enabled,
            "check_interval": self.check_interval,
            "trailing_stop_enabled": self.trailing_stop_enabled,
            "trailing_stop_pct": self.trailing_stop_pct,
            "last_check": self._last_check,
            "pending_exits": list(self._pending_exits),
            "trailing_highs": {
                k: round(v, 2) for k, v in self._trailing_highs.items()
            },
            "stats": self._stats.copy(),
        }
