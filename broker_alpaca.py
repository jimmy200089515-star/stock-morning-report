# -*- coding: utf-8 -*-
"""
Alpaca 美股券商整合

==================== 開通步驟 ====================
1. 註冊 https://alpaca.markets
2. 登入後到 Dashboard → Paper Trading（紙上交易，免費虛擬）
   或 Live Trading（需要實際開美國券商戶，要 W-8BEN）
3. 點 "Generate New Key" → 取得 API_KEY 和 SECRET_KEY
4. 填入 credentials.py：
   ALPACA_API_KEY, ALPACA_SECRET_KEY
   ALPACA_PAPER = True  # 模擬環境

==================== 安裝 ====================
pip install alpaca-py

文件：https://alpaca.markets/docs/api-references/

==================== 注意 ====================
- 強烈建議先用 Paper Trading 模式跑數週
- Live Trading 需要實際 KYC + 入金，最低 $1 但流程久
- 台灣人開實盤帳戶要證明在美國有住址或申報海外帳戶，較麻煩
- Alpaca 預設只支援整股（fractional shares 要另外開通）
"""

from __future__ import annotations

from typing import Optional

from broker_base import Broker, Position, OrderResult


try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce
    ALPACA_AVAILABLE = True
except ImportError:
    TradingClient = None
    ALPACA_AVAILABLE = False


class AlpacaBroker(Broker):
    name = "Alpaca"
    market = "US"

    def __init__(self,
                 api_key: str,
                 secret_key: str,
                 paper: bool = True):
        if not ALPACA_AVAILABLE:
            raise ImportError(
                "alpaca-py 未安裝。請先 `pip install alpaca-py`"
            )
        self.api_key = api_key
        self.secret_key = secret_key
        self.is_paper = paper
        self.client: Optional[TradingClient] = None

    # ---------------- 連線 ----------------
    def connect(self) -> bool:
        try:
            self.client = TradingClient(
                api_key=self.api_key,
                secret_key=self.secret_key,
                paper=self.is_paper,
            )
            account = self.client.get_account()
            mode = "Paper" if self.is_paper else "Live"
            print(f"[Alpaca] 已登入（{mode} 模式）｜ 帳戶 #{account.account_number}")
            return True
        except Exception as e:
            print(f"[Alpaca] 登入失敗：{e}")
            return False

    def disconnect(self) -> None:
        self.client = None

    # ---------------- 帳戶 ----------------
    def get_cash(self) -> float:
        try:
            return float(self.client.get_account().cash)
        except Exception as e:
            print(f"[Alpaca] 查現金失敗：{e}")
            return 0.0

    def get_positions(self) -> list[Position]:
        try:
            raw = self.client.get_all_positions()
            return [
                Position(
                    code=p.symbol,
                    quantity=int(float(p.qty)),
                    avg_price=float(p.avg_entry_price),
                    market_value=float(p.market_value),
                    unrealized_pnl=float(p.unrealized_pl),
                    direction="long" if float(p.qty) > 0 else "short",
                )
                for p in raw
            ]
        except Exception as e:
            print(f"[Alpaca] 查持倉失敗：{e}")
            return []

    # ---------------- 下單 ----------------
    def place_order(self,
                    code: str,
                    side: str,
                    quantity: int,
                    price: Optional[float] = None,
                    order_type: str = "LIMIT") -> OrderResult:
        try:
            # Alpaca 把多空整合在 OrderSide 跟 qty 正負，這裡用「side」直譯
            alpaca_side = (OrderSide.BUY if side in ("BUY", "COVER")
                          else OrderSide.SELL)

            if order_type == "MARKET":
                req = MarketOrderRequest(
                    symbol=code,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                )
            else:
                req = LimitOrderRequest(
                    symbol=code,
                    qty=quantity,
                    side=alpaca_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=price,
                )

            order = self.client.submit_order(req)
            print(f"[Alpaca] 已送單 {side} {code} x{quantity} @{price}")
            return OrderResult(
                success=True,
                order_id=str(order.id),
                filled_quantity=0,
                message="已送出",
                raw={"order_status": str(order.status)},
            )
        except Exception as e:
            print(f"[Alpaca] 下單失敗 {code}：{e}")
            return OrderResult(False, message=str(e))

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except Exception as e:
            print(f"[Alpaca] 取消單失敗：{e}")
            return False

    def get_order_status(self, order_id: str) -> dict:
        try:
            order = self.client.get_order_by_id(order_id)
            return {
                "id": str(order.id),
                "status": str(order.status),
                "filled_qty": int(float(order.filled_qty or 0)),
                "filled_price": float(order.filled_avg_price or 0),
            }
        except Exception as e:
            return {"id": order_id, "status": "error", "error": str(e)}


# ============================================================
# 工廠函式
# ============================================================
def create_from_credentials(paper: bool = True) -> Optional[AlpacaBroker]:
    """從 credentials.py 載入並建立 Broker。"""
    try:
        import credentials as cred
    except ImportError:
        return None

    api_key = getattr(cred, "ALPACA_API_KEY", None)
    secret_key = getattr(cred, "ALPACA_SECRET_KEY", None)
    if not api_key or not secret_key:
        print("[Alpaca] credentials.py 缺少 ALPACA_API_KEY/SECRET_KEY，跳過")
        return None

    return AlpacaBroker(api_key=api_key, secret_key=secret_key, paper=paper)
