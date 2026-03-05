"""
NeoStock2 策略 — 持久化模組

負責：
- 策略配置自動存檔到 data/strategies.json
- 啟動時自動載入上次的策略配置
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("neostock2.strategies.persistence")

DEFAULT_PATH = Path("data/strategies.json")


def save_strategies(strategies_info: list[dict], path: Path = None) -> bool:
    """
    儲存策略配置

    Args:
        strategies_info: 策略資訊列表，每項包含:
            - name: 策略實例名稱
            - strategy_type: 策略類型 key
            - symbols: 監控標的
            - params: 策略參數
            - enabled: 是否啟用
        path: 儲存路徑

    Returns:
        是否儲存成功
    """
    filepath = path or DEFAULT_PATH
    try:
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # 只保留必要的欄位
        data = []
        for info in strategies_info:
            data.append({
                "name": info.get("name", ""),
                "strategy_type": info.get("strategy_type", ""),
                "symbols": info.get("symbols", []),
                "params": info.get("params", {}),
                "enabled": info.get("enabled", False),
            })

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.debug(f"策略配置已儲存: {len(data)} 個策略 → {filepath}")
        return True

    except Exception as e:
        logger.error(f"策略配置儲存失敗: {e}")
        return False


def load_strategies(path: Path = None) -> list[dict]:
    """
    載入策略配置

    Args:
        path: 檔案路徑

    Returns:
        策略配置列表
    """
    filepath = path or DEFAULT_PATH
    try:
        if not filepath.exists():
            logger.info(f"策略配置檔不存在: {filepath}，跳過載入")
            return []

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            logger.warning("策略配置格式錯誤，預期為 list")
            return []

        logger.info(f"✅ 載入 {len(data)} 個策略配置 ← {filepath}")
        return data

    except json.JSONDecodeError as e:
        logger.error(f"策略配置 JSON 解析失敗: {e}")
        return []
    except Exception as e:
        logger.error(f"策略配置載入失敗: {e}")
        return []
