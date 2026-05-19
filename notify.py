# -*- coding: utf-8 -*-
"""
推播模組 — 目前支援 Telegram

設定方式：
1. 複製 credentials_example.py → credentials.py
2. 填入 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID

取得方法見 credentials_example.py 註解。
"""

from __future__ import annotations

import os
from typing import Optional

import requests


def _load_credentials() -> tuple[Optional[str], Optional[str]]:
    """從 credentials.py 或環境變數取 Telegram 金鑰。"""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if token and chat_id:
        return token, chat_id

    try:
        import credentials as cred
        token = getattr(cred, "TELEGRAM_BOT_TOKEN", "") or token
        chat_id = getattr(cred, "TELEGRAM_CHAT_ID", "") or chat_id
    except ImportError:
        pass

    return (token or None), (chat_id or None)


def telegram_push(message: str, parse_mode: str = "Markdown") -> bool:
    """送一段文字到 Telegram。回傳是否成功。"""
    token, chat_id = _load_credentials()
    if not token or not chat_id:
        print("[Notify] 未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，跳過")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            return True
        # Markdown 解析失敗就用純文字重試
        if "can't parse entities" in resp.text.lower():
            resp = requests.post(url, json={
                "chat_id": chat_id, "text": message,
                "disable_web_page_preview": True,
            }, timeout=15)
            return resp.status_code == 200
        print(f"[Notify] Telegram 失敗 {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"[Notify] Telegram 例外：{e}")
        return False


def telegram_alert(title: str, body: str, urgency: str = "high") -> bool:
    """格式化警報。urgency: high/medium/low"""
    icons = {"high": "🚨🚨🚨", "medium": "⚠️", "low": "ℹ️"}
    icon = icons.get(urgency, "ℹ️")
    return telegram_push(f"{icon} *{title}*\n\n{body}", parse_mode="Markdown")


def test_connection() -> bool:
    """測試連線。"""
    token, chat_id = _load_credentials()
    if not token or not chat_id:
        print("❌ 未設定金鑰")
        return False

    print("📤 發送測試訊息 ...")
    ok = telegram_push(
        "✅ *盤中監控設定成功*\n\n"
        "從現在開始你會收到：\n"
        "• 持股觸發停損 / 目標\n"
        "• 爆量、急漲、急跌警示\n"
        "• 重大新聞\n\n"
        "祝你交易順利 💰",
        parse_mode="Markdown",
    )
    print("✅ 已發送，去 Telegram 確認" if ok else "❌ 發送失敗")
    return ok


if __name__ == "__main__":
    import sys, io
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    test_connection()
