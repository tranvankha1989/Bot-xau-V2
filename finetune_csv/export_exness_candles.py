#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Công cụ tự động trích xuất nến lịch sử từ MT5 Exness (export_exness_candles.py)
Xuất dữ liệu nến M5 chuẩn định dạng CSV để huấn luyện (Fine-tuning) cho Kronos.
"""

import os
import sys
from datetime import datetime
import pandas as pd

# Chống phát sinh .pyc / __pycache__
sys.dont_write_bytecode = True

try:
    import MetaTrader5 as mt5
except ImportError:
    print("❌ Lỗi: Thư viện MetaTrader5 chưa được cài đặt. Hãy chạy: pip install MetaTrader5", file=sys.stderr)
    sys.exit(1)


def export_candles(symbol: str = "XAUUSDm", timeframe_str: str = "M5", count: int = 60000, out_csv: str = None) -> str:
    """Kết nối MT5 Exness và trích xuất số lượng nến lịch sử chỉ định ra file CSV."""
    if not mt5.initialize():
        print(f"❌ Không thể kết nối với MT5 Exness. Mã lỗi: {mt5.last_error()}", file=sys.stderr)
        return ""

    tf_map = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "H1": mt5.TIMEFRAME_H1,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = tf_map.get(timeframe_str.upper(), mt5.TIMEFRAME_M5)

    if not mt5.symbol_select(symbol, True):
        # Thử tìm các biến thể tên symbol (ví dụ XAUUSD thay vì XAUUSDm)
        alt_sym = symbol.replace("m", "") if symbol.endswith("m") else f"{symbol}m"
        if mt5.symbol_select(alt_sym, True):
            print(f"ℹ️ Đã tự động chuyển sang mã symbol hợp lệ: {alt_sym}")
            symbol = alt_sym
        else:
            print(f"❌ Không tìm thấy mã {symbol} trên MT5 Exness.", file=sys.stderr)
            mt5.shutdown()
            return ""

    print(f"⏳ Đang tải {count:,} nến {symbol} ({timeframe_str}) từ máy chủ MT5 Exness...")
    # Lấy nến đã đóng hoàn toàn (bắt đầu từ vị trí 1 để loại bỏ nến đang hình thành)
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, count)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print(f"❌ Không trích xuất được dữ liệu cho {symbol}.", file=sys.stderr)
        return ""

    df = pd.DataFrame(rates)
    df["timestamps"] = pd.to_datetime(df["time"], unit="s").dt.strftime("%Y/%m/%d %H:%M")
    df["volume"] = df["tick_volume"].astype(float)
    avg_price = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    df["amount"] = (df["volume"] * avg_price).astype(float)

    # Đúng thứ tự 7 cột theo chuẩn dataset Kronos
    df_out = df[["timestamps", "open", "close", "high", "low", "volume", "amount"]].copy()

    if not out_csv:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(base_dir, "data")
        os.makedirs(data_dir, exist_ok=True)
        clean_sym = symbol.replace("/", "_")
        out_csv = os.path.join(data_dir, f"{clean_sym}_{timeframe_str}_exness.csv")

    df_out.to_csv(out_csv, index=False)
    print(f"✅ Đã xuất thành công {len(df_out):,} nến vào file:\n   -> {out_csv}")
    print(f"   Thời gian từ: {df_out['timestamps'].iloc[0]} đến {df_out['timestamps'].iloc[-1]}")
    return out_csv


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDm"
    bars = int(sys.argv[2]) if len(sys.argv) > 2 else 60000
    export_candles(symbol=sym, timeframe_str="M5", count=bars)

