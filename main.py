# -*- coding: utf-8 -*-
"""
早報主程式 — 持股每日追蹤 + 全市場掃描推薦 + 紙上交易機器人

使用方式：
  python main.py                 # 本機完整執行（含 AI via Claude SDK）
  python main.py --mode collect  # 只收集資料 → 存成 report_data.json
  python main.py --mode send     # 讀 report_data.json + ai_analysis.json → 寄信
"""

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from fetcher import (
    analyze_holdings,
    analyze_tw_sectors,
    analyze_us_sectors,
    fetch_taiex,
    fetch_us_indices,
    scan_and_recommend_tw,
    scan_and_recommend_us,
)
from mailer import build_html, send_report
from news_alert import scan_holdings_news
from paper_trader import run_paper_trader

# AI（Claude Agent SDK，本機用；遠端 routine 由外部 Claude 直接分析）
try:
    from ai_judge import (
        daily_market_summary, holdings_health_check,
        ai_final_verdict_batch, SDK_AVAILABLE as AI_AVAILABLE,
    )
except ImportError:
    AI_AVAILABLE = False
    daily_market_summary = lambda *a, **kw: ""
    holdings_health_check = lambda *a, **kw: ""
    ai_final_verdict_batch = lambda *a, **kw: []

DATA_FILE = Path(__file__).parent / "report_data.json"
AI_FILE   = Path(__file__).parent / "ai_analysis.json"


class _SafeEncoder(json.JSONEncoder):
    """把 numpy / pandas 型別轉成原生 Python，避免 JSON 序列化失敗。"""
    def default(self, obj):
        try:
            import numpy as np
            if isinstance(obj, np.integer):
                return int(obj)
            if isinstance(obj, np.floating):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
        except ImportError:
            pass
        if hasattr(obj, "item"):        # pandas scalar
            return obj.item()
        if hasattr(obj, "isoformat"):   # datetime / Timestamp
            return obj.isoformat()
        return super().default(obj)


# ---------------------------------------------------------------------------
# 資料收集（共用）
# ---------------------------------------------------------------------------
def _collect_data() -> dict:
    print(f"=== 資料收集 {datetime.now():%Y-%m-%d %H:%M:%S} ===")

    print("[1/8] 抓取台股加權指數 ...")
    taiex = fetch_taiex()
    print(f"      → {taiex}")

    print("[2/8] 抓取美股四大指數 ...")
    us_indices = fetch_us_indices()
    for name, info in us_indices.items():
        print(f"      → {name}: {info}")

    print("[3/8] 分析所有持股 ...")
    holdings = analyze_holdings()
    print(f"      → TW {len(holdings['tw'])} 檔 / US {len(holdings['us'])} 檔")

    print("[4+5/8] 全市場掃描台股 + 美股宇宙（同時進行）...")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as exe:
        fut_tw = exe.submit(scan_and_recommend_tw)
        fut_us = exe.submit(scan_and_recommend_us)
        rec_tw = fut_tw.result()
        rec_us = fut_us.result()
    print(f"      → 台股推薦 {len(rec_tw.get('picks', []))} 檔 / 美股推薦 {len(rec_us.get('picks', []))} 檔")

    print("[6/8] 紙上交易機器人結算 ...")
    paper_result = None
    try:
        paper_result = run_paper_trader()
    except Exception as e:
        print(f"      ⚠️ 紙上交易失敗：{e}")

    print("[7/8] 掃描持股新聞警報 ...")
    news_alerts = []
    try:
        extra = []
        if paper_result and paper_result.get("portfolio"):
            for code, pos in paper_result["portfolio"].get("positions", {}).items():
                market = "TW" if str(code).isdigit() else "US"
                extra.append((code, pos.get("name", code), market))
        news_alerts = scan_holdings_news(extra_codes=extra)
        print(f"      → 共 {len(news_alerts)} 檔出現警示新聞")
    except Exception as e:
        print(f"      ⚠️ 新聞掃描失敗：{e}")

    print("[8/8] 族群輪動 ...")
    tw_sectors = analyze_tw_sectors()
    us_sectors = analyze_us_sectors()

    # 把新聞附加到各股票 entry，方便 AI 直接讀 JSON 分析
    news_by_code = {n["code"]: n.get("alerts", []) for n in news_alerts}
    holdings_codes = set()

    for entry in holdings.get("tw", []) + holdings.get("us", []):
        entry["news_alerts"] = news_by_code.get(entry["code"], [])
        entry["source"] = "holding"
        holdings_codes.add(entry["code"])

    for entry in (rec_tw.get("picks", []) if rec_tw else []):
        entry["news_alerts"] = news_by_code.get(entry["code"], [])
        entry["source"] = "recommend"

    for entry in (rec_us.get("picks", []) if rec_us else []):
        entry["news_alerts"] = news_by_code.get(entry["code"], [])
        entry["source"] = "recommend"

    return {
        "taiex": taiex,
        "us_indices": us_indices,
        "holdings": holdings,
        "rec_tw": rec_tw,
        "rec_us": rec_us,
        "tw_sectors": tw_sectors,
        "us_sectors": us_sectors,
        "paper_html": paper_result["html"] if paper_result else None,
        "trade_signals": paper_result["trade_signals"] if paper_result else [],
        "news_alerts": news_alerts,
    }


# ---------------------------------------------------------------------------
# 模式 1：只收集資料，存 JSON（給遠端 routine 用）
# ---------------------------------------------------------------------------
def collect_mode() -> int:
    data = _collect_data()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, cls=_SafeEncoder)
    print(f"\n資料已存至 {DATA_FILE} ✅")
    return 0


# ---------------------------------------------------------------------------
# 模式 2：讀 JSON + AI 分析檔，組 HTML 寄信（給遠端 routine 用）
# ---------------------------------------------------------------------------
def send_mode() -> int:
    print(f"=== 寄信 {datetime.now():%Y-%m-%d %H:%M:%S} ===")

    if not DATA_FILE.exists():
        print(f"[ERROR] 找不到 {DATA_FILE}，請先執行 --mode collect")
        return 1

    with open(DATA_FILE, encoding="utf-8") as f:
        data = json.load(f)

    ai: dict = {}
    if AI_FILE.exists():
        with open(AI_FILE, encoding="utf-8") as f:
            ai = json.load(f)
        print(f"AI 分析載入：摘要 {len(ai.get('ai_market_summary', ''))} 字 / "
              f"持股 {len(ai.get('ai_verdicts_holdings', []))} 檔 / "
              f"推薦 {len(ai.get('ai_verdicts_recommend', []))} 檔")
    else:
        print("⚠️  ai_analysis.json 不存在，將不含 AI 內容寄出")

    html = build_html(
        data["taiex"], data["us_indices"], data["holdings"],
        data["rec_tw"], data["rec_us"],
        data["tw_sectors"], data["us_sectors"],
        paper_html=data.get("paper_html"),
        trade_signals=data.get("trade_signals", []),
        news_alerts=data.get("news_alerts", []),
        ai_market_summary=ai.get("ai_market_summary", ""),
        ai_verdicts_holdings=ai.get("ai_verdicts_holdings", []),
        ai_verdicts_recommend=ai.get("ai_verdicts_recommend", []),
        show_details=True,
    )
    send_report(html)
    print("寄送完成 ✅")

    try:
        from executor import execute_signals
        trade_signals = data.get("trade_signals", [])
        if trade_signals:
            execute_signals(trade_signals)
    except Exception as e:
        print(f"Executor 跳過：{e}")

    return 0


# ---------------------------------------------------------------------------
# 模式 3：本機完整執行（含 Claude SDK AI）
# ---------------------------------------------------------------------------
def full_mode() -> int:
    print(f"=== 早報 {datetime.now():%Y-%m-%d %H:%M:%S} ===")

    print("[1/7] 抓取台股加權指數 ...")
    taiex = fetch_taiex()
    print(f"      → {taiex}")

    print("[2/7] 抓取美股四大指數 ...")
    us_indices = fetch_us_indices()
    for name, info in us_indices.items():
        print(f"      → {name}: {info}")

    print("[3/7] 分析所有持股（每日必看） ...")
    holdings = analyze_holdings()
    print(f"      → TW {len(holdings['tw'])} 檔 / US {len(holdings['us'])} 檔")

    print("[4+5/7] 全市場掃描台股 + 美股宇宙（同時進行）...")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as exe:
        fut_tw = exe.submit(scan_and_recommend_tw)
        fut_us = exe.submit(scan_and_recommend_us)
        rec_tw = fut_tw.result()
        rec_us = fut_us.result()
    print(f"      → 台股推薦 {len(rec_tw.get('picks', []))} 檔 / 美股推薦 {len(rec_us.get('picks', []))} 檔")

    print("[6/8] 紙上交易機器人結算 ...")
    paper_result = None
    try:
        paper_result = run_paper_trader()
    except Exception as e:
        print(f"      ⚠️ 紙上交易執行失敗：{e}")

    print("[7/8] 掃描持股新聞警報 ...")
    news_alerts = []
    try:
        extra = []
        if paper_result and paper_result.get("portfolio"):
            for code, pos in paper_result["portfolio"].get("positions", {}).items():
                market = "TW" if str(code).isdigit() else "US"
                extra.append((code, pos.get("name", code), market))
        news_alerts = scan_holdings_news(extra_codes=extra)
        print(f"      → 共 {len(news_alerts)} 檔出現警示新聞")
    except Exception as e:
        print(f"      ⚠️ 新聞掃描失敗：{e}")

    print("[8/9] 族群輪動 ...")
    tw_sectors = analyze_tw_sectors()
    us_sectors = analyze_us_sectors()

    print("[9/9] AI 最終決策 + 大盤摘要 + 組 HTML 寄信 ...")
    ai_market_summary = ""
    ai_verdicts_holdings: list = []
    ai_verdicts_recommend: list = []

    if AI_AVAILABLE:
        try:
            ai_market_summary = daily_market_summary(taiex, us_indices, tw_sectors, us_sectors)
            print(f"      → AI 摘要 {len(ai_market_summary)} 字")
        except Exception as e:
            print(f"      ⚠️ AI 摘要失敗：{e}")

        news_by_code = {n["code"]: n["alerts"] for n in news_alerts}
        holdings_codes = set()
        all_stocks_for_ai = []

        for entry in holdings.get("tw", []) + holdings.get("us", []):
            entry["news_alerts"] = news_by_code.get(entry["code"], [])
            entry["source"] = "holding"
            holdings_codes.add(entry["code"])
            all_stocks_for_ai.append(entry)

        for entry in (rec_tw.get("picks", []) if rec_tw else []):
            if entry["code"] in holdings_codes:
                continue
            entry["news_alerts"] = news_by_code.get(entry["code"], [])
            entry["source"] = "recommend"
            all_stocks_for_ai.append(entry)
        for entry in (rec_us.get("picks", []) if rec_us else []):
            if entry["code"] in holdings_codes:
                continue
            entry["news_alerts"] = news_by_code.get(entry["code"], [])
            entry["source"] = "recommend"
            all_stocks_for_ai.append(entry)

        try:
            all_verdicts = ai_final_verdict_batch(all_stocks_for_ai)
            print(f"      → AI 決策 {len(all_verdicts)} 檔")
        except Exception as e:
            print(f"      ⚠️ AI 決策失敗：{e}")
            all_verdicts = []

        ai_verdicts_holdings = [v for v in all_verdicts if v.get("source") == "holding"]
        ai_verdicts_recommend = [v for v in all_verdicts if v.get("source") == "recommend"]
        buy_only = {"強力買進", "買進", "持有觀望"}
        ai_verdicts_recommend = [v for v in ai_verdicts_recommend
                                  if v.get("verdict") in buy_only]
        print(f"      → 持股 {len(ai_verdicts_holdings)} 檔 / 推薦 {len(ai_verdicts_recommend)} 檔（已過濾）")

    paper_html = paper_result["html"] if paper_result else None
    trade_signals = paper_result["trade_signals"] if paper_result else []
    html = build_html(taiex, us_indices, holdings, rec_tw, rec_us,
                      tw_sectors, us_sectors,
                      paper_html=paper_html,
                      trade_signals=trade_signals,
                      news_alerts=news_alerts,
                      ai_market_summary=ai_market_summary,
                      ai_verdicts_holdings=ai_verdicts_holdings,
                      ai_verdicts_recommend=ai_verdicts_recommend)
    send_report(html)
    print("      → 寄送完成 ✅")

    try:
        from executor import execute_signals
        if trade_signals:
            execute_signals(trade_signals)
    except Exception as e:
        print(f"      ⚠️ Executor 跳過：{e}")

    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="台股早報系統")
    parser.add_argument(
        "--mode",
        choices=["full", "collect", "send"],
        default="full",
        help="full=本機完整執行（預設）/ collect=只收集資料存 JSON / send=讀 JSON 寄信",
    )
    args = parser.parse_args()

    try:
        if args.mode == "collect":
            sys.exit(collect_mode())
        elif args.mode == "send":
            sys.exit(send_mode())
        else:
            sys.exit(full_mode())
    except Exception:
        print("執行失敗：")
        traceback.print_exc()
        sys.exit(1)
