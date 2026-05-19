# 📋 台股早報系統 — 交接文件

> 一套**個人化、AI 加持**的台股 + 美股自動分析與通知系統
> 持有人：jimmy200089515@gmail.com
> 主路徑：`C:\Users\88696\Desktop\stock\morning_report\`

---

## 🎯 系統能做什麼

### 1. 每日 08:00 自動寄出早報 Email
包含：
- 🚨 持股新聞警報（AI 判斷嚴重度）
- 📌 持股 AI 建議（買/賣/持有 + 停損目標）
- 🚀 系統推薦（從全市場掃描挑出的新進場機會）
- 🧠 AI 大盤摘要（一段話講今日盤勢）
- 📊 大盤指數、🔄 族群輪動
- 🤖 紙上交易帳戶結算

### 2. 盤中監控（Telegram 推播）
- 平日 09:00-13:35 每 5 分鐘檢查台股
- 每天 22:00-05:00 每 10 分鐘檢查美股
- 觸發即推播：跌破 MA20 / 急跌 / 急漲 / 爆量 / 紙上倉位虧損

### 3. 紙上交易機器人
- 起始本金 NT$200,000
- 用同一套技術策略每日自動進出
- 持久化記錄到 `paper_portfolio.json`

---

## 📂 完整檔案結構

```
morning_report/
├── 設定
│   ├── config.py              ★ 持股清單、產業分類、Email 設定
│   ├── credentials.py         ★ API 金鑰（Telegram、券商）
│   ├── credentials_example.py    範本
│   └── requirements.txt          套件清單
│
├── 主程式
│   ├── main.py                ★ 每日早報主流程（08:00 自動跑）
│   ├── intraday_monitor.py    ★ 盤中監控（每 5/10 分鐘跑）
│   └── paper_trader.py           紙上交易機器人
│
├── 資料抓取
│   └── fetcher.py                K 線、技術指標、籌碼、基本面
│
├── 技術分析
│   ├── pattern_detector.py       K 棒型態識別（錘子、吞噬、晨星...）
│   ├── ta_external.py            TradingView 26 指標評等抓取
│   └── chart_vision.py           畫 K 線圖 + Claude 看圖描述
│
├── AI 整合
│   ├── ai_judge.py            ★ Claude Code SDK 整合（新聞/摘要/決策）
│   └── signals.py                交易訊號標準化
│
├── 警報 / 通知
│   ├── news_alert.py             新聞掃描（AI 判讀嚴重度）
│   └── notify.py                 Telegram 推播
│
├── 寄信
│   └── mailer.py                 HTML email 組裝 + SMTP 寄送
│
├── 策略回測
│   ├── backtest.py               回測引擎（含寬鬆/嚴謹兩版）
│   ├── backtest_periods.py       多期間比較（1/3/6 月）
│   ├── compare_execution.py      寬鬆 vs 嚴謹偏差量化
│   └── optimizer.py              參數網格搜尋
│
├── 券商接線（未啟用，骨架已就緒）
│   ├── broker_base.py            抽象介面
│   ├── broker_shioaji.py         永豐金（台股）
│   ├── broker_alpaca.py          Alpaca（美股）
│   └── executor.py               訊號 → 券商橋接
│
├── 排程器
│   ├── setup_task.bat            早報排程一鍵設定（喚醒電腦）
│   └── setup_intraday_task.bat   盤中排程一鍵設定
│
├── 狀態檔（自動產生）
│   ├── paper_portfolio.json      紙上帳戶
│   ├── intraday_alert_state.json 當日警報去重
│   ├── execution_log.json        下單紀錄
│   ├── backtest_result.json      回測結果
│   └── optimizer_result.json     最佳化結果
│
└── HANDOFF.md                    ★ 你正在看的這份
```

---

## 🚀 已部署的自動排程

| 排程名 | 時間 | 行為 |
|--------|------|------|
| `MorningStockReport` | 每天 08:00 | 跑 main.py，寄 Email |
| `IntradayMonitor_TW` | 平日 09:00-13:35 / 每 5 分鐘 | 監控台股，推 Telegram |
| `IntradayMonitor_US` | 每天 22:00-05:00 / 每 10 分鐘 | 監控美股，推 Telegram |

3 個都設定為**從睡眠喚醒電腦**執行。

管理指令：
```powershell
# 立即測試
schtasks /Run /TN "MorningStockReport"

# 查看狀態
schtasks /Query /TN "MorningStockReport" /V /FO LIST

# 移除排程
schtasks /Delete /TN "MorningStockReport" /F
```

---

## 🧠 交易策略（技術分析）

### 進場規則（純規則計分）
> 完整邏輯在 `backtest.py` 的 `score_at()` 函式

11 個維度加減分，總分 `bull - bear`：

| 維度 | 加分情境 | 扣分情境 |
|------|----------|----------|
| 均線排列 | 多頭排列 +3 | 空頭排列 -3 |
| 突破 20 日高 | +5（權重最大）| - |
| 突破 60 日高 | +3 | - |
| 站上月線 | +2 | 跌破月線 -2 |
| 量價齊揚 | +3 | 量增價跌 -3 |
| KD 黃金交叉 | +1 | 死亡交叉 -1 |
| KD 超賣 | +1 | 超買 -2 |
| RSI | 50-70 +1 | ≥80 超買 -2 |
| MACD 紅柱 | +1 | 綠柱 -1 |
| 漲幅控制 | - | 今日漲 >9% 扣 4 分 |

**進場門檻**：`bull_score - bear_score ≥ 9`（很嚴）

### 出場規則（純技術判斷）
任一觸發即出場：
1. 收盤**跌破 MA20**（趨勢轉弱）
2. 收盤**跌破前 5 日低**（短期支撐失守）
3. **跌幅 ≥ -12%**（極端停損保護）
4. 多空分數 ≤ **-6**（技術翻空）

**注意**：策略**純做多**，熊市抱現金不放空。

### 資金管理
- 本金 NT$200,000
- 最多同時持 5 檔
- 每檔平分 ~20%（`現金 / 空檔位 × 95%`）

---

## 📊 回測績效（6 個月）

| 期間 | 報酬 | 勝率 | 回撤 | 交易 |
|------|------|------|------|------|
| 1 個月 | +15.46% | 62.5% | -3.71% | 8 |
| 3 個月 | +50.96% | 50.0% | -18.49% | 24 |
| **6 個月** | **+103.42%** | 51.3% | -21.63% | 39 |

### ⚠️ 真實預期
回測有「同日收盤決定 + 同日收盤成交」的小作弊。嚴謹版（明日開盤成交）：
- 1 個月: +19.73%
- 3 個月: +30.79%
- 6 個月: +94.23%

**實盤估計**：再扣滑價 / 心理因素 → **+70~80%** 算合理。

### 6 個月戰績亮點
| 標的 | 持有 | 損益 |
|------|------|------|
| 3491 昇達科 | 112 天 | +195% / +$48k |
| 6147 頎邦 | 50 天 | +163% / +$97k |
| 3189 景碩 | 56 天 | +62% / +$23k |

---

## 🤖 AI 整合（Claude Code SDK + Max 訂閱）

**全部免費**（吃 Max 額度，不需要 API Key）。

### 三個 AI 函式
| 函式 | 用 Haiku 還是 Sonnet | 用途 |
|------|----------------------|------|
| `judge_news_batch()` | Haiku | 批次新聞情緒判斷 |
| `daily_market_summary()` | Sonnet | 每日盤勢摘要（口語化）|
| `ai_final_verdict_batch()` | Sonnet | 綜合所有資料給最終決策 |

### AI 決策時看的資料
每檔股票丟給 Claude Sonnet 的內容：
- K 線、均線位置、突破事件
- KD / RSI / MACD 讀數
- K 棒型態（錘子、吞噬、晨星...）
- TradingView 26 指標評等
- AI 看圖描述（W底/旗形/三角...）— 只對持股做
- 基本面（PE / 營收 YoY / EPS YoY）
- 三大法人買賣超
- 重大新聞（含 AI 已判讀的嚴重度）
- 支撐壓力位

→ 一個結論 + 具體價位

### 規則 vs AI 分工
| 層 | 用 | 用途 |
|----|----|------|
| 規則層 | 進出場決策 | 純技術計分，可回測重現 |
| AI 層 | 統整解讀 + 推薦 | 給人話、加上新聞 / 多面向綜合 |

**重要**：回測**不用 AI**，因為 AI 不可重現 + 不能保證沒偷看未來。

---

## 📱 Telegram 推播

### 已設定的 bot
- Bot Token: `8700330723:...`（在 credentials.py）
- Chat ID: `1131459205`

### 觸發條件（盤中監控）
| 條件 | 嚴重度 | 範例 |
|------|--------|------|
| 跌破 MA20 | 🚨🚨🚨 | "中釉 (1809) 跌破 MA20" |
| 單日大跌 < -5% | 🚨🚨🚨 | "全新 (2455) 急跌 -7.5%" |
| 單日大漲 > +5% | ⚠️ | "頎邦 (6147) 急漲 +8.2%" |
| 爆量 > 2.5x | ⚠️ | "智原 (3035) 爆量 3.2x" |
| 紙上倉位虧損 > -10% | 🚨🚨🚨 | "已觸發 -10% 警戒線" |

**去重**：同檔同類型警報當日只送一次。

---

## 🔌 券商整合（未啟用，骨架已備）

### 為什麼還沒接
- 風險高、應該先讓 paper trader 跑 1-2 個月驗證策略
- 接 API 等於把錢交給程式，要先信任

### 接線時要做什麼
**台股（Shioaji 永豐金）**：
1. 開永豐金證券戶
2. 大戶投 App → API 申請
3. 下載 CA 憑證
4. `pip install shioaji`
5. 編輯 `credentials.py` 填入金鑰
6. 預設 `SHIOAJI_SIMULATION = True` 跑模擬，確認後改 False

**美股（Alpaca）**：
1. https://alpaca.markets 註冊
2. 取 API Key
3. `pip install alpaca-py`
4. 編輯 `credentials.py`
5. 預設 `ALPACA_PAPER = True` 用 Paper Trading，確認後改 False

### 已有的橋接
`executor.py` 會把訊號送給對應 broker，預設 **dry_run**（沒 credentials 就只 print，不下單）。

---

## 🔧 日常維護

### 修改持股
編輯 `config.py` 的 `HOLDINGS_TW` 和 `HOLDINGS_US`。

### 改交易策略參數
編輯 `paper_trader.py` 的 `DEFAULT_STRATEGY`：
```python
DEFAULT_STRATEGY = StrategyConfig(
    long_entry_threshold=9,           # 進場分數門檻
    long_exit_break_ma="ma20",        # 跌破哪條均線出場
    catastrophic_stop=-0.12,          # 極端虧損保護
    max_positions=5,                  # 最多持幾檔
    ...
)
```

### 改 Email 收件人
編輯 `config.py` 的 `EMAIL["to"]`。

### 改盤中監控觸發條件
編輯 `intraday_monitor.py` 的 `check_position()` 函式。

### 重新跑回測
```
python backtest.py                  # 單一策略
python backtest_periods.py          # 1/3/6 月對比
python compare_execution.py         # 寬鬆 vs 嚴謹偏差
python optimizer.py                 # 參數網格搜尋
```

### 重置紙上交易帳戶
```
del paper_portfolio.json
del intraday_alert_state.json
```
下次跑會自動建新的空帳戶（NT$200,000）。

---

## 🛠️ 常見問題排除

### Email 寄不出
- 檢查 `config.py` 的 `EMAIL["password"]` 是 Gmail App Password（不是登入密碼）
- App Password 需 Google 帳號開啟兩步驟驗證後申請

### Telegram 沒推播
```
python notify.py
```
測試是否能發訊息。若失敗：
- 確認 `credentials.py` 的 token 和 chat_id 正確
- 確認你有對 bot 傳過 `/start`

### 排程沒跑
```
schtasks /Query /TN "MorningStockReport" /V /FO LIST
```
查看狀態。Last Run Result 應該是 0（成功）。

### Claude AI 失敗
- 確認 Claude Code 還在本機正常運作
- 確認 Max 訂閱沒過期 / 沒被限流
- 失敗時系統會 fallback 到關鍵字判斷（不會崩）

### yfinance 抓不到某檔
- 上櫃股票要用 `.TWO` 後綴（系統已自動處理）
- `_resolve_yf_ticker()` 會嘗試 `.TW` → `.TWO` → 找不到回 None

---

## ⚠️ 系統限制與風險

### 資料限制
- yfinance 美股延遲 15 分鐘（免費限制）
- 台股大致即時
- 新聞來源主要是 yfinance（台股新聞覆蓋差）

### 策略限制
- 回測有「同日收盤」偏差（實盤略低）
- 回測沒含倖存者偏差（下市股不在池內）
- 純做多策略 → 熊市抱現金不賺空頭錢
- 黑天鵝（戰爭、央行突發）無法預測

### AI 限制
- AI 偶爾胡言亂語（給荒謬建議）
- 同樣輸入可能回不同答案
- 不能完全信任 → 仍須人工確認

### 排程限制
- 必須「睡眠」不能「完全關機」才能喚醒
- 筆電閤蓋若設「休眠/關機」也不行
- 網路斷的話會跳過該次

---

## 📅 開發歷程摘要

| 階段 | 內容 |
|------|------|
| 1 | 基礎早報 — 大盤指數 + 持股技術分析 |
| 2 | 完整 K 線分析 — 多空判讀、突破壓力支撐 |
| 3 | 加入族群輪動偵測、業績成長/PE/短線推薦三類總覽 |
| 4 | 全市場掃描推薦（從 TWSE OpenAPI 抓 1000+ 檔篩選）|
| 5 | 回測機器人 — 6 個月模擬 +103% |
| 6 | 參數最佳化（54 組組合 grid search）|
| 7 | 做空 + 槓桿支援（後來改純做多）|
| 8 | 紙上交易機器人（每日自動進出）|
| 9 | 訊號標準化 + 券商接線骨架（Shioaji / Alpaca）|
| 10 | Claude Code SDK 整合 — AI 新聞判讀 + 摘要 + 持股健檢 |
| 11 | 技術出場改革（拿掉 % 數，改純技術判斷）|
| 12 | 移動停利→改「跌破 MA20 出場」邏輯 |
| 13 | 寬鬆 vs 嚴謹回測對比（量化偷看偏差）|
| 14 | 新聞警報模組（AI 判讀嚴重度）|
| 15 | TradingView 26 指標評等 + K 棒型態識別 + Claude 看圖 |
| 16 | AI 最終決策表（一表給結論）|
| 17 | Telegram 推播 + 盤中監控 |
| 18 | Windows 排程器設定（含喚醒電腦）|
| 19 | 持股 / 推薦拆分 + 不再建議放空 |

---

## 🚀 未來可以加什麼

### 短期可做
- [ ] AI 解讀技術型態（已有 chart_vision，可擴大）
- [ ] 把回測結果存到 SQLite 累積長期績效
- [ ] 加 Discord 推播當備援
- [ ] 加「黑名單機制」— 剛被砍出去的股冷凍 N 天不再進
- [ ] 每週末寄週報（綜合本週紙上交易表現）

### 中期可做
- [ ] 接 Shioaji paper trading 模式（模擬下單測試）
- [ ] 加台股「強勢族群」追蹤（不只個股）
- [ ] 加經濟數據事件警報（Fed 利率決議、CPI 公布前提醒）
- [ ] 視覺化網頁儀表板（用 Streamlit 5 分鐘做完）

### 長期可做（風險高）
- [ ] 接 Shioaji 實盤（小資金驗證）
- [ ] 接 Alpaca 美股實盤
- [ ] 增加多策略平行跑 + 自動切換
- [ ] 用 ML 模型輔助評分

---

## 📝 給未來自己的話

1. **不要相信回測 100%** — 真實永遠比模擬難
2. **策略勝率 ~50%、賺賠比 ~2:1**，這是金融常態，沒有 90% 勝率的聖杯
3. **紙上跑 1-2 個月確認績效**再考慮接實盤
4. **API 金鑰外洩 = 帳戶清空**，credentials.py 絕對不要 commit 進 git
5. **AI 是助手不是老闆**，最後決策權在你
6. **黑天鵝來時不要硬撐**，停損就停損

---

## 🔗 重要連結

- TWSE OpenAPI: https://openapi.twse.com.tw/
- Shioaji 文件: https://sinotrade.github.io/
- Alpaca 文件: https://alpaca.markets/docs/
- Claude Agent SDK: https://docs.claude.com/zh-TW/api/agent-sdk/
- Telegram Bot API: https://core.telegram.org/bots/api

---

**最後更新**：2026-05-17 ｜ 系統作者：Jimmy + Claude
