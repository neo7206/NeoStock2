"""
NeoStock2 儀表板路由 — 交易 API
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from dashboard.state import app_state

router = APIRouter()


class OrderRequest(BaseModel):
    symbol: str
    action: str  # Buy / Sell
    quantity: int = 1
    price: float = 0
    price_type: str = "LMT"
    order_type: str = "ROD"


@router.post("/order")
async def place_order(req: OrderRequest):
    """手動下單"""
    om = app_state.get("order_manager")
    if om is None:
        raise HTTPException(status_code=503, detail="交易服務未啟動")

    result = om.place_order(
        symbol=req.symbol,
        action=req.action,
        quantity=req.quantity,
        price=req.price,
        price_type=req.price_type,
        order_type=req.order_type,
    )

    if result.get("success"):
        return {"data": result}

    raise HTTPException(status_code=400, detail=result.get("error", "下單失敗"))


@router.get("/orders")
async def get_orders():
    """取得委託記錄"""
    om = app_state.get("order_manager")
    if om is None:
        return {"data": []}
    return {"data": list(om.get_orders().values())}


@router.post("/orders/update")
async def update_orders():
    """更新委託狀態"""
    om = app_state.get("order_manager")
    if om is None:
        raise HTTPException(status_code=503, detail="交易服務未啟動")
    
    # 未登入時直接回傳本地快取，避免無效的 API 呼叫
    client = app_state.get("api_client")
    if not client or not client.is_logged_in:
        return {"data": list(om.get_orders().values())}
    
    orders = om.update_status()
    return {"data": orders}


@router.post("/order/{order_id}/cancel")
async def cancel_order(order_id: str):
    """取消委託"""
    om = app_state.get("order_manager")
    if om is None:
        raise HTTPException(status_code=503, detail="交易服務未啟動")
    
    result = om.cancel_order_by_id(order_id)
    if result.get("success"):
        return {"data": result}
        
    raise HTTPException(status_code=400, detail=result.get("error", "取消失敗"))


@router.get("/account")
async def get_account():
    """取得帳戶資訊"""
    client = app_state.get("api_client")
    if client is None or not client.is_logged_in:
        return {"data": {"logged_in": False}}

    info = client.get_account_info()
    balance = client.get_account_balance()
    positions = client.get_positions() or []

    return {
        "data": {
            "logged_in": True,
            "account": info,
            "balance": balance,
            "broker_positions": positions,
        }
    }


@router.get("/profit_loss")
async def get_profit_loss(code: str = None):
    """取得券商端損益明細（每筆未平倉的買入記錄）"""
    client = app_state.get("api_client")
    if client is None or not client.is_logged_in:
        return {"data": []}

    result = client.get_profit_loss()
    if result is None:
        return {"data": [], "error": "查詢失敗"}

    # 若指定代碼則篩選
    if code:
        result = [r for r in result if r.get("code") == code]

    return {"data": result}
