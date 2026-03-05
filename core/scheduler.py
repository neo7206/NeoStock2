"""
NeoStock2 核心 — 台灣股市時間排程器

負責：
- 盤前（08:30）：登入、更新帳務、訂閱行情
- 開盤（09:00）：啟動策略引擎
- 盤中（09:00-13:25）：策略監控循環
- 收盤（13:30）：停止策略、結算
- 盤後（14:00）：推播帳務報告
"""

import logging
import threading
import time
from datetime import datetime, timedelta, date
from typing import Callable, Optional

logger = logging.getLogger("neostock2.core.scheduler")


class MarketScheduler:
    """台灣股市時間排程器"""

    # 台灣股市標準時段
    DEFAULT_PHASES = {
        "pre_market":  "08:30",
        "market_open": "09:00",
        "sim_trade_end": "13:25",
        "market_close": "13:30",
        "post_market": "14:00",
    }

    def __init__(self, settings: dict = None, market_data=None):
        self._settings = settings or {}
        self._phases = self._settings.get("scheduler", {}).get("phases", self.DEFAULT_PHASES)
        self._callbacks: dict[str, list[Callable]] = {
            "pre_market": [],
            "market_open": [],
            "sim_trade_end": [],
            "market_close": [],
            "post_market": [],
        }
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

        # 動態交易日判斷（透過 API 查 2330）
        self._market_data = market_data
        self._trading_day_cache: Optional[bool] = None
        self._trading_day_cache_date: Optional[date] = None

    def on(self, phase: str, callback: Callable):
        """
        註冊時段回呼

        Args:
            phase: 'pre_market', 'market_open', 'market_close', 'post_market'
            callback: 無引數回呼函式
        """
        if phase not in self._callbacks:
            raise ValueError(f"未知的交易時段: {phase}，可用: {list(self._callbacks.keys())}")
        self._callbacks[phase].append(callback)
        logger.info(f"已註冊 [{phase}] 回呼: {callback.__name__}")

    def is_trading_hours(self) -> bool:
        """
        判斷當前是否為交易時段

        Returns:
            True if 09:00 <= now < 13:30
        """
        now = datetime.now().strftime("%H:%M")
        open_time = self._phases.get("market_open", "09:00")
        close_time = self._phases.get("market_close", "13:30")
        return open_time <= now < close_time

    def is_trading_day(self) -> bool:
        """
        判斷今天是否為交易日（動態判斷，不依賴硬編碼假日表）

        策略：
        1. 週末直接回 False（無需打 API）
        2. 平日透過 API 查 2330 K 棒判斷（結果每日快取）
        3. API 不可用時 fallback 為只判斷週末
        """
        today = datetime.now().date()

        # 週末一定不是交易日
        if today.weekday() >= 5:
            return False

        # 檢查快取（同一天只查一次 API）
        if self._trading_day_cache_date == today and self._trading_day_cache is not None:
            return self._trading_day_cache

        # 透過 API 動態判斷
        result = self._check_trading_day_via_api(today)
        self._trading_day_cache = result
        self._trading_day_cache_date = today
        return result

    def _check_trading_day_via_api(self, today: date) -> bool:
        """
        透過 Shioaji API 查 2330 近 7 天 K 棒來判斷今天是否為交易日。
        
        邏輯：
        - 盤前 (< 09:00)：K 棒尚未產生，一律視為交易日（讓 pre_market 回呼先跑）
        - 盤中/盤後 (>= 09:00)：若今天有 K 棒 → 交易日；否則 → 假日
        - API 失敗時 fallback 視為交易日（避免因網路問題漏掉排程）
        """
        if self._market_data is None:
            logger.debug("無 market_data 模組，使用 fallback（僅排除週末）")
            return True

        now = datetime.now()

        # 盤前（09:00 之前）：K 棒要開盤後才有，無法確認，一律視為交易日
        if now.hour < 9:
            return True

        try:
            end_str = today.isoformat()
            start_str = (today - timedelta(days=7)).isoformat()
            df = self._market_data.get_kbars("2330", start=start_str, end=end_str)

            if df.empty:
                logger.warning("查詢 2330 K 棒無資料，假設為非交易日")
                return False

            latest_date = df.index.max().date()

            if latest_date == today:
                logger.info(f"✅ 確認今天 ({today}) 為交易日（2330 有 K 棒）")
                return True
            else:
                logger.info(f"⛔ 今天 ({today}) 非交易日（最後 K 棒: {latest_date}）")
                return False

        except Exception as e:
            logger.warning(f"API 查詢交易日失敗: {e}，fallback 視為交易日")
            return True

    def is_weekday(self) -> bool:
        """判斷今天是否為工作日（排除週六日，向下相容）"""
        return self.is_trading_day()

    def start(self):
        """啟動排程器"""
        if self._running:
            logger.warning("排程器已在運行中")
            return

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True,
            name="MarketScheduler"
        )
        self._thread.start()
        logger.info("✅ 市場排程器已啟動")

    def stop(self):
        """停止排程器"""
        self._stop_event.set()
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        logger.info("市場排程器已停止")

    def _scheduler_loop(self):
        """排程主迴圈"""
        triggered_today: set[str] = set()
        last_date = datetime.now().date()

        while not self._stop_event.is_set():
            now = datetime.now()
            now_time = now.strftime("%H:%M")
            today = now.date()

            # 日期變更 → 重置觸發記錄 + 清除交易日快取
            if today != last_date:
                triggered_today.clear()
                self._trading_day_cache = None
                self._trading_day_cache_date = None
                last_date = today
                logger.info(f"新的交易日: {today}")

            # 只在交易日執行
            if self.is_trading_day():
                for phase, target_time in self._phases.items():
                    if phase in self._callbacks and phase not in triggered_today:
                        if now_time >= target_time:
                            triggered_today.add(phase)
                            logger.info(f"🕐 觸發交易時段: [{phase}] ({target_time})")
                            self._execute_phase(phase)

            # 每 10 秒檢查一次
            self._stop_event.wait(10)

    def _execute_phase(self, phase: str):
        """執行指定時段的所有回呼"""
        callbacks = self._callbacks.get(phase, [])
        for cb in callbacks:
            try:
                cb()
            except Exception as e:
                logger.error(f"[{phase}] 回呼 {cb.__name__} 執行失敗: {e}")

    def get_status(self) -> dict:
        """取得排程器狀態"""
        now = datetime.now()
        return {
            "running": self._running,
            "is_trading_day": self.is_trading_day(),
            "is_trading_hours": self.is_trading_hours(),
            "current_time": now.strftime("%H:%M:%S"),
            "phases": self._phases,
            "registered_callbacks": {
                phase: len(cbs) for phase, cbs in self._callbacks.items()
            },
        }
