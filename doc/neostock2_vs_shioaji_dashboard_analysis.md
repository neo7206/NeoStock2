# NeoStock2 vs shioaji-api-dashboard 深度分析報告

本報告針對您目前的 `NeoStock2` 實作與開源專案 `shioaji-api-dashboard` 進行全面的對比分析。

## 🚀 執行摘要 (Executive Summary)

*   **NeoStock2 (您的專案)**：定位為**「全功能台股自動交易機器人」**。
    *   **核心優勢**：整合了「帳務管理系統」與「策略回測引擎 (Backtrader)」，專為**台股 (Stock)** 現貨交易設計（包含交易稅/手續費計算），且是一個自包含 (Self-contained) 的 Python 應用，部署門檻較低（不強制依賴 Docker/Redis）。
    *   **目前狀態**：核心架構已成形，具備完整的 MVC 結構 (API/Data/UI 分離)，且已有基本的策略框架與回測接口。

*   **shioaji-api-dashboard**：定位為**「期貨交易 API 閘道器與儀表板」**。
    *   **核心優勢**：現代化微服務架構 (FastAPI + Redis + Celery)，專注於**期貨 (Futures)** 的高併發與穩定性。特點是支援 **Webhook** (如 TradingView) 觸發下單，適合做為「執行端」而非「策略端」。
    *   **主要差異**：它不包含策略邏輯或回測功能，而是作為外部訊號的接收器。

---

## 📊 綜合對比分析

| 比較維度 | 您的 NeoStock2 🟢 | shioaji-api-dashboard 🔵 | 分析與建議 |
| :--- | :--- | :--- | :--- |
| **核心定位** | **全自包含機器人**<br>(策略+執行+帳務) | **API 閘道器**<br>(訊號接收+執行) | NeoStock2 定位更適合個人全自動化交易；Dashboard 適合已有外部策略訊號源 (如 TradingView) 的用戶。 |
| **資產類別** | **台股現貨 (Stock)**<br>(含證交稅/手續費邏輯) | **期貨 (Futures)**<br>(主要針對期貨代碼與邏輯) | **✅ NeoStock2 勝**<br>目前開源界缺乏完整的台股現貨機器人，您的方向正確。 |
| **系統架構** | **Monolithic (單體式)**<br>FastAPI + 內部 Threading | **Microservices (微服務)**<br>FastAPI + Redis Queue + Workers | **⚠️ 改進點**<br>若交易量大，NeoStock2 可能會因單一 Process 阻塞而延遲。建議參考 Dashboard 引入 **Redis Queue** 來處理下單請求。 |
| **策略來源** | **內部 Python 策略**<br>(支援 Backtrader) | **外部 Webhook**<br>(TradingView / Python requests) | **💡 建議補足**<br>NeoStock2 目前缺 Webhook。建議補上 `/api/webhook` 接口，讓 TradingView 也能觸發您的下單邏輯。 |
| **回測能力** | **✅ 內建整合**<br>(Backtrader Bridge) | **❌ 無**<br>(需自行處理) | **✅ NeoStock2 勝**<br>這是您的強大優勢，能從「策略研發」到「實盤執行」一條龍與系統整合。 |
| **帳務管理** | **✅ 完整 SQL 帳本**<br>(ROI, 損益, 庫存同步) | **⚠️ 基礎**<br>(僅依賴 API 回傳資訊) | **✅ NeoStock2 勝**<br>您的 `Portfolio` 模組考慮了台股的稅費與歷史損益記錄，這對資產管理至關重要。 |
| **部署方式** | **Python Script**<br>(直接執行 `main.py`) | **Docker Container**<br>(`docker-compose up`) | **💡 建議補足**<br>建議為 NeoStock2 撰寫 `Dockerfile`，讓部署更標準化，避免環境依賴問題。 |
| **UI 介面** | **Jinja2 模板 (SSR)**<br>(簡單直觀) | **Vue.js / React (SPA)**<br>(互動性較高) | 目前 NeoStock2 的 SSR 足夠使用，未來可考慮分離前後端以提升體驗。 |

---

## 🔍 NeoStock2 目前實作深入檢視

經過程式碼審查，您的專案架構相當紮實：

1.  **模組化清晰**：`core` (API), `ledger` (帳務), `strategies` (策略), `dashboard` (UI) 職責分離明確。
2.  **帳務邏輯細膩**：`ledger/portfolio.py` 中詳細實作了 `calculate_fee` (手續費折扣) 與 `calculate_tax` (證交稅)，這是通用開源專案常忽略的細節。
3.  **策略引擎設計良好**：`strategies/base_strategy.py` 定義了標準的 `on_tick` / `on_bar` 介面，且透過 `Signal` 物件解耦了「策略判斷」與「下單執行」。
4.  **回測整合**：利用 `backtrader_bridge.py` 讓使用者可以用同一套邏輯進行回測，這非常有價值。

### ⚠️ 發現的潛在隱憂
*   **併發處理 (Concurrency)**：目前 `order_manager` 與 `market_data` 似乎都在主執行緒或簡單的 Thread 中運行。若瞬間Tick量大或下單請求多，雖因是 IO Bound 可能影響不大，但缺乏像 Redis 這樣的緩衝機制，容易在網路波動時丟單或阻塞。
*   **容錯機制**：`shioaji-api-dashboard` 特別強調自動重連與 Token 過期處理。您的 `main.py` 有自動登入，但需確認 `ShioajiClient` 內部是否有健壯的斷線重連機制。

---

## 🛠️ 給 NeoStock2 的改進建議 (Action Items)

基於上述分析，我建議您可以依序補足以下部分，讓 NeoStock2 更完美：

### 1. 短期目標 (High Priority)
- [ ] **新增 Webhook 接口**：參考 `shioaji-api-dashboard`，在 `dashboard/routers` 中新增一個 Webhook 路由，接收 JSON 格式訊號 (如 `{ "action": "buy", "code": "2330", "price": 500 }`)，讓您可以用 TradingView 寫策略並觸發 NeoStock2 下單。
- [ ] **強化異常處理**：檢查 `core/api_client.py`，確保有處理 Shioaji API 斷線 (Solace disconnect) 的自動重連邏輯。

### 2. 中期目標 (Medium Priority)
- [ ] **Docker 化**：新增 `Dockerfile` 與 `docker-compose.yaml`。這樣您換電腦或部署到雲端 server 時，一行指令就能啟動整個環境 (含 DB)。
- [ ] **Line/Telegram 通知**：目前看結構有 `notifications` 資料夾但尚未深度整合。建議在 `OrderManager` 成交回報 (`on_trade_callback`) 時串接通知發送。

### 3. 長期目標 (Advanced)
- [ ] **引入 Redis 任務佇列**：若未來策略複雜度提高，可將「策略運算」與「下單執行」拆開。策略產生訊號丟入 Redis，另一個 Worker 程式負責從 Redis 取出並執行下單。這能大幅提升系統穩定度，避免單一 Process 掛掉全盤皆輸。
- [ ] **前端分離**：將 Dashboard 升級為 React/Vue 獨立前端，透過 API 與後端溝通，體驗會更流暢 (如動態 K 線圖)。

## 🎯 結論

**NeoStock2 已經走在正確的道路上。**

您選擇了「自建框架」(Option D) 是正確的，因為 `shioaji-api-dashboard` 雖然架構現代，但功能上太過偏向「期貨下單機」，缺乏台股現貨所需的帳務與回測功能。

您的下一步不應該是模仿它的架構 (引入 Redis/Microservices) 而把系統搞得太複雜，而是應該 **「吸收它的功能優點」**：即 **Webhook 支援** 與 **Docker 部署**。補足這兩點，您的 NeoStock2 將會是一個非常強大且適合個人使用的量化交易系統。
