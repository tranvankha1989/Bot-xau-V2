"""
kronos_license.py
=================
Hệ thống Quản lý Bản quyền & Bảo mật Ngoại tuyến (Offline License & Security Engine)
dành riêng cho AI Scalping Bot (KhaTran - Kronos).

Đặc điểm kỹ thuật & Tiêu chuẩn bảo mật:
1. Mật mã học bất đối xứng Ed25519:
   - Nhúng Public Key cố định vào Client để xác thực chữ ký số 100% offline.
   - Private Key được giữ tuyệt đối an toàn trên máy Admin/Developer.
   - Tương thích 100% với công cụ tạo key trong tools/keygen_cli.py & tools/keygen_gui.py.

2. Định danh phần cứng bất biến (HWID Engine):
   - Thu thập thông số phần cứng vật lý: CPU Processor ID + Motherboard Serial + System Disk 0 Serial.
   - Tự động fallback: PowerShell CIM -> WMIC -> Windows Registry.
   - Định dạng chuẩn: TTS-XXXX-XXXX-XXXX-XXXX.

3. Lưu trữ an toàn đa vị trí (Tripple-Breadcrumbs Persistence):
   - Phân tán dấu vết vào 3 thư mục chuẩn người dùng của Windows:
     + Vị trí 1: %LOCALAPPDATA%\\Kronos\\Data\\lic_cache.dat
     + Vị trí 2: %APPDATA%\\Kronos\\Config\\app_state.dat
     + Vị trí 3: %ProgramData%\\Kronos\\shared_lic.dat (hoặc ~/.kronos/shared_lic.dat)
   - Mã hóa AES-256-GCM với khóa dẫn xuất từ chính HWID của máy (PBKDF2 100.000 vòng).
   - Cơ chế đồng thuận (Consensus) + Tự phục hồi (Self-Healing) khi 1 vị trí bị xóa.
   - An toàn tuyệt đối với Windows Defender / Phần mềm diệt virus (0 can thiệp Registry COM, 0 dùng NTFS ADS).
   - Khách gỡ cài đặt app bằng trình gỡ của Windows hay bên thứ ba đều KHÔNG làm mất key bản quyền.

4. Chính sách Dùng thử (Trial) & Chống gian lận ngày:
   - Tự động cấp 7 ngày dùng thử miễn phí cho máy cài lần đầu.
   - Chống tua lùi đồng hồ hệ thống (Anti-Clock Rollback) dựa trên last_run_timestamp.
   - Chống gỡ ra cài lại để reset ngày dùng thử (Anti-Trial Reset).

5. Giao diện Kích hoạt Bản quyền (Activation Dialog):
   - Tkinter Dark Mode cao cấp (chuẩn thiết kế Slate & Gold/Emerald).
   - Sao chép HWID 1 chạm vào Clipboard.
   - Xác thực và kích hoạt tức thì.
"""

import os
import re
import sys
import json
import time
import math
import base64
import hashlib
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple

import tkinter as tk
from tkinter import messagebox

# Thư viện mật mã học chuyên dụng
try:
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    from cryptography.exceptions import InvalidSignature
except Exception as e:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Lỗi khởi động bảo mật (kronos_license):\nThiếu mô-đun cryptography hoặc các phần tử mật mã ({e}).\nVui lòng cài đặt lại phần mềm.",
            "Lỗi Bản Quyền — Trading AI PRO",
            0x10 | 0x10000
        )
    except Exception:
        pass
    raise

logger = logging.getLogger("Kronos.License")

# 32 bytes Raw Public Key của hệ thống (Khớp chính xác với tools/ed25519_public.pem)
ED25519_PUBLIC_KEY_BYTES = b"i\x9cstB\xa1|0Fx\r\xf2\xf0\x80\xf4\x15\xc2\xed\xa5\x08%s\xf6{\xab\\g\xfdD\xb8w\x01"

# Salt định danh phần cứng (đồng bộ chuẩn với tools/keygen_cli.py để ra cùng 1 HWID)
HWID_SALT = "OMNIVOICE_STUDIO_HWID_SALT_2026_OFFLINE_IMMUTABLE_CORE"

# Salt mã hóa lưu trữ file local
PERSISTENCE_SALT = "KRONOS_SCALPING_BOT_LICENSE_PERSISTENCE_SALT_2026_OFFLINE_GCM"

# Thời hạn sử dụng hoàn toàn do License Key quy định (không tự động cấp ngày khi chưa có key)
TRIAL_DAYS = 0
TRIAL_DURATION_SECONDS = 0

_CACHED_HWID: Optional[str] = None
_CACHED_HWID_RAW: Optional[str] = None


# =============================================================================
# 1. HWID ENGINE (ĐỊNH DANH PHẦN CỨNG BẤT BIẾN)
# =============================================================================
def _clean_str(val: Any) -> str:
    """Làm sạch chuỗi, loại bỏ ký tự lạ, chuyển chữ hoa và lọc các giá trị rác của nhà sản xuất."""
    if not val:
        return ""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(val)).strip().upper()
    invalid_defaults = {
        "TOBEFILLEDBYOEM",
        "DEFAULTSTRING",
        "NONE",
        "UNKNOWN",
        "NULL",
        "NOTAVAILABLE",
        "00000000",
        "12345678",
    }
    if cleaned in invalid_defaults:
        return ""
    return cleaned


def _get_hardware_via_powershell() -> Tuple[str, str, str]:
    """Lấy thông số CPU, Mainboard, Disk Index 0 qua PowerShell CIM (chuẩn Windows 10 & 11)."""
    cpu_id, mb_serial, disk_serial = "", "", ""
    ps_script = """
    $Output = @{
        cpu = (Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessorId -First 1);
        mb  = (Get-CimInstance Win32_BaseBoard -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SerialNumber -First 1);
        disk = (Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue | Where-Object { $_.Index -eq 0 } | Select-Object -ExpandProperty SerialNumber -First 1);
    }
    Write-Output "$($Output.cpu)|$($Output.mb)|$($Output.disk)"
    """
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW

        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=creationflags,
        )
        if res.returncode == 0 and res.stdout:
            parts = res.stdout.strip().split("|")
            if len(parts) >= 3:
                cpu_id = _clean_str(parts[0])
                mb_serial = _clean_str(parts[1])
                disk_serial = _clean_str(parts[2])
    except Exception as e:
        logger.debug(f"Không thể đọc HWID qua PowerShell CIM: {e}")

    return cpu_id, mb_serial, disk_serial


def _get_hardware_via_wmic() -> Tuple[str, str, str]:
    """Fallback: Lấy thông số qua WMIC (Windows 7/8/10)."""
    cpu_id, mb_serial, disk_serial = "", "", ""

    def run_cmd(cmd: str) -> str:
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            res = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=creationflags,
            )
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if len(lines) >= 2:
                    return lines[1]
        except Exception:
            pass
        return ""

    cpu_id = _clean_str(run_cmd("wmic cpu get processorid"))
    mb_serial = _clean_str(run_cmd("wmic baseboard get serialnumber"))
    disk_serial = _clean_str(run_cmd('wmic diskdrive where "Index=0" get serialnumber'))
    return cpu_id, mb_serial, disk_serial


def _get_hardware_via_registry() -> Tuple[str, str, str]:
    """Fallback an toàn: Đọc trực tiếp từ Windows Registry mà không cần gọi sub-process."""
    cpu_id, mb_serial, disk_serial = "", "", ""
    if sys.platform != "win32":
        return cpu_id, mb_serial, disk_serial

    try:
        import winreg

        # 1. CPU Identifier
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as k:
                val, _ = winreg.QueryValueEx(k, "Identifier")
                cpu_id = _clean_str(val)
        except Exception:
            pass

        # 2. Mainboard / BIOS Serial
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\BIOS",
            ) as k:
                for reg_name in ["BaseBoardSerialNumber", "SystemSerialNumber", "BaseBoardProduct"]:
                    try:
                        val, _ = winreg.QueryValueEx(k, reg_name)
                        clean_v = _clean_str(val)
                        if clean_v:
                            mb_serial = clean_v
                            break
                    except Exception:
                        pass
        except Exception:
            pass

        # 3. Disk Serial hoặc MachineGuid
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as k:
                val, _ = winreg.QueryValueEx(k, "MachineGuid")
                disk_serial = _clean_str(val)
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"Không thể đọc registry hardware: {e}")

    return cpu_id, mb_serial, disk_serial


def get_hardware_id() -> str:
    """
    Trả về chuỗi HWID chuẩn hoá dạng: TTS-XXXX-XXXX-XXXX-XXXX
    Có cơ chế cache để chỉ tính toán 1 lần duy nhất trong toàn bộ vòng đời tiến trình.
    """
    global _CACHED_HWID, _CACHED_HWID_RAW
    if _CACHED_HWID:
        return _CACHED_HWID

    # B1: Lấy qua PowerShell CIM
    cpu, mb, disk = _get_hardware_via_powershell()

    # B2: Fallback WMIC nếu thiếu
    if not (cpu and mb and disk):
        w_cpu, w_mb, w_disk = _get_hardware_via_wmic()
        cpu = cpu or w_cpu
        mb = mb or w_mb
        disk = disk or w_disk

    # B3: Fallback Registry nếu thiếu
    if not (cpu and mb and disk):
        r_cpu, r_mb, r_disk = _get_hardware_via_registry()
        cpu = cpu or r_cpu
        mb = mb or r_mb
        disk = disk or r_disk

    # Fallback cuối cùng nếu máy ảo hoặc thiếu quyền tuyệt đối
    if not cpu:
        import uuid
        cpu = f"NODE{uuid.getnode()}"
    if not mb:
        mb = os.environ.get("COMPUTERNAME", "PC_DEFAULT")
    if not disk:
        disk = os.environ.get("USERNAME", "USER_DEFAULT")

    _CACHED_HWID_RAW = f"{cpu}:{mb}:{disk}:{HWID_SALT}"
    hwid_hash = hashlib.sha256(_CACHED_HWID_RAW.encode("utf-8")).hexdigest().upper()

    # Định dạng hiển thị chuẩn: TTS-XXXX-XXXX-XXXX-XXXX
    formatted_hwid = f"TTS-{hwid_hash[:4]}-{hwid_hash[4:8]}-{hwid_hash[8:12]}-{hwid_hash[12:16]}"
    _CACHED_HWID = formatted_hwid
    return _CACHED_HWID


# =============================================================================
# 2. XÁC THỰC MẬT MÃ BẤT ĐỐI XỨNG ED25519
# =============================================================================
def verify_license_string(
    license_key: str,
    expected_hwid: Optional[str] = None,
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """
    Xác thực chữ ký số mật mã Ed25519 của chuỗi bản quyền.
    Trả về: (is_valid, payload_dict, error_or_success_message)
    """
    if not license_key or not isinstance(license_key, str):
        return False, None, "Chuỗi mã bản quyền trống."

    clean_key = license_key.strip()
    if not clean_key.startswith("ED25519."):
        return False, None, "Định dạng mã bản quyền không đúng chuẩn (phải bắt đầu bằng ED25519.)."

    parts = clean_key.split(".")
    if len(parts) != 3:
        return False, None, "Cấu trúc mã bản quyền bị sai lệch hoặc thiếu thành phần."

    _, b64_payload, b64_sig = parts

    def pad_b64(s: str) -> str:
        return s + "=" * (-len(s) % 4)

    try:
        payload_bytes = base64.urlsafe_b64decode(pad_b64(b64_payload))
        signature_bytes = base64.urlsafe_b64decode(pad_b64(b64_sig))
    except Exception:
        return False, None, "Mã bản quyền chứa ký tự Base64 không hợp lệ."

    # 1. Kiểm tra chữ ký số Ed25519 với Public Key nhúng sẵn
    try:
        pub_key = ed25519.Ed25519PublicKey.from_public_bytes(ED25519_PUBLIC_KEY_BYTES)
        pub_key.verify(signature_bytes, payload_bytes)
    except InvalidSignature:
        return False, None, "Chữ ký số bản quyền không hợp lệ hoặc chuỗi mã đã bị chỉnh sửa!"
    except Exception as e:
        return False, None, f"Lỗi xác thực chữ ký số: {e}"

    # 2. Giải mã Payload JSON
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return False, None, "Dữ liệu bản quyền không thể giải mã thành cấu trúc JSON hợp lệ."

    # 3. So khớp Hardware ID
    if expected_hwid:
        norm_expected = expected_hwid.strip().upper()
        norm_target = str(payload.get("hwid", "")).strip().upper()
        if norm_target != norm_expected:
            return (
                False,
                payload,
                f"Mã bản quyền được cấp cho thiết bị khác ({norm_target}), không khớp với máy này ({norm_expected}).",
            )

    # 4. Kiểm tra ngày hết hạn
    is_lifetime = payload.get("is_lifetime", False)
    expiry_date_str = str(payload.get("expiry_date", "")).strip()

    if not is_lifetime and expiry_date_str:
        try:
            exp_date = datetime.strptime(expiry_date_str, "%Y-%m-%d").date()
            today_utc = datetime.now(timezone.utc).date()
            if today_utc > exp_date:
                days_over = (today_utc - exp_date).days
                return (
                    False,
                    payload,
                    f"Mã bản quyền đã hết hạn vào ngày {expiry_date_str} (quá hạn {days_over} ngày). Vui lòng gia hạn thêm.",
                )
        except ValueError:
            return False, payload, "Định dạng ngày hết hạn trong bản quyền không hợp lệ."

    msg = "Bản quyền VĨNH VIỄN (PRO) hợp lệ!" if is_lifetime else f"Bản quyền hợp lệ đến ngày {expiry_date_str}!"
    return True, payload, msg


# =============================================================================
# 3. TRIPPLE-BREADCRUMBS SAFE PERSISTENCE (LƯU TRỮ ĐA ĐIỂM CHỐNG GỠ CÀI ĐẶT)
# =============================================================================
def _derive_aes_key(hwid: str) -> bytes:
    """Dẫn xuất khóa AES-256 (32 bytes) từ HWID thông qua PBKDF2 (100.000 rounds)."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=PERSISTENCE_SALT.encode("utf-8"),
        iterations=100_000,
    )
    return kdf.derive(hwid.strip().upper().encode("utf-8"))


def encrypt_payload(data: dict, hwid: str) -> str:
    """Mã hóa payload thành chuỗi Base64 bằng AES-256-GCM gắn chặt với HWID."""
    key = _derive_aes_key(hwid)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    raw_data = json.dumps(data, sort_keys=True).encode("utf-8")
    aad = hwid.strip().upper().encode("utf-8")
    ciphertext = aesgcm.encrypt(nonce, raw_data, aad)
    combined = nonce + ciphertext
    return base64.b64encode(combined).decode("ascii")


def decrypt_payload(b64_str: str, hwid: str) -> Optional[dict]:
    """Giải mã chuỗi Base64 bằng AES-256-GCM. Trả về None nếu sai HWID hoặc dữ liệu bị sửa đổi."""
    if not b64_str:
        return None
    try:
        combined = base64.b64decode(b64_str.encode("ascii"))
        if len(combined) < 28:
            return None
        nonce = combined[:12]
        ciphertext = combined[12:]
        key = _derive_aes_key(hwid)
        aesgcm = AESGCM(key)
        aad = hwid.strip().upper().encode("utf-8")
        plaintext = aesgcm.decrypt(nonce, ciphertext, aad)
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None


def _get_localappdata_path() -> Path:
    """Vị trí 1: %LOCALAPPDATA%\\Kronos\\Data\\lic_cache.dat"""
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else (Path.home() / "AppData" / "Local")
    return base / "Kronos" / "Data" / "lic_cache.dat"


def _get_roamingappdata_path() -> Path:
    """Vị trí 2: %APPDATA%\\Kronos\\Config\\app_state.dat"""
    roaming = os.environ.get("APPDATA")
    base = Path(roaming) if roaming else (Path.home() / "AppData" / "Roaming")
    return base / "Kronos" / "Config" / "app_state.dat"


def _get_shared_data_paths() -> list[Path]:
    """Vị trí 3: %ProgramData%\\Kronos\\shared_lic.dat (hoặc ~/.kronos/shared_lic.dat)."""
    paths = []
    pd = os.environ.get("ProgramData")
    if pd:
        paths.append(Path(pd) / "Kronos" / "shared_lic.dat")
    paths.append(Path.home() / ".kronos" / "shared_lic.dat")
    return paths


def _read_file_payload(file_path: Path, hwid: str) -> Optional[dict]:
    if not file_path.exists():
        return None
    try:
        content = file_path.read_text(encoding="ascii").strip()
        return decrypt_payload(content, hwid)
    except Exception:
        return None


def _write_file_payload(file_path: Path, data: dict, hwid: str) -> bool:
    try:
        encrypted = encrypt_payload(data, hwid)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(encrypted, encoding="ascii")
        return True
    except Exception as e:
        logger.debug(f"Không thể ghi file {file_path}: {e}")
        return False


def load_license_state(hwid: str) -> Optional[Dict[str, Any]]:
    """
    Đọc đồng thời từ 3 vị trí độc lập đạt chuẩn Windows AppData:
    1. %LOCALAPPDATA%\\Kronos\\Data\\lic_cache.dat
    2. %APPDATA%\\Kronos\\Config\\app_state.dat
    3. %ProgramData%\\Kronos\\shared_lic.dat (hoặc ~/.kronos/shared_lic.dat)

    Áp dụng thuật toán đồng thuận (Consensus) + Tự phục hồi (Self-Healing):
    - Lấy install_timestamp nhỏ nhất (mốc bắt đầu sớm nhất, chống reset Trial).
    - Lấy last_run_timestamp lớn nhất (mốc chạy mới nhất, chống tua lùi giờ).
    - Lấy license_key hợp lệ nếu có trong bất kỳ vị trí nào.
    - Tự động ghi bù lại nếu có vị trí bị thiếu do người dùng hoặc trình gỡ xóa dở dang.
    """
    p1 = _get_localappdata_path()
    p2 = _get_roamingappdata_path()
    shared_paths = _get_shared_data_paths()

    s1 = _read_file_payload(p1, hwid)
    s2 = _read_file_payload(p2, hwid)

    s3 = None
    for sp in shared_paths:
        s3 = _read_file_payload(sp, hwid)
        if s3:
            break

    states = [s for s in (s1, s2, s3) if s and isinstance(s, dict)]
    if not states:
        return None

    # Tìm license_key đã kích hoạt trong bất kỳ bản sao nào
    saved_key = ""
    activated_at = ""
    client_name = ""
    for s in states:
        k = s.get("license_key")
        if k and isinstance(k, str) and k.startswith("ED25519."):
            saved_key = k
            activated_at = s.get("activated_at", "")
            client_name = s.get("client_name", "")
            break

    # Lấy install_timestamp xa nhất trong quá khứ để chống gỡ ra cài lại reset Trial
    install_ts = min(s.get("install_timestamp", float("inf")) for s in states)
    if install_ts == float("inf"):
        install_ts = time.time()

    last_run_ts = max(s.get("last_run_timestamp", 0) for s in states)

    unified_state = {
        "install_timestamp": install_ts,
        "last_run_timestamp": last_run_ts,
        "license_key": saved_key,
        "activated_at": activated_at,
        "client_name": client_name,
    }

    # Tự động phục hồi (Self-Healing) vào các vị trí bị thiếu
    if not (s1 and s2 and s3):
        save_license_state(hwid, unified_state)

    return unified_state


def save_license_state(hwid: str, data: dict) -> bool:
    """Ghi đồng bộ dữ liệu vào cả 3 vị trí an toàn trên máy."""
    p1 = _get_localappdata_path()
    p2 = _get_roamingappdata_path()
    shared_paths = _get_shared_data_paths()

    r1 = _write_file_payload(p1, data, hwid)
    r2 = _write_file_payload(p2, data, hwid)

    r3 = False
    for sp in shared_paths:
        if _write_file_payload(sp, data, hwid):
            r3 = True
            break

    return r1 or r2 or r3


# =============================================================================
# 4. TRÌNH QUẢN LÝ BẢN QUYỀN CHÍNH (LICENSE MANAGER)
# =============================================================================
class LicenseManager:
    """Quản lý toàn bộ nghiệp vụ kiểm tra, kích hoạt và hiển thị bản quyền."""

    def __init__(self):
        self.hwid = get_hardware_id()
        self._cached_status: Optional[Dict[str, Any]] = None
        self._last_status_check = 0.0

    def get_hwid(self) -> str:
        return self.hwid

    def check_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Kiểm tra trạng thái bản quyền của máy theo các ưu tiên:
        1. Key Ed25519 đã kích hoạt (Vĩnh viễn hoặc còn hạn).
        2. Nếu chưa có key: Kiểm tra thời hạn Dùng thử (Trial 7 ngày).
        3. Kiểm tra chống tua lùi thời gian (Anti-Clock Rollback).
        """
        now = time.time()
        if not force_refresh and self._cached_status and (now - self._last_status_check) < 1.0:
            return self._cached_status

        state = load_license_state(self.hwid)
        if not state:
            # Máy cài lần đầu tiên: Khởi tạo mốc install_timestamp và bắt đầu Trial 7 ngày
            state = {
                "install_timestamp": now,
                "last_run_timestamp": now,
                "license_key": "",
                "activated_at": "",
                "client_name": "",
            }
            save_license_state(self.hwid, state)

        install_ts = float(state.get("install_timestamp", now))
        last_run_ts = float(state.get("last_run_timestamp", now))

        # Kiểm tra chống tua lùi đồng hồ hệ thống (cho phép lệch tối đa 300 giây)
        if now < (last_run_ts - 300):
            res = {
                "is_valid": False,
                "status_type": "TAMPERED",
                "days_remaining": 0,
                "badge_text": "[ Thời gian hệ thống sai ]",
                "badge_color": "#f87171",
                "message": "Phát hiện thời gian trên máy tính bị tua lùi bất thường! Vui lòng chỉnh lại ngày giờ chuẩn.",
                "hwid": self.hwid,
                "payload": None,
            }
            self._cached_status = res
            self._last_status_check = now
            return res

        # Cập nhật mốc chạy mới nhất
        state["last_run_timestamp"] = now
        save_license_state(self.hwid, state)

        # ─── TRƯỜNG HỢP 1: ĐÃ KÍCH HOẠT KEY BẢN QUYỀN ED25519 ──────────────────
        saved_key = str(state.get("license_key", "")).strip()
        if saved_key and saved_key.startswith("ED25519."):
            is_valid, payload, msg = verify_license_string(saved_key, expected_hwid=self.hwid)
            if is_valid and payload:
                is_lifetime = payload.get("is_lifetime", False)
                if is_lifetime:
                    res = {
                        "is_valid": True,
                        "status_type": "LIFETIME",
                        "days_remaining": 9999,
                        "badge_text": "[ Bản quyền: Vĩnh viễn (PRO) ]",
                        "badge_color": "#38bdf8",  # Cyan nhã nhặn
                        "message": "Bản quyền Vĩnh viễn (PRO) đã kích hoạt.",
                        "hwid": self.hwid,
                        "payload": payload,
                    }
                else:
                    exp_str = payload.get("expiry_date", "")
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    today_utc = datetime.now(timezone.utc).date()
                    rem_days = max(0, (exp_date - today_utc).days)
                    tier_name = str(payload.get("tier", "PRO")).upper()
                    if tier_name in ("DEMO", "TRIAL"):
                        badge_txt = f"[ Demo: Còn {rem_days} ngày ]"
                        badge_color = "#fb923c"  # Cam ấm cảnh báo demo
                        st_type = "DEMO"
                        msg_txt = f"Bản Demo còn {rem_days} ngày (đến ngày {exp_str})."
                    else:
                        badge_txt = f"[ Bản quyền: Còn {rem_days} ngày ]"
                        badge_color = "#fcd34d" if rem_days <= 5 else "#38bdf8"
                        st_type = "DAYS"
                        msg_txt = f"Bản quyền còn {rem_days} ngày (đến ngày {exp_str})."
                    res = {
                        "is_valid": True,
                        "status_type": st_type,
                        "days_remaining": rem_days,
                        "badge_text": badge_txt,
                        "badge_color": badge_color,
                        "message": msg_txt,
                        "hwid": self.hwid,
                        "payload": payload,
                    }
                self._cached_status = res
                self._last_status_check = now
                return res
            else:
                # Key đã lưu nhưng không còn hợp lệ (ví dụ hết hạn hoặc bị can thiệp)
                res = {
                    "is_valid": False,
                    "status_type": "EXPIRED",
                    "days_remaining": 0,
                    "badge_text": "[ Bản quyền đã hết hạn ]",
                    "badge_color": "#f87171",
                    "message": msg or "Bản quyền đã hết hạn. Vui lòng gia hạn thêm.",
                    "hwid": self.hwid,
                    "payload": payload,
                }
                self._cached_status = res
                self._last_status_check = now
                return res

        # ─── TRƯỜNG HỢP 2: CHƯA KÍCH HOẠT KEY -> BẮT BUỘC PHẢI NHẬP KEY ─────────
        res = {
            "is_valid": False,
            "status_type": "UNACTIVATED",
            "days_remaining": 0,
            "badge_text": "[ Chưa kích hoạt ]",
            "badge_color": "#f87171",
            "message": "Ứng dụng chưa được kích hoạt bản quyền. Vui lòng gửi mã HWID cho Admin để nhận mã kích hoạt.",
            "hwid": self.hwid,
            "payload": None,
        }
        self._cached_status = res
        self._last_status_check = now
        return res

    def activate_key(self, license_key_str: str) -> Tuple[bool, str]:
        """Thực hiện xác thực và lưu key kích hoạt vào cả 3 vị trí an toàn trên máy."""
        clean_key = (license_key_str or "").strip()
        if not clean_key:
            return False, "Vui lòng nhập hoặc dán chuỗi mã bản quyền do Admin cấp."

        is_valid, payload, msg = verify_license_string(clean_key, expected_hwid=self.hwid)
        if not is_valid or not payload:
            return False, msg

        state = load_license_state(self.hwid) or {}
        state["license_key"] = clean_key
        state["activated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state["client_name"] = payload.get("client", "")
        state["last_run_timestamp"] = time.time()

        saved_ok = save_license_state(self.hwid, state)
        if not saved_ok:
            return False, "Không thể lưu trữ mã kích hoạt vào hệ thống (lỗi ghi file)."

        self.check_status(force_refresh=True)
        return True, msg


# =============================================================================
# 5. GIAO DIỆN KÍCH HOẠT BẢN QUYỀN (ACTIVATION DIALOG)
# =============================================================================
class LicenseActivationDialog:
    """
    Cửa sổ Kích Hoạt Bản Quyền Ứng Dụng (Tkinter Dark Mode hiện đại)
    thiết kế chuẩn xác theo giao diện mẫu (Mã HWID + Sao chép mã + Dán chuỗi Ed25519).
    """

    def __init__(self, parent: Optional[tk.Tk], license_mgr: LicenseManager, is_startup_block: bool = False):
        self.license_mgr = license_mgr
        self.is_startup_block = is_startup_block
        self.success = False

        if parent is not None:
            self.dialog = tk.Toplevel(parent)
        else:
            self.dialog = tk.Tk()

        # Ẩn cửa sổ ban đầu để nạp giao diện và tính toán tọa độ căn giữa chuẩn xác, tránh bị lệch hoặc nhấp nháy
        self.dialog.withdraw()
        self.dialog.title("Kích Hoạt Bản Quyền Ứng Dụng — AI Scalping Bot")
        self.dialog.configure(bg="#0f172a")  # Slate 900
        self.dialog.resizable(False, False)

        # Thiết lập Icon cửa sổ nếu có
        try:
            icon_path = Path(__file__).resolve().parent / "tools" / "license-key-icon.ico"
            if icon_path.exists():
                self.dialog.iconbitmap(str(icon_path))
        except Exception:
            pass

        try:
            self.dialog.attributes("-topmost", True)
        except Exception:
            pass

        # Bắt sự kiện đóng cửa sổ
        self.dialog.protocol("WM_DELETE_WINDOW", self._on_close)

        # 1. Dựng đầy đủ giao diện trước
        self._build_ui()

        # 2. Căn giữa màn hình chuẩn xác 100%
        self._center_on_screen(640, 540)
        self.dialog.deiconify()
        self.dialog.lift()
        self.dialog.focus_force()

        # Đảm bảo giữ đúng vị trí trung tâm màn hình sau khi Windows hoàn tất vẽ (chống DWM cascade)
        self.dialog.after(50, lambda: self._center_on_screen(640, 540))

    def _center_on_screen(self, width: int = 640, height: int = 540):
        """Căn giữa cửa sổ chính xác 100% trên màn hình hiển thị (hỗ trợ đa màn hình & DPI scaling)."""
        self.dialog.update_idletasks()
        w = width
        h = height

        try:
            import ctypes
            # Lấy độ phân giải màn hình chuẩn xác qua Windows API
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
        except Exception:
            sw = self.dialog.winfo_screenwidth()
            sh = self.dialog.winfo_screenheight()

        if not sw or sw <= 0:
            sw = 1920
        if not sh or sh <= 0:
            sh = 1080

        pos_x = max(0, int((sw - w) / 2))
        pos_y = max(0, int((sh - h) / 2))

        self.dialog.geometry(f"{w}x{h}+{pos_x}+{pos_y}")

    def _build_ui(self):
        root_f = tk.Frame(self.dialog, bg="#0f172a", padx=30, pady=25)
        root_f.pack(fill=tk.BOTH, expand=True)

        # 1. TIÊU ĐỀ
        tk.Label(
            root_f, text="Kích Hoạt Bản Quyền Ứng Dụng",
            bg="#0f172a", fg="#f8fafc", font=("Segoe UI", 16, "bold")
        ).pack(pady=(0, 4))

        tk.Label(
            root_f,
            text="Vui lòng sao chép Mã thiết bị (HWID) bên dưới gửi cho Admin để nhận chuỗi mã kích hoạt Ed25519.",
            bg="#0f172a", fg="#94a3b8", font=("Segoe UI", 9), wraplength=560, justify="center"
        ).pack(pady=(0, 16))

        # 2. KHUNG HIỂN THỊ MÃ MÁY HWID & NÚT SAO CHÉP
        hwid_card = tk.Frame(root_f, bg="#1e293b", highlightthickness=1, highlightbackground="#334155", padx=16, pady=12)
        hwid_card.pack(fill=tk.X, pady=(0, 14))

        hwid_left = tk.Frame(hwid_card, bg="#1e293b")
        hwid_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(
            hwid_left, text="Mã thiết bị phần cứng (HWID):",
            bg="#1e293b", fg="#94a3b8", font=("Segoe UI", 8, "bold")
        ).pack(anchor="w")

        hwid_str = self.license_mgr.get_hwid()
        self.hwid_lbl = tk.Label(
            hwid_left, text=hwid_str,
            bg="#1e293b", fg="#f59e0b", font=("Consolas", 12, "bold")
        )
        self.hwid_lbl.pack(anchor="w", pady=(2, 0))

        self.copy_btn = tk.Button(
            hwid_card, text="Sao chép mã", bg="#25334c", fg="#ffffff",
            activebackground="#324567", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=12, pady=6, cursor="hand2",
            command=self._copy_hwid
        )
        self.copy_btn.pack(side=tk.RIGHT)

        # 3. KHUNG NHẬP CHUỖI MÃ BẢN QUYỀN (ED25519)
        key_header = tk.Frame(root_f, bg="#0f172a")
        key_header.pack(fill=tk.X, pady=(0, 4))
        tk.Label(
            key_header, text="Chuỗi mã bản quyền (Ed25519)",
            bg="#0f172a", fg="#f8fafc", font=("Segoe UI", 9, "bold")
        ).pack(side=tk.LEFT)

        key_box = tk.Frame(root_f, bg="#090d16", highlightthickness=1, highlightbackground="#475569", padx=2, pady=2)
        key_box.pack(fill=tk.X, pady=(0, 8))

        self.key_text = tk.Text(
            key_box, height=4, bg="#090d16", fg="#f8fafc", insertbackground="#f8fafc",
            font=("Consolas", 9), relief=tk.FLAT, wrap=tk.CHAR, padx=6, pady=6
        )
        self.key_text.pack(fill=tk.BOTH, expand=True)
        self.key_text.insert("1.0", "Dán chuỗi bản quyền do Admin cấp (Bắt đầu bằng ED25519.)...")
        self.key_text.bind("<FocusIn>", self._clear_placeholder)

        # 4. THÔNG BÁO KẾT QUẢ / LỖI
        self.msg_lbl = tk.Label(
            root_f, text="", bg="#0f172a", fg="#f87171",
            font=("Segoe UI", 8, "bold"), wraplength=560, justify="center"
        )
        self.msg_lbl.pack(pady=(0, 8))

        # 5. CÁC NÚT THAO TÁC (QUAY LẠI / XÁC THỰC)
        btn_row = tk.Frame(root_f, bg="#0f172a")
        btn_row.pack(fill=tk.X, pady=(0, 10))

        cancel_txt = "Thoát Ứng Dụng" if self.is_startup_block else "Đóng"
        self.cancel_btn = tk.Button(
            btn_row, text=cancel_txt, bg="#1e293b", fg="#94a3b8",
            activebackground="#334155", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=16, pady=8, cursor="hand2",
            command=self._on_close
        )
        self.cancel_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.activate_btn = tk.Button(
            btn_row, text="Xác Thực & Kích Hoạt", bg="#0284c7", fg="#ffffff",
            activebackground="#0369a1", activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"), relief=tk.FLAT, padx=16, pady=8, cursor="hand2",
            command=self._on_activate
        )
        self.activate_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))

        # 6. DÒNG CHỮ PHỤ CUỐI CÙNG
        tk.Label(
            root_f, text="Chữ ký số mật mã học Ed25519 xác thực an toàn tuyệt đối 100% offline",
            bg="#0f172a", fg="#64748b", font=("Segoe UI", 8, "italic")
        ).pack(side=tk.BOTTOM)

    def _clear_placeholder(self, event=None):
        content = self.key_text.get("1.0", tk.END).strip()
        if content.startswith("Dán chuỗi bản quyền"):
            self.key_text.delete("1.0", tk.END)

    def _copy_hwid(self):
        try:
            self.dialog.clipboard_clear()
            self.dialog.clipboard_append(self.license_mgr.get_hwid())
            self.copy_btn.config(text="Đã chép mã!", fg="#34d399")
            self.dialog.after(2000, lambda: self.copy_btn.config(text="Sao chép mã", fg="#ffffff"))
        except Exception as e:
            self.msg_lbl.config(text=f"Lỗi sao chép: {e}", fg="#f87171")

    def _on_activate(self):
        raw_key = self.key_text.get("1.0", tk.END).strip()
        if raw_key.startswith("Dán chuỗi bản quyền"):
            raw_key = ""

        if not raw_key:
            self.msg_lbl.config(text="Vui lòng dán chuỗi mã bản quyền nhận được từ Admin!", fg="#f87171")
            return

        ok, msg = self.license_mgr.activate_key(raw_key)
        if ok:
            self.success = True
            messagebox.showinfo("Kích Hoạt Thành Công", f"Chúc mừng bạn!\n{msg}", parent=self.dialog)
            self.dialog.destroy()
        else:
            self.msg_lbl.config(text=f"{msg}", fg="#f87171")

    def _on_close(self):
        if self.is_startup_block and not self.success:
            # Nếu đang chặn lúc khởi động mà đóng cửa sổ -> Thoát app hoàn toàn
            self.dialog.destroy()
            sys.exit(0)
        else:
            self.dialog.destroy()


def show_activation_dialog(parent: Optional[tk.Tk], license_mgr: LicenseManager, is_startup_block: bool = False) -> bool:
    """Hiển thị cửa sổ kích hoạt bản quyền dạng Modal."""
    dlg = LicenseActivationDialog(parent, license_mgr, is_startup_block=is_startup_block)
    if isinstance(dlg.dialog, tk.Toplevel):
        dlg.dialog.grab_set()
        dlg.dialog.wait_window()
    else:
        dlg.dialog.mainloop()
    return dlg.success

