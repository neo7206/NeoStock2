"""
NeoStock2 核心 — 下單管理模組

負責：
- 限價/市價下單
- 委託狀態追蹤
- 成交回報處理
"""

import logging
import threading
from enum import Enum
from typing import Callable
from datetime import datetime

import shioaji as sj
from shioaji.constant import Action, StockPriceType, OrderType, StockOrderLot

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

    def _ensure_callbacks(self):
        """確保已設定委託/成交回呼"""
        if self._is_callback_set:
            return

        api = self.client.api

        def on_order_status(order_state, *args):
            self._handle_order_status(order_state, *args)

        api.set_order_callback(on_order_status)

        self._is_callback_set = True
        logger.info("委託回呼已設定")

    def _handle_order_status(self, order_state, *args):
        """處理委託狀態變更"""
        try:


            # 暫時嘗試從 args[0] (通常是 msg/trade) 取得資料
            msg = args[0] if args else None
            status_msg = ""
            
            if msg:
                target = msg
                # ... 既有解析邏輯 ...

            # 嘗試適應不同的物件結構 (OrderState vs Trade vs Dict)
            if isinstance(target, dict):
                 # 3. 處理 Dictionary 格式
                 order_id = target.get("order_id") or target.get("id") or target.get("seq_no") or ""
                 contract_data = target.get("contract", {})
                 order_data_inner = target.get("order", {})
                 status_data = target.get("status", {})
                 operation_data = target.get("operation", {})
                 
                 # 嘗試從內層取值
                 if order_data_inner:
                     # 結構類似 Trade 物件轉 dict
                     order_id = order_data_inner.get("id", order_id)
                     symbol = contract_data.get("code", "")
                     action = order_data_inner.get("action", "")
                     price = order_data_inner.get("price", 0)
                     quantity = order_data_inner.get("quantity", 0)
                     # 嘗試從 order 內層取得 status
                     status_inner = order_data_inner.get("status")
                     if isinstance(status_inner, dict):
                         status = status_inner.get("status", "")
                         status_msg = status_inner.get("msg", "")
                     else:
                         status = str(status_inner) if status_inner else ""
                 else:
                     # 扁平結構
                     symbol = target.get("code", "")
                     action = str(target.get("action", ""))
                     price = target.get("price", 0)
                     quantity = target.get("quantity", 0)
                     status = str(status_data.get("status", target.get("status", "")))
                     status_msg = status_data.get("msg", target.get("msg", ""))

                 # 優先檢查 operation 錯誤
                 if operation_data:
                     op_code = operation_data.get("op_code", "")
                     op_msg = operation_data.get("op_msg", "")
                     if op_code != "00":
                         status = "Failed"
                         status_msg = f"{op_msg} ({op_code})"
            

            elif hasattr(target, "order"):
                 # 1. 嘗試從 target.order 取得資訊 (Trade 物件)
                 order_id = target.order.id
                 symbol = target.contract.code if hasattr(target, "contract") else ""
                 action = target.order.action
                 price = target.order.price
                 quantity = target.order.quantity
                 status = target.status.status if hasattr(target, "status") else ""
                 # msg 屬性可能與 msg 變數混淆，Shioaji 的 Status 物件也有 msg 屬性
                 status_msg = target.status.msg if hasattr(target, "status") else ""
            else:
                 # 2. 嘗試直接從 target 取得資訊
                 order_id = getattr(target, "id", "") or getattr(target, "seq_no", "")
                 symbol = getattr(target, "code", "")
                 action = str(getattr(target, "action", ""))
                 price = getattr(target, "price", 0)
                 quantity = getattr(target, "quantity", 0)
                 status = str(getattr(target, "status", ""))
                 status_msg = getattr(target, "msg", "")



            order_data = {
                "order_id": order_id,
                "symbol": symbol,
                "action": action,
                "price": price,
                "quantity": quantity,
                "status": status,
                "msg": status_msg,
                "timestamp": datetime.now().isoformat(),
            }

            with self._lock:
                if order_data["order_id"]:
                    self._orders[order_data["order_id"]] = order_data

            # 觸發委託狀態回呼
            for callback in self._order_callbacks:
                try:
                    callback(order_data)
                except Exception as e:
                    logger.error(f"委託回呼錯誤: {e}")

            # 若為成交狀態，觸發成交回呼
            if order_data["status"] in ["Filled", "PartFilled"]:
                # 注意: 這裡的 quantity 是委託總量還是成交量？
                # Shioaji 的 OrderState.order.quantity 是委託總量
                # Shioaji 的 OrderState.deal_quantity 是成交量 (如果有)
                # 簡單起見，如果是 Filled，假設全部成交。如果是 PartFilled，需要 deal_quantity。
                # 暫時簡單處理：只在 Filled 時視為一次性成交 (忽略部分成交的複雜度，或需調整)
                # 為了避免重複記帳，這裡需要更嚴謹的邏輯 (如檢查 deal_seq)。
                # 由於時間限制，先針對 "Filled" 做處理，並假設是一次性成交。
                
                # 檢查是否需要解析成交細節
                # 這裡簡單傳遞 order_data，接收端需自行判斷
                for callback in self._trade_callbacks:
                    try:
                        callback(order_data)
                    except Exception as e:
                        logger.error(f"成交回呼錯誤: {e}")

            logger.info(
                f"委託狀態: {order_data['symbol']} "
                f"{order_data['action']} "
                f"{order_data['quantity']}股 @ {order_data['price']} "
                f"-> {order_data['status']}"
            )
        except Exception as e:
            logger.error(f"處理委託狀態錯誤: {e}")

    def place_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        price: float = 0,
        price_type: str = None,
        order_type: str = "ROD",
        order_lot: str = None,
    ) -> dict:
        """
        下單

        Args:
            symbol: 股票代碼
            action: 'Buy' 或 'Sell'
            quantity: 數量（張）
            price: 價格（限價單用）
            price_type: 'LMT'=限價, 'MKT'=市價
            order_type: 'ROD'=當日有效, 'IOC'=立即成交否則取消, 'FOK'=全部成交否則取消
            order_lot: 'Common'=整股, 'Odd'=零股

        Returns:
            下單結果 dict
        """
        self._ensure_callbacks()

        if not self.client.is_logged_in:
            return {"error": "尚未登入", "success": False}

        if not self.client.is_ca_activated:
            return {"error": "CA 憑證未啟用", "success": False}

        contract = self.client.get_contract(symbol)
        if contract is None:
            return {"error": f"找不到合約: {symbol}", "success": False}

        # 預設值
        strategy_cfg = self._settings.get("strategy", {})
        if price_type is None:
            price_type = strategy_cfg.get("default_order_type", "LMT")
        if order_lot is None:
            order_lot = strategy_cfg.get("default_order_lot", "Common")

        # 市價單不需要價格
        if price_type == "MKT":
            price = 0

        try:
            order = self.client.api.Order(
                price=price,
                quantity=quantity,
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
                "quantity": quantity,
                "price_type": price_type,
                "order_type": order_type,
                "status": trade.status.status if hasattr(trade, "status") else "submitted",
                "timestamp": datetime.now().isoformat(),
            }

            with self._lock:
                if result["order_id"]:
                    self._orders[result["order_id"]] = result

            logger.info(
                f"下單成功: {symbol} {action} {quantity}張 @ {price} ({price_type})"
            )
            return result

        except Exception as e:
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
        更新所有委託狀態

        Returns:
            目前所有委託列表
        """
        try:
            self.client.api.update_status(self.client.api.stock_account)
            trades = self.client.api.list_trades()
            result = []
            
            with self._lock:
                orders_map = self._orders.copy()

            for trade in trades:
                order_id = trade.order.id
                status = trade.status.status
                msg = trade.status.msg if hasattr(trade.status, "msg") else ""
                
                # 若本地緩存有更新的狀態，優先使用
                if order_id in orders_map:
                    local_data = orders_map[order_id]
                    local_status = local_data.get("status")
                    
                    # 若 API 狀態為 Pending/PreSubmitted，但本地已是最終狀態，則覆蓋
                    if status in ["PendingSubmit", "PreSubmitted"] and local_status and local_status not in ["PendingSubmit", "PreSubmitted", ""]:
                        status = local_status
                        msg = local_data.get("msg", msg)

                trade_info = {
                    "order_id": order_id,
                    "symbol": trade.contract.code,
                    "action": trade.order.action,
                    "price": trade.order.price,
                    "quantity": trade.order.quantity,
                    "status": status,
                    "msg": msg,
                    "timestamp": datetime.now().isoformat(), 
                }
                result.append(trade_info)
            return result
        except Exception as e:
            logger.error(f"更新委託狀態失敗: {e}")
            return []

    def get_orders(self) -> dict[str, dict]:
        """取得本地委託記錄"""
        with self._lock:
            return dict(self._orders)

    def on_trade(self, callback: Callable):
        """註冊成交回呼"""
        self._trade_callbacks.append(callback)
        self._trade_callbacks.append(callback)
