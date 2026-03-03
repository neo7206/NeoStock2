import vectorbt as vbt
import numpy as np
import pandas as pd
from typing import Literal

from .cost_model import CostModel, cost_model_a, cost_model_b

class BacktestEngine:
    def __init__(self, style: Literal['A', 'B'] = 'A'):
        self.style = style
        self.cost_model = cost_model_a if style == 'A' else cost_model_b
        
        # 基礎費率 (買賣相同)
        # 注意: vbt 的 fees 若設為 float，是 apply 到每筆 order (Buy & Sell)
        # 台股手續費 0.1425% is applied on both sides.
        # User defined: Slip A=0.03%, B=0.02%.
        # VBT fees = fee_rate
        # VBT slippage = slippage_rate
        self.fees = self.cost_model.fee_rate  # 0.001425
        self.slippage = self.cost_model.slippage # 0.0002 or 0.0003
        self.tax_rate = self.cost_model.tax_rate # 0.003 (Sell only)

    def run_backtest(self, close, entries, exits, freq='60Min'):
        """
        執行回測
        """
        # 1. 執行 Portfolio 模擬
        # 使用 vbt.Portfolio.from_signals
        pf = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            fees=self.fees,
            slippage=self.slippage,
            freq=freq,
            # Init cash 1,000,000 or 100% equity
            init_cash=1_000_000,
            size=1.0,           # 全倉模式
            size_type='percent', # 每次 100% equity
            accumulate=False,    # 不加碼
        )
        
        # 2. 處理證交稅 (Tax) - 賣出時額外扣除 0.3%
        # VBT 目前較難直接設定 "Sell Only Fee"。
        # Workaround:
        # Access trade records, calculate tax, and adjust PnL / Equity?
        # 但這會很慢且複雜，破壞向量化優勢。
        # 
        # Alternative: 將 Fees 設定為 (Fee + Tax/2) ? 不精確。
        # 
        # Better VBT Approach:
        # 使用 custom order class 或 sim_kwargs (較進階)。
        # 
        # "MVP" Approach:
        # 先忽略 Tax 導致的複利減少細微差異 (因為是全倉 rolling)，
        # 但在計算 Performance Stats 時，手動扣除 Tax 總和。
        # 若需要更精確的 Equity Curve (用於 drawdown)，
        # 我們可以在 fees 上做手腳：
        # fees = 0.001425 + (0.003 / 2) = 0.002925
        # 這樣一進一出總共扣 0.00585
        # 實際是: Buy(0.001425) + Sell(0.001425+0.003) = 0.00585
        # 所以設定 fees = 0.002925 是一個數學上等價的近似 (對總損益而言)
        # 雖然單次扣款時間點有點偏差，但對 MVP 足夠精確。
        
        approximated_fee = self.fees + (self.tax_rate / 2)
        
        # Re-run with approximated fee
        pf_adjusted = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            fees=approximated_fee,
            slippage=self.slippage,
            freq=freq,
            init_cash=1_000_000,
            size=1.0,
            size_type='percent',
            accumulate=False,
        )
        
        return pf_adjusted
