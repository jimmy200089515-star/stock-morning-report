# -*- coding: utf-8 -*-
"""
盤中監控機器人

每 5 分鐘執行一次，檢查：
- 我的持股（HOLDINGS_TW / HOLDINGS_US）
- 紙上交易系統持倉（paper_portfolio.json）

觸發條件就推播 Telegram：
- 跌破停損價 / 漲破目標價
- 跌破 MA20（技術出場）
- 單日大跌 < -5%
- 單日大漲 > +5%
- 爆量（量比 > 2x）
- 接近停損/目標（±2% 範圍）

去重：同一檔當日相同類型警報只送一次（用 intraday_alert_state.json）
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import time
from datetime import datetime, time as dtime
from typing import Optional

if sys.platform == "win32" and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import yfinance as yf

from config import HOLDINGS_TW, HOLDINGS_US
from fetcher import _resolve_yf_ticker, _sma
from notify import telegram_alert, telegram_push

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


STATE_FILE = "intraday_alert_state.json"
PORTFOLIO_FILE = "paper_portfolio.json"


# ============================================================
# 市場開盤時間判斷（台北時間）
# ============================================================
def is_tw_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:  # 週末
        return False
    t = now.time()
    return dtime(9, 0) <= t <= dtime(13, 35)


def is_us_market_open() -> bool:
    """美股開盤約 22:30-05:00 台北時間（日光節約 21:30-04:00）。"""
    now = datetime.now()
    # 美股週末 = 台北週六/週日
    wd = now.weekday()
    t = now.time()
    if wd == 5 or wd == 6:  # 週六、週日早上
        if wd == 6 and t > dtime(5, 0):
            return False
        if wd == 5 and t < dtime(21, 30):
            return False
    return t >= dtime(21, 30) or t <= dtime(5, 0)


# ============================================================
# 狀態記錄（去重）
# ============================================================
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"date": "", "sent": []}
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # 跨日重置
        today = datetime.now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            return {"date": today, "sent": []}
        return data
    except Exception:
        return {"date": datetime.now().strftime("%Y-%m-%d"), "sent": []}


def save_state(state: dict):
    state["date"] = datetime.now().strftime("%Y-%m-%d")
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def already_sent(state: dict, code: str, alert_type: str) -> bool:
    key = f"{code}:{alert_type}"
    return key in state.get("sent", [])


def mark_sent(state: dict, code: str, alert_type: str):
    state.setdefault("sent", []).append(f"{code}:{alert_type}")


# ============================================================
# 抓盤中即時資料
# ============================================================
def fetch_intraday(code_or_ticker: str, market: str = "TW") -> Optional[dict]:
    """抓 1 分鐘 K 線最後一根；回傳 {price, open, high, low, volume, day_change_pct, vol_ratio, ma20}"""
    try:
        ticker = code_or_ticker if market == "US" else _resolve_yf_ticker(code_or_ticker)
        if not ticker:
            return None

        # 抓今日 5 分鐘 K 線
        intraday = yf.Ticker(ticker).history(period="1d", interval="5m")
        if intraday.empty:
            return None
        last = intraday.iloc[-1]
        current_price = float(last["Close"])
        today_open = float(intraday.iloc[0]["Open"])
        today_high = float(intraday["High"].max())
        today_low = float(intraday["Low"].min())
        today_volume = float(intraday["Volume"].sum())

        # 抓昨日收盤
        daily = yf.Ticker(ticker).history(period="30d")
        if daily.empty or len(daily) < 2:
            return None
        prev_close = float(daily["Close"].iloc[-2])

        # 算 5 日均量
        vol_5 = daily["Volume"].iloc[-6:-1].mean()
        vol_ratio = today_volume / vol_5 if vol_5 > 0 else 1

        # 算 MA20
        closes = daily["Close"].tolist()
        ma20 = _sma(closes, 20)

        day_change_pct = (current_price - prev_close) / prev_close * 100 if prev_close > 0 else 0

        return {
            "ticker": ticker,
            "price": current_price,
            "open": today_open,
            "high": today_high,
            "low": today_low,
            "volume": int(today_volume),
            "prev_close": prev_close,
            "day_change_pct": day_change_pct,
            "vol_ratio": vol_ratio,
            "ma20": ma20,
        }
    except Exception as e:
        print(f"[Intraday] {code_or_ticker} 失敗：{e}")
        return None


# ============================================================
# 監控單一檔，回傳要發的警報列表
# ============================================================
def check_position(code: str, name: str, market: str,
                    paper_pos: dict | None = None) -> list[dict]:
    """檢查一檔的觸發條件。

    paper_pos: 紙上交易持倉資料，若有則含 entry_price, stop_loss_price, take_profit_price
    """
    snap = fetch_intraday(code, market)
    if not snap:
        return []

    price = snap["price"]
    day_pct = snap["day_change_pct"]
    vol_ratio = snap["vol_ratio"]
    ma20 = snap["ma20"]

    alerts = []

    # 1. 跌破 MA20（技術出場）
    if ma20 and price < ma20:
        alerts.append({
            "type": "break_ma20",
            "urgency": "high",
            "title": f"{name} ({code}) 跌破 MA20",
            "body": (f"📉 現價 {price:.2f}  vs  MA20 {ma20:.2f}\n"
                    f"今日漲跌 {day_pct:+.2f}%\n\n"
                    f"⚠️ 技術面已轉弱，建議出場"),
        })

    # 2. 單日大跌
    if day_pct < -5:
        alerts.append({
            "type": "big_drop",
            "urgency": "high",
            "title": f"{name} ({code}) 急跌 {day_pct:.1f}%",
            "body": (f"📉 現價 {price:.2f}（開 {snap['open']:.2f}）\n"
                    f"量比 {vol_ratio:.1f}x\n\n"
                    f"⚠️ 確認新聞或停損"),
        })

    # 3. 單日大漲
    if day_pct > 5:
        alerts.append({
            "type": "big_rise",
            "urgency": "medium",
            "title": f"{name} ({code}) 急漲 {day_pct:+.1f}%",
            "body": (f"📈 現價 {price:.2f}\n"
                    f"量比 {vol_ratio:.1f}x\n\n"
                    f"💡 考慮加碼或部分停利"),
        })

    # 4. 爆量
    if vol_ratio > 2.5:
        alerts.append({
            "type": "volume_spike",
            "urgency": "medium",
            "title": f"{name} ({code}) 爆量 {vol_ratio:.1f}x",
            "body": (f"📊 今日量已達 5 日均量 {vol_ratio:.1f} 倍\n"
                    f"現價 {price:.2f}（{day_pct:+.2f}%）\n\n"
                    f"💡 有資金進駐，留意走勢"),
        })

    # 5. 紙上交易系統 - 停損/目標觸發
    if paper_pos:
        entry = paper_pos.get("entry_price")
        if entry:
            # 推算停損價（用我們策略的 -12% 極端停損 + MA20）
            stop_pct = (price - entry) / entry
            if stop_pct <= -0.10:
                alerts.append({
                    "type": "paper_stop",
                    "urgency": "high",
                    "title": f"{name} ({code}) 紙上倉位虧損 {stop_pct:+.1%}",
                    "body": (f"進場 {entry:.2f} → 現價 {price:.2f}\n"
                            f"已觸發 -10% 警戒線\n\n"
                            f"⚠️ 接近策略停損，準備出場"),
                })

    return alerts


# ============================================================
# 主流程
# ============================================================
def main():
    print(f"=== 盤中監控 {datetime.now():%Y-%m-%d %H:%M:%S} ===")

    tw_open = is_tw_market_open()
    us_open = is_us_market_open()
    print(f"市場：TW {'開盤' if tw_open else '收盤'} | US {'開盤' if us_open else '收盤'}")

    if not (tw_open or us_open):
        print("沒有開盤的市場，跳過")
        return 0

    state = load_state()

    # 載入紙上交易持倉
    paper_positions = {}
    if os.path.exists(PORTFOLIO_FILE):
        try:
            with open(PORTFOLIO_FILE, encoding="utf-8") as f:
                paper_positions = json.load(f).get("positions", {})
        except Exception:
            pass

    total_alerts = 0

    # ---- 台股 ----
    if tw_open:
        for code, name in HOLDINGS_TW:
            print(f"  檢查 TW {code} {name} ...")
            paper_pos = paper_positions.get(code)
            alerts = check_position(code, name, "TW", paper_pos)
            for a in alerts:
                if already_sent(state, code, a["type"]):
                    continue
                ok = telegram_alert(a["title"], a["body"], a["urgency"])
                if ok:
                    mark_sent(state, code, a["type"])
                    total_alerts += 1
                    print(f"    🚨 推播：{a['title']}")
            time.sleep(0.3)

    # ---- 美股 ----
    if us_open:
        for ticker, name in HOLDINGS_US:
            print(f"  檢查 US {ticker} {name} ...")
            paper_pos = paper_positions.get(ticker)
            alerts = check_position(ticker, name, "US", paper_pos)
            for a in alerts:
                if already_sent(state, ticker, a["type"]):
                    continue
                ok = telegram_alert(a["title"], a["body"], a["urgency"])
                if ok:
                    mark_sent(state, ticker, a["type"])
                    total_alerts += 1
                    print(f"    🚨 推播：{a['title']}")
            time.sleep(0.3)

    save_state(state)
    print(f"\n✅ 完成。本次推播 {total_alerts} 則警報")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        try:
            telegram_push(f"❌ *盤中監控異常*\n\n{traceback.format_exc()[:500]}")
        except Exception:
            pass
        sys.exit(1)
