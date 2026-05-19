# -*- coding: utf-8 -*-
"""
新聞警報模組 — 掃描持股近 24 小時新聞，標出「一看就挫賽」的負面字眼

來源：
- yfinance.Ticker.news（主，但對台股覆蓋率不一定好）
- Yahoo TW 股市新聞頁（備援，scrape）

警示後不會自動賣股，只在早報最頂部放紅色橫幅提醒。
"""

from __future__ import annotations

import io
import logging
import re
import sys
import time
from datetime import datetime, timedelta

if sys.platform == "win32" and getattr(sys.stdout, "encoding", "").lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests
import yfinance as yf

from config import HOLDINGS_TW, HOLDINGS_US
from fetcher import _resolve_yf_ticker

# AI 判斷（若有 Claude Agent SDK 就用）
try:
    from ai_judge import judge_news_batch, SDK_AVAILABLE as AI_AVAILABLE
except ImportError:
    AI_AVAILABLE = False
    judge_news_batch = None

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ============================================================
# 負面關鍵字
# ============================================================
NEGATIVE_KEYWORDS_TW = [
    # 公司結構問題
    "下市", "停牌", "暫停交易", "減資", "增資", "倒閉", "破產", "重整",
    "違約", "違法", "起訴", "判刑", "詐欺", "違規", "舞弊", "掏空",
    # 業務問題
    "停產", "召回", "停工", "罷工", "歇業", "裁員",
    # 監管問題
    "查封", "罰款", "監管", "金管會", "調查",
    # 資安/事件
    "駭客", "資料外洩", "資安事件", "工安",
    # 股價/業績警示
    "預警", "下修", "獲利警示", "重挫", "崩跌", "跌停", "黑天鵝", "崩盤",
    "減持", "出脫", "違約金", "巨虧", "鉅額虧損",
    # 高層異動
    "解任", "離職", "出走", "辭職", "請辭",
    # 訴訟/糾紛
    "訴訟", "求償", "賠償", "和解",
]

NEGATIVE_KEYWORDS_US = [
    # Company structure
    "bankrupt", "bankruptcy", "delisted", "delisting", "liquidat",
    "default", "restructur", "shutdown", "shut down",
    # Legal/regulatory
    "fraud", "lawsuit", "sue", "investigat", "subpoena", "settlement",
    "guilty", "indict", "violat", "fined", "penalty",
    "sec ", "doj ", "fcc ", "ftc ", "probe", "scandal",
    # Operations
    "recall", "halt", "ban", "block", "embargo",
    "layoff", "fire", "firing", "fired", "resign", "step down",
    # Stock/earnings warnings
    "miss earnings", "earnings miss", "guidance cut", "downgrade",
    "plunge", "crash", "tumble", "slump", "collapse",
    "warning", "alert",
    # Security
    "hack", "breach", "leak", "ransomware",
]


# ============================================================
# 抓新聞
# ============================================================
def _normalize_news_item(item: dict) -> dict:
    """yfinance 新舊版資料格式不同，統一成 {title, publisher, link, timestamp}"""
    # 新版 v0.2.40+：item["content"]["title"] / item["content"]["provider"]...
    content = item.get("content", {}) if isinstance(item, dict) else {}
    title = item.get("title") or content.get("title") or ""
    publisher = (item.get("publisher")
                 or content.get("provider", {}).get("displayName", "")
                 or "—")
    link = item.get("link") or content.get("canonicalUrl", {}).get("url", "") or ""
    ts = (item.get("providerPublishTime")
          or content.get("pubDate") or 0)

    # 字串時間戳轉 timestamp
    if isinstance(ts, str):
        try:
            ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
        except Exception:
            ts = 0

    return {"title": title, "publisher": publisher, "link": link,
            "timestamp": ts}


def fetch_yf_news(ticker: str, max_items: int = 15) -> list[dict]:
    try:
        raw = yf.Ticker(ticker).news
        if not raw:
            return []
        return [_normalize_news_item(it) for it in raw[:max_items]]
    except Exception:
        return []


def fetch_yahoo_tw_news(code: str, max_items: int = 10) -> list[dict]:
    """從 Yahoo TW 股市頁面抓新聞。"""
    url = f"https://tw.stock.yahoo.com/quote/{code}.TW/news"
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        html = resp.text
        # 簡單抓 <h3>標題</h3>，Yahoo 結構簡單時可用
        titles = re.findall(r'<h3[^>]*>([^<]{8,})</h3>', html)
        return [{"title": t.strip(), "publisher": "Yahoo TW",
                 "link": url, "timestamp": int(time.time())}
                for t in titles[:max_items]]
    except Exception:
        return []


# ============================================================
# 關鍵字偵測
# ============================================================
def has_negative(title: str, keywords: list[str]) -> list[str]:
    if not title:
        return []
    title_lower = title.lower()
    matched = []
    for kw in keywords:
        if kw.lower() in title_lower:
            matched.append(kw)
    return matched


def scan_stock(code: str, name: str, market: str = "TW",
                use_ai: bool = True, ai_severity_threshold: int = 6) -> dict | None:
    """掃單檔股票的新聞，回傳警示（無警示則 None）。

    use_ai: 用 Claude 判斷（更準）；否則 fallback 關鍵字比對
    ai_severity_threshold: AI 判 severity >= 此值才列警示（預設 6 = 值得留意以上）
    """
    if market == "TW":
        ticker = _resolve_yf_ticker(code)
        keywords = NEGATIVE_KEYWORDS_TW
    else:
        ticker = code
        keywords = NEGATIVE_KEYWORDS_US

    if not ticker:
        return None

    news = fetch_yf_news(ticker)
    if not news and market == "TW":
        news = fetch_yahoo_tw_news(code)

    if not news:
        return None

    now = datetime.now()

    # 過濾近 36 小時 + 去重
    recent_news = []
    seen_titles = set()
    for item in news:
        title = item["title"]
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        if item["timestamp"]:
            try:
                ts = datetime.fromtimestamp(item["timestamp"])
                if (now - ts).total_seconds() > 36 * 3600:
                    continue
            except Exception:
                pass
        recent_news.append(item)

    if not recent_news:
        return None

    # ========================================
    # 路徑 A：用 AI 判斷（推薦）
    # ========================================
    if use_ai and AI_AVAILABLE:
        ai_results = judge_news_batch(code, name, market, recent_news[:10])
        # 只留 severity >= 門檻 且 sentiment != 中性的
        alerts = [
            {
                "title": r["title"],
                "publisher": r["publisher"],
                "link": r["link"],
                "sentiment": r["sentiment"],
                "severity": r["severity"],
                "reason": r["reason"],
                "action": r["action"],
                "keywords": [],  # AI 模式不用 keyword
                "ai": True,
            }
            for r in ai_results
            if r["severity"] >= ai_severity_threshold and r["sentiment"] != "中性"
        ]
        # 依嚴重度排序
        alerts.sort(key=lambda x: -x["severity"])
        if alerts:
            return {"code": code, "name": name, "market": market,
                    "ticker": ticker, "alerts": alerts[:5]}
        return None

    # ========================================
    # 路徑 B：Fallback 關鍵字判斷
    # ========================================
    alerts = []
    for item in recent_news:
        matched = has_negative(item["title"], keywords)
        if matched:
            alerts.append({
                "title": item["title"],
                "publisher": item["publisher"],
                "link": item["link"],
                "keywords": matched[:3],
                "ai": False,
            })
    if alerts:
        return {"code": code, "name": name, "market": market,
                "ticker": ticker, "alerts": alerts[:5]}
    return None


# ============================================================
# 對外
# ============================================================
def scan_holdings_news(extra_codes: list[tuple] | None = None) -> list[dict]:
    """掃描所有持股的新聞警報。

    extra_codes: 額外要掃的標的 [(code, name, market)]，例如 paper trader 目前持倉
    """
    print("[News] 掃描持股近期新聞 ...")
    results = []

    for code, name in HOLDINGS_TW:
        r = scan_stock(code, name, "TW")
        if r:
            results.append(r)
            print(f"  🚨 {code} {name}：{len(r['alerts'])} 則警示")
        time.sleep(0.3)

    for ticker, name in HOLDINGS_US:
        r = scan_stock(ticker, name, "US")
        if r:
            results.append(r)
            print(f"  🚨 {ticker} {name}：{len(r['alerts'])} 則警示")
        time.sleep(0.3)

    for extra in (extra_codes or []):
        code, name, market = extra
        r = scan_stock(code, name, market)
        if r and not any(x["code"] == code for x in results):
            results.append(r)
            print(f"  🚨 {code} {name}（紙上持倉）：{len(r['alerts'])} 則警示")
        time.sleep(0.3)

    if not results:
        print("  ✅ 無重大負面新聞")
    return results


if __name__ == "__main__":
    import json
    alerts = scan_holdings_news()
    print("\n" + "=" * 50)
    print(f"共 {len(alerts)} 檔有警示新聞")
    print("=" * 50)
    print(json.dumps(alerts, ensure_ascii=False, indent=2, default=str))
