"""
NeoStock2 — 主程式入口

負責：
1. 讀取設定
2. 初始化所有模組
3. 啟動 FastAPI 伺服器
"""

import logging
import socket
import sys
import os
import webbrowser
from pathlib import Path

import yaml
import uvicorn
from dotenv import load_dotenv

# === 日誌設定 ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("neostock2.main")


def load_settings(config_dir: str = "config") -> dict:
    """載入設定檔"""
    settings_path = Path(config_dir) / "settings.yaml"
    if settings_path.exists():
        with open(settings_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    logger.warning(f"設定檔不存在: {settings_path}")
    return {}


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """檢查 port 是否已被佔用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def kill_port_process(port: int) -> bool:
    """強制終止佔用指定 port 的程序 (Windows)"""
    import subprocess
    import time

    try:
        # 用 netstat 找出佔用 port 的 PID
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=5,
        )
        pids = set()
        for line in result.stdout.splitlines():
            if f":{port}" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts:
                    try:
                        pid = int(parts[-1])
                        if pid > 0:
                            pids.add(pid)
                    except ValueError:
                        pass

        if not pids:
            print(f"找不到佔用 port {port} 的程序")
            return False

        for pid in pids:
            print(f"🔪 終止程序 PID={pid}...")
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, timeout=5,
            )

        # 等待 port 釋放
        for _ in range(10):
            time.sleep(0.5)
            if not is_port_in_use(port):
                print(f"✅ Port {port} 已釋放")
                return True

        print(f"⚠️ Port {port} 仍被佔用，請手動處理")
        return False

    except Exception as e:
        print(f"❌ 終止程序失敗: {e}")
        return False


def main():
    """主流程"""
    import argparse

    parser = argparse.ArgumentParser(description="NeoStock2 — 自動策略下單工具")
    parser.add_argument("-f", "--force", action="store_true",
                        help="強制啟動：終止已在運行的舊程序後重新啟動")
    args = parser.parse_args()

    # === 防重複啟動 ===
    settings_early = load_settings()
    port = settings_early.get("dashboard", {}).get("port", 8000)

    if is_port_in_use(port):
        if args.force:
            print()
            print("=" * 50)
            print(f"🔄 強制模式：終止舊的 NeoStock2 (port {port})...")
            print("=" * 50)
            if not kill_port_process(port):
                sys.exit(1)
            print()
        else:
            url = f"http://localhost:{port}"
            print()
            print("=" * 50)
            print(f"⚠️  NeoStock2 已經在運行中！(port {port})")
            print(f"🌐 儀表板位址: {url}")
            print(f"💡 使用 python main.py -f 可強制重啟")
            print("=" * 50)
            print()
            print("正在為您開啟瀏覽器...")
            webbrowser.open(url)
            sys.exit(0)

    logger.info("=" * 50)
    logger.info("NeoStock2 — 自動策略下單工具 啟動中...")
    logger.info("=" * 50)

    # 載入環境變數
    env_path = Path("config/.env")
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("✅ 環境變數已載入")
    else:
        logger.warning("⚠️ config/.env 不存在，請參考 config/.env.template 建立")

    # 載入設定
    settings = load_settings()
    logger.info("✅ 設定檔已載入")

    # --- 初始化核心模組 ---
    from core.api_client import ShioajiClient
    from core.market_data import MarketDataManager
    from core.order_manager import OrderManager
    from ledger.database import Database
    from ledger.portfolio import Portfolio
    from ledger.risk_manager import RiskManager
    from ledger.roi_calculator import ROICalculator
    from strategies.strategy_engine import StrategyEngine
    from dashboard.app import create_app
    from dashboard.state import app_state

    # 1. 資料庫
    db_path = settings.get("database", {}).get("path", "data/neostock2.db")
    db = Database(db_path)
    logger.info("✅ 資料庫已初始化")

    # 2. API 客戶端
    client = ShioajiClient(config_dir="config")
    logger.info("✅ Shioaji 客戶端已建立")

    # 3. 行情管理
    market_data = MarketDataManager(client)
    logger.info("✅ 行情管理已初始化")

    # 4. 下單管理
    order_manager = OrderManager(client, settings=settings)
    logger.info("✅ 下單管理已初始化")

    # 5. 帳本
    portfolio = Portfolio(db, settings=settings)
    logger.info("✅ 帳本已初始化")

    # 6. 風險管理
    risk_manager = RiskManager(db, settings=settings)
    logger.info("✅ 風險管理已初始化")

    # 7. ROI 計算
    roi_calc = ROICalculator(db)
    logger.info("✅ ROI 計算已初始化")

    # 8. 策略引擎
    strategy_engine = StrategyEngine(
        order_manager=order_manager,
        portfolio=portfolio,
        risk_manager=risk_manager,
        settings=settings,
    )
    logger.info("✅ 策略引擎已初始化")

    # 行情 → 策略引擎連動
    market_data.on_tick(lambda tick: strategy_engine.process_tick(tick))

    # --- 注入服務到 Web 層 ---
    app_state["db"] = db
    app_state["api_client"] = client
    app_state["market_data"] = market_data
    app_state["order_manager"] = order_manager
    app_state["portfolio"] = portfolio
    app_state["risk_manager"] = risk_manager
    app_state["roi_calculator"] = roi_calc
    app_state["strategy_engine"] = strategy_engine

    # --- 串接事件回呼 ---
    def on_trade_filled(order_data: dict):
        """處理成交回報，寫入帳本"""
        try:
            # 避免重複記帳的簡單檢查 (實際應檢查 DB)
            # 這裡假設 Filled 是最終狀態且只觸發一次 (需依賴 Shioaji 行為)
            logger.info(f"收到成交回報，寫入帳本: {order_data}")
            portfolio.record_trade(
                code=order_data["symbol"],
                action=order_data["action"],
                price=order_data["price"],
                quantity=order_data["quantity"],
                strategy_name="manual", # 或從 order_data 判斷
                order_id=order_data["order_id"],
                note=f"Auto-recorded from {order_data['status']}"
            )
        except Exception as e:
            logger.error(f"寫入成交記錄失敗: {e}")

    order_manager.on_trade(on_trade_filled)

    # --- 自動登入 ---
    api_key = os.getenv("SHIOAJI_API_KEY")
    secret_key = os.getenv("SHIOAJI_SECRET_KEY")
    if api_key and secret_key:
        try:
            client.login(api_key, secret_key)
            logger.info("✅ Shioaji 已自動登入")

            ca_path = os.getenv("SHIOAJI_CA_PATH")
            ca_pass = os.getenv("SHIOAJI_CA_PASSWORD")
            if ca_path:
                client.activate_ca(ca_path, ca_pass)
                logger.info("✅ CA 憑證已啟用")
        except Exception as e:
            logger.error(f"❌ 自動登入失敗: {e}")
    else:
        logger.info("ℹ️ 未設定 API Key，跳過自動登入（請透過儀表板手動操作）")

    # --- 自選股自動訂閱 (Streaming) ---
    try:
        from ledger.models import Watchlist
        session = db.get_session()
        watchlists = session.query(Watchlist).all()
        symbols = [w.symbol for w in watchlists]
        session.close()

        if symbols and client.is_logged_in:
            logger.info(f"📜 載入自選股清單: {len(symbols)} 檔，啟動即時訂閱...")
            market_data.init_quote_cache(symbols)
    except Exception as e:
        logger.error(f"❌ 自選股訂閱失敗: {e}")

    # --- 啟動 Web 伺服器 ---
    app = create_app()
    dashboard_cfg = settings.get("dashboard", {})
    host = dashboard_cfg.get("host", "0.0.0.0")
    port = dashboard_cfg.get("port", 8000)

    logger.info(f"🌐 儀表板啟動: http://localhost:{port}")
    logger.info("=" * 50)

    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
