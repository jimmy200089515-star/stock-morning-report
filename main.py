# -*- coding: utf-8 -*-
"""
早報主程式 — 持股每日追蹤 + 全市場掃描推薦 + 紙上交易機器人
"""

import sys
import traceback
from datetime import datetime

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

# AI（Claude Agent SDK，可選）
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


def main() -> int:
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

    print("[4/7] 全市場掃描台股 → 推薦 ...")
    rec_tw = scan_and_recommend_tw()
    print(f"      → 推薦 {len(rec_tw.get('picks', []))} 檔")

    print("[5/7] 掃描美股宇宙 → 推薦 ...")
    rec_us = scan_and_recommend_us()
    print(f"      → 推薦 {len(rec_us.get('picks', []))} 檔")

    print("[6/8] 紙上交易機器人結算 ...")
    paper_result = None
    try:
        paper_result = run_paper_trader()
    except Exception as e:
        print(f"      ⚠️ 紙上交易執行失敗：{e}")

    print("[7/8] 掃描持股新聞警報 ...")
    news_alerts = []
    try:
        # 把 paper trader 目前持倉也一併納入掃描
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

        # 收集所有要 AI 判斷的股票，並標記 source
        news_by_code = {n["code"]: n["alerts"] for n in news_alerts}
        holdings_codes = set()

        all_stocks_for_ai = []
        for entry in holdings.get("tw", []) + holdings.get("us", []):
            entry["news_alerts"] = news_by_code.get(entry["code"], [])
            entry["source"] = "holding"
            holdings_codes.add(entry["code"])
            all_stocks_for_ai.append(entry)

        # 推薦標記為 recommend；如果跟持股重複，跳過（持股優先）
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

        # 分流：持股 vs 推薦
        ai_verdicts_holdings = [v for v in all_verdicts if v.get("source") == "holding"]
        ai_verdicts_recommend = [v for v in all_verdicts if v.get("source") == "recommend"]

        # 推薦端：過濾掉「賣出/減碼/強力賣出/暫不進場」(只留可進場的)
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

    # 若已設定券商 API，這裡會丟給 executor 去下單（預設 dry_run）
    try:
        from executor import execute_signals
        if trade_signals:
            execute_signals(trade_signals)
    except Exception as e:
        print(f"      ⚠️ Executor 跳過：{e}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        print("執行失敗：")
        traceback.print_exc()
        sys.exit(1)
