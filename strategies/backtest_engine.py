"""
NeoStock2 策略 — 回測引擎 (Backtest Engine)

負責：
1. 取得歷史數據
2. 模擬策略執行 (Bar-by-Bar Loop)
3. 計算損益與績效指標
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Type, Dict, Any, List, Optional
from strategies.base_strategy import BaseStrategy, SignalAction

logger = logging.getLogger("neostock2.strategies.backtest_engine")

class BacktestEngine:
    def __init__(self, history_manager):
        self.history_manager = history_manager

    def run_backtest(
        self,
        strategy_cls: Type[BaseStrategy],
        params: Dict[str, Any],
        symbol: str,
        start_date: str,
        end_date: str,
        initial_capital: float = 1000000,
        timeframe: str = "1min",
        stop_loss_pct: float = 0.0,
        take_profit_pct: float = 0.0,
        max_position: int = 5 # Default lots limit
    ) -> Dict[str, Any]:
        """
        執行回測
        """
        # 1. 取得歷史數據
        try:
            s_dt = datetime.strptime(start_date, "%Y-%m-%d")
            e_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        except ValueError:
            return {"error": "Invalid date format (start/end)"}

        df = self.history_manager.get_history(symbol, s_dt, e_dt, timeframe)
        
        if df.empty:
            return {"error": "No data found for the specified range"}

        # Standardize columns to Title Case for Strategies (Open, High, Low, Close, Volume)
        df = df.rename(columns={
            "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume", "amount": "Amount"
        })
            
        # 2. 初始化策略
        strategy = strategy_cls(symbols=[symbol], params=params)
        strategy.initialize()
        
        # 3. 初始化回測狀態
        cash = initial_capital
        position = 0 # 股數
        trades = []
        
        # MDD 計算變數
        peak_equity = initial_capital
        max_drawdown = 0.0
        
        active_trade = None # {entry_price, quantity, entry_time, type}
        
        # Debug Stats
        debug_info = {
            "total_signals": 0,
            "filtered_insufficient_funds": 0,
            "filtered_max_position": 0,
            "filtered_other": 0
        }
        
        # 4. 逐筆模擬 (Bar-by-Bar)
        min_bars = 50 # 預留給指標計算的暖身期
        total_bars = len(df)
        
        if total_bars < min_bars:
             return {"error": "Insufficient data points (min 50 bars required)"}
            
        logger.info(f"開始回測 {symbol}: {total_bars} bars, {strategy.name}, Capital={initial_capital}, Risk=(SL:{stop_loss_pct}%, TP:{take_profit_pct}%)")

        for i in range(min_bars, total_bars):
            # 當前 Bar (此 Bar 剛收盤)
            current_bar = df.iloc[i]
            current_time = current_bar.name # datetime index
            current_price = float(current_bar['Close'])
            current_low = float(current_bar['Low'])
            current_high = float(current_bar['High'])
            current_open = float(current_bar['Open'])
            
            # --- 風控檢查 (SL/TP) ---
            # 假設: 在當前 Bar 的盤中觸發。
            # 優先順序: 若 SL 和 TP 同時觸發 (e.g. 震盪大)，保守起見視為 SL (或依 Open 接近誰)
            # 這裡簡化: 如果 Low <= SL Price -> SL Triggered
            trade_exited_this_bar = False
            
            if position > 0 and active_trade:
                entry_price = active_trade["entry_price"]
                
                # Check Stop Loss
                if stop_loss_pct > 0:
                    sl_price = entry_price * (1 - stop_loss_pct / 100)
                    if current_low <= sl_price:
                        # 觸發停損
                        # 成交價估計: 若 Open 已經低於 SL，則成交在 Open (跳空)，否則成交在 SL
                        exit_price = sl_price if current_open > sl_price else current_open
                        
                        self._execute_exit(trades, active_trade, current_time, exit_price, symbol, "Stop Loss", cash, float(position))
                        # Update State
                        revenue = exit_price * position
                        cash += revenue
                        position = 0
                        active_trade = None
                        trade_exited_this_bar = True

                # Check Take Profit (若尚未 SL)
                if not trade_exited_this_bar and take_profit_pct > 0:
                    tp_price = entry_price * (1 + take_profit_pct / 100)
                    if current_high >= tp_price:
                         # 觸發停利
                         exit_price = tp_price if current_open < tp_price else current_open
                         
                         self._execute_exit(trades, active_trade, current_time, exit_price, symbol, "Take Profit", cash, float(position))
                         revenue = exit_price * position
                         cash += revenue
                         position = 0
                         active_trade = None
                         trade_exited_this_bar = True

            # 若本 Bar 已出場，則不進行新訊號判斷 (避免同 Bar 進出)
            if trade_exited_this_bar:
                # Update MDD before skipping
                current_equity = cash + (position * current_price)
                if current_equity > peak_equity:
                    peak_equity = current_equity
                drawdown = (peak_equity - current_equity) / peak_equity * 100
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                continue

            # 準備數據視窗 (包含當前 Bar)
            window = df.iloc[:i+1] 
            
            # --- 策略訊號 ---
            try:
                signal = strategy.on_bar(symbol, window)
            except Exception as e:
                logger.error(f"Strategy Error at {current_time}: {e}")
                signal = None
                
            # --- 交易執行 (模擬市價成交於當前 Bar Close) ---
            if signal:
                debug_info["total_signals"] += 1
                
                # 策略回傳的是 "張數"，轉為 "股數"
                qty_shares = signal.quantity * 1000 
                
                # [買入訊號]
                if signal.action == SignalAction.BUY:
                    # 檢查部位上限
                    current_lots = int(position / 1000)
                    if current_lots + signal.quantity > max_position:
                         debug_info["filtered_max_position"] += 1
                         continue

                    # 若無持倉 或 加碼(視為同筆? 簡化為不支援加碼，只支援 0 -> N)
                    # 這裡為了簡單，若已有持倉且未達上限，我們視為加碼，並更新平均成本 (Weighted Average)
                    # 但 V1 架構 active_trade 只有一筆，暫且簡化：
                    # 若已有持倉，計算新成本
                    
                    cost = current_price * qty_shares
                    if cash >= cost:
                        # Execute Buy
                        if position == 0:
                            active_trade = {
                                "symbol": symbol,
                                "entry_time": current_time.isoformat(),
                                "entry_price": current_price,
                                "quantity": qty_shares,
                                "action": "Buy"
                            }
                        else:
                            # Averaging logic
                            total_val = (active_trade["entry_price"] * position) + cost
                            new_total_qty = position + qty_shares
                            avg_price = total_val / new_total_qty
                            active_trade["entry_price"] = avg_price
                            active_trade["quantity"] = new_total_qty
                        
                        cash -= cost
                        position += qty_shares
                    else:
                        debug_info["filtered_insufficient_funds"] += 1

                # [賣出訊號]
                elif signal.action == SignalAction.SELL:
                    # 若持有部位 => 平倉賣出
                    if position > 0:
                        self._execute_exit(trades, active_trade, current_time, current_price, symbol, "Signal Sell", cash, float(position))
                        revenue = current_price * position
                        cash += revenue
                        position = 0
                        active_trade = None
                    else:
                        # 無部位可賣
                        pass

            # --- 每日結算 (與 MDD 更新) ---
            current_equity = cash + (position * current_price)
            if current_equity > peak_equity:
                peak_equity = current_equity
            drawdown = (peak_equity - current_equity) / peak_equity * 100
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # --- 回測結束: 強制平倉 ---
        final_price = float(df.iloc[-1]['Close'])
        if position > 0:
             self._execute_exit(trades, active_trade, df.index[-1].isoformat(), final_price, symbol, "End of Backtest", cash, float(position))
             revenue = final_price * position
             cash += revenue
             position = 0
             
        # --- 最終結算 ---
        final_equity = cash
        total_net_profit = final_equity - initial_capital
        roi = (total_net_profit / initial_capital) * 100
        
        winning_trades = [t for t in trades if t["pnl"] > 0]
        losing_trades = [t for t in trades if t["pnl"] <= 0]
        
        total_trades_count = len(trades)
        win_rate = (len(winning_trades) / total_trades_count * 100) if total_trades_count > 0 else 0
        
        gross_profit = sum(t["pnl"] for t in winning_trades)
        gross_loss = abs(sum(t["pnl"] for t in losing_trades))
        
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = 999.0 if gross_profit > 0 else 0.0
        
        avg_win = (gross_profit / len(winning_trades)) if winning_trades else 0
        avg_loss = (gross_loss / len(losing_trades)) if losing_trades else 0
        
        return {
            "performance": {
                "initial_capital": initial_capital,
                "final_equity": round(final_equity, 0),
                "total_net_profit": round(total_net_profit, 0),
                "roi_pct": round(roi, 2),
                "total_trades": total_trades_count,
                "win_rate_pct": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "max_drawdown_pct": round(max_drawdown, 2),
                "avg_win": round(avg_win, 0),
                "avg_loss": round(avg_loss, 0)
            },
            "trades": trades,
            "debug": debug_info
        }

    def _execute_exit(self, trades, active_trade, exit_time_iso, exit_price, symbol, reason, cash, position):
        entry_price = active_trade["entry_price"]
        gross_pnl = (exit_price - entry_price) * position
        
        # 暫時使用 Gross PnL
        net_pnl = gross_pnl 
        
        trades.append({
            "entry_time": active_trade["entry_time"],
            "exit_time": exit_time_iso,
            "symbol": symbol,
            "action": "Sell", 
            "reason": reason,
            "side": "Long", 
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "quantity": int(position / 1000), 
            "pnl": round(net_pnl, 2),
            "return_pct": round((net_pnl / (entry_price * position)) * 100, 2)
        })


