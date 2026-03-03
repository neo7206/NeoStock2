import pandas as pd
import vectorbt as vbt
from pathlib import Path
from .data_loader import fetch_data, process_kbars

class StrategyUniverse:
    def __init__(self, tickers: list[str], data_dir: str = "data/research_kbars"):
        self.tickers = tickers
        self.data_dir = Path(data_dir)
        self.ohlcv = {}
    
    def load_data(self):
        """
        載入資料，若無則嘗試下載 (需確保 data_loader 可運作)
        並轉換為 vbt 可用的 Dict[symbol, df] 格式
        """
        for ticker in self.tickers:
            file_path = self.data_dir / f"{ticker}_60m.csv"
            if not file_path.exists():
                print(f"[Universe] 資料不存在: {file_path}，請先執行 data_loader.py")
                continue
                
            df = pd.read_csv(file_path)
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
            
            # vbt 偏好單一 index 或 datetime index
            self.ohlcv[ticker] = df
            
        print(f"[Universe] 已載入: {list(self.ohlcv.keys())}")

    def get_price(self, ticker: str, column: str = 'close') -> pd.Series:
        if ticker not in self.ohlcv:
            raise ValueError(f"Ticker {ticker} not loaded.")
        return self.ohlcv[ticker][column]

    def get_data(self, ticker: str) -> pd.DataFrame:
        if ticker not in self.ohlcv:
            raise ValueError(f"Ticker {ticker} not loaded.")
        return self.ohlcv[ticker]
