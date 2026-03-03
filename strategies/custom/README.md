# strategies/custom — 自訂策略目錄

## 如何新增策略

1. 在此目錄建立一個 `.py` 檔案，例如 `my_strategy.py`
2. 繼承 `BaseStrategy`，實作 `on_tick()` 和 `on_bar()` 方法
3. 系統啟動時會自動掃描此目錄，載入所有合法策略

## 範例

```python
from strategies.base_strategy import BaseStrategy, Signal, SignalAction

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "我的自訂策略"
    default_params = {
        "threshold": 0.05,
    }

    def on_tick(self, tick_data: dict):
        # 處理即時 tick 資料
        pass

    def on_bar(self, symbol: str, bars):
        # 處理 K 棒資料，產生 Signal
        pass
```

4. 重啟系統或呼叫 API `POST /strategies/reload` 即可生效
