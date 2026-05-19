# -*- coding: utf-8 -*-
"""
K 線圖 AI 視覺解讀

流程：
1. yfinance 抓最近 60 天 OHLC
2. mplfinance 畫 K 線 + 均線 + 量
3. 存成 PNG
4. 用 Claude Agent SDK，提供圖片路徑，請 Claude 視覺判讀型態

需要：
    pip install mplfinance

Claude 會「看」圖（透過 Read 工具讀圖片），描述：
- 整體趨勢（多/空/盤整）
- 圖形型態（W底、頭肩底、三角收斂、楔形...）
- 關鍵價位與訊號
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from typing import Optional

try:
    import mplfinance as mpf
    import matplotlib
    import pandas as pd
    # 設定中文字型避免亂碼
    matplotlib.rcParams['font.sans-serif'] = [
        'Microsoft JhengHei', 'Microsoft YaHei', 'SimHei', 'Arial'
    ]
    matplotlib.rcParams['axes.unicode_minus'] = False
    MPF_AVAILABLE = True
except ImportError:
    MPF_AVAILABLE = False
    mpf = None
    pd = None

import yfinance as yf

from fetcher import _resolve_yf_ticker
from ai_judge import ask_claude, SDK_AVAILABLE as AI_AVAILABLE

logging.getLogger("yfinance").setLevel(logging.CRITICAL)


CHART_DIR = os.path.join(tempfile.gettempdir(), "morning_report_charts")
os.makedirs(CHART_DIR, exist_ok=True)


# ============================================================
# 畫 K 線圖
# ============================================================
def draw_chart(code: str, market: str = "TW", days: int = 60) -> Optional[str]:
    """畫最近 N 天 K 線圖（含均線+成交量），存到暫存資料夾，回傳路徑。"""
    if not MPF_AVAILABLE:
        return None
    try:
        ticker = code if market == "US" else _resolve_yf_ticker(code)
        if not ticker:
            return None

        hist = yf.Ticker(ticker).history(period=f"{days + 30}d")
        if hist.empty or len(hist) < 30:
            return None

        # 取最後 N 天
        hist = hist.tail(days)

        # mplfinance 需要欄位名 Open/High/Low/Close/Volume
        # yfinance 回的 DataFrame 本來就是這些名稱

        path = os.path.join(CHART_DIR, f"{ticker.replace('.', '_')}_{datetime.now():%Y%m%d}.png")

        # 樣式：美國風（紅漲綠跌）→ 改成台股風（紅漲綠跌已是預設）
        mc = mpf.make_marketcolors(
            up="#d32f2f", down="#2e7d32",
            edge="inherit", wick="inherit", volume="inherit",
        )
        style = mpf.make_mpf_style(marketcolors=mc, gridstyle="--", y_on_right=True)

        mpf.plot(
            hist,
            type="candle",
            mav=(5, 20, 60),         # 5/20/60 日均線
            volume=True,
            style=style,
            title=f"{ticker} (近 {days} 日)",
            ylabel="價格",
            ylabel_lower="量",
            figsize=(12, 7),
            savefig=dict(fname=path, dpi=100, bbox_inches="tight"),
        )
        return path
    except Exception as e:
        print(f"[draw_chart] {code} 失敗：{e}")
        return None


# ============================================================
# Claude 看圖 → 描述型態
# ============================================================
VISION_PROMPT_TEMPLATE = """請看這張**日線 K 線圖**（檔案路徑：{path}），圖中包含 60 個交易日資料、5/20/60 日均線、下方為成交量。

請用繁體中文簡短描述（5 點，每點一行 30 字內）：

1. **整體趨勢**：上升 / 下降 / 盤整？多空排列如何？
2. **型態判讀**：W 底、頭肩底/頂、三角收斂、楔形、突破/跌破？無明顯型態就寫「無明顯型態」
3. **關鍵價位**：上方壓力數字、下方支撐數字
4. **量價關係**：量價齊揚 / 量縮反彈 / 爆量出貨？
5. **短線建議**：偏多 / 偏空 / 觀望，附 1 句具體理由

要求：
- 直接寫結論不要客套
- 注意：這是「**日線**」不是分鐘線
- 數字要具體
- 不要再說「以下是分析」前綴

請使用 Read 工具讀取圖片後直接回應 5 點分析。"""


def analyze_chart_with_ai(chart_path: str) -> str:
    """請 Claude 看圖描述。"""
    if not AI_AVAILABLE or not chart_path or not os.path.exists(chart_path):
        return ""
    prompt = VISION_PROMPT_TEMPLATE.format(path=chart_path)
    # 視覺分析要 Sonnet 才看得懂圖
    return ask_claude(prompt, model="claude-sonnet-4-6")


# ============================================================
# 一站式：抓資料 → 畫圖 → AI 看圖
# ============================================================
def chart_vision_for(code: str, name: str, market: str = "TW",
                     days: int = 60) -> dict:
    """完整流程，回傳 {chart_path, ai_description}。"""
    if not MPF_AVAILABLE or not AI_AVAILABLE:
        return {"chart_path": None, "ai_description": "", "available": False}

    path = draw_chart(code, market, days)
    if not path:
        return {"chart_path": None, "ai_description": "", "available": False}

    print(f"  [Vision] {code} {name}: 已畫圖 → AI 解讀 ...")
    desc = analyze_chart_with_ai(path)
    return {
        "chart_path": path,
        "ai_description": desc,
        "available": True,
    }


if __name__ == "__main__":
    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print(f"mplfinance: {MPF_AVAILABLE} ｜ Claude SDK: {AI_AVAILABLE}")
    result = chart_vision_for("2330", "台積電", "TW")
    print(f"圖檔：{result['chart_path']}")
    print(f"\nAI 描述：\n{result['ai_description']}")
