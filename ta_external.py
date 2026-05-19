# -*- coding: utf-8 -*-
"""
TradingView 技術評等抓取

用 tradingview-ta 套件取 TV 網頁上的 26 指標綜合評等。
注意：屬於爬 TV 公開頁面，TV TOS 灰色地帶，僅供個人參考。

安裝：
    pip install tradingview-ta
"""

from __future__ import annotations

import time
from typing import Optional


try:
    from tradingview_ta import TA_Handler, Interval, Exchange
    TV_AVAILABLE = True
except ImportError:
    TV_AVAILABLE = False
    TA_Handler = None
    Interval = None


# 把 TV 英文評等翻成中文 + 顏色標籤
RECO_LABEL = {
    "STRONG_BUY": ("強力買進", "#b71c1c", "🟢🟢"),
    "BUY":        ("買進",     "#d32f2f", "🟢"),
    "NEUTRAL":    ("中立",     "#757575", "⚪"),
    "SELL":       ("賣出",     "#2e7d32", "🔴"),
    "STRONG_SELL":("強力賣出", "#1b5e20", "🔴🔴"),
}


def get_tv_rating(code: str, market: str = "TW") -> Optional[dict]:
    """抓 TradingView 技術評等。

    code: 純代號（台股例 "2330"）或完整 ticker（美股例 "NVDA"）
    market: "TW" / "US"
    """
    if not TV_AVAILABLE:
        return None

    try:
        if market == "TW":
            # TV 上市/上櫃都歸 TWSE
            symbol = code.replace(".TW", "").replace(".TWO", "")
            exchange = "TWSE"
            screener = "taiwan"
        else:
            symbol = code
            exchange = "NASDAQ"  # 嘗試 NASDAQ，失敗會自動 NYSE
            screener = "america"

        # 試 NASDAQ
        try:
            handler = TA_Handler(
                symbol=symbol, exchange=exchange,
                screener=screener,
                interval=Interval.INTERVAL_1_DAY,
                timeout=10,
            )
            analysis = handler.get_analysis()
        except Exception:
            # 美股 fallback 試 NYSE
            if market == "US":
                handler = TA_Handler(
                    symbol=symbol, exchange="NYSE",
                    screener="america",
                    interval=Interval.INTERVAL_1_DAY,
                    timeout=10,
                )
                analysis = handler.get_analysis()
            else:
                raise

        summary = analysis.summary
        recommend = summary.get("RECOMMENDATION", "NEUTRAL")
        label, color, emoji = RECO_LABEL.get(recommend, ("—", "#757575", "⚪"))

        # 移動均線 / 震盪指標各別評等
        ma_reco = analysis.moving_averages.get("RECOMMENDATION", "NEUTRAL")
        osc_reco = analysis.oscillators.get("RECOMMENDATION", "NEUTRAL")

        return {
            "recommendation": recommend,
            "label": label,
            "color": color,
            "emoji": emoji,
            "buy_count": summary.get("BUY", 0),
            "sell_count": summary.get("SELL", 0),
            "neutral_count": summary.get("NEUTRAL", 0),
            "ma_reco": RECO_LABEL.get(ma_reco, (ma_reco, "", ""))[0],
            "osc_reco": RECO_LABEL.get(osc_reco, (osc_reco, "", ""))[0],
            "interval": "1D",
        }
    except Exception as e:
        # TV 沒收到資料或 symbol 找不到 → 安靜回 None，不擾報告
        return None


def get_tv_rating_multi_interval(code: str, market: str = "TW") -> dict:
    """抓多個時間框（日、週）。給較深入分析用。"""
    if not TV_AVAILABLE:
        return {}
    result = {}
    for tf_label, tf in [("1D", "INTERVAL_1_DAY"), ("1W", "INTERVAL_1_WEEK")]:
        try:
            symbol = code.replace(".TW", "").replace(".TWO", "")
            exchange = "TWSE" if market == "TW" else "NASDAQ"
            screener = "taiwan" if market == "TW" else "america"
            handler = TA_Handler(
                symbol=symbol, exchange=exchange, screener=screener,
                interval=getattr(Interval, tf), timeout=10,
            )
            analysis = handler.get_analysis()
            reco = analysis.summary.get("RECOMMENDATION", "NEUTRAL")
            result[tf_label] = RECO_LABEL.get(reco, (reco, "", ""))[0]
        except Exception:
            result[tf_label] = "—"
        time.sleep(0.2)
    return result


if __name__ == "__main__":
    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print(f"TV 套件可用：{TV_AVAILABLE}")
    if TV_AVAILABLE:
        for code, market in [("2330", "TW"), ("3037", "TW"), ("NVDA", "US"), ("TSLA", "US")]:
            r = get_tv_rating(code, market)
            print(f"\n{code} ({market}):")
            if r:
                print(f"  總評：{r['emoji']} {r['label']}")
                print(f"  BUY:{r['buy_count']} / SELL:{r['sell_count']} / NEUTRAL:{r['neutral_count']}")
                print(f"  均線:{r['ma_reco']} / 震盪:{r['osc_reco']}")
            else:
                print(f"  無資料")
