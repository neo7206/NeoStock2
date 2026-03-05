from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import List, Optional

from dashboard.state import app_state

router = APIRouter()

class HistoryStatus(BaseModel):
    symbol: str
    name: str = ""
    count: int
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    last_trading_day: Optional[str] = None
    timeframe: str

@router.get("/status", response_model=List[HistoryStatus])
async def get_history_status():
    """取得所有自選股的歷史數據狀態"""
    if not app_state["history_manager"]:
        raise HTTPException(status_code=503, detail="History Manager not initialized")
    
    manager = app_state["history_manager"]
    
    session = manager.db.get_session()
    from ledger.models import Watchlist, MarketData
    from sqlalchemy import func
    try:
        watchlist = session.query(Watchlist).order_by(Watchlist.sort_order).all()
        symbols = [w.symbol for w in watchlist]
        name_map = {w.symbol: w.name for w in watchlist}
        
        if not symbols:
            return []
        
        # 批量查詢所有 symbol 的歷史統計（避免 N+1）
        stats = session.query(
            MarketData.symbol,
            func.min(MarketData.datetime),
            func.max(MarketData.datetime),
            func.count(MarketData.id)
        ).filter(
            MarketData.symbol.in_(symbols),
            MarketData.timeframe == "1min"
        ).group_by(MarketData.symbol).all()
        
        stat_map = {s[0]: {"start": s[1], "end": s[2], "count": s[3]} for s in stats}
    finally:
        session.close()
    
    # 取得 last_trading_day（只查一次）
    last_td = manager.get_last_trading_day().isoformat()

    result = []
    for sym in symbols:
        s = stat_map.get(sym, {"start": None, "end": None, "count": 0})
        result.append(HistoryStatus(
            symbol=sym,
            name=name_map.get(sym, ""),
            count=s["count"],
            start_date=s["start"].isoformat() if s["start"] else None,
            end_date=s["end"].isoformat() if s["end"] else None,
            last_trading_day=last_td,
            timeframe="1min"
        ))
    return result

@router.post("/fetch/{symbol}")
async def fetch_history(symbol: str, background_tasks: BackgroundTasks):
    """觸發歷史數據抓取 (背景執行)"""
    if not app_state["history_manager"]:
        raise HTTPException(status_code=503, detail="History Manager not initialized")
    
    manager = app_state["history_manager"]
    
    # 使用 BackgroundTasks 避免阻塞 API
    background_tasks.add_task(manager.fetch_history_smart, symbol, months=3)
    
    return {"message": f"Started fetching history for {symbol}"}

@router.delete("/{symbol}")
async def delete_history(symbol: str):
    """刪除指定代碼的歷史數據"""
    if not app_state["history_manager"]:
        raise HTTPException(status_code=503, detail="History Manager not initialized")
    
    manager = app_state["history_manager"]
    try:
        count = manager.delete_history(symbol, timeframe="1min")
        return {"symbol": symbol, "deleted_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
