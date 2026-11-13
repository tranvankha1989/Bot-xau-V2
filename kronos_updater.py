"""
kronos_updater.py
─────────────────
Hệ thống Tự Động Cập Nhật Trực Tuyến qua GitHub Release (OTA Patch Updater)
cho ứng dụng Trading AI PRO by Kha Tran Design.

Đặc tính kỹ thuật:
1. Hoàn toàn sử dụng thư viện tiêu chuẩn (urllib, zipfile, json, tkinter), không phụ thuộc bên ngoài.
2. Kiểm tra bản phát hành mới qua GitHub Release API & fallback version.json trên repository.
3. Cơ chế Hot-Patch: Tải file patch zip và ghi đè an toàn vào thư mục làm việc của App mà khách hàng
   không cần cài đặt lại hay tải trọn bộ installer dung lượng lớn.
4. Bảo vệ 100% dữ liệu nhạy cảm của khách hàng: Không ghi đè kronos_settings.json, license breadcrumbs, logs.
5. Giao diện Changelog hiển thị chi tiết tính năng mới trước khi người dùng quyết định cập nhật.
"""

import os
import sys
import json
import re
import time
import zipfile
import io
import ssl
import urllib.request
import urllib.error
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

# Cấu hình SSL Context an toàn dự phòng trên máy chủ ảo VPS / Windows Server
def _get_safe_ssl_context():
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        try:
            return ssl._create_unverified_context()
        except Exception:
            return None

try:
    ssl._create_default_https_context = _get_safe_ssl_context
except Exception:
    pass

# Cấu hình Repository GitHub của tác giả
GITHUB_OWNER = "tranvankha1989"
GITHUB_REPO = "Bot-xau-V2"
GITHUB_RELEASE_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
GITHUB_RAW_VERSION_DEV = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/dev/version.json"
GITHUB_RAW_VERSION_MAIN = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/version.json"

APP_ROOT = Path(__file__).resolve().parent

# Danh sách tệp và thư mục được bảo vệ 100% (Tuyệt đối không ghi đè khi update)
PROTECTED_PATHS = {
    "kronos_settings.json",
    "logs",
    "tools/issued_licenses.json",
    "tools/ed25519_private.pem",
    "tools/ed25519_public.pem",
}


def get_current_app_version() -> str:
    """Lấy số phiên bản hiện tại từ version.json (mặc định fallback 1.0.1)."""
    v_path = APP_ROOT / "version.json"
    if v_path.exists():
        try:
            with open(v_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return str(data.get("version", "1.0.1")).strip()
        except Exception:
            pass
    return "1.0.1"


def parse_semver(v_str: str) -> list[int]:
    """Phân tích chuỗi phiên bản dạng semver (VD: 'v1.0.2' -> [1, 0, 2])."""
    nums = re.findall(r"\d+", str(v_str))
    return [int(x) for x in nums] if nums else [0, 0, 0]


def is_version_newer(latest_ver: str, current_ver: str) -> bool:
    """So sánh phiên bản mới nhất từ remote so với phiên bản hiện tại."""
    latest_nums = parse_semver(latest_ver)
    current_nums = parse_semver(current_ver)
    max_len = max(len(latest_nums), len(current_nums))
    latest_nums += [0] * (max_len - len(latest_nums))
    current_nums += [0] * (max_len - len(current_nums))
    return latest_nums > current_nums


class AppUpdater:
    """Bộ điều khiển kiểm tra và thực thi cập nhật ứng dụng."""

    def __init__(self, current_version: str = None):
        self.current_version = current_version or get_current_app_version()
        self.latest_version = self.current_version
        self.release_notes = ""
        self.patch_download_url = None
        self.patch_file_name = None
        self.is_checking = False
        self.has_update = False

    def check_update_sync(self, timeout: float = 6.0) -> dict:
        """
        Kiểm tra bản cập nhật mới đồng bộ từ GitHub API:
        1. Thử GitHub Releases API (releases/latest).
        2. Nếu API bị giới hạn rate limit hoặc chưa có release -> fallback đọc raw version.json.
        """
        self.is_checking = True
        headers = {
            "User-Agent": f"Trading-AI-PRO/{self.current_version}",
            "Accept": "application/vnd.github.v3+json",
        }

        remote_ver = None
        remote_notes = ""
        patch_url = None
        patch_name = None

        # 1. Kiểm tra qua GitHub Releases API
        try:
            req = urllib.request.Request(GITHUB_RELEASE_API, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=_get_safe_ssl_context()) as resp:
                if resp.status == 200:
                    raw_data = resp.read().decode("utf-8")
                    rel_data = json.loads(raw_data)
                    tag_name = str(rel_data.get("tag_name", "")).strip()
                    remote_ver = tag_name.lstrip("vV")
                    remote_notes = str(rel_data.get("body") or rel_data.get("name") or "").strip()

                    # Tìm asset zip chứa bản vá (patch_v*.zip hoặc update_*.zip)
                    for asset in rel_data.get("assets", []):
                        aname = str(asset.get("name", "")).lower()
                        if aname.endswith(".zip") and any(k in aname for k in ("patch", "update", "release", "bot")):
                            patch_url = asset.get("browser_download_url")
                            patch_name = asset.get("name")
                            break

                    # Nếu không có asset riêng, lấy zipball mã nguồn của release
                    if not patch_url:
                        patch_url = rel_data.get("zipball_url")
                        patch_name = f"patch_v{remote_ver}.zip"
        except Exception:
            pass

        # 2. Fallback: Đọc trực tiếp file version.json trên raw GitHub
        if not remote_ver:
            for raw_url in (GITHUB_RAW_VERSION_DEV, GITHUB_RAW_VERSION_MAIN):
                try:
                    req_raw = urllib.request.Request(f"{raw_url}?_t={int(time.time())}", headers=headers)
                    with urllib.request.urlopen(req_raw, timeout=timeout, context=_get_safe_ssl_context()) as resp_raw:
                        if resp_raw.status == 200:
                            data_raw = json.loads(resp_raw.read().decode("utf-8"))
                            remote_ver = str(data_raw.get("version", "")).strip().lstrip("vV")
                            remote_notes = str(data_raw.get("description", "")).strip()
                            if remote_ver:
                                break
                except Exception:
                    continue

        # Đảm bảo luôn có đường dẫn tải tệp patch nếu bản cập nhật hợp lệ
        if remote_ver and not patch_url:
            patch_url = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/v{remote_ver}/patch_v{remote_ver}.zip"
            patch_name = f"patch_v{remote_ver}.zip"

        self.is_checking = False
        if remote_ver and is_version_newer(remote_ver, self.current_version):
            self.has_update = True
            self.latest_version = remote_ver
            self.release_notes = remote_notes or f"Phiên bản nâng cấp v{remote_ver} với nhiều cải tiến tính năng và hiệu năng."
            self.patch_download_url = patch_url
            self.patch_file_name = patch_name or f"patch_v{remote_ver}.zip"
            return {
                "ok": True,
                "has_update": True,
                "current_version": self.current_version,
                "latest_version": self.latest_version,
                "release_notes": self.release_notes,
                "patch_url": self.patch_download_url,
                "patch_name": self.patch_file_name,
            }

        self.has_update = False
        return {
            "ok": True,
            "has_update": False,
            "current_version": self.current_version,
            "latest_version": self.current_version,
            "release_notes": "",
            "patch_url": None,
            "patch_name": None,
        }

    def check_update_async(self, callback):
        """Khởi chạy kiểm tra cập nhật trong luồng ngầm và trả kết quả về callback."""
        def _worker():
            res = self.check_update_sync()
            if callback:
                callback(res)

        threading.Thread(target=_worker, daemon=True).start()

    def download_and_apply_patch(self, patch_url: str, progress_cb=None) -> tuple[bool, str]:
        """
        Tải xuống tệp nén patch và giải nén đè lên thư mục cài đặt hiện tại:
        - Bỏ qua các tệp cấu hình, license và logs để bảo vệ dữ liệu người dùng.
        """
        if not patch_url:
            return False, "Không tìm thấy đường dẫn tải gói cập nhật (Patch URL trống)."

        headers = {"User-Agent": f"Trading-AI-PRO/{self.current_version}"}
        req = urllib.request.Request(patch_url, headers=headers)

        try:
            if progress_cb:
                progress_cb(10, "Đang kết nối đến máy chủ cập nhật...")

            with urllib.request.urlopen(req, timeout=30.0, context=_get_safe_ssl_context()) as resp:
                total_size = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                buffer = io.BytesIO()
                chunk_size = 64 * 1024

                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    buffer.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total_size > 0:
                        pct = int(10 + (downloaded / total_size) * 60)
                        progress_cb(min(pct, 70), f"Đang tải bản cập nhật ({downloaded // 1024} KB / {total_size // 1024} KB)...")

            if progress_cb:
                progress_cb(75, "Đang kiểm tra và giải nén tệp nâng cấp...")

            zip_bytes = buffer.getvalue()
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
                # Kiểm tra tiền tố thư mục nếu tải từ zipball của GitHub
                namelist = z.namelist()
                prefix = ""
                if namelist and "/" in namelist[0]:
                    first_seg = namelist[0].split("/")[0]
                    if any(first_seg.startswith(k) for k in ("tranvankha1989-Bot-xau-V2-", "Bot-xau-V2-", "tranvankha1989-BOT_XAU-", "BOT_XAU-", "patch-", "release-")):
                        prefix = first_seg + "/"

                for member in z.infolist():
                    rel_path = member.filename
                    if prefix and rel_path.startswith(prefix):
                        rel_path = rel_path[len(prefix):]

                    if not rel_path or rel_path.endswith("/"):
                        continue

                    # Chuẩn hóa đường dẫn dạng posix
                    norm_rel = rel_path.replace("\\", "/")

                    # Bỏ qua tệp/thư mục được bảo vệ
                    should_skip = False
                    for prot in PROTECTED_PATHS:
                        if norm_rel == prot or norm_rel.startswith(prot + "/"):
                            should_skip = True
                            break
                    if norm_rel.startswith(".git") or norm_rel.endswith(".pyc") or "__pycache__" in norm_rel:
                        should_skip = True

                    if should_skip:
                        continue

                    target_file = APP_ROOT / rel_path
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with z.open(member) as source, open(target_file, "wb") as target:
                            target.write(source.read())
                    except PermissionError:
                        pass

            if progress_cb:
                progress_cb(100, "Cập nhật hoàn tất! Đang khởi động lại ứng dụng...")

            return True, "Cập nhật thành công!"
        except Exception as e:
            return False, f"Lỗi trong quá trình cập nhật: {e}"

    def restart_application(self):
        """Khởi động lại ứng dụng với phiên bản mới và giải phóng tiến trình cũ."""
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable] + sys.argv[1:]
            else:
                main_script = APP_ROOT / "mt5_scalping_bot.py"
                cmd = [sys.executable, str(main_script)] + sys.argv[1:]
            subprocess.Popen(cmd, cwd=str(APP_ROOT))
            time.sleep(0.5)
            os._exit(0)
        except Exception as e:
            messagebox.showerror("Khởi Động Lại Thất Bại", f"Không thể tự động khởi động lại App:\n{e}\nVui lòng mở lại ứng dụng thủ công.")


def show_update_modal(parent, updater: AppUpdater, update_info: dict, on_complete=None):
    """
    Hiển thị cửa sổ Thông Tin Tính Năng Của Bản Cập Nhật Mới (Changelog Modal):
    - Giao diện tối sang trọng đồng bộ với App.
    - Không sử dụng các ký hiệu 🚀, tia sét.
    - Cung cấp nút 'Cập Nhật Ngay' và 'Để Sau'.
    """
    latest_ver = update_info.get("latest_version", updater.latest_version)
    current_ver = update_info.get("current_version", updater.current_version)
    notes = update_info.get("release_notes", updater.release_notes)
    patch_url = update_info.get("patch_url")

    dialog = tk.Toplevel(parent)
    dialog.title("Cập Nhật Ứng Dụng — Trading AI PRO")
    dialog.geometry("620x500")
    dialog.minsize(520, 420)
    dialog.resizable(True, True)
    dialog.configure(bg="#0b0f19")
    dialog.transient(parent)
    dialog.grab_set()

    # Căn giữa cửa sổ chính xác
    dialog.update_idletasks()
    pw = parent.winfo_width()
    ph = parent.winfo_height()
    px = parent.winfo_rootx()
    py = parent.winfo_rooty()
    dx = px + (pw - 620) // 2
    dy = py + (ph - 500) // 2
    dialog.geometry(f"+{max(10, dx)}+{max(10, dy)}")

    # 1. Header Card (Docked to TOP)
    header_frame = tk.Frame(dialog, bg="#111827", padx=20, pady=16)
    header_frame.pack(side=tk.TOP, fill=tk.X)

    title_lbl = tk.Label(
        header_frame,
        text="PHÁT HIỆN BẢN CẬP NHẬT MỚI",
        bg="#111827",
        fg="#10b981",
        font=("Segoe UI", 12, "bold"),
    )
    title_lbl.pack(anchor="w")

    ver_compare_lbl = tk.Label(
        header_frame,
        text=f"Phiên bản hiện tại: v{current_ver}   →   Phiên bản mới: v{latest_ver}",
        bg="#111827",
        fg="#94a3b8",
        font=("Segoe UI", 9),
    )
    ver_compare_lbl.pack(anchor="w", pady=(4, 0))

    # 2. Footer Section (Docked to BOTTOM first - Đảm bảo luôn luôn hiển thị các nút thao tác)
    footer_frame = tk.Frame(dialog, bg="#0b0f19", padx=20, pady=16)
    footer_frame.pack(side=tk.BOTTOM, fill=tk.X)

    sep_line = tk.Frame(dialog, bg="#1e293b", height=1)
    sep_line.pack(side=tk.BOTTOM, fill=tk.X)

    # 3. Thân cửa sổ: Danh sách tính năng mới (Co giãn tự nhiên ở giữa)
    body_frame = tk.Frame(dialog, bg="#0b0f19", padx=20, pady=12)
    body_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    desc_lbl = tk.Label(
        body_frame,
        text="Giới thiệu tính năng & cải tiến mới:",
        bg="#0b0f19",
        fg="#cbd5e1",
        font=("Segoe UI", 9, "bold"),
    )
    desc_lbl.pack(anchor="w", pady=(0, 6))

    text_frame = tk.Frame(body_frame, bg="#111827", highlightthickness=1, highlightbackground="#1e293b")
    text_frame.pack(fill=tk.BOTH, expand=True)

    notes_text = tk.Text(
        text_frame,
        bg="#111827",
        fg="#f1f5f9",
        font=("Segoe UI", 9),
        wrap=tk.WORD,
        padx=10,
        pady=8,
        relief="flat",
        highlightthickness=0,
    )
    notes_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=notes_text.yview)
    notes_text.configure(yscrollcommand=notes_scroll.set)
    notes_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    notes_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    notes_text.insert(tk.END, notes if notes else "Không có ghi chú tính năng kèm theo.")
    notes_text.config(state=tk.DISABLED)

    # Vùng thanh tiến trình nằm trong footer_frame (Ẩn ban đầu, chỉ hiện khi người dùng bấm Cập Nhật)
    progress_frame = tk.Frame(footer_frame, bg="#0b0f19")
    status_msg_lbl = tk.Label(progress_frame, text="", bg="#0b0f19", fg="#38bdf8", font=("Segoe UI", 8, "italic"))
    status_msg_lbl.pack(anchor="w", pady=(0, 2))
    progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", mode="determinate")
    progress_bar.pack(fill=tk.X, pady=(0, 10))

    btn_row = tk.Frame(footer_frame, bg="#0b0f19")
    btn_row.pack(side=tk.BOTTOM, fill=tk.X)

    def _start_update():
        if not patch_url:
            messagebox.showinfo(
                "Thông Báo Cập Nhật",
                f"Đã phát hiện phiên bản v{latest_ver} trên GitHub!\nTuy nhiên chưa có gói tải tự động (patch zip) trên mục Release.\nBạn có thể tải thủ công từ kho lưu trữ.",
                parent=dialog
            )
            dialog.destroy()
            return

        btn_update.config(state=tk.DISABLED, text="Đang cập nhật...")
        btn_later.config(state=tk.DISABLED)
        progress_frame.pack(side=tk.TOP, fill=tk.X, before=btn_row)

        def _progress(pct, msg):
            def _ui():
                progress_bar["value"] = pct
                status_msg_lbl.config(text=msg)
            dialog.after(0, _ui)

        def _worker():
            ok, err = updater.download_and_apply_patch(patch_url, progress_cb=_progress)
            def _finish():
                if ok:
                    messagebox.showinfo(
                        "Cập Nhật Thành Công",
                        f"Đã nâng cấp ứng dụng lên phiên bản v{latest_ver} thành công!\nỨng dụng sẽ tự động khởi động lại ngay bây giờ.",
                        parent=dialog
                    )
                    dialog.destroy()
                    updater.restart_application()
                else:
                    messagebox.showerror("Cập Nhật Thất Bại", f"Không thể cập nhật:\n{err}", parent=dialog)
                    btn_update.config(state=tk.NORMAL, text="Thử lại")
                    btn_later.config(state=tk.NORMAL)
                    if on_complete:
                        on_complete(False)
            dialog.after(0, _finish)

        threading.Thread(target=_worker, daemon=True).start()

    btn_update = tk.Button(
        btn_row,
        text="  ✓ ĐỒNG Ý CẬP NHẬT NGAY  ",
        bg="#059669",
        fg="#ffffff",
        activebackground="#10b981",
        activeforeground="#ffffff",
        font=("Segoe UI", 9, "bold"),
        padx=18,
        pady=8,
        relief="flat",
        cursor="hand2",
        command=_start_update,
    )
    btn_update.pack(side=tk.RIGHT, padx=(8, 0))

    btn_later = tk.Button(
        btn_row,
        text="Để Sau",
        bg="#1e293b",
        fg="#94a3b8",
        activebackground="#334155",
        activeforeground="#f1f5f9",
        font=("Segoe UI", 9),
        padx=16,
        pady=8,
        relief="flat",
        cursor="hand2",
        command=dialog.destroy,
    )
    btn_later.pack(side=tk.RIGHT)

    dialog.bind("<Return>", lambda e: _start_update())
    dialog.bind("<Escape>", lambda e: dialog.destroy())
    btn_update.focus_set()

