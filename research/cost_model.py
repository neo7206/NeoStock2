from dataclasses import dataclass
from typing import Literal

@dataclass
class CostModel:
    fee_rate: float = 0.001425
    tax_rate: float = 0.003
    slippage: float = 0.0002  # 預設 0.02% 或是固定 tick

    def calculate_cost(self, price: float, size: float, direction: Literal['buy', 'sell']) -> float:
        """
        計算單筆交易成本
        Buy: fee + slippage
        Sell: fee + tax + slippage
        """
        # 手續費 (買賣都要)
        fee = price * size * self.fee_rate
        # 最低手續費 20 元 (可選，這裡先不考慮，假設電子下單打折後)
        
        # 證交稅 (只在賣出時收)
        tax = price * size * self.tax_rate if direction == 'sell' else 0
        
        return fee + tax

    def get_slippage_price(self, price: float, direction: Literal['buy', 'sell']) -> float:
        """
        計算滑價後的成交價
        Buy: price * (1 + slippage)
        Sell: price * (1 - slippage)
        """
        if direction == 'buy':
            return price * (1 + self.slippage)
        else:
            return price * (1 - self.slippage)

# 預定義 A/B 兩種風格的成本模型

# A (保守): 滑價 0.03%
cost_model_a = CostModel(slippage=0.0003)

# B (積極): 滑價 0.02%
cost_model_b = CostModel(slippage=0.0002)
