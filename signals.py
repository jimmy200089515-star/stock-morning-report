# -*- coding: utf-8 -*-
"""
交易訊號模組 — 把策略決策轉成可執行訊號

TradeSignal 是介於「策略」與「下單」中間的標準化資料結構：
- mailer.py 用來渲染「今日該買/該賣」清單
- executor.py 用來呼叫券商 API 實際下單
- paper_trader.py 內部記錄用

設計目標：一個 TradeSignal 拿到券商 App 上就能照單下，不用再想參數。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class TradeSignal:
    # ---- 基本資訊 ----
    action: str                 # "BUY" / "SELL" / "SHORT" / "COVER"
    code: str
    name: str
    market: str                 # "TW" / "US"

    # ---- 數量與價格 ----
    quantity: int               # 股數
    suggested_price: float      # 建議價格（限價單用）
    order_type: str = "LIMIT"   # "MARKET" / "LIMIT"

    # ---- 技術出場條件（進場訊號才有）----
    exit_break_ma_label: Optional[str] = None       # 例 "跌破 MA20"
    exit_break_ma_price: Optional[float] = None     # 當前 MA20 價位
    exit_break_swing_label: Optional[str] = None    # 例 "跌破前 5 日低"
    exit_break_swing_price: Optional[float] = None  # 5 日低點價
    catastrophic_stop_price: Optional[float] = None # 極端停損保護價
    expected_return_pct: Optional[float] = None     # 預期報酬

    # ---- 上下文 ----
    reason: str = ""            # 簡短理由（1-2 句）
    confidence: int = 0         # 1-10
    direction: str = "long"     # "long" / "short"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def estimated_cost(self) -> float:
        return self.quantity * self.suggested_price * 1.001425


# ============================================================
# 從 paper_trader 的 actions 轉出 TradeSignal 列表
# ============================================================
def signals_from_paper_actions(actions: dict, strategy_cfg,
                                today_signals: dict | None = None) -> list[TradeSignal]:
    """把 paper_trader.step_one_day() 的 actions 拆成標準 TradeSignal 物件。

    today_signals: 今日各股的 score_at 結果（含 ma20/low_5 等技術位）
    """
    signals: list[TradeSignal] = []
    today_signals = today_signals or {}

    # ---- 進場訊號 ----
    for b in actions.get("buys", []):
        direction = b.get("direction", "long")
        action = "BUY" if direction == "long" else "SHORT"

        # 從今日訊號取技術位
        tech = today_signals.get(b["code"], {})

        if direction == "long":
            ma_key = strategy_cfg.long_exit_break_ma
            swing_key = f"low_{strategy_cfg.long_exit_break_swing}"
            ma_label = f"跌破 {ma_key.upper()} 出場"
            swing_label = f"跌破前 {strategy_cfg.long_exit_break_swing} 日低出場"
            catastrophic = round(b["price"] * (1 + strategy_cfg.catastrophic_stop), 2)
        else:
            ma_key = strategy_cfg.short_exit_break_ma
            swing_key = f"high_{strategy_cfg.short_exit_break_swing}"
            ma_label = f"站上 {ma_key.upper()} 出場"
            swing_label = f"突破前 {strategy_cfg.short_exit_break_swing} 日高出場"
            catastrophic = round(b["price"] * (1 + strategy_cfg.short_catastrophic_stop), 2)

        ma_price = tech.get(ma_key)
        swing_price = tech.get(swing_key)

        sig = TradeSignal(
            action=action,
            code=b["code"],
            name=b["name"],
            market="TW" if b["code"].isdigit() else "US",
            quantity=b["shares"],
            suggested_price=round(b["price"], 2),
            order_type="LIMIT",
            exit_break_ma_label=ma_label,
            exit_break_ma_price=round(ma_price, 2) if ma_price else None,
            exit_break_swing_label=swing_label,
            exit_break_swing_price=round(swing_price, 2) if swing_price else None,
            catastrophic_stop_price=catastrophic,
            reason=f"技術分數 {b['score']:+d}，符合進場條件",
            confidence=min(10, max(1, b["score"])),
            direction=direction,
        )
        signals.append(sig)

    # ---- 出場訊號 ----
    for s in actions.get("sells", []):
        direction = s.get("direction", "long")
        action = "SELL" if direction == "long" else "COVER"
        sig = TradeSignal(
            action=action,
            code=s["code"],
            name=s["name"],
            market="TW" if s["code"].isdigit() else "US",
            quantity=s["shares"],
            suggested_price=round(s["exit_price"], 2),
            order_type="LIMIT",
            reason=s["reason"],
            direction=direction,
        )
        signals.append(sig)

    return signals


# ============================================================
# 序列化（給 executor 用）
# ============================================================
def signals_to_json(signals: list[TradeSignal]) -> list[dict]:
    return [s.to_dict() for s in signals]
