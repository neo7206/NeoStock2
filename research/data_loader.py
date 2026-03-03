import sys
import os
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import time

# 設定專案根目錄路徑以匯入核心模組
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

try:
    from core.api_client import ShioajiClient
except ImportError:
    print("錯誤: 無法匯入 core.api_client。請確認您是在專案根目錄或透過正確的環境執行。")
    sys.exit(1)

def process_kbars(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """
    清洗與重取樣資料
    1. 設定 Index 為 datetime
    2. 只保留 09:00 - 13:30
    3. Resample 為 60分K
    """
    # 確保 ts 為 datetime 且為 UTC+8 (Shioaji ts 通常是 ns)
    df['ts'] = pd.to_datetime(df['ts'])
    
    # 假設 Shioaji 回傳的是本地時間 (依賴 API 行為，通常是)
    # 若需要時區處理可在此加入
    
    df.set_index('ts', inplace=True)
    
    # 過濾非交易時段 (簡單過濾，詳細的一般需考慮盤後盤等，這裡先只取 09:00-13:30)
    # 先做 Resample 再過濾，或者是先過濾再 Resample?
    # Shioaji 給的 1分K 包含 09:00:00 - 13:30:00
    # 我們需要 60分K: 09:00, 10:00, 11:00, 12:00, 13:00
    
    # 定義 Resample 規則
    # left label, left closed: 09:00-10:00 歸在 09:00
    ohlc_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }
    
    # 進行 Resample
    df_60m = df.resample('60min', label='left', closed='left').agg(ohlc_dict)
    
    # 移除 NaN (非交易時段產生的空 Bar)
    df_60m.dropna(inplace=True)
    
    # 過濾時間只保留 09:00 到 13:00 (含)
    # 13:00 的 Bar 代表 13:00-14:00 (實際只有到 13:30)
    df_60m = df_60m.between_time('09:00', '13:01')
    
    # 重設 Index 轉回欄位
    df_60m.reset_index(inplace=True)
    
    # 重新命名欄位符合需求
    df_60m.rename(columns={'ts': 'datetime'}, inplace=True)
    
    return df_60m

def fetch_data(
    tickers: list[str] = ["2330", "2308", "0050"],
    start_date: str = None,
    end_date: str = None,
    output_dir: str = "data/research_kbars"
):
    """
    抓取指定股票的歷史資料並存為 CSV
    """
    # 預設抓取 5 年 (365 * 5)
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")
    if not start_date:
        start_date = (datetime.now() - timedelta(days=365*5)).strftime("%Y-%m-%d")

    print(f"專案根目錄: {PROJECT_ROOT}")
    print(f"資料區間: {start_date} ~ {end_date}")
    
    # 建立輸出目錄
    output_path = PROJECT_ROOT / output_dir
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 初始化 Shioaji 客戶端
    client = ShioajiClient(config_dir=str(PROJECT_ROOT / "config"))
    if not client.login():
        print("無法登入 Shioaji API，請檢查設定檔。")
        return

    api = client.api

    for ticker in tickers:
        print(f"\n[{ticker}] 正在處理...")
        contract = client.get_contract(ticker)
        if not contract:
            print(f"[{ticker}] 找不到合約，跳過。")
            continue
            
        print(f"[{ticker}] 正在下載 1分K 資料...")
        
        try:
            # 呼叫 Shioaji API 抓取 K 線
            # 注意: 一次抓 5 年可能會很久或 timeout，視網路與 API 限制而定
            kbars = api.kbars(contract, start=start_date, end=end_date)
            
            # 轉換為 DataFrame
            df = pd.DataFrame({
                "ts": pd.to_datetime(kbars.ts),
                "open": kbars.Open,
                "high": kbars.High,
                "low": kbars.Low,
                "close": kbars.Close,
                "volume": kbars.Volume
            })
            
            if df.empty:
                print(f"[{ticker}] 查無資料。")
                continue
                
            print(f"[{ticker}] 下載完成，共 {len(df)} 筆原始 1分K 資料。")
            
            # 資料處理與 Resample
            df_60m = process_kbars(df, ticker)
            
            print(f"[{ticker}] 轉換完成，共 {len(df_60m)} 筆 60分K 資料。")
            
            # 儲存為 CSV
            save_path = output_path / f"{ticker}_60m.csv"
            df_60m.to_csv(save_path, index=False)
            print(f"[{ticker}] 已儲存至: {save_path}")
            
        except Exception as e:
            print(f"[{ticker}] 發生錯誤: {e}")
            import traceback
            traceback.print_exc()

    # 登出
    client.logout()
    print("\n所有作業完成。")


def update_data(
    tickers: list[str] = ["2330", "2308", "0050"],
    output_dir: str = "data/research_kbars"
):
    """
    增量更新：讀取既有 CSV 的最後一筆日期，
    只下載該日之後的新資料，合併後覆蓋寫回。
    若 CSV 不存在，fallback 到全量下載。
    """
    output_path = PROJECT_ROOT / output_dir
    output_path.mkdir(parents=True, exist_ok=True)
    
    # 檢查哪些需要全量 vs 增量
    full_download = []
    incremental = []
    
    for ticker in tickers:
        csv_path = output_path / f"{ticker}_60m.csv"
        if csv_path.exists():
            try:
                df_existing = pd.read_csv(csv_path)
                if len(df_existing) > 0 and 'datetime' in df_existing.columns:
                    last_date = pd.to_datetime(df_existing['datetime']).max()
                    incremental.append((ticker, csv_path, df_existing, last_date))
                    continue
            except Exception:
                pass
        full_download.append(ticker)
    
    # 全量下載不存在的
    if full_download:
        print(f"以下標的無既有CSV，執行全量下載: {full_download}")
        fetch_data(tickers=full_download, output_dir=output_dir)
    
    if not incremental:
        return
    
    # 增量更新
    end_date = datetime.now().strftime("%Y-%m-%d")
    
    print(f"\n=== 增量更新模式 ===")
    print(f"目標日期: 至 {end_date}")
    
    client = ShioajiClient(config_dir=str(PROJECT_ROOT / "config"))
    if not client.login():
        print("無法登入 Shioaji API")
        return
    
    api = client.api
    
    for ticker, csv_path, df_existing, last_date in incremental:
        # 從最後日期的隔天開始下載
        start_date = (last_date + timedelta(days=1)).strftime("%Y-%m-%d")
        
        if start_date > end_date:
            print(f"[{ticker}] 資料已是最新 (最後: {last_date.strftime('%Y-%m-%d')})")
            continue
        
        print(f"[{ticker}] 增量下載 {start_date} ~ {end_date} ...")
        
        contract = client.get_contract(ticker)
        if not contract:
            print(f"[{ticker}] 找不到合約，跳過。")
            continue
        
        try:
            kbars = api.kbars(contract, start=start_date, end=end_date)
            df_new = pd.DataFrame({
                "ts": pd.to_datetime(kbars.ts),
                "open": kbars.Open,
                "high": kbars.High,
                "low": kbars.Low,
                "close": kbars.Close,
                "volume": kbars.Volume
            })
            
            if df_new.empty:
                print(f"[{ticker}] 無新資料。")
                continue
            
            print(f"[{ticker}] 下載 {len(df_new)} 筆新 1分K")
            
            # 轉換為 60分K
            df_new_60m = process_kbars(df_new, ticker)
            print(f"[{ticker}] 轉換為 {len(df_new_60m)} 筆新 60分K")
            
            # 合併（去重）
            df_merged = pd.concat([df_existing, df_new_60m], ignore_index=True)
            df_merged['datetime'] = pd.to_datetime(df_merged['datetime'])
            df_merged.drop_duplicates(subset=['datetime'], keep='last', inplace=True)
            df_merged.sort_values('datetime', inplace=True)
            df_merged.reset_index(drop=True, inplace=True)
            
            # 覆蓋寫回
            df_merged.to_csv(csv_path, index=False)
            print(f"[{ticker}] 更新完成，總計 {len(df_merged)} 筆 (原 {len(df_existing)}, 新增 {len(df_merged)-len(df_existing)})")
            
        except Exception as e:
            print(f"[{ticker}] 增量更新失敗: {e}")
            import traceback
            traceback.print_exc()
    
    client.logout()
    print("\n增量更新完成。")


if __name__ == "__main__":
    # 支援命令列參數：python data_loader.py --update 增量更新
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--update', action='store_true', help='增量更新既有CSV')
    parser.add_argument('--tickers', nargs='+', default=["2330", "2308", "0050"])
    args = parser.parse_args()
    
    if args.update:
        update_data(tickers=args.tickers)
    else:
        fetch_data(tickers=args.tickers)
