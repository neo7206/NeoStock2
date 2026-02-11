"""
NeoStock2 帳本 — ORM 資料模型

定義 SQLAlchemy 資料表：
- Trade: 交易記錄
- Position: 持倉狀態
- DailySnapshot: 每日帳戶快照
- StrategyConfig: 策略設定
"""

from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    DateTime,
    Boolean,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Trade(Base):
    """交易記錄"""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    order_id = Column(String(64), index=True)
    code = Column(String(10), nullable=False, index=True)  # 股票代碼
    name = Column(String(50), default="")  # 股票名稱
    action = Column(String(10), nullable=False)  # Buy / Sell
    price = Column(Float, nullable=False)  # 成交價
    quantity = Column(Integer, nullable=False)  # 成交數量（張）
    amount = Column(Float, default=0)  # 成交金額
    fee = Column(Float, default=0)  # 手續費
    tax = Column(Float, default=0)  # 證交稅
    net_amount = Column(Float, default=0)  # 淨金額 (含費用)
    strategy_name = Column(String(100), default="manual")  # 策略名稱
    status = Column(String(20), default="filled")
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "code": self.code,
            "name": self.name,
            "action": self.action,
            "price": self.price,
            "quantity": self.quantity,
            "amount": self.amount,
            "fee": self.fee,
            "tax": self.tax,
            "net_amount": self.net_amount,
            "strategy_name": self.strategy_name,
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Position(Base):
    """持倉狀態"""

    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), nullable=False, unique=True, index=True)
    name = Column(String(50), default="")
    quantity = Column(Integer, default=0)  # 持有數量（張）
    avg_cost = Column(Float, default=0)  # 平均成本
    total_cost = Column(Float, default=0)  # 總成本
    current_price = Column(Float, default=0)  # 當前市價
    market_value = Column(Float, default=0)  # 市值
    unrealized_pnl = Column(Float, default=0)  # 未實現損益
    unrealized_pnl_pct = Column(Float, default=0)  # 未實現損益率
    strategy_name = Column(String(100), default="manual")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "quantity": self.quantity,
            "avg_cost": self.avg_cost,
            "total_cost": self.total_cost,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "strategy_name": self.strategy_name,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DailySnapshot(Base):
    """每日帳戶快照"""

    __tablename__ = "daily_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(String(10), nullable=False, unique=True, index=True)  # YYYY-MM-DD
    total_asset = Column(Float, default=0)  # 總資產
    cash = Column(Float, default=0)  # 現金
    market_value = Column(Float, default=0)  # 持倉市值
    realized_pnl = Column(Float, default=0)  # 當日已實現損益
    unrealized_pnl = Column(Float, default=0)  # 未實現損益
    total_fee = Column(Float, default=0)  # 累計手續費
    total_tax = Column(Float, default=0)  # 累計證交稅
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.date,
            "total_asset": self.total_asset,
            "cash": self.cash,
            "market_value": self.market_value,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": self.unrealized_pnl,
            "total_fee": self.total_fee,
            "total_tax": self.total_tax,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class StrategyConfig(Base):
    """策略設定"""

    __tablename__ = "strategy_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    strategy_type = Column(String(50), nullable=False)  # sma_crossover, rsi, etc.
    enabled = Column(Boolean, default=False)
    symbols = Column(Text, default="[]")  # JSON: 監控的標的列表
    params = Column(Text, default="{}")  # JSON: 策略參數
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "name": self.name,
            "strategy_type": self.strategy_type,
            "enabled": self.enabled,
            "symbols": json.loads(self.symbols) if self.symbols else [],
            "params": json.loads(self.params) if self.params else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Account(Base):
    """帳戶資金設定"""

    __tablename__ = "account"

    id = Column(Integer, primary_key=True)
    initial_capital = Column(Float, default=1000000)
    available_cash = Column(Float, default=1000000)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    def to_dict(self) -> dict:
        return {
            "initial_capital": self.initial_capital,
            "available_cash": self.available_cash,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Watchlist(Base):
    """自選股清單"""

    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, unique=True, index=True)
    name = Column(String(100), default="")
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "name": self.name,
            "sort_order": self.sort_order,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
