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
    symbols = manager.get_watchlist_symbols()
    
    # 這裡我們需要 symbol 對應的 name，目前 Watchlist table 有 name
    # 我們改用 session 查 Watchlist 比較快
    session = manager.db.get_session()
    from ledger.models import Watchlist
    watchlist = session.query(Watchlist).order_by(Watchlist.sort_order).all()
    session.close()
    
    result = []
    for item in watchlist:
        status = manager.get_history_status(item.symbol, timeframe="1min")
        result.append(HistoryStatus(
            symbol=item.symbol,
            name=item.name,
            count=status["count"],
            start_date=status["start_date"],
            end_date=status["end_date"],
            last_trading_day=status["last_trading_day"],
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
