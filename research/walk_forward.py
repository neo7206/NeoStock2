"""
NeoStock2 研究模組 — Walk-Forward Analysis (重構版)

支援：
- 多策略族群
- 進度回呼 (for API progress reporting)
- 結構化結果輸出
"""

import pandas as pd
import numpy as np
import vectorbt as vbt
from dateutil.relativedelta import relativedelta
from typing import Callable, Optional

from .backtest_engine import BacktestEngine
from .strategies import run_strategy, STRATEGY_TEMPLATES
from .ranker import Ranker


class WalkForwardAnalysis:
    def __init__(
        self,
        ranker: Ranker,
        engine: BacktestEngine,
        train_months: int = 12,
        test_months: int = 3,
        top_k: int = 5,
    ):
        self.ranker = ranker
        self.engine = engine
        self.train_months = train_months
        self.test_months = test_months
        self.top_k = top_k

    def run(
        self,
        close: pd.Series,
        strategy_id: str,
        param_grid: dict,
        progress_cb: Optional[Callable] = None,
    ) -> dict:
        """
        執行單一策略的 Walk-Forward Analysis

        Returns:
            dict: {
                'strategy_id': str,
                'oos_equity': pd.Series,
                'windows': list[dict],
                'final_stats': dict,
            }
        """
        # 1. 產生全域訊號
        try:
            entries, exits = run_strategy(strategy_id, close, param_grid)
        except Exception as e:
            return {
                'strategy_id': strategy_id,
                'error': f"策略訊號產生失敗: {str(e)}",
                'oos_equity': pd.Series(dtype=float),
                'windows': [],
                'final_stats': {},
            }

        if entries.empty:
            return {
                'strategy_id': strategy_id,
                'error': "無有效訊號",
                'oos_equity': pd.Series(dtype=float),
                'windows': [],
                'final_stats': {},
            }

        # 2. 計算視窗
        start_date = close.index[0]
        end_date = close.index[-1]
        current_date = start_date + relativedelta(months=self.train_months)

        windows_info = []
        oos_equity_parts = []
        window_id = 0

        # 計算總視窗數 (for progress)
        temp = current_date
        total_windows = 0
        while temp < end_date:
            total_windows += 1
            temp += relativedelta(months=self.test_months)

        while current_date < end_date:
            train_start = current_date - relativedelta(months=self.train_months)
            train_end = current_date
            test_end = min(
                current_date + relativedelta(months=self.test_months),
                end_date
            )

            # 進度回呼
            if progress_cb:
                pct = int((window_id / max(total_windows, 1)) * 100)
                progress_cb(pct, f"Window {window_id+1}/{total_windows}")

            # 3. Train Phase
            train_mask = (close.index >= train_start) & (close.index < train_end)
            train_close = close[train_mask]
            train_entries = entries[train_mask]
            train_exits = exits[train_mask]

            if len(train_close) < 100:
                current_date += relativedelta(months=self.test_months)
                window_id += 1
                continue

            try:
                train_pf = self.engine.run_backtest(train_close, train_entries, train_exits)
                ranked_df = self.ranker.score_strategies(train_pf)
            except Exception:
                current_date += relativedelta(months=self.test_months)
                window_id += 1
                continue

            if ranked_df.empty:
                windows_info.append({
                    'window_id': window_id,
                    'train_start': str(train_start.date()),
                    'train_end': str(train_end.date()),
                    'test_end': str(test_end.date()) if isinstance(test_end, pd.Timestamp) else str(test_end),
                    'best_params': None,
                    'score': None,
                    'status': 'no_valid_strategy',
                })
                current_date += relativedelta(months=self.test_months)
                window_id += 1
                continue

            # 取 Best (Top 1 先做 MVP)
            best_idx = ranked_df.index[0]
            best_score = float(ranked_df.iloc[0]['Score'])

            # 4. Test Phase
            test_mask = (close.index >= train_end) & (close.index < test_end)
            test_close = close[test_mask]

            if len(test_close) < 10:
                current_date += relativedelta(months=self.test_months)
                window_id += 1
                continue

            # 取單一參數訊號
            try:
                if isinstance(entries.columns, pd.MultiIndex):
                    best_entries_col = entries[best_idx]
                    best_exits_col = exits[best_idx]
                else:
                    loc = entries.columns.get_loc(best_idx)
                    best_entries_col = entries.iloc[:, loc]
                    best_exits_col = exits.iloc[:, loc]

                test_entries_k = best_entries_col[test_mask]
                test_exits_k = best_exits_col[test_mask]

                test_pf = self.engine.run_backtest(test_close, test_entries_k, test_exits_k)
                test_equity = test_pf.value()

                # 串接 Equity
                if len(oos_equity_parts) == 0:
                    oos_equity_parts.append(test_equity)
                else:
                    last_val = oos_equity_parts[-1].iloc[-1]
                    scaled = test_equity * (last_val / 1_000_000)
                    oos_equity_parts.append(scaled)

                # 記錄 window info
                test_return = float(test_pf.total_return().values) if hasattr(test_pf.total_return(), 'values') else float(test_pf.total_return())

                windows_info.append({
                    'window_id': window_id,
                    'train_start': str(train_start.date()),
                    'train_end': str(train_end.date()),
                    'test_end': str(test_end.date()) if isinstance(test_end, pd.Timestamp) else str(test_end),
                    'best_params': str(best_idx),
                    'score': best_score,
                    'test_return': test_return,
                    'status': 'ok',
                })
            except Exception as e:
                windows_info.append({
                    'window_id': window_id,
                    'train_start': str(train_start.date()),
                    'train_end': str(train_end.date()),
                    'test_end': str(test_end.date()) if isinstance(test_end, pd.Timestamp) else str(test_end),
                    'best_params': str(best_idx),
                    'score': best_score,
                    'status': f'test_error: {str(e)}',
                })

            current_date += relativedelta(months=self.test_months)
            window_id += 1

        # 5. 組合結果
        if oos_equity_parts:
            full_equity = pd.concat(oos_equity_parts)
            # Ensure no duplicate indices
            full_equity = full_equity[~full_equity.index.duplicated(keep='first')]

            # 計算最終績效
            total_return = (full_equity.iloc[-1] / full_equity.iloc[0]) - 1
            # 計算年化 (用實際天數)
            days = (full_equity.index[-1] - full_equity.index[0]).days
            years = max(days / 365.25, 0.01)
            cagr = (1 + total_return) ** (1 / years) - 1
            # MaxDD
            running_max = full_equity.cummax()
            drawdown = (full_equity - running_max) / running_max
            max_dd = float(drawdown.min())
            calmar = cagr / abs(max_dd) if max_dd != 0 else 0

            final_stats = {
                'total_return_pct': round(total_return * 100, 2),
                'cagr_pct': round(cagr * 100, 2),
                'max_drawdown_pct': round(max_dd * 100, 2),
                'calmar_ratio': round(calmar, 3),
                'total_windows': total_windows,
                'valid_windows': len([w for w in windows_info if w.get('status') == 'ok']),
                'start_equity': round(float(full_equity.iloc[0]), 2),
                'end_equity': round(float(full_equity.iloc[-1]), 2),
            }
        else:
            full_equity = pd.Series(dtype=float)
            final_stats = {}

        if progress_cb:
            progress_cb(100, "完成")

        return {
            'strategy_id': strategy_id,
            'oos_equity': full_equity,
            'windows': windows_info,
            'final_stats': final_stats,
        }
