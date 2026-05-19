# -*- coding: utf-8 -*-
"""
HTML 郵件組裝與寄送 — 持股 + 推薦版
"""

import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import EMAIL


COLOR_UP = "#d32f2f"
COLOR_DOWN = "#2e7d32"
COLOR_FLAT = "#666666"
COLOR_HEADER = "#1a237e"


def _color(change) -> str:
    if change is None: return COLOR_FLAT
    if change > 0: return COLOR_UP
    if change < 0: return COLOR_DOWN
    return COLOR_FLAT


def _fmt_change(change, pct):
    if change is None: return "—"
    sign = "+" if change > 0 else ""
    return f"{sign}{change} ({sign}{pct}%)"


def _flag(market: str) -> str:
    return "🇹🇼" if market == "TW" else "🇺🇸"


def _bias_chip(bias_label: str, bias: str) -> str:
    bg, fg = "#eceff1", "#37474f"
    if bias == "bullish": bg, fg = "#ffebee", "#c62828"
    elif bias == "bearish": bg, fg = "#e8f5e9", "#2e7d32"
    return (f'<span style="display:inline-block;padding:4px 12px;background:{bg};'
            f'color:{fg};border-radius:12px;font-size:13px;font-weight:600;">{bias_label}</span>')


def _pattern_chip(pattern: str, pattern_name: str) -> str:
    bg, fg = "#e3f2fd", "#1565c0"
    if pattern in ("breakout", "uptrend"): bg, fg = "#fff3e0", "#e65100"
    elif pattern in ("breakdown", "downtrend"): bg, fg = "#e8eaf6", "#3949ab"
    return (f'<span style="display:inline-block;padding:4px 10px;background:{bg};'
            f'color:{fg};border-radius:10px;font-size:12px;margin-left:6px;">📐 {pattern_name}</span>')


# ===========================================================================
# 🎯 AI 最終決策表（主秀）
# ===========================================================================
VERDICT_STYLE = {
    "強力買進": ("#b71c1c", "#fff", "🚀🚀"),
    "買進":     ("#d32f2f", "#fff", "🟢"),
    "持有觀望": ("#9e9e9e", "#fff", "⚪"),
    "減碼":     ("#ef6c00", "#fff", "🟠"),
    "賣出":     ("#2e7d32", "#fff", "🔴"),
    "強力賣出": ("#1b5e20", "#fff", "🔴🔴"),
}


def _verdict_badge(verdict: str) -> str:
    bg, fg, emoji = VERDICT_STYLE.get(verdict, ("#757575", "#fff", "❓"))
    return (f'<span style="display:inline-block;padding:4px 12px;background:{bg};'
            f'color:{fg};border-radius:14px;font-size:12px;font-weight:700;">'
            f'{emoji} {verdict}</span>')


def _build_ai_verdict_section(verdicts: list,
                              title: str = "🎯 AI 最終決策",
                              subtitle: str = "",
                              color: str = "#1a237e") -> str:
    if not verdicts:
        return ""

    # 依 verdict 排序：買進類在前，賣出類在後
    sort_order = {"強力買進": 0, "買進": 1, "持有觀望": 2,
                  "減碼": 3, "賣出": 4, "強力賣出": 5}
    verdicts_sorted = sorted(verdicts, key=lambda v: (sort_order.get(v.get("verdict", ""), 99),
                                                      -v.get("confidence", 0)))

    rows = []
    for v in verdicts_sorted:
        flag = _flag(v.get("market", "TW"))
        verdict = v.get("verdict", "—")
        conf = v.get("confidence", 0)
        reason = v.get("reason", "—")
        entry = v.get("entry"); stop = v.get("stop"); target = v.get("target")
        current = v.get("current_price")
        horizon = v.get("horizon", "—")

        # 信心強弱顏色
        if conf >= 8: conf_color = "#b71c1c"
        elif conf >= 6: conf_color = "#f57c00"
        else: conf_color = "#757575"

        # 價位顯示（盡量友善）
        def _fmt_p(p):
            if p is None: return "—"
            try:
                return f"{float(p):,.1f}"
            except Exception:
                return str(p)

        # 計算停損、目標相對於現價的 %
        def _pct_from_current(p):
            if p is None or not current:
                return ""
            try:
                pct = (float(p) - float(current)) / float(current) * 100
                color = COLOR_UP if pct > 0 else COLOR_DOWN
                return f'<span style="color:{color};font-size:10px;">({pct:+.1f}%)</span>'
            except Exception:
                return ""

        rows.append(f"""
        <tr>
          <td style="padding:10px 10px;border-bottom:1px solid #eee;">
              {flag} <b style="font-size:13px;">{v['name']}</b>
              <div style="color:#999;font-size:10px;">{v['code']}</div>
          </td>
          <td style="padding:10px 10px;border-bottom:1px solid #eee;text-align:center;">
              {_verdict_badge(verdict)}
              <div style="color:{conf_color};font-size:10px;margin-top:2px;font-weight:600;">
                  信心 {conf}/10
              </div>
          </td>
          <td style="padding:10px 10px;border-bottom:1px solid #eee;text-align:right;font-size:12px;">
              <div><span style="color:#999;">現價</span> <b>{_fmt_p(current)}</b></div>
              <div style="margin-top:2px;"><span style="color:#999;">進場</span> <b>{_fmt_p(entry)}</b></div>
          </td>
          <td style="padding:10px 10px;border-bottom:1px solid #eee;text-align:right;font-size:12px;">
              <div style="color:{COLOR_DOWN};">
                  🔴 停損 <b>{_fmt_p(stop)}</b> {_pct_from_current(stop)}
              </div>
              <div style="color:{COLOR_UP};margin-top:2px;">
                  🎯 目標 <b>{_fmt_p(target)}</b> {_pct_from_current(target)}
              </div>
          </td>
          <td style="padding:10px 10px;border-bottom:1px solid #eee;font-size:12px;color:#37474f;">
              {reason}
              <div style="color:#888;font-size:10px;margin-top:4px;">⏱️ {horizon}</div>
          </td>
        </tr>""")

    subtitle_html = ""
    if subtitle:
        subtitle_html = (f'<div style="padding:8px 14px;background:#fffde7;border:1px solid #ffe082;'
                        f'border-radius:4px;font-size:11px;color:#5d4037;margin-bottom:12px;">'
                        f'{subtitle}</div>')

    return f"""
    <div style="padding:20px 24px 4px;">
      <h2 style="font-size:18px;color:#fff;background:{color};
                 padding:12px 16px;margin:8px 0 10px;border-radius:8px;">
          {title}（{len(verdicts)} 檔）
      </h2>
      {subtitle_html}
      <table style="width:100%;border-collapse:collapse;background:#fff;border:1px solid #e0e0e0;
                    border-radius:6px;overflow:hidden;">
        <thead>
          <tr style="background:#f5f7fa;">
            <th style="padding:10px;text-align:left;font-size:11px;color:#555;">標的</th>
            <th style="padding:10px;text-align:center;font-size:11px;color:#555;">AI 建議</th>
            <th style="padding:10px;text-align:right;font-size:11px;color:#555;">現價/進場</th>
            <th style="padding:10px;text-align:right;font-size:11px;color:#555;">停損 / 目標</th>
            <th style="padding:10px;text-align:left;font-size:11px;color:#555;">理由</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
    """


# ===========================================================================
# 🧠 AI 每日摘要 / 持股健檢
# ===========================================================================
def _build_ai_summary_section(market_summary: str, holdings_check: str) -> str:
    if not market_summary and not holdings_check:
        return ""

    summary_html = ""
    if market_summary:
        # 把換行轉成 <br>
        summary_html = f"""
        <div style="background:#fff;padding:14px 16px;border-radius:6px;
                    border-left:4px solid #3949ab;margin-bottom:10px;">
            <div style="font-size:13px;color:#1a237e;font-weight:700;margin-bottom:6px;">
                🧠 AI 今日盤勢摘要
            </div>
            <div style="font-size:13px;color:#37474f;line-height:1.7;">
                {market_summary.replace(chr(10), '<br>')}
            </div>
        </div>"""

    check_html = ""
    if holdings_check:
        check_html = f"""
        <div style="background:#fff;padding:14px 16px;border-radius:6px;
                    border-left:4px solid #f9a825;">
            <div style="font-size:13px;color:#bf6900;font-weight:700;margin-bottom:6px;">
                🩺 AI 持股健檢
            </div>
            <div style="font-size:13px;color:#37474f;line-height:1.7;">
                {holdings_check.replace(chr(10), '<br>')}
            </div>
        </div>"""

    return f"""
    <div style="padding:18px 24px 4px;background:#f0f4ff;margin:0 0 8px 0;">
        {summary_html}
        {check_html}
    </div>
    """


# ===========================================================================
# 🚨 重大新聞警報（放在最頂部，超醒目）
# ===========================================================================
def _build_news_alert_section(news_alerts: list) -> str:
    """渲染重大負面新聞警報，無警示時不顯示。"""
    if not news_alerts:
        return ""

    cards_html = []
    for stock in news_alerts:
        items_html = ""
        max_severity = 0
        for a in stock["alerts"]:
            link_html = (f'<a href="{a["link"]}" style="color:#b71c1c;text-decoration:underline;">'
                         f'{a["title"]}</a>' if a.get("link") else a["title"])

            # AI 模式
            if a.get("ai"):
                sev = a.get("severity", 5)
                max_severity = max(max_severity, sev)
                sentiment = a.get("sentiment", "—")
                action = a.get("action", "—")
                reason = a.get("reason", "")

                # 嚴重度顏色
                if sev >= 9:
                    sev_color = "#b71c1c"; sev_emoji = "🚨🚨"
                elif sev >= 7:
                    sev_color = "#d32f2f"; sev_emoji = "🚨"
                else:
                    sev_color = "#f57c00"; sev_emoji = "⚠️"

                # 動作顏色
                if action == "出場":
                    action_html = f'<span style="background:#b71c1c;color:#fff;padding:1px 8px;border-radius:3px;font-weight:700;">建議出場</span>'
                elif action == "減碼":
                    action_html = f'<span style="background:#f57c00;color:#fff;padding:1px 8px;border-radius:3px;">建議減碼</span>'
                else:
                    action_html = f'<span style="background:#9e9e9e;color:#fff;padding:1px 8px;border-radius:3px;">持有觀察</span>'

                sentiment_emoji = "📉" if sentiment == "利空" else ("📈" if sentiment == "利多" else "➡️")

                items_html += f"""
                <li style="margin:8px 0;color:#5d2020;font-size:12px;line-height:1.6;
                            padding:8px 10px;background:#fff;border-radius:4px;border-left:3px solid {sev_color};">
                    {sev_emoji} <b style="color:{sev_color};">嚴重度 {sev}/10</b>
                    ｜ {sentiment_emoji} {sentiment}
                    ｜ {action_html}
                    <br><b style="font-size:13px;color:#222;">{link_html}</b>
                    <br><span style="color:#666;font-size:11px;">
                        💡 AI 判讀：{reason}
                    </span>
                    <br><span style="color:#999;font-size:10px;">來源：{a['publisher']}</span>
                </li>"""
            # Fallback 關鍵字模式
            else:
                kw_str = " · ".join(a.get("keywords", []))
                items_html += f"""
                <li style="margin:6px 0;color:#5d2020;font-size:12px;line-height:1.5;">
                    {link_html}
                    <br><span style="color:#888;font-size:10px;">
                        🔍 關鍵字：<b style="color:#b71c1c;">{kw_str}</b>
                        ｜ 來源：{a['publisher']}
                    </span>
                </li>"""

        flag = _flag(stock["market"])
        # 整檔最高嚴重度標籤
        sev_badge = ""
        if max_severity >= 8:
            sev_badge = f'<span style="background:#b71c1c;color:#fff;padding:2px 10px;border-radius:10px;font-size:11px;margin-left:8px;">最高 {max_severity}/10</span>'
        elif max_severity >= 6:
            sev_badge = f'<span style="background:#f57c00;color:#fff;padding:2px 10px;border-radius:10px;font-size:11px;margin-left:8px;">最高 {max_severity}/10</span>'

        cards_html.append(f"""
        <div style="border:2px solid #d32f2f;background:#ffebee;padding:14px 16px;
                    margin:10px 0;border-radius:6px;">
            <div style="font-weight:700;color:#b71c1c;font-size:15px;margin-bottom:6px;">
                ⚠️ {flag} {stock['name']}
                <span style="color:#999;font-size:12px;font-weight:400;">
                    ({stock['code']})
                </span>
                {sev_badge}
            </div>
            <ul style="margin:4px 0;padding-left:0;list-style:none;">
                {items_html}
            </ul>
        </div>
        """)

    return f"""
    <div style="padding:18px 24px 6px;">
      <div style="background:#b71c1c;color:#fff;padding:14px 20px;border-radius:8px;
                  text-align:center;margin-bottom:8px;box-shadow:0 2px 8px rgba(183,28,28,0.3);">
          <div style="font-size:20px;font-weight:700;letter-spacing:2px;">
              🚨 重大新聞警報 🚨
          </div>
          <div style="font-size:12px;opacity:0.95;margin-top:4px;">
              以下持股出現可能影響股價的新聞，<b>建議優先確認後再決定是否出場</b>
          </div>
      </div>
      <div style="padding:0 6px;">
          {''.join(cards_html)}
      </div>
    </div>
    """


# ===========================================================================
# 今日操作建議（次頂部）
# ===========================================================================
def _build_signal_section(signals: list) -> str:
    """渲染可執行交易訊號清單。

    signals: list of TradeSignal dict（或 dataclass，用 .__dict__ 取）
    """
    if not signals:
        return f"""
        <div style="padding:20px 24px 4px;">
          <h2 style="font-size:18px;color:{COLOR_HEADER};border-left:5px solid #43a047;
                     padding-left:10px;margin:8px 0 8px;background:#e8f5e9;
                     padding-top:8px;padding-bottom:8px;border-radius:0 4px 4px 0;">
              📬 今日操作建議
          </h2>
          <div style="padding:14px 18px;background:#f5f7fa;border-radius:6px;
                      color:#666;font-size:13px;text-align:center;margin:8px 14px;">
              今日無新進出場訊號，繼續觀察。
          </div>
        </div>
        """

    buys = [s for s in signals if s["action"] in ("BUY", "SHORT")]
    sells = [s for s in signals if s["action"] in ("SELL", "COVER")]

    def _sig_to_dict(s):
        return s if isinstance(s, dict) else s.__dict__

    buys = [_sig_to_dict(s) for s in buys]
    sells = [_sig_to_dict(s) for s in sells]

    def _buy_card(s):
        is_long = s["action"] == "BUY"
        action_label = "📈 買進" if is_long else "📉 放空"
        action_color = COLOR_UP if is_long else COLOR_DOWN
        flag = _flag(s["market"])
        cost = round(s["quantity"] * s["suggested_price"] * 1.001425)

        ma_label = s.get("exit_break_ma_label", "—")
        ma_price = s.get("exit_break_ma_price")
        swing_label = s.get("exit_break_swing_label", "—")
        swing_price = s.get("exit_break_swing_price")
        catastrophic = s.get("catastrophic_stop_price")

        ma_html = f"{ma_price:.2f}" if ma_price else "—"
        swing_html = f"{swing_price:.2f}" if swing_price else "—"
        cat_html = f"{catastrophic:.2f}" if catastrophic else "—"

        return f"""
        <div style="border:2px solid {action_color};border-radius:8px;padding:14px 16px;
                    margin:10px 0;background:#fff;">
          <table style="width:100%;border-collapse:collapse;">
            <tr>
              <td style="padding:0;">
                <span style="background:{action_color};color:#fff;padding:3px 10px;
                             border-radius:4px;font-size:13px;font-weight:700;">
                    {action_label}
                </span>
                <span style="margin-left:8px;font-size:15px;font-weight:700;">
                    {flag} {s['name']} <span style="color:#888;font-size:12px;">{s['code']}</span>
                </span>
              </td>
              <td style="padding:0;text-align:right;">
                <span style="font-size:12px;color:#666;">建議價</span>
                <span style="font-size:18px;font-weight:700;color:#222;margin-left:6px;">
                    {s['suggested_price']:.2f}
                </span>
              </td>
            </tr>
          </table>

          <table style="width:100%;border-collapse:collapse;margin-top:10px;font-size:12px;">
            <tr style="background:#fafafa;">
              <td style="padding:8px 10px;width:33%;">
                  <div style="color:#888;font-size:10px;">股數</div>
                  <div style="font-weight:700;font-size:14px;">{s['quantity']:,}</div>
              </td>
              <td style="padding:8px 10px;width:33%;">
                  <div style="color:#888;font-size:10px;">預估動用金額</div>
                  <div style="font-weight:700;font-size:14px;">NT$ {cost:,}</div>
              </td>
              <td style="padding:8px 10px;width:33%;">
                  <div style="color:#888;font-size:10px;">委託類型</div>
                  <div style="font-weight:700;font-size:14px;">{s['order_type']} 單</div>
              </td>
            </tr>
          </table>

          <div style="margin-top:10px;padding:10px 12px;background:#fff8f0;
                      border-radius:6px;border:1px solid #ffe0b2;">
            <div style="font-size:11px;color:#bf6900;font-weight:700;margin-bottom:6px;">
                🔴 技術出場條件（任一觸發即出場）
            </div>
            <div style="font-size:12px;color:#5d4037;line-height:1.8;">
                ① <b>{ma_label}</b>　當前位置 <b>{ma_html}</b><br>
                ② <b>{swing_label}</b>　當前位置 <b>{swing_html}</b><br>
                ③ <b>技術翻空</b>（多空分數翻負）即出場<br>
                ④ <b>極端虧損保護</b> {cat_html}　（最後一道防線）
            </div>
            <div style="font-size:10px;color:#888;margin-top:6px;">
                💡 註：均線會隨股價上漲而升高，自動把停損拉高鎖利
            </div>
          </div>

          <div style="margin-top:8px;font-size:11px;color:#666;">
              💡 進場依據：{s.get('reason', '—')} ｜ 信心 {s.get('confidence', 0)}/10
          </div>
        </div>
        """

    def _sell_card(s):
        is_long = s["action"] == "SELL"
        action_label = "📤 賣出（多單出場）" if is_long else "📤 回補（空單出場）"
        flag = _flag(s["market"])
        return f"""
        <div style="border:2px solid #ff9800;border-radius:8px;padding:12px 16px;
                    margin:10px 0;background:#fff3e0;">
          <table style="width:100%;border-collapse:collapse;">
            <tr>
              <td style="padding:0;">
                <span style="background:#ff9800;color:#fff;padding:3px 10px;
                             border-radius:4px;font-size:13px;font-weight:700;">
                    {action_label}
                </span>
                <span style="margin-left:8px;font-size:14px;font-weight:700;">
                    {flag} {s['name']} <span style="color:#888;font-size:12px;">{s['code']}</span>
                </span>
              </td>
              <td style="padding:0;text-align:right;">
                <span style="font-size:12px;color:#666;">出場價</span>
                <span style="font-size:16px;font-weight:700;color:#222;margin-left:6px;">
                    {s['suggested_price']:.2f}
                </span>
              </td>
            </tr>
          </table>
          <div style="margin-top:6px;font-size:11px;color:#666;">
              股數 {s['quantity']:,} ｜ 出場原因：{s.get('reason', '—')}
          </div>
        </div>
        """

    sells_html = ""
    if sells:
        sells_html = (
            '<div style="margin-top:8px;"><h3 style="font-size:13px;color:#ff9800;'
            'margin:8px 0 4px;">📤 必須出場（{n}）</h3>'.format(n=len(sells))
            + "".join(_sell_card(s) for s in sells)
            + '</div>'
        )

    buys_html = ""
    if buys:
        buys_html = (
            '<div style="margin-top:12px;"><h3 style="font-size:13px;color:#c62828;'
            'margin:8px 0 4px;">📥 建議進場（{n}）</h3>'.format(n=len(buys))
            + "".join(_buy_card(s) for s in buys)
            + '</div>'
        )

    return f"""
    <div style="padding:20px 24px 4px;">
      <h2 style="font-size:18px;color:#fff;background:linear-gradient(90deg,#c62828,#ff6f00);
                 padding:10px 14px;margin:8px 0 12px;border-radius:6px;">
          📬 今日操作建議（共 {len(signals)} 筆）
      </h2>
      <div style="padding:8px 14px;background:#fffde7;border:1px solid #ffe082;
                  border-radius:4px;font-size:11px;color:#5d4037;margin-bottom:10px;">
          ⚠️ 此清單為紙上交易機器人依策略產生，<b>實盤下單前請自行確認</b>。
          建議用「限價單」掛在建議價附近，避免追高。
      </div>
      <div style="padding:0 14px;">
        {sells_html}
        {buys_html}
      </div>
    </div>
    """


# ===========================================================================
# Header / Footer
# ===========================================================================
def _build_header(today: str) -> str:
    return f"""
    <div style="background:linear-gradient(135deg,#0d1b3d 0%,#1a237e 50%,#3949ab 100%);
                color:#fff;padding:28px 32px;border-radius:8px 8px 0 0;">
        <h1 style="margin:0;font-size:24px;letter-spacing:1px;">📈 早報 · 持股追蹤 + 系統推薦</h1>
        <div style="margin-top:6px;opacity:.88;font-size:13px;">
            {today} ｜ 全市場掃描篩選 · K 線型態 · 多空判讀 · 突破壓力支撐
        </div>
    </div>
    """


def _build_footer() -> str:
    return """
    <div style="padding:18px 24px;background:#f5f7fa;color:#888;
                font-size:11px;line-height:1.6;border-radius:0 0 8px 8px;">
        資料來源：TWSE Open API、Yahoo Finance。<br>
        推薦由演算法依技術面（型態、突破、量價、KD/RSI/MACD）+ 基本面（營收/EPS 成長）綜合計分。<br>
        本報告為個人參考，不構成投資建議。
    </div>
    """


# ===========================================================================
# 共用：K 線詳細卡 / 簡卡
# ===========================================================================
def _ma_row(ma: dict, close: float) -> str:
    items = []
    for label, key in [("5", "ma5"), ("10", "ma10"), ("20", "ma20"),
                        ("60", "ma60"), ("120", "ma120"), ("240", "ma240")]:
        v = ma.get(key)
        if v is None: continue
        color = COLOR_UP if close > v else COLOR_DOWN
        items.append(f'<span style="display:inline-block;margin:0 8px 4px 0;font-size:12px;color:{color};">'
                     f'MA{label} <b>{v:,.2f}</b></span>')
    return "".join(items)


def _sr_block(supports, resistances) -> str:
    r_html = "".join(
        f'<div style="margin:2px 0;"><span style="color:#999;">壓 {i+1}</span> '
        f'<b style="color:{COLOR_UP};">{r["level"]:,.2f}</b> '
        f'<span style="color:#888;font-size:11px;">({r["label"]})</span></div>'
        for i, r in enumerate(resistances)
    ) or '<div style="color:#bbb;">—</div>'
    s_html = "".join(
        f'<div style="margin:2px 0;"><span style="color:#999;">支 {i+1}</span> '
        f'<b style="color:{COLOR_DOWN};">{s["level"]:,.2f}</b> '
        f'<span style="color:#888;font-size:11px;">({s["label"]})</span></div>'
        for i, s in enumerate(supports)
    ) or '<div style="color:#bbb;">—</div>'
    return f"""
    <table style="width:100%;border-collapse:collapse;font-size:12px;margin-top:6px;">
      <tr>
        <td style="width:50%;vertical-align:top;padding:6px 10px;background:#fffafa;border-radius:4px;">
            <div style="color:#666;font-size:11px;margin-bottom:4px;">🔴 上方壓力</div>
            {r_html}
        </td>
        <td style="width:8px;"></td>
        <td style="width:50%;vertical-align:top;padding:6px 10px;background:#f7fbf7;border-radius:4px;">
            <div style="color:#666;font-size:11px;margin-bottom:4px;">🟢 下方支撐</div>
            {s_html}
        </td>
      </tr>
    </table>
    """


def _fmt_pct(v):
    if v is None: return "—"
    return f"{v * 100:+.1f}%"


def _fund_row(fund: dict | None) -> str:
    if not fund: return ""
    parts = []
    pe = fund.get("pe")
    fpe = fund.get("forward_pe")
    rg = fund.get("revenue_growth")
    eg = fund.get("eps_growth")
    if pe: parts.append(f'PE <b>{pe:.1f}</b>')
    if fpe: parts.append(f'F-PE <b>{fpe:.1f}</b>')
    if rg is not None:
        c = COLOR_UP if rg > 0 else COLOR_DOWN
        parts.append(f'營收 YoY <b style="color:{c};">{_fmt_pct(rg)}</b>')
    if eg is not None:
        c = COLOR_UP if eg > 0 else COLOR_DOWN
        parts.append(f'EPS YoY <b style="color:{c};">{_fmt_pct(eg)}</b>')
    if not parts: return ""
    return ('<div style="margin-top:8px;padding:6px 10px;background:#f3e5f5;'
            'border-radius:4px;font-size:11px;color:#4a148c;">'
            '💎 ' + ' ｜ '.join(parts) + '</div>')


def _tv_rating_chip(tv: dict | None) -> str:
    if not tv:
        return ""
    label = tv.get("label", "—")
    color = tv.get("color", "#757575")
    emoji = tv.get("emoji", "")
    buy = tv.get("buy_count", 0); sell = tv.get("sell_count", 0)
    return (f'<span style="display:inline-block;padding:3px 10px;background:{color};'
            f'color:#fff;border-radius:10px;font-size:11px;margin-left:6px;" '
            f'title="TradingView 26 指標綜合評等 ｜ 買 {buy} 賣 {sell}">'
            f'TV {emoji} {label}</span>')


def _patterns_block(patterns: dict | None) -> str:
    if not patterns:
        return ""
    bull = patterns.get("bullish", [])
    bear = patterns.get("bearish", [])
    neutral = patterns.get("neutral", [])
    if not bull and not bear and not neutral:
        return ""

    parts = []
    for p in bull:
        parts.append(f'<span style="background:#ffebee;color:#c62828;padding:2px 8px;'
                     f'border-radius:8px;font-size:11px;margin:2px;">🟢 {p}</span>')
    for p in bear:
        parts.append(f'<span style="background:#e8f5e9;color:#2e7d32;padding:2px 8px;'
                     f'border-radius:8px;font-size:11px;margin:2px;">🔴 {p}</span>')
    for p in neutral:
        parts.append(f'<span style="background:#f5f5f5;color:#757575;padding:2px 8px;'
                     f'border-radius:8px;font-size:11px;margin:2px;">⚪ {p}</span>')

    return f"""
    <div style="margin-top:8px;padding:6px 10px;background:#fafafa;border-radius:4px;">
        <div style="color:#666;font-size:10px;margin-bottom:4px;">🕯️ K 棒型態識別</div>
        {''.join(parts)}
    </div>
    """


def _ai_vision_block(vision: dict | None) -> str:
    if not vision or not vision.get("available") or not vision.get("ai_description"):
        return ""
    desc = vision["ai_description"].replace("\n", "<br>")
    return f"""
    <div style="margin-top:10px;padding:10px 12px;background:#f3e5f5;
                border-left:3px solid #6a1b9a;border-radius:4px;">
        <div style="color:#6a1b9a;font-size:11px;font-weight:700;margin-bottom:6px;">
            🤖 AI 視覺判讀（Claude 看圖分析）
        </div>
        <div style="font-size:12px;color:#37474f;line-height:1.7;">
            {desc}
        </div>
    </div>
    """


def _kline_card(name: str, code: str, market: str, kline: dict,
                fund: dict | None = None,
                inst: dict | None = None, margin: dict | None = None,
                tags: str = "", score: int | None = None,
                vision: dict | None = None) -> str:
    if not kline:
        return (f'<div style="border:1px solid #eee;border-radius:8px;padding:12px;margin:10px 0;'
                f'color:#999;background:#fafafa;">{_flag(market)} {name} ({code}) — 技術資料暫缺</div>')

    c = _color(kline["change"])
    indicators = []

    if kline.get("rsi") is not None:
        rc = COLOR_FLAT; tag = ""
        if kline["rsi"] >= 70: rc = COLOR_UP; tag = "(超買)"
        elif kline["rsi"] <= 30: rc = COLOR_DOWN; tag = "(超賣)"
        indicators.append(f'<span style="color:{rc};">RSI <b>{kline["rsi"]:.0f}</b>{tag}</span>')

    kd = kline.get("kd") or {}
    if kd.get("k") is not None and kd.get("d") is not None:
        kdir = "黃金交叉" if kd["k"] > kd["d"] else "死亡交叉"
        kc = COLOR_UP if kd["k"] > kd["d"] else COLOR_DOWN
        indicators.append(f'<span style="color:{kc};">KD <b>{kd["k"]:.0f}/{kd["d"]:.0f}</b> ({kdir})</span>')

    macd = kline.get("macd") or {}
    if macd.get("dif") is not None:
        m_color = COLOR_UP if (macd.get("osc") or 0) > 0 else COLOR_DOWN
        indicators.append(f'<span style="color:{m_color};">MACD 柱 <b>{macd["osc"]:+.2f}</b></span>')

    indicators_html = " ｜ ".join(indicators) if indicators else "—"

    reasons_html = "".join(
        f'<li style="margin:3px 0;color:#37474f;">{r}</li>' for r in kline.get("reasons", [])
    ) or '<li style="color:#bbb;">（無明顯訊號）</li>'

    chips_parts = []
    if inst:
        chips_parts.append(
            f'外資 <b style="color:{_color(inst["foreign"])};">{inst["foreign"]:+,}</b> ｜ '
            f'投信 <b style="color:{_color(inst["trust"])};">{inst["trust"]:+,}</b> ｜ '
            f'自營 <b style="color:{_color(inst["dealer"])};">{inst["dealer"]:+,}</b>')
    if margin:
        chips_parts.append(
            f'融資 <b style="color:{_color(margin["margin_change"])};">{margin["margin_change"]:+,}</b> ｜ '
            f'融券 <b style="color:{_color(-margin["short_change"])};">{margin["short_change"]:+,}</b>')
    chips_html = ""
    if chips_parts:
        chips_html = ('<div style="margin-top:8px;padding:6px 10px;background:#f5f7fa;'
                      'border-radius:4px;font-size:11px;color:#555;">'
                      + "<br>".join(chips_parts) + "</div>")

    # 推薦標籤
    tag_html = ""
    if tags:
        tag_html = (f'<span style="display:inline-block;padding:3px 10px;background:#fff3e0;'
                    f'color:#e65100;border-radius:10px;font-size:11px;margin-left:6px;">🏷️ {tags}</span>')
    score_html = ""
    if score is not None:
        score_html = f' <span style="color:#888;font-size:11px;">[評分 {score}]</span>'

    return f"""
    <div style="border:1px solid #e0e0e0;border-radius:8px;padding:14px 16px;margin:12px 0;background:#fff;">

      <table style="width:100%;border-collapse:collapse;">
        <tr>
          <td style="padding:0;">
            {_flag(market)} <span style="font-size:15px;font-weight:700;color:#222;">{name}</span>
            <span style="color:#888;font-size:12px;margin-left:6px;">{code}</span>
            {_pattern_chip(kline["pattern"], kline["pattern_name"])}
            {_tv_rating_chip(kline.get("tv_rating"))}
            {tag_html}{score_html}
          </td>
          <td style="padding:0;text-align:right;">
            <span style="font-size:16px;font-weight:700;">{kline["close"]:,}</span>
            <span style="color:{c};font-weight:600;margin-left:6px;">
                {_fmt_change(kline["change"], kline["change_pct"])}
            </span>
          </td>
        </tr>
      </table>

      <div style="margin:10px 0;padding:10px 12px;background:#fafafa;border-radius:6px;">
        {_bias_chip(kline["bias_label"], kline["bias"])}
        <span style="margin-left:10px;font-size:12px;color:#555;">🕯️ {kline["candle"]}</span>
        <span style="margin-left:10px;font-size:12px;color:#555;">📊 {kline["volume_relation"]}</span>
      </div>

      <div style="margin:8px 0;">
        <span style="color:#666;font-size:11px;">均線：</span>
        {_ma_row(kline["ma"], kline["close"])}
      </div>

      <div style="margin:8px 0;font-size:12px;color:#555;">
        <span style="color:#666;">指標：</span> {indicators_html}
      </div>

      {_patterns_block(kline.get("candle_patterns"))}

      <div style="margin-top:8px;">
        <div style="color:#666;font-size:11px;margin-bottom:4px;">判讀依據：</div>
        <ul style="margin:0;padding-left:18px;font-size:12px;">{reasons_html}</ul>
      </div>

      {_sr_block(kline.get("supports", []), kline.get("resistances", []))}

      <div style="margin-top:10px;padding:8px 12px;background:#fff8e1;
                  border-left:3px solid #f9a825;border-radius:4px;font-size:12px;color:#5d4037;">
          💡 <b>結論：</b>{kline["conclusion"]}
      </div>

      {_ai_vision_block(vision)}

      {_fund_row(fund)}
      {chips_html}
    </div>
    """


# ===========================================================================
# 持股區塊
# ===========================================================================
def _build_holdings_section(holdings: dict) -> str:
    blocks = [f"""
    <div style="padding:20px 24px 4px;">
      <h2 style="font-size:18px;color:{COLOR_HEADER};border-left:5px solid #ff6f00;
                 padding-left:10px;margin:8px 0 8px;background:#fff8e1;
                 padding-top:8px;padding-bottom:8px;border-radius:0 4px 4px 0;">
          📌 我的持股 · 每日追蹤
      </h2>
      <div style="color:#666;font-size:12px;margin:6px 0 14px 14px;">
          完整 K 線分析 ｜ 多空判讀 ｜ 突破狀態 ｜ 支撐壓力 ｜ 基本面
      </div>
    </div>
    """]

    if holdings.get("tw"):
        blocks.append('<div style="padding:0 24px;">'
                      f'<h3 style="font-size:14px;color:{COLOR_HEADER};background:#e8eaf6;'
                      'padding:6px 12px;border-radius:4px;margin:12px 0 4px;">▸ 台股持股</h3>')
        for entry in holdings["tw"]:
            blocks.append(_kline_card(
                entry["name"], entry["code"], "TW",
                entry.get("kline"), entry.get("fund"),
                entry.get("inst"), entry.get("margin"),
                vision=entry.get("chart_vision"),
            ))
        blocks.append("</div>")

    if holdings.get("us"):
        blocks.append('<div style="padding:0 24px;">'
                      f'<h3 style="font-size:14px;color:{COLOR_HEADER};background:#e8eaf6;'
                      'padding:6px 12px;border-radius:4px;margin:14px 0 4px;">▸ 美股持股</h3>')
        for entry in holdings["us"]:
            blocks.append(_kline_card(
                entry["name"], entry["code"], "US",
                entry.get("kline"), entry.get("fund"),
                vision=entry.get("chart_vision"),
            ))
        blocks.append("</div>")

    return "".join(blocks)


# ===========================================================================
# 系統推薦區塊
# ===========================================================================
def _build_recommendations_section(rec_tw: dict, rec_us: dict) -> str:
    blocks = [f"""
    <div style="padding:20px 24px 4px;">
      <h2 style="font-size:18px;color:{COLOR_HEADER};border-left:5px solid #c62828;
                 padding-left:10px;margin:18px 0 8px;background:#ffebee;
                 padding-top:8px;padding-bottom:8px;border-radius:0 4px 4px 0;">
          🏆 系統推薦標的 · 全市場掃描篩選
      </h2>
      <div style="color:#666;font-size:12px;margin:6px 0 14px 14px;">
          評分依據：型態突破 + 量價共振 + KD/RSI/MACD + 營收/EPS 成長
      </div>
    </div>
    """]

    if rec_tw:
        scanned = rec_tw.get("scanned_count", 0); cands = rec_tw.get("candidates_count", 0)
        blocks.append(f'<div style="padding:0 24px;">'
                      f'<h3 style="font-size:14px;color:#c62828;background:#fff5f5;'
                      f'padding:6px 12px;border-radius:4px;margin:12px 0 4px;">'
                      f'▸ 🇹🇼 台股推薦 '
                      f'<span style="color:#888;font-size:11px;font-weight:400;">'
                      f'（候選 {cands} 檔 → 深度分析 {scanned} 檔 → 精選 {len(rec_tw.get("picks",[]))} 檔）</span>'
                      f'</h3>')
        for entry in rec_tw.get("picks", []):
            blocks.append(_kline_card(
                entry["name"], entry["code"], "TW",
                entry.get("kline"), entry.get("fund"),
                entry.get("inst"), entry.get("margin"),
                tags=entry.get("tags", ""), score=entry.get("score"),
            ))
        blocks.append("</div>")

    if rec_us:
        scanned = rec_us.get("scanned_count", 0)
        blocks.append(f'<div style="padding:0 24px;">'
                      f'<h3 style="font-size:14px;color:#c62828;background:#fff5f5;'
                      f'padding:6px 12px;border-radius:4px;margin:14px 0 4px;">'
                      f'▸ 🇺🇸 美股推薦 '
                      f'<span style="color:#888;font-size:11px;font-weight:400;">'
                      f'（深度分析 {scanned} 檔 → 精選 {len(rec_us.get("picks",[]))} 檔）</span>'
                      f'</h3>')
        for entry in rec_us.get("picks", []):
            blocks.append(_kline_card(
                entry["name"], entry["code"], "US",
                entry.get("kline"), entry.get("fund"),
                tags=entry.get("tags", ""), score=entry.get("score"),
            ))
        blocks.append("</div>")

    return "".join(blocks)


# ===========================================================================
# 大盤指數 + 族群輪動精簡表
# ===========================================================================
def _build_indices_section(taiex, us_indices) -> str:
    rows = []
    if taiex:
        c = _color(taiex["change"])
        rows.append(f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">🇹🇼 台股加權</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;"><b>{taiex['close']:,}</b></td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:{c};">
              {_fmt_change(taiex['change'], taiex['change_pct'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:#666;">
              量 {taiex['volume']:,.0f} 張</td>
        </tr>""")
    else:
        rows.append('<tr><td colspan="4" style="padding:10px;color:#999;">台股加權暫缺</td></tr>')

    for name, info in us_indices.items():
        if info is None:
            rows.append(f'<tr><td colspan="4" style="padding:10px;color:#999;">🇺🇸 {name} 暫缺</td></tr>'); continue
        c = _color(info["change"])
        rows.append(f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;">🇺🇸 {name}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;"><b>{info['close']:,}</b></td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:{c};">
              {_fmt_change(info['change'], info['change_pct'])}</td>
          <td style="padding:10px 12px;border-bottom:1px solid #eee;text-align:right;color:#666;">—</td>
        </tr>""")

    return f"""
    <div style="padding:14px 24px 4px;">
      <h2 style="font-size:17px;color:{COLOR_HEADER};border-left:4px solid #3949ab;
                 padding-left:10px;margin:8px 0 12px;">📊 大盤指數</h2>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr style="background:#f5f7fa;">
          <th style="padding:8px 12px;text-align:left;color:#555;">指數</th>
          <th style="padding:8px 12px;text-align:right;color:#555;">收盤</th>
          <th style="padding:8px 12px;text-align:right;color:#555;">漲跌</th>
          <th style="padding:8px 12px;text-align:right;color:#555;">備註</th>
        </tr></thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def _sector_table(sectors: dict, market_flag: str, market_name: str) -> str:
    """族群強弱精簡表，不再放代表股深度卡。"""
    def rows_for(group, label_prefix, color):
        if not group:
            return f'<tr><td colspan="3" style="padding:10px;color:#999;">—</td></tr>'
        out = []
        for s in group:
            avg = s["avg_pct"]
            ac = _color(avg)
            leader = s.get("leader") or {}
            laggard = s.get("laggard") or {}
            if leader or laggard:
                detail = f'領 {leader.get("name","—")}({leader.get("change_pct",0):+.1f}%)'
                detail += f' ｜ 弱 {laggard.get("name","—")}({laggard.get("change_pct",0):+.1f}%)'
            else:
                detail = f"代表 ETF {s.get('etf','—')}"
            out.append(f"""
            <tr>
              <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;color:{color};">
                  {label_prefix} {s["name"]}
              </td>
              <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;text-align:right;
                  color:{ac};font-weight:600;">{avg:+.2f}%</td>
              <td style="padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:11px;color:#666;">
                  {detail}</td>
            </tr>""")
        return "".join(out)

    return f"""
    <div style="padding:8px 24px;">
      <h3 style="font-size:14px;color:{COLOR_HEADER};background:#e8eaf6;
                 padding:6px 12px;border-radius:4px;margin:14px 0 6px;">
        {market_flag} {market_name} 族群輪動
      </h3>
      <table style="width:100%;border-collapse:collapse;font-size:12px;">
        <thead><tr style="background:#fafafa;color:#666;">
          <th style="padding:6px 12px;text-align:left;">族群</th>
          <th style="padding:6px 12px;text-align:right;">平均</th>
          <th style="padding:6px 12px;text-align:left;">領頭/落後</th>
        </tr></thead>
        <tbody>
          <tr><td colspan="3" style="padding:4px 12px;background:#fff5f5;color:#c62828;
              font-size:11px;font-weight:700;">💹 強勢族群</td></tr>
          {rows_for(sectors.get("strong", []), "🔥", "#c62828")}
          <tr><td colspan="3" style="padding:4px 12px;background:#f1f8f1;color:#2e7d32;
              font-size:11px;font-weight:700;">📉 弱勢族群</td></tr>
          {rows_for(sectors.get("weak", []), "❄️", "#2e7d32")}
        </tbody>
      </table>
    </div>
    """


def _build_sectors_summary(tw_sectors: dict, us_sectors: dict) -> str:
    return f"""
    <div style="padding:8px 24px;">
      <h2 style="font-size:17px;color:{COLOR_HEADER};border-left:4px solid #3949ab;
                 padding-left:10px;margin:18px 0 4px;">🔄 族群輪動概況</h2>
    </div>
    {_sector_table(tw_sectors, "🇹🇼", "台股")}
    {_sector_table(us_sectors, "🇺🇸", "美股")}
    """


# ===========================================================================
# 對外
# ===========================================================================
def build_html(taiex, us_indices,
               holdings=None, rec_tw=None, rec_us=None,
               tw_sectors=None, us_sectors=None,
               paper_html: str = None,
               trade_signals: list = None,
               news_alerts: list = None,
               ai_market_summary: str = "",
               ai_verdicts_holdings: list = None,
               ai_verdicts_recommend: list = None,
               show_details: bool = False) -> str:
    """
    show_details=False → 精簡版（AI 決策 + 大盤 + 族群 + 持倉狀態）
    show_details=True → 完整版（含每檔詳細卡片）
    """
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    parts = [
        '<div style="max-width:880px;margin:0 auto;font-family:'
        '\'Segoe UI\',\'Microsoft JhengHei\',Arial,sans-serif;'
        'background:#fff;border:1px solid #e0e0e0;border-radius:8px;">',
        _build_header(today),
        # 🚨 重大新聞警報（最優先）
        _build_news_alert_section(news_alerts or []),
        # 📌 我的持股 AI 建議
        _build_ai_verdict_section(
            ai_verdicts_holdings or [],
            title="📌 我的持股 — AI 建議",
            subtitle="🤖 Claude Sonnet 綜合 K 線 / KD / RSI / 量價 / 基本面 / 新聞 / 法人籌碼 給每檔持股一個明確建議",
            color="linear-gradient(90deg,#1a237e,#3949ab)",
        ),
        # 🚀 系統推薦新進場
        _build_ai_verdict_section(
            ai_verdicts_recommend or [],
            title="🚀 系統推薦 — 新進場機會",
            subtitle="從全市場掃描挑出的候選股，只顯示 AI 認為「強力買進 / 買進 / 持有觀望」者",
            color="linear-gradient(90deg,#c62828,#ff6f00)",
        ),
        # 🧠 AI 大盤摘要
        _build_ai_summary_section(ai_market_summary, ""),
        # 📊 大盤指數
        _build_indices_section(taiex, us_indices),
        # 🔄 族群輪動
        _build_sectors_summary(tw_sectors or {}, us_sectors or {}),
        # 🤖 紙上交易帳戶結算
        (f'<div style="padding:8px 24px;margin-top:18px;">{paper_html}</div>'
         if paper_html else ''),
    ]

    if show_details:
        parts.extend([
            _build_signal_section(trade_signals or []),
            _build_holdings_section(holdings or {}),
            _build_recommendations_section(rec_tw or {}, rec_us or {}),
        ])

    parts.extend([
        _build_footer(),
        "</div>",
    ])
    return "\n".join(parts)


def send_report(html: str) -> None:
    msg = MIMEMultipart("alternative")
    today = datetime.now().strftime("%Y-%m-%d")
    msg["Subject"] = f"{EMAIL['subject']} - {today}"
    msg["From"] = EMAIL["sender"]
    msg["To"] = ", ".join(EMAIL["to"])
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(EMAIL["sender"], EMAIL["password"])
        smtp.send_message(msg)
