"""
NeoStock2 儀表板路由 — 行情 API
"""

from fastapi import APIRouter, HTTPException, Query
from dashboard.state import app_state

router = APIRouter()


@router.get("/snapshot")
async def get_snapshot(symbols: str = Query(..., description="逗號分隔的股票代碼")):
    """取得行情快照"""
    md = app_state.get("market_data")
    if md is None:
        raise HTTPException(status_code=503, detail="行情服務未啟動")

    symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="請提供股票代碼")

    snapshots = md.get_snapshot(symbol_list)
    return {"data": snapshots}


@router.get("/tick/{symbol}")
async def get_latest_tick(symbol: str):
    """取得最新 Tick"""
    md = app_state.get("market_data")
    if md is None:
        raise HTTPException(status_code=503, detail="行情服務未啟動")

    tick = md.get_latest_tick(symbol)
    if tick is None:
        return {"data": None, "message": f"{symbol} 尚無訂閱數據"}
    return {"data": tick}


@router.get("/kbars/{symbol}")
async def get_kbars(
    symbol: str,
    start: str = Query(None, description="起始日期 YYYY-MM-DD"),
    end: str = Query(None, description="結束日期 YYYY-MM-DD"),
):
    """取得 K 棒歷史數據"""
    md = app_state.get("market_data")
    if md is None:
        raise HTTPException(status_code=503, detail="行情服務未啟動")

    df = md.get_kbars(symbol, start=start, end=end)
    if df.empty:
        return {"data": [], "message": "無數據"}

    records = df.reset_index().to_dict(orient="records")
    # 轉換 Timestamp 為字串
    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    return {"data": records}


@router.post("/subscribe/{symbol}")
async def subscribe(symbol: str, quote_type: str = "tick"):
    """訂閱即時行情"""
    md = app_state.get("market_data")
    if md is None:
        raise HTTPException(status_code=503, detail="行情服務未啟動")

    success = md.subscribe(symbol, quote_type=quote_type)
    if success:
        return {"message": f"已訂閱 {symbol} ({quote_type})"}
    raise HTTPException(status_code=400, detail=f"訂閱失敗: {symbol}")


@router.get("/subscribed")
async def get_subscribed():
    """取得已訂閱的標的"""
    md = app_state.get("market_data")
    if md is None:
        return {"data": []}
    return {"data": list(md.get_subscribed_symbols())}


# ========== 自選股 ==========

from pydantic import BaseModel
from ledger.models import Watchlist


class WatchlistAdd(BaseModel):
    symbol: str
    name: str = ""


class WatchlistReorder(BaseModel):
    symbols: list[str]  # 排序後的代碼順序

@router.get("/watchlist")
async def get_watchlist():
    """取得自選股清單"""
    db = app_state.get("db")
    if not db:
        return {"data": []}

    session = db.get_session()
    try:
        items = session.query(Watchlist).order_by(Watchlist.sort_order, Watchlist.id).all()
        return {"data": [w.to_dict() for w in items]}
    finally:
        session.close()


@router.post("/watchlist")
async def add_watchlist(item: WatchlistAdd):
    """新增自選股"""
    db = app_state.get("db")
    if not db:
        raise HTTPException(status_code=503, detail="資料庫未連接")

    # 自動取得中文名稱
    name = item.name
    # 自動取得中文名稱 & 啟動訂閱
    name = item.name
    md = app_state.get("market_data")
    if md:
        try:
            # 初始化快取並訂閱（也會順便拿到 contract name）
            md.init_quote_cache([item.symbol])
            contract = md.client.get_contract(item.symbol)
            if contract and not name:
                name = getattr(contract, "name", "")
        except Exception:
            pass

    session = db.get_session()
    try:
        exists = session.query(Watchlist).filter(Watchlist.symbol == item.symbol).first()
        if exists:
            raise HTTPException(status_code=400, detail=f"{item.symbol} 已在自選股中")

        # 取得目前最大排序值
        max_order = session.query(Watchlist).count()
        w = Watchlist(symbol=item.symbol, name=name, sort_order=max_order)
        session.add(w)
        session.commit()
        session.refresh(w)
        return {"message": f"已新增 {item.symbol}", "data": w.to_dict()}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.delete("/watchlist/{symbol}")
async def remove_watchlist(symbol: str):
    """移除自選股"""
    db = app_state.get("db")
    if not db:
        raise HTTPException(status_code=503, detail="資料庫未連接")

    session = db.get_session()
    try:
        item = session.query(Watchlist).filter(Watchlist.symbol == symbol).first()
        if not item:
            raise HTTPException(status_code=404, detail=f"{symbol} 不在自選股中")
        session.delete(item)
        session.commit()
        
        # 取消訂閱
        md = app_state.get("market_data")
        if md:
            md.unsubscribe(symbol, "tick")
            md.unsubscribe(symbol, "bidask")
            
        return {"message": f"已移除 {symbol}"}
    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()


@router.get("/watchlist/quotes")
async def get_watchlist_quotes():
    """取得自選股即時報價"""
    db = app_state.get("db")
    md = app_state.get("market_data")
    if not db:
        return {"data": []}

    session = db.get_session()
    try:
        items = session.query(Watchlist).order_by(Watchlist.sort_order, Watchlist.id).all()
        symbols = [w.symbol for w in items]
        name_map = {w.symbol: w.name for w in items}
    finally:
        session.close()

    if not symbols:
        return {"data": []}

    if md is None:
        # 無行情服務，只回傳代碼列表
        return {"data": [{"symbol": s, "name": name_map.get(s, ""), "close": None} for s in symbols]}

    # 透過快取取得即時報價 (Streaming)
    snapshots = md.get_latest_quotes(symbols)
    snap_map = {s["code"]: s for s in snapshots}

    # 回寫沒有名稱的自選股
    names_to_update = {}
    result = []
    for sym in symbols:
        snap = snap_map.get(sym, {})
        snap_name = snap.get("name") or ""
        db_name = name_map.get(sym, "")
        final_name = snap_name or db_name
        if snap_name and not db_name:
            names_to_update[sym] = snap_name
        result.append({
            "symbol": sym,
            "name": final_name,
            "close": snap.get("close"),
            "open": snap.get("open"),
            "high": snap.get("high"),
            "low": snap.get("low"),
            "volume": snap.get("total_volume"),
            "change_price": snap.get("change_price"),
            "change_rate": snap.get("change_rate"),
            "buy_price": snap.get("buy_price"),
            "sell_price": snap.get("sell_price"),
        })

    # 回寫缺少名稱的紀錄
    if names_to_update:
        session2 = db.get_session()
        try:
            for sym, nm in names_to_update.items():
                item = session2.query(Watchlist).filter(Watchlist.symbol == sym).first()
                if item:
                    item.name = nm
            session2.commit()
        except Exception:
            session2.rollback()
        finally:
            session2.close()

    return {"data": result}


@router.put("/watchlist/reorder")
async def reorder_watchlist(order: WatchlistReorder):
    """更新自選股排序"""
    db = app_state.get("db")
    if not db:
        raise HTTPException(status_code=503, detail="資料庫未連接")

    session = db.get_session()
    try:
        for idx, symbol in enumerate(order.symbols):
            item = session.query(Watchlist).filter(Watchlist.symbol == symbol).first()
            if item:
                item.sort_order = idx
        session.commit()
        return {"message": "排序已更新"}
    except Exception as e:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        session.close()
