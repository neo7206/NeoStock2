"""
NeoStock2 帳本 — 部位管理器 (Position Sizer)

負責：
- 根據帳戶資金和風險偏好計算每筆交易的最佳下單數量
- 支援固定百分比風險法和 Kelly Criterion
"""

import logging
import math

logger = logging.getLogger("neostock2.ledger.position_sizer")


class PositionSizer:
    """部位管理器"""

    def __init__(self, settings: dict = None):
        self._settings = settings or {}
        cfg = self._settings.get("position_sizer", {})

        self.method = cfg.get("method", "fixed_pct")  # fixed_pct / kelly
        self.risk_per_trade_pct = cfg.get("risk_per_trade_pct", 0.02)  # 2%
        self.max_position_pct = cfg.get("max_position_pct", 0.25)  # 單檔最多 25%
        self.min_lots = cfg.get("min_lots", 1)  # 最小張數

    def calculate(
        self,
        account_value: float,
        price: float,
        stop_loss_pct: float = 0.05,
        win_rate: float = 0.5,
        avg_win_loss_ratio: float = 1.5,
    ) -> int:
        """
        計算最佳下單張數

        Args:
            account_value: 帳戶淨值
            price: 股票價格（每股）
            stop_loss_pct: 停損百分比
            win_rate: 勝率（僅 Kelly 需要）
            avg_win_loss_ratio: 平均盈虧比（僅 Kelly 需要）

        Returns:
            建議張數（最少 1 張）
        """
        if account_value <= 0 or price <= 0:
            return self.min_lots

        if self.method == "kelly":
            lots = self._kelly_sizing(
                account_value, price, win_rate, avg_win_loss_ratio
            )
        else:
            lots = self._fixed_pct_sizing(account_value, price, stop_loss_pct)

        # 上限：單檔不超過帳戶淨值的 max_position_pct
        max_lots = math.floor(
            (account_value * self.max_position_pct) / (price * 1000)
        )
        lots = min(lots, max(max_lots, self.min_lots))

        return max(lots, self.min_lots)

    def _fixed_pct_sizing(
        self, account_value: float, price: float, stop_loss_pct: float
    ) -> int:
        """
        固定百分比風險法

        原理：每筆交易的最大虧損金額 = 帳戶淨值 × 每筆風險百分比
        張數 = 最大虧損金額 ÷ (價格 × 1000 × 停損百分比)
        """
        if stop_loss_pct <= 0:
            stop_loss_pct = 0.05  # 預設 5%

        max_risk_amount = account_value * self.risk_per_trade_pct
        risk_per_lot = price * 1000 * stop_loss_pct  # 每張的停損金額
        lots = math.floor(max_risk_amount / risk_per_lot)

        logger.debug(
            f"固定百分比: 帳戶={account_value:,.0f}, "
            f"最大風險={max_risk_amount:,.0f}, "
            f"每張風險={risk_per_lot:,.0f}, "
            f"建議={lots}張"
        )
        return lots

    def _kelly_sizing(
        self,
        account_value: float,
        price: float,
        win_rate: float,
        avg_win_loss_ratio: float,
    ) -> int:
        """
        Kelly Criterion

        公式：f* = (p × b - q) / b
        - p = 勝率
        - q = 1 - p（敗率）
        - b = 平均盈虧比

        實務上使用 Half Kelly (f*/2) 以降低波動
        """
        if win_rate <= 0 or win_rate >= 1 or avg_win_loss_ratio <= 0:
            return self.min_lots

        p = win_rate
        q = 1 - p
        b = avg_win_loss_ratio

        kelly_fraction = (p * b - q) / b
        if kelly_fraction <= 0:
            logger.warning(
                f"Kelly 為負值 ({kelly_fraction:.4f})，"
                f"勝率={p:.2f}, 盈虧比={b:.2f}，建議不下單"
            )
            return self.min_lots

        # Half Kelly 更保守
        half_kelly = kelly_fraction / 2
        position_value = account_value * half_kelly
        lots = math.floor(position_value / (price * 1000))

        logger.debug(
            f"Kelly: 勝率={p:.2f}, 盈虧比={b:.2f}, "
            f"f*={kelly_fraction:.4f}, half_kelly={half_kelly:.4f}, "
            f"position_value={position_value:,.0f}, 建議={lots}張"
        )
        return lots

    def get_info(self) -> dict:
        """取得部位管理器資訊"""
        return {
            "method": self.method,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_position_pct": self.max_position_pct,
            "min_lots": self.min_lots,
        }
