"""
NeoStock2 帳本 — 投報率計算模組

負責：
- 已實現 / 未實現損益計算
- 投報率（ROI）計算（含手續費與稅）
- 年化報酬率
- 夏普比率
- 淨值曲線生成
"""

import logging
import math
from datetime import datetime, timedelta

from ledger.database import Database
from ledger.models import Trade, Position, DailySnapshot

logger = logging.getLogger("neostock2.ledger.roi_calculator")


class ROICalculator:
    """投報率計算器"""

    def __init__(self, db: Database):
        self.db = db

    def calculate_realized_pnl(self, code: str = None) -> dict:
        """
        計算已實現損益

        Args:
            code: 股票代碼（None = 全部）

        Returns:
            已實現損益摘要
        """
        session = self.db.get_session()
        try:
            query = session.query(Trade)
            if code:
                query = query.filter_by(code=code)

            trades = query.order_by(Trade.created_at).all()

            # 使用先進先出法 (FIFO) 計算已實現損益
            buy_queue: dict[str, list] = {}  # code -> [(price, qty, fee)]
            total_realized = 0
            total_fee = 0
            total_tax = 0
            trade_count = 0

            for trade in trades:
                total_fee += trade.fee
                total_tax += trade.tax

                if trade.action == "Buy":
                    buy_queue.setdefault(trade.code, [])
                    buy_queue[trade.code].append({
                        "price": trade.price,
                        "quantity": trade.quantity,
                        "fee_per_share": trade.fee / (trade.quantity * 1000)
                        if trade.quantity > 0
                        else 0,
                    })

                elif trade.action == "Sell":
                    remaining = trade.quantity
                    sell_price = trade.price
                    sell_fee = trade.fee
                    sell_tax = trade.tax

                    queue = buy_queue.get(trade.code, [])
                    while remaining > 0 and queue:
                        buy = queue[0]
                        matched = min(remaining, buy["quantity"])

                        # 成本 = 買入價 + 手續費
                        cost_per_share = buy["price"] + buy["fee_per_share"]
                        revenue_per_share = sell_price

                        pnl = (revenue_per_share - cost_per_share) * matched * 1000
                        total_realized += pnl

                        buy["quantity"] -= matched
                        remaining -= matched
                        trade_count += 1

                        if buy["quantity"] <= 0:
                            queue.pop(0)

                    # 扣除賣出方的費用
                    total_realized -= sell_fee + sell_tax

            return {
                "total_realized_pnl": round(total_realized, 2),
                "total_fee": round(total_fee, 2),
                "total_tax": round(total_tax, 2),
                "total_costs": round(total_fee + total_tax, 2),
                "matched_trades": trade_count,
            }
        finally:
            session.close()

    def calculate_roi(self, initial_capital: float = None) -> dict:
        """
        計算整體投報率

        Args:
            initial_capital: 初始資金（若未指定，使用已投入的總金額）

        Returns:
            ROI 摘要
        """
        session = self.db.get_session()
        try:
            # 已投入的總金額
            from sqlalchemy import func

            total_buy = (
                session.query(func.sum(Trade.amount))
                .filter(Trade.action == "Buy")
                .scalar()
                or 0
            )

            if initial_capital is None:
                initial_capital = total_buy

            if initial_capital <= 0:
                return {
                    "roi_pct": 0,
                    "message": "無交易記錄",
                }

            # 已實現損益
            realized = self.calculate_realized_pnl()

            # 未實現損益
            positions = session.query(Position).all()
            total_unrealized = sum(p.unrealized_pnl for p in positions)
            total_market_value = sum(p.market_value for p in positions)

            # 總損益 = 已實現 + 未實現
            total_pnl = realized["total_realized_pnl"] + total_unrealized

            # ROI
            roi_pct = (total_pnl / initial_capital) * 100

            # 交易期間（天數）
            first_trade = (
                session.query(Trade)
                .order_by(Trade.created_at)
                .first()
            )
            if first_trade:
                days = (datetime.now() - first_trade.created_at).days
                days = max(days, 1)  # 至少 1 天
            else:
                days = 1

            # 年化報酬率
            annualized_roi = ((1 + total_pnl / initial_capital) ** (365 / days) - 1) * 100

            return {
                "initial_capital": round(initial_capital, 2),
                "total_invested": round(total_buy, 2),
                "current_market_value": round(total_market_value, 2),
                "realized_pnl": round(realized["total_realized_pnl"], 2),
                "unrealized_pnl": round(total_unrealized, 2),
                "total_pnl": round(total_pnl, 2),
                "total_costs": round(realized["total_costs"], 2),
                "roi_pct": round(roi_pct, 2),
                "annualized_roi_pct": round(annualized_roi, 2),
                "trading_days": days,
            }
        finally:
            session.close()

    def calculate_sharpe_ratio(self, risk_free_rate: float = 0.015) -> float | None:
        """
        計算夏普比率（基於每日快照）

        Args:
            risk_free_rate: 無風險利率（年化，預設 1.5%）

        Returns:
            夏普比率，若數據不足則回傳 None
        """
        session = self.db.get_session()
        try:
            snapshots = (
                session.query(DailySnapshot)
                .order_by(DailySnapshot.date)
                .all()
            )

            if len(snapshots) < 2:
                return None

            # 計算每日報酬率
            daily_returns = []
            for i in range(1, len(snapshots)):
                prev_asset = snapshots[i - 1].total_asset
                curr_asset = snapshots[i].total_asset
                if prev_asset > 0:
                    daily_return = (curr_asset - prev_asset) / prev_asset
                    daily_returns.append(daily_return)

            if not daily_returns:
                return None

            avg_return = sum(daily_returns) / len(daily_returns)
            daily_rf = risk_free_rate / 252  # 年化 → 日化

            variance = sum((r - avg_return) ** 2 for r in daily_returns) / len(
                daily_returns
            )
            std = math.sqrt(variance)

            if std == 0:
                return None

            sharpe = (avg_return - daily_rf) / std * math.sqrt(252)
            return round(sharpe, 4)
        finally:
            session.close()

    def get_equity_curve(self) -> list[dict]:
        """
        取得淨值曲線（供圖表用）

        Returns:
            [{"date": "YYYY-MM-DD", "total_asset": 金額}, ...]
        """
        session = self.db.get_session()
        try:
            snapshots = (
                session.query(DailySnapshot)
                .order_by(DailySnapshot.date)
                .all()
            )
            return [
                {
                    "date": s.date,
                    "total_asset": s.total_asset,
                    "market_value": s.market_value,
                    "cash": s.cash,
                    "realized_pnl": s.realized_pnl,
                    "unrealized_pnl": s.unrealized_pnl,
                }
                for s in snapshots
            ]
        finally:
            session.close()

    def get_full_report(self, initial_capital: float = None) -> dict:
        """取得完整投報率報告"""
        roi = self.calculate_roi(initial_capital)
        sharpe = self.calculate_sharpe_ratio()
        equity = self.get_equity_curve()

        roi["sharpe_ratio"] = sharpe
        roi["equity_curve_points"] = len(equity)
        return roi
