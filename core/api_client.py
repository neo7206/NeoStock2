"""
NeoStock2 核心 — Shioaji API 連線封裝

負責：
- API 登入與 CA 驗證
- 帳戶資訊查詢
- 連線狀態管理
"""

import os
import logging
import yaml
import shioaji as sj
from dotenv import load_dotenv
from pathlib import Path

logger = logging.getLogger("neostock2.core.api_client")


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
            logger.info(f"Shioaji API 實例已建立 (模擬模式: {simulation})")
        return self._api

    @property
    def is_logged_in(self) -> bool:
        return self._is_logged_in

    @property
    def is_ca_activated(self) -> bool:
        return self._is_ca_activated

    def login(self, api_key: str = None, secret_key: str = None) -> bool:
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

        try:
            self._accounts = self.api.login(
                api_key=api_key,
                secret_key=secret_key,
            )
            self._is_logged_in = True
            logger.info(f"登入成功，帳戶數量: {len(self._accounts)}")
            return True
        except Exception as e:
            logger.error(f"登入失敗: {e}")
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
            return {
                "available_balance": balance.acc_balance,
                "settlement_amount": balance.delivery_balance,
            }
        except Exception as e:
            logger.error(f"查詢餘額失敗: {e}")
            return {"error": str(e)}

    def get_positions(self) -> list[dict]:
        """查詢券商端持倉"""
        if not self._is_logged_in:
            return []

        try:
            positions = self.api.list_positions(self.api.stock_account)
            return [
                {
                    "code": pos.code,
                    "direction": pos.direction,
                    "quantity": pos.quantity,
                    "price": pos.price,
                    "pnl": pos.pnl,
                    "yd_quantity": pos.yd_quantity,
                }
                for pos in positions
            ]
        except Exception as e:
            logger.error(f"查詢持倉失敗: {e}")
            return []

    def get_contract(self, symbol: str):
        """
        取得商品合約

        Args:
            symbol: 股票代碼（如 '2330'）

        Returns:
            Shioaji Contract 物件
        """
        try:
            contract = self.api.Contracts.Stocks[symbol]
            if contract is None:
                logger.warning(f"找不到合約: {symbol}")
            return contract
        except Exception as e:
            logger.error(f"取得合約失敗 ({symbol}): {e}")
            return None

    def logout(self):
        """登出"""
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
