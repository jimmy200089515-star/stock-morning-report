# -*- coding: utf-8 -*-
"""
永豐金 Shioaji 券商整合

==================== 開通步驟 ====================
1. 開永豐金證券帳戶
2. 透過大戶投 / 豐存股 App 申請 API 權限
   路徑：設定 → 證券 → API 申請 → 啟用
3. 簽電子下單委託書（線上簽）
4. 申請 CA 憑證並下載 .pfx 檔（給程式下單授權用）
   路徑：永豐金證券官網 → 我的帳戶 → 憑證下載
5. 取得 API_KEY 與 SECRET_KEY
   路徑：API 申請頁面 → 我的金鑰
6. 在 credentials.py 填入金鑰（複製 credentials_example.py）

==================== 安裝 ====================
pip install shioaji

文件：https://sinotrade.github.io/

==================== 注意 ====================
- 預設先連模擬環境（simulation=True），確認無誤再切實盤
- 實盤下單前一定要 enable_simulation=False 並且手動確認
- API_KEY 切勿外洩，且建議用環境變數，不要寫死在程式中
"""

from __future__ import annotations

import os
from typing import Optional

from broker_base import Broker, Position, OrderResult


try:
    import shioaji as sj
    SHIOAJI_AVAILABLE = True
except ImportError:
    sj = None
    SHIOAJI_AVAILABLE = False


class ShioajiBroker(Broker):
    name = "Shioaji"
    market = "TW"

    def __init__(self,
                 api_key: str,
                 secret_key: str,
                 ca_path: str,
                 ca_password: str,
                 person_id: str,
                 simulation: bool = True):
        if not SHIOAJI_AVAILABLE:
            raise ImportError(
                "shioaji 套件未安裝。請先 `pip install shioaji`"
            )
        self.api_key = api_key
        self.secret_key = secret_key
        self.ca_path = ca_path
        self.ca_password = ca_password
        self.person_id = person_id
        self.is_paper = simulation
        self.api = None

    # ---------------- 連線 ----------------
    def connect(self) -> bool:
        try:
            self.api = sj.Shioaji(simulation=self.is_paper)
            self.api.login(
                api_key=self.api_key,
                secret_key=self.secret_key,
            )
            # 啟用憑證（下單必須）
            self.api.activate_ca(
                ca_path=self.ca_path,
                ca_passwd=self.ca_password,
                person_id=self.person_id,
            )
            mode = "模擬" if self.is_paper else "實盤"
            print(f"[Shioaji] 已登入（{mode}模式）")
            return True
        except Exception as e:
            print(f"[Shioaji] 登入失敗：{e}")
            return False

    def disconnect(self) -> None:
        try:
            if self.api:
                self.api.logout()
                print("[Shioaji] 已登出")
        except Exception:
            pass

    # ---------------- 帳戶 ----------------
    def get_cash(self) -> float:
        try:
            balance = self.api.account_balance()
            return float(balance.acc_balance)
        except Exception as e:
            print(f"[Shioaji] 查餘額失敗：{e}")
            return 0.0

    def get_positions(self) -> list[Position]:
        try:
            raw_positions = self.api.list_positions(self.api.stock_account)
            return [
                Position(
                    code=str(p.code),
                    quantity=int(p.quantity),
                    avg_price=float(p.price),
                    market_value=float(p.last_price * p.quantity * 1000),
                    unrealized_pnl=float(p.pnl),
                    direction="long" if p.direction == sj.constant.Action.Buy else "short",
                )
                for p in raw_positions
            ]
        except Exception as e:
            print(f"[Shioaji] 查持倉失敗：{e}")
            return []

    # ---------------- 下單 ----------------
    def place_order(self,
                    code: str,
                    side: str,
                    quantity: int,
                    price: Optional[float] = None,
                    order_type: str = "LIMIT") -> OrderResult:
        """送單到 Shioaji。

        side: BUY/SELL/SHORT/COVER
        quantity: 股數（會自動換算為「張」，1張=1000股）
        """
        try:
            # 取得標的（自動判斷上市/上櫃）
            contract = None
            if code in self.api.Contracts.Stocks.TSE:
                contract = self.api.Contracts.Stocks.TSE[code]
            elif code in self.api.Contracts.Stocks.OTC:
                contract = self.api.Contracts.Stocks.OTC[code]
            else:
                return OrderResult(False, message=f"找不到標的 {code}")

            # 動作對映
            action_map = {
                "BUY": sj.constant.Action.Buy,
                "SELL": sj.constant.Action.Sell,
                "SHORT": sj.constant.Action.Sell,      # 融券賣
                "COVER": sj.constant.Action.Buy,       # 融券買回
            }
            sj_action = action_map.get(side)
            if sj_action is None:
                return OrderResult(False, message=f"未知動作 {side}")

            # 委託類型
            price_type = (sj.constant.StockPriceType.MKT
                          if order_type == "MARKET"
                          else sj.constant.StockPriceType.LMT)

            # 融資/融券（如果是 SHORT/COVER 要設）
            order_lot = sj.constant.StockOrderLot.Common  # 整股
            order_cond = (sj.constant.StockOrderCond.MarginTrading
                          if side in ("SHORT", "COVER")
                          else sj.constant.StockOrderCond.Cash)

            # 張數（Shioaji 用張）
            qty_lots = max(1, quantity // 1000)

            order = self.api.Order(
                price=price or 0,
                quantity=qty_lots,
                action=sj_action,
                price_type=price_type,
                order_type=sj.constant.OrderType.ROD,    # 當日有效
                order_lot=order_lot,
                order_cond=order_cond,
                account=self.api.stock_account,
            )
            trade = self.api.place_order(contract, order)
            print(f"[Shioaji] 已送單 {side} {code} x{qty_lots}張 @{price}")
            return OrderResult(
                success=True,
                order_id=trade.order.id,
                filled_quantity=0,  # 剛送出，還未成交
                message="已送出，等待成交",
                raw={"trade": str(trade)},
            )
        except Exception as e:
            print(f"[Shioaji] 下單失敗 {code}：{e}")
            return OrderResult(False, message=str(e))

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.api.cancel_order(order_id)
            return True
        except Exception as e:
            print(f"[Shioaji] 取消單失敗：{e}")
            return False

    def get_order_status(self, order_id: str) -> dict:
        try:
            self.api.update_status(self.api.stock_account)
            trades = self.api.list_trades()
            for t in trades:
                if t.order.id == order_id:
                    return {
                        "id": order_id,
                        "status": str(t.status.status),
                        "filled_qty": t.status.deal_quantity,
                        "filled_price": float(t.status.deal_price) if t.status.deal_price else 0,
                    }
            return {"id": order_id, "status": "not_found"}
        except Exception as e:
            return {"id": order_id, "status": "error", "error": str(e)}


# ============================================================
# 工廠函式（從 credentials.py 載入）
# ============================================================
def create_from_credentials(simulation: bool = True) -> Optional[ShioajiBroker]:
    """嘗試從 credentials.py 讀取設定建立 Broker。失敗回傳 None。"""
    try:
        import credentials as cred
    except ImportError:
        return None

    required = ["SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
                "SHIOAJI_CA_PATH", "SHIOAJI_CA_PASSWORD", "SHIOAJI_PERSON_ID"]
    for k in required:
        if not getattr(cred, k, None):
            print(f"[Shioaji] credentials.py 缺少 {k}，跳過")
            return None

    return ShioajiBroker(
        api_key=cred.SHIOAJI_API_KEY,
        secret_key=cred.SHIOAJI_SECRET_KEY,
        ca_path=cred.SHIOAJI_CA_PATH,
        ca_password=cred.SHIOAJI_CA_PASSWORD,
        person_id=cred.SHIOAJI_PERSON_ID,
        simulation=simulation,
    )
