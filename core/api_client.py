"""
NeoStock2 核心 — Shioaji API 連線封裝

負責：
- API 登入與 CA 驗證
- 帳戶資訊查詢
- 連線狀態管理
"""

import os
import time
import logging
import threading
import yaml
import shioaji as sj
from dotenv import load_dotenv
from pathlib import Path

logger = logging.getLogger("neostock2.core.api_client")


def _silence_callback(context, msg):
    """Callback to silence Shioaji output"""
    pass

class ShioajiClient:
    """Shioaji API 連線封裝"""

    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self._api: sj.Shioaji | None = None
        self._accounts = None
        self._is_logged_in = False
        self._is_ca_activated = False
        self._settings = self._load_settings()
        self._load_env()
        self._contract_cache: dict = {}  # 合約快取

        # Auto-reconnect thread
        self._stop_event = threading.Event()
        self._reconnect_thread: threading.Thread | None = None
        self._on_reconnect_callbacks: list = []  # 重連後回呼清單

    def _load_settings(self) -> dict:
        """載入 settings.yaml"""
        settings_path = self.config_dir / "settings.yaml"
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}

    def _load_env(self):
        """載入 .env 環境變數"""
        env_path = self.config_dir / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    @property
    def api(self) -> sj.Shioaji:
        """取得 Shioaji API 實例"""
        if self._api is None:
            simulation = os.getenv("SHIOAJI_SIMULATION", "True").lower() == "true"
            self._api = sj.Shioaji(simulation=simulation)
            # 抑制預設的 context callback (避免印出 Response Code 200...)
            self._api.set_context(_silence_callback)
            logger.info(f"Shioaji API 實例已建立 (模擬模式: {simulation})")
        return self._api

    def _empty_callback(self, context, msg):
        """用於抑制 Shioaji 預設回呼的輸出"""
        pass

    @property
    def is_logged_in(self) -> bool:
        return self._is_logged_in

    @property
    def is_ca_activated(self) -> bool:
        return self._is_ca_activated

    @property
    def is_simulation(self) -> bool:
        """是否為模擬模式"""
        return os.getenv("SHIOAJI_SIMULATION", "True").lower() == "true"

    @property
    def account_id(self) -> str:
        """取得當前股票帳戶 ID（供回呼過濾用）"""
        try:
            return self.api.stock_account.account_id if self._is_logged_in else ""
        except Exception:
            return ""

    def login(self, api_key: str = None, secret_key: str = None, max_retries: int = 5) -> bool:
        """
        登入 Shioaji

        Args:
            api_key: API Key（若未提供則從環境變數讀取）
            secret_key: Secret Key（若未提供則從環境變數讀取）

        Returns:
            是否登入成功
        """
        api_key = api_key or os.getenv("SHIOAJI_API_KEY")
        secret_key = secret_key or os.getenv("SHIOAJI_SECRET_KEY")

        if not api_key or not secret_key:
            logger.error("缺少 API Key 或 Secret Key")
            return False

        for attempt in range(1, max_retries + 1):
            try:
                self._accounts = self.api.login(
                    api_key=api_key,
                    secret_key=secret_key,
                )
                self._is_logged_in = True
                
                # 確保登入後監聽器仍被抑制
                self.api.set_context(_silence_callback)
                
                # 抑制 Solace 連線事件的 stdout 輸出 (Session reconnecting/reconnected)
                try:
                    self.api.quote.set_on_event(lambda resp_code, event_code, info, event: None)
                except Exception:
                    pass  # 若 API 不支援則靜默跳過
                
                logger.info(f"登入成功，帳戶數量: {len(self._accounts)}")
                return True
            except Exception as e:
                logger.warning(f"登入嘗試 {attempt}/{max_retries} 失敗: {e}")
                if attempt < max_retries:
                    time.sleep(5)
                else:
                    logger.error(f"登入失敗：已重試 {max_retries} 次")
        return False

    def activate_ca(self, ca_path: str = None, ca_password: str = None) -> bool:
        """
        啟用 CA 憑證（下單必須）

        Args:
            ca_path: CA 憑證路徑
            ca_password: CA 憑證密碼

        Returns:
            是否啟用成功
        """
        if not self._is_logged_in:
            logger.error("請先登入")
            return False

        ca_path = ca_path or os.getenv("SHIOAJI_CA_PATH")
        ca_password = ca_password or os.getenv("SHIOAJI_CA_PASSWORD")

        if not ca_path or not ca_password:
            logger.error("缺少 CA 憑證路徑或密碼")
            return False

        try:
            self.api.activate_ca(
                ca_path=ca_path,
                ca_passwd=ca_password,
            )
            self._is_ca_activated = True
            logger.info("CA 憑證啟用成功")
            return True
        except Exception as e:
            logger.error(f"CA 憑證啟用失敗: {e}")
            return False

    def get_account_info(self) -> dict:
        """取得帳戶基本資訊"""
        if not self._is_logged_in:
            return {"error": "尚未登入"}

        stock_account = self.api.stock_account
        return {
            "person_id": stock_account.person_id,
            "broker_id": stock_account.broker_id,
            "account_id": stock_account.account_id,
            "signed": stock_account.signed,
        }

    def get_account_balance(self) -> dict:
        """查詢帳戶餘額"""
        if not self._is_logged_in:
            return {"error": "尚未登入"}

        try:
            balance = self.api.account_balance()
            # Shioaji AccountBalance 可能沒有 delivery_balance 屬性
            # acc_balance 通常是可用餘額
            return {
                "available_balance": getattr(balance, "acc_balance", 0),
                "settlement_amount": getattr(balance, "delivery_balance", 0), # 交割款
                "errmsg": getattr(balance, "errmsg", ""),
            }
        except Exception as e:
            logger.error(f"查詢餘額失敗: {e}")
            return {"error": str(e)}

    def get_positions(self) -> list[dict]:
        """查詢券商端持倉"""
        if not self._is_logged_in:
            return None

        try:
            positions = self.api.list_positions(self.api.stock_account)
            result = []
            for pos in positions:
                qty = pos.quantity                # 張數
                cost_price = pos.price            # 平均成本
                last_price = pos.last_price       # 當前市價 (API 直接提供)
                pnl = pos.pnl                     # 未實現損益

                total_cost = cost_price * qty * 1000
                market_value = last_price * qty * 1000
                pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

                # 從合約取得名稱
                name = ""
                try:
                    contract = self.api.Contracts.Stocks.get(pos.code)
                    if contract:
                        name = getattr(contract, 'name', '')
                except Exception:
                    pass

                result.append({
                    "code": pos.code,
                    "name": name,
                    "direction": str(pos.direction),
                    "quantity": qty,
                    "price": cost_price,
                    "last_price": round(last_price, 2),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "market_value": round(market_value, 2),
                    "yd_quantity": pos.yd_quantity,
                })
            return result
        except Exception as e:
            logger.warning(f"查詢持倉失敗（連線可能暫時中斷）: {e}")
            return None  # 回傳 None 表示查詢失敗，區別於空列表（無持倉）

    def get_profit_loss(self) -> list[dict] | None:
        """查詢券商端損益明細（每筆未平倉的買入記錄）"""
        if not self._is_logged_in:
            return None

        try:
            pnl_list = self.api.list_profit_loss(self.api.stock_account)
            result = []
            for item in pnl_list:
                result.append({
                    "code": getattr(item, "code", ""),
                    "quantity": getattr(item, "quantity", 0),
                    "price": getattr(item, "price", 0),
                    "last_price": getattr(item, "last_price", 0),
                    "pnl": getattr(item, "pnl", 0),
                    "pr_ratio": getattr(item, "pr_ratio", 0),
                    "cond": str(getattr(item, "cond", "")),
                    "date": str(getattr(item, "date", "")),
                    "dseq": getattr(item, "dseq", ""),
                    "direction": str(getattr(item, "direction", "")),
                    "entry_price": getattr(item, "entry_price", getattr(item, "price", 0)),
                    "fee": getattr(item, "fee", 0),
                    "tax": getattr(item, "tax", 0),
                })
            return result
        except Exception as e:
            logger.warning(f"查詢損益明細失敗: {e}")
            return None

    def get_settlements(self) -> list[dict]:
        """查詢交割資訊 (T/T+1/T+2)"""
        if not self._is_logged_in:
            return []

        try:
            settlements = self.api.settlements(self.api.stock_account)
            return [
                {
                    "date": getattr(s, "date", ""),
                    "amount": getattr(s, "amount", 0),
                    "T": getattr(s, "T", ""),
                }
                for s in settlements
            ]
        except Exception as e:
            logger.warning(f"查詢交割資訊失敗（模擬模式不支援）: {e}")
            return []

    def get_contract(self, symbol: str):
        """
        取得商品合約（含記憶體快取）

        Args:
            symbol: 股票代碼（如 '2330'）

        Returns:
            Shioaji Contract 物件
        """
        # 先查快取
        if symbol in self._contract_cache:
            return self._contract_cache[symbol]

        try:
            contract = self.api.Contracts.Stocks[symbol]
            if contract is None:
                logger.warning(f"找不到合約: {symbol}")
            else:
                self._contract_cache[symbol] = contract
            return contract
        except Exception as e:
            logger.error(f"取得合約失敗 ({symbol}): {e}")
            return None

    def check_connection(self) -> bool:
        """
        檢查連線狀態（使用實際 API 呼叫測試）
        
        Returns:
            是否連線正常
        """
        if not self._is_logged_in:
            return False
            
        try:
            # 使用 list_positions 做真實的網路呼叫，而非讀本地快取
            self.api.list_positions(self.api.stock_account)
            return True
        except Exception as e:
            logger.warning(f"連線檢測失敗: {e}")
            return False

    def on_reconnect(self, callback):
        """註冊重連成功後的回呼（用於重設委託回呼、行情訂閱等）"""
        self._on_reconnect_callbacks.append(callback)

    def reconnect(self) -> bool:
        """
        重新連線（登入 + CA + 觸發重連回呼）
        
        Returns:
            是否重連成功
        """
        logger.info("正在嘗試重新連線...")
        
        # 1. 先登出清理
        self.logout()
        
        # 2. 重新登入
        if self.login():
            # 3. 重新啟用 CA
            ca_path = os.getenv("SHIOAJI_CA_PATH")
            ca_pass = os.getenv("SHIOAJI_CA_PASSWORD")
            if ca_path and ca_pass:
                if not self.activate_ca(ca_path, ca_pass):
                    logger.warning("重連成功但 CA 啟用失敗")
            
            # 4. 觸發所有重連回呼（重設 order callback、行情訂閱等）
            for cb in self._on_reconnect_callbacks:
                try:
                    cb()
                except Exception as e:
                    logger.error(f"重連回呼執行失敗: {e}")
            
            logger.info("重連成功（已觸發回呼）")
            return True
            
        logger.error("重新連線失敗")
        return False

    def start_auto_reconnect(self, interval: int = 60):
        """
        啟動自動重連檢查
        
        Args:
            interval: 檢查間隔 (秒)
        """
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return

        def _check_loop():
            logger.info(f"自動重連機制已啟動 (間隔: {interval}s)")
            while not self._stop_event.is_set():
                time.sleep(interval)
                if self._stop_event.is_set():
                    break
                
                # 若應該登入但連線檢測失敗
                if self._is_logged_in:
                    if not self.check_connection():
                        logger.warning("檢測到連線中斷，嘗試自動重連...")
                        self.reconnect()
                    else:
                        # logger.debug("連線狀態正常")
                        pass

        self._reconnect_thread = threading.Thread(target=_check_loop, daemon=True, name="AutoReconnect")
        self._reconnect_thread.start()

    def logout(self):
        """登出"""
        self._stop_event.set() # 停止重連檢查
        if self._api is not None:
            try:
                self._api.logout()
                logger.info("已登出")
            except Exception as e:
                logger.warning(f"登出時發生錯誤: {e}")
            finally:
                self._is_logged_in = False
                self._is_ca_activated = False

    def __del__(self):
        self.logout()
