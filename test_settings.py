import requests
import time

BASE_URL = "http://127.0.0.1:8080/api"

def test_settings():
    print("🚀 開始資金設定測試...")

    # 1. 取得預設資金
    try:
        resp = requests.get(f"{BASE_URL}/settings/account")
        data = resp.json().get("data", {})
        print(f"✅ 預設資金: {data}")
        assert data.get("initial_capital", 0) > 0
    except Exception as e:
        print(f"❌ 取得預設資金失敗: {e}")
        return

    # 2. 修改資金
    new_capital = 2000000
    new_cash = 1500000
    try:
        payload = {"initial_capital": new_capital, "available_cash": new_cash}
        resp = requests.post(f"{BASE_URL}/settings/account", json=payload)
        resp.raise_for_status()
        print(f"✅ 修改資金請求成功: {resp.json()}")
    except Exception as e:
        print(f"❌ 修改資金失敗: {e}")
        return

    # 3. 驗證修改結果
    try:
        resp = requests.get(f"{BASE_URL}/settings/account")
        data = resp.json().get("data", {})
        assert data["initial_capital"] == new_capital
        assert data["available_cash"] == new_cash
        print(f"✅ 資金設定驗證成功: {data}")
    except Exception as e:
        print(f"❌ 驗證修改結果失敗: {e}")
        return

    # 4. 驗證總覽 API 反應
    try:
        resp = requests.get(f"{BASE_URL}/ledger/summary")
        summary = resp.json().get("data", {})
        
        # 總資產應該 = 可用現金 + 持倉市值
        market_value = summary.get("total_market_value", 0)
        expected_asset = new_cash + market_value
        
        print(f"📊 總覽數據: 現金={summary.get('available_cash')}, 市值={market_value}, 總資產={summary.get('total_asset')}")
        
        assert summary["available_cash"] == new_cash
        assert summary["total_asset"] == expected_asset
        
        # 驗證總損益 (總資產 - 本金)
        expected_pnl = expected_asset - new_capital
        print(f"💰 預期損益: {expected_pnl}, 實際損益: {summary.get('total_pnl')}")
        assert abs(summary["total_pnl"] - expected_pnl) < 1.0
        
        print("✅ 總覽 API 整合驗證成功")
    except Exception as e:
        print(f"❌ 總覽驗證失敗: {e}")

if __name__ == "__main__":
    test_settings()
