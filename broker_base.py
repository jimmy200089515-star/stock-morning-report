# -*- coding: utf-8 -*-
"""
券商整合的抽象基底類別

任何券商 implementation（Shioaji、Alpaca、IB...）都繼承 Broker 並實作這些方法。
executor.py 不直接呼叫券商套件，只透過 Broker 介面，所以可以無痛切換。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Position:
    code: str
    quantity: int           # 持股數
    avg_price: float        # 平均成本
    market_value: float = 0
    unrealized_pnl: float = 0
    direction: str = "long"  # "long" or "short"


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_quantity: int = 0
    message: str = ""
    raw: dict = field(default_factory=dict)


class Broker(ABC):
    """券商抽象介面。"""

    name: str = "base"
    market: str = "TW"        # "TW" / "US"
    is_paper: bool = True     # 是否為模擬環境

    # -------- 連線 --------
    @abstractmethod
    def connect(self) -> bool:
        """連線/登入券商。回傳 True 表成功。"""

    @abstractmethod
    def disconnect(self) -> None:
        """登出。"""

    # -------- 帳戶 --------
    @abstractmethod
    def get_cash(self) -> float:
        """可用資金（含融資餘額）。"""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """目前持倉清單。"""

    # -------- 下單 --------
    @abstractmethod
    def place_order(self,
                    code: str,
                    side: str,                # "BUY"/"SELL"/"SHORT"/"COVER"
                    quantity: int,
                    price: Optional[float] = None,
                    order_type: str = "LIMIT") -> OrderResult:
        """送出訂單。回傳 OrderResult。"""

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """取消未成交單。"""

    @abstractmethod
    def get_order_status(self, order_id: str) -> dict:
        """查詢訂單狀態。"""

    # -------- 共用工具 --------
    def __enter__(self):
        if not self.connect():
            raise RuntimeError(f"無法連上 {self.name}")
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self.disconnect()
        except Exception:
            pass
