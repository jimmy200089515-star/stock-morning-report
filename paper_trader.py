# -*- coding: utf-8 -*-
"""
紙上交易機器人（Paper Trader）

每天執行一次：
1. 載入過去的投資組合 (paper_portfolio.json)
2. 抓今日全市場 + 跑評分
3. 套用「最佳化策略」進場/出場
4. 更新投資組合並存檔
5. 產生 HTML 報告（可被早報 mailer 嵌入或單獨寄送）

跟 backtest.py 共用同樣的策略邏輯，差別在 paper_trader 是「以今日為準」一步一步往前走。
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime

import yfinance as yf

from backtest import (
    StrategyConfig, BUY_FEE, SELL_FEE, score_at, resolve_ticker,
    build_universe,
)
from config import HOLDINGS_TW, TW_SECTORS
from signals import signals_from_paper_actions, TradeSignal

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


PORTFOLIO_FILE = "paper_portfolio.json"


# ============================================================
# 預設策略 — 由 optimizer.py 6 個月回測結果挑出
#
# 選這組的理由：
#   - 6 個月模擬報酬 +83%（20萬 → 36.5萬）
#   - 最大回撤僅 -9.3%（非槓桿組最低）
#   - 含做空：在弱勢股出現高分數翻轉時可賺空頭
#   - 無融資 = 無追繳爆倉風險，最貼近實際操作
#
# 若想更積極（風險自負）：
#   leverage=2.0      → 預期 +207%、回撤 -20%
#   leverage=2.5 + 同設定 → 預期 +302%、回撤 -25%（接近融資追繳）
# ============================================================
DEFAULT_STRATEGY = StrategyConfig(
    name="technical-exit",
    initial_capital=200_000,
    max_positions=5,
    max_hold_days=120,                 # 安全網（殭屍部位）
    long_entry_threshold=9,            # 進場分數 ≥9
    long_exit_bearish_score=6,         # 技術翻空門檻
    long_exit_break_ma="ma20",         # 跌破月線出場 → MA20 隨股價拉高自然 trailing
    long_exit_break_swing=5,           # 跌破前 5 日低 → 短期支撐失守出場
    catastrophic_stop=-0.12,           # 極端虧損保護 -12%（黑天鵝防線）
    enable_short=False,                # 不做空，熊市抱現金
    leverage=1.0,
)


# ============================================================
# 投資組合儲存
# ============================================================
def load_portfolio() -> dict:
    if not os.path.exists(PORTFOLIO_FILE):
        return {
            "cash": DEFAULT_STRATEGY.initial_capital,
            "positions": {},  # code -> {direction, entry_price, shares, entry_date, name, ...}
            "trade_log": [],
            "equity_history": [],
            "started_at": datetime.now().strftime("%Y-%m-%d"),
        }
    with open(PORTFOLIO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(pf: dict):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(pf, f, ensure_ascii=False, indent=2, default=str)


# ============================================================
# 取得今日訊號（全市場跑一次評分）
# ============================================================
def get_today_signals(universe, verbose=True) -> dict:
    """對 universe 內每檔抓最新歷史並算當日分數。
    回傳：{code: {score, close, change_pct, bias, name}}
    """
    signals = {}
    for code, name in universe:
        ticker = resolve_ticker(code)
        if not ticker:
            continue
        try:
            hist = yf.Ticker(ticker).history(period="6mo")
            if hist.empty or len(hist) < 80:
                continue
            opens = hist["Open"].tolist()
            highs = hist["High"].tolist()
            lows = hist["Low"].tolist()
            closes = hist["Close"].tolist()
            volumes = hist["Volume"].tolist()
            sig = score_at(opens, highs, lows, closes, volumes, len(closes) - 1)
            if sig:
                sig["name"] = name
                signals[code] = sig
                if verbose:
                    print(f"  {code} {name}: score={sig['score']:+d} close={sig['close']:.1f}")
        except Exception:
            pass
        time.sleep(0.15)
    return signals


# ============================================================
# 紙上交易：套用策略到當日訊號 + 投資組合
# ============================================================
def step_one_day(pf: dict, signals: dict, cfg: StrategyConfig,
                  today: str) -> dict:
    """前進一日：處理出場、進場，更新權益。回傳當日動作摘要。"""
    actions = {"date": today, "buys": [], "sells": []}

    cash = pf["cash"]
    positions = pf["positions"]

    # ---- 累加借錢成本 ----
    for code, pos in positions.items():
        if pos["direction"] == "long" and pos.get("leverage", 1) > 1:
            borrowed = pos["shares"] * pos["entry_price"] * (1 - 1 / pos["leverage"])
            pos["borrow_cost"] = pos.get("borrow_cost", 0) + borrowed * cfg.margin_daily_rate
        elif pos["direction"] == "short":
            notional = pos["shares"] * pos["entry_price"]
            pos["borrow_cost"] = pos.get("borrow_cost", 0) + notional * cfg.short_borrow_daily_rate

    # ---- 處理出場（純技術判斷）----
    today_dt = datetime.strptime(today, "%Y-%m-%d")
    for code in list(positions.keys()):
        if code not in signals:
            continue
        pos = positions[code]
        sig = signals[code]
        curr = sig["close"]
        entry_dt = datetime.strptime(pos["entry_date"], "%Y-%m-%d")
        hold_days = (today_dt - entry_dt).days
        score = sig["score"]

        reason = None
        if pos["direction"] == "long":
            pnl_pct = (curr - pos["entry_price"]) / pos["entry_price"]
            ma_level = sig.get(cfg.long_exit_break_ma) or 0
            swing_low = sig.get(f"low_{cfg.long_exit_break_swing}") or 0
            catastrophic = pos["entry_price"] * (1 + cfg.catastrophic_stop)

            if curr < ma_level and ma_level > 0:
                reason = f"跌破{cfg.long_exit_break_ma.upper()} ({ma_level:.2f}) ｜ {pnl_pct:+.1%}"
            elif curr < swing_low and swing_low > 0:
                reason = f"跌破前 {cfg.long_exit_break_swing} 日低 ({swing_low:.2f}) ｜ {pnl_pct:+.1%}"
            elif curr <= catastrophic:
                reason = f"極端停損 {cfg.catastrophic_stop:+.0%} ({pnl_pct:+.1%})"
            elif score <= -cfg.long_exit_bearish_score:
                reason = f"技術翻空 分數{score} ({pnl_pct:+.1%})"
            elif hold_days >= cfg.max_hold_days:
                reason = f"持有滿{hold_days}日安全網 ({pnl_pct:+.1%})"
        else:  # short
            pnl_pct = (pos["entry_price"] - curr) / pos["entry_price"]
            price_rise = (curr - pos["entry_price"]) / pos["entry_price"]
            ma_level = sig.get(cfg.short_exit_break_ma) or 0
            swing_high = sig.get(f"high_{cfg.short_exit_break_swing}") or 0
            catastrophic = pos["entry_price"] * (1 + cfg.short_catastrophic_stop)

            if curr > ma_level and ma_level > 0:
                reason = f"站上{cfg.short_exit_break_ma.upper()} ({ma_level:.2f}) ｜ {pnl_pct:+.1%}"
            elif curr > swing_high and swing_high > 0:
                reason = f"突破前 {cfg.short_exit_break_swing} 日高 ({swing_high:.2f}) ｜ {pnl_pct:+.1%}"
            elif curr >= catastrophic:
                reason = f"空單極端停損 +{cfg.short_catastrophic_stop:.0%} ({pnl_pct:+.1%})"
            elif score >= cfg.short_exit_bullish_score:
                reason = f"空單轉多 分數{score} ({pnl_pct:+.1%})"
            elif hold_days >= cfg.max_hold_days:
                reason = f"持有滿{hold_days}日安全網 ({pnl_pct:+.1%})"

        if reason:
            # 計算實現損益
            if pos["direction"] == "long":
                proceeds = pos["shares"] * curr * (1 - SELL_FEE)
                cost = pos["shares"] * pos["entry_price"] * (1 + BUY_FEE)
                pnl = proceeds - cost - pos.get("borrow_cost", 0)
                cash_back = pos["margin_locked"] + pnl
            else:
                proceeds_initial = pos["shares"] * pos["entry_price"] * (1 - SELL_FEE)
                cost_to_close = pos["shares"] * curr * (1 + BUY_FEE)
                pnl = proceeds_initial - cost_to_close - pos.get("borrow_cost", 0)
                cash_back = pos["margin_locked"] + pnl

            cash += cash_back
            sell_entry = {
                "code": code, "name": pos["name"],
                "direction": pos["direction"],
                "entry_date": pos["entry_date"], "exit_date": today,
                "entry_price": pos["entry_price"], "exit_price": round(curr, 2),
                "shares": pos["shares"], "hold_days": hold_days,
                "pnl": round(pnl), "pnl_pct": pnl_pct,
                "reason": reason,
            }
            pf["trade_log"].append(sell_entry)
            actions["sells"].append(sell_entry)
            del positions[code]

    # ---- 處理進場 ----
    long_count = sum(1 for p in positions.values() if p["direction"] == "long")
    long_slots = cfg.max_positions - long_count
    if long_slots > 0:
        cands = sorted(
            [(c, s) for c, s in signals.items()
             if c not in positions and s["score"] >= cfg.long_entry_threshold],
            key=lambda x: -x[1]["score"],
        )
        for code, sig in cands[:long_slots]:
            if cash < 5000:
                break
            price = sig["close"]
            allocation = (cash / max(long_slots, 1)) * 0.95
            if cfg.leverage > 1:
                allocation *= cfg.leverage
            shares = int(allocation / (price * (1 + BUY_FEE)))
            if shares <= 0:
                continue
            cost_full = shares * price * (1 + BUY_FEE)
            margin = cost_full / cfg.leverage
            if margin > cash:
                continue
            cash -= margin
            positions[code] = {
                "code": code, "name": sig.get("name", code),
                "direction": "long",
                "entry_price": round(price, 2),
                "shares": shares,
                "entry_date": today,
                "leverage": cfg.leverage,
                "margin_locked": round(margin, 2),
                "borrow_cost": 0.0,
                "entry_score": sig["score"],
            }
            actions["buys"].append({
                "code": code, "name": sig.get("name", code),
                "direction": "long",
                "price": round(price, 2), "shares": shares,
                "score": sig["score"],
            })

    # 空單
    if cfg.enable_short:
        short_count = sum(1 for p in positions.values() if p["direction"] == "short")
        short_slots = cfg.max_short_positions - short_count
        if short_slots > 0:
            cands = sorted(
                [(c, s) for c, s in signals.items()
                 if c not in positions and s["score"] <= cfg.short_entry_threshold],
                key=lambda x: x[1]["score"],
            )
            for code, sig in cands[:short_slots]:
                price = sig["close"]
                allocation = cash * 0.15
                shares = int(allocation / (price * 0.9))
                if shares <= 0:
                    continue
                margin = shares * price * 0.9
                if margin > cash:
                    continue
                cash -= margin
                positions[code] = {
                    "code": code, "name": sig.get("name", code),
                    "direction": "short",
                    "entry_price": round(price, 2),
                    "shares": shares,
                    "entry_date": today,
                    "leverage": 1.0,
                    "margin_locked": round(margin, 2),
                    "borrow_cost": 0.0,
                    "entry_score": sig["score"],
                }
                actions["buys"].append({
                    "code": code, "name": sig.get("name", code),
                    "direction": "short",
                    "price": round(price, 2), "shares": shares,
                    "score": sig["score"],
                })

    # ---- 結算當日權益 ----
    equity = cash
    for code, pos in positions.items():
        if code in signals:
            curr = signals[code]["close"]
        else:
            curr = pos["entry_price"]
        if pos["direction"] == "long":
            proceeds = pos["shares"] * curr * (1 - SELL_FEE)
            cost = pos["shares"] * pos["entry_price"] * (1 + BUY_FEE)
            unrealized = proceeds - cost - pos.get("borrow_cost", 0)
            equity += pos["margin_locked"] + unrealized
        else:
            proceeds_initial = pos["shares"] * pos["entry_price"] * (1 - SELL_FEE)
            cost_to_close = pos["shares"] * curr * (1 + BUY_FEE)
            unrealized = proceeds_initial - cost_to_close - pos.get("borrow_cost", 0)
            equity += pos["margin_locked"] + unrealized

    pf["cash"] = cash
    pf["positions"] = positions
    pf["equity_history"].append({
        "date": today, "equity": round(equity), "cash": round(cash),
        "n_positions": len(positions),
    })
    actions["equity"] = round(equity)
    actions["cash"] = round(cash)
    actions["n_positions"] = len(positions)
    return actions


# ============================================================
# HTML 報告
# ============================================================
def render_html(pf: dict, today_actions: dict, cfg: StrategyConfig) -> str:
    initial = cfg.initial_capital
    equity = today_actions["equity"]
    pnl = equity - initial
    pnl_pct = pnl / initial
    color = "#d32f2f" if pnl >= 0 else "#2e7d32"

    # 當日進出表
    sells_html = ""
    if today_actions["sells"]:
        rows = "".join(
            f"<tr><td>{s['code']}</td><td>{s['name']}</td>"
            f"<td style='text-align:right;'>{s['entry_price']:.1f}</td>"
            f"<td style='text-align:right;'>{s['exit_price']:.1f}</td>"
            f"<td style='text-align:right;color:{'#d32f2f' if s['pnl']>=0 else '#2e7d32'};'>"
            f"${s['pnl']:+,} ({s['pnl_pct']:+.1%})</td>"
            f"<td style='font-size:11px;color:#666;'>{s['reason']}</td></tr>"
            for s in today_actions["sells"]
        )
        sells_html = f"""
        <h3 style='color:#c62828;margin-top:14px;'>📤 今日出場 ({len(today_actions['sells'])})</h3>
        <table style='width:100%;border-collapse:collapse;font-size:12px;'>
          <tr style='background:#fff5f5;'><th>代號</th><th>名稱</th><th>進</th><th>出</th><th>損益</th><th>原因</th></tr>
          {rows}
        </table>"""

    buys_html = ""
    if today_actions["buys"]:
        rows = "".join(
            f"<tr><td>{'📈' if b['direction']=='long' else '📉'} {b['code']}</td>"
            f"<td>{b['name']}</td>"
            f"<td style='text-align:right;'>{b['price']:.1f}</td>"
            f"<td style='text-align:right;'>{b['shares']:,}</td>"
            f"<td style='text-align:right;color:#888;'>分 {b['score']:+d}</td></tr>"
            for b in today_actions["buys"]
        )
        buys_html = f"""
        <h3 style='color:#2e7d32;margin-top:14px;'>📥 今日進場 ({len(today_actions['buys'])})</h3>
        <table style='width:100%;border-collapse:collapse;font-size:12px;'>
          <tr style='background:#f5fff5;'><th>標的</th><th>名稱</th><th>價</th><th>股數</th><th>分數</th></tr>
          {rows}
        </table>"""

    # 現有部位
    pos_rows = ""
    for code, pos in pf["positions"].items():
        d_icon = "📈" if pos["direction"] == "long" else "📉"
        pos_rows += (
            f"<tr><td>{d_icon} {code}</td><td>{pos['name']}</td>"
            f"<td style='text-align:right;'>{pos['entry_price']:.1f}</td>"
            f"<td style='text-align:right;'>{pos['shares']:,}</td>"
            f"<td style='font-size:11px;color:#666;'>{pos['entry_date']}</td></tr>"
        )
    pos_html = ""
    if pos_rows:
        pos_html = f"""
        <h3 style='color:#1565c0;margin-top:14px;'>📋 目前持倉 ({len(pf['positions'])})</h3>
        <table style='width:100%;border-collapse:collapse;font-size:12px;'>
          <tr style='background:#e3f2fd;'><th>標的</th><th>名稱</th><th>進場價</th><th>股數</th><th>進場日</th></tr>
          {pos_rows}
        </table>"""

    return f"""
    <div style='max-width:780px;margin:0 auto;font-family:"Microsoft JhengHei",Arial,sans-serif;
                background:#fff;border:1px solid #ddd;border-radius:8px;padding:20px;'>
      <div style='background:linear-gradient(135deg,#1a237e,#3949ab);color:#fff;padding:16px 20px;
                  border-radius:6px;margin-bottom:16px;'>
        <h2 style='margin:0;font-size:18px;'>🤖 紙上交易機器人 · 每日結算</h2>
        <div style='font-size:12px;opacity:.85;margin-top:4px;'>{today_actions["date"]}</div>
      </div>

      <div style='padding:12px 16px;background:#f5f7fa;border-radius:6px;margin-bottom:12px;'>
        <div style='font-size:13px;color:#555;'>當前權益</div>
        <div style='font-size:24px;font-weight:700;color:{color};'>
            NT$ {equity:,}
            <span style='font-size:14px;'>（{pnl:+,} / {pnl_pct:+.2%}）</span>
        </div>
        <div style='font-size:11px;color:#888;margin-top:4px;'>
            起始 NT$ {initial:,} ｜ 現金 NT$ {today_actions["cash"]:,} ｜
            持倉 {today_actions["n_positions"]} 檔 ｜
            累積交易 {len(pf["trade_log"])} 筆
        </div>
      </div>

      {sells_html}
      {buys_html}
      {pos_html}

      <div style='margin-top:16px;padding:10px 14px;background:#fffde7;border-left:3px solid #f9a825;
                  border-radius:4px;font-size:11px;color:#5d4037;'>
          策略：{cfg.name} ｜ 進場分數≥{cfg.long_entry_threshold} ｜
          技術出場：跌破 {cfg.long_exit_break_ma.upper()} 或前 {cfg.long_exit_break_swing} 日低 ｜
          極端停損 {cfg.catastrophic_stop:.0%} ｜
          最長持有 {cfg.max_hold_days} 日
          {' ｜ 含做空' if cfg.enable_short else ''}
          {f' ｜ {cfg.leverage}x 槓桿' if cfg.leverage > 1 else ''}
      </div>
    </div>
    """


# ============================================================
# 對外
# ============================================================
def run_paper_trader(cfg: StrategyConfig = None) -> dict:
    """執行紙上交易一日。回傳 HTML 報告字串 + actions。"""
    if cfg is None:
        cfg = DEFAULT_STRATEGY

    print("[Paper] 載入投資組合 ...")
    pf = load_portfolio()
    print(f"      → 現金 NT$ {pf['cash']:,.0f} ｜ 持倉 {len(pf['positions'])} 檔")

    print("[Paper] 取得今日全市場訊號 ...")
    universe = build_universe()
    signals = get_today_signals(universe, verbose=False)
    print(f"      → 取得 {len(signals)} 檔訊號")

    today = datetime.now().strftime("%Y-%m-%d")
    print("[Paper] 套用策略 ...")
    actions = step_one_day(pf, signals, cfg, today)
    print(f"      → 進場 {len(actions['buys'])} / 出場 {len(actions['sells'])} / "
          f"權益 NT$ {actions['equity']:,}")

    save_portfolio(pf)
    print("[Paper] 投資組合已儲存")

    # 為了讓 mailer 顯示「股數」，sells 要補上 shares 欄位
    for s in actions.get("sells", []):
        s.setdefault("shares", 0)

    # 出場動作沒帶 shares，但 trade_log 有，補一下
    # （從 actions["sells"] 的 trade_log entry 取 shares）
    if actions.get("sells"):
        for sell in actions["sells"]:
            # find matching trade log entry
            for tr in reversed(pf["trade_log"]):
                if (tr["code"] == sell["code"]
                    and tr["exit_date"] == actions["date"]
                    and tr["direction"] == sell["direction"]):
                    sell["shares"] = tr["shares"]
                    break

    trade_signals = signals_from_paper_actions(actions, cfg, signals)
    html = render_html(pf, actions, cfg)
    return {"html": html, "actions": actions, "portfolio": pf,
            "trade_signals": [s.to_dict() for s in trade_signals]}


def main():
    result = run_paper_trader()
    # 單機跑：寫一份 HTML 出來看
    with open("paper_trader_report.html", "w", encoding="utf-8") as f:
        f.write(result["html"])
    print("\n💾 報告存到 paper_trader_report.html")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
