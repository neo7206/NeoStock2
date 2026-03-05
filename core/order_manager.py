"""
NeoStock2 核心 — 下單管理模組

負責：
- 限價/市價下單
- 委託狀態追蹤
- 成交回報處理
"""

import logging
import queue
import time
import threading
from enum import Enum
from typing import Callable, Any
from datetime import datetime

import shioaji as sj
from shioaji.constant import Action, StockPriceType, OrderType, StockOrderLot, OrderState

logger = logging.getLogger("neostock2.core.order_manager")


class OrderAction(str, Enum):
    BUY = "Buy"
    SELL = "Sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderManager:
    """下單管理器"""

    def __init__(self, api_client, settings: dict = None):
        """
        Args:
            api_client: ShioajiClient 實例
            settings: settings.yaml 中的設定
        """
        self.client = api_client
        self._settings = settings or {}
        self._orders: dict[str, dict] = {}  # order_id -> order info
        self._trade_callbacks: list[Callable] = []
        self._order_callbacks: list[Callable] = []
        self._lock = threading.Lock()
        self._is_callback_set = False
        self._market_data = None  # 行情管理器 (注入用)
        self._risk_manager = None  # 風控管理器 (注入用)
        
        # --- Worker Setup ---
        self._event_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._worker, daemon=True, name="OrderManagerWorker")
        self._worker_thread.start()

    def _worker(self):
        """背景 Worker: 處理委託狀態更新"""
        logger.info("OrderManager Worker 已啟動")
        while not self._stop_event.is_set():
            try:
                # 阻塞直到有事件或超時 (方便退出)
                item = self._event_queue.get(timeout=1)
                self._process_order_event(item)
                self._event_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Worker 發生未預期錯誤: {e}")

    def stop(self):
        """停止 Worker"""
        self._stop_event.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)
        logger.info("OrderManager Worker 已停止")

    @staticmethod
    def _clean_enum(value) -> str:
        """將 Shioaji Enum 轉為乾淨字串，如 'Status.Filled' → 'Filled'"""
        s = str(value)
        return s.split(".")[-1] if "." in s else s

    def _ensure_callbacks(self):
        """確保已設定委託/成交回呼"""
        if self._is_callback_set:
            return

        api = self.client.api

        def on_order_status(order_state, msg):
            self._event_queue.put((order_state, msg))

        api.set_order_callback(on_order_status)

        self._is_callback_set = True
        logger.info("委託回呼已設定")

    def _process_order_event(self, item):
        """
        實際處理委託狀態 (在 Worker Thread 執行)
        
        Shioaji 回呼格式：
        - StockOrder: order_state=OrderState.StockOrder, msg=dict (委託回報)
        - StockDeal:  order_state=OrderState.StockDeal, msg=dict (成交回報)
        - FuturesOrder / FuturesDeal: 期貨相關 (預留)
        """
        order_state, msg = item
        try:
            state_name = self._clean_enum(order_state)

            # --- account_id 過濾：只處理自己帳戶的事件 ---
            my_account_id = self.client.account_id
            if my_account_id and isinstance(msg, dict):
                msg_account_id = ""
                if state_name == "StockOrder":
                    msg_account_id = msg.get("order", {}).get("account", {}).get("account_id", "")
                elif state_name == "StockDeal":
                    msg_account_id = msg.get("account_id", "")
                elif state_name in ["FuturesOrder", "FuturesDeal"]:
                    msg_account_id = msg.get("order", {}).get("account", {}).get("account_id", "")

                if msg_account_id and msg_account_id != my_account_id:
                    logger.debug(f"跳過非本帳戶事件: {msg_account_id}")
                    return

            # --- 依 OrderState 分流處理 ---
            if state_name == "StockOrder":
                self._handle_stock_order(msg)
            elif state_name == "StockDeal":
                self._handle_stock_deal(msg)
            elif state_name in ["FuturesOrder", "FuturesDeal"]:
                logger.info(f"收到期貨事件 ({state_name})，目前跳過")
            else:
                # 舊版相容：無法辨識的事件用舊邏輯解析
                self._handle_legacy_event(order_state, msg)

        except Exception as e:
            logger.error(f"處理委託狀態錯誤: {e}")

    def _handle_stock_order(self, msg: dict):
        """
        處理股票委託回報 (StockOrder)
        msg 格式: {"operation": {...}, "order": {...}, "status": {...}, "contract": {...}}
        """
        order_info = msg.get("order", {})
        contract_info = msg.get("contract", {})
        operation = msg.get("operation", {})
        status_info = msg.get("status", {})

        order_id = order_info.get("id", "")
        symbol = contract_info.get("code", "")
        action = self._clean_enum(order_info.get("action", ""))
        price = float(order_info.get("price", 0))
        quantity = int(order_info.get("quantity", 0))
        status = self._clean_enum(status_info.get("status", ""))
        status_msg = status_info.get("msg", "")

        # operation 判斷（優先）
        op_code = operation.get("op_code", "")
        op_msg = operation.get("op_msg", "")
        op_type = operation.get("op_type", "")

        if op_code and op_code != "00":
            status = "Failed"
            status_msg = f"{op_msg} ({op_code})"
        elif op_type == "Cancel":
            status = "Cancelled"
        elif op_type == "New" and not status:
            status = "Submitted"

        self._update_order_cache(order_id, symbol, action, price, quantity, status, status_msg)

    def _handle_stock_deal(self, msg: dict):
        """
        處理股票成交回報 (StockDeal)
        msg 格式: {"trade_id": ..., "code": ..., "action": ..., "price": ..., "quantity": ..., ...}
        注意：Shioaji StockDeal 的 quantity 單位為「張」（Common lot）
        """
        order_id = msg.get("order_id", msg.get("seq_no", ""))
        symbol = msg.get("code", "")
        action = self._clean_enum(msg.get("action", ""))
        price = float(msg.get("price", 0))
        raw_quantity = int(msg.get("quantity", 0))

        # 安全轉換：若 quantity 異常大（超過 1000），可能是「股」而非「張」
        if raw_quantity >= 1000 and raw_quantity % 1000 == 0:
            logger.warning(
                f"⚠️ 成交數量 {raw_quantity} 可能為股數，自動轉換為 {raw_quantity // 1000} 張"
            )
            quantity = raw_quantity // 1000
        else:
            quantity = raw_quantity

        status = "Filled"  # StockDeal 一定是成交
        status_msg = ""

        # === 反查原始委託：deal 的 order_id 可能與下單時不同 ===
        with self._lock:
            if order_id and order_id not in self._orders:
                # 嘗試根據 symbol + action 找到最近的待處理委託
                matched_id = None
                for oid, odata in self._orders.items():
                    if (odata.get("symbol") == symbol
                        and odata.get("action") == action
                        and odata.get("status") in [
                            "PendingSubmit", "PreSubmitted",
                            "Submitted", "submitted", ""
                        ]):
                        matched_id = oid
                        break  # 取第一筆匹配的

                if matched_id:
                    logger.info(
                        f"🔗 成交回報 order_id 反查: "
                        f"{order_id} → {matched_id} ({symbol} {action})"
                    )
                    order_id = matched_id

        self._update_order_cache(order_id, symbol, action, price, quantity, status, status_msg)

    def _handle_legacy_event(self, order_state, msg):
        """
        舊版相容：處理無法辨識 OrderState 的事件
        """
        target = msg if msg else order_state
        fallback_status = self._clean_enum(order_state)

        order_id = ""
        symbol = ""
        action = ""
        price = 0.0
        quantity = 0
        status = ""
        status_msg = ""

        if isinstance(target, dict):
            order_data_inner = target.get("order", {})
            contract_data = target.get("contract", {})
            status_data = target.get("status", {})
            order_id = target.get("order_id") or order_data_inner.get("id", "")
            symbol = contract_data.get("code", target.get("code", ""))
            action = self._clean_enum(order_data_inner.get("action", target.get("action", "")))
            price = float(order_data_inner.get("price", target.get("price", 0)))
            quantity = int(order_data_inner.get("quantity", target.get("quantity", 0)))
            status = self._clean_enum(status_data.get("status", target.get("status", "")))
            status_msg = status_data.get("msg", target.get("msg", ""))
        elif hasattr(target, "order"):
            order_id = target.order.id
            symbol = target.contract.code if hasattr(target, "contract") else ""
            action = self._clean_enum(target.order.action)
            price = float(target.order.price)
            quantity = int(target.order.quantity)
            status = self._clean_enum(target.status.status) if hasattr(target, "status") else ""
            status_msg = target.status.msg if hasattr(target, "status") else ""
        else:
            order_id = getattr(target, "id", "") or getattr(target, "seq_no", "")
            symbol = getattr(target, "code", "")
            action = self._clean_enum(getattr(target, "action", ""))
            price = float(getattr(target, "price", 0))
            quantity = int(getattr(target, "quantity", 0))
            status = self._clean_enum(getattr(target, "status", ""))
            status_msg = getattr(target, "msg", "")

        # fallback 狀態映射
        if not status and fallback_status:
            for keyword, mapped in [("Cancel", "Cancelled"), ("Fail", "Failed"), ("Submitted", "Submitted"), ("Filled", "Filled")]:
                if keyword in fallback_status:
                    status = mapped
                    break

        self._update_order_cache(order_id, symbol, action, price, quantity, status, status_msg)

    def _update_order_cache(
        self, order_id: str, symbol: str, action: str,
        price: float, quantity: int, status: str, msg: str
    ):
        """統一更新緩存 + 觸發回呼"""
        order_data = {
            "order_id": order_id,
            "symbol": symbol,
            "action": self._clean_enum(action),
            "price": price,
            "quantity": quantity,
            "status": self._clean_enum(status),
            "msg": msg,
            "timestamp": datetime.now().isoformat(),
        }

        # 更新本地緩存（不允許空白狀態覆蓋有效狀態）
        with self._lock:
            if order_data["order_id"]:
                existing = self._orders.get(order_data["order_id"])
                if existing and not order_data["status"] and existing.get("status"):
                    order_data["status"] = existing["status"]
                    order_data["msg"] = existing.get("msg", order_data["msg"])
                self._orders[order_data["order_id"]] = order_data

        # 觸發委託狀態回呼 (Web UI 等)
        for callback in self._order_callbacks:
            try:
                callback(order_data)
            except Exception as e:
                logger.error(f"委託回呼錯誤: {e}")

        # 若為成交狀態，觸發成交回呼 (寫入 DB)
        if order_data["status"] in ["Filled", "PartFilled"]:
            for callback in self._trade_callbacks:
                try:
                    callback(order_data)
                except Exception as e:
                    logger.error(f"成交回呼錯誤: {e}")

        # 根據狀態使用不同圖標和日誌等級
        s = order_data['status']
        label = f"{order_data['symbol']} {order_data['action']} {order_data['quantity']}張 @ {order_data['price']}"
        if s == 'Failed':
            logger.warning(f"❌ 委託失敗: {label} — {msg}")
        elif s == 'Filled':
            logger.info(f"✅ 已成交: {label}")
        elif s == 'PartFilled':
            logger.info(f"📦 部分成交: {label}")
        elif s == 'Cancelled':
            logger.info(f"🚫 已取消: {label}")
        elif s == 'Submitted':
            logger.info(f"📨 委託已接受: {label}")
        else:
            logger.info(f"📋 委託更新: {label} -> {s} ({msg})")

    def _cleanup_orders(self, max_keep: int = 200):
        """清理舊的委託快取，避免記憶體無限增長"""
        with self._lock:
            if len(self._orders) <= max_keep:
                return
            # 按 timestamp 排序，保留最新的 max_keep 筆
            sorted_ids = sorted(
                self._orders.keys(),
                key=lambda k: self._orders[k].get("timestamp", ""),
                reverse=True,
            )
            keep_ids = set(sorted_ids[:max_keep])
            removed = len(self._orders) - max_keep
            self._orders = {k: v for k, v in self._orders.items() if k in keep_ids}
            logger.info(f"🧹 已清理 {removed} 筆舊委託快取，保留 {max_keep} 筆")

    def set_market_data(self, market_data):
        """注入行情管理器（供五檔報價定價用）"""
        self._market_data = market_data

    def set_risk_manager(self, risk_manager):
        """注入風控管理器（下單前強制檢查用）"""
        self._risk_manager = risk_manager

    @staticmethod
    def _align_tick_size(price: float, action: str = "Buy") -> float:
        """
        將價格對齊到台股合法的升降單位

        台股升降單位規則：
        - < 10:      0.01
        - 10~50:     0.05
        - 50~100:    0.10
        - 100~500:   0.50
        - 500~1000:  1.00
        - >= 1000:   5.00

        Args:
            price: 原始價格
            action: 'Buy' 無條件進位 / 'Sell' 無條件捨去
        """
        import math
        if price <= 0:
            return price

        if price < 10:
            tick = 0.01
        elif price < 50:
            tick = 0.05
        elif price < 100:
            tick = 0.10
        elif price < 500:
            tick = 0.50
        elif price < 1000:
            tick = 1.00
        else:
            tick = 5.00

        # 買入進位、賣出捨去（對交易者有利）
        if action == "Buy":
            aligned = math.ceil(price / tick) * tick
        else:
            aligned = math.floor(price / tick) * tick

        return round(aligned, 2)

    def place_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float = 0,
        price_type: str = None,
        order_type: str = "ROD",
        order_lot: str = None,
        auto_price: bool = False,
        strategy_name: str = "manual",
    ) -> dict:
        """
        下單（支援五檔定價、批次拆單、零股自動判斷）

        Args:
            symbol: 股票代碼
            action: 'Buy' 或 'Sell'
            quantity: 數量（張）
            price: 價格（限價單用；auto_price=True 時自動從五檔取價）
            price_type: 'LMT'=限價, 'MKT'=市價
            order_type: 'ROD'/'IOC'/'FOK'
            order_lot: 'Common'=整股, 'Odd'=零股, None=自動判斷
            auto_price: True=自動從五檔報價取價
            strategy_name: 策略名稱（供記帳用）
        """
        self._ensure_callbacks()

        if not self.client.is_logged_in:
            return {"error": "尚未登入", "success": False}

        if not self.client.is_ca_activated:
            return {"error": "CA 憑證未啟用", "success": False}

        contract = self.client.get_contract(symbol)
        if contract is None:
            return {"error": f"找不到合約: {symbol}", "success": False}

        # --- 下單前風控檢查 ---
        if self._risk_manager:
            allowed, reason = self._risk_manager.check_order_risk(symbol, action, quantity, price)
            if not allowed:
                logger.warning(f"風控拒絕: {reason}")
                return {"error": f"風控拒絕: {reason}", "success": False}

        # 預設值
        strategy_cfg = self._settings.get("strategy", {})
        trading_cfg = self._settings.get("trading", {})
        if price_type is None:
            price_type = strategy_cfg.get("default_order_type", "LMT")

        # --- 零股/整股自動判斷 ---
        if order_lot is None:
            if quantity < 1:
                order_lot = "Odd"  # 零股
            else:
                order_lot = "Common"

        # --- 五檔報價定價 ---
        if auto_price and self._market_data and price == 0:
            bidask = self._market_data.get_latest_bidask(symbol)
            if bidask:
                if action == "Buy":
                    ask_prices = bidask.get("ask_price", [0])
                    price = ask_prices[0] if ask_prices else 0
                else:
                    bid_prices = bidask.get("bid_price", [0])
                    price = bid_prices[0] if bid_prices else 0
                if price > 0:
                    price_type = "LMT"
                    logger.info(f"五檔定價: {symbol} {action} @ {price}")

        # --- 尾盤自動改限價 ---
        sim_trade_end = trading_cfg.get("sim_trade_end", "13:25")
        now_time = datetime.now().strftime("%H:%M")
        if now_time >= sim_trade_end and price_type == "MKT":
            if self._market_data:
                bidask = self._market_data.get_latest_bidask(symbol)
                if bidask:
                    if action == "Buy":
                        ask_prices = bidask.get("ask_price", [0])
                        price = ask_prices[0] if ask_prices else 0
                    else:
                        bid_prices = bidask.get("bid_price", [0])
                        price = bid_prices[0] if bid_prices else 0
                    if price > 0:
                        price_type = "LMT"
                        logger.info(f"尾盤自動改限價: {symbol} @ {price}")

        # 市價單不需要價格
        if price_type == "MKT":
            price = 0

        # --- 價格對齊台股升降單位 ---
        if price_type == "LMT" and price > 0:
            aligned = self._align_tick_size(price, action)
            if aligned != price:
                logger.info(f"🔧 價格對齊升降單位: {symbol} {price} → {aligned}")
                price = aligned

        # --- 批次拆單 ---
        batch_size = trading_cfg.get("batch_size", 5)
        remaining = quantity
        all_results = []

        try:
            while remaining > 0:
                batch_qty = min(remaining, batch_size)
                order = self.client.api.Order(
                    price=price,
                    quantity=batch_qty,
                    action=Action.Buy if action == "Buy" else Action.Sell,
                    price_type=StockPriceType[price_type],
                    order_type=OrderType[order_type],
                    order_lot=StockOrderLot[order_lot],
                    account=self.client.api.stock_account,
                )

                trade = self.client.api.place_order(contract, order)

                result = {
                    "success": True,
                    "order_id": trade.order.id if hasattr(trade, "order") else "",
                    "symbol": symbol,
                    "action": action,
                    "price": price,
                    "quantity": batch_qty,
                    "price_type": price_type,
                    "order_type": order_type,
                    "status": self._clean_enum(trade.status.status) if hasattr(trade, "status") else "submitted",
                    "strategy_name": strategy_name,
                    "timestamp": datetime.now().isoformat(),
                }

                with self._lock:
                    if result["order_id"]:
                        self._orders[result["order_id"]] = result

                logger.info(
                    f"📤 委託已送出: {symbol} {action} {batch_qty}張 @ {price} ({price_type})"
                )

                all_results.append(result)
                remaining -= batch_qty

                # 多批時稍微間隔
                if remaining > 0:
                    time.sleep(0.1)

            # 下單後主動更新狀態確認
            try:
                time.sleep(0.2)
                self.client.api.update_status(self.client.api.stock_account)
            except Exception:
                pass

            # 定期清理舊委託快取
            self._cleanup_orders()

            # 若只有一批，直接回傳；多批回傳第一個加上 batches 資訊
            if len(all_results) == 1:
                return all_results[0]
            else:
                first = all_results[0].copy()
                first["quantity"] = quantity
                first["batches"] = len(all_results)
                first["batch_details"] = all_results
                return first

        except Exception as e:
            sent_qty = sum(r["quantity"] for r in all_results)
            if all_results:
                logger.error(
                    f"批次拆單部分失敗: 已送出 {sent_qty}/{quantity}張 "
                    f"({len(all_results)}批成功), 剩餘 {remaining}張失敗: {e}"
                )
                first = all_results[0].copy()
                first["quantity"] = sent_qty
                first["success"] = True
                first["partial_failure"] = True
                first["failed_quantity"] = remaining
                first["error_msg"] = str(e)
                first["batches"] = len(all_results)
                first["batch_details"] = all_results
                return first
            else:
                logger.error(f"下單失敗: {e}")
                return {"error": str(e), "success": False}

    def cancel_order(self, trade_or_order_id) -> dict:
        """
        取消委託
        
        Args:
            trade_or_order_id: Trade 物件或 order_id 字串
            
        Returns:
            取消結果 dict
        """
        try:
            self.client.api.cancel_order(trade_or_order_id)
            logger.info(f"取消委託成功")
            return {"success": True}
        except Exception as e:
            logger.error(f"取消委託失敗: {e}")
            return {"error": str(e), "success": False}

    def cancel_order_by_id(self, order_id: str) -> dict:
        """透過 Order ID 取消委託"""
        try:
            # 遍歷所有 Trades 尋找對應的 order_id
            self.client.api.update_status(self.client.api.stock_account)
            trades = self.client.api.list_trades()
            target_trade = next((t for t in trades if t.order.id == order_id), None)
            
            if not target_trade:
                return {"error": "找不到指定委託", "success": False}
                
            return self.cancel_order(target_trade)
        except Exception as e:
            logger.error(f"透過 ID 取消委託失敗: {e}")
            return {"error": str(e), "success": False}

    def update_status(self) -> list[dict]:
        """
        更新所有委託狀態（從 broker API 同步到本地 cache）

        Returns:
            目前所有委託列表
        """
        try:
            self.client.api.update_status(self.client.api.stock_account)
            trades = self.client.api.list_trades()
            result = []

            for trade in trades:
                order_id = trade.order.id
                status = self._clean_enum(trade.status.status)
                msg = trade.status.msg if hasattr(trade.status, "msg") else ""

                trade_info = {
                    "order_id": order_id,
                    "symbol": trade.contract.code,
                    "action": self._clean_enum(trade.order.action),
                    "price": trade.order.price,
                    "quantity": trade.order.quantity,
                    "status": status,
                    "msg": msg,
                    "timestamp": datetime.now().isoformat(),
                }

                # === 同步到本地 cache ===
                with self._lock:
                    existing = self._orders.get(order_id)
                    if existing:
                        local_status = existing.get("status", "")
                        # 優先使用較「終結」的狀態
                        # 若本地已是 Filled/Cancelled/Failed，保留本地狀態
                        final_states = {"Filled", "Cancelled", "Failed"}
                        if local_status in final_states:
                            trade_info["status"] = local_status
                            trade_info["msg"] = existing.get("msg", msg)
                        elif status in final_states:
                            # API 回傳終結狀態 → 更新本地
                            pass  # 使用 API 狀態
                        elif local_status and status in ["PendingSubmit", "PreSubmitted"]:
                            # API 還很早期，用本地較新的狀態
                            trade_info["status"] = local_status
                            trade_info["msg"] = existing.get("msg", msg)

                        # 保留策略名稱
                        if existing.get("strategy_name"):
                            trade_info["strategy_name"] = existing["strategy_name"]

                    # 寫回 cache
                    self._orders[order_id] = trade_info

                result.append(trade_info)

            # === 處理不在 API 結果中的本地 cache 條目 ===
            # (可能是 StockDeal 回報用了不同 order_id 寫入的條目)
            api_order_ids = {t.order.id for t in trades}
            with self._lock:
                for oid, odata in list(self._orders.items()):
                    if oid and oid not in api_order_ids:
                        # 本地有但 API 沒有的條目也要顯示
                        result.append(odata)

            return result
        except Exception as e:
            # 連線不穩時避免刷屏：每 5 分鐘才記一次 warning
            now = time.time()
            if now - getattr(self, '_last_update_warn', 0) > 300:
                logger.warning(f"更新委託狀態失敗（連線暫時中斷，此訊息每5分鐘最多一次）")
                self._last_update_warn = now
            # 回傳本地快取資料
            with self._lock:
                return list(self._orders.values())

    def get_orders(self) -> dict[str, dict]:
        """取得本地委託記錄"""
        with self._lock:
            return dict(self._orders)

    def on_trade(self, callback: Callable):
        """註冊成交回呼"""
        self._trade_callbacks.append(callback)

    def on_order(self, callback: Callable):
        """註冊委託狀態回呼（含失敗/取消等）"""
        self._order_callbacks.append(callback)
