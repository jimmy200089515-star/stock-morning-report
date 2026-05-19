# -*- coding: utf-8 -*-
"""
策略參數最佳化 — Grid Search

跑多組停損/停利/進場分數組合，找最高報酬/回撤比的設定。
"""

from __future__ import annotations

import io
import sys
import json
import itertools
from dataclasses import asdict

from backtest import (
    StrategyConfig, build_universe, download_history, simulate, calc_metrics
)


# ============================================================
# 參數網格
# ============================================================
PARAM_GRID = {
    "long_stop_loss":     [-0.05, -0.07, -0.10],
    "long_take_profit":   [0.10, 0.15, 0.20],
    "long_entry_threshold": [5, 7, 9],
    "max_hold_days":      [10, 20],
}

# 期間設定
PERIOD = "9mo"
BACKTEST_DAYS = 120  # 約 6 個月

# 是否開做空 / 槓桿（在最佳組合上加測）
INCLUDE_SHORT_TEST = True
INCLUDE_LEVERAGE_TEST = True


def grid_combos(grid: dict):
    keys = list(grid.keys())
    for vals in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, vals))


def run_one(data, base_cfg: dict, params: dict) -> dict:
    cfg = StrategyConfig(**{**base_cfg, **params})
    result = simulate(data, cfg)
    m = calc_metrics(result)
    return {"params": params, **m, "final": result["final_cash"]}


def optimize():
    print("=" * 70)
    print("🔬 策略參數最佳化")
    print(f"網格大小：{len(list(grid_combos(PARAM_GRID)))} 組")
    print(f"回測期間：最近 {BACKTEST_DAYS} 交易日（約 6 個月）")
    print("=" * 70)

    universe = build_universe()
    data = download_history(universe, period=PERIOD)
    print(f"使用 {len(data)} 檔股票")

    base = {
        "initial_capital": 200_000,
        "max_positions": 5,
        "backtest_days": BACKTEST_DAYS,
    }

    results = []
    combos = list(grid_combos(PARAM_GRID))
    for i, params in enumerate(combos, 1):
        print(f"\n[{i}/{len(combos)}] 測試 {params}")
        try:
            r = run_one(data, base, params)
            print(f"  → 報酬 {r['pnl_pct']:+.2%} ｜ 勝率 {r['win_rate']:.1%} ｜ "
                  f"回撤 {r['max_dd']:+.2%} ｜ 評分 {r['score']:.2f}")
            results.append(r)
        except Exception as e:
            print(f"  ❌ 失敗 {e}")

    # 依「評分」排序
    results.sort(key=lambda x: -x["score"])

    print("\n" + "=" * 70)
    print("🏆 TOP 10 最佳參數組合（依 報酬/回撤 比排序）")
    print("=" * 70)
    print(f"{'名次':<4}{'報酬':>9}{'勝率':>8}{'回撤':>9}{'交易數':>7}{'評分':>7}  參數")
    for i, r in enumerate(results[:10], 1):
        p = r["params"]
        print(f"{i:<4}{r['pnl_pct']:>+8.2%}{r['win_rate']:>7.1%}"
              f"{r['max_dd']:>+8.2%}{r['n_trades']:>7}{r['score']:>7.2f}  "
              f"SL={p['long_stop_loss']:+.0%} TP={p['long_take_profit']:+.0%} "
              f"分={p['long_entry_threshold']} 日={p['max_hold_days']}")

    # 存最佳組合
    best = results[0]
    with open("optimizer_result.json", "w", encoding="utf-8") as f:
        json.dump({"top_10": results[:10], "all": results}, f,
                  ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 完整結果存到 optimizer_result.json")

    # ----------------------------------------
    # 用最佳參數做進階測試（做空 / 槓桿）
    # ----------------------------------------
    if INCLUDE_SHORT_TEST or INCLUDE_LEVERAGE_TEST:
        print("\n" + "=" * 70)
        print("🧪 在最佳參數基礎上測試做空 / 槓桿")
        print("=" * 70)
        best_params = best["params"]

        scenarios = [{"name": "純做多（最佳）", **best_params}]

        if INCLUDE_SHORT_TEST:
            scenarios.append({
                "name": "做多 + 做空",
                **best_params,
                "enable_short": True,
                "max_short_positions": 2,
            })

        if INCLUDE_LEVERAGE_TEST:
            scenarios.append({
                "name": "做多 + 2x 槓桿",
                **best_params,
                "leverage": 2.0,
            })
            scenarios.append({
                "name": "做多 + 2.5x 槓桿 + 做空",
                **best_params,
                "leverage": 2.5,
                "enable_short": True,
                "max_short_positions": 2,
            })

        for sc in scenarios:
            name = sc.pop("name")
            cfg = StrategyConfig(**{**base, **sc, "name": name})
            res = simulate(data, cfg)
            m = calc_metrics(res)
            print(f"\n📌 {name}")
            print(f"   最終 NT$ {res['final_cash']:,.0f} ｜ 報酬 {m['pnl_pct']:+.2%} ｜ "
                  f"勝率 {m['win_rate']:.1%} ｜ 交易 {m['n_trades']} "
                  f"(多 {m['n_long']} 空 {m['n_short']})")
            print(f"   獲利因子 {m['profit_factor']:.2f} ｜ 回撤 {m['max_dd']:+.2%} ｜ "
                  f"評分 {m['score']:.2f}")

    return results


if __name__ == "__main__":
    try:
        optimize()
    except Exception:
        import traceback
        traceback.print_exc()
