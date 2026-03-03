"""
NeoStock2 儀表板路由 — 策略研究 API

提供：
- POST /start — 啟動研究任務
- GET /status/{task_id} — 查詢進度
- GET /results/{ticker} — 取得研究結果
- GET /data-status/{ticker} — 檢查資料是否已下載
- GET /templates — 取得可用策略模板清單
"""

import uuid
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from research.research_runner import (
    run_full_research,
    get_progress,
    check_data_exists,
    get_results,
)
from research.strategies import STRATEGY_TEMPLATES

router = APIRouter()


class ResearchStartRequest(BaseModel):
    ticker: str


@router.post("/start")
async def start_research(req: ResearchStartRequest):
    """啟動研究任務（背景執行）"""
    task_id = str(uuid.uuid4())[:8]

    # 用 Thread 執行長時間運算（避免 blocking event loop）
    t = threading.Thread(
        target=run_full_research,
        args=(req.ticker, task_id),
        daemon=True,
    )
    t.start()

    return {
        "task_id": task_id,
        "message": f"研究任務已啟動: {req.ticker}",
    }


@router.get("/status/{task_id}")
async def research_status(task_id: str):
    """查詢研究進度"""
    progress = get_progress(task_id)
    return progress


@router.get("/results/{ticker}")
async def research_results(ticker: str):
    """取得研究結果"""
    results = get_results(ticker)
    if results is None:
        raise HTTPException(status_code=404, detail=f"{ticker} 尚無研究結果")
    return results


@router.get("/data-status/{ticker}")
async def data_status(ticker: str):
    """檢查資料是否已下載"""
    return check_data_exists(ticker)


@router.get("/templates")
async def list_templates():
    """取得可用策略模板清單"""
    return {
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "param_count": sum(len(v) for v in t.param_grid.values()),
            }
            for t in STRATEGY_TEMPLATES.values()
        ]
    }
