# Shioaji API 使用深度對比報告：NeoStock2 vs shioaji-api-dashboard

本報告針對您目前 `NeoStock2` 專案中對 Shioaji API 的使用方式，與開源標竿專案 `shioaji-api-dashboard` 進行逐項對比，並提出架構上的建議。

## 1. 核心連線與認證 (Connection & Auth)

| 項目 | NeoStock2 (我的實作) | shioaji-api-dashboard (參考實作) | 差異分析 |
| :--- | :--- | :--- | :--- |
| **登入機制** | 單次登入 (`main.py` 啟動時) | 自動重連機制 (Auto-Reconnect) | **⚠️ 風險**：與券商連線可能會斷線或 Token 過期 (通常 24hr)。Dashboard 通常有背景 Worker 定期檢查連線狀態並重登。NeoStock2 目前若斷線需重啟程式。 |
| **CA 憑證** | 單次啟用 (`activate_ca`) | 隨連線啟用 | **✅ 兩者類似**，但需注意 CA 憑證路徑設定是否靈活。 |
| **模擬環境** | 透過 `.env` 設定 `SHIOAJI_SIMULATION` | 環境變數控制 | **✅ 一致**。 |

### 💡 建議
*   **短期**：在 `ApiClient` 中加入 `check_connection()` 方法，若發現 `api.stock_account` 失效或呼叫報錯，自動觸發重登入流程。
*   **長期**：參考 Dashboard 使用 `apscheduler` 或背景 Thread 定時 (例如每 5 分鐘) 發送 Heartbeat (如 `api.list_trades` 或 `api.account_balance`) 確保連線存活。

## 2. 下單與委託管理 (Order Placements)

| 項目 | NeoStock2 (我的實作) | shioaji-api-dashboard (參考實作) | 差異分析 |
| :--- | :--- | :--- | :--- |
| **下單呼叫** | `api.place_order(contract, order)` | 同樣使用標準呼叫 | **✅ 核心呼叫一致**。 |
| **委託狀態更新** | **混合模式**：<br>1. 此時 `update_status` 主動查詢<br>2. 註冊 `set_order_callback` 接收推播 | **事件驅動 (Event Driven)**：<br>主要依賴 Callback 推播更新 Redis 狀態，前端再輪詢 Redis。 | **⚠️ 潛在問題**：您的 `OrderManager` 使用 `threading.Lock` 保護 `_orders`。若瞬間大量成交回報進來，Lock 可能造成效能瓶頸。Dashboard 使用 Redis Queue 可緩衝瞬間流量。 |
| **成交寫入** | `on_trade` 回呼直接寫入 `Portfolio` (DB) | Worker 接收成交事件 -> 寫入 DB | **⚠️ 事務風險**：在 Callback 中直接寫 DB (SQLite) 若卡住會阻塞 API 接收下一個封包。 |
| **例外處理** | 基礎 `try-catch` | 完整的錯誤重試 (Retry) 機制 | Dashboard 針對特定 API 錯誤代碼 (如 Network Error) 會有重試邏輯。 |

### 💡 建議
*   **無需更換架構，但需優化**：
    *   將 `on_trade` 回呼中的「寫入帳本」邏輯改為 **非同步 (Asynchronous)** 或丟入 Python `queue.Queue`，另開一個 Thread 專門負責寫 DB，**避免阻塞 Shioaji 的 Callback function**。這是最關鍵的效能優化點。

## 3. 行情數據 (Market Data)

| 項目 | NeoStock2 (我的實作) | shioaji-api-dashboard (參考實作) | 差異分析 |
| :--- | :--- | :--- | :--- |
| **即時行情** | `api.quote.subscribe` + 本地 `dict` 快取 | Redis Pub/Sub | **✅ NeoStock2 較簡單直接**。對於單機策略，直接用記憶體 (`dict`) 存取是最快的，Redis 反而多一層延遲。 |
| **歷史 K 棒** | `api.kbars` | 同樣使用標準呼叫 | **✅ 一致**。 |
| **雙向維護** | 同時維護 Tick 與 BidAsk | 視需求訂閱 | **✅ 一致**。 |

### 💡 建議
*   您的 `MarketDataManager` 寫得不錯，使用了 `threading.Lock` 保護快取。只要策略運算不要卡住主程式，這套機制對單機來說效能優於 Redis 架構。

## 4. 庫存與帳務查詢 (Positions & PnL)

| 項目 | NeoStock2 (我的實作) | shioaji-api-dashboard (參考實作) | 差異分析 |
| :--- | :--- | :--- | :--- |
| **庫存來源** | **雙軌制**：<br>1. `Portfolio` 資料庫 (本地記帳)<br>2. `api.list_positions` (券商同步) | **單一來源**：<br>主要依賴 `api.list_positions` 或 `list_profit_loss` | **✅ NeoStock2 勝出**。<br>單純依賴 `api.list_positions` 會有延遲且無法記錄歷史損益。您的「本地記帳 + 定期同步」模式是正確的量化交易做法。 |
| **即時損益** | 本地計算 (市價 - 成本) | 依賴 API 回傳 | **✅ NeoStock2 勝出**。<br>Shioaji 的即時損益更新較慢，您用即時 Tick 計算本地庫存損益是更即時的做法。 |

## 🚀 綜合結論與行動建議

**Q: 是否要改成 shioaji-api-dashboard 的使用方法？**

**A: 不需要完全改用，但應學習其「非阻塞 (Non-blocking)」的設計精神。**

您的 `NeoStock2` 目前是 **「胖客戶端 (Fat Client)」** 架構 (邏輯全在本地)，而 `shioaji-api-dashboard` 是 **「薄客戶端 (Thin Client) + 伺服器」** 架構。對於個人交易者，您的架構其實更單純好維護。

**您只需要補足以下三點，即可達到 Dashboard 的穩定度：**

1.  **非阻塞式回調 (Critical)**：
    *   修改 `OrderManager` 和 `MarketDataManager` 的 Callback。
    *   **不要**在 Callback 裡面做複雜運算或 DB IO。
    *   **要**只做一件事：把資料丟進 `queue.Queue`。
    *   另外起一個 `Worker Thread` 專門從 Queue 拿資料去寫 DB 或觸發策略。

2.  **斷線重連 (Stability)**：
    *   在 `main.py` 加入一個定期檢查連線的 Loop，若 `api_client.is_logged_in` 雖為 True 但呼叫失敗，則執行重新登入。

3.  **Operation Code 檢查**：
    *   Dashboard 對 `Trade` 物件中的 `operation` 欄位有詳細檢查 (如 `op_code != "00"` 代表失敗)。您的 `process_order_status` 中已有部分處理，建議加強對「下單失敗」狀態的解析，以免卡在 Pending。

### 總結
您的 API 使用方式大致正確，且在庫存管理上比 Dashboard 更完善。不需重寫，只需優化 Callback 的處理效率即可。
