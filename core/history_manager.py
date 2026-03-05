"""
NeoStock2 核心 — 歷史數據管理模組

負責：
- 管理自選股清單
- 抓取並儲存歷史 K 棒 (1分K, 日K)
- 提供歷史數據查詢
"""

import logging
from datetime import datetime, timedelta, date
from typing import List, Optional
import pandas as pd
from sqlalchemy.dialects.sqlite import insert

from ledger.database import Database
from ledger.models import MarketData, Watchlist
from core.market_data import MarketDataManager

logger = logging.getLogger("neostock2.core.history_manager")


class HistoryDataManager:
    def __init__(self, db: Database, market_data: MarketDataManager):
        self.db = db
        self.market_data = market_data
        
        # Smart Caching for last_trading_day
        self._last_trading_day_cache: Optional[date] = None
        self._last_trading_day_ts: datetime = datetime.min # timestamp of last fetch

    def get_watchlist_symbols(self) -> List[str]:
        """取得自選股代碼列表"""
        session = self.db.get_session()
        try:
            watchlist = session.query(Watchlist).order_by(Watchlist.sort_order).all()
            return [item.symbol for item in watchlist]
        finally:
            session.close()

    def fetch_and_store_history(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "1min",  # 1min or 1day
    ) -> int:
        """
        抓取並儲存歷史數據 (分批抓取，避免 Timeout)
        
        Args:
            symbol: 股票代碼
            start_date: YYYY-MM-DD
            end_date: YYYY-MM-DD
            timeframe: '1min' | '1day'
            
        Returns:
            新增/更新的筆數
        """
        import time
        
        current_date = datetime.strptime(start_date, "%Y-%m-%d")
        final_date = datetime.strptime(end_date, "%Y-%m-%d")
        total_count = 0
        
        logger.info(f"開始抓取 {symbol} 歷史數據 ({timeframe}): {start_date} ~ {end_date}")

        while current_date <= final_date:
            # 設定每次抓取的區間為 30 天 (避免 API Timeout)
            next_date = current_date + timedelta(days=30)
            
            # 確保區間不超過最終結束日期
            chunk_end_date = min(next_date, final_date)
            
            s_str = current_date.strftime("%Y-%m-%d")
            e_str = chunk_end_date.strftime("%Y-%m-%d")
            
            logger.info(f"抓取區間: {symbol} [{s_str} ~ {e_str}]")
            
            try:
                # 1. Fetch data
                df = self.market_data.get_kbars(symbol, start=s_str, end=e_str)
                
                if not df.empty:
                    # Normalize columns to lower case
                    df.columns = [c.lower() for c in df.columns]
                    logger.info(f"  -> Columns: {df.columns.tolist()}")
                     # 2. Resample if needed (1day)
                    if timeframe == '1day':
                         ohlc_dict = {
                            'Open': 'first',
                            'High': 'max',
                            'Low': 'min',
                            'Close': 'last',
                            'Volume': 'sum',
                            'Amount': 'sum'
                        }
                        
                         renamed_df = df.rename(columns={
                            'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 
                            'volume': 'Volume', 'amount': 'Amount'
                        })
                         
                         if 'Amount' not in renamed_df.columns:
                             renamed_df['Amount'] = 0
                        
                         daily_df = renamed_df.resample('1D').agg(ohlc_dict).dropna()
                         
                         # Restore structure
                         df = daily_df.rename(columns={
                            'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 
                            'Volume': 'volume', 'Amount': 'amount'
                         })

                    # 3. Store to DB
                    session = self.db.get_session()
                    try:
                        data_to_insert = []
                        for ts, row in df.iterrows():
                            amt = row.get('amount', 0)
                            record = {
                                "symbol": symbol,
                                "datetime": ts.to_pydatetime(),
                                "open": float(row['open']),
                                "high": float(row['high']),
                                "low": float(row['low']),
                                "close": float(row['close']),
                                "volume": int(row['volume']),
                                "amount": float(amt),
                                "timeframe": timeframe
                            }
                            data_to_insert.append(record)
                        
                        if data_to_insert:
                            # Batch insert to avoid SQLite parameter limit
                            batch_size = 500
                            for i in range(0, len(data_to_insert), batch_size):
                                batch = data_to_insert[i : i + batch_size]
                                stmt = insert(MarketData).values(batch)
                                stmt = stmt.on_conflict_do_update(
                                    index_elements=['symbol', 'datetime', 'timeframe'],
                                    set_={
                                        "open": stmt.excluded.open,
                                        "high": stmt.excluded.high,
                                        "low": stmt.excluded.low,
                                        "close": stmt.excluded.close,
                                        "volume": stmt.excluded.volume,
                                        "amount": stmt.excluded.amount,
                                    }
                                )
                                session.execute(stmt)
                                session.commit()
                                logger.info(f"  -> 已儲存批次 {i} ~ {i+len(batch)} / {len(data_to_insert)}")
                            
                            count = len(data_to_insert)
                            total_count += count
                    except Exception as e:
                        session.rollback()
                        logger.error(f"  -> 儲存失敗: {e}")
                    finally:
                        session.close()
                else:
                    logger.info(f"  -> 無數據")

            except Exception as e:
                 logger.error(f"抓取失敗 ({s_str} ~ {e_str}): {e}")

            # 推進日期 (避免重複抓取同一天，下一次從 chunk_end_date + 1 天開始? 
            # 但 Shioaji kbars start/end 是 inclusive，如果 next_date 是 30天後，
            # 若我們直接用 next_date 當下一輪 start，會重疊一天嗎?
            # 假設 current=1, next=31. fetch 1~31. 
            # Set current = next (31). fetch 31~... -> 重疊 31 號.
            # 為了避免重疊，我們可以讓下一輪 start = chunk_end_date + 1 day.
            # 但如果 chunk_end_date >= final_date，迴圈就該結束了。
            
            if chunk_end_date >= final_date:
                break
                
            current_date = chunk_end_date + timedelta(days=1)
            
            # 禮貌性暫停
            time.sleep(0.5)
            
        logger.info(f"歷史數據抓取完成 {symbol}: 總共 {total_count} 筆")
        return total_count

    def update_all_watchlist_history(
        self,
        days: int = 5,
        timeframe: str = "1min"
    ):
        """更新所有自選股的歷史數據"""
        symbols = self.get_watchlist_symbols()
        start_date = (date.today() - timedelta(days=days)).isoformat()
        end_date = date.today().isoformat()
        
        logger.info(f"準備更新自選股歷史數據: {symbols}, {start_date} ~ {end_date}")
        
        for symbol in symbols:
            try:
                self.fetch_and_store_history(symbol, start_date, end_date, timeframe)
            except Exception as e:
                logger.error(f"更新 {symbol} 失敗: {e}")

    def get_history(
        self,
        symbol: str, 
        start_date: Optional[datetime] = None, 
        end_date: Optional[datetime] = None, 
        timeframe: str = "1min"
    ) -> pd.DataFrame:
        """從資料庫讀取歷史數據"""
        session = self.db.get_session()
        try:
            q = session.query(MarketData).filter(
                MarketData.symbol == symbol,
                MarketData.timeframe == timeframe
            )
            if start_date:
                q = q.filter(MarketData.datetime >= start_date)
            if end_date:
                q = q.filter(MarketData.datetime <= end_date)
            
            q = q.order_by(MarketData.datetime.asc())
            rows = q.all()
            
            if not rows:
                return pd.DataFrame()
            
            data = [r.to_dict() for r in rows]
            df = pd.DataFrame(data)
            df['datetime'] = pd.to_datetime(df['datetime'])
            df.set_index('datetime', inplace=True)
            return df
            
        finally:
            session.close()

    def get_history_status(self, symbol: str, timeframe: str = "1min") -> dict:
        """
        取得該標的的歷史數據狀態
        Returns:
            {
                "symbol": str,
                "count": int,
                "start_date": str | None,
                "end_date": str | None
            }
        """
        session = self.db.get_session()
        try:
            # 取得最早和最晚時間，以及總筆數
            from sqlalchemy import func
            q = session.query(
                func.min(MarketData.datetime),
                func.max(MarketData.datetime),
                func.count(MarketData.id)
            ).filter(
                MarketData.symbol == symbol,
                MarketData.timeframe == timeframe
            )
            min_dt, max_dt, count = q.first()
            
            return {
                "symbol": symbol,
                "count": count,
                "start_date": min_dt.isoformat() if min_dt else None,
                "end_date": max_dt.isoformat() if max_dt else None,
                "last_trading_day": self.get_last_trading_day().isoformat(),
                "timeframe": timeframe
            }
        finally:
            session.close()

    def fetch_history_smart(self, symbol: str, months: int = 3, timeframe: str = "1min") -> int:
        """
        智慧抓取歷史數據
        - 若無數據：抓取過去 N 個月
        - 若有數據：抓取 (最後日期+1天) ~ 今天
        """
        status = self.get_history_status(symbol, timeframe)
        today = date.today()
        
        if status["count"] == 0 or not status["end_date"]:
            # 無數據 => 抓過去 N 個月
            start_date = (today - timedelta(days=30*months)).isoformat()
            end_date = today.isoformat()
            logger.info(f"智慧抓取 {symbol}: 無數據，抓取過去 {months} 個月 ({start_date} ~ {end_date})")
            return self.fetch_and_store_history(symbol, start_date, end_date, timeframe)
        else:
            # 有數據 => 增量更新
            last_date = datetime.fromisoformat(status["end_date"]).date()
            if last_date >= today:
                logger.info(f"智慧抓取 {symbol}: 數據已是最新 ({last_date})")
                return 0
            
            # 從最後日期的隔天開始抓
            start_date = (last_date + timedelta(days=1)).isoformat()
            end_date = today.isoformat()
            
            # double check we are not fetching future
            if start_date > end_date:
                return 0
                
            logger.info(f"智慧抓取 {symbol}: 增量更新 ({start_date} ~ {end_date})")
            return self.fetch_and_store_history(symbol, start_date, end_date, timeframe)

    def delete_history(self, symbol: str, timeframe: str = "1min") -> int:
        """刪除指定代碼的歷史數據"""
        session = self.db.get_session()
        try:
            deleted = session.query(MarketData).filter(
                MarketData.symbol == symbol,
                MarketData.timeframe == timeframe
            ).delete()
            session.commit()
            logger.info(f"已刪除 {symbol} ({timeframe}) 歷史數據: {deleted} 筆")
            return deleted
        except Exception as e:
            session.rollback()
            logger.error(f"刪除失敗 {symbol}: {e}")
            raise e
        finally:
            session.close()

    def get_last_trading_day(self) -> date:
        """
        取得「最後交易日」 (Smart Caching)
        策略:
        1. 啟動時: 強制抓一次 API (2330)
        2. 平日盤中 (09:00~13:45): 使用快取 (如果是昨天的也沒關係，因為今天還沒收盤)
        3. 平日盤後 (>=13:45): 
           - 檢查快取時間是否在今天 13:45 之後? 
           - 若否 (代表是盤前抓的 or 舊的)，強制重抓一次，確認今天是否已收盤產生 K 棒
        4. 假日: 使用快取 (通常週五收盤後抓到的就是最新的)
        """
        now = datetime.now()
        today = now.date()
        
        # 判斷收盤時間 (13:45)
        cutoff_time = now.replace(hour=13, minute=45, second=0, microsecond=0)
        
        # 決定是否需要強制重抓
        need_refresh = False
        
        # 1. 尚未有快取 (啟動時)
        if self._last_trading_day_cache is None:
            need_refresh = True
            
        # 2. 盤後檢查: 如果現在已經過了 13:45，但快取時間是在 13:45 之前 (代表可能是盤中或昨天抓的)
        #    我們需要確認今天到底有沒有 K 棒 (是否為交易日)
        elif now >= cutoff_time:
            if self._last_trading_day_ts < cutoff_time:
                need_refresh = True
                
        # 如果不需要重抓，直接回傳快取
        if not need_refresh and self._last_trading_day_cache:
            return self._last_trading_day_cache

        # --- 執行 API 抓取 (以 2330 台積電為基準) ---
        target_symbol = "2330" 
        try:
             # 抓過去 7 天的 K 棒
             end_str = today.isoformat()
             start_str = (today - timedelta(days=7)).isoformat()
             
             logger.info(f"正在更新最後交易日 (基準: {target_symbol})...")
             
             # 注意：這裡呼叫 market_data.get_kbars，它內部是用 Shioaji API
             df = self.market_data.get_kbars(target_symbol, start=start_str, end=end_str)
             
             if not df.empty:
                 # df index is datetime (ts)
                 latest_ts = df.index.max()
                 latest_date = latest_ts.date()
                 
                 self._last_trading_day_cache = latest_date
                 self._last_trading_day_ts = now
                 logger.info(f"最後交易日更新成功: {latest_date} (Cache Updated)")
                 return latest_date
             else:
                 logger.warning(f"無法取得 {target_symbol} K棒，將使用 fallback邏輯")
                 
        except Exception as e:
            logger.error(f"更新最後交易日失敗: {e}，將使用 fallback邏輯")
            
        # --- Fallback: 使用原本的日期推算邏輯 ---
        # 如果 API 失敗，回退到原本的邏輯，並且不更新 cache (讓下次還有機會重試)
        logger.info("使用 Fallback 日期推算邏輯")
        target_date = today
        
        if now.time() < cutoff_time.time():
            target_date -= timedelta(days=1)
            
        while target_date.weekday() > 4: # 5=Sat, 6=Sun
            target_date -= timedelta(days=1)
            
        # 為了避免 API 一直掛掉導致每次都重算，我們也暫時快取這個 fallback 結果?
        # 不，fallback 就動態算就好，以免錯過 API 恢復的時機
        # 但為了介面一致性，如果 cache 是空的，先暫存 fallback 值
        if self._last_trading_day_cache is None:
            self._last_trading_day_cache = target_date
            # timestamp 不更新，這樣下次還會嘗試 API? 
            # 或是設一個短一點的 expire? 
            # 簡單做: 設為 now，讓它符合上面的規則
            self._last_trading_day_ts = now
            
        return target_date
