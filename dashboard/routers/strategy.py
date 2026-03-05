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


# ========== 預設策略套件 ==========

@router.get("/presets")
async def list_presets():
    """取得可用的策略預設套件列表"""
    from strategies.presets import get_presets
    return {"data": get_presets()}


@router.get("/presets/{preset_id}")
async def get_preset(preset_id: str):
    """取得預設套件詳情"""
    from strategies.presets import get_preset_detail
    detail = get_preset_detail(preset_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"找不到預設套件: {preset_id}")
    return {"data": detail}


class PresetApplyRequest(BaseModel):
    symbols: list[str] = []  # 可選：自訂標的（覆蓋預設）


@router.post("/presets/{preset_id}/apply")
async def apply_preset(preset_id: str, req: PresetApplyRequest = None):
    """一鍵套用預設策略套件"""
    from strategies.presets import apply_preset as _apply_preset

    engine = app_state.get("strategy_engine")
    risk_manager = app_state.get("risk_manager")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未啟動")

    symbols_override = req.symbols if req and req.symbols else None
    result = _apply_preset(
        preset_id=preset_id,
        strategy_engine=engine,
        risk_manager=risk_manager,
        symbols_override=symbols_override,
    )

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "套用失敗"))

    return {"message": f"已套用 {result.get('preset_name', preset_id)}", "data": result}


# ========== AutoGuardian 狀態 ==========

@router.get("/guardian/status")
async def guardian_status():
    """取得 AutoGuardian 狀態"""
    guardian = app_state.get("auto_guardian")
    if guardian is None:
        return {"data": {"running": False, "enabled": False}}
    return {"data": guardian.get_status()}


class GuardianToggleRequest(BaseModel):
    enabled: bool


@router.post("/guardian/toggle")
async def guardian_toggle(req: GuardianToggleRequest):
    """啟停 AutoGuardian"""
    guardian = app_state.get("auto_guardian")
    if guardian is None:
        raise HTTPException(status_code=503, detail="AutoGuardian 未初始化")

    if req.enabled:
        guardian.start()
        return {"message": "AutoGuardian 已啟動"}
    else:
        guardian.stop()
        return {"message": "AutoGuardian 已停止"}


# ========== 績效報告 ==========

@router.get("/performance")
async def get_performance(days: int = 30):
    """取得績效歸因報告"""
    perf = app_state.get("perf_report")
    if perf is None:
        raise HTTPException(status_code=503, detail="績效報告模組未初始化")
    try:
        report = perf.generate(days=days)
        return {"data": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"產生報告失敗: {e}")


# ========== 研究→實盤部署 ==========

class DeployResearchRequest(BaseModel):
    strategy_id: str        # research strategy ID (trend_ma, breakout, etc.)
    symbol: str
    params: dict = {}       # 最佳參數
    name: str = ""          # 策略名稱（可選）
    enabled: bool = True


@router.post("/deploy_research")
async def deploy_research(req: DeployResearchRequest):
    """從研究結果一鍵部署策略到實盤"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未啟動")

    # 將研究策略 ID 對應到 swing adapter
    adapter_map = {
        "trend_ma": "swing_trend_ma",
        "breakout": "swing_breakout",
        "pullback": "swing_pullback",
        "macd": "swing_macd",
    }

    strategy_type = adapter_map.get(req.strategy_id)
    if not strategy_type:
        raise HTTPException(
            status_code=400,
            detail=f"找不到對應的實盤策略: {req.strategy_id}"
        )

    name = req.name or f"研究部署_{req.strategy_id}_{req.symbol}"
    success = engine.register_strategy(
        name=name,
        strategy_type=strategy_type,
        symbols=[req.symbol],
        params=req.params,
        enabled=req.enabled,
    )

    if success:
        return {"message": f"策略 [{name}] 已部署", "strategy_name": name}
    raise HTTPException(status_code=400, detail="部署失敗")


# ========== 部位管理器 ==========

@router.get("/position_sizer")
async def get_position_sizer_info():
    """取得部位管理器設定"""
    sizer = app_state.get("position_sizer")
    if sizer is None:
        return {"data": {"method": "disabled"}}
    return {"data": sizer.get_info()}

