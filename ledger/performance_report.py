"""
NeoStock2 帳本 — 績效歸因報告

負責：
- 按策略分組統計（勝率、平均損益、最大回撤）
- 產生每日/每週績效報告
- 標記表現差的策略
"""

import logging
from datetime import datetime, timedelta
from collections import defaultdict

from ledger.database import Database
from ledger.models import Trade, Position, DailySnapshot

logger = logging.getLogger("neostock2.ledger.performance_report")


class PerformanceReport:
    """績效歸因報告產生器"""

    def __init__(self, db: Database):
        self.db = db

    def generate(self, days: int = 30) -> dict:
        """
        產生績效報告

        Args:
            days: 統計天數

        Returns:
            如下結構的 dict
        """
        session = self.db.get_session()
        try:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()

            # 取得期間內的交易
            trades = (
                session.query(Trade)
                .filter(Trade.created_at >= cutoff)
                .order_by(Trade.created_at)
                .all()
            )

            # 取得當前持倉
            positions = session.query(Position).all()

            # 按策略分組
            strategy_stats = self._analyze_by_strategy(trades)

            # 整體統計
            overall = self._overall_stats(trades, positions)

            # 標記表現差的策略
            alerts = self._flag_underperformers(strategy_stats)

            return {
                "period_days": days,
                "generated_at": datetime.now().isoformat(),
                "overall": overall,
                "by_strategy": strategy_stats,
                "alerts": alerts,
            }

        finally:
            session.close()

    def _analyze_by_strategy(self, trades: list) -> list[dict]:
        """按策略分組統計"""
        groups: dict[str, list] = defaultdict(list)
        for t in trades:
            groups[t.strategy_name or "manual"].append(t)

        result = []
        for strat_name, strat_trades in groups.items():
            # 分析買賣配對
            buys = [t for t in strat_trades if t.action == "Buy"]
            sells = [t for t in strat_trades if t.action == "Sell"]

            # 計算已實現損益（僅從 Sell 交易）
            total_pnl = sum(t.realized_pnl for t in sells if t.realized_pnl is not None)
            wins = [t for t in sells if t.realized_pnl is not None and t.realized_pnl > 0]
            losses = [t for t in sells if t.realized_pnl is not None and t.realized_pnl <= 0]

            win_count = len(wins)
            loss_count = len(losses)
            total_closed = win_count + loss_count

            win_rate = win_count / total_closed if total_closed > 0 else 0
            avg_win = (sum(t.realized_pnl for t in wins) / win_count) if win_count > 0 else 0
            avg_loss = (sum(t.realized_pnl for t in losses) / loss_count) if loss_count > 0 else 0
            win_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0

            # 最大單筆虧損
            max_loss = min((t.realized_pnl for t in sells if t.realized_pnl is not None), default=0)

            result.append({
                "strategy_name": strat_name,
                "total_trades": len(strat_trades),
                "buy_count": len(buys),
                "sell_count": len(sells),
                "closed_trades": total_closed,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": round(win_rate, 4),
                "total_pnl": round(total_pnl, 2),
                "avg_win": round(avg_win, 2),
                "avg_loss": round(avg_loss, 2),
                "win_loss_ratio": round(win_loss_ratio, 2),
                "max_single_loss": round(max_loss, 2),
            })

        # 按 total_pnl 排序
        result.sort(key=lambda x: x["total_pnl"], reverse=True)
        return result

    def _overall_stats(self, trades: list, positions: list) -> dict:
        """整體統計"""
        sells = [t for t in trades if t.action == "Sell"]
        realized_pnl = sum(t.realized_pnl for t in sells if t.realized_pnl is not None)
        unrealized_pnl = sum(p.unrealized_pnl for p in positions)

        total_buy_amount = sum(
            t.price * t.quantity * 1000 for t in trades if t.action == "Buy"
        )
        total_sell_amount = sum(
            t.price * t.quantity * 1000 for t in trades if t.action == "Sell"
        )

        return {
            "total_trades": len(trades),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "total_pnl": round(realized_pnl + unrealized_pnl, 2),
            "total_buy_amount": round(total_buy_amount, 2),
            "total_sell_amount": round(total_sell_amount, 2),
            "active_positions": len(positions),
        }

    def _flag_underperformers(self, strategy_stats: list) -> list[dict]:
        """標記表現差的策略"""
        alerts = []
        for stat in strategy_stats:
            name = stat["strategy_name"]
            if name == "manual":
                continue

            # 連虧：虧損次數 >= 3 且勝率 < 30%
            if stat["closed_trades"] >= 3 and stat["win_rate"] < 0.30:
                alerts.append({
                    "strategy": name,
                    "level": "warning",
                    "message": (
                        f"策略 [{name}] 表現不佳: "
                        f"勝率 {stat['win_rate']:.0%}, "
                        f"累計虧損 {stat['total_pnl']:,.0f}元"
                    ),
                    "recommendation": "建議停用並重新回測參數",
                })

            # 持續虧損
            if stat["total_pnl"] < -5000 and stat["closed_trades"] >= 2:
                alerts.append({
                    "strategy": name,
                    "level": "danger",
                    "message": (
                        f"策略 [{name}] 累計虧損嚴重: "
                        f"{stat['total_pnl']:,.0f}元"
                    ),
                    "recommendation": "強烈建議停用此策略",
                })

        return alerts

    def format_telegram_report(self, report: dict = None) -> str:
        """
        產生 Telegram 格式的績效報告

        Args:
            report: 績效報告 dict，若未提供則重新產生

        Returns:
            Markdown 格式文字
        """
        if report is None:
            report = self.generate(days=7)

        overall = report.get("overall", {})
        strategies = report.get("by_strategy", [])
        alerts = report.get("alerts", [])

        lines = [
            "📊 *NeoStock2 週報*",
            f"統計期間: {report['period_days']} 天",
            "",
            "💰 *整體績效*",
            f"  已實現損益: {overall.get('realized_pnl', 0):+,.0f}",
            f"  未實現損益: {overall.get('unrealized_pnl', 0):+,.0f}",
            f"  總損益: {overall.get('total_pnl', 0):+,.0f}",
            f"  交易次數: {overall.get('total_trades', 0)}",
            f"  持倉檔數: {overall.get('active_positions', 0)}",
        ]

        if strategies:
            lines.extend(["", "📈 *各策略績效*"])
            for s in strategies[:5]:  # 最多 5 個
                emoji = "🟢" if s["total_pnl"] >= 0 else "🔴"
                lines.append(
                    f"  {emoji} {s['strategy_name']}: "
                    f"{s['total_pnl']:+,.0f} "
                    f"(勝率 {s['win_rate']:.0%}, 共 {s['closed_trades']} 筆)"
                )

        if alerts:
            lines.extend(["", "⚠️ *警示*"])
            for a in alerts:
                lines.append(f"  • {a['message']}")

        return "\n".join(lines)
