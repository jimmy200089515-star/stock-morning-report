# -*- coding: utf-8 -*-
"""
券商金鑰設定範本

使用方式：
1. 複製本檔成 credentials.py（同資料夾）
2. 填入你的 API 金鑰
3. 把 credentials.py 加進 .gitignore（別外洩！）

切記：
- credentials.py 絕對不要 commit 進 git
- 切勿分享給任何人
- API 金鑰外洩 = 你的帳戶可能被任意下單
"""

# =============================================================
# 永豐金 Shioaji（台股）
# =============================================================
# 開戶 + 申請 API：
#   1. 開永豐金證券戶
#   2. 大戶投 App → 設定 → API 申請
#   3. 簽電子下單委託書
#   4. 下載 CA 憑證（.pfx 檔）
#   5. 取得 API_KEY / SECRET_KEY
#
# 文件：https://sinotrade.github.io/

SHIOAJI_API_KEY = ""              # API Key
SHIOAJI_SECRET_KEY = ""           # API Secret
SHIOAJI_PERSON_ID = ""            # 身分證字號（憑證綁定用）
SHIOAJI_CA_PATH = ""              # CA 憑證 .pfx 檔路徑
SHIOAJI_CA_PASSWORD = ""          # CA 憑證密碼
SHIOAJI_SIMULATION = True         # ⚠️ True=模擬環境，False=實盤


# =============================================================
# Alpaca（美股）
# =============================================================
# 註冊 + 取得金鑰：
#   1. https://alpaca.markets 註冊
#   2. Dashboard → API Keys → Generate
#   3. Paper Trading 金鑰是免費虛擬，Live 才需要實際開戶
#
# 文件：https://alpaca.markets/docs/

ALPACA_API_KEY = ""
ALPACA_SECRET_KEY = ""
ALPACA_PAPER = True               # ⚠️ True=模擬交易，False=實盤


# =============================================================
# Telegram 推播（盤中警報用）
# =============================================================
# 取得步驟（5 分鐘）：
#   1. 手機開 Telegram，搜尋 @BotFather → 開始對話 → 傳 /newbot
#   2. 給 bot 取名 + username（要 _bot 結尾，例如 jimmy_stock_bot）
#   3. BotFather 會回一個 Token（很長字串），複製到下面 TELEGRAM_BOT_TOKEN
#   4. 搜尋你剛建的 bot → 點開 → 按 START / 傳 /start
#   5. 開瀏覽器訪問（換 <TOKEN> 為上面的 token）：
#      https://api.telegram.org/bot<TOKEN>/getUpdates
#   6. 找回應中 "chat":{"id": 123456789} → 那個數字就是你的 chat_id

TELEGRAM_BOT_TOKEN = ""            # 例 "7891234567:AAH..."
TELEGRAM_CHAT_ID = ""              # 例 "123456789"


# =============================================================
# 安全建議
# =============================================================
# 為了多一層保護，可以改用環境變數讀取：
#
# import os
# SHIOAJI_API_KEY = os.getenv("SHIOAJI_API_KEY", "")
#
# 然後在 Windows 設定環境變數，程式碼裡就不會留下金鑰
