# -*- coding: utf-8 -*-
"""
交易執行器（Executor）

把 TradeSignal 列表丟給適合的 Broker 實際下單。

預設行為（無 credentials.py）：
- **dry_run** 模式：只 print 訊號，不下任何單

設定 credentials.py 後：
- TW 訊號 → ShioajiBroker（預設先連模擬環境）
- US 訊號 → AlpacaBroker（預設先連 Paper Trading）

切換到實盤：在 credentials.py 改 SHIOAJI_SIMULATION=False / ALPACA_PAPER=False
"""

from __future__ import annotations

import json
import os
from datetime import datetime


EXECUTION_LOG = "execution_log.json"


# ============================================================
# 主入口
# ============================================================
def execute_signals(signals: list,
                    force_dry_run: bool = False) -> dict:
    """執行訊號清單。回傳執行報告 dict。

    signals: list of dict（TradeSignal.to_dict() 結果）
    force_dry_run: 強制 dry_run（即使有 credentials.py 也不下實單）
    """
    if not signals:
        return {"status": "no_signals", "executed": 0}

    # 嘗試載入 credentials
    try:
        import credentials as cred
        has_cred = True
    except ImportError:
        cred = None
        has_cred = False

    dry_run = force_dry_run or not has_cred
    if dry_run:
        return _dry_run_signals(signals)

    # 真實下單模式
    tw_signals = [s for s in signals if s["market"] == "TW"]
    us_signals = [s for s in signals if s["market"] == "US"]

    report = {
        "started_at": datetime.now().isoformat(),
        "executed_tw": [],
        "executed_us": [],
        "skipped": [],
    }

    # TW 用 Shioaji
    if tw_signals:
        report["executed_tw"] = _execute_tw(tw_signals, cred)

    # US 用 Alpaca
    if us_signals:
        report["executed_us"] = _execute_us(us_signals, cred)

    _save_log(report)
    return report


# ============================================================
# Dry run（無 broker 設定時走這個）
# ============================================================
def _dry_run_signals(signals: list) -> dict:
    print("\n" + "=" * 60)
    print("🧪 Executor [DRY RUN] — 未設定 credentials.py，僅顯示訊號")
    print("=" * 60)
    for i, s in enumerate(signals, 1):
        line = (f"  #{i} [{s['action']:5}] {s['market']} {s['code']} {s['name']:<12} "
                f"x{s['quantity']:>5} @ {s['suggested_price']:.2f}")
        if s.get("stop_loss_price"):
            line += f"  SL {s['stop_loss_price']:.2f}"
        if s.get("take_profit_price"):
            line += f"  TP {s['take_profit_price']:.2f}"
        print(line)
    print("=" * 60)
    print("💡 若要實際下單：")
    print("   1. 複製 credentials_example.py → credentials.py")
    print("   2. 填入 Shioaji 或 Alpaca 金鑰")
    print("   3. 再執行一次 main.py")
    print("=" * 60 + "\n")
    return {
        "status": "dry_run",
        "executed": 0,
        "signals_count": len(signals),
    }


# ============================================================
# TW 下單
# ============================================================
def _execute_tw(signals: list, cred) -> list:
    from broker_shioaji import create_from_credentials

    simulation = getattr(cred, "SHIOAJI_SIMULATION", True)
    broker = create_from_credentials(simulation=simulation)
    if broker is None:
        print("[Executor] 無 Shioaji 設定，TW 訊號跳過")
        return []

    results = []
    if not broker.connect():
        return results

    try:
        for s in signals:
            res = broker.place_order(
                code=s["code"],
                side=s["action"],
                quantity=s["quantity"],
                price=s["suggested_price"] if s["order_type"] == "LIMIT" else None,
                order_type=s["order_type"],
            )
            results.append({
                "signal": s,
                "success": res.success,
                "order_id": res.order_id,
                "message": res.message,
            })
    finally:
        broker.disconnect()

    return results


# ============================================================
# US 下單
# ============================================================
def _execute_us(signals: list, cred) -> list:
    from broker_alpaca import create_from_credentials

    paper = getattr(cred, "ALPACA_PAPER", True)
    broker = create_from_credentials(paper=paper)
    if broker is None:
        print("[Executor] 無 Alpaca 設定，US 訊號跳過")
        return []

    results = []
    if not broker.connect():
        return results

    try:
        for s in signals:
            res = broker.place_order(
                code=s["code"],
                side=s["action"],
                quantity=s["quantity"],
                price=s["suggested_price"] if s["order_type"] == "LIMIT" else None,
                order_type=s["order_type"],
            )
            results.append({
                "signal": s,
                "success": res.success,
                "order_id": res.order_id,
                "message": res.message,
            })
    finally:
        broker.disconnect()

    return results


def _save_log(report: dict):
    log = []
    if os.path.exists(EXECUTION_LOG):
        try:
            with open(EXECUTION_LOG, "r", encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            log = []
    log.append(report)
    with open(EXECUTION_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2, default=str)
