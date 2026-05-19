# -*- coding: utf-8 -*-
"""
跑 3 個期間（1 個月 / 3 個月 / 6 個月）回測同一套最佳策略，
比較績效是否在不同期間都穩定。
"""

from __future__ import annotations

import io
import sys

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from backtest import (
    StrategyConfig, build_universe, download_history, simulate,
    calc_metrics, print_report,
)


PERIODS = [
    ("1 個月",  22),
    ("3 個月",  66),
    ("6 個月", 132),
]


def make_config(name: str, days: int) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        initial_capital=200_000,
        backtest_days=days,
        max_positions=5,
        max_hold_days=120,
        long_entry_threshold=9,
        long_exit_break_ma="ma20",    # 跌破月線出場
        long_exit_break_swing=5,      # 跌破前 5 日低出場
        catastrophic_stop=-0.12,      # 極端虧損保護
        enable_short=False,           # 純做多
    )


def main():
    print("=" * 70)
    print("🔁 多期間回測：純做多 + 技術出場")
    print("   進場分數≥9 ｜ 跌破MA20出場 ｜ 跌破5日低出場 ｜ 極端-12%保護")
    print("=" * 70)

    universe = build_universe()
    # 抓 9 個月歷史，足夠最久 6 個月回測 + 60 日 lookback
    data = download_history(universe, period="9mo", verbose=False)
    print(f"使用 {len(data)} 檔股票\n")

    summaries = []
    for label, days in PERIODS:
        cfg = make_config(label, days)
        result = simulate(data, cfg)
        m = calc_metrics(result)
        summaries.append((label, result, m))
        print_report(result, top_n=3)

    # 總覽比較
    print("\n" + "=" * 70)
    print("📋 三期間比較總覽")
    print("=" * 70)
    print(f"{'期間':<8} {'本金':>10} {'最終':>12} {'報酬%':>8} {'勝率':>7} {'回撤%':>9} {'交易':>5}  {'多/空':>7}")
    print("-" * 70)
    for label, result, m in summaries:
        cfg = result["config"]
        print(f"{label:<8} {cfg['initial_capital']:>10,} {result['final_cash']:>12,.0f}  "
              f"{m['pnl_pct']:>+7.2%} {m['win_rate']:>6.1%}  "
              f"{m['max_dd']:>+8.2%}  {m['n_trades']:>4}   "
              f"{m['n_long']}/{m['n_short']}")
    print("=" * 70)

    # 換算年化報酬
    print("\n💡 年化報酬概算：")
    for label, result, m in summaries:
        days = result["config"]["backtest_days"]
        cal_days = days * 1.4  # 22 trading days ≈ 31 calendar days
        annualized = (1 + m["pnl_pct"]) ** (365 / cal_days) - 1
        print(f"   {label}：原始 {m['pnl_pct']:+.2%} → 年化 ≈ {annualized:+.2%}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
