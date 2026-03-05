"""
NeoStock2 儀表板路由 — 系統設定 API
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dashboard.state import app_state
from ledger.models import Account
from datetime import datetime

router = APIRouter()


class AccountSettings(BaseModel):
    initial_capital: float
    available_cash: float


@router.get("/account")
async def get_account_settings():
    """取得帳戶資金設定"""
    db = app_state.get("db")
    if not db:
        raise HTTPException(status_code=503, detail="資料庫未連接")

    session = db.get_session()
    try:
        account = session.query(Account).first()
        if not account:
            # 若無記錄，建立預設值
            account = Account(initial_capital=1000000, available_cash=1000000)
            session.add(account)
            session.commit()
            session.refresh(account)
        
        return {"data": account.to_dict()}
    finally:
        session.close()


@router.post("/account")
async def update_account_settings(settings: AccountSettings):
    """更新帳戶資金設定"""
    db = app_state.get("db")
    if not db:
        raise HTTPException(status_code=503, detail="資料庫未連接")

    session = db.get_session()
    try:
        account = session.query(Account).first()
        if not account:
            account = Account()
            session.add(account)
        
        account.initial_capital = settings.initial_capital
        account.available_cash = settings.available_cash
        session.commit()
        
        return {"message": "帳戶設定已更新", "data": account.to_dict()}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


# ========== 系統參數設定 ==========
import yaml
import os

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "settings.yaml")


class SystemSettings(BaseModel):
    trading_costs: dict = {}
    risk_management: dict = {}
    strategy: dict = {}


@router.get("/system")
async def get_system_settings():
    """取得系統設定（settings.yaml）"""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return {
            "data": {
                "trading_costs": cfg.get("trading_costs", {}),
                "risk_management": cfg.get("risk_management", {}),
                "strategy": cfg.get("strategy", {}),
                "dashboard": cfg.get("dashboard", {}),
                "logging": cfg.get("logging", {}),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"讀取設定失敗: {e}")


@router.post("/system")
async def update_system_settings(settings: SystemSettings):
    """更新系統設定（寫入 settings.yaml）"""
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        # 只更新允許的區塊
        if settings.trading_costs:
            cfg["trading_costs"] = settings.trading_costs
        if settings.risk_management:
            cfg["risk_management"] = settings.risk_management
        if settings.strategy:
            cfg["strategy"] = settings.strategy

        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        # 同步更新記憶體中的設定 dict
        # TODO: 各模組（RiskManager, Portfolio 等）在 __init__ 時已將設定讀入成員變數，
        #       修改此處 dict 不會即時反映到這些模組。若需熱更新，需通知各模組重讀設定。
        current = app_state.get("settings")
        if current and isinstance(current, dict):
            current.update(cfg)

        return {"message": "系統設定已更新", "data": {
            "trading_costs": cfg.get("trading_costs", {}),
            "risk_management": cfg.get("risk_management", {}),
            "strategy": cfg.get("strategy", {}),
        }}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"儲存設定失敗: {e}")


# ========== 帳戶同步 ==========

@router.post("/sync")
async def sync_account():
    """從券商同步帳戶餘額與持倉"""
    client = app_state.get("api_client")
    db = app_state.get("db")
    portfolio = app_state.get("portfolio")

    if not client or not client.is_logged_in:
        raise HTTPException(status_code=503, detail="尚未登入券商，無法同步")

    results = {"balance": None, "positions": None, "errors": []}

    # 1. 同步餘額
    try:
        balance = client.get_account_balance()
        if "error" not in balance:
            available = balance.get("available_balance", 0)
            results["balance"] = balance

            # 更新 DB
            if db:
                session = db.get_session()
                try:
                    account = session.query(Account).first()
                    if not account:
                        account = Account()
                        session.add(account)
                    account.available_cash = available
                    session.commit()
                except Exception as e:
                    session.rollback()
                    results["errors"].append(f"更新餘額失敗: {e}")
                finally:
                    session.close()
        else:
            results["errors"].append(balance["error"])
    except Exception as e:
        results["errors"].append(f"查詢餘額失敗: {e}")

    # 2. 同步持倉
    try:
        broker_positions = client.get_positions()
        if broker_positions is None:
            results["errors"].append("查詢持倉失敗（連線不穩），本次跳過同步以保護現有資料")
        else:
            results["positions"] = broker_positions
            if portfolio:
                portfolio.sync_from_broker(broker_positions)
    except Exception as e:
        results["errors"].append(f"同步持倉失敗: {e}")

    msg = "同步完成" if not results["errors"] else f"同步完成（有 {len(results['errors'])} 個警告）"
    return {"message": msg, "data": results}


@router.post("/clear_trades")
async def clear_trades():
    """清除所有交易記錄"""
    portfolio = app_state.get("portfolio")
    if not portfolio:
        raise HTTPException(status_code=503, detail="服務未就緒")
    
    try:
        portfolio.delete_all_trades()
        return {"message": "交易記錄已清除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
