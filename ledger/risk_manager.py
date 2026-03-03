"""
NeoStock2 帳本 — 風險管理模組

負責：
- 單筆停損/停利判斷
- 部位上限控制
- 每日最大虧損限額
- 策略訊號風險檢查
"""

import logging
from datetime import date

from ledger.database import Database
from ledger.models import Position, Trade

logger = logging.getLogger("neostock2.ledger.risk_manager")


class RiskManager:
    """風險管理器"""

    def __init__(self, db: Database, settings: dict = None):
        self.db = db
        self._settings = settings or {}
        self._risk_cfg = self._settings.get("risk_management", {})

        # 風控參數
        self.max_single_position_pct = self._risk_cfg.get("max_single_position_pct", 0.25)
        self.max_daily_loss = self._risk_cfg.get("max_daily_loss", 10000)
        self.default_stop_loss_pct = self._risk_cfg.get("default_stop_loss_pct", 0.05)
        self.default_take_profit_pct = self._risk_cfg.get("default_take_profit_pct", 0.10)
        self.max_total_positions = self._risk_cfg.get("max_total_positions", 10)
        self.max_single_amount = self._risk_cfg.get("max_single_amount", 0)  # 0 = 不限制
        self.daily_loss_halt = self._risk_cfg.get("daily_loss_halt", True)

        self._daily_loss = 0
        self._daily_date = date.today().isoformat()
        self._halted = False  # 每日虧損停機旗標

    def check_signal(self, signal, strategy_params: dict = None) -> tuple[bool, str]:
        """
        檢查交易訊號是否符合風控規則

        Args:
            signal: Signal 物件
            strategy_params: 策略專屬參數 (可覆寫全域設定)

        Returns:
            (是否允許, 原因說明)
        """
        params = strategy_params or {}
        # 取得生效的風控參數 (策略值 > 全域值)
        max_total_positions = params.get("max_total_positions", self.max_total_positions)
        max_single_position_pct = params.get("max_single_position_pct", self.max_single_position_pct)
        # 注意: max_daily_loss 通常是帳戶層級，不建議被策略覆寫，維持全域

        # === 檢查每日虧損限額 ===
        today = date.today().isoformat()
        if today != self._daily_date:
            self._daily_loss = 0
            self._daily_date = today

        if self._daily_loss >= self.max_daily_loss:
            if self.daily_loss_halt:
                self._halted = True
                logger.warning("🚨 每日虧損停機！禁止新開倉")
            return False, f"已達每日最大虧損限額 ({self.max_daily_loss:,.0f}元)"

        session = self.db.get_session()
        try:
            # === 買入前檢查 ===
            if signal.action.value == "Buy":
                # 檢查持倉數量上限 (針對該策略? 或是全帳戶?)
                # 這裡假設 max_total_positions 是指該策略能持有的最大檔數?
                # 為了相容性，我們先維持全帳戶檢查，但在策略參數中這通常解釋為 "單標的上限"?
                # 不，前端欄位是 "單標的上限 (張)" -> 這應該是 max_position_qty
                
                # 前端欄位: sMaxPosition (單標的上限 張數)
                # 這與 max_total_positions (總持倉檔數) 不同
                # 我們新增一個檢查: max_position_qty
                max_position_qty = params.get("max_position", 999)

                # 檢查全帳戶總檔數
                pos_count = session.query(Position).count()
                if pos_count >= self.max_total_positions: # 這是全域限制
                     return False, f"帳戶總持倉檔數已達上限 ({self.max_total_positions})"

                # 檢查單一標的持倉量
                existing = session.query(Position).filter_by(
                    code=signal.symbol
                ).first()
                
                current_qty = existing.quantity if existing else 0
                if current_qty + signal.quantity > max_position_qty:
                    return False, f"該標的持倉 ({current_qty}+{signal.quantity}) 將超過策略上限 ({max_position_qty}張)"
                
                # 檢查是否已持有該標的（避免重複買入）- 這裡邏輯保留
                if existing and existing.quantity > 0:
                    # 檢查單一標的部位佔比 (資金)
                    total_cost = sum(
                        p.total_cost for p in session.query(Position).all()
                    )
                    if total_cost > 0:
                        position_pct = existing.total_cost / total_cost
                        if position_pct >= max_single_position_pct:
                            return False, (
                                f"{signal.symbol} 部位佔比 "
                                f"{position_pct:.1%} >= {max_single_position_pct:.0%}"
                            )

            # === 賣出前檢查 ===
            elif signal.action.value == "Sell":
                position = session.query(Position).filter_by(
                    code=signal.symbol
                ).first()
                if position is None or position.quantity <= 0:
                    return False, f"{signal.symbol} 無持倉可賣出"
                if signal.quantity > position.quantity:
                    return False, (
                        f"賣出數量 ({signal.quantity}張) > "
                        f"持倉數量 ({position.quantity}張)"
                    )

            return True, "通過風控檢查"

        except Exception as e:
            logger.error(f"風控檢查錯誤: {e}")
            return False, f"風控檢查異常: {e}"
        finally:
            session.close()

    def check_stop_loss(self, positions: list[dict], strategy_map: dict = None) -> list[dict]:
        """
        檢查是否需要停損

        Args:
            positions: 持倉列表（含 unrealized_pnl_pct）
            strategy_map: 策略參數對照表 {strategy_name: params}

        Returns:
            需要停損的持倉列表
        """
        strategy_map = strategy_map or {}
        stop_list = []
        for pos in positions:
            pnl_pct = pos.get("unrealized_pnl_pct", 0) / 100  # 轉為小數
            
            # 取得該持倉策略的停損設定，若無則用全域預設
            strat_name = pos.get("strategy_name")
            strat_params = strategy_map.get(strat_name, {})
            stop_loss_pct = strat_params.get("stop_loss_pct", self.default_stop_loss_pct)

            if pnl_pct <= -stop_loss_pct:
                stop_list.append({
                    **pos,
                    "trigger": "stop_loss",
                    "threshold": f"-{stop_loss_pct:.0%}",
                    "current_pnl_pct": f"{pnl_pct:.2%}",
                })
                logger.warning(
                    f"⚠️ 停損觸發: {pos['code']} ({strat_name}) "
                    f"損益 {pnl_pct:.2%} <= -{stop_loss_pct:.0%}"
                )
        return stop_list

    def check_take_profit(self, positions: list[dict], strategy_map: dict = None) -> list[dict]:
        """
        檢查是否需要停利

        Args:
            positions: 持倉列表（含 unrealized_pnl_pct）
            strategy_map: 策略參數對照表 {strategy_name: params}

        Returns:
            需要停利的持倉列表
        """
        strategy_map = strategy_map or {}
        profit_list = []
        for pos in positions:
            pnl_pct = pos.get("unrealized_pnl_pct", 0) / 100
            
            # 取得該持倉策略的停利設定
            strat_name = pos.get("strategy_name")
            strat_params = strategy_map.get(strat_name, {})
            take_profit_pct = strat_params.get("take_profit_pct", self.default_take_profit_pct)

            if pnl_pct >= take_profit_pct:
                profit_list.append({
                    **pos,
                    "trigger": "take_profit",
                    "threshold": f"+{take_profit_pct:.0%}",
                    "current_pnl_pct": f"{pnl_pct:.2%}",
                })
                logger.info(
                    f"✅ 停利觸發: {pos['code']} ({strat_name}) "
                    f"損益 {pnl_pct:.2%} >= +{take_profit_pct:.0%}"
                )
        return profit_list

    def record_daily_loss(self, amount: float):
        """記錄每日虧損"""
        today = date.today().isoformat()
        if today != self._daily_date:
            self._daily_loss = 0
            self._daily_date = today
            self._halted = False  # 新的一天重置停機旗標
        if amount > 0:
            self._daily_loss += amount
            # 檢查是否觸發停機
            if self.daily_loss_halt and self._daily_loss >= self.max_daily_loss:
                self._halted = True
                logger.warning(f"🚨 每日虧損停機觸發！累計虧損: {self._daily_loss:,.0f} >= {self.max_daily_loss:,.0f}")

    @property
    def is_halted(self) -> bool:
        """是否已停機"""
        # 新的一天自動重置
        if date.today().isoformat() != self._daily_date:
            self._halted = False
        return self._halted

    def check_order_risk(self, symbol: str, action: str, quantity: int, price: float) -> tuple[bool, str]:
        """
        下單前快速風控檢查（不需要 Signal 物件）

        Args:
            symbol: 股票代碼
            action: 'Buy' / 'Sell'
            quantity: 張數
            price: 價格

        Returns:
            (True/False, 原因)
        """
        # 停機檢查
        if self.is_halted:
            return False, "每日虧損已停機，禁止新下單"

        if action == "Buy":
            # 單筆金額限制
            if self.max_single_amount > 0:
                order_amount = price * quantity * 1000  # 張 → 股 → 金額
                if order_amount > self.max_single_amount:
                    return False, f"單筆金額 {order_amount:,.0f} 超過上限 {self.max_single_amount:,.0f}"

            # 總持倉檔數檢查
            session = self.db.get_session()
            try:
                pos_count = session.query(Position).count()
                if pos_count >= self.max_total_positions:
                    return False, f"帳戶總持倉檔數已達上限 ({self.max_total_positions})"
            finally:
                session.close()

        return True, "通過風控檢查"

    def get_risk_summary(self, strategy_map: dict = None) -> dict:
        """取得風險摘要"""
        session = self.db.get_session()
        try:
            positions = session.query(Position).all()
            pos_dicts = [p.to_dict() for p in positions]

            stop_loss_alerts = self.check_stop_loss(pos_dicts, strategy_map)
            take_profit_alerts = self.check_take_profit(pos_dicts, strategy_map)

            total_unrealized = sum(p.unrealized_pnl for p in positions)
            max_loss_pos = (
                min(positions, key=lambda p: p.unrealized_pnl)
                if positions else None
            )

            return {
                "position_count": len(positions),
                "max_total_positions": self.max_total_positions,
                "daily_loss": round(self._daily_loss, 2),
                "max_daily_loss": self.max_daily_loss,
                "daily_loss_remaining": round(
                    self.max_daily_loss - self._daily_loss, 2
                ),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "stop_loss_threshold": f"-{self.default_stop_loss_pct:.0%} (預設)",
                "take_profit_threshold": f"+{self.default_take_profit_pct:.0%} (預設)",
                "stop_loss_alerts": len(stop_loss_alerts),
                "take_profit_alerts": len(take_profit_alerts),
                "alerts": stop_loss_alerts + take_profit_alerts,
                "worst_position": (
                    {
                        "code": max_loss_pos.code,
                        "unrealized_pnl": max_loss_pos.unrealized_pnl,
                        "pnl_pct": max_loss_pos.unrealized_pnl_pct,
                    }
                    if max_loss_pos
                    else None
                ),
            }
        finally:
            session.close()
