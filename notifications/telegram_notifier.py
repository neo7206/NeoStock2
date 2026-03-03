"""
NeoStock2 通知 — Telegram 通知模組

負責：
- 委託/成交即時推播
- 每日帳務報告
- 風控警報通知
"""

import logging
import requests
from datetime import datetime

logger = logging.getLogger("neostock2.notifications.telegram")


class TelegramNotifier:
    """Telegram Bot 通知器"""

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token
        self.chat_id = chat_id
        self._enabled = bool(token and chat_id)
        if self._enabled:
            logger.info("✅ Telegram 通知已啟用")
        else:
            logger.info("ℹ️ Telegram 通知未設定（需填入 token 和 chat_id）")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str) -> bool:
        """
        發送 Telegram 訊息

        Args:
            message: 訊息內容（支援 Markdown）
        """
        if not self._enabled:
            return False

        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "Markdown",
            }
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            else:
                logger.warning(f"Telegram 發送失敗: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Telegram 發送錯誤: {e}")
            return False

    def notify_order(self, order_data: dict):
        """推播委託狀態"""
        status_emoji = {
            "Submitted": "📝",
            "Filled": "✅",
            "Cancelled": "❌",
            "Failed": "🚫",
            "PartFilled": "🔄",
        }
        emoji = status_emoji.get(order_data.get("status", ""), "📋")
        msg = (
            f"{emoji} *委託通知*\n"
            f"標的: `{order_data.get('symbol', '')}`\n"
            f"方向: {order_data.get('action', '')}\n"
            f"數量: {order_data.get('quantity', 0)} 張\n"
            f"價格: {order_data.get('price', 0)}\n"
            f"狀態: {order_data.get('status', '')}\n"
            f"時間: {datetime.now().strftime('%H:%M:%S')}"
        )
        if order_data.get("msg"):
            msg += f"\n備註: {order_data['msg']}"
        self.send(msg)

    def notify_fill(self, fill_data: dict):
        """推播成交回報"""
        msg = (
            f"💰 *成交通知*\n"
            f"標的: `{fill_data.get('symbol', fill_data.get('code', ''))}`\n"
            f"方向: {fill_data.get('action', '')}\n"
            f"成交: {fill_data.get('quantity', 0)} 張 @ {fill_data.get('price', 0)}\n"
            f"時間: {datetime.now().strftime('%H:%M:%S')}"
        )
        self.send(msg)

    def notify_risk_alert(self, alert: dict):
        """推播風控警報"""
        trigger = alert.get("trigger", "")
        emoji = "🚨" if trigger == "stop_loss" else "🎯"
        msg = (
            f"{emoji} *風控警報*\n"
            f"觸發: {trigger}\n"
            f"標的: `{alert.get('code', '')}`\n"
            f"損益: {alert.get('current_pnl_pct', '')}\n"
            f"閾值: {alert.get('threshold', '')}"
        )
        self.send(msg)

    def daily_report(self, summary: dict):
        """推播每日帳務報告"""
        msg = (
            f"📊 *每日帳務報告*\n"
            f"日期: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"──────────\n"
            f"持倉檔數: {summary.get('position_count', 0)}\n"
            f"未實現損益: {summary.get('total_unrealized_pnl', 0):,.0f}\n"
            f"今日已實現: {summary.get('daily_realized_pnl', 0):,.0f}\n"
            f"今日虧損額: {summary.get('daily_loss', 0):,.0f}\n"
            f"──────────\n"
            f"風控狀態: {'🟢 正常' if not summary.get('halted') else '🔴 已停機'}"
        )
        self.send(msg)
