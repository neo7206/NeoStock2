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
from typing import Callable

logger = logging.getLogger("neostock2.core.scheduler")

# 台灣股市固定假日（每年更新）
TW_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # 元旦
    date(2026, 1, 2),   # 元旦補假
    date(2026, 2, 14),  # 除夕前一日（調整放假）
    date(2026, 2, 15),  # 除夕
    date(2026, 2, 16),  # 春節
    date(2026, 2, 17),  # 春節
    date(2026, 2, 18),  # 春節
    date(2026, 2, 19),  # 春節
    date(2026, 2, 20),  # 春節
    date(2026, 2, 28),  # 和平紀念日
    date(2026, 3, 27),  # 兒童節（調整放假）
    date(2026, 4, 3),   # 歡感節
    date(2026, 4, 4),   # 兒童節
    date(2026, 4, 5),   # 清明節
    date(2026, 5, 1),   # 勞動節
    date(2026, 5, 31),  # 端午節
    date(2026, 6, 1),   # 端午節補假
    date(2026, 10, 1),  # 中秋節
    date(2026, 10, 2),  # 中秋節補假
    date(2026, 10, 10), # 國慶日
}


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

    def __init__(self, settings: dict = None):
        self._settings = settings or {}
        self._phases = self._settings.get("scheduler", {}).get("phases", self.DEFAULT_PHASES)
        self._callbacks: dict[str, list[Callable]] = {
            "pre_market": [],
            "market_open": [],
            "market_close": [],
            "post_market": [],
        }
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

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
        """判斷今天是否為交易日（排除週末和國定假日）"""
        today = datetime.now()
        if today.weekday() >= 5:  # 0=Mon, 4=Fri, 5=Sat, 6=Sun
            return False
        if today.date() in TW_HOLIDAYS_2026:
            return False
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

            # 日期變更 → 重置觸發記錄
            if today != last_date:
                triggered_today.clear()
                last_date = today
                logger.info(f"新的交易日: {today}")

            # 只在工作日執行
            if self.is_weekday():
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
            "is_trading_hours": self.is_trading_hours(),
            "is_weekday": self.is_weekday(),
            "current_time": now.strftime("%H:%M:%S"),
            "phases": self._phases,
            "registered_callbacks": {
                phase: len(cbs) for phase, cbs in self._callbacks.items()
            },
        }
