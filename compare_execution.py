# -*- coding: utf-8 -*-
"""
對比兩種執行假設：
- 寬鬆版（simulate）：今日收盤決定 + 今日收盤成交  ← 有半作弊嫌疑
- 嚴謹版（simulate_strict）：今日收盤決定 + 明日開盤成交  ← 真實可執行

跑 1/3/6 個月三組期間，看落差有多大。
"""

from __future__ import annotations

from backtest import (
    StrategyConfig, build_universe, download_history,
    simulate, simulate_strict, calc_metrics,
)


PERIODS = [("1 個月", 22), ("3 個月", 66), ("6 個月", 132)]


def make_config(name: str, days: int) -> StrategyConfig:
    return StrategyConfig(
        name=name,
        initial_capital=200_000,
        backtest_days=days,
        max_positions=5,
        max_hold_days=120,
        long_entry_threshold=9,
        long_exit_break_ma="ma20",
        long_exit_break_swing=5,
        catastrophic_stop=-0.12,
        enable_short=False,
    )


def main():
    print("=" * 78)
    print("🔍 執行假設比較：寬鬆（同日收盤）vs 嚴謹（明日開盤）")
    print("   策略：純做多 + 跌破MA20/前5日低出場 + -12%保護")
    print("=" * 78)

    universe = build_universe()
    data = download_history(universe, period="9mo", verbose=False)
    print(f"使用 {len(data)} 檔股票\n")

    results = []
    for label, days in PERIODS:
        cfg_loose = make_config(f"{label}·寬鬆", days)
        cfg_strict = make_config(f"{label}·嚴謹", days)

        print(f"\n----- {label}（{days} 交易日）-----")
        print(f"  跑寬鬆版（同日收盤成交）...")
        r_loose = simulate(data, cfg_loose)
        m_loose = calc_metrics(r_loose)

        print(f"  跑嚴謹版（明日開盤成交）...")
        r_strict = simulate_strict(data, cfg_strict)
        m_strict = calc_metrics(r_strict)

        results.append((label, days, m_loose, m_strict))

    # 印出比較表
    print("\n" + "=" * 78)
    print("📊 對比結果")
    print("=" * 78)
    print(f"{'期間':<8} {'指標':<10} {'寬鬆版':>14} {'嚴謹版':>14} {'落差':>14}")
    print("-" * 78)

    for label, days, m_loose, m_strict in results:
        diff_pct = m_strict["pnl_pct"] - m_loose["pnl_pct"]
        ratio = (m_strict["pnl_pct"] / m_loose["pnl_pct"]
                 if m_loose["pnl_pct"] > 0 else float("nan"))

        rows = [
            ("報酬率",  f"{m_loose['pnl_pct']:+.2%}",  f"{m_strict['pnl_pct']:+.2%}",
             f"{diff_pct:+.2%} ({ratio*100:.0f}%)" if ratio == ratio else "—"),
            ("勝率",    f"{m_loose['win_rate']:.1%}",   f"{m_strict['win_rate']:.1%}",
             f"{(m_strict['win_rate']-m_loose['win_rate'])*100:+.1f}pt"),
            ("交易數",  f"{m_loose['n_trades']}",       f"{m_strict['n_trades']}",
             f"{m_strict['n_trades']-m_loose['n_trades']:+d}"),
            ("最大回撤", f"{m_loose['max_dd']:+.2%}",   f"{m_strict['max_dd']:+.2%}",
             f"{(m_strict['max_dd']-m_loose['max_dd'])*100:+.2f}pt"),
            ("獲利因子", f"{m_loose['profit_factor']:.2f}",
             f"{m_strict['profit_factor']:.2f}",
             f"{m_strict['profit_factor']-m_loose['profit_factor']:+.2f}"),
        ]
        for i, (metric, l_val, s_val, diff) in enumerate(rows):
            prefix = label if i == 0 else ""
            print(f"{prefix:<8} {metric:<10} {l_val:>14} {s_val:>14} {diff:>14}")
        print("-" * 78)

    # 總結
    print("\n💡 解讀：")
    for label, days, m_loose, m_strict in results:
        if m_loose["pnl_pct"] > 0:
            ratio = m_strict["pnl_pct"] / m_loose["pnl_pct"]
            print(f"   {label}：嚴謹版報酬約為寬鬆版的 {ratio*100:.0f}%（"
                  f"落差 {(m_loose['pnl_pct']-m_strict['pnl_pct'])*100:+.1f} 個百分點）")

    print("\n📌 結論：實盤預期落在「嚴謹版」附近，寬鬆版屬於樂觀估計。")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
