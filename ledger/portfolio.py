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

    def calculate_fee(self, amount: float) -> int:
        """計算手續費（整數化）"""
        fee = amount * self._fee_rate * self._fee_discount
        return round(max(fee, self._min_fee))

    def calculate_tax(self, amount: float, action: str) -> int:
        """計算證交稅（僅賣出時收，整數化）"""
        if action == "Sell":
            return round(amount * self._tax_rate)
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

        # 計算賣出已實現損益（在下方 session 內統一處理）
        realized_pnl = None

        session = self.db.get_session()
        try:
            # 賣出時計算已實現損益（純看價差，不含手續費/稅）
            if action == "Sell":
                position = session.query(Position).filter_by(code=code).first()
                if position and position.avg_cost > 0:
                    realized_pnl = (price - position.avg_cost) * shares
                else:
                    realized_pnl = net_amount

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
                realized_pnl=realized_pnl,
                strategy_name=strategy_name,
                note=note,
            )
            session.add(trade)

            # 更新持倉
            self._update_position(session, code, action, price, quantity, name, strategy_name)

            # 更新可用資金
            from ledger.models import Account
            account = session.query(Account).first()
            if account:
                account.available_cash += net_amount  # Buy=負數扣款, Sell=正數入帳

            session.commit()
            pnl_info = f", 損益={realized_pnl:+,.0f}" if realized_pnl is not None else ""
            logger.info(
                f"交易記錄: {action} {code} {quantity}張 @ {price}, "
                f"費用={fee:.0f}, 稅={tax:.0f}, 淨額={net_amount:.0f}{pnl_info}"
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
                position = Position(
                    code=code,
                    name=name,
                    quantity=quantity,
                    avg_cost=price,
                    total_cost=amount,
                    strategy_name=strategy_name,
                )
                session.add(position)
            else:
                old_total = position.avg_cost * position.quantity * 1000
                new_amount = price * quantity * 1000
                new_total = old_total + new_amount
                new_qty = position.quantity + quantity
                position.quantity = new_qty
                position.avg_cost = new_total / (new_qty * 1000) if new_qty > 0 else 0
                position.total_cost = new_total
                position.updated_at = datetime.now()

        elif action == "Sell":
            if position is None:
                logger.warning(f"賣出 {code} 但無持倉記錄")
                return
            # 防護：賣出數量不得超過持倉
            if quantity > position.quantity:
                logger.warning(
                    f"賣出數量 {quantity} 超過持倉 {position.quantity}，自動調整"
                )
                quantity = position.quantity
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
        """取得所有持倉（損益用 avg_cost 即時重算，不依賴 DB 舊值）"""
        session = self.db.get_session()
        try:
            positions = session.query(Position).all()
            result = []
            for pos in positions:
                d = pos.to_dict()
                # 用 avg_cost 重算 total_cost 和 unrealized_pnl（不含手續費）
                recalc_cost = pos.avg_cost * pos.quantity * 1000
                recalc_mv = pos.current_price * pos.quantity * 1000 if pos.current_price else 0
                d["total_cost"] = recalc_cost
                d["market_value"] = recalc_mv
                d["unrealized_pnl"] = recalc_mv - recalc_cost
                d["unrealized_pnl_pct"] = (
                    (d["unrealized_pnl"] / recalc_cost * 100) if recalc_cost > 0 else 0
                )
                result.append(d)
            return result
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

    def delete_all_trades(self):
        """清除所有交易記錄"""
        session = self.db.get_session()
        try:
            session.query(Trade).delete()
            session.commit()
            logger.info("已清除所有交易記錄")
        except Exception as e:
            session.rollback()
            logger.error(f"清除交易記錄失敗: {e}")
            raise
        finally:
            session.close()

    def get_portfolio_summary(self) -> dict:
        """
        取得帳戶總覽
        
        Returns:
            包含總資產、持倉市值、未實現/已實現損益等
        """
        # 嘗試取得即時行情以計算最新市值
        from dashboard.state import app_state
        market_data = app_state.get("market_data")
        
        session = self.db.get_session()
        try:
            positions = session.query(Position).all()
            
            total_cost = 0
            total_market_value = 0
            total_unrealized_pnl = 0
            
            for p in positions:
                # 取得即時報價
                current_price = 0
                if market_data:
                    tick = market_data.get_latest_tick(p.code)
                    if tick:
                        current_price = tick.get("close", 0)
                    else:
                        # 嘗試從 quote cache 拿 (可能有些沒訂閱 tick 但有 snapshot)
                        quotes = market_data.get_latest_quotes([p.code])
                        if quotes:
                            current_price = quotes[0].get("close", 0)
                
                # 若無即時報價，回退使用儲存的 current_price (可能是上次同步的)
                if current_price <= 0:
                    current_price = p.current_price
                
                # 計算該持倉市值與損益（用 avg_cost 重算，與 get_positions() 保持一致）
                # quantity 是張數，所以 * 1000
                mkt_val = current_price * p.quantity * 1000
                cost = p.avg_cost * p.quantity * 1000  # 用 avg_cost 重算，而非 DB 的 total_cost
                unrealized = mkt_val - cost
                
                total_cost += cost
                total_market_value += mkt_val
                total_unrealized_pnl += unrealized
                
                # 這裡不寫回 DB Position table 以免頻繁 IO，僅作為顯示計算
                # 但若有需要持久化，可考慮非同步更新

            # 已實現損益：直接從 Trade.realized_pnl 欄位彙總
            total_realized_pnl = (
                session.query(func.sum(Trade.realized_pnl))
                .filter(Trade.realized_pnl.isnot(None))
                .scalar() or 0
            )

            total_fee = (
                session.query(func.sum(Trade.fee)).scalar() or 0
            )
            total_tax = (
                session.query(func.sum(Trade.tax)).scalar() or 0
            )

            unrealized_pnl_pct = (
                round(total_unrealized_pnl / total_cost * 100, 2)
                if total_cost > 0 else 0
            )

            return {
                "position_count": len(positions),
                "total_cost": round(total_cost, 2),
                "total_market_value": round(total_market_value, 2),
                "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "total_realized_pnl": round(total_realized_pnl, 2),
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
                existing.realized_pnl = summary["total_realized_pnl"]
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
                realized_pnl=summary["total_realized_pnl"],
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
        從券商端同步持倉至本地（券商 = 唯一權威源）

        - Position 表直接覆蓋為券商數據
        - Trade 表僅在該股票「完全無記錄」時才補寫 broker_sync
        - 券商不存在的本地持倉 → 刪除
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
                name = bp.get("name", "")
                if not code or qty <= 0:
                    continue

                broker_codes.add(code)

                # 券商數據
                last_price = bp.get("last_price", price)
                pnl = bp.get("pnl", 0)
                pnl_pct = bp.get("pnl_pct", 0)
                market_value = bp.get("market_value", 0)
                # total_cost 純成交金額，不含手續費
                total_cost = price * qty * 1000

                # --- 1. 覆蓋 Position 表 ---
                if code in local_map:
                    pos = local_map[code]
                    if name:
                        pos.name = name
                    pos.quantity = qty
                    pos.avg_cost = price
                    pos.total_cost = total_cost
                    pos.current_price = last_price
                    pos.market_value = market_value or (last_price * qty * 1000)
                    pos.unrealized_pnl = pos.market_value - total_cost
                    pos.unrealized_pnl_pct = (
                        (pos.unrealized_pnl / total_cost * 100) if total_cost > 0 else 0
                    )
                    pos.updated_at = datetime.now()
                else:
                    actual_mv = market_value or (last_price * qty * 1000)
                    calc_pnl = actual_mv - total_cost
                    calc_pnl_pct = (calc_pnl / total_cost * 100) if total_cost > 0 else 0
                    pos = Position(
                        code=code,
                        name=name,
                        quantity=qty,
                        avg_cost=price,
                        total_cost=total_cost,
                        current_price=last_price,
                        market_value=actual_mv,
                        unrealized_pnl=calc_pnl,
                        unrealized_pnl_pct=calc_pnl_pct,
                        strategy_name="broker_sync",
                    )
                    session.add(pos)

                # --- 2. 僅首次（Trade 表無此股票記錄）才補寫 ---
                has_trades = session.query(Trade).filter_by(code=code).count() > 0
                if not has_trades:
                    amount = price * qty * 1000
                    fee = self.calculate_fee(amount)
                    trade = Trade(
                        order_id="",
                        code=code,
                        name=name,
                        action="Buy",
                        price=price,
                        quantity=qty,
                        amount=amount,
                        fee=fee,
                        tax=0,
                        net_amount=-(amount + fee),
                        realized_pnl=None,
                        strategy_name="broker_sync",
                        status="filled",
                        note="券商庫存同步",
                    )
                    session.add(trade)
                    logger.info(f"📥 首次同步: Buy {code} {name} {qty}張 @ {price}")

            # --- 3. 刪除券商不存在的本地持倉 ---
            for code, pos in local_map.items():
                if code not in broker_codes:
                    session.delete(pos)
                    logger.info(f"同步刪除本地持倉: {code}")

            # --- 4. 自動將持倉股票加入 Watchlist ---
            from ledger.models import Watchlist
            existing_watchlist = {w.symbol for w in session.query(Watchlist).all()}
            added_count = 0
            for code in broker_codes:
                if code not in existing_watchlist:
                    # 取得名稱（從 broker_positions 或已存在的 Position）
                    bp = next((b for b in broker_positions if b.get("code") == code), {})
                    name = bp.get("name", "")
                    session.add(Watchlist(symbol=code, name=name))
                    added_count += 1
            if added_count > 0:
                logger.info(f"📋 自動加入 {added_count} 檔持倉到自選股清單")

            session.commit()
            logger.info(f"券商同步完成: {len(broker_codes)} 筆持倉")
        except Exception as e:
            session.rollback()
            logger.error(f"同步持倉失敗: {e}")
            raise
        finally:
            session.close()

