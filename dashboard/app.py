"""
NeoStock2 儀表板 — FastAPI 主應用

提供 Web API 與靜態頁面伺服。
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from dashboard.state import app_state  # noqa: F401

logger = logging.getLogger("neostock2.dashboard")


def create_app() -> FastAPI:
    """建立 FastAPI 應用"""
    # 延遲引入 routers，避免循環引入
    from dashboard.routers import market, trading, ledger, strategy, settings

    app = FastAPI(
        title="NeoStock2",
        description="永豐 Shioaji 自動策略下單工具",
        version="1.0.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由
    app.include_router(market.router, prefix="/api/market", tags=["行情"])
    app.include_router(trading.router, prefix="/api/trading", tags=["交易"])
    app.include_router(ledger.router, prefix="/api/ledger", tags=["帳本"])
    app.include_router(strategy.router, prefix="/api/strategy", tags=["策略"])
    app.include_router(settings.router, prefix="/api/settings", tags=["設定"])

    # 靜態檔案
    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # 首頁
    @app.get("/", response_class=HTMLResponse)
    async def index():
        template_path = Path(__file__).parent / "templates" / "index.html"
        if template_path.exists():
            return template_path.read_text(encoding="utf-8")
        return "<h1>NeoStock2</h1><p>儀表板載入中...</p>"

    # 健康檢查
    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "logged_in": (
                app_state["api_client"].is_logged_in
                if app_state["api_client"]
                else False
            ),
        }

    return app
