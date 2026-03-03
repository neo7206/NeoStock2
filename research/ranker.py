"""
NeoStock2 研究模組 — A/B Ranking 評分系統 (重構版)

相容 vectorbt 0.28.x API
"""

import numpy as np
import pandas as pd
import vectorbt as vbt
from typing import Literal


class Ranker:
    def __init__(self, style: Literal['A', 'B']):
        self.style = style

    def score_strategies(self, pf: vbt.Portfolio) -> pd.DataFrame:
        """
        評分並排序策略參數

        Returns:
            DataFrame with columns: [Total Return, CAGR, Max Drawdown, Calmar, Sortino, Win Rate, Total Trades, Score]
        """
        try:
            total_return = pf.total_return()
            max_dd = pf.max_drawdown()

            # vbt 0.28.x: 使用 pf.trades.count() 取代 pf.total_trades()
            try:
                total_trades = pf.trades.count()
            except Exception:
                try:
                    total_trades = pf.entry_trades.count()
                except Exception:
                    total_trades = pd.Series(0, index=total_return.index if hasattr(total_return, 'index') else [0])

            # 保護：若 total_return 是 scalar 就包裹一下
            if isinstance(total_return, (int, float, np.floating)):
                total_return = pd.Series([total_return])
                max_dd = pd.Series([max_dd])
            if isinstance(total_trades, (int, float, np.floating)):
                total_trades = pd.Series([total_trades])

            stats_df = pd.DataFrame({
                'Total Return [%]': total_return * 100,
                'Max Drawdown [%]': max_dd * 100,
                'Total Trades': total_trades,
            })

            # 安全計算 CAGR
            try:
                ann_ret = pf.annualized_return()
                if isinstance(ann_ret, (int, float, np.floating)):
                    ann_ret = pd.Series([ann_ret])
                stats_df['CAGR [%]'] = ann_ret * 100
            except Exception:
                stats_df['CAGR [%]'] = stats_df['Total Return [%]']

            # 安全計算 Sortino
            try:
                sortino = pf.sortino_ratio()
                if isinstance(sortino, (int, float, np.floating)):
                    sortino = pd.Series([sortino])
                stats_df['Sortino Ratio'] = sortino
            except Exception:
                stats_df['Sortino Ratio'] = 0.0

            # 安全計算 Calmar
            try:
                calmar = pf.calmar_ratio()
                if isinstance(calmar, (int, float, np.floating)):
                    calmar = pd.Series([calmar])
                stats_df['Calmar Ratio'] = calmar
            except Exception:
                stats_df['Calmar Ratio'] = 0.0

            # 安全計算 Win Rate (vbt 0.28.x: pf.trades.win_rate())
            try:
                wr = pf.trades.win_rate()
                if isinstance(wr, (int, float, np.floating)):
                    wr = pd.Series([wr])
                stats_df['Win Rate [%]'] = wr * 100
            except Exception:
                stats_df['Win Rate [%]'] = 0.0

        except Exception as e:
            print(f"[Ranker] 計算統計失敗: {e}")
            return pd.DataFrame()

        # Replace inf/nan
        stats_df.replace([np.inf, -np.inf], np.nan, inplace=True)
        stats_df.fillna(0, inplace=True)

        # Hard Constraints (放寬條件)
        # 60 分 K 下，1 年 train window 交易次數可能很少
        valid_mask = pd.Series(True, index=stats_df.index)

        if self.style == 'A':
            valid_mask &= (stats_df['Max Drawdown [%]'] > -25)   # MaxDD < 25%
            valid_mask &= (stats_df['Total Trades'] >= 2)         # 至少 2 筆交易
            valid_mask &= (stats_df['Total Return [%]'] > -50)    # 不要太慘的
        else:
            valid_mask &= (stats_df['Max Drawdown [%]'] > -50)   # MaxDD < 50%
            valid_mask &= (stats_df['Total Trades'] >= 2)         # 至少 2 筆交易
            valid_mask &= (stats_df['Total Return [%]'] > -50)    # 不要太慘的

        filtered = stats_df[valid_mask].copy()

        if filtered.empty:
            return pd.DataFrame()

        # Scoring
        if self.style == 'A':
            calmar_s = filtered['Calmar Ratio'].rank(pct=True, na_option='bottom')
            dd_s = (-filtered['Max Drawdown [%]']).rank(pct=True, na_option='bottom')
            sortino_s = filtered['Sortino Ratio'].rank(pct=True, na_option='bottom')
            filtered['Score'] = calmar_s * 0.5 + dd_s * 0.3 + sortino_s * 0.2
        else:
            cagr_s = filtered['CAGR [%]'].rank(pct=True, na_option='bottom')
            sortino_s = filtered['Sortino Ratio'].rank(pct=True, na_option='bottom')
            dd_s = (-filtered['Max Drawdown [%]']).rank(pct=True, na_option='bottom')
            filtered['Score'] = cagr_s * 0.5 + sortino_s * 0.3 + dd_s * 0.2

        return filtered.sort_values('Score', ascending=False)
