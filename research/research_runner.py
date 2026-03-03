"""
NeoStock2 研究模組 — Research Runner

整合全流程：資料準備 → 策略訊號 → Walk-forward → A/B Ranking → 結果輸出
支援背景執行與進度回報。
"""

import json
import time
import pandas as pd
from pathlib import Path
from typing import Optional, Callable
from datetime import datetime, timedelta

from .universe import StrategyUniverse
from .strategies import STRATEGY_TEMPLATES, run_strategy
from .backtest_engine import BacktestEngine
from .ranker import Ranker
from .walk_forward import WalkForwardAnalysis

# 全域進度狀態（簡單版，適合單 worker）
_progress_store = {}

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "research_kbars"
RESULTS_DIR = PROJECT_ROOT / "data" / "research_results"


def get_progress(task_id: str) -> dict:
    """取得進度"""
    return _progress_store.get(task_id, {
        'status': 'unknown',
        'percent': 0,
        'message': '',
    })


def check_data_exists(ticker: str) -> dict:
    """檢查資料是否已下載"""
    file_path = DATA_DIR / f"{ticker}_60m.csv"
    if file_path.exists():
        df = pd.read_csv(file_path, nrows=5)
        # 讀取行數
        total_rows = sum(1 for _ in open(file_path)) - 1  # 扣掉 header
        return {
            'exists': True,
            'rows': total_rows,
            'file': str(file_path),
            'columns': list(df.columns),
        }
    return {'exists': False, 'rows': 0}


def prepare_data(ticker: str, task_id: str) -> bool:
    """準備資料（增量更新 60 分 K，若無既有 CSV 則全量下載 5 年）"""
    import sys
    sys.path.append(str(PROJECT_ROOT))

    _progress_store[task_id] = {
        'status': 'downloading',
        'percent': 5,
        'message': f'正在更新 {ticker} 的歷史資料...',
        'step': 'data_prep',
    }

    try:
        from .data_loader import update_data
        update_data(tickers=[ticker])
        return True
    except Exception as e:
        _progress_store[task_id] = {
            'status': 'error',
            'percent': 0,
            'message': f'資料更新失敗: {str(e)}',
            'step': 'data_prep',
        }
        return False


def run_full_research(ticker: str, task_id: str):
    """
    執行完整研究流程（背景任務）

    1. 檢查/準備資料
    2. 對每個策略模板執行 WFA (A 風格 + B 風格)
    3. 輸出結果至 data/research_results/{ticker}/
    """
    try:
        _progress_store[task_id] = {
            'status': 'running',
            'percent': 0,
            'message': '初始化研究環境...',
            'step': 'init',
        }

        # 1. 資料準備
        data_status = check_data_exists(ticker)
        if not data_status['exists']:
            _progress_store[task_id] = {
                'status': 'running',
                'percent': 5,
                'message': f'正在下載 {ticker} 的 5 年 60 分 K 資料...',
                'step': 'data_prep',
            }
            success = prepare_data(ticker, task_id)
            if not success:
                return

        # 2. 載入資料
        _progress_store[task_id] = {
            'status': 'running',
            'percent': 10,
            'message': '載入資料中...',
            'step': 'loading',
        }

        universe = StrategyUniverse([ticker], data_dir=str(DATA_DIR))
        universe.load_data()
        close = universe.get_price(ticker, 'close')

        if close.empty:
            _progress_store[task_id] = {
                'status': 'error',
                'percent': 0,
                'message': f'{ticker} 資料為空',
                'step': 'loading',
            }
            return

        # 3. 對每個策略 + 每個風格執行 WFA
        strategies = list(STRATEGY_TEMPLATES.keys())
        total_tasks = len(strategies) * 2  # A + B
        current_task = 0

        results_a = {}
        results_b = {}

        for strat_id in strategies:
            template = STRATEGY_TEMPLATES[strat_id]

            for style in ['A', 'B']:
                current_task += 1
                base_pct = 10 + int((current_task / total_tasks) * 80)

                style_name = '保守穩定型' if style == 'A' else '積極成長型'
                _progress_store[task_id] = {
                    'status': 'running',
                    'percent': base_pct,
                    'message': f'[{current_task}/{total_tasks}] {template.name} ({style_name})...',
                    'step': f'wfa_{strat_id}_{style}',
                }

                engine = BacktestEngine(style=style)
                ranker = Ranker(style=style)
                wfa = WalkForwardAnalysis(ranker=ranker, engine=engine)

                def progress_cb(pct, msg):
                    inner_pct = base_pct + int(pct * 0.8 / total_tasks)
                    _progress_store[task_id] = {
                        'status': 'running',
                        'percent': min(inner_pct, 95),
                        'message': f'{template.name} ({style_name}) - {msg}',
                        'step': f'wfa_{strat_id}_{style}',
                    }

                result = wfa.run(close, strat_id, template.param_grid, progress_cb=progress_cb)

                # 將 equity Series 轉為可序列化格式
                serializable_result = {
                    'strategy_id': result['strategy_id'],
                    'strategy_name': template.name,
                    'strategy_desc': template.description,
                    'windows': result['windows'],
                    'final_stats': result.get('final_stats', {}),
                    'error': result.get('error'),
                }

                if not result['oos_equity'].empty:
                    equity_data = {
                        'dates': [str(d) for d in result['oos_equity'].index],
                        'values': [round(float(v), 2) for v in result['oos_equity'].values],
                    }
                    serializable_result['oos_equity'] = equity_data
                else:
                    serializable_result['oos_equity'] = {'dates': [], 'values': []}

                if style == 'A':
                    results_a[strat_id] = serializable_result
                else:
                    results_b[strat_id] = serializable_result

        # 4. 排序：找出最佳策略
        def sort_key_a(r):
            stats = r.get('final_stats', {})
            return stats.get('calmar_ratio', -999)

        def sort_key_b(r):
            stats = r.get('final_stats', {})
            return stats.get('cagr_pct', -999)

        best_a = sorted(results_a.values(), key=sort_key_a, reverse=True)
        best_b = sorted(results_b.values(), key=sort_key_b, reverse=True)

        # 5. 輸出結果
        output_dir = RESULTS_DIR / ticker
        output_dir.mkdir(parents=True, exist_ok=True)

        final_output = {
            'ticker': ticker,
            'timestamp': datetime.now().isoformat(),
            'style_a': {
                'label': '保守穩定型（低回撤）',
                'description': '優先控制風險，Calmar Ratio 排序，MaxDD < 15%',
                'strategies': best_a,
            },
            'style_b': {
                'label': '積極成長型（高報酬）',
                'description': '追求高報酬，CAGR 排序，MaxDD < 35%',
                'strategies': best_b,
            },
        }

        result_path = output_dir / "results.json"
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(final_output, f, ensure_ascii=False, indent=2)

        _progress_store[task_id] = {
            'status': 'completed',
            'percent': 100,
            'message': '研究完成！',
            'step': 'done',
            'result_path': str(result_path),
        }

    except Exception as e:
        import traceback
        _progress_store[task_id] = {
            'status': 'error',
            'percent': 0,
            'message': f'研究失敗: {str(e)}',
            'step': 'error',
            'traceback': traceback.format_exc(),
        }


def get_results(ticker: str) -> Optional[dict]:
    """讀取已完成的研究結果"""
    result_path = RESULTS_DIR / ticker / "results.json"
    if result_path.exists():
        with open(result_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None
