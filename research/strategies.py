"""
NeoStock2 研究模組 — 跨日波段策略模板

5 個經典跨日波段策略，全自動掃參，使用者無需手動選擇。
所有策略均只做多（Long Only），適合台股跨日持股。
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Tuple


@dataclass
class StrategyTemplate:
    """策略模板描述"""
    id: str
    name: str
    description: str
    param_grid: dict  # 參數網格（用於 walk-forward 搜尋）


# ======================================================================
# S1: 均線趨勢 (Trend MA)
#   入場: MA(fast) > MA(slow) 且 close > MA(slow)
#   出場: MA(fast) < MA(slow)
#   適合: 中長線趨勢行情 (0050, 2330)
# ======================================================================

class TrendMAStrategy:
    ID = "trend_ma"
    NAME = "均線趨勢交叉"
    DESC = "快線突破慢線 + 股價站穩慢線時進場，反轉時出場。適合趨勢明確的標的。"
    PARAM_GRID = {
        'fast_windows': [10, 20, 30],
        'slow_windows': [60, 90, 120],
    }

    @staticmethod
    def run(close: pd.Series, fast_windows: list, slow_windows: list) -> Tuple[pd.DataFrame, pd.DataFrame]:
        all_entries = []
        all_exits = []
        col_tuples = []

        for fw in fast_windows:
            ma_fast = close.rolling(fw).mean()
            for sw in slow_windows:
                if fw >= sw:
                    continue
                ma_slow = close.rolling(sw).mean()

                trend_up = ma_fast > ma_slow
                price_above_slow = close > ma_slow
                entry = trend_up & price_above_slow

                trend_down = ma_fast < ma_slow
                exit_ = trend_down

                all_entries.append(entry)
                all_exits.append(exit_)
                col_tuples.append((fw, sw))

        if not col_tuples:
            return pd.DataFrame(), pd.DataFrame()

        cols = pd.MultiIndex.from_tuples(col_tuples, names=['fast_ma', 'slow_ma'])
        entries_df = pd.concat(all_entries, axis=1)
        entries_df.columns = cols
        exits_df = pd.concat(all_exits, axis=1)
        exits_df.columns = cols

        return entries_df, exits_df


# ======================================================================
# S2: 趨勢突破 (Breakout)
#   入場: close > rolling_max(close, N)
#   出場: close < rolling_min(close, M)
#   適合: 強勢突破行情
# ======================================================================

class BreakoutStrategy:
    ID = "breakout"
    NAME = "通道突破"
    DESC = "股價突破 N 根 K 棒高點進場，跌破 M 根 K 棒低點出場。適合捕捉突破行情。"
    PARAM_GRID = {
        'entry_windows': [20, 40, 60],
        'exit_windows': [10, 20, 40],
    }

    @staticmethod
    def run(close: pd.Series, entry_windows: list, exit_windows: list) -> Tuple[pd.DataFrame, pd.DataFrame]:
        all_entries = []
        all_exits = []
        col_tuples = []

        for ew in entry_windows:
            rolling_high = close.rolling(ew).max().shift(1)  # 前 N 根最高 (不含當根)
            for xw in exit_windows:
                rolling_low = close.rolling(xw).min().shift(1)
                entry = close > rolling_high
                exit_ = close < rolling_low
                all_entries.append(entry)
                all_exits.append(exit_)
                col_tuples.append((ew, xw))

        cols = pd.MultiIndex.from_tuples(col_tuples, names=['entry_window', 'exit_window'])
        entries_df = pd.concat(all_entries, axis=1)
        entries_df.columns = cols
        exits_df = pd.concat(all_exits, axis=1)
        exits_df.columns = cols

        return entries_df, exits_df


# ======================================================================
# S3: 多頭回檔買進 (Pullback in Uptrend)
#   濾網: close > MA(long)
#   入場: RSI(len) < threshold
#   出場: RSI > 60 或 close > 前高
#   適合: 穩定上漲趨勢中的修正買點
# ======================================================================

def _calc_rsi(close: pd.Series, window: int) -> pd.Series:
    """手動計算 RSI (避免依賴 vbt.RSI 的 index 問題)"""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


class PullbackStrategy:
    ID = "pullback"
    NAME = "多頭回檔買進"
    DESC = "在上升趨勢中（股價站穩長均線），等 RSI 過低時買進，RSI 反彈時出場。"
    PARAM_GRID = {
        'long_ma_windows': [60, 120],
        'rsi_windows': [7, 14],
        'rsi_entry_thresholds': [30, 40],
    }

    @staticmethod
    def run(close: pd.Series, long_ma_windows: list, rsi_windows: list,
            rsi_entry_thresholds: list) -> Tuple[pd.DataFrame, pd.DataFrame]:
        all_entries = []
        all_exits = []
        col_tuples = []

        for lma in long_ma_windows:
            ma_long = close.rolling(lma).mean()
            uptrend = close > ma_long  # 多頭濾網
            for rw in rsi_windows:
                rsi = _calc_rsi(close, rw)
                for th in rsi_entry_thresholds:
                    entry = uptrend & (rsi < th)
                    exit_ = rsi > 60  # RSI 回到 60 以上出場
                    all_entries.append(entry)
                    all_exits.append(exit_)
                    col_tuples.append((lma, rw, th))

        cols = pd.MultiIndex.from_tuples(col_tuples, names=['long_ma', 'rsi_window', 'rsi_threshold'])
        entries_df = pd.concat(all_entries, axis=1)
        entries_df.columns = cols
        exits_df = pd.concat(all_exits, axis=1)
        exits_df.columns = cols

        return entries_df, exits_df


# ======================================================================
# S4: 布林通道回歸 (Bollinger Band Mean Reversion)
#   入場: close < BB_lower（跌破布林下軌）
#   出場: close > BB_mid（回到中軌）
#   適合: 均值回歸行情（盤整或溫和上漲的 ETF）
# ======================================================================

class BollingerStrategy:
    ID = "bollinger"
    NAME = "布林通道回歸"
    DESC = "股價跌破布林帶下軌時買進，回到中軌時出場。適合波動穩定的標的。"
    PARAM_GRID = {
        'bb_windows': [20, 30, 40],
        'bb_stds': [1.5, 2.0, 2.5],
    }

    @staticmethod
    def run(close: pd.Series, bb_windows: list, bb_stds: list) -> Tuple[pd.DataFrame, pd.DataFrame]:
        all_entries = []
        all_exits = []
        col_tuples = []

        for bw in bb_windows:
            bb_mid = close.rolling(bw).mean()
            bb_std = close.rolling(bw).std()
            for bs in bb_stds:
                bb_lower = bb_mid - bs * bb_std
                entry = close < bb_lower
                exit_ = close > bb_mid
                all_entries.append(entry)
                all_exits.append(exit_)
                col_tuples.append((bw, bs))

        cols = pd.MultiIndex.from_tuples(col_tuples, names=['bb_window', 'bb_std'])
        entries_df = pd.concat(all_entries, axis=1)
        entries_df.columns = cols
        exits_df = pd.concat(all_exits, axis=1)
        exits_df.columns = cols

        return entries_df, exits_df


# ======================================================================
# S5: MACD 趨勢跟隨 (MACD Trend Following)
#   入場: MACD line > Signal line 且 MACD > 0（確認趨勢）
#   出場: MACD line < Signal line
#   適合: 中長期趨勢
# ======================================================================

class MACDStrategy:
    ID = "macd"
    NAME = "MACD 趨勢"
    DESC = "MACD 金叉且位於零軸之上時進場，死叉時出場。中長期趨勢追蹤。"
    PARAM_GRID = {
        'fast_windows': [8, 12],
        'slow_windows': [21, 26],
        'signal_windows': [7, 9],
    }

    @staticmethod
    def run(close: pd.Series, fast_windows: list, slow_windows: list,
            signal_windows: list) -> Tuple[pd.DataFrame, pd.DataFrame]:
        all_entries = []
        all_exits = []
        col_tuples = []

        for fw in fast_windows:
            ema_fast = close.ewm(span=fw, adjust=False).mean()
            for sw in slow_windows:
                if fw >= sw:
                    continue  # fast 必須小於 slow
                ema_slow = close.ewm(span=sw, adjust=False).mean()
                macd_line = ema_fast - ema_slow
                for sig in signal_windows:
                    signal_line = macd_line.ewm(span=sig, adjust=False).mean()
                    entry = (macd_line > signal_line) & (macd_line > 0)
                    exit_ = macd_line < signal_line
                    all_entries.append(entry)
                    all_exits.append(exit_)
                    col_tuples.append((fw, sw, sig))

        if not col_tuples:
            return pd.DataFrame(), pd.DataFrame()

        cols = pd.MultiIndex.from_tuples(col_tuples, names=['macd_fast', 'macd_slow', 'signal'])
        entries_df = pd.concat(all_entries, axis=1)
        entries_df.columns = cols
        exits_df = pd.concat(all_exits, axis=1)
        exits_df.columns = cols

        return entries_df, exits_df


# ======================================================================
# 模板註冊表（全自動，使用者不需選擇）
# ======================================================================

STRATEGY_TEMPLATES = {
    TrendMAStrategy.ID: StrategyTemplate(
        id=TrendMAStrategy.ID, name=TrendMAStrategy.NAME,
        description=TrendMAStrategy.DESC, param_grid=TrendMAStrategy.PARAM_GRID,
    ),
    BreakoutStrategy.ID: StrategyTemplate(
        id=BreakoutStrategy.ID, name=BreakoutStrategy.NAME,
        description=BreakoutStrategy.DESC, param_grid=BreakoutStrategy.PARAM_GRID,
    ),
    PullbackStrategy.ID: StrategyTemplate(
        id=PullbackStrategy.ID, name=PullbackStrategy.NAME,
        description=PullbackStrategy.DESC, param_grid=PullbackStrategy.PARAM_GRID,
    ),
    BollingerStrategy.ID: StrategyTemplate(
        id=BollingerStrategy.ID, name=BollingerStrategy.NAME,
        description=BollingerStrategy.DESC, param_grid=BollingerStrategy.PARAM_GRID,
    ),
    MACDStrategy.ID: StrategyTemplate(
        id=MACDStrategy.ID, name=MACDStrategy.NAME,
        description=MACDStrategy.DESC, param_grid=MACDStrategy.PARAM_GRID,
    ),
}


def get_strategy_class(strategy_id: str):
    """根據 ID 取得策略類別"""
    mapping = {
        'trend_ma': TrendMAStrategy,
        'breakout': BreakoutStrategy,
        'pullback': PullbackStrategy,
        'bollinger': BollingerStrategy,
        'macd': MACDStrategy,
    }
    return mapping.get(strategy_id)


def run_strategy(strategy_id: str, close: pd.Series, param_grid: dict) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """統一入口：執行策略並取得 entries/exits"""
    cls = get_strategy_class(strategy_id)
    if cls is None:
        raise ValueError(f"Unknown strategy: {strategy_id}")
    return cls.run(close, **param_grid)
