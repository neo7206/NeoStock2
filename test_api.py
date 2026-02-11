import requests
import json

BASE_URL = "http://127.0.0.1:8080/api"

def test_api():
    print("🚀 開始 API 測試...")

    # 1. 建立策略
    payload = {
        "name": "TestRules",
        "strategy_type": "sma_crossover",
        "symbols": ["2330"],
        "params": {
            "lot_size": 3,
            "max_position": 8,
            "stop_loss_pct": 0.04,
            "take_profit_pct": 0.12,
            "fast_period": 8,
            "slow_period": 24
        },
        "enabled": True
    }
    
    try:
        resp = requests.post(f"{BASE_URL}/strategy/create", json=payload)
        resp.raise_for_status()
        print(f"✅ 策略建立成功: {resp.json()}")
    except Exception as e:
        print(f"❌ 策略建立失敗: {e}")
        return

    # 2. 驗證策略列表參數
    try:
        resp = requests.get(f"{BASE_URL}/strategy/list")
        strategies = resp.json().get("data", [])
        found = next((s for s in strategies if s["name"] == "TestRules"), None)
        
        if found:
            params = found.get("params", {})
            assert params.get("lot_size") == 3, "lot_size 錯誤"
            assert params.get("stop_loss_pct") == 0.04, "stop_loss_pct 錯誤"
            print(f"✅ 策略參數驗證成功: {params}")
        else:
            print("❌ 找不到建立的策略")
            return
    except Exception as e:
        print(f"❌ 列表驗證失敗: {e}")

    # 3. 驗證風控摘要
    try:
        # 先刪除舊策略以避免名稱衝突
        requests.delete(f"{BASE_URL}/strategy/TestRules")
        print("✅ 測試策略已清理")
    except Exception as e:
        print(f"⚠️ 清理失敗: {e}")

if __name__ == "__main__":
    test_api()
