"""
NeoStock2 策略 — 預設策略套件

提供三種風險偏好的預設策略組合，讓不熟悉技術指標的使用者可以一鍵開始交易。
"""

import logging

logger = logging.getLogger("neostock2.strategies.presets")


PRESETS = {
    "conservative": {
        "id": "conservative",
        "name": "🟢 保守型",
        "description": "追求穩定收益，以 ETF 和大型權值股為主。目標年化 8-12%。",
        "target_return": "8-12%",
        "risk_level": 1,
        "strategies": [
            {
                "name": "ETF均線趨勢",
                "strategy_type": "sma_crossover",
                "symbols": ["0050", "0056"],
                "params": {
                    "short_period": 10,
                    "long_period": 60,
                    "quantity": 1,
                    "stop_loss_pct": 0.07,     # 寬停損
                    "take_profit_pct": 0.10,
                    "trailing_stop_pct": 0.05,
                    "max_position": 3,
                },
                "enabled": True,
            },
            {
                "name": "ETF布林回歸",
                "strategy_type": "bollinger_band",
                "symbols": ["0050", "006208"],
                "params": {
                    "period": 20,
                    "std_dev": 2.0,
                    "quantity": 1,
                    "stop_loss_pct": 0.07,
                    "take_profit_pct": 0.08,
                    "max_position": 3,
                },
                "enabled": True,
            },
        ],
        "risk_settings": {
            "max_single_position_pct": 0.30,
            "max_daily_loss": 8000,
            "default_stop_loss_pct": 0.07,
            "default_take_profit_pct": 0.10,
            "max_total_positions": 6,
            "max_single_amount": 300000,
        },
    },
    "balanced": {
        "id": "balanced",
        "name": "🟡 穩健型",
        "description": "平衡風險與收益，以大型股搭配趨勢策略。目標年化 12-18%。",
        "target_return": "12-18%",
        "risk_level": 2,
        "strategies": [
            {
                "name": "趨勢突破追蹤",
                "strategy_type": "sma_crossover",
                "symbols": ["2330", "2317", "2454"],
                "params": {
                    "short_period": 5,
                    "long_period": 20,
                    "quantity": 1,
                    "stop_loss_pct": 0.05,
                    "take_profit_pct": 0.12,
                    "trailing_stop_pct": 0.04,
                    "max_position": 3,
                },
                "enabled": True,
            },
            {
                "name": "RSI回檔買進",
                "strategy_type": "rsi_reversal",
                "symbols": ["2330", "2317", "2454"],
                "params": {
                    "period": 14,
                    "oversold": 30,
                    "overbought": 70,
                    "quantity": 1,
                    "stop_loss_pct": 0.05,
                    "take_profit_pct": 0.12,
                    "max_position": 2,
                },
                "enabled": True,
            },
            {
                "name": "MACD趨勢",
                "strategy_type": "macd_signal",
                "symbols": ["0050", "2330"],
                "params": {
                    "fast_period": 12,
                    "slow_period": 26,
                    "signal_period": 9,
                    "quantity": 1,
                    "stop_loss_pct": 0.05,
                    "take_profit_pct": 0.12,
                    "max_position": 2,
                },
                "enabled": True,
            },
        ],
        "risk_settings": {
            "max_single_position_pct": 0.25,
            "max_daily_loss": 12000,
            "default_stop_loss_pct": 0.05,
            "default_take_profit_pct": 0.12,
            "max_total_positions": 8,
            "max_single_amount": 250000,
        },
    },
    "aggressive": {
        "id": "aggressive",
        "name": "🔴 積極型",
        "description": "追求高報酬，多策略組合搭配嚴格風控。目標年化 18-25%。",
        "target_return": "18-25%",
        "risk_level": 3,
        "strategies": [
            {
                "name": "短線SMA交叉",
                "strategy_type": "sma_crossover",
                "symbols": ["2330", "2454", "3711", "2383"],
                "params": {
                    "short_period": 3,
                    "long_period": 10,
                    "quantity": 1,
                    "stop_loss_pct": 0.03,     # 緊停損
                    "take_profit_pct": 0.15,   # 寬停利
                    "trailing_stop_pct": 0.03,
                    "max_position": 2,
                },
                "enabled": True,
            },
            {
                "name": "MACD動能追蹤",
                "strategy_type": "macd_signal",
                "symbols": ["2330", "2454", "3711", "2383"],
                "params": {
                    "fast_period": 8,
                    "slow_period": 21,
                    "signal_period": 9,
                    "quantity": 1,
                    "stop_loss_pct": 0.03,
                    "take_profit_pct": 0.15,
                    "max_position": 2,
                },
                "enabled": True,
            },
            {
                "name": "RSI反轉短線",
                "strategy_type": "rsi_reversal",
                "symbols": ["2454", "3711", "2383"],
                "params": {
                    "period": 7,
                    "oversold": 25,
                    "overbought": 75,
                    "quantity": 1,
                    "stop_loss_pct": 0.03,
                    "take_profit_pct": 0.15,
                    "max_position": 2,
                },
                "enabled": True,
            },
            {
                "name": "布林極端反轉",
                "strategy_type": "bollinger_band",
                "symbols": ["2330", "2454"],
                "params": {
                    "period": 15,
                    "std_dev": 2.5,
                    "quantity": 1,
                    "stop_loss_pct": 0.03,
                    "take_profit_pct": 0.15,
                    "max_position": 2,
                },
                "enabled": True,
            },
        ],
        "risk_settings": {
            "max_single_position_pct": 0.20,
            "max_daily_loss": 15000,
            "default_stop_loss_pct": 0.03,
            "default_take_profit_pct": 0.15,
            "max_total_positions": 10,
            "max_single_amount": 200000,
        },
    },
}


def get_presets() -> list[dict]:
    """取得所有預設套件列表"""
    result = []
    for preset_id, preset in PRESETS.items():
        result.append({
            "id": preset["id"],
            "name": preset["name"],
            "description": preset["description"],
            "target_return": preset["target_return"],
            "risk_level": preset["risk_level"],
            "strategy_count": len(preset["strategies"]),
        })
    return result


def get_preset_detail(preset_id: str) -> dict | None:
    """取得指定預設套件的完整資訊"""
    return PRESETS.get(preset_id)


def apply_preset(
    preset_id: str,
    strategy_engine,
    risk_manager=None,
    symbols_override: list[str] = None,
) -> dict:
    """
    套用預設套件

    Args:
        preset_id: 套件 ID
        strategy_engine: 策略引擎實例
        risk_manager: 風控管理器（可選，可更新風控設定）
        symbols_override: 若提供，覆蓋所有策略的標的

    Returns:
        套用結果
    """
    preset = PRESETS.get(preset_id)
    if not preset:
        return {"success": False, "error": f"找不到預設套件: {preset_id}"}

    results = {"success": True, "strategies_created": 0, "errors": []}

    # 1. 建立策略
    for strat_cfg in preset["strategies"]:
        symbols = symbols_override or strat_cfg["symbols"]
        try:
            success = strategy_engine.register_strategy(
                name=strat_cfg["name"],
                strategy_type=strat_cfg["strategy_type"],
                symbols=symbols,
                params=strat_cfg["params"],
                enabled=strat_cfg.get("enabled", True),
            )
            if success:
                results["strategies_created"] += 1
            else:
                results["errors"].append(
                    f"建立策略 [{strat_cfg['name']}] 失敗"
                )
        except Exception as e:
            results["errors"].append(
                f"建立策略 [{strat_cfg['name']}] 異常: {e}"
            )

    # 2. 更新風控設定（如果提供）
    if risk_manager and "risk_settings" in preset:
        risk_cfg = preset["risk_settings"]
        try:
            risk_manager.max_single_position_pct = risk_cfg.get(
                "max_single_position_pct", risk_manager.max_single_position_pct
            )
            risk_manager.max_daily_loss = risk_cfg.get(
                "max_daily_loss", risk_manager.max_daily_loss
            )
            risk_manager.default_stop_loss_pct = risk_cfg.get(
                "default_stop_loss_pct", risk_manager.default_stop_loss_pct
            )
            risk_manager.default_take_profit_pct = risk_cfg.get(
                "default_take_profit_pct", risk_manager.default_take_profit_pct
            )
            risk_manager.max_total_positions = risk_cfg.get(
                "max_total_positions", risk_manager.max_total_positions
            )
            risk_manager.max_single_amount = risk_cfg.get(
                "max_single_amount", risk_manager.max_single_amount
            )
            results["risk_updated"] = True
            logger.info(f"✅ 風控設定已更新為 [{preset['name']}] 模式")
        except Exception as e:
            results["errors"].append(f"更新風控設定失敗: {e}")

    results["preset_name"] = preset["name"]
    logger.info(
        f"✅ 預設套件 [{preset['name']}] 已套用, "
        f"建立 {results['strategies_created']} 個策略"
    )
    return results
