"""
tools/binary_packer.py
──────────────────────
Công cụ Băm và Mã hóa Toàn bộ Mã Nguồn Thuật Toán thành Tệp Nhị Phân / Hex (01010101).
Thuộc quyền sở hữu trí tuệ: Trading AI PRO by Kha Tran Design.

Nguyên lý bảo mật:
1. Đọc toàn bộ code nguồn Python (mt5_scalping_bot, kronos_license, kronos_updater).
2. Biên dịch (compile) sang Python Bytecode mức tối ưu hóa cao nhất (optimize=2, xóa docstring và assert).
3. Tuần tự hóa (marshal) và mã hóa cấp quân sự AES-256-GCM.
4. Xuất ra file nhị phân duy nhất (core_engine.bin).
   - Mở bằng Notepad/Hex Editor chỉ thấy toàn chuỗi nhị phân 010101 và byte Hex ngẫu nhiên.
   - Không chứa bất kỳ dòng text, tên biến hay công thức nào của thuật toán.
5. Khi chạy, Bootloader giải mã trực tiếp trong bộ nhớ RAM, tuyệt đối KHÔNG ghi file code ra đĩa cứng.
"""

import os
import sys
import json
import marshal
import types
import hashlib
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    print("[ERROR] Thư viện 'cryptography' chưa được cài đặt. Chạy: pip install cryptography")
    sys.exit(1)

ROOT_DIR = Path(__file__).resolve().parent.parent

# Khóa bí mật dẫn xuất nội bộ của ứng dụng (32 bytes AES-256)
PROPRIETARY_SALT = b"TRADING_AI_PRO_KHA_TRAN_DESIGN_IMMUTABLE_PROPRIETARY_SALT_2026"
ENGINE_KEY = hashlib.sha256(b"KHA_TRAN_TRADING_ALGORITHM_AES_KEY_V1" + PROPRIETARY_SALT).digest()
AAD_HEADER = b"TRADING_AI_PRO_ENCRYPTED_BINARY_CONTAINER_V1"


def pack_code_to_binary(source_files: dict[str, Path], output_bin_path: Path) -> int:
    """
    Biên dịch và mã hóa danh sách file mã nguồn thành một khối nhị phân duy nhất.
    """
    raw_dict = {}
    for mod_name, file_path in source_files.items():
        if not file_path.exists():
            raise FileNotFoundError(f"Không tìm thấy file nguồn: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            code_str = f.read()

        # compile với optimize=2 để triệt tiêu toàn bộ docstring và chú thích
        code_obj = compile(code_str, filename=f"<native_binary_{mod_name}>", mode="exec", optimize=2)
        raw_dict[mod_name] = marshal.dumps(code_obj)

    container_bytes = marshal.dumps(raw_dict)
    aesgcm = AESGCM(ENGINE_KEY)
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, container_bytes, AAD_HEADER)

    output_bin_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_bin_path, "wb") as f_out:
        # Ghi 12 bytes nonce đầu + ciphertext kèm 16 bytes auth tag của GCM
        f_out.write(nonce + ciphertext)

    total_bytes = len(nonce) + len(ciphertext)
    return total_bytes


def unpack_and_load_in_memory(bin_path: Path, target_module: str = "mt5_scalping_bot"):
    """
    Giải mã trực tiếp khối nhị phân trong bộ nhớ RAM và đăng ký vào sys.modules.
    """
    if not bin_path.exists():
        raise FileNotFoundError(f"Không tìm thấy tệp nhị phân lõi: {bin_path}")

    with open(bin_path, "rb") as f_in:
        raw_data = f_in.read()

    nonce = raw_data[:12]
    ciphertext = raw_data[12:]
    aesgcm = AESGCM(ENGINE_KEY)
    container_bytes = aesgcm.decrypt(nonce, ciphertext, AAD_HEADER)
    raw_dict = marshal.loads(container_bytes)

    # B1: Khởi tạo tất cả module và thiết lập __package__, __path__ trong sys.modules
    for mod_name in raw_dict.keys():
        mod = types.ModuleType(mod_name)
        if "." in mod_name:
            pkg_name = mod_name.rsplit(".", 1)[0]
            mod.__package__ = pkg_name
        else:
            mod.__package__ = mod_name
            mod.__path__ = []
        mod.__file__ = f"<native_binary_{mod_name}>"
        sys.modules[mod_name] = mod

    # B2: Gán submodules vào parent packages
    for mod_name in raw_dict.keys():
        if "." in mod_name:
            pkg_name, sub_name = mod_name.rsplit(".", 1)
            if pkg_name in sys.modules:
                setattr(sys.modules[pkg_name], sub_name, sys.modules[mod_name])

    # B3: Thực thi code objects theo thứ tự phụ thuộc
    order = ["model.module", "model.kronos", "model", "kronos_license", "kronos_updater", "mt5_scalping_bot"]
    executed = set()
    for mod_name in order:
        if mod_name in raw_dict:
            code_obj = marshal.loads(raw_dict[mod_name])
            exec(code_obj, sys.modules[mod_name].__dict__)
            executed.add(mod_name)
    for mod_name, code_bytes in raw_dict.items():
        if mod_name not in executed:
            code_obj = marshal.loads(code_bytes)
            exec(code_obj, sys.modules[mod_name].__dict__)

    # Xóa sạch container_bytes khỏi RAM ngay sau khi nạp xong
    del container_bytes
    del raw_data

    return sys.modules.get(target_module)


if __name__ == "__main__":
    out_file = ROOT_DIR / "core_engine.bin"
    src = {
        "model.module": ROOT_DIR / "model" / "module.py",
        "model.kronos": ROOT_DIR / "model" / "kronos.py",
        "model": ROOT_DIR / "model" / "__init__.py",
        "kronos_license": ROOT_DIR / "kronos_license.py",
        "kronos_updater": ROOT_DIR / "kronos_updater.py",
        "mt5_scalping_bot": ROOT_DIR / "mt5_scalping_bot.py",
    }
    print("[*] Đang băm và mã hóa toàn bộ mã nguồn sang file nhị phân (core_engine.bin)...")
    sz = pack_code_to_binary(src, out_file)
    print(f"[OK] Đã xuất file nhị phân: {out_file} ({sz / 1024:.1f} KB)")
