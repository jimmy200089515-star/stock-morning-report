# -*- coding: utf-8 -*-
"""
虛擬交易回測引擎 — 進階版

支援：
- 做多 / 做空（融券模擬）
- 槓桿（融資模擬，2x ~ 2.5x）
- 可調策略參數（停損停利、進場分數、持有日數、最大持倉）
- 任意回測期間（22日 / 6個月 / 1年）

費用模型：
- 買進手續費：0.1425%
- 賣出手續費：0.1425% + 證交稅 0.3% = 0.4425%
- 融資利率：6%/年（每日 0.0165%）
- 融券借券費：0.08%/日（年化 ~30%）
- 融券保證金：90%
"""

from __future__ import annotations

import io
import json
import logging
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

# 強制 stdout 用 UTF-8（只在主程式時做、避免被多次 import 重複包）
if sys.platform == "win32" and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import yfinance as yf

from config import HOLDINGS_TW, TW_SECTORS
from fetcher import _kd, _macd, _rsi, _sma

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ============================================================
# 策略參數
# ============================================================
@dataclass
class StrategyConfig:
    name: str = "default"
    initial_capital: int = 200_000

    # 通用
    max_positions: int = 5
    max_hold_days: int = 120                # 安全網（防殭屍部位），通常不觸發

    # ---- 做多 ----
    long_entry_threshold: int = 6           # 分數 >= 此值進多
    long_exit_bearish_score: int = 6        # 分數差 <= -此值 時出場
    # 技術出場規則（任一觸發即出場）：
    long_exit_break_ma: str = "ma20"        # 跌破此均線 → 出場（"ma10"/"ma20"/"ma60"）
    long_exit_break_swing: int = 5          # 跌破前 N 日低 → 出場
    catastrophic_stop: float = -0.12        # 極端虧損保護（最後一道防線）

    # ---- 做空 ----
    enable_short: bool = False
    short_entry_threshold: int = -6
    short_exit_bullish_score: int = 6
    short_exit_break_ma: str = "ma20"       # 站上此均線 → 空單出場
    short_exit_break_swing: int = 5         # 漲破前 N 日高 → 出場
    short_catastrophic_stop: float = 0.12   # 漲超過此 % → 出場
    max_short_positions: int = 2

    # 槓桿（融資）
    leverage: float = 1.0
    margin_daily_rate: float = 0.000165
    short_borrow_daily_rate: float = 0.0008

    # 回測期間
    backtest_days: int = 22


BUY_FEE = 0.001425
SELL_FEE = 0.001425 + 0.003


# ============================================================
# 工具
# ============================================================
def resolve_ticker(code: str) -> Optional[str]:
    for suffix in (".TW", ".TWO"):
        try:
            hist = yf.Ticker(f"{code}{suffix}").history(period="5d")
            if not hist.empty:
                return f"{code}{suffix}"
        except Exception:
            pass
    return None


def build_universe():
    uni = {}
    for code, name in HOLDINGS_TW:
        uni[code] = name
    for sector_stocks in TW_SECTORS.values():
        for code, name in sector_stocks:
            if code not in uni:
                uni[code] = name
    return sorted(uni.items())


_DATA_CACHE = {}


def download_history(universe, period="9mo", verbose=True) -> dict:
    """下載歷史資料；同 universe + period 會快取在記憶體。"""
    cache_key = (tuple(c for c, _ in universe), period)
    if cache_key in _DATA_CACHE:
        if verbose:
            print(f"  (使用快取，{len(_DATA_CACHE[cache_key])} 檔)")
        return _DATA_CACHE[cache_key]

    if verbose:
        print(f"\n下載 {len(universe)} 檔 × {period} 歷史...")
    data = {}
    for code, name in universe:
        ticker = resolve_ticker(code)
        if not ticker:
            continue
        try:
            hist = yf.Ticker(ticker).history(period=period)
            if hist.empty or len(hist) < 80:
                continue
            data[code] = {"name": name, "ticker": ticker, "hist": hist}
            if verbose:
                print(f"  {code} {name}: ✓ ({len(hist)} bars)")
        except Exception:
            pass
        time.sleep(0.15)

    _DATA_CACHE[cache_key] = data
    if verbose:
        print(f"  → 成功 {len(data)} 檔")
    return data


# ============================================================
# 評分（at-time）
# ============================================================
def score_at(opens, highs, lows, closes, volumes, idx) -> Optional[dict]:
    if idx < 60 or idx >= len(closes):
        return None

    c = closes[:idx + 1]; o = opens[:idx + 1]
    h = highs[:idx + 1]; l = lows[:idx + 1]; v = volumes[:idx + 1]
    if len(c) < 60:
        return None

    close = c[-1]; prev = c[-2]
    if prev <= 0:
        return None
    change_pct = (close - prev) / prev * 100

    ma5 = _sma(c, 5); ma10 = _sma(c, 10); ma20 = _sma(c, 20); ma60 = _sma(c, 60)
    rsi = _rsi(c, 14)
    k_val, d_val = _kd(h, l, c)
    dif, macd_line, osc = _macd(c)

    high_20 = max(h[-21:-1]) if len(h) >= 21 else 0
    low_20 = min(l[-21:-1]) if len(l) >= 21 else 0
    high_60 = max(h[-61:-1]) if len(h) >= 61 else 0
    low_60 = min(l[-61:-1]) if len(l) >= 61 else 0

    bull = 0; bear = 0

    if ma5 and ma20 and ma60:
        if close > ma5 > ma20 > ma60: bull += 3
        elif close < ma5 < ma20 < ma60: bear += 3
        elif close > ma20: bull += 1
        else: bear += 1

    if high_20 and prev < high_20 and close > high_20: bull += 5
    if high_60 and prev < high_60 and close > high_60: bull += 3
    if low_20 and prev > low_20 and close < low_20: bear += 5
    if low_60 and prev > low_60 and close < low_60: bear += 3
    if ma20 and prev < ma20 and close > ma20: bull += 2
    if ma20 and prev > ma20 and close < ma20: bear += 2

    if len(v) >= 6:
        avg_v5 = sum(v[-6:-1]) / 5 if sum(v[-6:-1]) > 0 else 0
        if avg_v5 > 0:
            ratio = v[-1] / avg_v5
            if ratio >= 1.5 and change_pct > 0: bull += 3
            elif ratio >= 1.5 and change_pct < 0: bear += 3

    if k_val is not None and d_val is not None:
        if k_val > d_val and k_val < 80: bull += 1
        elif k_val < d_val and k_val > 20: bear += 1
        if k_val >= 85: bear += 2
        elif k_val <= 15: bull += 1

    if rsi is not None:
        if rsi >= 80: bear += 2
        elif rsi <= 25: bull += 1
        elif rsi > 50: bull += 1
        else: bear += 1

    if dif is not None and macd_line is not None:
        if dif > macd_line and (osc or 0) > 0: bull += 1
        elif dif < macd_line and (osc or 0) < 0: bear += 1

    if change_pct > 9: bull = max(0, bull - 4)
    elif change_pct > 6: bull = max(0, bull - 2)
    if change_pct < -9: bear = max(0, bear - 4)
    elif change_pct < -6: bear = max(0, bear - 2)

    score = bull - bear

    # 給出場用：關鍵均線 + 近期支撐壓力
    low_5 = min(l[-6:-1]) if len(l) >= 6 else 0
    low_10 = min(l[-11:-1]) if len(l) >= 11 else 0
    high_5 = max(h[-6:-1]) if len(h) >= 6 else 0
    high_10 = max(h[-11:-1]) if len(h) >= 11 else 0

    return {
        "score": score, "bull": bull, "bear": bear,
        "close": close, "change_pct": change_pct, "rsi": rsi,
        # 技術位（給出場決策用）
        "ma5": ma5, "ma10": ma10, "ma20": ma20, "ma60": ma60,
        "low_5": low_5, "low_10": low_10, "low_20": low_20,
        "high_5": high_5, "high_10": high_10, "high_20": high_20,
    }


# ============================================================
# 模擬
# ============================================================
def simulate(data: dict, cfg: StrategyConfig) -> dict:
    """執行交易模擬。"""
    all_dates = sorted(set().union(*[set(d["hist"].index) for d in data.values()]))
    if len(all_dates) < 80:
        raise RuntimeError(f"歷史資料不足 ({len(all_dates)})")

    backtest_dates = all_dates[-cfg.backtest_days:]

    # 預備價量序列
    series_cache = {}
    date_to_idx = {}
    for code, info in data.items():
        hist = info["hist"]
        series_cache[code] = {
            "opens": hist["Open"].tolist(),
            "highs": hist["High"].tolist(),
            "lows": hist["Low"].tolist(),
            "closes": hist["Close"].tolist(),
            "volumes": hist["Volume"].tolist(),
        }
        date_to_idx[code] = {d: i for i, d in enumerate(hist.index)}

    cash = float(cfg.initial_capital)
    positions: dict[str, dict] = {}  # code -> position
    trades: list[dict] = []
    equity_history: list[dict] = []

    for current_date in backtest_dates:
        # ---- 算今日訊號 ----
        signals = {}; today_prices = {}
        for code, info in data.items():
            idx = date_to_idx[code].get(current_date)
            if idx is None or idx < 60:
                continue
            sc = series_cache[code]
            sig = score_at(sc["opens"], sc["highs"], sc["lows"],
                          sc["closes"], sc["volumes"], idx)
            if sig:
                signals[code] = sig
                today_prices[code] = sig["close"]

        # ---- 累加借錢成本（融資/融券）----
        for code, pos in positions.items():
            if pos["direction"] == "long" and pos["leverage"] > 1:
                borrowed = pos["shares"] * pos["entry_price"] * (1 - 1 / pos["leverage"])
                pos["borrow_cost"] = pos.get("borrow_cost", 0) + borrowed * cfg.margin_daily_rate
            elif pos["direction"] == "short":
                notional = pos["shares"] * pos["entry_price"]
                pos["borrow_cost"] = pos.get("borrow_cost", 0) + notional * cfg.short_borrow_daily_rate

        # ---- 處理出場（純技術判斷）----
        for code in list(positions.keys()):
            if code not in today_prices:
                continue
            pos = positions[code]
            curr = today_prices[code]
            sig = signals.get(code, {})
            hold_days = (current_date - pos["entry_date"]).days

            reason = None
            if pos["direction"] == "long":
                pnl_pct = (curr - pos["entry_price"]) / pos["entry_price"]
                ma_level = sig.get(cfg.long_exit_break_ma) or 0
                swing_low = sig.get(f"low_{cfg.long_exit_break_swing}") or 0
                catastrophic = pos["entry_price"] * (1 + cfg.catastrophic_stop)

                if curr < ma_level and ma_level > 0:
                    reason = (f"跌破{cfg.long_exit_break_ma.upper()} "
                              f"({ma_level:.2f}) ｜ {pnl_pct:+.1%}")
                elif curr < swing_low and swing_low > 0:
                    reason = (f"跌破前 {cfg.long_exit_break_swing} 日低 "
                              f"({swing_low:.2f}) ｜ {pnl_pct:+.1%}")
                elif curr <= catastrophic:
                    reason = f"極端停損保護 {cfg.catastrophic_stop:+.0%} ({pnl_pct:+.1%})"
                elif sig.get("score", 0) <= -cfg.long_exit_bearish_score:
                    reason = f"技術翻空 分數{sig.get('score')} ({pnl_pct:+.1%})"
                elif hold_days >= cfg.max_hold_days:
                    reason = f"持有滿{hold_days}日安全網 ({pnl_pct:+.1%})"
            else:  # short
                pnl_pct = (pos["entry_price"] - curr) / pos["entry_price"]
                price_rise = (curr - pos["entry_price"]) / pos["entry_price"]
                ma_level = sig.get(cfg.short_exit_break_ma) or 0
                swing_high = sig.get(f"high_{cfg.short_exit_break_swing}") or 0
                catastrophic = pos["entry_price"] * (1 + cfg.short_catastrophic_stop)

                if curr > ma_level and ma_level > 0:
                    reason = (f"站上{cfg.short_exit_break_ma.upper()} "
                              f"({ma_level:.2f}) ｜ {pnl_pct:+.1%}")
                elif curr > swing_high and swing_high > 0:
                    reason = (f"突破前 {cfg.short_exit_break_swing} 日高 "
                              f"({swing_high:.2f}) ｜ {pnl_pct:+.1%}")
                elif curr >= catastrophic:
                    reason = f"空單極端停損 +{cfg.short_catastrophic_stop:.0%} ({pnl_pct:+.1%})"
                elif sig.get("score", 0) >= cfg.short_exit_bullish_score:
                    reason = f"空單轉多 分數{sig.get('score')} ({pnl_pct:+.1%})"
                elif hold_days >= cfg.max_hold_days:
                    reason = f"持有滿{hold_days}日安全網 ({pnl_pct:+.1%})"

            if reason:
                pnl, cash_back = _close_position(pos, curr, cash, cfg)
                cash += cash_back
                trades.append({
                    "code": code, "name": pos["name"],
                    "direction": pos["direction"],
                    "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                    "exit_date": current_date.strftime("%Y-%m-%d"),
                    "entry_price": round(pos["entry_price"], 2),
                    "exit_price": round(curr, 2),
                    "shares": pos["shares"],
                    "hold_days": hold_days,
                    "leverage": pos["leverage"],
                    "pnl": round(pnl),
                    "pnl_pct": pnl_pct,
                    "borrow_cost": round(pos.get("borrow_cost", 0)),
                    "reason": reason,
                })
                del positions[code]

        # ---- 處理進場 ----
        # 多單
        long_count = sum(1 for p in positions.values() if p["direction"] == "long")
        long_slots = cfg.max_positions - long_count
        if long_slots > 0:
            cands = sorted(
                [(c, s) for c, s in signals.items()
                 if c not in positions and s["score"] >= cfg.long_entry_threshold],
                key=lambda x: -x[1]["score"],
            )
            for code, sig in cands[:long_slots]:
                price = sig["close"]
                allocation = (cash / max(long_slots, 1)) * 0.95
                if cfg.leverage > 1:
                    allocation *= cfg.leverage  # 槓桿放大可買金額
                shares = int(allocation / (price * (1 + BUY_FEE)))
                if shares <= 0: continue

                cost_full = shares * price * (1 + BUY_FEE)
                margin = cost_full / cfg.leverage  # 自備款
                if margin > cash: continue
                cash -= margin
                positions[code] = {
                    "code": code, "name": data[code]["name"],
                    "direction": "long",
                    "entry_price": price,
                    "shares": shares,
                    "entry_date": current_date,
                    "leverage": cfg.leverage,
                    "margin_locked": margin,
                    "borrow_cost": 0.0,
                }

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
                    allocation = cash * 0.15  # 每檔空單只用 15% 資金當保證金
                    shares = int(allocation / (price * 0.9))  # 90% 保證金率
                    if shares <= 0: continue
                    margin = shares * price * 0.9
                    if margin > cash: continue
                    cash -= margin
                    positions[code] = {
                        "code": code, "name": data[code]["name"],
                        "direction": "short",
                        "entry_price": price,
                        "shares": shares,
                        "entry_date": current_date,
                        "leverage": 1.0,
                        "margin_locked": margin,
                        "borrow_cost": 0.0,
                    }

        # ---- 結算當日權益 ----
        equity = cash
        for code, pos in positions.items():
            curr = today_prices.get(code, pos["entry_price"])
            if pos["direction"] == "long":
                # 釋出保證金 + 賣出價值（扣稅費） - 借款本金
                pnl, cash_back = _close_position(pos, curr, cash, cfg, simulate_only=True)
                equity += cash_back
            else:
                pnl, cash_back = _close_position(pos, curr, cash, cfg, simulate_only=True)
                equity += cash_back

        equity_history.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "equity": round(equity),
            "cash": round(cash),
            "long_positions": sum(1 for p in positions.values() if p["direction"] == "long"),
            "short_positions": sum(1 for p in positions.values() if p["direction"] == "short"),
        })

    # ---- 回測結束強制平倉 ----
    last_date = backtest_dates[-1]
    for code in list(positions.keys()):
        idx = date_to_idx[code].get(last_date)
        if idx is None:
            continue
        price = series_cache[code]["closes"][idx]
        pos = positions[code]
        pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"]
                  if pos["direction"] == "long"
                  else (pos["entry_price"] - price) / pos["entry_price"])
        pnl, cash_back = _close_position(pos, price, cash, cfg)
        cash += cash_back
        hold_days = (last_date - pos["entry_date"]).days
        trades.append({
            "code": code, "name": pos["name"],
            "direction": pos["direction"],
            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": last_date.strftime("%Y-%m-%d"),
            "entry_price": round(pos["entry_price"], 2),
            "exit_price": round(price, 2),
            "shares": pos["shares"],
            "hold_days": hold_days,
            "leverage": pos["leverage"],
            "pnl": round(pnl),
            "pnl_pct": pnl_pct,
            "borrow_cost": round(pos.get("borrow_cost", 0)),
            "reason": "回測結束強制平倉",
        })
        del positions[code]

    return {
        "config": asdict(cfg),
        "final_cash": cash,
        "trades": trades,
        "equity_history": equity_history,
        "backtest_start": backtest_dates[0].strftime("%Y-%m-%d"),
        "backtest_end": backtest_dates[-1].strftime("%Y-%m-%d"),
    }


# ============================================================
# 嚴謹版模擬：今日收盤決定 → 明日開盤執行
# 修正「同日收盤決定與成交」的偷看偏差
# ============================================================
def simulate_strict(data: dict, cfg: StrategyConfig) -> dict:
    all_dates = sorted(set().union(*[set(d["hist"].index) for d in data.values()]))
    if len(all_dates) < 80:
        raise RuntimeError(f"歷史不足 ({len(all_dates)})")

    backtest_dates = all_dates[-cfg.backtest_days:]

    series_cache = {}
    date_to_idx = {}
    for code, info in data.items():
        hist = info["hist"]
        series_cache[code] = {
            "opens": hist["Open"].tolist(),
            "highs": hist["High"].tolist(),
            "lows": hist["Low"].tolist(),
            "closes": hist["Close"].tolist(),
            "volumes": hist["Volume"].tolist(),
        }
        date_to_idx[code] = {d: i for i, d in enumerate(hist.index)}

    cash = float(cfg.initial_capital)
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_history: list[dict] = []

    # 待執行：{date: [{type, code, ...}]}
    pending = {}

    for i, current_date in enumerate(backtest_dates):
        today_opens = {}; today_closes = {}
        for code in data:
            idx = date_to_idx[code].get(current_date)
            if idx is None: continue
            sc = series_cache[code]
            today_opens[code] = sc["opens"][idx]
            today_closes[code] = sc["closes"][idx]

        # ---- 借款成本 ----
        for code, pos in positions.items():
            if pos["direction"] == "long" and pos["leverage"] > 1:
                borrowed = pos["shares"] * pos["entry_price"] * (1 - 1 / pos["leverage"])
                pos["borrow_cost"] = pos.get("borrow_cost", 0) + borrowed * cfg.margin_daily_rate
            elif pos["direction"] == "short":
                notional = pos["shares"] * pos["entry_price"]
                pos["borrow_cost"] = pos.get("borrow_cost", 0) + notional * cfg.short_borrow_daily_rate

        # ============ STEP 1: 執行昨天決定、今天開盤要做的動作 ============
        actions_today = pending.pop(current_date, [])

        # 先賣（釋出現金）
        for act in actions_today:
            if act["type"] != "SELL": continue
            code = act["code"]
            if code not in today_opens: continue  # 標的今日無交易，跳過
            if code not in positions: continue
            pos = positions[code]
            exec_price = today_opens[code]
            pnl, cash_back = _close_position(pos, exec_price, cash, cfg)
            cash += cash_back
            pnl_pct = ((exec_price - pos["entry_price"]) / pos["entry_price"]
                       if pos["direction"] == "long"
                       else (pos["entry_price"] - exec_price) / pos["entry_price"])
            hold_days = (current_date - pos["entry_date"]).days
            trades.append({
                "code": code, "name": pos["name"],
                "direction": pos["direction"],
                "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
                "exit_date": current_date.strftime("%Y-%m-%d"),
                "entry_price": round(pos["entry_price"], 2),
                "exit_price": round(exec_price, 2),
                "shares": pos["shares"],
                "hold_days": hold_days,
                "leverage": pos["leverage"],
                "pnl": round(pnl),
                "pnl_pct": pnl_pct,
                "borrow_cost": round(pos.get("borrow_cost", 0)),
                "reason": act.get("reason", "—") + "（明日開盤執行）",
            })
            del positions[code]

        # 再買
        for act in actions_today:
            if act["type"] != "BUY": continue
            code = act["code"]
            if code not in today_opens: continue
            if code in positions: continue
            exec_price = today_opens[code]
            allocation = act["allocation"]
            if cfg.leverage > 1:
                allocation *= cfg.leverage
            shares = int(allocation / (exec_price * (1 + BUY_FEE)))
            if shares <= 0: continue
            cost_full = shares * exec_price * (1 + BUY_FEE)
            margin = cost_full / cfg.leverage
            if margin > cash: continue
            cash -= margin
            positions[code] = {
                "code": code, "name": act["name"],
                "direction": act["direction"],
                "entry_price": exec_price,
                "shares": shares,
                "entry_date": current_date,
                "leverage": cfg.leverage if act["direction"] == "long" else 1.0,
                "margin_locked": margin,
                "borrow_cost": 0.0,
            }

        # ============ STEP 2: 用今日收盤計算訊號 ============
        signals = {}
        for code in data:
            idx = date_to_idx[code].get(current_date)
            if idx is None or idx < 60: continue
            sc = series_cache[code]
            sig = score_at(sc["opens"], sc["highs"], sc["lows"],
                          sc["closes"], sc["volumes"], idx)
            if sig: signals[code] = sig

        # ============ STEP 3: 排定明日要做的動作 ============
        if i + 1 < len(backtest_dates):
            tomorrow = backtest_dates[i + 1]
            tomorrow_actions = []

            # 出場條件檢查（用今日收盤判斷）
            for code, pos in positions.items():
                if code not in signals: continue
                sig = signals[code]
                close = today_closes.get(code)
                if close is None: continue
                hold_days = (current_date - pos["entry_date"]).days
                reason = None

                if pos["direction"] == "long":
                    ma_level = sig.get(cfg.long_exit_break_ma) or 0
                    swing_low = sig.get(f"low_{cfg.long_exit_break_swing}") or 0
                    catastrophic = pos["entry_price"] * (1 + cfg.catastrophic_stop)
                    if close < ma_level and ma_level > 0:
                        reason = f"跌破{cfg.long_exit_break_ma.upper()} ({ma_level:.2f})"
                    elif close < swing_low and swing_low > 0:
                        reason = f"跌破前{cfg.long_exit_break_swing}日低 ({swing_low:.2f})"
                    elif close <= catastrophic:
                        reason = f"極端停損 {cfg.catastrophic_stop:+.0%}"
                    elif sig.get("score", 0) <= -cfg.long_exit_bearish_score:
                        reason = f"技術翻空 分數{sig.get('score')}"
                    elif hold_days >= cfg.max_hold_days:
                        reason = f"持有滿{hold_days}日安全網"
                else:  # short
                    ma_level = sig.get(cfg.short_exit_break_ma) or 0
                    swing_high = sig.get(f"high_{cfg.short_exit_break_swing}") or 0
                    catastrophic = pos["entry_price"] * (1 + cfg.short_catastrophic_stop)
                    if close > ma_level and ma_level > 0:
                        reason = f"站上{cfg.short_exit_break_ma.upper()} ({ma_level:.2f})"
                    elif close > swing_high and swing_high > 0:
                        reason = f"突破前{cfg.short_exit_break_swing}日高 ({swing_high:.2f})"
                    elif close >= catastrophic:
                        reason = f"空單極端停損 +{cfg.short_catastrophic_stop:.0%}"
                    elif sig.get("score", 0) >= cfg.short_exit_bullish_score:
                        reason = f"空單轉多"

                if reason:
                    tomorrow_actions.append({
                        "type": "SELL", "code": code, "reason": reason,
                    })

            # 入場條件 — 多單
            existing_long = sum(1 for p in positions.values() if p["direction"] == "long")
            pending_long = sum(1 for a in tomorrow_actions if a["type"] == "BUY"
                              and a.get("direction") == "long")
            # 還有，排定出場的會釋放 slot
            slots = cfg.max_positions - existing_long - pending_long + \
                    sum(1 for a in tomorrow_actions if a["type"] == "SELL")

            if slots > 0:
                cands = sorted(
                    [(c, s) for c, s in signals.items()
                     if c not in positions
                     and not any(a["type"] == "BUY" and a["code"] == c for a in tomorrow_actions)
                     and s["score"] >= cfg.long_entry_threshold],
                    key=lambda x: -x[1]["score"],
                )
                # 預估明日可用現金（保守）
                est_cash = cash * 0.95
                allocation_per_slot = est_cash / max(slots, 1)

                for code, sig in cands[:slots]:
                    tomorrow_actions.append({
                        "type": "BUY", "code": code,
                        "name": data[code]["name"],
                        "direction": "long",
                        "allocation": allocation_per_slot,
                    })

            # 空單
            if cfg.enable_short:
                existing_short = sum(1 for p in positions.values() if p["direction"] == "short")
                pending_short = sum(1 for a in tomorrow_actions
                                   if a["type"] == "BUY" and a.get("direction") == "short")
                short_slots = cfg.max_short_positions - existing_short - pending_short
                if short_slots > 0:
                    cands = sorted(
                        [(c, s) for c, s in signals.items()
                         if c not in positions
                         and not any(a["type"] == "BUY" and a["code"] == c for a in tomorrow_actions)
                         and s["score"] <= cfg.short_entry_threshold],
                        key=lambda x: x[1]["score"],
                    )
                    for code, sig in cands[:short_slots]:
                        tomorrow_actions.append({
                            "type": "BUY", "code": code,
                            "name": data[code]["name"],
                            "direction": "short",
                            "allocation": cash * 0.15,
                        })

            if tomorrow_actions:
                pending[tomorrow] = tomorrow_actions

        # ============ STEP 4: 用今日收盤結算權益 ============
        equity = cash
        for code, pos in positions.items():
            curr = today_closes.get(code, pos["entry_price"])
            _, cash_back = _close_position(pos, curr, cash, cfg, simulate_only=True)
            equity += cash_back
        equity_history.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "equity": round(equity), "cash": round(cash),
            "long_positions": sum(1 for p in positions.values() if p["direction"] == "long"),
            "short_positions": sum(1 for p in positions.values() if p["direction"] == "short"),
        })

    # 強制平倉（用最後一天收盤）
    last_date = backtest_dates[-1]
    for code in list(positions.keys()):
        idx = date_to_idx[code].get(last_date)
        if idx is None: continue
        price = series_cache[code]["closes"][idx]
        pos = positions[code]
        pnl, cash_back = _close_position(pos, price, cash, cfg)
        cash += cash_back
        pnl_pct = ((price - pos["entry_price"]) / pos["entry_price"]
                   if pos["direction"] == "long"
                   else (pos["entry_price"] - price) / pos["entry_price"])
        trades.append({
            "code": code, "name": pos["name"],
            "direction": pos["direction"],
            "entry_date": pos["entry_date"].strftime("%Y-%m-%d"),
            "exit_date": last_date.strftime("%Y-%m-%d"),
            "entry_price": round(pos["entry_price"], 2),
            "exit_price": round(price, 2),
            "shares": pos["shares"],
            "hold_days": (last_date - pos["entry_date"]).days,
            "leverage": pos["leverage"],
            "pnl": round(pnl),
            "pnl_pct": pnl_pct,
            "borrow_cost": round(pos.get("borrow_cost", 0)),
            "reason": "回測結束強制平倉",
        })
        del positions[code]

    return {
        "config": asdict(cfg),
        "final_cash": cash,
        "trades": trades,
        "equity_history": equity_history,
        "backtest_start": backtest_dates[0].strftime("%Y-%m-%d"),
        "backtest_end": backtest_dates[-1].strftime("%Y-%m-%d"),
        "execution": "next_open_strict",
    }


def _close_position(pos: dict, curr: float, cash: float,
                    cfg: StrategyConfig, simulate_only: bool = False) -> tuple[float, float]:
    """關閉 position，回傳 (pnl, cash_back_to_balance)。

    pnl: 此次交易的淨損益（已扣費用、借款）
    cash_back: 應該加回給 cash 的金額（保證金 + pnl）
    """
    shares = pos["shares"]
    entry = pos["entry_price"]
    borrow = pos.get("borrow_cost", 0)

    if pos["direction"] == "long":
        proceeds = shares * curr * (1 - SELL_FEE)
        cost = shares * entry * (1 + BUY_FEE)
        # 如有槓桿，需還借款本金
        borrowed_principal = cost * (1 - 1 / pos["leverage"])
        pnl = proceeds - cost - borrow
        cash_back = pos["margin_locked"] + pnl  # 自備款 + 損益
        return pnl, cash_back
    else:
        # short: 漲虧、跌賺
        proceeds_initial = shares * entry * (1 - SELL_FEE)  # 開倉「賣」拿到
        cost_to_close = shares * curr * (1 + BUY_FEE)        # 回補要花
        pnl = proceeds_initial - cost_to_close - borrow
        cash_back = pos["margin_locked"] + pnl
        return pnl, cash_back


# ============================================================
# 分析績效
# ============================================================
def calc_metrics(result: dict) -> dict:
    final = result["final_cash"]
    initial = result["config"]["initial_capital"]
    pnl = final - initial
    pnl_pct = pnl / initial

    trades = result["trades"]
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]

    long_trades = [t for t in trades if t["direction"] == "long"]
    short_trades = [t for t in trades if t["direction"] == "short"]

    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    total_win = sum(t["pnl"] for t in wins)
    total_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = total_win / total_loss if total_loss > 0 else float("inf")

    peak = initial; max_dd = 0
    for h in result["equity_history"]:
        peak = max(peak, h["equity"])
        dd = (h["equity"] - peak) / peak
        max_dd = min(max_dd, dd)

    return {
        "pnl": pnl, "pnl_pct": pnl_pct,
        "n_trades": len(trades),
        "n_long": len(long_trades), "n_short": len(short_trades),
        "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": win_rate,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": profit_factor,
        "max_dd": max_dd,
        "score": pnl_pct / (abs(max_dd) + 0.01),  # 報酬/回撤 比
    }


def print_report(result: dict, top_n: int = 5):
    m = calc_metrics(result)
    cfg = result["config"]

    print("\n" + "=" * 62)
    print(f"📊 回測：{cfg['name']}")
    print("=" * 62)
    print(f"期間：{result['backtest_start']} ~ {result['backtest_end']}")
    print(f"本金 NT$ {cfg['initial_capital']:,}")
    print(f"最終 NT$ {result['final_cash']:,.0f}  "
          f"損益 NT$ {m['pnl']:+,.0f} ({m['pnl_pct']:+.2%})")
    print("-" * 62)
    print(f"交易：{m['n_trades']} (多 {m['n_long']} / 空 {m['n_short']})")
    print(f"勝率：{m['win_rate']:.1%}  ({m['n_wins']} 勝 / {m['n_losses']} 敗)")
    print(f"平均勝幅 {m['avg_win']:+.2%} ｜ 平均虧損 {m['avg_loss']:+.2%}")
    print(f"獲利因子 {m['profit_factor']:.2f} ｜ 最大回撤 {m['max_dd']:+.2%}")
    print(f"報酬/回撤比 {m['score']:.2f}")
    print("=" * 62)

    trades = result["trades"]
    if trades:
        print(f"\n🏆 獲利前 {top_n}：")
        for t in sorted(trades, key=lambda x: -x["pnl"])[:top_n]:
            d = "📈" if t["direction"] == "long" else "📉"
            print(f"  {d} {t['code']} {t['name']:<10} "
                  f"{t['entry_date']} → {t['exit_date']} "
                  f"({t['hold_days']}日) "
                  f"{t['entry_price']:>7.1f} → {t['exit_price']:>7.1f}  "
                  f"${t['pnl']:>+7,} ({t['pnl_pct']:+.2%}) [{t['reason']}]")

        print(f"\n💔 虧損前 {top_n}：")
        for t in sorted(trades, key=lambda x: x["pnl"])[:top_n]:
            d = "📈" if t["direction"] == "long" else "📉"
            print(f"  {d} {t['code']} {t['name']:<10} "
                  f"{t['entry_date']} → {t['exit_date']} "
                  f"({t['hold_days']}日) "
                  f"{t['entry_price']:>7.1f} → {t['exit_price']:>7.1f}  "
                  f"${t['pnl']:>+7,} ({t['pnl_pct']:+.2%}) [{t['reason']}]")


# ============================================================
# 入口
# ============================================================
def run(cfg: StrategyConfig = None,
        period: str = "9mo", verbose: bool = True) -> dict:
    if cfg is None:
        cfg = StrategyConfig()
    universe = build_universe()
    data = download_history(universe, period=period, verbose=verbose)
    if len(data) < 5:
        raise RuntimeError("可用標的過少")
    result = simulate(data, cfg)
    return result


def main():
    print("=" * 62)
    print("🤖 虛擬交易回測 · 最佳策略")
    print("=" * 62)

    # 純做多 + 技術出場
    cfg = StrategyConfig(
        name="technical-exit",
        initial_capital=200_000,
        backtest_days=120,
        max_positions=5,
        max_hold_days=120,
        long_entry_threshold=9,
        long_exit_break_ma="ma20",       # 跌破月線出場
        long_exit_break_swing=5,         # 跌破 5 日低出場
        catastrophic_stop=-0.12,         # 極端虧損保護
        enable_short=False,
    )
    result = run(cfg)
    print_report(result)

    with open("backtest_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print("\n💾 詳細結果存到 backtest_result.json")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
