"""
NeoStock2 Dashboard — Autopilot 一鍵自動交易

提供：
- 啟動預設策略組合（穩健型 / 積極型）
- 停止所有自動策略
- 取得自動交易狀態
"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from dashboard.app import app_state

logger = logging.getLogger("neostock2.dashboard.autopilot")
router = APIRouter(prefix="/autopilot", tags=["autopilot"])

# 預設策略組合
PRESETS = {
    "conservative": {
        "name": "穩健型",
        "description": "追蹤大盤 ETF + 龍頭股，使用均線趨勢策略，低頻交易",
        "strategies": [
            {
                "name": "穩健_0050均線",
                "strategy_type": "swing_trend_ma",
                "symbols": ["0050"],
                "params": {"fast_period": 10, "slow_period": 30, "quantity": 1, "lot_size": 1},
            },
            {
                "name": "穩健_2330均線",
                "strategy_type": "swing_trend_ma",
                "symbols": ["2330"],
                "params": {"fast_period": 10, "slow_period": 30, "quantity": 1, "lot_size": 1},
            },
        ],
    },
    "aggressive": {
        "name": "積極型",
        "description": "多策略組合，MACD + 通道突破，適合追求高報酬",
        "strategies": [
            {
                "name": "積極_0050突破",
                "strategy_type": "swing_breakout",
                "symbols": ["0050"],
                "params": {"entry_period": 20, "exit_period": 10, "quantity": 1, "lot_size": 1},
            },
            {
                "name": "積極_2330MACD",
                "strategy_type": "swing_macd",
                "symbols": ["2330"],
                "params": {"fast": 12, "slow": 26, "signal": 9, "quantity": 1, "lot_size": 1},
            },
            {
                "name": "積極_2454回檔",
                "strategy_type": "swing_pullback",
                "symbols": ["2454"],
                "params": {"ma_period": 60, "rsi_period": 14, "rsi_buy": 35, "rsi_sell": 70, "quantity": 1, "lot_size": 1},
            },
        ],
    },
}


class AutopilotStartRequest(BaseModel):
    preset: str = "conservative"  # conservative / aggressive
    custom_symbols: Optional[list[str]] = None  # 使用者自選股覆蓋


@router.get("/presets")
async def get_presets():
    """取得可用的預設組合"""
    result = {}
    for key, preset in PRESETS.items():
        result[key] = {
            "name": preset["name"],
            "description": preset["description"],
            "strategy_count": len(preset["strategies"]),
            "symbols": list(set(
                sym for s in preset["strategies"] for sym in s["symbols"]
            )),
        }
    return {"data": result}


@router.post("/start")
async def start_autopilot(req: AutopilotStartRequest):
    """啟動一鍵自動交易"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未初始化")

    preset = PRESETS.get(req.preset)
    if not preset:
        raise HTTPException(status_code=400, detail=f"未知預設: {req.preset}")

    deployed = []
    for strat_cfg in preset["strategies"]:
        name = strat_cfg["name"]
        symbols = strat_cfg["symbols"]

        # 若使用者提供自選股，覆蓋預設
        if req.custom_symbols:
            symbols = req.custom_symbols

        # 先移除同名策略（避免重複）
        try:
            engine.remove_strategy(name)
        except Exception:
            pass

        success = engine.register_strategy(
            name=name,
            strategy_type=strat_cfg["strategy_type"],
            symbols=symbols,
            params=strat_cfg["params"],
            enabled=True,  # 直接啟用
        )
        if success:
            deployed.append(name)

    if not deployed:
        raise HTTPException(status_code=500, detail="部署失敗，沒有策略被成功建立")

    # 立即執行一次盤前掃描
    market_data = app_state.get("market_data")
    if market_data:
        try:
            engine.run_daily_scan(market_data=market_data)
        except Exception as e:
            logger.warning(f"Autopilot 初始掃描失敗: {e}")

    logger.info(f"🚀 Autopilot 已啟動: {preset['name']} ({len(deployed)} 個策略)")

    return {
        "message": f"已啟動 {preset['name']} 自動交易",
        "data": {
            "preset": req.preset,
            "preset_name": preset["name"],
            "deployed_strategies": deployed,
            "strategy_count": len(deployed),
        },
    }


@router.post("/stop")
async def stop_autopilot():
    """停止所有自動策略"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        raise HTTPException(status_code=503, detail="策略引擎未初始化")

    # 停用並移除所有 autopilot 策略
    stopped = []
    all_presets_names = set()
    for preset in PRESETS.values():
        for s in preset["strategies"]:
            all_presets_names.add(s["name"])

    for name in list(all_presets_names):
        try:
            engine.disable_strategy(name)
            engine.remove_strategy(name)
            stopped.append(name)
        except Exception:
            pass

    logger.info(f"⏹️ Autopilot 已停止: {len(stopped)} 個策略")

    return {
        "message": f"已停止 {len(stopped)} 個自動策略",
        "data": {"stopped_strategies": stopped},
    }


@router.get("/status")
async def get_autopilot_status():
    """取得自動交易狀態"""
    engine = app_state.get("strategy_engine")
    if engine is None:
        return {"data": {"active": False, "strategies": []}}

    # 檢查 autopilot 策略是否在運行中
    all_presets_names = set()
    for preset in PRESETS.values():
        for s in preset["strategies"]:
            all_presets_names.add(s["name"])

    infos = engine.get_strategies_info()
    active_autopilot = [
        info for info in infos
        if info.get("name") in all_presets_names and info.get("enabled")
    ]

    # 判斷目前使用的預設
    current_preset = None
    for key, preset in PRESETS.items():
        preset_names = {s["name"] for s in preset["strategies"]}
        active_names = {s["name"] for s in active_autopilot}
        if preset_names & active_names:  # 有交集
            current_preset = key
            break

    return {
        "data": {
            "active": len(active_autopilot) > 0,
            "current_preset": current_preset,
            "strategy_count": len(active_autopilot),
            "strategies": [
                {
                    "name": s.get("name"),
                    "type": s.get("strategy_type"),
                    "symbols": s.get("symbols", []),
                    "enabled": s.get("enabled"),
                }
                for s in active_autopilot
            ],
        },
    }
