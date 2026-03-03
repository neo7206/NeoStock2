"""
NeoStock2 儀表板路由 — 帳本 API
"""

from fastapi import APIRouter, Query
from dashboard.state import app_state

router = APIRouter()


@router.get("/positions")
async def get_positions():
    """取得持倉"""
    portfolio = app_state.get("portfolio")
    if portfolio is None:
        return {"data": []}
    return {"data": portfolio.get_positions()}


@router.get("/trades")
async def get_trades(
    limit: int = Query(50, ge=1, le=500),
    code: str = Query(None),
):
    """取得交易記錄"""
    portfolio = app_state.get("portfolio")
    if portfolio is None:
        return {"data": []}
    return {"data": portfolio.get_trades(limit=limit, code=code)}


from ledger.models import Account

@router.get("/summary")
async def get_summary():
    """取得帳戶總覽"""
    portfolio = app_state.get("portfolio")
    db = app_state.get("db")
    if portfolio is None or db is None:
        return {"data": {}}
        
    summary = portfolio.get_portfolio_summary()
    
    # 讀取資金設定
    session = db.get_session()
    try:
        account = session.query(Account).first()
        initial_capital = account.initial_capital if account else 1000000
        available_cash = account.available_cash if account else 1000000
    finally:
        session.close()

    # 計算總資產 (現金 + 持倉市值)
    market_value = summary.get("total_market_value", 0)
    total_asset = available_cash + market_value
    
    # 更新 summary
    summary["initial_capital"] = initial_capital
    summary["available_cash"] = available_cash
    summary["total_asset"] = total_asset
    
    # 重新計算總損益 (總資產 - 本金)
    total_pnl = total_asset - initial_capital
    total_pnl_pct = (total_pnl / initial_capital * 100) if initial_capital > 0 else 0
    
    summary["total_pnl"] = round(total_pnl, 2)
    summary["total_pnl_pct"] = round(total_pnl_pct, 2)

    return {"data": summary}


@router.get("/roi")
async def get_roi(initial_capital: float = Query(None)):
    """取得投報率報告"""
    calc = app_state.get("roi_calculator")
    db = app_state.get("db")
    if calc is None or db is None:
        return {"data": {}}
        
    # 若未指定本金，從 DB 讀取
    if initial_capital is None:
        session = db.get_session()
        try:
            account = session.query(Account).first()
            initial_capital = account.initial_capital if account else 1000000
        finally:
            session.close()
            
    return {"data": calc.get_full_report(initial_capital)}


@router.get("/equity-curve")
async def get_equity_curve():
    """取得淨值曲線"""
    calc = app_state.get("roi_calculator")
    if calc is None:
        return {"data": []}
    return {"data": calc.get_equity_curve()}


@router.get("/risk")
async def get_risk_summary():
    """取得風險摘要"""
    risk_manager = app_state.get("risk_manager")
    if risk_manager is None:
        return {"data": {}}

    # 取得所有策略的參數，供風控檢查使用
    strategy_map = {}
    engine = app_state.get("strategy_engine")
    if engine:
        strategies = engine.get_strategies_info()
        for s in strategies:
            strategy_map[s["name"]] = s.get("params", {})

    return {"data": risk_manager.get_risk_summary(strategy_map)}


@router.get("/snapshots")
async def get_snapshots(limit: int = Query(30, ge=1, le=365)):
    """取得歷史快照"""
    portfolio = app_state.get("portfolio")
    if portfolio is None:
        return {"data": []}
    return {"data": portfolio.get_snapshots(limit=limit)}
