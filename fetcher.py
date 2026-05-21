# -*- coding: utf-8 -*-
"""
資料抓取與技術分析模組

提供：
- 大盤資料：fetch_taiex / fetch_us_indices
- 持股每日分析：analyze_holdings
- 全市場掃描 + 推薦：scan_and_recommend_tw / scan_and_recommend_us
- 族群輪動：analyze_tw_sectors / analyze_us_sectors（僅出強弱榜，不再做代表股深度卡）
- 深度 K 線：deep_kline_analysis
- 個股工具：fetch_stock_price / fetch_technical / fetch_institutional / fetch_margin / fetch_fundamentals
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import requests
import yfinance as yf

from config import (
    DISCOVERY,
    HOLDINGS_TW,
    HOLDINGS_US,
    RECOMMENDATION,
    TW_SECTORS,
    US_INDICES,
    US_SECTOR_ETFS,
    US_UNIVERSE,
)

# 進階分析（選用）
try:
    from pattern_detector import detect_patterns
except ImportError:
    detect_patterns = None

try:
    from ta_external import get_tv_rating
except ImportError:
    get_tv_rating = None

try:
    from chart_vision import chart_vision_for
except ImportError:
    chart_vision_for = None


# 壓低 yfinance 的雜訊（possibly delisted 等）
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


TWSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}


# ===========================================================================
# Yahoo Finance ticker 解析（自動切換 .TW / .TWO）
# ===========================================================================
_TICKER_CACHE: dict[str, str | None] = {}  # code -> 完整 ticker / None


def _resolve_yf_ticker(code_or_ticker: str) -> str | None:
    """解析台股代碼到能用的 yfinance ticker：先試 .TW，沒資料試 .TWO。

    美股 ticker 直接回傳。失敗回傳 None（並 cache）。
    """
    if not code_or_ticker:
        return None
    # 已經是完整 ticker（含 . 或非數字）→ 直接用
    if not code_or_ticker.replace(".", "").replace("-", "").isdigit() or "." in code_or_ticker:
        return code_or_ticker

    if code_or_ticker in _TICKER_CACHE:
        return _TICKER_CACHE[code_or_ticker]

    for suffix in (".TW", ".TWO"):
        candidate = f"{code_or_ticker}{suffix}"
        try:
            hist = yf.Ticker(candidate).history(period="5d")
            if not hist.empty and len(hist) >= 1:
                _TICKER_CACHE[code_or_ticker] = candidate
                return candidate
        except Exception:
            pass

    _TICKER_CACHE[code_or_ticker] = None
    return None


# ===========================================================================
# 工具函式
# ===========================================================================
def _twse_get(url: str, params: dict, max_back_days: int = 7):
    """呼叫 TWSE API，遇到非交易日空資料時往前回推。"""
    date_str = params.get("date")
    if not date_str:
        return None
    current = datetime.strptime(date_str, "%Y%m%d")
    for _ in range(max_back_days + 1):
        params["date"] = current.strftime("%Y%m%d")
        try:
            resp = requests.get(url, params=params, headers=TWSE_HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if data and (data.get("data") or data.get("tables")):
                return data
        except Exception:
            pass
        current -= timedelta(days=1)
    return None


def _pct(curr: float, prev: float) -> float:
    if prev in (0, None):
        return 0.0
    return (curr - prev) / prev * 100


def _sma(series, n: int):
    if len(series) < n:
        return None
    return sum(series[-n:]) / n


def _rsi(closes, period: int = 14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff >= 0:
            gains.append(diff); losses.append(0)
        else:
            gains.append(0); losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _kd(highs, lows, closes, k_period: int = 9):
    if len(closes) < k_period + 3:
        return None, None
    rsv_list = []
    for i in range(k_period - 1, len(closes)):
        wh = max(highs[i - k_period + 1:i + 1])
        wl = min(lows[i - k_period + 1:i + 1])
        rsv = 50.0 if wh == wl else (closes[i] - wl) / (wh - wl) * 100
        rsv_list.append(rsv)
    k_prev = 50.0; k_values = []
    for rsv in rsv_list:
        k = (1 / 3) * rsv + (2 / 3) * k_prev
        k_values.append(k); k_prev = k
    d_prev = 50.0; d_values = []
    for k in k_values:
        d = (1 / 3) * k + (2 / 3) * d_prev
        d_values.append(d); d_prev = d
    return k_values[-1], d_values[-1]


def _macd(closes):
    if len(closes) < 35:
        return None, None, None

    def ema_series(series, n):
        k = 2 / (n + 1)
        out = [series[0]]
        for v in series[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    ema12 = ema_series(closes, 12)
    ema26 = ema_series(closes, 26)
    dif_series = [a - b for a, b in zip(ema12, ema26)]
    # EMA9 of dif series
    k = 2 / 10
    macd_curr = dif_series[0]
    for v in dif_series[1:]:
        macd_curr = v * k + macd_curr * (1 - k)
    osc = dif_series[-1] - macd_curr
    return dif_series[-1], macd_curr, osc


# ===========================================================================
# 大盤指數
# ===========================================================================
def fetch_us_indices() -> dict:
    result = {}
    for name, ticker in US_INDICES.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if hist.empty or len(hist) < 2:
                result[name] = None; continue
            last = float(hist["Close"].iloc[-1])
            prev = float(hist["Close"].iloc[-2])
            result[name] = {
                "close": round(last, 2),
                "change": round(last - prev, 2),
                "change_pct": round(_pct(last, prev), 2),
            }
        except Exception as e:
            print(f"[fetch_us_indices] {name} 失敗：{e}")
            result[name] = None
    return result


def fetch_taiex() -> dict | None:
    today = datetime.now().strftime("%Y%m%d")
    url = "https://www.twse.com.tw/exchangeReport/FMTQIK"
    data = _twse_get(url, {"response": "json", "date": today})
    if not data or not data.get("data"):
        return None
    try:
        last = data["data"][-1]
        close = float(str(last[4]).replace(",", ""))
        change = float(str(last[5]).replace(",", ""))
        prev = close - change
        volume = float(str(last[1]).replace(",", "")) / 1000
        return {
            "date": last[0],
            "close": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(_pct(close, prev), 2),
            "volume": round(volume, 0),
        }
    except Exception as e:
        print(f"[fetch_taiex] 解析失敗：{e}")
        return None


# ===========================================================================
# 個股簡項
# ===========================================================================
def fetch_stock_price(code_or_ticker: str) -> dict | None:
    try:
        ticker = _resolve_yf_ticker(code_or_ticker)
        if not ticker:
            return None
        hist = yf.Ticker(ticker).history(period="5d")
        if hist.empty or len(hist) < 2:
            return None
        last = float(hist["Close"].iloc[-1])
        prev = float(hist["Close"].iloc[-2])
        return {
            "close": round(last, 2),
            "change": round(last - prev, 2),
            "change_pct": round(_pct(last, prev), 2),
            "volume": int(hist["Volume"].iloc[-1]),
        }
    except Exception as e:
        print(f"[fetch_stock_price] {code_or_ticker} 失敗：{e}")
        return None


def fetch_institutional(code: str) -> dict | None:
    today = datetime.now().strftime("%Y%m%d")
    url = "https://www.twse.com.tw/fund/T86"
    data = _twse_get(url, {"response": "json", "date": today, "selectType": "ALL"})
    if not data or not data.get("data"):
        return None
    try:
        for row in data["data"]:
            if str(row[0]).strip() == code:
                def to_int(v):
                    try: return int(str(v).replace(",", "").strip() or 0)
                    except: return 0
                return {
                    "foreign": to_int(row[4]) // 1000,
                    "trust": to_int(row[10]) // 1000,
                    "dealer": to_int(row[14]) // 1000,
                    "total": to_int(row[18]) // 1000,
                }
        return None
    except Exception as e:
        print(f"[fetch_institutional] {code} 解析失敗：{e}")
        return None


def fetch_margin(code: str) -> dict | None:
    today = datetime.now().strftime("%Y%m%d")
    url = "https://www.twse.com.tw/exchangeReport/MI_MARGN"
    data = _twse_get(url, {"response": "json", "date": today, "selectType": "ALL"})
    if not data:
        return None
    try:
        rows = []
        if data.get("tables"):
            for tbl in data["tables"]:
                if tbl.get("data"):
                    rows = tbl["data"]; break
        else:
            rows = data.get("data", [])
        for row in rows:
            if str(row[0]).strip() == code:
                def to_int(v):
                    try: return int(str(v).replace(",", "").strip() or 0)
                    except: return 0
                return {
                    "margin_balance": to_int(row[3]),
                    "margin_change": to_int(row[4]),
                    "short_balance": to_int(row[8]),
                    "short_change": to_int(row[9]),
                }
        return None
    except Exception as e:
        print(f"[fetch_margin] {code} 解析失敗：{e}")
        return None


def fetch_fundamentals(code_or_ticker: str) -> dict | None:
    try:
        ticker = _resolve_yf_ticker(code_or_ticker)
        if not ticker:
            return None
        info = yf.Ticker(ticker).info
        if not info:
            return None
        return {
            "pe": info.get("trailingPE"),
            "forward_pe": info.get("forwardPE"),
            "eps_growth": info.get("earningsGrowth"),
            "revenue_growth": info.get("revenueGrowth"),
            "market_cap": info.get("marketCap"),
            "sector_en": info.get("sector"),
            "industry_en": info.get("industry"),
        }
    except Exception as e:
        print(f"[fetch_fundamentals] {code_or_ticker} 失敗：{e}")
        return None


# ===========================================================================
# 深度 K 線分析（完整版）
# ===========================================================================
def deep_kline_analysis(code_or_ticker: str, market: str = "TW") -> dict | None:
    try:
        ticker = _resolve_yf_ticker(code_or_ticker)
        if not ticker:
            return None
        hist = yf.Ticker(ticker).history(period="1y")
        if hist.empty or len(hist) < 60:
            return None

        opens = [float(x) for x in hist["Open"].tolist()]
        highs = [float(x) for x in hist["High"].tolist()]
        lows = [float(x) for x in hist["Low"].tolist()]
        closes = [float(x) for x in hist["Close"].tolist()]
        volumes = [float(x) for x in hist["Volume"].tolist()]

        close = closes[-1]; prev = closes[-2]
        change = close - prev; change_pct = _pct(close, prev)

        ma5 = _sma(closes, 5); ma10 = _sma(closes, 10); ma20 = _sma(closes, 20)
        ma60 = _sma(closes, 60)
        ma120 = _sma(closes, 120) if len(closes) >= 120 else None
        ma240 = _sma(closes, 240) if len(closes) >= 240 else None
        prev_ma5 = _sma(closes[:-1], 5); prev_ma20 = _sma(closes[:-1], 20)

        rsi = _rsi(closes, 14)
        k_val, d_val = _kd(highs, lows, closes)
        dif, macd_line, osc = _macd(closes)

        high_20 = max(highs[-21:-1]) if len(highs) >= 21 else max(highs[:-1])
        low_20 = min(lows[-21:-1]) if len(lows) >= 21 else min(lows[:-1])
        high_60 = max(highs[-61:-1]) if len(highs) >= 61 else high_20
        low_60 = min(lows[-61:-1]) if len(lows) >= 61 else low_20

        # 量價
        vol_relation = "—"; vol_ratio = 1.0
        if len(volumes) >= 6:
            avg_vol5 = sum(volumes[-6:-1]) / 5
            vol_ratio = volumes[-1] / avg_vol5 if avg_vol5 > 0 else 1
            if vol_ratio >= 1.5 and change > 0:
                vol_relation = f"量價齊揚（量比 {vol_ratio:.1f}x）"
            elif vol_ratio >= 1.5 and change < 0:
                vol_relation = f"量增價跌，賣壓沉重（量比 {vol_ratio:.1f}x）"
            elif vol_ratio <= 0.7 and change > 0:
                vol_relation = f"量縮價漲，動能不足（量比 {vol_ratio:.1f}x）"
            elif vol_ratio <= 0.7 and change < 0:
                vol_relation = f"量縮整理（量比 {vol_ratio:.1f}x）"
            else:
                vol_relation = f"量能持平（量比 {vol_ratio:.1f}x）"

        # K 棒
        o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
        body = abs(c - o); full = h - l if h - l > 0 else 0.0001
        upper = h - max(o, c); lower = min(o, c) - l
        candle = "—"
        if body / full < 0.1: candle = "十字星（猶豫）"
        elif c > o and body / full > 0.7: candle = "長紅 K（強勢）"
        elif c < o and body / full > 0.7: candle = "長黑 K（弱勢）"
        elif c > o and lower > body * 2 and upper < body: candle = "下影線錘子（買盤承接）"
        elif c < o and upper > body * 2 and lower < body: candle = "上影線流星（賣壓出現）"
        elif c > o: candle = "紅 K"
        else: candle = "黑 K"
        if len(closes) >= 2:
            po, pc = opens[-2], closes[-2]
            if c > o and o < min(po, pc) and c > max(po, pc): candle += " + 多頭吞噬"
            elif c < o and o > max(po, pc) and c < min(po, pc): candle += " + 空頭吞噬"

        # 突破事件
        breakout_events = []
        if prev < high_20 and close > high_20: breakout_events.append(f"突破 20 日高 {high_20:.2f}")
        if prev > low_20 and close < low_20: breakout_events.append(f"跌破 20 日低 {low_20:.2f}")
        if prev < high_60 and close > high_60: breakout_events.append(f"突破 60 日高 {high_60:.2f}（中期）")
        if prev > low_60 and close < low_60: breakout_events.append(f"跌破 60 日低 {low_60:.2f}（中期）")
        if ma20 and prev < ma20 and close > ma20: breakout_events.append(f"站上月線 {ma20:.2f}")
        if ma20 and prev > ma20 and close < ma20: breakout_events.append(f"跌破月線 {ma20:.2f}")
        if ma60 and prev < ma60 and close > ma60: breakout_events.append(f"站上季線 {ma60:.2f}")
        if ma60 and prev > ma60 and close < ma60: breakout_events.append(f"跌破季線 {ma60:.2f}")

        # 多空計分
        reasons = []; bull_score = 0; bear_score = 0
        if ma5 and ma20 and ma60:
            if close > ma5 > ma20 > ma60:
                bull_score += 3; reasons.append("均線多頭排列（價>5>20>60）")
            elif close < ma5 < ma20 < ma60:
                bear_score += 3; reasons.append("均線空頭排列（價<5<20<60）")
            elif close > ma20:
                bull_score += 1; reasons.append(f"站上月線 {ma20:.2f}")
            else:
                bear_score += 1; reasons.append(f"位於月線 {ma20:.2f} 下方")

        if ma240 and close > ma240:
            bull_score += 2; reasons.append(f"位於年線 {ma240:.2f} 上方（長多）")
        elif ma240:
            bear_score += 2; reasons.append(f"位於年線 {ma240:.2f} 下方（長空）")

        for ev in breakout_events:
            if "突破" in ev or "站上" in ev: bull_score += 2; reasons.append(ev)
            else: bear_score += 2; reasons.append(ev)

        if k_val is not None and d_val is not None:
            if k_val > d_val and k_val < 80:
                bull_score += 1; reasons.append(f"KD 黃金交叉（K={k_val:.0f}, D={d_val:.0f}）")
            elif k_val < d_val and k_val > 20:
                bear_score += 1; reasons.append(f"KD 死亡交叉（K={k_val:.0f}, D={d_val:.0f}）")
            if k_val >= 80: reasons.append(f"KD 超買區（K={k_val:.0f}）")
            elif k_val <= 20: reasons.append(f"KD 超賣區（K={k_val:.0f}）")

        if rsi is not None:
            if rsi >= 70: reasons.append(f"RSI 超買 {rsi:.0f}")
            elif rsi <= 30: reasons.append(f"RSI 超賣 {rsi:.0f}")
            elif rsi > 50: bull_score += 1
            else: bear_score += 1

        if dif is not None and macd_line is not None:
            if dif > macd_line and osc > 0:
                bull_score += 1; reasons.append("MACD 紅柱（DIF>MACD）")
            elif dif < macd_line and osc < 0:
                bear_score += 1; reasons.append("MACD 綠柱（DIF<MACD）")

        if "齊揚" in vol_relation: bull_score += 2
        elif "量增價跌" in vol_relation: bear_score += 2

        # 多空結論
        if bull_score - bear_score >= 4:
            bias = "bullish"; bias_label = "偏多 ⬆️"
        elif bear_score - bull_score >= 4:
            bias = "bearish"; bias_label = "偏空 ⬇️"
        else:
            bias = "neutral"; bias_label = "中性整理 ➡️"

        # 型態
        if breakout_events and any("突破" in e or "站上" in e for e in breakout_events):
            pattern = "breakout"; pattern_name = "突破型態"
        elif breakout_events and any("跌破" in e for e in breakout_events):
            pattern = "breakdown"; pattern_name = "跌破型態"
        elif bias == "bullish":
            pattern = "uptrend"; pattern_name = "多頭趨勢"
        elif bias == "bearish":
            pattern = "downtrend"; pattern_name = "空頭趨勢"
        else:
            range_pct = (high_20 - low_20) / close * 100 if close else 0
            pattern = "range"
            pattern_name = "箱型整理" if range_pct < 8 else "震盪整理"

        # 支撐壓力
        supports = []; resistances = []
        def add_sr(level, label):
            if level is None or level <= 0: return
            if level < close * 0.999:
                supports.append({"level": round(level, 2), "label": label})
            elif level > close * 1.001:
                resistances.append({"level": round(level, 2), "label": label})
        for level, label in [(ma5, "5日線"), (ma20, "月線"), (ma60, "季線"),
                              (ma120, "半年線"), (ma240, "年線"),
                              (high_20, "20日高"), (low_20, "20日低"),
                              (high_60, "60日高"), (low_60, "60日低")]:
            add_sr(level, label)
        # 整數關卡
        magnitude = max(10 ** (len(str(int(close))) - 2), 1)
        round_above = (int(close / magnitude) + 1) * magnitude
        round_below = int(close / magnitude) * magnitude
        add_sr(round_above, "整數關卡")
        if round_below > 0: add_sr(round_below, "整數關卡")
        supports.sort(key=lambda x: -x["level"]); resistances.sort(key=lambda x: x["level"])
        supports = supports[:3]; resistances = resistances[:3]

        # 結論
        if bias == "bullish":
            key_r = resistances[0]["level"] if resistances else None
            key_s = supports[0]["level"] if supports else None
            conclusion = f"技術面偏多。短線盯 {key_r if key_r else '前高'} 壓力，未跌破 {key_s if key_s else '近期支撐'} 前可續抱。"
        elif bias == "bearish":
            key_r = resistances[0]["level"] if resistances else None
            key_s = supports[0]["level"] if supports else None
            conclusion = f"技術面偏空。{key_s if key_s else '近期低點'} 為下一支撐，反彈遇 {key_r if key_r else '前壓'} 仍以反彈視之。"
        else:
            r_high = resistances[0]["level"] if resistances else None
            s_low = supports[0]["level"] if supports else None
            conclusion = f"技術面中性。區間 {s_low}~{r_high}，突破或跌破後再表態。"

        # 進階：K 棒型態識別（純本機運算，秒解）
        candle_patterns = None
        if detect_patterns is not None:
            try:
                candle_patterns = detect_patterns(opens, highs, lows, closes)
            except Exception:
                pass

        # 進階：TradingView 26 指標綜合評等（網路呼叫）
        tv_rating = None
        if get_tv_rating is not None:
            market_for_tv = "TW" if code_or_ticker.isdigit() else "US"
            try:
                tv_rating = get_tv_rating(code_or_ticker, market_for_tv)
            except Exception:
                pass

        return {
            "ticker": ticker,
            "close": round(close, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 2),
            "volume": int(volumes[-1]),
            "vol_ratio": round(vol_ratio, 2),
            "ma": {"ma5": round(ma5, 2) if ma5 else None,
                   "ma10": round(ma10, 2) if ma10 else None,
                   "ma20": round(ma20, 2) if ma20 else None,
                   "ma60": round(ma60, 2) if ma60 else None,
                   "ma120": round(ma120, 2) if ma120 else None,
                   "ma240": round(ma240, 2) if ma240 else None},
            "rsi": round(rsi, 2) if rsi else None,
            "kd": {"k": round(k_val, 2) if k_val else None,
                   "d": round(d_val, 2) if d_val else None},
            "macd": {"dif": round(dif, 3) if dif else None,
                     "macd": round(macd_line, 3) if macd_line else None,
                     "osc": round(osc, 3) if osc else None},
            "pattern": pattern, "pattern_name": pattern_name,
            "bias": bias, "bias_label": bias_label,
            "reasons": reasons[:8],
            "breakout_events": breakout_events,
            "candle": candle,
            "volume_relation": vol_relation,
            "supports": supports,
            "resistances": resistances,
            "conclusion": conclusion,
            "bull_score": bull_score,
            "bear_score": bear_score,
            # 進階分析（可能為 None）
            "candle_patterns": candle_patterns,
            "tv_rating": tv_rating,
        }
    except Exception as e:
        print(f"[deep_kline_analysis] {code_or_ticker} 失敗：{e}")
        return None


# ===========================================================================
# 持股每日分析
# ===========================================================================
def analyze_holdings(enable_vision: bool = True) -> dict:
    """對所有持股做完整 K 線分析（含基本面、籌碼、AI 看圖）。平行抓取加速。"""

    def _fetch_tw(code, name):
        entry = {
            "code": code, "name": name, "market": "TW",
            "kline": deep_kline_analysis(code, market="TW"),
            "fund": fetch_fundamentals(code),
            "inst": fetch_institutional(code),
            "margin": fetch_margin(code),
            "chart_vision": None,
        }
        if enable_vision and chart_vision_for is not None:
            try:
                entry["chart_vision"] = chart_vision_for(code, name, "TW")
            except Exception as e:
                print(f"    [Vision 失敗] {e}")
        print(f"  [持股-TW] {code} {name} done")
        return entry

    def _fetch_us(ticker, name):
        entry = {
            "code": ticker, "name": name, "market": "US",
            "kline": deep_kline_analysis(ticker, market="US"),
            "fund": fetch_fundamentals(ticker),
            "chart_vision": None,
        }
        if enable_vision and chart_vision_for is not None:
            try:
                entry["chart_vision"] = chart_vision_for(ticker, name, "US")
            except Exception as e:
                print(f"    [Vision] {e}")
        print(f"  [持股-US] {ticker} {name} done")
        return entry

    tw_results: dict = {}
    us_results: dict = {}

    with ThreadPoolExecutor(max_workers=8) as exe:
        tw_futs = {exe.submit(_fetch_tw, c, n): c for c, n in HOLDINGS_TW}
        us_futs = {exe.submit(_fetch_us, t, n): t for t, n in HOLDINGS_US}
        for f in as_completed({**tw_futs, **us_futs}):
            key = tw_futs.get(f) or us_futs.get(f)
            try:
                res = f.result()
                if res["market"] == "TW":
                    tw_results[key] = res
                else:
                    us_results[key] = res
            except Exception as e:
                print(f"  [持股] {key} 失敗：{e}")

    return {
        "tw": [tw_results[c] for c, _ in HOLDINGS_TW if c in tw_results],
        "us": [us_results[t] for t, _ in HOLDINGS_US if t in us_results],
    }


# ===========================================================================
# 全市場掃描：台股
# ===========================================================================
def fetch_all_tw_listed() -> list:
    """從 TWSE OpenAPI 一次抓所有上市股票今日行情。"""
    url = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    try:
        resp = requests.get(url, headers=TWSE_HEADERS, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:
        print(f"[fetch_all_tw_listed] 失敗：{e}")
        return []

    result = []
    for r in rows:
        try:
            code = str(r.get("Code", "")).strip()
            name = str(r.get("Name", "")).strip()
            close = float(str(r.get("ClosingPrice", "0") or 0).replace(",", ""))
            change = float(str(r.get("Change", "0") or 0).replace(",", ""))
            if close <= 0:
                continue
            prev = close - change
            volume_share = int(str(r.get("TradeVolume", "0") or 0).replace(",", ""))
            result.append({
                "code": code, "name": name,
                "open": float(str(r.get("OpeningPrice", 0) or 0).replace(",", "")),
                "high": float(str(r.get("HighestPrice", 0) or 0).replace(",", "")),
                "low": float(str(r.get("LowestPrice", 0) or 0).replace(",", "")),
                "close": close,
                "change": change,
                "change_pct": (change / prev * 100) if prev > 0 else 0,
                "volume_lots": volume_share // 1000,
            })
        except Exception:
            continue
    return result


def _pre_filter_tw(all_stocks: list) -> list:
    """從全市場挑出候選股做深度分析。
    規則：
      - 代號 4 碼且開頭 1-9（排除權證、ETF、特別股）
      - 至少 N 張成交量（過濾冷門）
      - 取漲幅前 60 + 成交量前 60 + 跌幅前 20 後去重
    """
    min_vol = RECOMMENDATION["min_volume_lots"]
    normal = [s for s in all_stocks
              if len(s["code"]) == 4
              and s["code"][0] in "123456789"
              and s["volume_lots"] >= min_vol
              and s["close"] >= 10]  # 排除低價股

    by_gain = sorted(normal, key=lambda s: -s["change_pct"])[:60]
    by_volume = sorted(normal, key=lambda s: -s["volume_lots"])[:60]
    by_loss = sorted(normal, key=lambda s: s["change_pct"])[:20]

    seen = set(); candidates = []
    for batch in (by_gain, by_volume, by_loss):
        for s in batch:
            if s["code"] in seen: continue
            seen.add(s["code"]); candidates.append(s)

    return candidates[:RECOMMENDATION["tw_scan_candidates"]]


def _score_for_recommendation(kline: dict, fund: dict | None) -> tuple[int, str]:
    """給推薦評分。回傳 (分數, 主要理由標籤)。"""
    score = 0; tags = []

    if not kline:
        return 0, "—"

    bull_diff = kline.get("bull_score", 0) - kline.get("bear_score", 0)
    score += bull_diff * 2

    if kline.get("breakout_events"):
        ev = kline["breakout_events"][0]
        if "突破" in ev or "站上" in ev:
            score += 8; tags.append("突破")
        else:
            score -= 3

    vr = kline.get("volume_relation", "")
    if "齊揚" in vr:
        score += 5; tags.append("帶量")
    elif "量增價跌" in vr:
        score -= 5

    candle = kline.get("candle", "")
    if "長紅" in candle: score += 3; tags.append("長紅")
    if "多頭吞噬" in candle: score += 5; tags.append("多吞")
    if "下影線錘子" in candle: score += 3; tags.append("錘子")
    if "長黑" in candle: score -= 3
    if "空頭吞噬" in candle: score -= 5

    rsi = kline.get("rsi")
    if rsi:
        if rsi > 85: score -= 5
        elif 50 < rsi < 70: score += 2
        elif rsi < 30: score += 1; tags.append("超賣反彈")

    kd = kline.get("kd") or {}
    if kd.get("k") and kd.get("d") and kd["k"] > kd["d"] and kd["k"] < 80:
        score += 3

    # 漲幅控制：今天已大漲 >8% 的，分數打折（避免追高）
    chg = kline.get("change_pct", 0)
    if chg > 9: score -= 4
    elif chg > 6: score -= 2
    elif 0 < chg < 4: score += 2

    # 基本面加分
    if fund:
        rg = fund.get("revenue_growth")
        eg = fund.get("eps_growth")
        if rg and rg > 0.20: score += 4; tags.append("營收高成長")
        elif rg and rg > 0.10: score += 2; tags.append("營收成長")
        if eg and eg > 0.30: score += 4; tags.append("EPS爆發")
        elif eg and eg > 0.15: score += 2; tags.append("EPS成長")

    if not tags:
        if kline.get("bias") == "bullish": tags.append("技術偏多")
        elif kline.get("bias") == "bearish": tags.append("技術偏空")
        else: tags.append("中性")

    return score, " · ".join(tags[:3])


def scan_and_recommend_tw() -> dict:
    """全市場掃描台股 → 推薦 top N。"""
    print("  [TW Scan] 抓取全市場行情 ...")
    all_stocks = fetch_all_tw_listed()
    if not all_stocks:
        return {"candidates_count": 0, "picks": []}

    candidates = _pre_filter_tw(all_stocks)
    print(f"  [TW Scan] 全市場 {len(all_stocks)} 檔 → 候選 {len(candidates)} 檔")

    holdings_codes = {c for c, _ in HOLDINGS_TW}
    candidates = [c for c in candidates if c["code"] not in holdings_codes]

    def _analyze_tw(c):
        code = c["code"]
        kline = deep_kline_analysis(code, market="TW")
        if kline is None:
            return None
        fund = fetch_fundamentals(code)
        score, tags = _score_for_recommendation(kline, fund)
        return {
            "code": code, "name": c["name"], "market": "TW",
            "kline": kline, "fund": fund,
            "score": score, "tags": tags,
            "inst": fetch_institutional(code),
            "margin": fetch_margin(code),
        }

    scored = []
    with ThreadPoolExecutor(max_workers=10) as exe:
        futs = {exe.submit(_analyze_tw, c): c for c in candidates}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 20 == 0:
                print(f"  [TW Scan] {done}/{len(candidates)} ...")
            try:
                res = f.result()
                if res:
                    scored.append(res)
            except Exception:
                pass

    scored.sort(key=lambda x: -x["score"])
    top_n = RECOMMENDATION["top_n_recommend_tw"]
    return {
        "candidates_count": len(candidates),
        "scanned_count": len(scored),
        "picks": scored[:top_n],
    }


# ===========================================================================
# 美股推薦掃描
# ===========================================================================
def scan_and_recommend_us() -> dict:
    """掃描美股宇宙 → 推薦 top N。"""
    universe = list(US_UNIVERSE)[: RECOMMENDATION["us_universe_limit"]]
    holdings_set = {t for t, _ in HOLDINGS_US}
    universe = [t for t in universe if t not in holdings_set]
    print(f"  [US Scan] 宇宙 {len(universe)} 檔")

    def _analyze_us(ticker):
        kline = deep_kline_analysis(ticker, market="US")
        if kline is None:
            return None
        fund = fetch_fundamentals(ticker)
        score, tags = _score_for_recommendation(kline, fund)
        return {
            "code": ticker, "name": ticker, "market": "US",
            "kline": kline, "fund": fund,
            "score": score, "tags": tags,
        }

    scored = []
    with ThreadPoolExecutor(max_workers=10) as exe:
        futs = {exe.submit(_analyze_us, t): t for t in universe}
        done = 0
        for f in as_completed(futs):
            done += 1
            if done % 20 == 0:
                print(f"  [US Scan] {done}/{len(universe)} ...")
            try:
                res = f.result()
                if res:
                    scored.append(res)
            except Exception:
                pass

    scored.sort(key=lambda x: -x["score"])
    top_n = RECOMMENDATION["top_n_recommend_us"]
    return {
        "candidates_count": len(universe),
        "scanned_count": len(scored),
        "picks": scored[:top_n],
    }


# ===========================================================================
# 族群輪動偵測（只出強弱榜，不再做代表股深度卡）
# ===========================================================================
def analyze_tw_sectors() -> dict:
    sector_results = []
    for sector_name, members in TW_SECTORS.items():
        prices = []
        for code, name in members:
            p = fetch_stock_price(code)
            time.sleep(0.15)
            if p:
                prices.append({
                    "code": code, "name": name,
                    "change_pct": p["change_pct"],
                })
        if not prices: continue
        avg_pct = sum(x["change_pct"] for x in prices) / len(prices)
        sector_results.append({
            "name": sector_name,
            "avg_pct": round(avg_pct, 2),
            "stocks": prices,
            "leader": sorted(prices, key=lambda x: -x["change_pct"])[0],
            "laggard": sorted(prices, key=lambda x: x["change_pct"])[0],
        })

    sector_results.sort(key=lambda s: -s["avg_pct"])
    top_n = DISCOVERY["top_n_sectors"]; bot_n = DISCOVERY["bottom_n_sectors"]
    return {
        "strong": sector_results[:top_n],
        "weak": sector_results[-bot_n:][::-1],
    }


def analyze_us_sectors() -> dict:
    sector_results = []
    for sector_name, etf in US_SECTOR_ETFS.items():
        p = fetch_stock_price(etf)
        time.sleep(0.2)
        if not p: continue
        sector_results.append({
            "name": sector_name, "etf": etf,
            "etf_close": p["close"],
            "avg_pct": p["change_pct"],
        })
    sector_results.sort(key=lambda s: -s["avg_pct"])
    top_n = DISCOVERY["top_n_sectors"]; bot_n = DISCOVERY["bottom_n_sectors"]
    return {
        "strong": sector_results[:top_n],
        "weak": sector_results[-bot_n:][::-1],
    }
