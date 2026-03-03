import sys
import argparse
import pandas as pd
try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from research.universe import StrategyUniverse
from research.strategies import TrendMAStrategy
from research.backtest_engine import BacktestEngine
from research.ranker import Ranker
from research.walk_forward import WalkForwardAnalysis

def main():
    parser = argparse.ArgumentParser(description="Run Research Pipeline")
    parser.add_argument("--ticker", type=str, default="0050", help="Ticker symbol")
    parser.add_argument("--style", type=str, default="A", choices=["A", "B"], help="Strategy Style (A/B)")
    args = parser.parse_args()

    print(f"=== Research Pipeline: {args.ticker} (Style {args.style}) ===")
    
    # 1. 初始環境
    universe = StrategyUniverse([args.ticker])
    universe.load_data()
    
    engine = BacktestEngine(style=args.style)
    ranker = Ranker(style=args.style)
    
    # 2. 定義參數空間 (MVP 小一點)
    # S2 Trend MA
    # Fast: 10, 20, 30
    # Slow: 60, 90, 120
    param_grid = {
        'fast_windows': [10, 20, 30],
        'slow_windows': [60, 90, 120]
    }
    
    # 3. 執行 Walk Forward
    wfa = WalkForwardAnalysis(universe, TrendMAStrategy, ranker, engine)
    
    oos_equity = wfa.run(args.ticker, param_grid)
    
    if oos_equity.empty:
        print("Error: No OOS Equity generated.")
        return

    # 4. 輸出結果
    final_return = (oos_equity.iloc[-1] / oos_equity.iloc[0]) - 1
    print(f"\n=== Result Summary ===")
    print(f"Total Return: {final_return:.2%}")
    print(f"Final Equity: {oos_equity.iloc[-1]:.2f}")
    
    # 儲存 Equity Curve CSV
    output_dir = PROJECT_ROOT / "research" / "results"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = output_dir / f"{args.ticker}_Style{args.style}_OOS.csv"
    oos_equity.to_csv(csv_path)
    print(f"Saved OOS Equity to: {csv_path}")
    
    # 簡單繪圖 (Optional if internal env)
    # 簡單繪圖 (Optional)
    if HAS_MATPLOTLIB:
        try:
            plt.figure(figsize=(10, 6))
            oos_equity.plot(title=f"OOS Equity: {args.ticker} Style-{args.style}")
            plt.grid(True)
            img_path = output_dir / f"{args.ticker}_Style{args.style}_OOS.png"
            plt.savefig(img_path)
            print(f"Saved Plot to: {img_path}")
        except Exception as e:
            print(f"Plotting failed: {e}")
    else:
        print("Matplotlib not installed, skipping plot generation.")

if __name__ == "__main__":
    main()
