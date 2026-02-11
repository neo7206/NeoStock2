"""
NeoStock2 儀表板 — 全域狀態

將 app_state 獨立出來，避免 app.py ↔ routers 循環引入。
"""

# === 全域服務實例（由 main.py 注入） ===
app_state = {
    "api_client": None,
    "market_data": None,
    "order_manager": None,
    "portfolio": None,
    "risk_manager": None,
    "roi_calculator": None,
    "strategy_engine": None,
}
