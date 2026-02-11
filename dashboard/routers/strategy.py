"""
NeoStock2 儀表板路由 — 策略 API
"""

import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dashboard.state import app_state
from strategies.strategy_engine import StrategyEngine

router = APIRouter()


class StrategyCreateRequest(BaseModel):
    name: str
    strategy_type: str
    symbols: list[str]
    params: dict = {}
    enabled: bool = False


class BacktestRequest(BaseModel):
    symbol: str
    start: str  # YYYY-MM-DD
    end: str  # YYYY-MM-DD
    strategy_type: str = "bt_sma_cross"
    params: dict = {}
    cash: float = 1_000_000


@router.get("/list")
async def list_strategies():
    """取得所有策略實例"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        return {"data": []}
    return {"data": engine.get_strategies_info()}


@router.get("/available")
async def available_strategies():
    """取得可用的策略類型"""
    return {"data": StrategyEngine.get_available_strategies()}


@router.post("/create")
async def create_strategy(req: StrategyCreateRequest):
    """建立新策略"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未啟動")

    success = engine.register_strategy(
        name=req.name,
        strategy_type=req.strategy_type,
        symbols=req.symbols,
        params=req.params,
        enabled=req.enabled,
    )

    if success:
        return {"message": f"策略 [{req.name}] 建立成功"}
    raise HTTPException(status_code=400, detail="建立策略失敗")


@router.post("/{name}/toggle")
async def toggle_strategy(name: str, enabled: bool = True):
    """啟停策略"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未啟動")

    if enabled:
        success = engine.enable_strategy(name)
    else:
        success = engine.disable_strategy(name)

    if success:
        state = "啟用" if enabled else "停用"
        return {"message": f"策略 [{name}] 已{state}"}
    raise HTTPException(status_code=404, detail=f"找不到策略: {name}")


@router.delete("/{name}")
async def delete_strategy(name: str):
    """刪除策略"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未啟動")

    if engine.remove_strategy(name):
        return {"message": f"策略 [{name}] 已刪除"}
    raise HTTPException(status_code=404, detail=f"找不到策略: {name}")


@router.get("/signals")
async def get_signals():
    """取得所有策略訊號歷史"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        return {"data": []}
    return {"data": engine.get_all_signals()}


@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    """執行策略回測"""
    from strategies.backtrader_bridge import run_backtest as bt_run, BACKTRADER_AVAILABLE

    if not BACKTRADER_AVAILABLE:
        raise HTTPException(status_code=503, detail="Backtrader 未安裝")

    md = app_state.get("market_data")
    if md is None:
        raise HTTPException(status_code=503, detail="行情服務未啟動")

    # 取得歷史數據
    df = md.get_kbars(req.symbol, start=req.start, end=req.end)
    if df.empty:
        raise HTTPException(status_code=400, detail="無歷史數據可回測")

    from strategies.backtrader_bridge import BTSmaCross
    strategy_map = {
        "bt_sma_cross": BTSmaCross,
    }

    strategy_cls = strategy_map.get(req.strategy_type)
    if strategy_cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"不支援的回測策略: {req.strategy_type}",
        )

    result = bt_run(
        strategy_cls=strategy_cls,
        data_df=df,
        cash=req.cash,
        strategy_params=req.params or None,
    )
    return {"data": result}
