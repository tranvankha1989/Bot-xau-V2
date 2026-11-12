"""
tools/publish_release.py
────────────────────────
Tự động tạo Release và Upload file patch_v*.zip lên GitHub Release
chuẩn cơ chế cập nhật của ứng dụng (giống kiến trúc TSS).
"""

import os
import sys
import json
import urllib.request
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

# 1. Đọc phiên bản từ version.json
version_file = ROOT_DIR / "version.json"
if not version_file.exists():
    print(f"[ERROR] Không tìm thấy {version_file}")
    sys.exit(1)

with open(version_file, "r", encoding="utf-8") as f:
    ver_data = json.load(f)

version = ver_data.get("version", "1.0.2")
tag = f"v{version}"
desc = ver_data.get("description", f"Bản phát hành {tag}")
title = f"Trading AI PRO {tag}"

# 2. Lấy GitHub Token từ Windows Git Credential Manager
res = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    text=True,
    capture_output=True
)

token = None
for line in res.stdout.splitlines():
    if line.startswith("password="):
        token = line.split("=", 1)[1].strip()
        break

if not token:
    print("[ERROR] Không tìm thấy GitHub Token trong Git Credential Manager.")
    sys.exit(1)

owner = "tranvankha1989"
repo = "Bot-xau-V2"

headers = {
    "Authorization": f"token {token}",
    "User-Agent": "Trading-AI-PRO",
    "Accept": "application/vnd.github.v3+json",
}

print(f"[*] Đang kết nối GitHub: {owner}/{repo} (Tag: {tag})...")

# 3. Kiểm tra xem Release đã tồn tại chưa
rel_url = f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
req = urllib.request.Request(rel_url, headers=headers)
rel_id = None
upload_url = None

try:
    with urllib.request.urlopen(req) as resp:
        if resp.status == 200:
            rel_info = json.loads(resp.read().decode())
            rel_id = rel_info["id"]
            upload_url = rel_info["upload_url"].split("{")[0]
            print(f"[OK] Đã tìm thấy Release {tag} (ID: {rel_id})")
except Exception:
    pass

# Tạo mới Release nếu chưa có
if not rel_id:
    print(f"[*] Đang tạo Release mới '{title}' trên GitHub...")
    create_url = f"https://api.github.com/repos/{owner}/{repo}/releases"
    payload = {
        "tag_name": tag,
        "target_commitish": "main",
        "name": title,
        "body": desc,
        "draft": False,
        "prerelease": False,
    }
    req_create = urllib.request.Request(
        create_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST"
    )
    with urllib.request.urlopen(req_create) as resp:
        rel_info = json.loads(resp.read().decode())
        rel_id = rel_info["id"]
        upload_url = rel_info["upload_url"].split("{")[0]
        print(f"[OK] Đã tạo thành công Release {tag} (ID: {rel_id})")

# 4. Upload tệp patch_v*.zip vào Release
patch_file = ROOT_DIR / "installer_output" / f"patch_{tag}.zip"
if not patch_file.exists():
    print(f"[ERROR] Không tìm thấy file patch: {patch_file}")
    sys.exit(1)

file_bytes = patch_file.read_bytes()
file_size_kb = round(len(file_bytes) / 1024, 1)

# Kiểm tra nếu asset đã có thì xóa bản cũ đi trước
assets_list_url = f"https://api.github.com/repos/{owner}/{repo}/releases/{rel_id}/assets"
req_assets = urllib.request.Request(assets_list_url, headers=headers)
with urllib.request.urlopen(req_assets) as resp:
    existing_assets = json.loads(resp.read().decode())
    for a in existing_assets:
        if a["name"] == patch_file.name:
            del_url = a["url"]
            del_req = urllib.request.Request(del_url, headers=headers, method="DELETE")
            urllib.request.urlopen(del_req)
            print(f"[*] Đã xóa asset cũ: {a['name']}")
            break

print(f"[*] Đang tải lên {patch_file.name} ({file_size_kb} KB) vào Release...")
asset_upload_url = f"{upload_url}?name={patch_file.name}"
asset_headers = {
    "Authorization": f"token {token}",
    "User-Agent": "Trading-AI-PRO",
    "Content-Type": "application/zip",
    "Content-Length": str(len(file_bytes)),
}

req_upload = urllib.request.Request(
    asset_upload_url,
    data=file_bytes,
    headers=asset_headers,
    method="POST"
)

with urllib.request.urlopen(req_upload) as resp:
    res_asset = json.loads(resp.read().decode())
    print("")
    print("====================================================================")
    print(f" [OK] ĐÃ ĐẨY LÊN MỤC RELEASE THÀNH CÔNG!")
    print(f" Tên tệp       : {res_asset['name']}")
    print(f" Dung lượng    : {round(res_asset['size'] / 1024, 1)} KB")
    print(f" Đường dẫn tải : {res_asset['browser_download_url']}")
    print(f" Release URL   : https://github.com/{owner}/{repo}/releases/tag/{tag}")
    print("====================================================================")

