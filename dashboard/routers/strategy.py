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
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    max_position: int = 5


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


@router.put("/{name}")
async def update_strategy(name: str, req: StrategyCreateRequest):
    """更新現有策略（刪除後重建，保留啟用狀態）"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未啟動")

    # 取得原策略啟用狀態
    old_enabled = False
    with engine._lock:
        if name in engine._enabled:
            old_enabled = engine._enabled[name]
        elif name not in engine._strategies:
            raise HTTPException(status_code=404, detail=f"找不到策略: {name}")

    # 移除舊策略
    engine.remove_strategy(name)

    # 重新註冊（使用新名稱，可能改名）
    new_name = req.name or name
    success = engine.register_strategy(
        name=new_name,
        strategy_type=req.strategy_type,
        symbols=req.symbols,
        params=req.params,
        enabled=old_enabled,
    )

    if success:
        return {"message": f"策略 [{new_name}] 更新成功"}
    raise HTTPException(status_code=400, detail="更新策略失敗")


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
    """執行策略回測 (使用 BacktestEngine)"""
    from strategies.backtest_engine import BacktestEngine
    from strategies.strategy_engine import STRATEGY_REGISTRY

    history_manager = app_state.get("history_manager")
    if history_manager is None:
        raise HTTPException(status_code=503, detail="History Manager 未啟動")

    # 1. 取得策略類別
    strategy_cls = STRATEGY_REGISTRY.get(req.strategy_type)
    if strategy_cls is None:
        raise HTTPException(
            status_code=400,
            detail=f"找不到策略類型: {req.strategy_type}",
        )

    # 2. 執行回測
    engine = BacktestEngine(history_manager)
    result = engine.run_backtest(
        strategy_cls=strategy_cls,
        params=req.params,
        symbol=req.symbol,
        start_date=req.start,
        end_date=req.end,
        initial_capital=req.cash,
        timeframe="1min",
        stop_loss_pct=req.stop_loss_pct,
        take_profit_pct=req.take_profit_pct,
        max_position=req.max_position
    )

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return {"data": result}
