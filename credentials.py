# -*- coding: utf-8 -*-
"""
個人金鑰設定（請勿外流）
"""

# =============================================================
# 永豐金 Shioaji（台股）— 尚未啟用
# =============================================================
SHIOAJI_API_KEY = ""
SHIOAJI_SECRET_KEY = ""
SHIOAJI_PERSON_ID = ""
SHIOAJI_CA_PATH = ""
SHIOAJI_CA_PASSWORD = ""
SHIOAJI_SIMULATION = True


# =============================================================
# Alpaca（美股）— 尚未啟用
# =============================================================
ALPACA_API_KEY = ""
ALPACA_SECRET_KEY = ""
ALPACA_PAPER = True


# =============================================================
# Telegram 推播 ✅ 已啟用
# =============================================================
def _get_telegram_creds():
    try:
        from secrets_local import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        return TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    except ImportError:
        return "", ""

TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID = _get_telegram_creds()
