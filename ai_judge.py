# -*- coding: utf-8 -*-
"""
AI 判斷模組 — 透過 Claude Agent SDK 用你的 Max 訂閱額度

無需 Anthropic API Key，使用 Max 訂閱免費呼叫。

安裝：
    pip install claude-agent-sdk

依賴的環境：
- 本機已登入 Claude Code（你現在用的就是）
- Claude Code SDK 會自動使用該登入狀態

未安裝 SDK 時：所有 judge_xxx 函式回傳 None，呼叫端會 fallback 到舊邏輯。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional


try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    SDK_AVAILABLE = True
except ImportError:
    try:
        # 舊版套件名稱備援
        from claude_code_sdk import query
        ClaudeAgentOptions = None  # type: ignore
        SDK_AVAILABLE = True
    except ImportError:
        SDK_AVAILABLE = False
        query = None
        ClaudeAgentOptions = None


# ============================================================
# 底層：呼叫 Claude
# ============================================================
async def _ask_claude_async(prompt: str, model: str = "haiku") -> str:
    """非同步問 Claude，回字串。失敗回空字串。"""
    if not SDK_AVAILABLE:
        return ""
    try:
        # 嘗試用 ClaudeAgentOptions 指定快版模型省 quota
        kwargs = {"prompt": prompt}
        if ClaudeAgentOptions is not None:
            try:
                kwargs["options"] = ClaudeAgentOptions(model=model)
            except Exception:
                pass

        chunks: list[str] = []
        async for message in query(**kwargs):
            content = getattr(message, "content", None)
            if content is None:
                continue
            if isinstance(content, str):
                chunks.append(content)
                continue
            for block in content:
                text = getattr(block, "text", None)
                if text:
                    chunks.append(text)
        return "".join(chunks).strip()
    except Exception as e:
        print(f"[AI] 呼叫失敗：{e}")
        return ""


def ask_claude(prompt: str, model: str = "haiku") -> str:
    """同步包裝。"""
    if not SDK_AVAILABLE:
        return ""
    try:
        return asyncio.run(_ask_claude_async(prompt, model))
    except RuntimeError:
        # 已在 event loop 中（罕見），用 new_event_loop
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_ask_claude_async(prompt, model))
        finally:
            loop.close()


# ============================================================
# 解析 JSON（容錯）
# ============================================================
def _extract_json(text: str) -> Optional[dict | list]:
    if not text:
        return None
    # 找第一個 {...} 或 [...]
    for pattern in (r'\[[\s\S]*\]', r'\{[\s\S]*\}'):
        m = re.search(pattern, text)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                continue
    return None


# ============================================================
# 1. 新聞判斷（批次：一檔股票多則新聞一次問）
# ============================================================
def judge_news_batch(code: str, name: str, market: str,
                     news_items: list[dict]) -> list[dict]:
    """批次評估某檔股票的多則新聞。

    回傳：[{title, sentiment, severity, reason, action}, ...]
    severity: 1-10, 10=立刻逃命
    sentiment: "利多" / "利空" / "中性"
    action: "持有" / "減碼" / "出場"
    """
    if not SDK_AVAILABLE or not news_items:
        return []

    titles_block = "\n".join(
        f"{i+1}. {item['title']}"
        for i, item in enumerate(news_items)
    )

    market_label = "台股" if market == "TW" else "美股"

    prompt = f"""你是專業股市分析師。請評估以下關於 {market_label} {code} {name} 的新聞，
對該公司的股價影響有多嚴重。

注意：
- severity 1-3 = 輕微/無關，5-6 = 值得留意，7-8 = 嚴重利空/利多，9-10 = 緊急
- 要區分「利空消息本身」vs「澄清/否認利空」(後者通常正面)
- 個股直接影響 > 產業/大盤間接影響

新聞清單：
{titles_block}

回 JSON array（不要其他文字、不要 markdown 程式碼框）：
[
  {{"idx": 1, "sentiment": "利多|利空|中性", "severity": 數字1-10, "reason": "簡短理由(20字內)", "action": "持有|減碼|出場"}},
  ...
]"""

    # 新聞批次判斷量大、判斷簡單 → 用 Haiku 省 quota
    response = ask_claude(prompt, model="claude-haiku-4-5")
    parsed = _extract_json(response)
    if not isinstance(parsed, list):
        return []

    results = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx", 0)
        if 1 <= idx <= len(news_items):
            results.append({
                "title": news_items[idx - 1]["title"],
                "publisher": news_items[idx - 1].get("publisher", "—"),
                "link": news_items[idx - 1].get("link", ""),
                "sentiment": item.get("sentiment", "中性"),
                "severity": int(item.get("severity", 5)),
                "reason": item.get("reason", "—"),
                "action": item.get("action", "持有"),
            })
    return results


# ============================================================
# 2. 每日大盤摘要 — 給人看的「今天市場一句話」
# ============================================================
def daily_market_summary(taiex: dict | None, us_indices: dict,
                         tw_sectors: dict, us_sectors: dict) -> str:
    """產生 2-3 句中文每日盤勢摘要（嵌在 email 頂部）。"""
    if not SDK_AVAILABLE:
        return ""

    taiex_str = "資料暫缺"
    if taiex:
        taiex_str = f"{taiex['close']:,} ({taiex['change_pct']:+.2f}%)"

    us_str_parts = []
    for name, info in us_indices.items():
        if info:
            us_str_parts.append(f"{name} {info['change_pct']:+.2f}%")
    us_str = " / ".join(us_str_parts) if us_str_parts else "資料暫缺"

    tw_strong = ", ".join(s["name"] for s in tw_sectors.get("strong", [])[:3])
    tw_weak = ", ".join(s["name"] for s in tw_sectors.get("weak", [])[:3])
    us_strong = ", ".join(s["name"] for s in us_sectors.get("strong", [])[:3])

    prompt = f"""你是台股盤勢分析師。請用 3-4 句中文寫今日盤勢摘要（口語化、像跟朋友聊天）：

數據：
- 台股加權：{taiex_str}
- 美股：{us_str}
- 台股強勢族群：{tw_strong}
- 台股弱勢族群：{tw_weak}
- 美股強勢板塊：{us_strong}

要求：
- 直接寫結論，不要客套話
- 點出今天最重要的觀察
- 可以提到資金流向、產業輪動
- 不要重複數字，要解讀
- 全程繁體中文

直接寫摘要，不要前綴。"""

    # 摘要要有觀點、有靈氣 → 用 Sonnet
    return ask_claude(prompt, model="claude-sonnet-4-6")


# ============================================================
# 3. 持股健檢 — AI 看完所有持股後給綜合意見
# ============================================================
def holdings_health_check(holdings: dict) -> str:
    """看完所有持股的 K 線資料後，給綜合健檢意見。"""
    if not SDK_AVAILABLE:
        return ""

    summary_parts = []
    for entry in holdings.get("tw", []) + holdings.get("us", []):
        kline = entry.get("kline") or {}
        if not kline:
            continue
        summary_parts.append(
            f"- {entry['code']} {entry['name']}: "
            f"{kline.get('bias_label', '—')} ｜ "
            f"型態 {kline.get('pattern_name', '—')} ｜ "
            f"收盤 {kline.get('close', '—')} ｜ "
            f"漲跌 {kline.get('change_pct', 0):+.2f}% ｜ "
            f"K棒 {kline.get('candle', '—')}"
        )

    if not summary_parts:
        return ""

    holdings_text = "\n".join(summary_parts[:30])

    prompt = f"""你是專業股市分析師。看完以下持股的技術面，給出 3-5 點具體建議（每點一行，繁體中文）：

{holdings_text}

要求：
- 點出哪幾檔最該注意（強勢延續/危險訊號）
- 哪幾檔可以加碼，哪幾檔該減碼
- 如有共同警訊（多檔同時轉弱）要提
- 不要客套話，直接給可操作建議
- 用簡潔短句，不超過 5 行"""

    # 健檢要整合多檔技術面、給策略級建議 → 用 Sonnet
    return ask_claude(prompt, model="claude-sonnet-4-6")


# ============================================================
# 4. 🎯 最終決策：所有股票丟給 AI，回 BUY/SELL/HOLD + 價位 + 理由
# ============================================================
def _compact_stock_context(s: dict) -> str:
    """把一檔股票的所有資料壓成精簡文字（給 AI 看）。"""
    kline = s.get("kline") or {}
    fund = s.get("fund") or {}
    vision = (s.get("chart_vision") or {})
    news = s.get("news_alerts") or []
    inst = s.get("inst") or {}

    code = s["code"]; name = s["name"]; market = s.get("market", "TW")

    if not kline:
        return f"{code} {name} ({market}): 技術資料不足"

    lines = [f"{code} {name} ({market}): 收盤 {kline.get('close')} 漲跌 {kline.get('change_pct'):+.2f}%"]

    # 技術面
    lines.append(f"  技術: {kline.get('bias_label','—')} | 型態 {kline.get('pattern_name','—')} | "
                f"分數 {kline.get('bull_score',0)}/{kline.get('bear_score',0)}")
    lines.append(f"  K棒: {kline.get('candle','—')} | 量價 {kline.get('volume_relation','—')}")

    # 均線位置
    ma = kline.get("ma", {})
    close = kline.get("close", 0)
    ma_lines = []
    for label, key in [("5", "ma5"), ("20", "ma20"), ("60", "ma60"), ("240", "ma240")]:
        v = ma.get(key)
        if v:
            pos = "上" if close > v else "下"
            ma_lines.append(f"MA{label}={v:.1f}({pos})")
    if ma_lines:
        lines.append(f"  均線: {' '.join(ma_lines)}")

    # 指標
    kd = kline.get("kd") or {}
    macd = kline.get("macd") or {}
    ind = []
    if kline.get("rsi") is not None: ind.append(f"RSI={kline['rsi']:.0f}")
    if kd.get("k") is not None: ind.append(f"KD={kd['k']:.0f}/{kd['d']:.0f}")
    if macd.get("osc") is not None: ind.append(f"MACD柱={macd['osc']:+.2f}")
    if ind:
        lines.append(f"  指標: {' | '.join(ind)}")

    # K 棒型態識別
    cp = kline.get("candle_patterns") or {}
    pat_parts = []
    if cp.get("bullish"): pat_parts.append("多:" + ",".join(cp["bullish"][:3]))
    if cp.get("bearish"): pat_parts.append("空:" + ",".join(cp["bearish"][:3]))
    if pat_parts:
        lines.append(f"  K型態: {' / '.join(pat_parts)}")

    # TV 評等
    tv = kline.get("tv_rating") or {}
    if tv:
        lines.append(f"  TradingView: {tv.get('label','—')} "
                    f"(買{tv.get('buy_count',0)}/賣{tv.get('sell_count',0)})")

    # 支撐壓力
    sups = kline.get("supports", [])
    ress = kline.get("resistances", [])
    if ress:
        res_str = ", ".join(f"{r['level']:.1f}" for r in ress[:3])
        lines.append(f"  壓力: {res_str}")
    if sups:
        sup_str = ", ".join(f"{s['level']:.1f}" for s in sups[:3])
        lines.append(f"  支撐: {sup_str}")

    # 基本面
    if fund:
        fb = []
        if fund.get("pe"): fb.append(f"PE={fund['pe']:.1f}")
        if fund.get("revenue_growth") is not None:
            fb.append(f"營收YoY={fund['revenue_growth']*100:+.1f}%")
        if fund.get("eps_growth") is not None:
            fb.append(f"EPS YoY={fund['eps_growth']*100:+.1f}%")
        if fb:
            lines.append(f"  基本面: {' | '.join(fb)}")

    # 法人
    if inst:
        lines.append(f"  法人: 外{inst.get('foreign',0):+d}張 投{inst.get('trust',0):+d}張")

    # AI 看圖
    if vision.get("ai_description"):
        desc = vision["ai_description"].replace("\n", " ")[:200]
        lines.append(f"  AI看圖: {desc}")

    # 新聞警報
    if news:
        n_summary = []
        for n in news[:2]:
            sev = n.get("severity", "")
            sent = n.get("sentiment", "")
            n_summary.append(f"[{sent}/{sev}] {n['title'][:50]}")
        lines.append(f"  新聞: {' | '.join(n_summary)}")

    return "\n".join(lines)


def ai_final_verdict_batch(stocks: list[dict]) -> list[dict]:
    """一次給所有股票 AI 最終建議。

    每個 stock dict 可額外帶 "source" 欄位：
      "holding"      → 是使用者的持股（會給 賣出/減碼 建議）
      "recommend"    → 系統推薦候選（不給 賣出建議，只給 買進/持有）

    回傳：[{idx, code, name, source, verdict, confidence, reason, entry, stop, target, horizon}, ...]
    """
    if not SDK_AVAILABLE or not stocks:
        return []

    contexts = []
    for i, s in enumerate(stocks):
        src = s.get("source", "holding")
        src_label = "【持股中】" if src == "holding" else "【未持有/候選】"
        contexts.append(f"=== #{i+1} {src_label} ===\n{_compact_stock_context(s)}")
    all_context = "\n\n".join(contexts)

    prompt = f"""你是專業股市分析師（**只做多，不做空**）。請看以下 {len(stocks)} 檔股票，為每檔給明確建議。

{all_context}

**重要規則**：
- 此系統純做多，**不做空**
- 標記【持股中】的：可建議「強力買進(加碼) / 買進(加碼) / 持有觀望 / 減碼 / 賣出 / 強力賣出」
- 標記【未持有/候選】的：只可建議「強力買進 / 買進 / 持有觀望 / 暫不進場」
  （絕對不要對未持有的股票建議「賣出/減碼/強力賣出」——你不能賣沒有的東西）

評斷準則：
- verdict: 上述允許值之一
- confidence: 1-10
- reason: 一句話（30 字內）關鍵理由
- entry: 建議進場價（具體數字；若是「賣出」可填 null）
- stop: 停損價（買進類必填；賣出類填 null）
- target: 目標價（買進類必填；賣出類填 null）
- horizon: "短線"|"中線"|"長線"

判斷思路：
- 技術面 + TradingView 評等 + 基本面 + 新聞綜合判斷
- 重大利空新聞 → 持股建議減碼/賣出，候選股建議「暫不進場」
- TradingView 強力買進 + 技術突破 → 可強力買進
- 多空矛盾 → 持有觀望 / 暫不進場

回 JSON array（不要其他文字、不要 markdown 框）：
[
  {{"idx": 1, "verdict": "...", "confidence": 數字, "reason": "...", "entry": 數字或null, "stop": 數字或null, "target": 數字或null, "horizon": "..."}},
  ...
]"""

    print(f"[AI Verdict] 發送 {len(stocks)} 檔股票給 Sonnet 綜合判讀 ...")
    response = ask_claude(prompt, model="claude-sonnet-4-6")
    parsed = _extract_json(response)
    if not isinstance(parsed, list):
        print(f"[AI Verdict] 解析失敗，回應：{response[:200]}")
        return []

    results = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("idx", 0)
        if 1 <= idx <= len(stocks):
            s = stocks[idx - 1]
            results.append({
                "code": s["code"], "name": s["name"], "market": s.get("market", "TW"),
                "source": s.get("source", "holding"),
                "verdict": item.get("verdict", "—"),
                "confidence": int(item.get("confidence", 5)),
                "reason": item.get("reason", "—"),
                "entry": item.get("entry"),
                "stop": item.get("stop"),
                "target": item.get("target"),
                "horizon": item.get("horizon", "—"),
                "current_price": (s.get("kline") or {}).get("close"),
            })
    return results


# ============================================================
# 自我測試
# ============================================================
if __name__ == "__main__":
    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print(f"SDK 可用：{SDK_AVAILABLE}")
    if not SDK_AVAILABLE:
        print("請先 pip install claude-agent-sdk")
        sys.exit(0)

    print("\n=== 測試新聞批次判斷 ===")
    test_news = [
        {"title": "NVIDIA reports record Q3 earnings, beats estimates", "publisher": "Reuters", "link": ""},
        {"title": "Nvidia stock plunges as China bans AI chip exports", "publisher": "Bloomberg", "link": ""},
        {"title": "NVIDIA CEO discusses AI roadmap at conference", "publisher": "CNBC", "link": ""},
    ]
    results = judge_news_batch("NVDA", "NVIDIA", "US", test_news)
    for r in results:
        print(f"\n  📰 {r['title'][:60]}")
        print(f"     {r['sentiment']} ｜ 嚴重度 {r['severity']}/10 ｜ {r['action']}")
        print(f"     💡 {r['reason']}")
