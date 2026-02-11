"""
NeoStock2 策略 — Backtrader 橋接器

提供：
- ShioajiDataFeed: 將 Shioaji K 棒數據轉為 Backtrader DataFeed
- 讓任何 Backtrader 格式的策略可直接使用 Shioaji 數據回測
"""

import logging
from datetime import datetime

import pandas as pd

try:
    import backtrader as bt

    class ShioajiDataFeed(bt.feeds.PandasData):
        """
        將 Shioaji K 棒 DataFrame 轉為 Backtrader DataFeed

        Shioaji kbars 欄位（小寫）會自動映射到 Backtrader 標準欄位
        """
        params = (
            ("datetime", None),
            ("open", "Open"),
            ("high", "High"),
            ("low", "Low"),
            ("close", "Close"),
            ("volume", "Volume"),
            ("openinterest", -1),
        )

    def run_backtest(
        strategy_cls: type,
        data_df: pd.DataFrame,
        cash: float = 1_000_000,
        commission: float = 0.001425,
        strategy_params: dict = None,
    ) -> dict:
        """
        執行回測

        Args:
            strategy_cls: Backtrader 策略類別
            data_df: 含有 OHLCV 的 DataFrame（index 為 datetime）
            cash: 初始資金
            commission: 手續費率
            strategy_params: 策略參數

        Returns:
            回測結果 dict
        """
        cerebro = bt.Cerebro()

        # 整理 DataFrame 欄位
        df = data_df.copy()
        col_map = {"open": "Open", "high": "High", "low": "Low",
                    "close": "Close", "volume": "Volume"}
        df.rename(columns={k: v for k, v in col_map.items() if k in df.columns},
                  inplace=True)

        if not isinstance(df.index, pd.DatetimeIndex):
            if "ts" in df.columns:
                df["ts"] = pd.to_datetime(df["ts"])
                df.set_index("ts", inplace=True)
            elif "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
                df.set_index("datetime", inplace=True)

        data = ShioajiDataFeed(dataname=df)
        cerebro.adddata(data)

        if strategy_params:
            cerebro.addstrategy(strategy_cls, **strategy_params)
        else:
            cerebro.addstrategy(strategy_cls)

        cerebro.broker.setcash(cash)
        cerebro.broker.setcommission(commission=commission)

        # 分析器
        cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")
        cerebro.addanalyzer(bt.analyzers.DrawDown, _name="drawdown")
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")
        cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")

        logger = logging.getLogger("neostock2.backtrader")
        logger.info(f"開始回測: 初始資金={cash:,.0f}, 手續費={commission}")

        results = cerebro.run()
        strat = results[0]

        final_value = cerebro.broker.getvalue()
        pnl = final_value - cash
        roi = (pnl / cash) * 100

        # 解析分析結果
        sharpe = strat.analyzers.sharpe.get_analysis()
        drawdown = strat.analyzers.drawdown.get_analysis()
        trades = strat.analyzers.trades.get_analysis()
        returns = strat.analyzers.returns.get_analysis()

        total_trades = trades.get("total", {}).get("total", 0)
        won = trades.get("won", {}).get("total", 0)
        lost = trades.get("lost", {}).get("total", 0)
        win_rate = (won / total_trades * 100) if total_trades > 0 else 0

        result = {
            "initial_cash": cash,
            "final_value": round(final_value, 2),
            "pnl": round(pnl, 2),
            "roi_pct": round(roi, 2),
            "sharpe_ratio": sharpe.get("sharperatio", None),
            "max_drawdown_pct": round(drawdown.get("max", {}).get("drawdown", 0), 2),
            "total_trades": total_trades,
            "won": won,
            "lost": lost,
            "win_rate_pct": round(win_rate, 2),
            "annualized_return": returns.get("rnorm100", None),
        }

        logger.info(
            f"回測完成: ROI={roi:.2f}%, 勝率={win_rate:.1f}%, "
            f"最大回撤={result['max_drawdown_pct']}%"
        )

        return result

    # === 示範：SMA 交叉 Backtrader 策略 ===
    class BTSmaCross(bt.Strategy):
        """Backtrader 版均線交叉策略（可直接用於回測）"""

        params = (
            ("short_period", 5),
            ("long_period", 20),
        )

        def __init__(self):
            self.sma_short = bt.indicators.SMA(
                self.data.close, period=self.params.short_period
            )
            self.sma_long = bt.indicators.SMA(
                self.data.close, period=self.params.long_period
            )
            self.crossover = bt.indicators.CrossOver(self.sma_short, self.sma_long)

        def next(self):
            if not self.position:
                if self.crossover > 0:
                    self.buy()
            elif self.crossover < 0:
                self.close()

    BACKTRADER_AVAILABLE = True

except ImportError:
    BACKTRADER_AVAILABLE = False

    def run_backtest(*args, **kwargs):
        return {"error": "Backtrader 未安裝，請執行 pip install backtrader"}

    class BTSmaCross:
        pass

    logging.getLogger("neostock2.backtrader").warning(
        "Backtrader 未安裝，回測功能不可用"
    )
