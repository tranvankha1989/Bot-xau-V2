#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trình chạy Huấn luyện Tinh chỉnh Kronos Exness 1-Click (run_exness_finetune.py)
Tự động xuất nến từ MT5 Exness và huấn luyện tạo trọng số Kronos-Gold-Exness.
"""

import os
import sys
import subprocess

# Chống phát sinh .pyc / __pycache__
sys.dont_write_bytecode = True

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINETUNE_DIR = os.path.join(ROOT_DIR, "finetune_csv")


def main():
    print("=" * 65)
    print("  🚀 QUY TRÌNH HUẤN LUYỆN TINH CHỈNH KRONOS CHO SÀN EXNESS (1-CLICK)")
    print("=" * 65)

    symbol = sys.argv[1] if len(sys.argv) > 1 else "XAUUSDm"
    bars = int(sys.argv[2]) if len(sys.argv) > 2 else 60000

    # 1. Trích xuất nến từ MT5 Exness
    print(f"\n[BƯỚC 1/2] Đang trích xuất {bars:,} nến M5 {symbol} từ MT5 Exness...")
    from export_exness_candles import export_candles
    csv_file = export_candles(symbol=symbol, timeframe_str="M5", count=bars)

    if not csv_file or not os.path.exists(csv_file):
        print("❌ Không thể tiếp tục vì chưa trích xuất được dữ liệu nến từ MT5.", file=sys.stderr)
        return 1

    # 2. Chạy huấn luyện Sequential Trainer
    print(f"\n[BƯỚC 2/2] Bắt đầu quá trình Fine-tuning Tokenizer & Base Model trên GPU...")
    config_path = os.path.join(FINETUNE_DIR, "configs", "config_xauusd_exness.yaml")
    train_script = os.path.join(FINETUNE_DIR, "train_sequential.py")

    cmd = [sys.executable, train_script, "--config", config_path]
    print(f"Lệnh thực thi: {' '.join(cmd)}")
    ret = subprocess.call(cmd, cwd=FINETUNE_DIR)

    if ret == 0:
        print("\n" + "=" * 65)
        print("  🎉 HUẤN LUYỆN HOÀN TẤT THÀNH CÔNG 100%!")
        print("  Trọng số chuyên biệt đã được lưu vào: model/weights/Kronos-Gold-Exness")
        print("  Bây giờ bạn có thể mở App mt5_scalping_bot.py và chọn:")
        print("  -> 'AI-Gold-Exness (Chuyên sâu Exness)' trong mục Mô hình!")
        print("=" * 65)
    else:
        print(f"\n⚠️ Quá trình huấn luyện dừng lại với mã thoát: {ret}", file=sys.stderr)
    return ret


if __name__ == "__main__":
    sys.exit(main())

