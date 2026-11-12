"""
launcher.py
───────────
Native In-Memory Secure Bootloader & 2-Stage Lazy-Load Crash Shield
Dành cho ứng dụng: Trading AI PRO by Kha Tran Design

Kiến trúc khởi chạy 2 giai đoạn (2-Stage Lazy Loading):
1. Giai đoạn 1 (Siêu nhẹ - 0.1 giây):
   - Nạp động cơ bản quyền và cập nhật (kronos_license, kronos_updater).
   - Kiểm tra HWID và hiển thị ngay hộp thoại kích hoạt bản quyền nếu máy mới/chưa có key.
   - Tuyệt đối không nạp PyTorch hay thư viện AI nặng ở giai đoạn này để đảm bảo cửa sổ luôn
     hiện lên ngay lập tức trên mọi máy tính, máy ảo (VM) hay VPS.
2. Giai đoạn 2 (Sau khi đã có key bản quyền hợp lệ):
   - Nạp các module mô hình AI (model.module, model.kronos, model).
   - Nạp và khởi chạy giao diện giao dịch chính (mt5_scalping_bot).
   - Bẫy lỗi toàn diện qua Win32 Native MessageBoxW và ghi log chi tiết.
"""

import os
import sys
import types
import marshal
import hashlib
import traceback

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# 1. Đảm bảo thư mục làm việc luôn cố định tại thư mục chứa file thực thi
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    os.chdir(APP_DIR)
except Exception:
    pass


def show_native_error(title: str, message: str):
    """
    Hiển thị thông báo lỗi trực tiếp qua Win32 API cấp thấp.
    Đảm bảo 100% người dùng luôn nhìn thấy lý do lỗi kể cả khi Tkinter bị hỏng hay thiếu DLL.
    """
    try:
        import ctypes
        # 0x10 = MB_ICONERROR, 0x10000 = MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), 0x10 | 0x10000)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox as mb
            _t = tk.Tk()
            _t.withdraw()
            _t.attributes("-topmost", True)
            mb.showerror(title, message)
            _t.destroy()
        except Exception:
            pass


# 2. Kiểm tra thư viện mã hóa bảo mật
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    show_native_error("Lỗi Khởi Động", "Thiếu thư viện mã hóa bảo mật 'cryptography'. Vui lòng cài đặt lại phần mềm.")
    sys.exit(1)

# Chỉ dẫn phân tích tĩnh để PyInstaller gom trọn vẹn toàn bộ thư viện nhị phân vào gói chạy
if False:
    import MetaTrader5
    import torch
    import torch.nn.functional
    import numpy
    import pandas
    import PIL
    import matplotlib
    import gradio_client
    import huggingface_hub
    import einops
    import safetensors
    import tqdm
    import cryptography
    import cryptography.hazmat.primitives.asymmetric.ed25519
    import cryptography.hazmat.primitives.ciphers.aead
    import cryptography.hazmat.primitives.kdf.pbkdf2
    import cryptography.hazmat.primitives.hashes
    import cryptography.exceptions

# Khóa bí mật dẫn xuất nội bộ của ứng dụng (32 bytes AES-256)
PROPRIETARY_SALT = b"TRADING_AI_PRO_KHA_TRAN_DESIGN_IMMUTABLE_PROPRIETARY_SALT_2026"
ENGINE_KEY = hashlib.sha256(b"KHA_TRAN_TRADING_ALGORITHM_AES_KEY_V1" + PROPRIETARY_SALT).digest()
AAD_HEADER = b"TRADING_AI_PRO_ENCRYPTED_BINARY_CONTAINER_V1"

_CONTAINER_RAW_DICT = None


def get_app_dir() -> str:
    """Lấy thư mục gốc chứa file thực thi của ứng dụng."""
    return APP_DIR


def init_binary_engine():
    """Giải mã tệp nhị phân và khởi tạo khung sys.modules trong bộ nhớ RAM."""
    global _CONTAINER_RAW_DICT
    app_dir = get_app_dir()
    bin_path = os.path.join(app_dir, "core_engine.bin")

    # Kiểm tra thêm trong _MEIPASS nếu đóng gói một tệp PyInstaller
    if not os.path.exists(bin_path) and hasattr(sys, "_MEIPASS"):
        bin_path = os.path.join(sys._MEIPASS, "core_engine.bin")

    if not os.path.exists(bin_path):
        show_native_error(
            "Lỗi Bản Cài Đặt",
            f"Không tìm thấy tệp nhị phân cốt lõi:\n{bin_path}\nVui lòng cài đặt lại ứng dụng."
        )
        sys.exit(1)

    try:
        with open(bin_path, "rb") as f_in:
            raw_data = f_in.read()

        nonce = raw_data[:12]
        ciphertext = raw_data[12:]
        aesgcm = AESGCM(ENGINE_KEY)
        container_bytes = aesgcm.decrypt(nonce, ciphertext, AAD_HEADER)
        _CONTAINER_RAW_DICT = marshal.loads(container_bytes)

        # B1: Khởi tạo tất cả module và thiết lập __package__, __path__ trong sys.modules
        for mod_name in _CONTAINER_RAW_DICT.keys():
            mod = types.ModuleType(mod_name)
            if "." in mod_name:
                pkg_name = mod_name.rsplit(".", 1)[0]
                mod.__package__ = pkg_name
            else:
                mod.__package__ = mod_name
                mod.__path__ = []
            mod.__file__ = os.path.join(app_dir, f"{mod_name}.pyd")
            sys.modules[mod_name] = mod

        # B2: Gán submodules vào parent packages
        for mod_name in _CONTAINER_RAW_DICT.keys():
            if "." in mod_name:
                pkg_name, sub_name = mod_name.rsplit(".", 1)
                if pkg_name in sys.modules:
                    setattr(sys.modules[pkg_name], sub_name, sys.modules[mod_name])

    except Exception as e:
        tb = traceback.format_exc()
        show_native_error(
            "Lỗi Giải Mã Nhị Phân",
            f"Không thể giải mã tệp nhị phân lõi:\n{e}\n\nChi tiết:\n{tb}"
        )
        sys.exit(1)


def execute_module(mod_name: str):
    """Thực thi mã nguồn bytecode của một module cụ thể từ bộ nhớ RAM."""
    global _CONTAINER_RAW_DICT
    if not _CONTAINER_RAW_DICT or mod_name not in _CONTAINER_RAW_DICT:
        raise ModuleNotFoundError(f"Không tìm thấy module nhị phân: {mod_name}")

    code_obj = marshal.loads(_CONTAINER_RAW_DICT[mod_name])
    exec(code_obj, sys.modules[mod_name].__dict__)


def main():
    # ── GIAI ĐOẠN 1: NẠP LỚP BẢN QUYỀN SIÊU NHẸ (KHÔNG PHỤ THUỘC PYTORCH) ──────
    init_binary_engine()

    try:
        execute_module("kronos_license")
        execute_module("kronos_updater")
    except Exception as e:
        tb = traceback.format_exc()
        show_native_error("Lỗi Khởi Động Bản Quyền", f"Lỗi nạp module bảo mật:\n{e}\n\n{tb}")
        sys.exit(1)

    lic_mod = sys.modules.get("kronos_license")
    if not lic_mod:
        show_native_error("Lỗi Khởi Động", "Không tìm thấy module kronos_license.")
        sys.exit(1)

    LicenseManager = getattr(lic_mod, "LicenseManager")
    show_activation_dialog = getattr(lic_mod, "show_activation_dialog")

    lic_mgr = LicenseManager()
    lic_status = lic_mgr.check_status()

    if not lic_status.get("is_valid", False):
        # Mở hộp thoại kích hoạt ĐỘC LẬP - Xuất hiện tức thì 0.1s trên mọi máy ảo mà không cần nạp PyTorch trước
        activated = show_activation_dialog(parent=None, license_mgr=lic_mgr, is_startup_block=True)
        if not activated:
            sys.exit(0)
        lic_status = lic_mgr.check_status(force_refresh=True)
        if not lic_status.get("is_valid", False):
            sys.exit(0)

    # ── GIAI ĐOẠN 2: ĐÃ XÁC THỰC KEY THÀNH CÔNG -> NẠP MÔ HÌNH AI & BOT CHÍNH ──
    try:
        execute_module("model.module")
        execute_module("model.kronos")
        execute_module("model")
        execute_module("mt5_scalping_bot")
    except Exception as e:
        tb = traceback.format_exc()
        show_native_error(
            "Lỗi Khởi Tạo Động Cơ AI",
            f"Không thể khởi động mô hình AI hoặc giao diện Bot:\n{e}\n\nChi tiết:\n{tb}\n\n"
            f"Gợi ý: Kiểm tra xem máy tính/máy ảo có hỗ trợ tập lệnh AVX/AVX2 hoặc đã cài VC++ 2015-2022 x64 chưa."
        )
        sys.exit(1)

    bot_mod = sys.modules.get("mt5_scalping_bot")
    if not bot_mod:
        show_native_error("Lỗi Khởi Động", "Không tìm thấy module mt5_scalping_bot.")
        sys.exit(1)

    if hasattr(bot_mod, "ensure_single_app_instance"):
        try:
            bot_mod.ensure_single_app_instance()
        except Exception:
            pass

    import tkinter as tk
    root = tk.Tk()
    KronosDesktopApp = getattr(bot_mod, "KronosDesktopApp")
    app = KronosDesktopApp(root, lic_mgr=lic_mgr)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except SystemExit as se:
        if se.code not in (0, None):
            err_msg = f"Ứng dụng đã thoát sớm với mã lỗi: {se.code}\n\nTraceback:\n{traceback.format_exc()}"
            try:
                log_dir = os.path.join(get_app_dir(), "logs")
                os.makedirs(log_dir, exist_ok=True)
                with open(os.path.join(log_dir, "crash_startup.log"), "w", encoding="utf-8") as f:
                    f.write(err_msg)
            except Exception:
                pass
            show_native_error("Lỗi Khởi Động Ứng Dụng", err_msg)
            sys.exit(se.code)
    except BaseException as e:
        err_msg = traceback.format_exc()
        try:
            log_dir = os.path.join(get_app_dir(), "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "crash_startup.log"), "w", encoding="utf-8") as f:
                f.write(err_msg)
        except Exception:
            pass

        show_native_error(
            "Lỗi Khởi Động Ứng Dụng — Trading AI PRO",
            f"Ứng dụng gặp sự cố khi khởi động:\n\n{err_msg}\n\nNhật ký chi tiết được ghi tại logs/crash_startup.log."
        )
        sys.exit(1)
