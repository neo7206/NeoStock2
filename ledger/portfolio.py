"""
NeoStock2 帳本 — 持倉與帳本管理

負責：
- 交易記錄寫入
- 持倉狀態更新
- 每日快照
"""

import logging
from datetime import datetime, date

from sqlalchemy import func

from ledger.database import Database
from ledger.models import Trade, Position, DailySnapshot

logger = logging.getLogger("neostock2.ledger.portfolio")


class Portfolio:
    """投資組合/帳本管理"""

    def __init__(self, db: Database, settings: dict = None):
        self.db = db
        self._settings = settings or {}
        self._cost_cfg = self._settings.get("trading_costs", {})
        self._fee_rate = self._cost_cfg.get("stock_fee_rate", 0.001425)
        self._fee_discount = self._cost_cfg.get("stock_fee_discount", 0.6)
        self._tax_rate = self._cost_cfg.get("stock_tax_rate", 0.003)
        self._min_fee = self._cost_cfg.get("min_fee", 20)

    def calculate_fee(self, amount: float) -> float:
        """計算手續費"""
        fee = amount * self._fee_rate * self._fee_discount
        return max(fee, self._min_fee)

    def calculate_tax(self, amount: float, action: str) -> float:
        """計算證交稅（僅賣出時收）"""
        if action == "Sell":
            return amount * self._tax_rate
        return 0

    def record_trade(
        self,
        code: str,
        action: str,
        price: float,
        quantity: int,
        strategy_name: str = "manual",
        order_id: str = "",
        name: str = "",
        note: str = "",
    ) -> Trade:
        """
        記錄一筆交易並更新持倉

        Args:
            code: 股票代碼
            action: 'Buy' / 'Sell'
            price: 成交價
            quantity: 數量（張）
            strategy_name: 策略名稱
            order_id: 委託 ID
            name: 股票名稱
            note: 備註

        Returns:
            Trade 物件
        """
        # 計算成交金額（1張=1000股）
        shares = quantity * 1000
        amount = price * shares
        fee = self.calculate_fee(amount)
        tax = self.calculate_tax(amount, action)

        if action == "Buy":
            net_amount = -(amount + fee)  # 買入 = 花錢（負數）
        else:
            net_amount = amount - fee - tax  # 賣出 = 收錢（正數）

        session = self.db.get_session()
        try:
            trade = Trade(
                order_id=order_id,
                code=code,
                name=name,
                action=action,
                price=price,
                quantity=quantity,
                amount=amount,
                fee=fee,
                tax=tax,
                net_amount=net_amount,
                strategy_name=strategy_name,
                note=note,
            )
            session.add(trade)

            # 更新持倉
            self._update_position(session, code, action, price, quantity, name, strategy_name)

            session.commit()
            logger.info(
                f"交易記錄: {action} {code} {quantity}張 @ {price}, "
                f"費用={fee:.0f}, 稅={tax:.0f}, 淨額={net_amount:.0f}"
            )
            return trade
        except Exception as e:
            session.rollback()
            logger.error(f"記錄交易失敗: {e}")
            raise
        finally:
            session.close()

    def _update_position(
        self,
        session,
        code: str,
        action: str,
        price: float,
        quantity: int,
        name: str = "",
        strategy_name: str = "manual",
    ):
        """更新持倉（在同一個 session 中）"""
        position = session.query(Position).filter_by(code=code).first()

        if action == "Buy":
            if position is None:
                amount = price * quantity * 1000
                fee = self.calculate_fee(amount)
                position = Position(
                    code=code,
                    name=name,
                    quantity=quantity,
                    avg_cost=price + fee / (quantity * 1000),
                    total_cost=amount + fee,
                    strategy_name=strategy_name,
                )
                session.add(position)
            else:
                old_total = position.avg_cost * position.quantity * 1000
                new_amount = price * quantity * 1000
                fee = self.calculate_fee(new_amount)
                new_total = old_total + new_amount + fee
                new_qty = position.quantity + quantity
                position.quantity = new_qty
                position.avg_cost = new_total / (new_qty * 1000) if new_qty > 0 else 0
                position.total_cost = new_total
                position.updated_at = datetime.now()

        elif action == "Sell":
            if position is None:
                logger.warning(f"賣出 {code} 但無持倉記錄")
                return
            position.quantity -= quantity
            if position.quantity <= 0:
                session.delete(position)
            else:
                position.total_cost = position.avg_cost * position.quantity * 1000
                position.updated_at = datetime.now()

    def update_market_prices(self, price_map: dict[str, float]):
        """
        更新持倉的即時市價

        Args:
            price_map: {股票代碼: 最新價格}
        """
        session = self.db.get_session()
        try:
            positions = session.query(Position).all()
            for pos in positions:
                if pos.code in price_map:
                    price = price_map[pos.code]
                    pos.current_price = price
                    pos.market_value = price * pos.quantity * 1000
                    pos.unrealized_pnl = pos.market_value - pos.total_cost
                    pos.unrealized_pnl_pct = (
                        (pos.unrealized_pnl / pos.total_cost * 100)
                        if pos.total_cost > 0
                        else 0
                    )
                    pos.updated_at = datetime.now()
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"更新市價失敗: {e}")
        finally:
            session.close()

    def get_positions(self) -> list[dict]:
        """取得所有持倉"""
        session = self.db.get_session()
        try:
            positions = session.query(Position).all()
            return [pos.to_dict() for pos in positions]
        finally:
            session.close()

    def get_trades(self, limit: int = 50, code: str = None) -> list[dict]:
        """取得交易記錄"""
        session = self.db.get_session()
        try:
            query = session.query(Trade).order_by(Trade.created_at.desc())
            if code:
                query = query.filter_by(code=code)
            trades = query.limit(limit).all()
            return [t.to_dict() for t in trades]
        finally:
            session.close()

    def get_portfolio_summary(self) -> dict:
        """
        取得帳戶總覽

        Returns:
            包含總資產、持倉市值、未實現/已實現損益等
        """
        session = self.db.get_session()
        try:
            positions = session.query(Position).all()

            total_cost = sum(p.total_cost for p in positions)
            total_market_value = sum(p.market_value for p in positions)
            total_unrealized_pnl = sum(p.unrealized_pnl for p in positions)

            # 計算已實現損益（所有賣出交易的淨額 + 對應買入的成本）
            realized_pnl = (
                session.query(func.sum(Trade.net_amount))
                .filter(Trade.action == "Sell")
                .scalar()
                or 0
            )
            buy_total = (
                session.query(func.sum(Trade.net_amount))
                .filter(Trade.action == "Buy")
                .scalar()
                or 0
            )

            total_fee = (
                session.query(func.sum(Trade.fee)).scalar() or 0
            )
            total_tax = (
                session.query(func.sum(Trade.tax)).scalar() or 0
            )

            return {
                "position_count": len(positions),
                "total_cost": round(total_cost, 2),
                "total_market_value": round(total_market_value, 2),
                "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                "total_unrealized_pnl_pct": (
                    round(total_unrealized_pnl / total_cost * 100, 2)
                    if total_cost > 0
                    else 0
                ),
                "realized_pnl": round(realized_pnl + buy_total, 2),
                "total_fee": round(total_fee, 2),
                "total_tax": round(total_tax, 2),
                "total_costs": round(total_fee + total_tax, 2),
            }
        finally:
            session.close()

    def take_daily_snapshot(self, cash: float = 0) -> DailySnapshot:
        """建立每日帳戶快照"""
        today = date.today().isoformat()
        summary = self.get_portfolio_summary()

        session = self.db.get_session()
        try:
            existing = session.query(DailySnapshot).filter_by(date=today).first()
            if existing:
                existing.total_asset = cash + summary["total_market_value"]
                existing.cash = cash
                existing.market_value = summary["total_market_value"]
                existing.realized_pnl = summary["realized_pnl"]
                existing.unrealized_pnl = summary["total_unrealized_pnl"]
                existing.total_fee = summary["total_fee"]
                existing.total_tax = summary["total_tax"]
                session.commit()
                return existing

            snapshot = DailySnapshot(
                date=today,
                total_asset=cash + summary["total_market_value"],
                cash=cash,
                market_value=summary["total_market_value"],
                realized_pnl=summary["realized_pnl"],
                unrealized_pnl=summary["total_unrealized_pnl"],
                total_fee=summary["total_fee"],
                total_tax=summary["total_tax"],
            )
            session.add(snapshot)
            session.commit()
            logger.info(f"每日快照已建立: {today}")
            return snapshot
        except Exception as e:
            session.rollback()
            logger.error(f"建立快照失敗: {e}")
            raise
        finally:
            session.close()

    def get_snapshots(self, limit: int = 30) -> list[dict]:
        """取得歷史快照（供淨值曲線用）"""
        session = self.db.get_session()
        try:
            snaps = (
                session.query(DailySnapshot)
                .order_by(DailySnapshot.date.desc())
                .limit(limit)
                .all()
            )
            return [s.to_dict() for s in reversed(snaps)]
        finally:
            session.close()

    def sync_from_broker(self, broker_positions: list[dict]):
        """
        從券商端同步持倉至本地

        以券商端為權威來源：
        - 券商有、本地無 → 新增
        - 券商有、本地有 → 更新數量
        - 券商無、本地有 → 刪除
        """
        session = self.db.get_session()
        try:
            local_positions = session.query(Position).all()
            local_map = {p.code: p for p in local_positions}

            broker_codes = set()
            for bp in broker_positions:
                code = bp.get("code", "")
                qty = bp.get("quantity", 0)
                price = bp.get("price", 0)
                if not code or qty <= 0:
                    continue

                broker_codes.add(code)

                if code in local_map:
                    # 更新
                    pos = local_map[code]
                    pos.quantity = qty
                    pos.avg_cost = price
                    pos.total_cost = price * qty * 1000
                    pos.updated_at = datetime.now()
                else:
                    # 新增
                    pos = Position(
                        code=code,
                        name="",
                        quantity=qty,
                        avg_cost=price,
                        total_cost=price * qty * 1000,
                        strategy_name="broker_sync",
                    )
                    session.add(pos)

            # 刪除券商不存在的本地持倉
            for code, pos in local_map.items():
                if code not in broker_codes:
                    session.delete(pos)
                    logger.info(f"同步刪除本地持倉: {code}")

            session.commit()
            logger.info(f"券商同步完成: {len(broker_codes)} 筆持倉")
        except Exception as e:
            session.rollback()
            logger.error(f"同步持倉失敗: {e}")
            raise
        finally:
            session.close()
