# -*- coding: utf-8 -*-
"""
K 棒型態識別

不依賴 TA-Lib（Windows 編譯麻煩），純 Python 實作 16 種常見型態：

單根：
- 錘子 / 倒鎚 / 上吊線 / 流星
- 十字星 / 墓碑十字 / 蜻蜓十字
- 長紅 / 長黑

雙根：
- 多頭吞噬 / 空頭吞噬
- 烏雲罩頂 / 旭日東升

三根：
- 晨星 / 黃昏星
- 紅三兵 / 三隻烏鴉
"""

from __future__ import annotations
from typing import Optional


def _body(o: float, c: float) -> float:
    return abs(c - o)


def _full(h: float, l: float) -> float:
    return max(h - l, 0.0001)


def _upper_wick(o: float, c: float, h: float) -> float:
    return h - max(o, c)


def _lower_wick(o: float, c: float, l: float) -> float:
    return min(o, c) - l


def _is_bullish(o: float, c: float) -> bool:
    return c > o


def _is_bearish(o: float, c: float) -> bool:
    return c < o


# ============================================================
# 型態偵測函式（回傳 True/False）
# ============================================================
def is_doji(o, h, l, c, threshold=0.1):
    """十字星：實體很小"""
    return _body(o, c) / _full(h, l) < threshold


def is_dragonfly_doji(o, h, l, c):
    """蜻蜓十字：實體小、長下影、無上影 → 多頭轉折"""
    return (_body(o, c) / _full(h, l) < 0.1
            and _lower_wick(o, c, l) > _body(o, c) * 3
            and _upper_wick(o, c, h) < _body(o, c) * 0.5)


def is_gravestone_doji(o, h, l, c):
    """墓碑十字：實體小、長上影、無下影 → 空頭轉折"""
    return (_body(o, c) / _full(h, l) < 0.1
            and _upper_wick(o, c, h) > _body(o, c) * 3
            and _lower_wick(o, c, l) < _body(o, c) * 0.5)


def is_hammer(o, h, l, c):
    """錘子：實體小、長下影、無上影 → 底部反轉訊號"""
    body = _body(o, c)
    full = _full(h, l)
    return (body / full < 0.35
            and _lower_wick(o, c, l) > body * 2
            and _upper_wick(o, c, h) < body * 0.5)


def is_inverted_hammer(o, h, l, c):
    """倒鎚：實體小、長上影、無下影 → 底部反轉訊號"""
    body = _body(o, c)
    full = _full(h, l)
    return (body / full < 0.35
            and _upper_wick(o, c, h) > body * 2
            and _lower_wick(o, c, l) < body * 0.5)


def is_shooting_star(o, h, l, c):
    """流星：實體小、長上影、出現在漲勢頂部 → 賣壓訊號"""
    return is_inverted_hammer(o, h, l, c) and _is_bearish(o, c)


def is_hanging_man(o, h, l, c):
    """上吊線：跟錘子同形但出現在頂部"""
    return is_hammer(o, h, l, c) and _is_bearish(o, c)


def is_long_bull(o, h, l, c, threshold=0.7):
    """長紅 K：實體佔全幅 70% 以上的紅 K"""
    return _is_bullish(o, c) and _body(o, c) / _full(h, l) > threshold


def is_long_bear(o, h, l, c, threshold=0.7):
    """長黑 K"""
    return _is_bearish(o, c) and _body(o, c) / _full(h, l) > threshold


# ---- 雙根 ----
def is_bullish_engulfing(o1, c1, o2, c2):
    """多頭吞噬：昨日黑、今日紅，且今日紅 K 完全吞噬昨日"""
    return (_is_bearish(o1, c1) and _is_bullish(o2, c2)
            and o2 < c1 and c2 > o1)


def is_bearish_engulfing(o1, c1, o2, c2):
    """空頭吞噬"""
    return (_is_bullish(o1, c1) and _is_bearish(o2, c2)
            and o2 > c1 and c2 < o1)


def is_dark_cloud(o1, c1, o2, c2):
    """烏雲罩頂：昨日強紅，今日跳空高開後收很低"""
    mid_yesterday = (o1 + c1) / 2
    return (_is_bullish(o1, c1) and _is_bearish(o2, c2)
            and o2 > c1 and c2 < mid_yesterday and c2 > o1)


def is_piercing(o1, c1, o2, c2):
    """旭日東升：昨日強黑，今日跳空低開後收很高"""
    mid_yesterday = (o1 + c1) / 2
    return (_is_bearish(o1, c1) and _is_bullish(o2, c2)
            and o2 < c1 and c2 > mid_yesterday and c2 < o1)


# ---- 三根 ----
def is_morning_star(o1, c1, o2, h2, l2, c2, o3, c3):
    """晨星：黑K + 小實體 + 紅K → 底部轉折"""
    return (_is_bearish(o1, c1)
            and _body(o2, c2) / _full(h2, l2) < 0.3
            and _is_bullish(o3, c3)
            and c3 > (o1 + c1) / 2)


def is_evening_star(o1, c1, o2, h2, l2, c2, o3, c3):
    """黃昏星：紅K + 小實體 + 黑K → 頂部轉折"""
    return (_is_bullish(o1, c1)
            and _body(o2, c2) / _full(h2, l2) < 0.3
            and _is_bearish(o3, c3)
            and c3 < (o1 + c1) / 2)


def is_three_white_soldiers(o1, c1, o2, c2, o3, c3):
    """紅三兵：連 3 根紅 K 且越來越高"""
    return (_is_bullish(o1, c1) and _is_bullish(o2, c2) and _is_bullish(o3, c3)
            and c1 < c2 < c3
            and o2 > o1 and o3 > o2)


def is_three_black_crows(o1, c1, o2, c2, o3, c3):
    """三隻烏鴉：連 3 根黑 K 且越來越低"""
    return (_is_bearish(o1, c1) and _is_bearish(o2, c2) and _is_bearish(o3, c3)
            and c1 > c2 > c3
            and o2 < o1 and o3 < o2)


# ============================================================
# 主入口
# ============================================================
def detect_patterns(opens, highs, lows, closes) -> dict:
    """偵測最近幾根 K 棒型態。

    回傳：
    {
      "bullish": ["錘子", "多頭吞噬", ...],  # 看漲型態
      "bearish": ["流星", "晨星" 反向 ...],   # 看跌型態
      "neutral": ["十字星"],
      "score": int  # 多頭計分（正越多越多頭）
    }
    """
    if len(closes) < 3:
        return {"bullish": [], "bearish": [], "neutral": [], "score": 0}

    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    o1, h1, l1, c1 = opens[-2], highs[-2], lows[-2], closes[-2]
    o2, h2, l2, c2 = opens[-3], highs[-3], lows[-3], closes[-3]

    bullish, bearish, neutral = [], [], []

    # 單根
    if is_dragonfly_doji(o, h, l, c):
        bullish.append("蜻蜓十字（多頭轉折）")
    elif is_gravestone_doji(o, h, l, c):
        bearish.append("墓碑十字（頂部訊號）")
    elif is_doji(o, h, l, c):
        neutral.append("十字星（猶豫）")

    if is_hammer(o, h, l, c):
        if _is_bullish(o, c):
            bullish.append("錘子（買盤進場）")
        else:
            bearish.append("上吊線（多頭失守）")

    if is_inverted_hammer(o, h, l, c):
        if _is_bullish(o, c):
            bullish.append("倒鎚（低檔翻揚）")
        else:
            bearish.append("流星（高檔賣壓）")

    if is_long_bull(o, h, l, c):
        bullish.append("長紅 K（強勢）")
    if is_long_bear(o, h, l, c):
        bearish.append("長黑 K（弱勢）")

    # 雙根
    if is_bullish_engulfing(o1, c1, o, c):
        bullish.append("多頭吞噬（強反轉）")
    if is_bearish_engulfing(o1, c1, o, c):
        bearish.append("空頭吞噬（強反轉）")
    if is_dark_cloud(o1, c1, o, c):
        bearish.append("烏雲罩頂")
    if is_piercing(o1, c1, o, c):
        bullish.append("旭日東升")

    # 三根
    if is_morning_star(o2, c2, o1, h1, l1, c1, o, c):
        bullish.append("晨星（底部反轉）")
    if is_evening_star(o2, c2, o1, h1, l1, c1, o, c):
        bearish.append("黃昏星（頂部反轉）")
    if is_three_white_soldiers(o2, c2, o1, c1, o, c):
        bullish.append("紅三兵（連續上攻）")
    if is_three_black_crows(o2, c2, o1, c1, o, c):
        bearish.append("三隻烏鴉（連續下殺）")

    score = len(bullish) * 2 - len(bearish) * 2
    return {
        "bullish": bullish,
        "bearish": bearish,
        "neutral": neutral,
        "score": score,
    }
