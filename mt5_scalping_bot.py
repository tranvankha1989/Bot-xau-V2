import os
import sys
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import json
import math
import time
import queue
import shutil
import ctypes
import traceback
import threading
import re
from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
from matplotlib.collections import LineCollection, PolyCollection

from model import Kronos, KronosTokenizer, KronosPredictor
from model.kronos import calc_time_stamps, sample_from_logits

# Thử import MetaTrader5
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False

# Thử import gradio_client
try:
    from gradio_client import Client
    HF_CLIENT_AVAILABLE = True
except ImportError:
    HF_CLIENT_AVAILABLE = False

# Múi giờ Việt Nam (UTC+7 / GMT+7) cố định để tính PnL và reset giới hạn ngày (không phụ thuộc giờ OS máy hay giờ Server MT5)
VN_TZ = timezone(timedelta(hours=7))

# Xác định định danh phiên làm việc (Instance Tag) theo thư mục để cho phép mở song song nhiều App độc lập
APP_DIR = os.path.dirname(os.path.abspath(__file__))
_dir_basename = re.sub(r'[^a-zA-Z0-9_]', '_', os.path.basename(APP_DIR).strip()).strip("_")
INSTANCE_ID = _dir_basename if _dir_basename.lower() not in ("kronos", "kronos_mt5_bot", "") else ""

# Đường dẫn lưu cấu hình người dùng, Nhật ký (Logs) & Icon trong AppData (tách biệt theo Instance để không ghi đè cài đặt của nhau)
if INSTANCE_ID:
    SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), f"Kronos_MT5_Bot_{INSTANCE_ID}")
else:
    SETTINGS_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Kronos_MT5_Bot")

SETTINGS_FILE = os.path.join(SETTINGS_DIR, "user_settings.json")
INSTANCE_LOCK_FILE = os.path.join(SETTINGS_DIR, "app_instance.json")
LOG_DIR = os.path.join(SETTINGS_DIR, "logs")
ERROR_LOG_FILE = os.path.join(LOG_DIR, "app_error.log")
ICON_ICO_FILE = os.path.join(SETTINGS_DIR, "khatran_icon.ico")
ICON_PNG_FILE = os.path.join(SETTINGS_DIR, "khatran_icon.png")
_SINGLETON_MUTEX_HANDLE = None

try:
    _app_user_id = f"KhaTran.Kronos.ScalpingBot.M5.v2.{INSTANCE_ID}" if INSTANCE_ID else "KhaTran.Kronos.ScalpingBot.M5.v2"
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_app_user_id)
except Exception:
    pass

def _init_instance_environment():
    """Khởi tạo môi trường riêng cho Instance: sao chép cài đặt gốc (nếu có) và đổi Magic Number để chống xung đột lệnh MT5."""
    try:
        os.makedirs(SETTINGS_DIR, exist_ok=True)
        os.makedirs(LOG_DIR, exist_ok=True)
        primary_dir = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Kronos_MT5_Bot")

        for icon_name in ("khatran_icon.ico", "khatran_icon.png"):
            dest_icon = os.path.join(SETTINGS_DIR, icon_name)
            src_icon = os.path.join(primary_dir, icon_name)
            if not os.path.exists(dest_icon) and os.path.exists(src_icon):
                try:
                    shutil.copy2(src_icon, dest_icon)
                except Exception:
                    pass

        if INSTANCE_ID and not os.path.exists(SETTINGS_FILE):
            primary_settings = os.path.join(primary_dir, "user_settings.json")
            if os.path.exists(primary_settings):
                try:
                    with open(primary_settings, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data.get("profiles"), dict):
                        for pk, pdata in data["profiles"].items():
                            if isinstance(pdata, dict):
                                m_val = str(pdata.get("magic_number", "")).strip()
                                if pk == "XAUUSD" and m_val == "20260501":
                                    pdata["magic_number"] = "20260601"
                    if str(data.get("magic_number", "")) == "20260501":
                        data["magic_number"] = "20260601"
                    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
    except Exception:
        pass

_init_instance_environment()

SYMBOL_XAU = "XAUUSDm"

MODEL_CONFIGS = {
    "AI-Gold-Exness (Chuyên sâu Exness)": {
        "model": "Kronos-Gold-Exness",
        "tokenizer": "Kronos-Tokenizer-Gold-Exness",
        "fallback_model": "NeoQuasar/Kronos-base",
        "fallback_tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
        "is_custom": True,
    },
    "AI-base (102.3M - Mạnh nhất)": {
        "model": "NeoQuasar/Kronos-base",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
        "is_custom": False,
    },
    "AI-small (24.7M - Nhanh)": {
        "model": "NeoQuasar/Kronos-small",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-base",
        "max_context": 512,
        "is_custom": False,
    },
    "AI-mini (4.1M - Siêu nhẹ)": {
        "model": "NeoQuasar/Kronos-mini",
        "tokenizer": "NeoQuasar/Kronos-Tokenizer-2k",
        "max_context": 2048,
        "is_custom": False,
    }
}

BG_DARK = "#10141d"
PANEL_BG = "#19202c"
SECTION_HEADER_BG = "#232d3f"
TEXT_MAIN = "#eaecef"
TEXT_MUTED = "#8b98a9"
GOLD_ACCENT = "#f0b90b"
GREEN_UP = "#0ecb81"
RED_DOWN = "#f6465d"
CYAN_LIVE = "#00e5ff"

DEFAULT_PROFILE_SETTINGS = {
    "XAUUSD": {
        "symbol": "XAUUSDm",
        "analysis_tf": "M15",
        "tf": "M5",
        "lot": "0.01",
        "max_pos": "3",
        "magic_number": "20260601" if INSTANCE_ID else "20260501",
        "scenarios": "8",
        "consensus": "75",
        "max_dispersion": "2.5",
        "min_move": "1.2",
        "max_spread": "0.35",
        "sl_atr": "1.5",
        "sl_buffer": "0.5",
        "max_sl_price": "5.0",
        "rr": "2.0",
        "min_entry_spacing": "1.0",
        "entry_mode": "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)",
        "one_order_per_candle": True,
        "auto_be": True,
        "be_trigger_r": "1.0",
        "auto_trail": True,
        "trail_dist_r": "0.35",
        "auto_rev_close": True,
        "rev_consensus": "75",
        "rev_min_move": "0",
        "be_plus_price": "0.2",
        "basket_tp_usd": "0",
        "basket_cooldown_mins": "15",
        "max_hold_mins": "0",
        "max_hold_action": "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)",
        "daily_tp_usd": "0",
        "daily_sl_usd": "0",
        "max_consec_loss": "0",
        "cooldown_mins": "30",
        "fast_sl_cooldown_mins": "15",
        "engine": "Chạy trực tiếp máy Local",
        "model": "AI-base (102.3M - Mạnh nhất)",
        "hf_space": "tranvankha2807/Kronos_Xau",
        "hf_token": "",
        "lookback": "450",
        "pred_len": "6",
        "realtime_chart": True,
    },
}

PROFILE_STATE_ATTRS = (
    "auto_loop_running",
    "is_analyzing",
    "last_candle_time",
    "last_recommendation",
    "pending_pullback_order",
    "daily_limit_locked_date",
    "daily_limit_reason",
    "cached_daily_pnl",
    "cached_win_rate",
    "cached_win_count",
    "cached_loss_count",
    "cached_closed_count",
    "consec_loss_count",
    "cooldown_until",
    "last_cooldown_deal_ticket",
    "basket_cooldown_until",
    "fast_sl_cooldown_until",
    "last_fast_sl_deal_ticket",
    "is_basket_closing",
    "cached_scenarios",
    "cached_mean_df",
    "cached_action",
    "cached_sl",
    "cached_tp",
    "cached_df_hist",
    "cached_df_analysis",
    "user_zoomed",
    "saved_xlim",
    "saved_ylim",
    "_last_detail_prefix",
    "_sig_title",
    "_sig_color",
)

# Các thuộc tính do luồng AI cập nhật trực tiếp vào profile_ctx (luồng UI 1 giây không được ghi đè ngược dữ liệu cũ lên)
AI_MANAGED_STATE_ATTRS = frozenset({
    "is_analyzing",
    "last_candle_time",
    "last_recommendation",
    "cached_scenarios",
    "cached_mean_df",
    "cached_action",
    "cached_sl",
    "cached_tp",
    "cached_df_hist",
    "cached_df_analysis",
    "_last_detail_prefix",
    "_sig_title",
    "_sig_color",
})


class CollapsibleSection(tk.Frame):
    """Mục giao diện có thể thu gọn / sổ xuống theo kiểu Accordion (mặc định thu gọn)."""
    def __init__(self, parent, title: str, initially_open: bool = False, group_list: list = None, on_reset: callable = None):
        super().__init__(parent, bg=PANEL_BG)
        self.pack(fill=tk.X, pady=2)
        self.title = title
        self.is_open = initially_open
        self.group_list = group_list
        self.on_reset = on_reset
        if self.group_list is not None:
            self.group_list.append(self)

        self.header_frame = tk.Frame(self, bg=SECTION_HEADER_BG)
        self.header_frame.pack(fill=tk.X)

        self.header_btn = tk.Button(
            self.header_frame,
            text=self._get_header_text(),
            bg=SECTION_HEADER_BG,
            fg=GOLD_ACCENT,
            activebackground="#2d3a52",
            activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            anchor="w",
            padx=8,
            pady=4,
            command=self.toggle
        )
        self.header_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Nút "↺ Mặc định" bên phải tiêu đề đã được ẩn theo yêu cầu; mỗi mục vẫn có nút khôi phục mặc định ở cuối mục.
        self.reset_btn = None

        self.content = tk.Frame(self, bg=PANEL_BG, padx=6, pady=3)
        if self.is_open:
            self.content.pack(fill=tk.X)

    def _get_header_text(self) -> str:
        arrow = "▾" if self.is_open else "▸"
        return f"{arrow}  {self.title}"

    def close_section(self):
        if self.is_open:
            self.is_open = False
            self.header_btn.config(text=self._get_header_text())
            self.content.pack_forget()

    def toggle(self):
        if not self.is_open and self.group_list is not None:
            for other in self.group_list:
                if other is not self:
                    other.close_section()
        self.is_open = not self.is_open
        self.header_btn.config(text=self._get_header_text())
        if self.is_open:
            self.content.pack(fill=tk.X)
        else:
            self.content.pack_forget()


class KronosDesktopApp:
    def __init__(self, root: tk.Tk, lic_mgr=None):
        self.root = root
        title_suffix = f" [{INSTANCE_ID}]" if INSTANCE_ID else ""
        self.root.title(f"Trading AI PRO{title_suffix} by Kha Tran Design — Scalping Vàng & Quản Lý Lệnh Real-Time (MT5 Exness)")
        self.root.geometry("1450x880")
        self.root.configure(bg=BG_DARK)
        self._apply_window_and_taskbar_icon()

        if lic_mgr is None:
            try:
                from kronos_license import LicenseManager
                self.lic_mgr = LicenseManager()
            except Exception:
                self.lic_mgr = None
        else:
            self.lic_mgr = lic_mgr

        try:
            from kronos_updater import AppUpdater, get_current_app_version
            self.app_version = get_current_app_version()
            self.updater = AppUpdater(self.app_version)
        except Exception:
            self.app_version = "1.0.1"
            self.updater = None

        self.local_cache = {}
        self.initial_r_cache = {}  # Lưu khoảng cách 1R (|Entry - SL ban đầu|) theo từng ticket
        self.pending_be_exit_tickets = set()  # Lưu các ticket đang âm bị AI báo đảo chiều -> chờ hồi về Entry thoát
        self.pending_pullback_order = None  # Lưu tín hiệu chờ giá hồi về <= SL tối đa (1 giá = 10 pip) mới vào lệnh
        self.daily_limit_locked_date = None  # Ngày (YYYY-MM-DD) đã chạm mục tiêu Lãi/Lỗ ngày -> Dừng giao dịch hết ngày
        self.daily_limit_reason = ""
        self.cached_daily_pnl = 0.0
        self.cached_win_rate = 0.0
        self.cached_win_count = 0
        self.cached_loss_count = 0
        self.cached_closed_count = 0
        self.consec_loss_count = 0  # Chuỗi lệnh thua liên tiếp hiện tại trong ngày
        self.cooldown_until = None  # Thời điểm hết hạn tạm ngừng sau chuỗi thua
        self.last_cooldown_deal_ticket = None
        self.basket_cooldown_until = None  # Thời điểm hết hạn tạm nghỉ sau khi chốt lời chùm lệnh
        self.fast_sl_cooldown_until = None  # Thời điểm hết hạn tạm ngừng khi có lệnh dính SL < 5 phút
        self.last_fast_sl_deal_ticket = None
        self.is_basket_closing = False  # Khóa bảo vệ chống đóng lặp nhiều luồng khi chốt chùm
        self.left_menu_visible = False  # Mặc định thu gọn vào
        self.drawer_width = 355
        self.drawer_x = -350
        self._drawer_anim_job = None
        self.auto_loop_running = False
        self.is_analyzing = False
        self.last_candle_time = None
        self.last_recommendation = None
        self.heartbeat_toggle = False
        self._loading_settings = False

        # Chế độ hiển thị biểu đồ: "LIVE_POSITIONS" (Tab 1) hoặc "AI_FORECAST" (Tab 2)
        self.chart_mode = "LIVE_POSITIONS"

        # Lưu trữ dữ liệu dự báo gần nhất
        self.cached_scenarios = None
        self.cached_mean_df = None
        self.cached_action = None
        self.cached_sl = None
        self.cached_tp = None
        self.cached_df_hist = None
        self.cached_df_analysis = None

        # Trạng thái tương tác biểu đồ kiểu TradingView (Zoom / Pan / Kéo thanh giá Y-Scale & X-Scale / Chuột phải Reset)
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        self._drag_start_px = None
        self._drag_start_xlim = None
        self._drag_start_ylim = None
        self._drag_mode = None  # "PAN", "Y_SCALE", hoặc "X_SCALE"
        self._chart_visible_bars = 170
        self._chart_hist_df = None
        self._chart_mean_df = None
        self._chart_all_times = []
        self._chart_n_hist = 0
        self._chart_total_x = 0
        self._pos_text_artists = []
        self._forming_wick_line = None
        self._forming_body_rect = None
        self._live_price_hline = None
        self._live_price_annot = None
        self._candle_countdown_annot = None
        self._last_drawn_chart_key = None
        self._last_drawn_pos_sig = None
        self._redraw_pending = False
        self._redraw_full_needed = True
        self._chart_bg_bbox = None
        self._last_deals_check_ts = 0.0
        self._cached_deals_stats = (0.0, 0, None)
        self._cached_deals_by_sym = {}
        self._ai_scan_lock = threading.Lock()
        self._mt5_lock = threading.RLock()
        self._log_file_lock = threading.Lock()
        self._order_guard_lock = threading.Lock()
        self._order_in_flight = {"XAUUSD": False}
        self._last_order_open_mono = {"XAUUSD": 0.0}
        self._last_order_candle_ts = {"XAUUSD": None}
        self._auto_worker_token = {"XAUUSD": 0}
        self._last_mt5_order_mono = 0.0
        self._ai_queue = queue.Queue()
        self._ai_queue_lock = threading.Lock()
        self._ai_queued_profiles = set()
        self._ai_current_running_pk = None
        self._app_closing = False
        self._sig_title = "⚪ ĐANG TẢI DỮ LIỆU & PHÂN TÍCH AI..."
        self._sig_color = GOLD_ACCENT
        self.active_profile_key = "XAUUSD"
        self.profile_settings = {
            "XAUUSD": dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"]),
        }
        self.chart_view_tf_by_profile = {
            "XAUUSD": "M5",
        }
        self.profile_ctx = {
            "XAUUSD": self._create_empty_profile_ctx("XAUUSD"),
        }
        self.pos_tree_by_profile = {}
        self.pos_summary_lbl_by_profile = {}
        self.cross_vline = None
        self.cross_hline = None
        self.cross_price_annot = None
        self.cross_time_annot = None
        self.cross_ohlc_text = None
        self._last_hover_xy = None

        # Trạng thái hiển thị % trên nút Reload Pill góc phải trên & đồng bộ trạng thái Bot
        self._scan_pct = 100.0
        self._scan_target_pct = 100.0
        self._scan_anim_job = None
        self._last_detail_prefix = None

        # Trạng thái cửa sổ kiểu MacBook (Traffic Light Minimize / Maximize / Close + Kéo thanh tiêu đề)
        self._win_maximized = False
        self._win_normal_geom = self._get_centered_geometry(1450, 880)
        self._win_drag_offset = None
        self._mac_ctrl_hover = False
        self._minimizing_to_taskbar = False

        # Trạng thái màn hình Loading khởi động kiểu MacBook (Xoay tròn + % Real-Time)
        self._is_initial_loading = True
        self._loading_cur_pct = 0.0
        self._loading_target_pct = 12.0
        self._spinner_step = 0
        self._spinner_lines = []
        self._loading_anim_job = None

        self._cleanup_workspace_junk()
        self._init_file_logger()
        self._setup_styles()
        self._build_ui()
        self.load_user_settings()
        self._bind_auto_save_settings()
        self._build_startup_loading_overlay()
        self._register_active_instance_lock()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close_window)
        self.root.bind("<Escape>", self._deselect_all_orders_and_reset)

        # Khởi động luồng Hàng Đợi Phân Tích AI (FIFO Queue Worker)
        self._ai_queue_worker_thread = threading.Thread(target=self._ai_queue_consumer_loop, daemon=True)
        self._ai_queue_worker_thread.start()

        # Khởi động kết nối, chạy phân tích AI lần đầu tự động và bật luồng Real-Time
        self.root.after(150, self.check_mt5_connection)
        self.root.after(350, self.trigger_single_scan)
        self.root.after(1000, self._realtime_tick_loop)
        self.root.after(2000, self._check_update_on_startup)

    def _create_empty_profile_ctx(self, profile_key: str = "XAUUSD") -> dict:
        """Khởi tạo bộ trạng thái cho hồ sơ Vàng XAUUSD."""
        sym_default = DEFAULT_PROFILE_SETTINGS.get(profile_key, {}).get("symbol", profile_key)
        return {
            "auto_loop_running": False,
            "is_analyzing": False,
            "last_candle_time": None,
            "last_recommendation": None,
            "pending_pullback_order": None,
            "daily_limit_locked_date": None,
            "daily_limit_reason": "",
            "cached_daily_pnl": 0.0,
            "cached_win_rate": 0.0,
            "cached_win_count": 0,
            "cached_loss_count": 0,
            "cached_closed_count": 0,
            "consec_loss_count": 0,
            "cooldown_until": None,
            "last_cooldown_deal_ticket": None,
            "basket_cooldown_until": None,
            "fast_sl_cooldown_until": None,
            "last_fast_sl_deal_ticket": None,
            "is_basket_closing": False,
            "cached_scenarios": None,
            "cached_mean_df": None,
            "cached_action": None,
            "cached_sl": None,
            "cached_tp": None,
            "cached_df_hist": None,
            "cached_df_analysis": None,
            "user_zoomed": False,
            "saved_xlim": None,
            "saved_ylim": None,
            "_last_detail_prefix": None,
            "_sig_title": f"⏳ ĐANG TẢI DỮ LIỆU & PHÂN TÍCH AI ({sym_default})...",
            "_sig_color": GOLD_ACCENT,
        }

    def _ui_after(self, delay_ms: int, fn, *args):
        """Gọi hàm cập nhật giao diện an toàn từ mọi luồng (Thread-Safe)."""
        try:
            if threading.current_thread() is threading.main_thread() and delay_ms == 0:
                fn(*args)
            else:
                self.root.after(delay_ms, fn, *args)
        except Exception:
            pass

    def _get_symbol_tolerances(self, symbol: str, tick=None) -> dict:
        """
        Trả về bộ ngưỡng đệm kỹ thuật tự động co giãn chuẩn xác cho Vàng XAUUSD:
        - Vàng (XAUUSDm ~ 2,900$-3,000$, spread ~ 0.16$): bước nhảy Trailing 0.40$, đệm dời SL 0.50$, dung sai bằng nhau 0.05$.
        """
        spread = 0.0
        stops_dist = 0.0
        try:
            if MT5_AVAILABLE:
                with self._mt5_lock:
                    if tick is None and symbol:
                        tick = mt5.symbol_info_tick(symbol)
                    if tick is not None and tick.ask > 0 and tick.bid > 0:
                        spread = max(float(tick.ask - tick.bid), 0.0)
                    if symbol:
                        s_info = mt5.symbol_info(symbol)
                        if s_info is not None:
                            stops_lvl = getattr(s_info, "trade_stops_level", 0) or 0
                            pt = getattr(s_info, "point", 0.01) or 0.01
                            stops_dist = float(stops_lvl) * float(pt)
        except Exception:
            pass

        return {
            "eps_equal": 0.05,
            "min_stop_dist": max(stops_dist * 1.2, spread * 1.2, 0.50),
            "min_trail_step": 0.40,
            "min_manual_be_dist": 0.30,
            "sl_break_tol": 0.25,
            "max_sl_tol": 0.05,
            "min_candle_body": 0.05,
            "min_atr_sl_floor": 1.0,
            "min_tp_floor": 1.5,
        }

    def _get_profile_cfg(self, profile_key: str = None) -> dict:
        """Lấy thông số cài đặt độc lập của hồ sơ XAUUSD."""
        pk = profile_key or self.active_profile_key
        if (
            pk == self.active_profile_key
            and not self._loading_settings
            and hasattr(self, "symbol_var")
            and threading.current_thread() is threading.main_thread()
        ):
            try:
                smap = self._get_settings_vars_map()
                cfg = {k: v.get() for k, v in smap.items()}
                self.profile_settings[pk] = dict(cfg)
                return dict(cfg)
            except Exception:
                pass
        return dict(self.profile_settings.get(pk, DEFAULT_PROFILE_SETTINGS.get(pk, {})))

    def _sync_self_to_active_ctx(self, include_ai_state: bool = False):
        """
        Lưu trạng thái giao diện và thông số của Tab đang mở vào bộ nhớ riêng của Tab đó.
        Mặc định KHÔNG ghi đè ngược các thuộc tính do luồng AI quản lý (AI_MANAGED_STATE_ATTRS) để tránh xung đột với luồng quét AI ngầm.
        """
        if self._loading_settings:
            return
        pk = self.active_profile_key
        ctx = self.profile_ctx[pk]
        for attr in PROFILE_STATE_ATTRS:
            if not include_ai_state and attr in AI_MANAGED_STATE_ATTRS:
                continue
            ctx[attr] = getattr(self, attr, None)
        if hasattr(self, "symbol_var") and threading.current_thread() is threading.main_thread():
            smap = self._get_settings_vars_map()
            self.profile_settings[pk] = {k: v.get() for k, v in smap.items()}

    def _update_forecast_tab_header(self):
        """Cập nhật tiêu đề Tab Nhật ký hệ thống."""
        try:
            if hasattr(self, "bottom_notebook"):
                self.bottom_notebook.tab(2, text="📋 NHẬT KÝ HỆ THỐNG")
        except Exception:
            pass

    def _sync_active_ctx_to_self(self):
        """Nạp toàn bộ trạng thái, thông số Menu, công tắc AUTO và bảng lệnh của Tab đang chọn lên giao diện."""
        pk = self.active_profile_key
        ctx = self.profile_ctx[pk]
        for attr in PROFILE_STATE_ATTRS:
            setattr(self, attr, ctx.get(attr))
        self._loading_settings = True
        try:
            smap = self._get_settings_vars_map()
            prof_cfg = self.profile_settings.get(pk, DEFAULT_PROFILE_SETTINGS[pk])
            for k, var in smap.items():
                if k in prof_cfg:
                    var.set(prof_cfg[k])
            self.auto_trade_var.set(bool(self.auto_loop_running))
        finally:
            self._loading_settings = False
        if pk in self.pos_tree_by_profile:
            self.pos_tree = self.pos_tree_by_profile[pk]
        if pk in self.pos_summary_lbl_by_profile:
            self.pos_summary_lbl = self.pos_summary_lbl_by_profile[pk]
        if hasattr(self, "drawer_title_lbl"):
            self.drawer_title_lbl.config(text=f"BẢNG ĐIỀU KHIỂN & CÀI ĐẶT ({pk})")
        self._update_forecast_tab_header()

    def _switch_active_profile(self, target_key: str = "XAUUSD"):
        """Đồng bộ hiển thị hồ sơ Vàng XAUUSD."""
        target_key = "XAUUSD"
        if target_key != self.active_profile_key:
            self._sync_self_to_active_ctx(include_ai_state=False)
            self.active_profile_key = target_key
            self._sync_active_ctx_to_self()
            self.save_user_settings()
        else:
            if target_key in self.pos_tree_by_profile:
                self.pos_tree = self.pos_tree_by_profile[target_key]
            if target_key in self.pos_summary_lbl_by_profile:
                self.pos_summary_lbl = self.pos_summary_lbl_by_profile[target_key]
            if hasattr(self, "drawer_title_lbl"):
                self.drawer_title_lbl.config(text="BẢNG ĐIỀU KHIỂN & CÀI ĐẶT (XAUUSD)")
            self._update_forecast_tab_header()

        # Cập nhật công tắc AUTO và nút Reload theo đúng trạng thái của Tab đang mở
        self._draw_toggle_switch()
        if self.is_analyzing:
            self._start_scan_progress_anim()
        else:
            self._scan_pct = 100.0
            self._scan_target_pct = 100.0
            self._draw_reload_pill()

        # Đồng bộ nút khung thời gian hiển thị trên thanh công cụ biểu đồ
        active_view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(target_key, self.tf_var.get().strip())
        self._highlight_active_tf_btn(active_view_tf)

        # Cập nhật hộp tín hiệu (Signal Box) của Tab đang mở
        sig_title = self._sig_title or f"⏳ ĐANG TẢI DỮ LIỆU & PHÂN TÍCH AI ({self.symbol_var.get().strip()})..."
        sig_color = self._sig_color or GOLD_ACCENT
        self.signal_title_lbl.config(text=sig_title, fg=sig_color)
        if self._last_detail_prefix:
            self.signal_detail_lbl.config(text=self._last_detail_prefix)
        else:
            self.signal_detail_lbl.config(
                text="💡 Rê chuột: Crosshair | Lăn chuột: Co giãn nến | Kéo chuột: Di chuyển | Chuột phải / Nháy đúp: Reset."
            )
        self._update_bot_status_bar(target_key)

        # Cập nhật bảng 6 cây nến dự báo AI theo đúng Tab đang mở nếu có widget tree
        if getattr(self, "tree", None) is not None:
            for row in self.tree.get_children():
                self.tree.delete(row)
            if self.cached_mean_df is not None:
                for _, r in self.cached_mean_df.iterrows():
                    ts_str = r["timestamps"].strftime("%Y-%m-%d %H:%M")
                    self.tree.insert(
                        "", tk.END,
                        values=(ts_str, f"{r['open']:.2f}", f"{r['high']:.2f}", f"{r['low']:.2f}", f"{r['close']:.2f}")
                    )

        # Cập nhật ngay giá Live, bảng lệnh đang mở và thông tin nhanh cho Tab vừa chọn
        try:
            with self._mt5_lock:
                mt5_ready = bool(MT5_AVAILABLE and mt5.initialize())
            if mt5_ready:
                sym = self.symbol_var.get().strip()
                tf_s = self.tf_var.get().strip()
                with self._mt5_lock:
                    mt5.symbol_select(sym, True)
                    tick = mt5.symbol_info_tick(sym)
                pos_cnt, tot_prof = self._update_open_positions_and_manage(sym, profile_key=target_key)
                self._check_daily_profit_loss_limits(tot_prof, tick, profile_key=target_key)
                self._update_left_quick_info(sym, tf_s, tick, pos_cnt, tot_prof)
        except Exception:
            pass

        # Nếu Tab vừa chuyển sang chưa có dữ liệu nến/dự báo AI -> Đưa vào hàng đợi quét AI cho Tab đó
        if self.cached_df_hist is None and not self.is_analyzing:
            self.trigger_single_scan(profile_key=target_key)

    def _cleanup_workspace_junk(self):
        """Tự động dọn dẹp mọi thư mục đệm (__pycache__, .pytest_cache, file .pyc/.tmp/.log) nếu có phát sinh."""
        try:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            for root_dir, dirs, files in os.walk(base_dir):
                if ".git" in root_dir:
                    continue
                for d in list(dirs):
                    if d in ("__pycache__", ".pytest_cache", ".mypy_cache"):
                        shutil.rmtree(os.path.join(root_dir, d), ignore_errors=True)
                for f in files:
                    if f.endswith((".pyc", ".pyo", ".tmp", ".log")):
                        try:
                            os.remove(os.path.join(root_dir, f))
                        except Exception:
                            pass
        except Exception:
            pass

    def _build_startup_loading_overlay(self):
        """Màn hình khởi động 'Đang tải dữ liệu' với vòng xoay kiểu MacBook và phần trăm Real-Time (0% -> 100%)."""
        self.loading_overlay = tk.Frame(self.root, bg="#0d1119")
        self.loading_overlay.place(x=0, y=0, relwidth=1.0, relheight=1.0)
        self.loading_overlay.lift()

        center_box = tk.Frame(self.loading_overlay, bg="#0d1119", padx=40, pady=35)
        center_box.place(relx=0.5, rely=0.47, anchor="center")

        # Vòng xoay 12 nan hoa kiểu MacBook / macOS Activity Indicator
        self.spinner_canvas = tk.Canvas(center_box, width=116, height=116, bg="#0d1119", highlightthickness=0)
        self.spinner_canvas.pack(pady=(0, 14))

        cx, cy = 58, 58
        r_in, r_out = 26, 48
        self._spinner_lines = []
        for i in range(12):
            angle_rad = math.radians(i * 30 - 90)
            x1 = cx + r_in * math.cos(angle_rad)
            y1 = cy + r_in * math.sin(angle_rad)
            x2 = cx + r_out * math.cos(angle_rad)
            y2 = cy + r_out * math.sin(angle_rad)
            line_id = self.spinner_canvas.create_line(
                x1, y1, x2, y2, width=5, capstyle=tk.ROUND, fill="#252f42"
            )
            self._spinner_lines.append(line_id)

        # Hiển thị Phần trăm Real-Time ngay chính giữa bên dưới vòng xoay
        self.loading_pct_lbl = tk.Label(
            center_box, text="0%", bg="#0d1119", fg=GOLD_ACCENT,
            font=("Segoe UI", 22, "bold")
        )
        self.loading_pct_lbl.pack(pady=(0, 4))

        self.loading_title_lbl = tk.Label(
            center_box, text="Đang tải dữ liệu...", bg="#0d1119", fg=TEXT_MAIN,
            font=("Segoe UI", 13, "bold")
        )
        self.loading_title_lbl.pack(pady=(0, 6))

        self.loading_sub_lbl = tk.Label(
            center_box, text="Đang khởi tạo kết nối MetaTrader 5 & Mô hình AI...",
            bg="#0d1119", fg="#8b98a9", font=("Segoe UI", 9)
        )
        self.loading_sub_lbl.pack(pady=(0, 14))

        # Thanh tiến trình mượt mà bên dưới
        self.loading_bar_canvas = tk.Canvas(
            center_box, width=320, height=6, bg="#1b2333", highlightthickness=0
        )
        self.loading_bar_canvas.pack()
        self.loading_bar_fill = self.loading_bar_canvas.create_rectangle(
            0, 0, 0, 6, fill=GOLD_ACCENT, outline=""
        )

        self._animate_startup_loader()

    def _set_loading_stage(self, target_pct: float, sub_text: str):
        self._scan_target_pct = max(self._scan_target_pct, float(target_pct))
        if not self._is_initial_loading:
            return
        self._loading_target_pct = max(self._loading_target_pct, float(target_pct))
        self._ui_after(0, lambda: self.loading_sub_lbl.config(text=sub_text) if hasattr(self, "loading_sub_lbl") and self.loading_sub_lbl.winfo_exists() else None)

    @staticmethod
    def _draw_capsule(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float, fill: str, outline: str = "", width: int = 1):
        """Vẽ hình viên thuốc bo tròn 2 đầu tuyệt đối mượt (Stadium/Pill) bằng 2 hình tròn + hình chữ nhật giữa."""
        h = max(y2 - y1, 2.0)
        r = h / 2.0
        if x2 - x1 < h:
            x2 = x1 + h
        if outline and width > 0:
            canvas.create_oval(x1, y1, x1 + h, y2, fill=fill, outline=outline, width=width)
            canvas.create_oval(x2 - h, y1, x2, y2, fill=fill, outline=outline, width=width)
            canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
            canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=width)
            canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=width)
        else:
            canvas.create_oval(x1, y1, x1 + h, y2, fill=fill, outline="")
            canvas.create_oval(x2 - h, y1, x2, y2, fill=fill, outline="")
            canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")

    def _draw_reload_pill(self):
        """Vẽ nút Reload dạng viên thuốc bo tròn kèm thanh tiến trình và % trực quan."""
        if not hasattr(self, "reload_canvas") or not self.reload_canvas.winfo_exists():
            return
        c = self.reload_canvas
        c.delete("all")
        w, h = 118, 32
        x1, y1, x2, y2 = 2, 2, w - 2, h - 2

        if self.is_analyzing:
            self._draw_capsule(c, x1, y1, x2, y2, fill="#161d2a", outline="#f0b90b", width=1)
            pct = min(max(self._scan_pct, 4.0), 100.0)
            fill_w = max((x2 - x1 - 4) * (pct / 100.0), y2 - y1 - 4)
            self._draw_capsule(c, x1 + 2, y1 + 2, min(x1 + 2 + fill_w, x2 - 2), y2 - 2, fill="#264653", outline="")
            c.create_text(
                w / 2, h / 2,
                text=f"⏳ {int(round(pct))}%",
                fill=GOLD_ACCENT,
                font=("Segoe UI", 9, "bold")
            )
        else:
            bg_col = "#243044"
            self._draw_capsule(c, x1, y1, x2, y2, fill=bg_col, outline="#3d4f6d", width=1)
            c.create_text(
                w / 2, h / 2,
                text="🔄 100%",
                fill="#ffffff",
                font=("Segoe UI", 9, "bold")
            )

    def _start_scan_progress_anim(self):
        self._scan_pct = 8.0
        self._scan_target_pct = 24.0
        if self._scan_anim_job is not None:
            try:
                self.root.after_cancel(self._scan_anim_job)
            except Exception:
                pass
            self._scan_anim_job = None
        self._draw_reload_pill()
        self._step_scan_progress_anim()

    def _step_scan_progress_anim(self):
        if not self.is_analyzing:
            return
        if self._scan_pct < self._scan_target_pct:
            diff = self._scan_target_pct - self._scan_pct
            self._scan_pct = min(self._scan_target_pct, self._scan_pct + max(0.8, diff * 0.22))
        elif self._scan_pct < 95.0:
            self._scan_pct = min(95.0, self._scan_pct + 0.25)
        self._draw_reload_pill()
        self._scan_anim_job = self.root.after(60, self._step_scan_progress_anim)

    def _finish_scan_progress_anim(self):
        if self._scan_anim_job is not None:
            try:
                self.root.after_cancel(self._scan_anim_job)
            except Exception:
                pass
            self._scan_anim_job = None
        self._scan_pct = 100.0
        self._scan_target_pct = 100.0
        self._draw_reload_pill()

    def _draw_toggle_switch(self):
        """Vẽ công tắc gạt (Toggle Switch) bo tròn mềm mại cho START / STOP."""
        if not hasattr(self, "toggle_switch_canvas") or not self.toggle_switch_canvas.winfo_exists():
            return
        c = self.toggle_switch_canvas
        c.delete("all")
        w, h = 118, 32
        x1, y1, x2, y2 = 2, 2, w - 2, h - 2
        knob_d = (y2 - y1) - 6

        if self.auto_loop_running:
            # Trạng thái ĐANG CHẠY (ON - START): Nền xanh ngọc, nút tròn bên phải
            self._draw_capsule(c, x1, y1, x2, y2, fill="#0ecb81", outline="#17e896", width=1)
            kx2 = x2 - 3
            kx1 = kx2 - knob_d
            c.create_oval(kx1, y1 + 3, kx2, y2 - 3, fill="#ffffff", outline="")
            c.create_text(
                (x1 + kx1) / 2 + 4, h / 2,
                text="RUNNING",
                fill="#071a12",
                font=("Segoe UI", 8, "bold")
            )
        else:
            # Trạng thái ĐANG DỪNG (OFF - STOP): Nền tối viền đỏ/xám, nút tròn đỏ bên trái
            self._draw_capsule(c, x1, y1, x2, y2, fill="#222a3a", outline="#f6465d", width=1)
            kx1 = x1 + 3
            kx2 = kx1 + knob_d
            c.create_oval(kx1, y1 + 3, kx2, y2 - 3, fill="#f6465d", outline="")
            c.create_text(
                (kx2 + x2) / 2 - 2, h / 2,
                text="STOPPED",
                fill="#f6465d",
                font=("Segoe UI", 8, "bold")
            )

    def _animate_startup_loader(self):
        if not self._is_initial_loading or not hasattr(self, "loading_overlay") or not self.loading_overlay.winfo_exists():
            return

        # 1. Cập nhật hiệu ứng xoay tròn 12 nan hoa kiểu MacBook
        palette = [
            "#ffffff", "#ffe082", "#f0b90b", "#d4a10a",
            "#b3860b", "#8f6b0d", "#6e5310", "#523f14",
            "#3c3018", "#2d261c", "#23201d", "#1c1b1b"
        ]
        for idx, line_id in enumerate(self._spinner_lines):
            color_idx = (self._spinner_step - idx) % 12
            self.spinner_canvas.itemconfig(line_id, fill=palette[color_idx])
        self._spinner_step = (self._spinner_step + 1) % 12

        # 2. Tăng phần trăm Real-Time mượt mà hướng tới _loading_target_pct
        if self._loading_cur_pct < self._loading_target_pct:
            diff = self._loading_target_pct - self._loading_cur_pct
            step = max(0.7, diff * 0.18) if diff > 3.0 else 0.45
            self._loading_cur_pct = min(self._loading_target_pct, self._loading_cur_pct + step)
        elif self._loading_cur_pct < 95.0:
            # Nhích nhẹ khi đang đợi mô hình AI tính toán kịch bản
            self._loading_cur_pct = min(95.0, self._loading_cur_pct + 0.15)

        pct_int = int(round(self._loading_cur_pct))
        self.loading_pct_lbl.config(text=f"{pct_int}%")
        bar_w = int(320 * (min(max(self._loading_cur_pct, 0.0), 100.0) / 100.0))
        self.loading_bar_canvas.coords(self.loading_bar_fill, 0, 0, bar_w, 6)

        self._loading_anim_job = self.root.after(50, self._animate_startup_loader)

    def _finish_startup_loading(self):
        """Đưa phần trăm lên 100% và đóng màn hình Loading để vào thẳng giao diện chính."""
        if not self._is_initial_loading:
            return
        self._is_initial_loading = False
        if self._loading_anim_job is not None:
            try:
                self.root.after_cancel(self._loading_anim_job)
            except Exception:
                pass
            self._loading_anim_job = None

        if hasattr(self, "loading_overlay") and self.loading_overlay.winfo_exists():
            try:
                self.loading_pct_lbl.config(text="100%")
                self.loading_title_lbl.config(text="Hoàn tất dữ liệu!")
                self.loading_sub_lbl.config(text="Đang mở giao diện biểu đồ Real-Time...")
                self.loading_bar_canvas.coords(self.loading_bar_fill, 0, 0, 320, 6)
            except Exception:
                pass
            self.root.after(220, lambda: self.loading_overlay.destroy() if hasattr(self, "loading_overlay") and self.loading_overlay.winfo_exists() else None)

    def _get_centered_geometry(self, win_w: int = 1450, win_h: int = 880) -> str:
        """Tính toán tọa độ để cửa sổ ứng dụng luôn nằm chính giữa màn hình khi khởi động."""
        try:
            self.root.update_idletasks()
            rect = (ctypes.c_long * 4)()
            if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                x0, y0, x1, y1 = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
                work_w = max(x1 - x0, win_w)
                work_h = max(y1 - y0, win_h)
                pos_x = x0 + max(0, (work_w - win_w) // 2)
                pos_y = y0 + max(0, (work_h - win_h) // 2)
                return f"{win_w}x{win_h}+{pos_x}+{pos_y}"
        except Exception:
            pass
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        pos_x = max(0, (sw - win_w) // 2)
        pos_y = max(0, (sh - 40 - win_h) // 2)
        return f"{win_w}x{win_h}+{pos_x}+{pos_y}"

    def _apply_window_and_taskbar_icon(self):
        """Gán biểu tượng KhaTran, căn giữa màn hình khi khởi động và kích hoạt thanh tiêu đề MacBook không viền."""
        try:
            centered_geom = self._get_centered_geometry(1450, 880)
            self._win_normal_geom = centered_geom
            self.root.geometry(centered_geom)
            if os.path.exists(ICON_ICO_FILE):
                self.root.iconbitmap(default=ICON_ICO_FILE)
                self.root.wm_iconbitmap(ICON_ICO_FILE)
            if os.path.exists(ICON_PNG_FILE):
                self._app_icon_img = tk.PhotoImage(file=ICON_PNG_FILE)
                self.root.iconphoto(True, self._app_icon_img)
        except Exception:
            pass
        self._apply_dark_titlebar()
        self.root.after(120, self._apply_dark_titlebar)
        self.root.bind("<Map>", self._on_window_map_restore)

    def _apply_dark_titlebar(self):
        """Ẩn thanh tiêu đề vuông [— □ ✕] mặc định của Windows để dùng 3 nút tròn Traffic Light kiểu MacBook, đồng thời giữ nguyên Icon dưới Taskbar."""
        try:
            self.root.overrideredirect(True)
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            GWL_EXSTYLE = -20
            WS_EX_APPWINDOW = 0x00040000
            WS_EX_TOOLWINDOW = 0x00000080
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            new_ex_style = (ex_style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            if ex_style != new_ex_style:
                ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_ex_style)
                ctypes.windll.user32.ShowWindow(hwnd, 0)
                ctypes.windll.user32.ShowWindow(hwnd, 5)
        except Exception:
            pass

    def _on_window_map_restore(self, event=None):
        if getattr(self, "_minimizing_to_taskbar", False) and self.root.state() == "normal":
            self._minimizing_to_taskbar = False
            self.root.after(20, self._apply_dark_titlebar)

    def _minimize_window(self):
        """Thu nhỏ cửa sổ xuống thanh Taskbar Windows khi bấm nút tròn màu Vàng kiểu MacBook."""
        try:
            self._minimizing_to_taskbar = True
            self.root.overrideredirect(False)
            self.root.iconify()
        except Exception:
            pass

    def _toggle_maximize_window(self, event=None):
        """Phóng to toàn màn hình (chừa thanh Taskbar) hoặc thu hồi kích thước cũ khi bấm nút tròn màu Xanh lá kiểu MacBook."""
        try:
            if not getattr(self, "_win_maximized", False):
                self._win_normal_geom = self.root.geometry()
                rect = (ctypes.c_long * 4)()
                if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
                    x0, y0, x1, y1 = rect[0], rect[1], rect[2], rect[3]
                    self.root.geometry(f"{x1 - x0}x{y1 - y0}+{x0}+{y0}")
                else:
                    sw = self.root.winfo_screenwidth()
                    sh = self.root.winfo_screenheight() - 42
                    self.root.geometry(f"{sw}x{sh}+0+0")
                self._win_maximized = True
            else:
                self.root.geometry(getattr(self, "_win_normal_geom", "1450x880+60+40"))
                self._win_maximized = False
            self._draw_mac_window_controls()
        except Exception:
            pass

    def _start_win_drag(self, event):
        if getattr(self, "_win_maximized", False):
            return
        self._win_drag_offset = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _on_win_drag(self, event):
        if getattr(self, "_win_maximized", False) or not getattr(self, "_win_drag_offset", None):
            return
        nx = event.x_root - self._win_drag_offset[0]
        ny = max(0, event.y_root - self._win_drag_offset[1])
        self.root.geometry(f"+{nx}+{ny}")

    def _draw_mac_window_controls(self):
        """Vẽ cụm 3 nút tròn Thu Phóng & Đóng chuẩn MacBook (Vàng: Thu nhỏ − | Xanh lá: Phóng to ⤢ | Đỏ: Đóng ×)."""
        if not hasattr(self, "mac_win_ctrl_canvas") or not self.mac_win_ctrl_canvas.winfo_exists():
            return
        c = self.mac_win_ctrl_canvas
        c.delete("all")
        w, h = 92, 28
        cy = h / 2.0
        r = 7.5
        hover = getattr(self, "_mac_ctrl_hover", False)

        centers = [
            (18.0, "#ffbd2e", "#dfa123", "MIN"),
            (46.0, "#28c840", "#1aab29", "MAX"),
            (74.0, "#ff5f57", "#e0443e", "CLOSE"),
        ]
        for cx, fill_col, border_col, kind in centers:
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill_col, outline=border_col, width=1)
            if hover:
                if kind == "MIN":
                    c.create_line(cx - 3.8, cy, cx + 3.8, cy, fill="#5a3e00", width=1.8, capstyle=tk.ROUND)
                elif kind == "MAX":
                    c.create_polygon(
                        cx - 3.6, cy - 3.6, cx + 1.2, cy - 3.6, cx - 3.6, cy + 1.2,
                        fill="#084d14", outline=""
                    )
                    c.create_polygon(
                        cx + 3.6, cy + 3.6, cx - 1.2, cy + 3.6, cx + 3.6, cy - 1.2,
                        fill="#084d14", outline=""
                    )
                elif kind == "CLOSE":
                    c.create_line(cx - 3.2, cy - 3.2, cx + 3.2, cy + 3.2, fill="#5a0004", width=1.8, capstyle=tk.ROUND)
                    c.create_line(cx - 3.2, cy + 3.2, cx + 3.2, cy - 3.2, fill="#5a0004", width=1.8, capstyle=tk.ROUND)

    def _on_mac_win_ctrl_click(self, event):
        x = event.x
        if x < 32:
            self._minimize_window()
        elif x < 60:
            self._toggle_maximize_window()
        else:
            self._on_close_window()

    def _update_license_badge_ui(self):
        """Cập nhật hiển thị số ngày bản quyền hoặc trạng thái trên thanh Header."""
        if not hasattr(self, "license_badge_btn") or not self.license_badge_btn.winfo_exists():
            return
        if not hasattr(self, "lic_mgr") or self.lic_mgr is None:
            return
        try:
            st = self.lic_mgr.check_status()
            b_text = st.get("badge_text", "[ Bản quyền ]")
            b_color = st.get("badge_color", "#38bdf8")
            self.license_badge_btn.config(
                text=b_text,
                fg=b_color,
                activeforeground=b_color,
            )
        except Exception:
            pass

    def _open_license_dialog(self):
        """Mở cửa sổ Kích Hoạt Bản Quyền Ứng Dụng để xem HWID hoặc nhập key gia hạn."""
        if not hasattr(self, "lic_mgr") or self.lic_mgr is None:
            try:
                from kronos_license import LicenseManager
                self.lic_mgr = LicenseManager()
            except Exception:
                return
        try:
            from kronos_license import show_activation_dialog
            show_activation_dialog(self.root, self.lic_mgr, is_startup_block=False)
            self._update_license_badge_ui()
        except Exception as e:
            self.log_error("OPEN_LICENSE_DIALOG", e)

    def _check_update_on_startup(self):
        """Tự động kiểm tra bản cập nhật mới trên GitHub Release khi khởi động ứng dụng."""
        if not hasattr(self, "updater") or self.updater is None:
            return

        def _on_res(res):
            if res.get("has_update"):
                self._latest_update_info = res
                self._ui_after(0, lambda: self._start_version_badge_blink(res.get("latest_version")))

        self.updater.check_update_async(_on_res)

    def _start_version_badge_blink(self, latest_ver: str):
        """Bật hiệu ứng nhấp nháy chữ trên nút phiên bản khi có bản cập nhật mới."""
        self._has_new_version = True
        self._new_version_tag = latest_ver
        self._version_blink_state = False
        self._run_version_badge_blink()

    def _run_version_badge_blink(self):
        if not getattr(self, "_has_new_version", False):
            return
        if not hasattr(self, "version_badge_btn") or not self.version_badge_btn.winfo_exists():
            return

        self._version_blink_state = not self._version_blink_state
        if self._version_blink_state:
            self.version_badge_btn.config(
                text=f"Có bản cập nhật v{self._new_version_tag}!",
                bg=PANEL_BG,
                fg="#fef08a",
                activebackground=PANEL_BG,
                activeforeground="#fef08a",
            )
        else:
            self.version_badge_btn.config(
                text=f"Có bản cập nhật v{self._new_version_tag}!",
                bg=PANEL_BG,
                fg="#f59e0b",
                activebackground=PANEL_BG,
                activeforeground="#fde047",
            )
        self._version_blink_after_id = self.root.after(600, self._run_version_badge_blink)

    def _on_click_version_badge(self):
        """Mở cửa sổ giới thiệu tính năng của bản cập nhật mới hoặc kiểm tra phiên bản thủ công."""
        if not hasattr(self, "updater") or self.updater is None:
            try:
                from kronos_updater import AppUpdater, get_current_app_version
                self.app_version = get_current_app_version()
                self.updater = AppUpdater(self.app_version)
            except Exception:
                return

        if getattr(self, "_has_new_version", False) and getattr(self, "_latest_update_info", None):
            from kronos_updater import show_update_modal
            show_update_modal(self.root, self.updater, self._latest_update_info)
        else:
            self.version_badge_btn.config(text="Đang kiểm tra...", fg="#38bdf8")

            def _on_checked(res):
                def _ui():
                    if res.get("has_update"):
                        self._latest_update_info = res
                        self._start_version_badge_blink(res.get("latest_version"))
                        from kronos_updater import show_update_modal
                        show_update_modal(self.root, self.updater, res)
                    else:
                        self.version_badge_btn.config(
                            text=f"Phiên bản mới nhất  v{self.app_version}  🔄",
                            bg=PANEL_BG,
                            fg="#10b981",
                        )
                        self.show_toast(f"Bạn đang sử dụng phiên bản mới nhất (v{self.app_version}).", is_error=False, duration_ms=2500)

                self._ui_after(0, _ui)

            self.updater.check_update_async(_on_checked)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=BG_DARK)
        style.configure("Panel.TFrame", background=PANEL_BG)
        style.configure("TLabel", background=PANEL_BG, foreground=TEXT_MAIN, font=("Segoe UI", 9))
        style.configure("Treeview", background="#131822", foreground=TEXT_MAIN, fieldbackground="#131822", rowheight=23, font=("Consolas", 9))
        style.configure(
            "Treeview.Heading",
            background="#1e293b",
            foreground=GOLD_ACCENT,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            borderwidth=0,
            lightcolor="#1e293b",
            darkcolor="#1e293b",
            bordercolor="#131822",
        )
        style.map(
            "Treeview.Heading",
            background=[("pressed", "#1e293b"), ("active", "#1e293b")],
            foreground=[("pressed", GOLD_ACCENT), ("active", GOLD_ACCENT)],
            relief=[("pressed", "flat"), ("active", "flat")],
        )
        style.configure("TNotebook", background=BG_DARK, borderwidth=0, tabmargins=[2, 2, 2, 0])
        style.configure(
            "TNotebook.Tab",
            background="#1a2230",
            foreground="#8b98a9",
            padding=[14, 5],
            font=("Segoe UI", 9, "bold"),
            borderwidth=1
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", "#0284c7"), ("active", "#28354a")],
            foreground=[("selected", "#ffffff"), ("active", "#ffffff")],
            padding=[("selected", [14, 5]), ("!selected", [14, 5])],
            expand=[("selected", [0, 0, 0, 0]), ("!selected", [0, 0, 0, 0])]
        )

    def _build_ui(self):
        # =====================================================================
        # 1. THANH TIÊU ĐỀ KIỂU MACBOOK: NÚT ☰ BÊN TRÁI, ĐỒNG HỒ GIỮA, 3 NÚT TRÒN THU PHÓNG MACBOOK BÊN PHẢI
        # =====================================================================
        top_bar = tk.Frame(self.root, bg=PANEL_BG, pady=4, padx=10, highlightthickness=1, highlightbackground="#252f42")
        top_bar.pack(fill=tk.X, padx=8, pady=(6, 4))
        top_bar.bind("<ButtonPress-1>", self._start_win_drag)
        top_bar.bind("<B1-Motion>", self._on_win_drag)
        top_bar.bind("<Double-Button-1>", self._toggle_maximize_window)

        self.menu_toggle_btn = tk.Button(
            top_bar, text="☰", bg="#252f42", fg="#ffffff",
            activebackground="#32415c", activeforeground="#ffffff",
            font=("Segoe UI", 12, "bold"), relief=tk.FLAT, width=3, pady=0, cursor="hand2",
            command=self.toggle_left_menu
        )
        self.menu_toggle_btn.pack(side=tk.LEFT, padx=(0, 8))

        app_title_lbl = tk.Label(
            top_bar, text="Trading AI PRO by Kha Tran Design",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold")
        )
        app_title_lbl.pack(side=tk.LEFT, padx=(0, 8))
        app_title_lbl.bind("<ButtonPress-1>", self._start_win_drag)
        app_title_lbl.bind("<B1-Motion>", self._on_win_drag)
        app_title_lbl.bind("<Double-Button-1>", self._toggle_maximize_window)

        # Nút Huy hiệu Phiên bản & Kiểm tra cập nhật (Chữ màu xanh lá, không nền)
        self.version_badge_btn = tk.Button(
            top_bar,
            text=f"Phiên bản mới nhất  v{self.app_version}  🔄",
            bg=PANEL_BG,
            fg="#10b981",
            activebackground=PANEL_BG,
            activeforeground="#34d399",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            bd=0,
            padx=4,
            pady=0,
            cursor="hand2",
            command=self._on_click_version_badge,
        )
        self.version_badge_btn.pack(side=tk.LEFT, padx=(0, 10))
        self.version_badge_btn.bind(
            "<Enter>",
            lambda e: self.version_badge_btn.config(fg="#34d399")
            if not getattr(self, "_has_new_version", False)
            else None
        )
        self.version_badge_btn.bind(
            "<Leave>",
            lambda e: self.version_badge_btn.config(fg="#10b981")
            if not getattr(self, "_has_new_version", False)
            else None
        )

        # Cụm 3 nút tròn Thu Phóng & Đóng cửa sổ chuẩn MacBook ở góc trên bên phải (Thay cho — □ ✕ vuông của Windows)
        self.mac_win_ctrl_canvas = tk.Canvas(
            top_bar, width=92, height=28, bg=PANEL_BG, highlightthickness=0, cursor="hand2"
        )
        self.mac_win_ctrl_canvas.pack(side=tk.RIGHT, padx=(8, 0))
        self.mac_win_ctrl_canvas.bind("<Enter>", lambda e: (setattr(self, "_mac_ctrl_hover", True), self._draw_mac_window_controls()))
        self.mac_win_ctrl_canvas.bind("<Leave>", lambda e: (setattr(self, "_mac_ctrl_hover", False), self._draw_mac_window_controls()))
        self.mac_win_ctrl_canvas.bind("<Button-1>", self._on_mac_win_ctrl_click)
        self._draw_mac_window_controls()

        # Nút Huy hiệu Bản quyền nằm ở góc phải ngay cạnh cụm nút thu phóng cửa sổ
        self.license_badge_btn = tk.Button(
            top_bar,
            text="[ Bản quyền ]",
            bg="#111827",
            fg="#38bdf8",
            activebackground="#1e293b",
            activeforeground="#38bdf8",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._open_license_dialog,
        )
        self.license_badge_btn.pack(side=tk.RIGHT, padx=(0, 10))
        self._update_license_badge_ui()

        # Căn giữa thông số đếm giờ & giá Live ngay chính giữa thanh trên cùng
        self.live_clock_lbl = tk.Label(
            top_bar, text="LIVE | Bid: --- | Ask: --- | Đóng nến: --:--",
            bg="#121722", fg=CYAN_LIVE, font=("Consolas", 9, "bold"), padx=14, pady=4
        )
        self.live_clock_lbl.pack(anchor="center")
        self.live_clock_lbl.bind("<ButtonPress-1>", self._start_win_drag)
        self.live_clock_lbl.bind("<B1-Motion>", self._on_win_drag)
        self.live_clock_lbl.bind("<Double-Button-1>", self._toggle_maximize_window)

        # KHUNG CHÍNH (RIGHT PANEL CHIẾM TOÀN MÀN HÌNH)
        self.main_frame = tk.Frame(self.root, bg=BG_DARK)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=2)
        main_frame = self.main_frame

        # =====================================================================
        # 2. CỘT PHẢI (HIỂN THỊ CỐ ĐỊNH 100% KHÔNG BỊ CO GIÃN KHI ĐÓNG/MỞ MENU)
        # =====================================================================
        self.right_panel = tk.Frame(main_frame, bg=BG_DARK)
        self.right_panel.pack(fill=tk.BOTH, expand=True)
        right_panel = self.right_panel

        # =====================================================================
        # 3. NGĂN KÉO MENU TRÁI (CHIỀU CAO BẰNG 100% CHIỀU CAO APP, NÚT ☰ NẰM TRONG TAB MENU)
        # =====================================================================
        self.left_panel = tk.Frame(
            self.root, bg=PANEL_BG, width=self.drawer_width, padx=10, pady=8,
            highlightthickness=1, highlightbackground="#384661"
        )
        self.left_panel.pack_propagate(False)
        # Đặt trực tiếp trên self.root với y=0, relheight=1.0 để chiều cao bằng đúng 100% chiều cao App!
        self.left_panel.place(x=self.drawer_x, y=0, relheight=1.0, width=self.drawer_width)
        left_panel = self.left_panel

        # THANH TIÊU ĐỀ TRÊN CÙNG CỦA MENU: CHỨA NÚT 3 GẠCH (☰) NẰM CHUNG TRONG TAB MENU
        drawer_header = tk.Frame(left_panel, bg=PANEL_BG)
        drawer_header.pack(fill=tk.X, pady=(0, 6))

        self.drawer_close_btn = tk.Button(
            drawer_header, text="☰", bg="#252f42", fg="#ffffff",
            activebackground="#32415c", activeforeground="#ffffff",
            font=("Segoe UI", 12, "bold"), relief=tk.FLAT, width=3, pady=0,
            command=self.toggle_left_menu
        )
        self.drawer_close_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.drawer_title_lbl = tk.Label(
            drawer_header, text="BẢNG ĐIỀU KHIỂN & CÀI ĐẶT (XAUUSD)",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold"), anchor="w"
        )
        self.drawer_title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # KHỐI ĐẦU MENU: KẾT NỐI MT5, CHART REAL-TIME & DÒNG HIỂN THỊ SỐ DƯ BÊN DƯỚI
        menu_top_card = tk.Frame(left_panel, bg="#121722", padx=8, pady=6, highlightthickness=1, highlightbackground="#263147")
        menu_top_card.pack(fill=tk.X, pady=(0, 6))

        mt5_row = tk.Frame(menu_top_card, bg="#121722")
        mt5_row.pack(fill=tk.X, pady=(0, 4))
        tk.Button(
            mt5_row, text="Kết nối MT5", bg="#2b3548", fg=TEXT_MAIN,
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=10, pady=2,
            command=self.check_mt5_connection
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.auto_trade_var = tk.BooleanVar(value=False)
        self.realtime_chart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            mt5_row, text="Chart Real-Time",
            variable=self.realtime_chart_var, bg="#121722", fg=CYAN_LIVE, selectcolor=BG_DARK,
            activebackground="#121722", activeforeground=CYAN_LIVE,
            font=("Segoe UI", 8, "bold")
        ).pack(side=tk.RIGHT)

        # Dòng thông tin Số dư nằm riêng bên dưới chữ Kết nối MT5 (không có ô tick)
        self.mt5_status_lbl = tk.Label(
            menu_top_card, text="Đang kết nối MT5...", bg="#121722", fg=GOLD_ACCENT,
            font=("Segoe UI", 8, "bold"), anchor="w"
        )
        self.mt5_status_lbl.pack(fill=tk.X, pady=(2, 2))

        self.accordion_sections = []

        # Mục 1: 1. Cài đặt cơ bản (Mặc định thu gọn)
        sec1 = CollapsibleSection(
            left_panel, "1. Cài đặt cơ bản", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(1)
        )
        self.symbol_var = self._add_labeled_entry(sec1.content, "Mã giao dịch MT5:", "XAUUSDm")
        self.analysis_tf_var = self._add_labeled_combo(sec1.content, "Khung phân tích AI:", ["M1", "M5", "M15", "H1"], "M15")
        self.tf_var = self._add_labeled_combo(sec1.content, "Khung vào lệnh (Scalp):", ["M1", "M5", "M15", "H1"], "M5")
        self.lot_var = self._add_labeled_entry(sec1.content, "Khối lượng (Lot):", "0.01")
        self.max_pos_var = self._add_labeled_entry(sec1.content, "Số lệnh tối đa:", "3")
        default_magic_str = "20260601" if INSTANCE_ID else "20260501"
        self.magic_number_var = self._add_labeled_entry(sec1.content, "Magic Number (Mã lệnh):", default_magic_str)
        tk.Button(
            sec1.content, text="↺ Khôi phục mặc định Mục 1 (Cài đặt cơ bản)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(1)
        ).pack(fill=tk.X, pady=(6, 2))

        # Mục 2: 2. Bộ lọc Entry & Nhồi lệnh (Mặc định thu gọn)
        sec2 = CollapsibleSection(
            left_panel, "2. Bộ lọc Entry & Nhồi lệnh", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(2)
        )
        self.scenarios_var = self._add_labeled_entry(sec2.content, "Số kịch bản AI (Ensemble):", "8")
        self.consensus_var = self._add_labeled_entry(sec2.content, "Độ đồng thuận tối thiểu (%):", "75")
        self.max_dispersion_var = self._add_labeled_entry(sec2.content, "Độ phân tán tối đa (x ATR, 0=Tắt):", "2.5")
        self.min_move_var = self._add_labeled_entry(sec2.content, "Biên độ tối thiểu (Giá):", "1.2")
        self.max_spread_var = self._add_labeled_entry(sec2.content, "Spread tối đa (Giá):", "0.35")
        self.sl_atr_var = self._add_labeled_entry(sec2.content, "Khoảng cách SL AI (x ATR14):", "1.5")
        self.sl_buffer_var = self._add_labeled_entry(sec2.content, "+ Giá an toàn vào SL (Giá):", "0.5")
        self.max_sl_price_var = self._add_labeled_entry(sec2.content, "SL tối đa (Giá, 0=Theo AI):", "5.0")
        self.rr_var = self._add_labeled_entry(sec2.content, "Tỷ lệ R:R (0=TP Theo AI):", "2.0")

        # Phân nhóm: Quy tắc nhồi lệnh & Chống trùng giá
        tk.Label(
            sec2.content, text="─── QUY TẮC NHỒI LỆNH ───",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 7, "bold"), pady=4
        ).pack(fill=tk.X)
        self.entry_mode_var = self._add_labeled_combo(
            sec2.content, "Chế độ nhồi lệnh:",
            [
                "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)",
                "Nhồi giá tốt hơn (DCA)",
                "Tự do theo AI (Cách >= khoảng cách tối thiểu)"
            ],
            "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)"
        )
        self.min_entry_spacing_var = self._add_labeled_entry(sec2.content, "Khoảng cách nhồi tối thiểu (Giá):", "1.0")
        self.one_order_per_candle_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            sec2.content, text="Chỉ mở tối đa 1 lệnh mỗi cây nến",
            variable=self.one_order_per_candle_var, bg=PANEL_BG, fg=TEXT_MAIN, selectcolor=BG_DARK,
            activebackground=PANEL_BG, font=("Segoe UI", 8, "bold"), anchor="w",
            command=self.save_user_settings
        ).pack(fill=tk.X, pady=(2, 2))

        tk.Button(
            sec2.content, text="↺ Khôi phục mặc định Mục 2 (Bộ lọc Entry & Nhồi lệnh)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(2)
        ).pack(fill=tk.X, pady=(6, 2))

        # Mục 3: 3. Quản lý lệnh & Chốt lời (Mặc định thu gọn)
        sec3 = CollapsibleSection(
            left_panel, "3. Quản lý lệnh & Chốt lời", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(3)
        )
        self.auto_be_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            sec3.content, text="Tự dời SL về đúng Entry theo R:R:",
            variable=self.auto_be_var, bg=PANEL_BG, fg=TEXT_MAIN, selectcolor=BG_DARK,
            activebackground=PANEL_BG, font=("Segoe UI", 8, "bold"), anchor="w",
            command=self.save_user_settings
        ).pack(fill=tk.X)
        self.be_trigger_r_var = self._add_labeled_entry(sec3.content, "   Mức kích hoạt (R, VD 1.0 = 1R):", "1.0")

        self.auto_trail_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            sec3.content, text="Tự trượt SL khóa lãi (Trailing Stop):",
            variable=self.auto_trail_var, bg=PANEL_BG, fg=TEXT_MAIN, selectcolor=BG_DARK,
            activebackground=PANEL_BG, font=("Segoe UI", 8, "bold"), anchor="w",
            command=self.save_user_settings
        ).pack(fill=tk.X)
        self.trail_dist_r_var = self._add_labeled_entry(sec3.content, "   Khoảng cách bám giá (R):", "0.35")

        self.auto_rev_close_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            sec3.content, text="Bảo vệ đảo chiều (Lãi: Chốt | Lỗ: Chờ về Entry+)",
            variable=self.auto_rev_close_var, bg=PANEL_BG, fg=GOLD_ACCENT, selectcolor=BG_DARK,
            activebackground=PANEL_BG, font=("Segoe UI", 8, "bold"), anchor="w",
            command=self.save_user_settings
        ).pack(fill=tk.X, pady=(2, 2))
        self.rev_consensus_var = self._add_labeled_entry(sec3.content, "   Đồng thuận đảo chiều (%):", "75")
        self.rev_min_move_var = self._add_labeled_entry(sec3.content, "   Biên độ ngược chiều (Giá, 0=Tắt):", "0")
        self.be_plus_price_var = self._add_labeled_entry(sec3.content, "   + Giá lãi nhỏ vào Entry (Giá):", "0.2")

        # Phân nhóm: Chốt lời chùm lệnh & Thời gian giữ lệnh
        tk.Label(
            sec3.content, text="─── CHỐT LỜI CHÙM LỆNH & THỜI GIAN ───",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 7, "bold"), pady=4
        ).pack(fill=tk.X)
        self.basket_tp_usd_var = self._add_labeled_entry(sec3.content, "Chốt lời chùm lệnh ($, 0=Tắt):", "0")
        self.basket_cooldown_mins_var = self._add_labeled_entry(sec3.content, "   Nghỉ sau chốt chùm (phút, 0=Tắt):", "15")
        self.max_hold_mins_var = self._add_labeled_entry(sec3.content, "Giữ lệnh tối đa (phút, 0=Tắt):", "0")
        self.max_hold_action_var = self._add_labeled_combo(
            sec3.content, "   Xử lý khi vượt thời gian:",
            [
                "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)",
                "Dời SL về hòa vốn (BE+)",
                "Đóng tất cả (Bất kể Lãi/Lỗ)"
            ],
            "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)"
        )

        # Phân nhóm: Giới hạn Lãi/Lỗ ngày & Cooldown
        tk.Label(
            sec3.content, text="─── GIỚI HẠN LÃI/LỖ TRONG NGÀY ───",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 7, "bold"), pady=4
        ).pack(fill=tk.X)
        self.daily_tp_usd_var = self._add_labeled_entry(sec3.content, "Dừng khi Lãi ngày ($, 0=Tắt):", "0")
        self.daily_sl_usd_var = self._add_labeled_entry(sec3.content, "Dừng khi Lỗ ngày ($, 0=Tắt):", "0")
        self.max_consec_loss_var = self._add_labeled_entry(sec3.content, "Thua liên tiếp nghỉ (lệnh, 0=Tắt):", "0")
        self.cooldown_mins_var = self._add_labeled_entry(sec3.content, "Thời gian tạm ngừng (phút):", "30")
        self.fast_sl_cooldown_mins_var = self._add_labeled_entry(sec3.content, "Nghỉ nếu SL < 5 phút (phút, 0=Tắt):", "15")
        tk.Button(
            sec3.content, text="↺ Khôi phục mặc định Mục 3 (Quản lý lệnh & Chốt lời)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(3)
        ).pack(fill=tk.X, pady=(6, 2))

        # Mục 4: 4. Phân tích AI & Mô hình (Mặc định thu gọn)
        sec4 = CollapsibleSection(
            left_panel, "4. Phân tích AI & Mô hình", initially_open=False,
            group_list=self.accordion_sections,
            on_reset=lambda: self.reset_section_settings(4)
        )
        self.engine_var = self._add_labeled_combo(
            sec4.content, "Chế độ chạy:",
            ["Ưu tiên Hugging Face -> GTX 1650", "Chạy trực tiếp máy Local"],
            "Chạy trực tiếp máy Local"
        )
        self.model_var = self._add_labeled_combo(
            sec4.content, "Mô hình:",
            list(MODEL_CONFIGS.keys()),
            "AI-base (102.3M - Mạnh nhất)"
        )
        self.hf_space_var = self._add_labeled_entry(sec4.content, "HF Space ID:", "tranvankha2807/Kronos_Xau", entry_width=18)
        self.hf_token_var = self._add_labeled_entry(sec4.content, "HF Token (nếu có):", "", show="*", entry_width=18)
        self.lookback_var = self._add_labeled_entry(sec4.content, "Số nến lịch sử AI (Lookback):", "450")
        self.pred_len_var = self._add_labeled_entry(sec4.content, "Số nến dự báo (6 = 30p):", "6")
        tk.Button(
            sec4.content, text="↺ Khôi phục mặc định Mục 4 (Phân tích AI & Mô hình)", bg="#161e2e", fg="#94a3b8",
            activebackground="#25334c", activeforeground=GOLD_ACCENT,
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, pady=2, cursor="hand2",
            command=lambda: self.reset_section_settings(4)
        ).pack(fill=tk.X, pady=(6, 2))

        # KHỐI LƯU CÀI ĐẶT DUY NHẤT Ở ĐÁY CÙNG MENU TRÁI (DƯỚI CÙNG GIAO DIỆN)
        save_card = tk.Frame(left_panel, bg="#121722", padx=8, pady=6, highlightthickness=1, highlightbackground="#263147")
        save_card.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 4))

        self.save_settings_btn = tk.Button(
            save_card,
            text="💾 LƯU CÀI ĐẶT NGAY (SAVE)",
            bg="#0d7a46",
            fg="#ffffff",
            activebackground="#159f54",
            activeforeground="#ffffff",
            font=("Segoe UI", 9, "bold"),
            relief=tk.FLAT,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.manual_save_user_settings
        )
        self.save_settings_btn.pack(fill=tk.X, pady=(1, 2))

        # Hàng 2 nút Xuất / Nhập cấu hình tinh gọn, tối giản (Slate thanh lịch, không màu mè)
        io_row = tk.Frame(save_card, bg="#121722")
        io_row.pack(fill=tk.X, pady=(2, 2))

        self.export_settings_btn = tk.Button(
            io_row,
            text="📤 Xuất Cấu Hình",
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self.export_settings_file
        )
        self.export_settings_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        self.import_settings_btn = tk.Button(
            io_row,
            text="📥 Nhập Cấu Hình",
            bg="#1e293b",
            fg="#cbd5e1",
            activebackground="#334155",
            activeforeground="#ffffff",
            font=("Segoe UI", 8, "bold"),
            relief=tk.FLAT,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self.import_settings_file
        )
        self.import_settings_btn.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(2, 0))

        self.save_status_lbl = tk.Label(
            save_card,
            text="✓ Tự động lưu: ĐÃ BẬT (Sẵn sàng)",
            bg="#121722",
            fg="#10b981",
            font=("Consolas", 7, "bold"),
            anchor="center"
        )
        self.save_status_lbl.pack(fill=tk.X, pady=(2, 0))

        # BẢNG THÔNG TIN PHIÊN & TÓM TẮT AI NẰM Ở GIỮA MENU TRÁI (CO GIÃN TỰ NHIÊN)
        info_card = tk.Frame(left_panel, bg="#131822", padx=10, pady=8, highlightthickness=1, highlightbackground="#263147")
        info_card.pack(fill=tk.BOTH, expand=True, pady=(4, 4))

        info_top_row = tk.Frame(info_card, bg="#131822")
        info_top_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            info_top_row, text="THÔNG TIN PHIÊN & TÓM TẮT AI",
            bg="#131822", fg=GOLD_ACCENT, font=("Segoe UI", 9, "bold"), anchor="w"
        ).pack(side=tk.LEFT)
        tk.Button(
            info_top_row, text="📂 Mở Log", bg="#232d3f", fg=CYAN_LIVE,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=self.open_log_folder
        ).pack(side=tk.RIGHT)

        self.quick_info_lbl = tk.Label(
            info_card,
            text=(
                "• Mã giao dịch: XAUUSDm (M5)\n"
                "• Giá Live: Đang tải...\n"
                "• Lệnh đang mở: 0 lệnh\n"
                "• Lãi/Lỗ nổi: 0.00 USD\n"
                "──────────────────────────\n"
                "• Trạng thái AI: Đang khởi tạo...\n"
                "• Xu hướng AI: ---\n"
                "• Mục tiêu 6 nến: ---\n"
                "• SL / TP gợi ý: ---"
            ),
            bg="#131822", fg=TEXT_MAIN, font=("Consolas", 8), justify=tk.LEFT, anchor="nw"
        )
        self.quick_info_lbl.pack(fill=tk.BOTH, expand=True)

        # Hộp Tín hiệu bên Cột phải (Tích hợp nút Reload % bo tròn & Công tắc gạt START/STOP ở góc phải)
        sig_box = tk.Frame(right_panel, bg=PANEL_BG, padx=12, pady=6)
        sig_box.pack(fill=tk.X, pady=(0, 4))

        sig_right = tk.Frame(sig_box, bg=PANEL_BG)
        sig_right.pack(side=tk.RIGHT, padx=(12, 0), anchor="center")

        # 1. Nút Reload dạng viên thuốc bo tròn hiển thị % trực quan
        reload_col = tk.Frame(sig_right, bg=PANEL_BG)
        reload_col.pack(side=tk.LEFT, padx=(0, 10))
        tk.Label(
            reload_col, text="QUÉT AI (RELOAD)", bg=PANEL_BG, fg=TEXT_MUTED,
            font=("Segoe UI", 7, "bold")
        ).pack( pady=(0, 2))
        self.reload_canvas = tk.Canvas(
            reload_col, width=118, height=32, bg=PANEL_BG, highlightthickness=0, cursor="hand2"
        )
        self.reload_canvas.pack()
        self.reload_canvas.bind("<Button-1>", lambda e: self.trigger_single_scan())
        self._draw_reload_pill()

        # 2. Công tắc gạt (Toggle Switch) bo tròn mềm mại cho START / STOP
        switch_col = tk.Frame(sig_right, bg=PANEL_BG)
        switch_col.pack(side=tk.LEFT)
        tk.Label(
            switch_col, text="TỰ ĐỘNG TRADE (AUTO)", bg=PANEL_BG, fg=TEXT_MUTED,
            font=("Segoe UI", 7, "bold")
        ).pack(pady=(0, 2))
        self.toggle_switch_canvas = tk.Canvas(
            switch_col, width=118, height=32, bg=PANEL_BG, highlightthickness=0, cursor="hand2"
        )
        self.toggle_switch_canvas.pack()
        self.toggle_switch_canvas.bind("<Button-1>", lambda e: self.toggle_bot_start_stop())
        self._draw_toggle_switch()

        sig_left = tk.Frame(sig_box, bg=PANEL_BG)
        sig_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.signal_title_lbl = tk.Label(
            sig_left, text="⏳ ĐANG TỰ ĐỘNG CHẠY PHÂN TÍCH AI LẦN ĐẦU...",
            bg=PANEL_BG, fg=GOLD_ACCENT, font=("Segoe UI", 11, "bold"), anchor="w"
        )
        self.signal_title_lbl.pack(fill=tk.X)

        self.signal_detail_lbl = tk.Label(
            sig_left,
            text="💡 Rê chuột: Crosshair | Lăn chuột: Co giãn nến | Kéo chuột: Di chuyển | Chuột phải / Nháy đúp: Reset.",
            bg=PANEL_BG, fg=TEXT_MAIN, font=("Consolas", 8), justify=tk.LEFT, anchor="w"
        )
        self.signal_detail_lbl.pack(fill=tk.X, pady=(2, 0))

        # THANH TRẠNG THÁI HOẠT ĐỘNG BOT CHUYÊN DỤNG (BOT STATUS STRIP - CANH GIỮA, CHỮ ĐỎ)
        self.bot_status_frame = tk.Frame(
            sig_left, bg="#121824", padx=8, pady=3,
            highlightthickness=1, highlightbackground="#222f44"
        )
        self.bot_status_frame.pack(fill=tk.X, pady=(4, 0))

        # Giữ đối tượng label để tương thích ngược nếu có hàm tham chiếu, không pack để chữ canh giữa hoàn hảo 100%
        self.bot_status_icon_lbl = tk.Label(self.bot_status_frame, text="", bg="#121824")
        self.bot_countdown_lbl = tk.Label(self.bot_status_frame, text="", bg="#121824")

        self.bot_status_text_lbl = tk.Label(
            self.bot_status_frame,
            text="",
            bg="#121824", fg=RED_DOWN, font=("Segoe UI", 9, "bold"),
            anchor="center", justify="center"
        )
        self.bot_status_text_lbl.pack(fill=tk.BOTH, expand=True)

        # Menu chuột phải trên biểu đồ kiểu TradingView
        self.chart_context_menu = tk.Menu(
            self.root, tearoff=0, bg="#19202c", fg=TEXT_MAIN,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 9, "bold")
        )
        self.chart_context_menu.add_command(label="🔄 Reset biểu đồ (Auto-Fit)", command=self.reset_chart_view)
        self.chart_context_menu.add_separator()
        self.chart_context_menu.add_command(label="➕ Mở rộng nến (Phóng to ngang)", command=lambda: self.adjust_chart_horizontal_scale(0.75))
        self.chart_context_menu.add_command(label="➖ Thu hẹp nến (Xem nhiều nến hơn)", command=lambda: self.adjust_chart_horizontal_scale(1.35))

        # Biểu đồ Nến tương tác trực tiếp bằng chuột + Thanh công cụ chọn nhanh Khung M1/M5/M15/H1
        chart_frame = tk.Frame(right_panel, bg=PANEL_BG)
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        chart_toolbar = tk.Frame(chart_frame, bg="#131822", padx=10, pady=4, highlightthickness=1, highlightbackground="#232d3f")
        chart_toolbar.pack(fill=tk.X)

        tk.Label(
            chart_toolbar, text="XEM BIỂU ĐỒ:", bg="#131822", fg=TEXT_MUTED,
            font=("Segoe UI", 8, "bold")
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.tf_quick_btns = {}
        for tf_opt in ("M1", "M5", "M15", "H1"):
            b = tk.Button(
                chart_toolbar, text=tf_opt,
                bg="#222c3c", fg=TEXT_MAIN,
                activebackground=GOLD_ACCENT, activeforeground="#000000",
                font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=10, pady=1, cursor="hand2",
                command=lambda t=tf_opt: self.switch_chart_timeframe_quick(t)
            )
            b.pack(side=tk.LEFT, padx=2)
            self.tf_quick_btns[tf_opt] = b

        tk.Label(
            chart_toolbar, text="(Chỉ đổi nến xem, AI giữ nguyên theo Menu Cài đặt)",
            bg="#131822", fg="#64748b", font=("Segoe UI", 7, "italic")
        ).pack(side=tk.LEFT, padx=(6, 0))

        self.ohlc_hover_lbl = tk.Label(
            chart_toolbar, text="Rê chuột lên nến để xem giá OHLC & Thời gian",
            bg="#131822", fg=CYAN_LIVE, font=("Consolas", 8, "bold"), anchor="e"
        )
        self.ohlc_hover_lbl.pack(side=tk.RIGHT, padx=(6, 0))

        self.fig = Figure(figsize=(9, 4.4), dpi=100, facecolor=PANEL_BG)
        self.ax = self.fig.add_subplot(111)
        self._style_axes()

        self.canvas = FigureCanvasTkAgg(self.fig, master=chart_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

        self.canvas.mpl_connect("scroll_event", self._on_mouse_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_mouse_press)
        self.canvas.mpl_connect("button_release_event", self._on_mouse_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_mouse_motion)
        self.canvas.mpl_connect("resize_event", lambda e: setattr(self, "_chart_bg_bbox", None))

        # KHU VỰC DƯỚI CÙNG: BẢNG ĐIỀU KHIỂN (XAUUSD & NHẬT KÝ HỆ THỐNG)
        self.bottom_notebook = ttk.Notebook(right_panel, height=188)
        self.bottom_notebook.pack(fill=tk.X)

        # TAB 0 (Mặc định khi mở App): XAUUSD (BIỂU ĐỒ & LỆNH REAL-TIME)
        pos_tab_xau = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
        self.bottom_notebook.add(pos_tab_xau, text="XAUUSD (BIỂU ĐỒ & LỆNH REAL-TIME)")
        self._build_positions_tab_ui(pos_tab_xau, "XAUUSD")

        # Mặc định khởi tạo trỏ vào Tab XAUUSD
        self.pos_tree = self.pos_tree_by_profile["XAUUSD"]
        self.pos_summary_lbl = self.pos_summary_lbl_by_profile["XAUUSD"]

        # TAB 1: NHẬT KÝ HỆ THỐNG & FILE LOG HOẠT ĐỘNG
        log_tab = tk.Frame(self.bottom_notebook, bg=PANEL_BG)
        self.bottom_notebook.add(log_tab, text="📋 NHẬT KÝ HỆ THỐNG")

        log_container = tk.Frame(log_tab, bg=PANEL_BG)
        log_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        log_header_bar = tk.Frame(log_container, bg="#131822", padx=6, pady=2)
        log_header_bar.pack(fill=tk.X, pady=(0, 3))
        tk.Label(
            log_header_bar, text="📋 NHẬT KÝ HỆ THỐNG & HOẠT ĐỘNG BOT (TỰ LƯU FILE LOG)",
            bg="#131822", fg=CYAN_LIVE, font=("Segoe UI", 8, "bold"), anchor="w"
        ).pack(side=tk.LEFT)
        tk.Button(
            log_header_bar, text="📂 Mở Thư Mục Log", bg="#232d3f", fg=TEXT_MAIN,
            activebackground=GOLD_ACCENT, activeforeground="#000000",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=self.open_log_folder
        ).pack(side=tk.RIGHT, padx=(3, 0))
        tk.Button(
            log_header_bar, text="⚠️ Xem Log Lỗi", bg="#5c232d", fg="#ffffff",
            activebackground=RED_DOWN, activeforeground="#ffffff",
            font=("Segoe UI", 7, "bold"), relief=tk.FLAT, padx=6, pady=1, cursor="hand2",
            command=self.open_error_log_file
        ).pack(side=tk.RIGHT)

        self.log_text = tk.Text(log_container, bg="#131822", fg="#b7bdc6", font=("Consolas", 8), relief=tk.FLAT)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        self.tree = None

        self.bottom_notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_positions_tab_ui(self, pos_tab: tk.Frame, profile_key: str):
        """Dựng giao diện bảng quản lý lệnh đang mở Real-Time cho XAUUSD."""
        pos_top_bar = tk.Frame(pos_tab, bg=PANEL_BG, pady=4, padx=6)
        pos_top_bar.pack(fill=tk.X)

        tk.Button(
            pos_top_bar, text="🔥 ĐÓNG TẤT CẢ (CLOSE ALL)", bg=RED_DOWN, fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2,
            command=self.close_all_positions
        ).pack(side=tk.RIGHT, padx=3)

        tk.Button(
            pos_top_bar, text="❌ Đóng Lệnh Chọn", bg="#8c2d3b", fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2,
            command=self.close_selected_position
        ).pack(side=tk.RIGHT, padx=3)

        tk.Button(
            pos_top_bar, text="🛡️ Dời SL về Entry", bg="#235c46", fg="#ffffff",
            font=("Segoe UI", 8, "bold"), relief=tk.FLAT, padx=8, pady=2,
            command=self.breakeven_all_positions
        ).pack(side=tk.RIGHT, padx=3)

        summary_lbl = tk.Label(
            pos_top_bar, text=f"Đang tải danh sách lệnh {profile_key} từ MT5...",
            bg=PANEL_BG, fg=CYAN_LIVE, font=("Consolas", 9, "bold")
        )
        summary_lbl.pack(side=tk.LEFT)

        pos_cols = ("ticket", "time", "duration", "symbol", "type", "volume", "open_price", "current_price", "sl", "tp", "profit", "status")
        pos_headers = {
            "ticket": "Mã Lệnh (Ticket)",
            "time": "Giờ",
            "duration": "Thời gian giữ",
            "symbol": "Mã",
            "type": "Loại",
            "volume": "Lot",
            "open_price": "Giá Vào (Entry)",
            "current_price": "Giá Hiện Tại / Đóng",
            "sl": "SL (Cắt lỗ)",
            "tp": "TP (Chốt lời)",
            "profit": "Lãi / Lỗ (USD)",
            "status": "Trạng thái Bảo vệ / Kết quả"
        }
        pos_widths = {
            "ticket": 85, "time": 95, "duration": 95, "symbol": 70, "type": 50, "volume": 45,
            "open_price": 85, "current_price": 90, "sl": 75, "tp": 75, "profit": 90, "status": 170
        }

        tree_container = tk.Frame(pos_tab, bg=PANEL_BG)
        tree_container.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        tree = ttk.Treeview(tree_container, columns=pos_cols, show="headings", height=5)
        scrollbar = ttk.Scrollbar(tree_container, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for c in pos_cols:
            tree.heading(c, text=pos_headers[c])
            tree.column(c, width=pos_widths[c], anchor=tk.CENTER)

        tree.tag_configure("profit_pos", foreground=GREEN_UP)
        tree.tag_configure("profit_neg", foreground=RED_DOWN)
        tree.tag_configure("closed_pos", foreground="#34d399")
        tree.tag_configure("closed_neg", foreground="#f87171")
        tree.tag_configure("closed_be", foreground="#94a3b8")

        tree.bind("<<TreeviewSelect>>", lambda e, pk=profile_key: self._on_pos_tree_select(pk))
        tree.bind("<Button-1>", lambda e, pk=profile_key: self._on_pos_tree_click(e, pk), add="+")
        tree.bind("<Escape>", self._deselect_all_orders_and_reset)

        self.pos_tree_by_profile[profile_key] = tree
        self.pos_summary_lbl_by_profile[profile_key] = summary_lbl

    def _get_selected_tickets_for_profile(self, profile_key: str = None) -> set:
        """Lấy danh sách mã ticket (int) các lệnh đang được người dùng nhấp chọn trong bảng vị thế."""
        pk = profile_key or self.active_profile_key
        tree = getattr(self, "pos_tree_by_profile", {}).get(pk, getattr(self, "pos_tree", None))
        if tree is None:
            return set()
        selected = set()
        try:
            for item_id in tree.selection():
                vals = tree.item(item_id, "values")
                if vals and len(vals) > 0:
                    raw_ticket = str(vals[0]).replace("#", "").strip()
                    if raw_ticket.isdigit():
                        selected.add(int(raw_ticket))
        except Exception:
            pass
        return selected

    def _on_pos_tree_select(self, profile_key: str = None):
        """Xử lý sự kiện khi người dùng chọn lệnh trong danh sách phía dưới: làm nổi bật lệnh tương ứng trên biểu đồ."""
        pk = profile_key or self.active_profile_key
        if pk == self.active_profile_key:
            sym = self.symbol_var.get().strip() or SYMBOL_XAU
            tf = self.tf_var.get().strip() or "M1"
            self._refresh_realtime_chart(sym, tf, force_full_redraw=True)

    def _on_pos_tree_click(self, event, profile_key: str = None):
        """
        1. Nếu người dùng nhấp vào hàng tiêu đề (heading), chặn hoàn toàn hiệu ứng nút bấm.
        2. Nếu người dùng nhấp vào khoảng trống ngoài các dòng lệnh, tự động bỏ chọn để biểu đồ trở về hiển thị bình thường.
        """
        pk = profile_key or self.active_profile_key
        tree = getattr(self, "pos_tree_by_profile", {}).get(pk, getattr(self, "pos_tree", None))
        if tree is not None:
            region = tree.identify_region(event.x, event.y)
            if region == "heading":
                return "break"
            clicked_row = tree.identify_row(event.y)
            if not clicked_row and tree.selection():
                tree.selection_remove(tree.selection())
                self._on_pos_tree_select(pk)

    def _deselect_all_orders_and_reset(self, event=None):
        """Khi người dùng nhấn phím ESC: Bỏ chọn dòng lệnh đang được tô sáng và đưa biểu đồ trở về trạng thái ban đầu."""
        pk = self.active_profile_key
        tree = getattr(self, "pos_tree_by_profile", {}).get(pk, getattr(self, "pos_tree", None))
        cleared = False
        if tree is not None and tree.winfo_exists():
            sel = tree.selection()
            if sel:
                tree.selection_remove(sel)
                cleared = True
            tree.focus("")

        for p_key, t_widget in getattr(self, "pos_tree_by_profile", {}).items():
            if t_widget is not None and t_widget != tree and t_widget.winfo_exists():
                s = t_widget.selection()
                if s:
                    t_widget.selection_remove(s)
                    t_widget.focus("")
                    if p_key == pk:
                        cleared = True

        sym = self.symbol_var.get().strip() or SYMBOL_XAU
        tf = self.tf_var.get().strip() or "M1"
        self._refresh_realtime_chart(sym, tf, force_full_redraw=True)

    # =========================================================================
    # TỰ ĐỘNG LƯU & KHÔI PHỤC CÀI ĐẶT NGƯỜI DÙNG (XAUUSD)
    # =========================================================================
    @staticmethod
    def _format_duration(duration_seconds: int) -> str:
        """Định dạng thời gian giữ lệnh thành chuỗi hiển thị trực quan (VD: 45s, 12p 30s, 1h 15p)."""
        if duration_seconds < 0:
            duration_seconds = 0
        if duration_seconds < 60:
            return f"{duration_seconds}s"
        m = duration_seconds // 60
        s = duration_seconds % 60
        if m < 60:
            return f"{m}p {s:02d}s"
        h = m // 60
        rem_m = m % 60
        return f"{h}h {rem_m:02d}p"

    def _get_settings_vars_map(self) -> dict:
        return {
            "symbol": self.symbol_var,
            "analysis_tf": self.analysis_tf_var,
            "tf": self.tf_var,
            "lot": self.lot_var,
            "max_pos": self.max_pos_var,
            "magic_number": self.magic_number_var,
            "scenarios": self.scenarios_var,
            "consensus": self.consensus_var,
            "max_dispersion": self.max_dispersion_var,
            "min_move": self.min_move_var,
            "max_spread": self.max_spread_var,
            "sl_atr": self.sl_atr_var,
            "sl_buffer": self.sl_buffer_var,
            "max_sl_price": self.max_sl_price_var,
            "rr": self.rr_var,
            "min_entry_spacing": self.min_entry_spacing_var,
            "entry_mode": self.entry_mode_var,
            "one_order_per_candle": self.one_order_per_candle_var,
            "auto_be": self.auto_be_var,
            "be_trigger_r": self.be_trigger_r_var,
            "auto_trail": self.auto_trail_var,
            "trail_dist_r": self.trail_dist_r_var,
            "auto_rev_close": self.auto_rev_close_var,
            "rev_consensus": self.rev_consensus_var,
            "rev_min_move": self.rev_min_move_var,
            "be_plus_price": self.be_plus_price_var,
            "basket_tp_usd": self.basket_tp_usd_var,
            "basket_cooldown_mins": self.basket_cooldown_mins_var,
            "max_hold_mins": self.max_hold_mins_var,
            "max_hold_action": self.max_hold_action_var,
            "daily_tp_usd": self.daily_tp_usd_var,
            "daily_sl_usd": self.daily_sl_usd_var,
            "max_consec_loss": self.max_consec_loss_var,
            "cooldown_mins": self.cooldown_mins_var,
            "fast_sl_cooldown_mins": self.fast_sl_cooldown_mins_var,
            "engine": self.engine_var,
            "model": self.model_var,
            "hf_space": self.hf_space_var,
            "hf_token": self.hf_token_var,
            "lookback": self.lookback_var,
            "pred_len": self.pred_len_var,
            "realtime_chart": self.realtime_chart_var,
        }

    def show_toast(self, message: str, is_error: bool = False, duration_ms: int = 2000):
        """Hiển thị thông báo Toast tinh gọn ở góc trên bên phải cửa sổ Bot, tự động ẩn sau 2 giây (không chặn giao diện, Thread-Safe)."""
        if threading.current_thread() is not threading.main_thread():
            self._ui_after(0, lambda: self.show_toast(message, is_error, duration_ms))
            return
        try:
            if getattr(self, "_active_toast", None) is not None:
                try:
                    self._active_toast.destroy()
                except Exception:
                    pass
                self._active_toast = None

            toast = tk.Toplevel(self.root)
            self._active_toast = toast
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)

            bg_color = "#1e293b"  # Slate tối thanh lịch, chuyên nghiệp, không màu mè
            border_color = "#ef4444" if is_error else "#10b981"
            fg_color = "#fca5a5" if is_error else "#6ee7b7"

            frame = tk.Frame(
                toast,
                bg=bg_color,
                padx=14,
                pady=8,
                highlightthickness=1,
                highlightbackground=border_color
            )
            frame.pack(fill=tk.BOTH, expand=True)

            icon_str = "⚠️" if is_error else "✓"
            tk.Label(
                frame,
                text=f"{icon_str}  {message}",
                bg=bg_color,
                fg=fg_color,
                font=("Segoe UI", 9, "bold"),
                anchor="w"
            ).pack(side=tk.LEFT)

            toast.update_idletasks()
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            tw = toast.winfo_width()

            pos_x = rx + rw - tw - 25
            pos_y = ry + 45
            toast.geometry(f"+{max(0, pos_x)}+{max(0, pos_y)}")

            self.root.after(duration_ms, lambda t=toast: self._dismiss_toast(t))
        except Exception as e:
            self.log_error("SHOW_TOAST", e, show_in_ui=False)

    def _dismiss_toast(self, toast):
        try:
            if toast and toast.winfo_exists():
                toast.destroy()
            if getattr(self, "_active_toast", None) == toast:
                self._active_toast = None
        except Exception:
            pass

    def manual_save_user_settings(self):
        """Người dùng nhấn nút Lưu cài đặt: Ghi ra file và hiển thị Toast góc phải tự tắt sau 2 giây."""
        saved_ok = self.save_user_settings(show_feedback=True)
        now_str = datetime.now().strftime("%H:%M:%S")
        if saved_ok:
            self.log(f"[CÀI ĐẶT] ✓ Đã lưu thành công toàn bộ thông số và Token của Tab {self.active_profile_key} vào ổ cứng ({now_str}).")
            self.show_toast(f"Đã lưu cài đặt [{self.active_profile_key}] & Áp dụng ngay!", is_error=False, duration_ms=2000)
        else:
            self.log(f"[CÀI ĐẶT] ⚠️ Không thể lưu cài đặt ({now_str}).")
            self.show_toast("Không thể lưu file cài đặt!", is_error=True, duration_ms=3000)

    def export_settings_file(self):
        """Xuất toàn bộ cấu hình 2 Tab ra file .json để sao lưu hoặc đồng bộ sang máy khác/VPS."""
        try:
            self.save_user_settings(show_feedback=False)
            default_name = f"kronos_settings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path = filedialog.asksaveasfilename(
                parent=self.root,
                title="Xuất File Cấu Hình",
                defaultextension=".json",
                initialfile=default_name,
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
            )
            if not file_path:
                return
            if os.path.exists(SETTINGS_FILE):
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {
                    "last_active_tab": self.active_profile_key,
                    "profiles": dict(self.profile_settings)
                }
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.log(f"[CẤU HÌNH] Đã xuất file cài đặt thành công: {os.path.basename(file_path)}")
            self.show_toast(f"Đã xuất cấu hình: {os.path.basename(file_path)}", is_error=False, duration_ms=2500)
        except Exception as e:
            self.log_error("EXPORT_SETTINGS", e)
            self.show_toast(f"Lỗi xuất file: {e}", is_error=True, duration_ms=3000)

    def import_settings_file(self):
        """Nhập file .json từ máy khác vào, cập nhật trực tiếp lên giao diện và lưu vào máy."""
        try:
            file_path = filedialog.askopenfilename(
                parent=self.root,
                title="Chọn File Cấu Hình Cần Nhập",
                filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")]
            )
            if not file_path:
                return

            with open(file_path, "r", encoding="utf-8") as f:
                imported_data = json.load(f)

            if not isinstance(imported_data, dict):
                raise ValueError("Định dạng file không hợp lệ (không phải đối tượng JSON).")

            os.makedirs(SETTINGS_DIR, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(imported_data, f, ensure_ascii=False, indent=2)

            self.load_user_settings()
            now_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(
                    text=f"✓ Đã nạp từ file lúc {now_str}",
                    fg="#10b981"
                )
            self.log(f"[CẤU HÌNH] Đã nhập và áp dụng thành công cấu hình từ: {os.path.basename(file_path)}")
            self.show_toast("Đã nhập & Áp dụng cấu hình thành công!", is_error=False, duration_ms=2500)
        except Exception as e:
            self.log_error("IMPORT_SETTINGS", e)
            self.show_toast(f"Lỗi nhập file: {e}", is_error=True, duration_ms=3000)

    def save_user_settings(self, *args, show_feedback: bool = False) -> bool:
        """Lưu toàn bộ cài đặt của bot Vàng XAUUSD."""
        if self._loading_settings:
            return False
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            cur_data = {k: v.get() for k, v in self._get_settings_vars_map().items()}
            self.profile_settings["XAUUSD"] = dict(cur_data)

            # Lấy HF Token và Space ID từ UI (nếu người dùng xóa thì lưu rỗng, không tự ý khôi phục token cũ)
            token_val = str(self.hf_token_var.get()).strip()
            space_val = str(self.hf_space_var.get()).strip()

            self.profile_settings["XAUUSD"]["hf_token"] = token_val
            self.profile_settings["XAUUSD"]["hf_space"] = space_val

            payload = dict(self.profile_settings.get("XAUUSD", cur_data))
            payload["hf_token"] = token_val
            payload["hf_space"] = space_val
            payload["last_active_tab"] = "XAUUSD"
            payload["profiles"] = {
                "XAUUSD": dict(self.profile_settings["XAUUSD"]),
            }
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            now_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(
                    text=f"✓ Đã lưu (XAUUSD) lúc {now_str}",
                    fg="#10b981"
                )
            return True
        except Exception as e:
            now_str = datetime.now().strftime("%H:%M:%S")
            if hasattr(self, "save_status_lbl"):
                self.save_status_lbl.config(
                    text=f"⚠️ Lỗi lưu file: {e} ({now_str})",
                    fg="#ef4444"
                )
            return False

    def load_user_settings(self):
        if not os.path.exists(SETTINGS_FILE):
            self.active_profile_key = "XAUUSD"
            self._sync_active_ctx_to_self()
            return
        try:
            self._loading_settings = True
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data.get("profiles"), dict) and "XAUUSD" in data["profiles"]:
                saved_p = data["profiles"].get("XAUUSD", {})
                if isinstance(saved_p, dict):
                    merged = dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"])
                    for k, val in saved_p.items():
                        if k in merged:
                            if k == "model" and isinstance(val, str) and val.startswith("Kronos-"):
                                val = val.replace("Kronos-", "AI-", 1)
                            elif k == "engine" and isinstance(val, str) and "trực tiếp" in val:
                                val = "Chạy trực tiếp máy Local"
                            merged[k] = val
                    self.profile_settings["XAUUSD"] = merged
            else:
                # Tự động nạp từ file cài đặt phẳng
                for k in DEFAULT_PROFILE_SETTINGS["XAUUSD"].keys():
                    if k in data:
                        val = data[k]
                        if k == "model" and isinstance(val, str) and val.startswith("Kronos-"):
                            val = val.replace("Kronos-", "AI-", 1)
                        elif k == "engine" and isinstance(val, str) and "trực tiếp" in val:
                            val = "Chạy trực tiếp máy Local"
                        self.profile_settings["XAUUSD"][k] = val

            token_saved = str(self.profile_settings.get("XAUUSD", {}).get("hf_token", "")).strip()
            if not token_saved and isinstance(data, dict):
                token_saved = str(data.get("hf_token", "")).strip()

            if token_saved:
                self.profile_settings["XAUUSD"]["hf_token"] = token_saved

            self.active_profile_key = "XAUUSD"
            self._sync_active_ctx_to_self()

            if hasattr(self, "chart_view_tf_by_profile"):
                self.chart_view_tf_by_profile["XAUUSD"] = str(self.profile_settings.get("XAUUSD", {}).get("tf", "M5")).strip()
            if hasattr(self, "tf_quick_btns"):
                self._highlight_active_tf_btn(self.chart_view_tf_by_profile.get("XAUUSD", "M5"))

            if hasattr(self, "bottom_notebook"):
                try:
                    self.bottom_notebook.select(0)
                except Exception:
                    pass

            self.log("Đã khôi phục hồ sơ cài đặt XAUUSD thành công.")
            if token_saved:
                self.log(f"🔑 Hugging Face Token đã sẵn sàng ({token_saved[:5]}...{token_saved[-4:]}).")
        except Exception as e:
            self.log(f"⚠️ Lỗi đọc file cài đặt user_settings.json: {e}")
        finally:
            self._loading_settings = False
            self.save_user_settings()

    def reset_section_settings(self, section_num: int):
        """Khôi phục các thông số của một mục cụ thể về giá trị mặc định tối ưu theo Tab đang chọn."""
        section_map = {
            1: ("Mục 1: Cài đặt cơ bản", ["symbol", "analysis_tf", "tf", "lot", "max_pos", "magic_number"]),
            2: ("Mục 2: Bộ lọc Entry & Nhồi lệnh", ["scenarios", "consensus", "max_dispersion", "min_move", "max_spread", "sl_atr", "sl_buffer", "max_sl_price", "rr", "min_entry_spacing", "entry_mode", "one_order_per_candle"]),
            3: ("Mục 3: Quản lý lệnh & Chốt lời", ["auto_be", "be_trigger_r", "auto_trail", "trail_dist_r", "auto_rev_close", "rev_consensus", "rev_min_move", "be_plus_price", "basket_tp_usd", "basket_cooldown_mins", "max_hold_mins", "max_hold_action", "daily_tp_usd", "daily_sl_usd", "max_consec_loss", "cooldown_mins", "fast_sl_cooldown_mins"]),
            4: ("Mục 4: Phân tích AI & Mô hình", ["engine", "model", "hf_space", "hf_token", "lookback", "pred_len"]),
        }
        if section_num not in section_map:
            return

        sec_title, keys = section_map[section_num]
        pk = self.active_profile_key
        defaults = DEFAULT_PROFILE_SETTINGS.get(pk, DEFAULT_PROFILE_SETTINGS["XAUUSD"])
        smap = self._get_settings_vars_map()
        cur_token = str(self.hf_token_var.get()).strip()

        self._loading_settings = True
        try:
            for k in keys:
                if k == "hf_token" and cur_token:
                    continue  # Không xóa token bảo mật của người dùng khi reset
                if k in smap and k in defaults:
                    smap[k].set(defaults[k])
                    if pk in self.profile_settings:
                        self.profile_settings[pk][k] = defaults[k]
        finally:
            self._loading_settings = False

        self.save_user_settings()
        self.log(f"[CÀI ĐẶT] Đã khôi phục thông số mặc định tối ưu cho {sec_title} ({pk}).")

    def reset_all_settings(self):
        """Khôi phục toàn bộ thông số của Tab đang chọn về cấu hình mặc định tối ưu."""
        pk = self.active_profile_key
        if not messagebox.askyesno(
            "Xác nhận khôi phục",
            f"Bạn có chắc chắn muốn khôi phục TẤT CẢ thông số cài đặt của tab {pk} về giá trị mặc định tối ưu?"
        ):
            return

        defaults = DEFAULT_PROFILE_SETTINGS.get(pk, DEFAULT_PROFILE_SETTINGS["XAUUSD"])
        smap = self._get_settings_vars_map()
        cur_token = str(self.hf_token_var.get()).strip()

        self._loading_settings = True
        try:
            for k, val in defaults.items():
                if k == "hf_token" and cur_token:
                    continue  # Không xóa token bảo mật cá nhân khi reset thông số giao dịch
                if k in smap:
                    smap[k].set(val)
                    if pk in self.profile_settings:
                        self.profile_settings[pk][k] = val
        finally:
            self._loading_settings = False

        self.save_user_settings()
        self.log(f"[CÀI ĐẶT] Đã khôi phục TOÀN BỘ thông số mặc định tối ưu cho tab {pk}.")

    def _bind_auto_save_settings(self):
        for var in self._get_settings_vars_map().values():
            var.trace_add("write", self.save_user_settings)
        self.tf_var.trace_add("write", self._on_timeframe_changed)
        self.analysis_tf_var.trace_add("write", self._on_timeframe_changed)
        self.root.bind_all("<Button-1>", self._on_global_click_outside_menu, add="+")
        self.root.bind_all("<Button-3>", self._on_global_click_outside_menu, add="+")

    def _on_global_click_outside_menu(self, event):
        """Khi Menu đang sổ ra, nếu click chuột ở bất kỳ chỗ nào bên ngoài Menu thì tự động thu Menu vào."""
        if not self.left_menu_visible or self._drawer_anim_job is not None:
            return
        w = getattr(event, "widget", None)
        if w is self.menu_toggle_btn or w is self.drawer_close_btn:
            return
        w_str = str(w).lower()
        if "popdown" in w_str or "combobox" in w_str:
            return
        curr = w
        while curr is not None:
            if curr is self.left_panel:
                return
            curr = getattr(curr, "master", None)
        try:
            lx = self.left_panel.winfo_rootx()
            ly = self.left_panel.winfo_rooty()
            lw = self.left_panel.winfo_width()
            lh = self.left_panel.winfo_height()
            if lx <= event.x_root <= lx + lw and ly <= event.y_root <= ly + lh:
                return
        except Exception:
            pass
        self.toggle_left_menu()

    def _highlight_active_tf_btn(self, active_tf: str):
        if not hasattr(self, "tf_quick_btns"):
            return
        for tf_k, btn in self.tf_quick_btns.items():
            if tf_k == active_tf:
                btn.config(bg=GOLD_ACCENT, fg="#000000")
            else:
                btn.config(bg="#222c3c", fg=TEXT_MAIN)

    def switch_chart_timeframe_quick(self, tf_opt: str):
        """
        Đổi nhanh khung thời gian hiển thị nến trên biểu đồ (CHỈ ĐỂ XEM BIỂU ĐỒ - VIEW ONLY).
        Hoàn toàn ĐỘC LẬP: KHÔNG làm thay đổi Khung phân tích AI hay Khung vào lệnh trong Menu Cài đặt,
        và KHÔNG kích hoạt phân tích lại AI.
        """
        pk = self.active_profile_key
        if not hasattr(self, "chart_view_tf_by_profile"):
            self.chart_view_tf_by_profile = {}
        self.chart_view_tf_by_profile[pk] = tf_opt
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        if pk in self.profile_ctx:
            self.profile_ctx[pk]["user_zoomed"] = False
            self.profile_ctx[pk]["saved_xlim"] = None
            self.profile_ctx[pk]["saved_ylim"] = None

        self._highlight_active_tf_btn(tf_opt)

        cfg = self._get_profile_cfg(pk)
        symbol = str(cfg.get("symbol", pk)).strip()
        ana_tf = str(cfg.get("analysis_tf", "M15")).strip()
        scalp_tf = str(cfg.get("tf", "M5")).strip()

        try:
            chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
        except ValueError:
            chart_bars = 450

        # Tải nến theo khung người dùng muốn xem và vẽ lên biểu đồ
        try:
            df_view = self.fetch_mt5_candles(symbol, tf_opt, chart_bars, include_forming=True)
            if df_view is not None:
                self._draw_candlestick_chart(
                    df_hist=df_view,
                    scenarios=None,
                    mean_df=self.cached_mean_df,
                    action=self.cached_action,
                    sl_price=self.cached_sl,
                    tp_price=self.cached_tp,
                    symbol=symbol,
                    tf=tf_opt,
                    has_forming_candle=True
                )
        except Exception as e:
            self.log(f"⚠️ [{pk}] Chưa tải được nến khung [{tf_opt}] do MT5 đang mất kết nối: {e}")

        self.log(f"👁️ [{pk}] Đang xem biểu đồ khung [{tf_opt}]. (Cài đặt AI: Phân tích [{ana_tf}] -> Vào lệnh [{scalp_tf}]).")

    def _on_timeframe_changed(self, *args):
        if self._loading_settings:
            return
        pk = self.active_profile_key
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        if pk in self.profile_ctx:
            self.profile_ctx[pk]["user_zoomed"] = False
            self.profile_ctx[pk]["saved_xlim"] = None
            self.profile_ctx[pk]["saved_ylim"] = None
        scalp_tf = self.tf_var.get().strip()
        ana_tf = self.analysis_tf_var.get().strip()
        if not hasattr(self, "chart_view_tf_by_profile"):
            self.chart_view_tf_by_profile = {}
        if pk not in self.chart_view_tf_by_profile:
            self.chart_view_tf_by_profile[pk] = scalp_tf
        active_tf = self.chart_view_tf_by_profile.get(pk, scalp_tf)
        self._highlight_active_tf_btn(active_tf)
        self._update_forecast_tab_header()
        if ana_tf == scalp_tf:
            self.log(f"🔄 [{pk}] Cài đặt Menu: Soi [{scalp_tf}] -> Trade [{scalp_tf}]. Đang quét lại AI...")
        else:
            self.log(f"🔄 [{pk}] Cài đặt Menu Đa Khung: Phân tích AI trên [{ana_tf}] -> Vào lệnh Scalping trên [{scalp_tf}]. Đang quét lại AI...")
        self.trigger_single_scan(profile_key=pk)

    def toggle_left_menu(self):
        """Trượt mượt mà (Slide-Over Animation) cột Menu bên trái mà không làm giật hay vẽ lại biểu đồ."""
        if self._drawer_anim_job is not None:
            try:
                self.root.after_cancel(self._drawer_anim_job)
            except Exception:
                pass
            self._drawer_anim_job = None

        self.left_menu_visible = not self.left_menu_visible
        if self.left_menu_visible:
            self.left_panel.lift()
            self.menu_toggle_btn.config(bg="#32415c", fg="#ffffff")
            target_x = 0
        else:
            self.menu_toggle_btn.config(bg="#252f42", fg="#ffffff")
            target_x = -self.drawer_width - 5
            self.save_user_settings()

        self._animate_drawer_step(target_x)

    def _animate_drawer_step(self, target_x: int):
        diff = target_x - self.drawer_x
        if abs(diff) <= 4:
            self.drawer_x = target_x
            self.left_panel.place(x=self.drawer_x, y=0, relheight=1.0, width=self.drawer_width)
            self._drawer_anim_job = None
            return
        # Nội suy mượt kiểu Ease-Out (nhanh lúc đầu, êm dần lúc cuối)
        step = int(diff * 0.42)
        if step == 0:
            step = 4 if diff > 0 else -4
        self.drawer_x += step
        self.left_panel.place(x=self.drawer_x, y=0, relheight=1.0, width=self.drawer_width)
        self._drawer_anim_job = self.root.after(12, self._animate_drawer_step, target_x)

    def _register_active_instance_lock(self):
        """Ghi nhận PID và Handle cửa sổ của phiên đang chạy để chống mở trùng hoặc chạy ngầm 2 App."""
        try:
            os.makedirs(SETTINGS_DIR, exist_ok=True)
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            info = {
                "pid": os.getpid(),
                "hwnd": int(hwnd),
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(INSTANCE_LOCK_FILE, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _release_active_instance_lock(self):
        """Giải phóng file khóa phiên chạy khi đóng ứng dụng."""
        try:
            if os.path.exists(INSTANCE_LOCK_FILE):
                with open(INSTANCE_LOCK_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("pid") == os.getpid():
                    os.remove(INSTANCE_LOCK_FILE)
        except Exception:
            pass

    def _on_close_window(self):
        self._app_closing = True
        self.save_user_settings()
        self.auto_loop_running = False
        for pk in self.profile_ctx:
            self.profile_ctx[pk]["auto_loop_running"] = False
            self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
        try:
            self._ai_queue.put_nowait(None)
        except Exception:
            pass
        try:
            if MT5_AVAILABLE:
                with self._mt5_lock:
                    mt5.shutdown()
        except Exception:
            pass
        self._write_file_log("Đã đóng ứng dụng an toàn và giải phóng toàn bộ tiến trình chạy ngầm.")
        self._release_active_instance_lock()
        self._cleanup_workspace_junk()
        try:
            self.root.destroy()
        except Exception:
            pass
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os._exit(0)

    def _on_tab_changed(self, event):
        selected_idx = self.bottom_notebook.index(self.bottom_notebook.select())
        if selected_idx == 0:
            self._switch_active_profile("XAUUSD")
        else:
            # Tab 1: Nhật ký hệ thống - giữ nguyên thị trường và biểu đồ đang mở
            pass

        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        pk = self.active_profile_key
        ana_tf = self.analysis_tf_var.get().strip()
        scalp_tf = self.tf_var.get().strip()
        view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(pk, scalp_tf)
        active_tf = view_tf
        self._highlight_active_tf_btn(active_tf)
        if active_tf == scalp_tf and self.cached_df_hist is not None:
            active_df = self.cached_df_hist
        elif active_tf == ana_tf and self.cached_df_analysis is not None:
            active_df = self.cached_df_analysis
        else:
            try:
                chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
            except ValueError:
                chart_bars = 450
            active_df = self.fetch_mt5_candles(self.symbol_var.get().strip(), active_tf, chart_bars, include_forming=True)

        if active_df is not None:
            self._draw_candlestick_chart(
                active_df,
                None,
                self.cached_mean_df,
                self.cached_action, self.cached_sl, self.cached_tp,
                self.symbol_var.get().strip(), active_tf, has_forming_candle=True
            )

    # =========================================================================
    # TƯƠNG TÁC BIỂU ĐỒ KIỂU TRADINGVIEW (ZOOM / PAN / X-SCALE / Y-SCALE / CROSSHAIR)
    # =========================================================================
    def reset_chart_view(self):
        self.user_zoomed = False
        self.saved_xlim = None
        self.saved_ylim = None
        self._chart_visible_bars = 170
        pk = self.active_profile_key
        ana_tf = self.analysis_tf_var.get().strip()
        scalp_tf = self.tf_var.get().strip()
        view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(pk, scalp_tf)
        active_tf = view_tf
        if active_tf == scalp_tf and self.cached_df_hist is not None:
            active_df = self.cached_df_hist
        elif active_tf == ana_tf and self.cached_df_analysis is not None:
            active_df = self.cached_df_analysis
        else:
            try:
                chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
            except ValueError:
                chart_bars = 450
            active_df = self.fetch_mt5_candles(self.symbol_var.get().strip(), active_tf, chart_bars, include_forming=True)

        if active_df is not None:
            self._draw_candlestick_chart(
                active_df,
                None,
                self.cached_mean_df,
                self.cached_action, self.cached_sl, self.cached_tp,
                self.symbol_var.get().strip(), active_tf, has_forming_candle=True
            )

    def adjust_chart_horizontal_scale(self, factor: float):
        """Thu hẹp (factor > 1) hoặc giãn rộng (factor < 1) số nến hiển thị trên biểu đồ theo chiều ngang."""
        cur_xlim = self.ax.get_xlim()
        right_edge = cur_xlim[1]
        cur_span = max(cur_xlim[1] - cur_xlim[0], 12.0)
        total_x = max(self._chart_total_x, 80)
        new_span = float(np.clip(cur_span * factor, 18.0, total_x + 15.0))
        new_left = max(-2.5, right_edge - new_span)
        self.saved_xlim = (new_left, right_edge)
        self._chart_visible_bars = int(round(new_span))
        # Tự động căn lại trục giá Y vừa khít với vùng nến đang nhìn thấy
        auto_ylim = self._compute_visible_ylim(self.saved_xlim[0], self.saved_xlim[1])
        if auto_ylim is not None:
            self.saved_ylim = auto_ylim
            self.ax.set_ylim(self.saved_ylim)
        else:
            self.saved_ylim = self.ax.get_ylim()
        self.user_zoomed = True
        self.ax.set_xlim(self.saved_xlim)
        self._sync_xlim_dependents(self.saved_xlim)
        self._request_canvas_redraw()

    def _compute_visible_ylim(self, x0: float, x1: float):
        """
        Tính giới hạn trục giá Y (ylim) tự động ôm sát các cây nến nằm trong vùng hiển thị [x0, x1],
        kèm toàn bộ râu nến, giá Live, SL/TP và chừa lề an toàn để không bao giờ bị cắt đáy/đỉnh.
        """
        hist = self._chart_hist_df
        if hist is None or len(hist) == 0:
            return None
        n_hist = len(hist)
        i_start = max(0, min(n_hist - 1, int(math.floor(x0))))
        i_end = max(i_start, min(n_hist - 1, int(math.ceil(x1))))
        sub = hist.iloc[i_start:i_end + 1]
        if len(sub) == 0:
            return None

        lows = [float(sub["low"].min())]
        highs = [float(sub["high"].max())]

        # Luôn bao gồm nến đang chạy và giá Live
        live_p = float(hist["close"].iloc[-1])
        lows.extend([live_p, float(hist["low"].iloc[-1])])
        highs.extend([live_p, float(hist["high"].iloc[-1])])

        # Bao gồm cụm nến dự báo AI nếu có nến dự báo
        if self._chart_mean_df is not None and len(self._chart_mean_df) > 0 and x1 >= n_hist - 1:
            lows.append(float(self._chart_mean_df["low"].min()))
            highs.append(float(self._chart_mean_df["high"].max()))

        # Bao gồm SL / TP của AI nếu có
        if self.cached_sl is not None:
            lows.append(float(self.cached_sl))
            highs.append(float(self.cached_sl))
        if self.cached_tp is not None:
            lows.append(float(self.cached_tp))
            highs.append(float(self.cached_tp))

        # Bao gồm các mức giá của Lệnh đang mở (Entry, SL, TP)
        sym = self.symbol_var.get().strip() if hasattr(self, "symbol_var") else "XAUUSDm"
        if MT5_AVAILABLE:
            try:
                with self._mt5_lock:
                    open_pos = mt5.positions_get(symbol=sym)
                if open_pos:
                    for p in open_pos:
                        lows.append(float(p.price_open))
                        highs.append(float(p.price_open))
                        if p.sl > 0:
                            lows.append(float(p.sl))
                            highs.append(float(p.sl))
                        if p.tp > 0:
                            lows.append(float(p.tp))
                            highs.append(float(p.tp))
            except Exception:
                pass

        y_min = min(lows)
        y_max = max(highs)
        raw_span = max(y_max - y_min, 0.5)

        # Chừa lề an toàn tối thiểu 16% ở trên và 16% ở dưới (tối thiểu 2.0 giá vàng)
        # để nhãn giá Live, đồng hồ đếm ngược và các râu nến không bao giờ bị cắt hay chui xuống dưới trục Y
        margin_pad = max(raw_span * 0.16, 2.0)
        target_y0 = y_min - margin_pad
        target_y1 = y_max + margin_pad

        return (target_y0, target_y1)

    def _sync_xlim_dependents(self, xlim):
        """Cập nhật mật độ nhãn thời gian trục X và vị trí nhãn Entry/SL/TP khi thu phóng hoặc kéo biểu đồ."""
        all_times = self._chart_all_times
        if all_times:
            i0 = max(0, int(math.floor(xlim[0])))
            i1 = min(len(all_times) - 1, int(math.ceil(xlim[1])))
            vis_count = max(1, i1 - i0 + 1)
            step = max(1, vis_count // 10)
            tick_indices = list(range(i0, i1 + 1, step))
            self.ax.set_xticks(tick_indices)
            self.ax.set_xticklabels([all_times[k] for k in tick_indices], color=TEXT_MAIN)

        if self._pos_text_artists:
            span = max(xlim[1] - xlim[0], 10.0)
            base_x = xlim[0] + span * 0.018
            col_step = span * 0.18
            for txt_artist, col_idx in self._pos_text_artists:
                try:
                    _, y_pos = txt_artist.get_position()
                    txt_artist.set_position((base_x + col_idx * col_step, y_pos))
                except Exception:
                    pass

    def _get_candle_countdown_str(self, candle_ts, tf_str: str, symbol: str = None) -> str:
        """Tính chuỗi thời gian đếm ngược (phút:giây) cho đến khi đóng cây nến hiện tại (chuẩn TradingView)."""
        if candle_ts is None:
            return "00:00"
        try:
            if hasattr(candle_ts, "timestamp"):
                c_ts = int(candle_ts.timestamp())
            elif isinstance(candle_ts, (int, float)):
                c_ts = int(candle_ts)
            else:
                c_ts = int(pd.to_datetime(candle_ts).timestamp())
        except Exception:
            return "00:00"

        tf_map = {
            "M1": 60,
            "M5": 300,
            "M15": 900,
            "M30": 1800,
            "H1": 3600,
            "H4": 14400,
            "D1": 86400,
        }
        dur = tf_map.get(str(tf_str).upper().strip(), 60)

        # Lấy mốc thời gian hiện tại (ưu tiên broker server time từ tick nếu có)
        now_ts = int(time.time())
        if MT5_AVAILABLE and symbol:
            try:
                with self._mt5_lock:
                    tick = mt5.symbol_info_tick(symbol)
                if tick and getattr(tick, "time", 0) > 0:
                    if abs(now_ts - tick.time) < 600:
                        now_ts = int(tick.time)
            except Exception:
                pass

        elapsed = max(0, now_ts - c_ts)
        if elapsed <= dur:
            rem = dur - elapsed
        else:
            rem = max(0, dur - (elapsed % dur))
            if rem == dur:
                rem = 0

        if rem >= 3600:
            h = rem // 3600
            m = (rem % 3600) // 60
            s = rem % 60
            return f"{h:02d}:{m:02d}:{s:02d}"
        else:
            m = rem // 60
            s = rem % 60
            return f"{m:02d}:{s:02d}"

    def _on_mouse_scroll(self, event):
        """Lăn chuột lên để mở rộng nến (xem rõ chi tiết), lăn chuột xuống để thu hẹp nến (xem bao quát nhiều nến hơn)."""
        if event.inaxes != self.ax:
            return
        scale_factor = 0.80 if event.button == "up" else 1.25
        self.adjust_chart_horizontal_scale(scale_factor)

    def _is_on_price_axis(self, event) -> bool:
        """Kiểm tra xem con trỏ chuột có đang nằm trên vùng Thanh Giá (cột số giá bên phải hoặc bên trái) không."""
        if event.x is None or event.y is None:
            return False
        bbox = self.ax.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            return False
        if not (bbox.y0 - 15 <= event.y <= bbox.y1 + 15):
            return False
        # Bao phủ toàn bộ cột số giá bên phải (từ mép phải đồ thị ra hết lề phải) và cột số giá bên trái
        return (event.x >= bbox.x1 - 18) or (event.x <= bbox.x0 + 18)

    def _is_on_time_axis(self, event) -> bool:
        """Kiểm tra xem con trỏ chuột có đang nằm trên Thanh Thời Gian (trục X phía dưới biểu đồ) không."""
        if event.x is None or event.y is None:
            return False
        bbox = self.ax.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            return False
        if not (bbox.x0 <= event.x <= bbox.x1):
            return False
        return 0 <= event.y <= bbox.y0 + 14

    def _on_mouse_press(self, event):
        # Nếu Menu đang mở mà click chuột vào biểu đồ (ngoài vùng Menu) -> Tự động thu Menu vào
        if self.left_menu_visible and self._drawer_anim_job is None:
            try:
                px = self.root.winfo_pointerx()
                lx = self.left_panel.winfo_rootx()
                lw = self.left_panel.winfo_width()
                if px > lx + lw:
                    self.toggle_left_menu()
            except Exception:
                self.toggle_left_menu()

        # Click chuột phải (Button 3) -> Hiển thị menu "Reset biểu đồ" như TradingView
        if event.button == 3:
            try:
                self.chart_context_menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
            finally:
                self.chart_context_menu.grab_release()
            return

        if event.dblclick:
            self.reset_chart_view()
            return

        if event.button == 1 and event.x is not None and event.y is not None:
            self._drag_start_px = (event.x, event.y)
            self._drag_start_xlim = self.ax.get_xlim()
            self._drag_start_ylim = self.ax.get_ylim()
            if self._is_on_price_axis(event):
                self._drag_mode = "Y_SCALE"
                self.canvas_widget.config(cursor="sb_v_double_arrow")
            elif self._is_on_time_axis(event):
                self._drag_mode = "X_SCALE"
                self.canvas_widget.config(cursor="sb_h_double_arrow")
            elif event.inaxes == self.ax:
                self._drag_mode = "PAN"
                self.canvas_widget.config(cursor="fleur")
            else:
                self._drag_start_px = None
                self._drag_mode = None

    def _on_mouse_release(self, event):
        self._drag_start_px = None
        self._drag_mode = None
        if self._is_on_price_axis(event):
            self.canvas_widget.config(cursor="sb_v_double_arrow")
        elif self._is_on_time_axis(event):
            self.canvas_widget.config(cursor="sb_h_double_arrow")
        else:
            self.canvas_widget.config(cursor="crosshair" if event.inaxes == self.ax else "")

    def _request_canvas_redraw(self, full: bool = True):
        """Gộp các yêu cầu vẽ lại biểu đồ trong khung 16ms (~60 FPS) kèm kỹ thuật Blitting giúp rê chuột Crosshair chỉ mất ~10ms (~100 FPS)."""
        if full:
            self._redraw_full_needed = True
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.root.after(14, self._flush_canvas_redraw)

    def _flush_canvas_redraw(self):
        self._redraw_pending = False
        full = self._redraw_full_needed or (self._chart_bg_bbox is None)
        self._redraw_full_needed = False
        try:
            cross_arts = (self.cross_vline, self.cross_hline, self.cross_price_annot, self.cross_time_annot)
            if full:
                vis_states = [art.get_visible() if art is not None else False for art in cross_arts]
                for art in cross_arts:
                    if art is not None:
                        art.set_visible(False)
                self.canvas.draw()
                self._chart_bg_bbox = self.canvas.copy_from_bbox(self.fig.bbox)
                any_vis = False
                for art, v in zip(cross_arts, vis_states):
                    if art is not None and v:
                        art.set_visible(True)
                        self.ax.draw_artist(art)
                        any_vis = True
                if any_vis:
                    self.canvas.blit(self.fig.bbox)
            else:
                self.canvas.restore_region(self._chart_bg_bbox)
                for art in cross_arts:
                    if art is not None and art.get_visible():
                        self.ax.draw_artist(art)
                self.canvas.blit(self.fig.bbox)
        except Exception:
            try:
                self.canvas.draw_idle()
            except Exception:
                pass

    def _hide_crosshair(self):
        self._last_hover_xy = None
        changed = False
        for art in (self.cross_vline, self.cross_hline, self.cross_price_annot, self.cross_time_annot):
            if art is not None and art.get_visible():
                art.set_visible(False)
                changed = True
        if changed:
            self._request_canvas_redraw(full=False)

    def _apply_crosshair_xy(self, snap_x: float, ydata: float):
        if self.cross_vline is None or self.cross_hline is None:
            return
        idx = int(round(snap_x))
        self.cross_vline.set_xdata([snap_x, snap_x])
        self.cross_vline.set_visible(True)
        self.cross_hline.set_ydata([ydata, ydata])
        self.cross_hline.set_visible(True)

        if self.cross_price_annot is not None:
            self.cross_price_annot.xy = (1.0, ydata)
            self.cross_price_annot.set_text(f" {ydata:,.2f} ")
            self.cross_price_annot.set_visible(True)

        hist = self._chart_hist_df
        mean_df = self._chart_mean_df
        n_hist = self._chart_n_hist

        if hist is not None and 0 <= idx < n_hist:
            r = hist.iloc[idx]
            ts_full = r["timestamps"].strftime("%d/%m %H:%M")
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            chg = c - o
            if self.cross_time_annot is not None:
                self.cross_time_annot.xy = (snap_x, 0.0)
                self.cross_time_annot.set_text(f" {ts_full} ")
                self.cross_time_annot.set_visible(True)
            if hasattr(self, "ohlc_hover_lbl"):
                col = GREEN_UP if chg >= 0 else RED_DOWN
                self.ohlc_hover_lbl.config(
                    text=f"🕒 {ts_full}  |  O: {o:.2f}   H: {h:.2f}   L: {l:.2f}   C: {c:.2f} ({chg:+.2f}$)",
                    fg=col
                )
        elif self.chart_mode == "AI_FORECAST" and mean_df is not None and n_hist <= idx < n_hist + len(mean_df):
            r = mean_df.iloc[idx - n_hist]
            ts_full = r["timestamps"].strftime("%d/%m %H:%M")
            o, h, l, c = float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])
            chg = c - o
            if self.cross_time_annot is not None:
                self.cross_time_annot.xy = (snap_x, 0.0)
                self.cross_time_annot.set_text(f" AI {ts_full} ")
                self.cross_time_annot.set_visible(True)
            if hasattr(self, "ohlc_hover_lbl"):
                self.ohlc_hover_lbl.config(
                    text=f"[Dự báo AI {ts_full}]  |  O: {o:.2f}   H: {h:.2f}   L: {l:.2f}   C: {c:.2f} ({chg:+.2f}$)",
                    fg=GOLD_ACCENT
                )
        else:
            if self.cross_time_annot is not None:
                self.cross_time_annot.set_visible(False)

    def _update_crosshair(self, event):
        """Hiển thị đường gióng chữ thập nét đứt + nhãn giá tối bên phải + thông số OHLC kiểu TradingView."""
        if event.inaxes != self.ax or event.xdata is None or event.ydata is None:
            self._hide_crosshair()
            return
        xdata = float(event.xdata)
        ydata = float(event.ydata)
        idx = int(round(xdata))
        snap_x = float(idx) if (0 <= idx < self._chart_total_x) else xdata
        self._last_hover_xy = (snap_x, ydata)
        self._apply_crosshair_xy(snap_x, ydata)
        self._request_canvas_redraw(full=False)

    def _on_mouse_motion(self, event):
        if self._drag_start_px is None:
            if self._is_on_price_axis(event):
                self.canvas_widget.config(cursor="sb_v_double_arrow")
                self._hide_crosshair()
            elif self._is_on_time_axis(event):
                self.canvas_widget.config(cursor="sb_h_double_arrow")
                self._hide_crosshair()
            elif event.inaxes == self.ax:
                self.canvas_widget.config(cursor="crosshair")
                self._update_crosshair(event)
            else:
                self.canvas_widget.config(cursor="")
                self._hide_crosshair()
            return

        if event.x is None or event.y is None:
            return

        dx_px = event.x - self._drag_start_px[0]
        dy_px = event.y - self._drag_start_px[1]

        bbox = self.ax.bbox
        if bbox.width <= 0 or bbox.height <= 0:
            return

        if self._drag_mode == "Y_SCALE":
            y0, y1 = self._drag_start_ylim
            y_center = (y0 + y1) * 0.5
            y_span = max(y1 - y0, 0.2)
            scale_factor = float(np.clip(1.006 ** (-dy_px), 0.15, 8.0))
            new_half = (y_span * scale_factor) * 0.5
            self.saved_xlim = self._drag_start_xlim
            self.saved_ylim = (y_center - new_half, y_center + new_half)
            self.user_zoomed = True
            self.ax.set_ylim(self.saved_ylim)
            self._request_canvas_redraw()
        elif self._drag_mode == "X_SCALE":
            x0, x1 = self._drag_start_xlim
            x_span = max(x1 - x0, 12.0)
            total_x = max(self._chart_total_x, 80)
            scale_factor = float(np.clip(1.006 ** (-dx_px), 0.2, 6.0))
            new_span = float(np.clip(x_span * scale_factor, 16.0, total_x + 20.0))
            new_left = max(-3.0, x1 - new_span)
            self.saved_xlim = (new_left, x1)
            self.saved_ylim = self._drag_start_ylim
            self.user_zoomed = True
            self.ax.set_xlim(self.saved_xlim)
            self._sync_xlim_dependents(self.saved_xlim)
            self._request_canvas_redraw()
        elif self._drag_mode == "PAN":
            x_span = self._drag_start_xlim[1] - self._drag_start_xlim[0]
            y_span = self._drag_start_ylim[1] - self._drag_start_ylim[0]

            dx_data = -(dx_px / bbox.width) * x_span
            dy_data = -(dy_px / bbox.height) * y_span

            total_x = max(self._chart_total_x, 80)
            new_x0 = float(np.clip(self._drag_start_xlim[0] + dx_data, -10.0, total_x - 5.0))
            new_x1 = new_x0 + x_span
            self.saved_xlim = (new_x0, new_x1)
            self.saved_ylim = (self._drag_start_ylim[0] + dy_data, self._drag_start_ylim[1] + dy_data)
            self.user_zoomed = True

            self.ax.set_xlim(self.saved_xlim)
            self.ax.set_ylim(self.saved_ylim)
            self._sync_xlim_dependents(self.saved_xlim)
            self._request_canvas_redraw()

    def _add_labeled_entry(self, parent, label_text, default_val, show=None, entry_width=10):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=label_text, bg=PANEL_BG, fg=TEXT_MAIN, font=("Segoe UI", 8), anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        var = tk.StringVar(value=default_val)
        entry = tk.Entry(
            row, textvariable=var, bg="#10141d", fg=TEXT_MAIN, insertbackground=TEXT_MAIN,
            relief=tk.FLAT, font=("Consolas", 8), show=show, width=entry_width,
            justify="center" if entry_width <= 12 else "left"
        )
        entry.pack(side=tk.RIGHT)
        entry.bind("<FocusOut>", lambda e: self.save_user_settings())
        entry.bind("<Return>", lambda e: self.save_user_settings())
        return var

    def _add_labeled_combo(self, parent, label_text, values, default_val, combo_width=None):
        row = tk.Frame(parent, bg=PANEL_BG)
        row.pack(fill=tk.X, pady=2)
        tk.Label(row, text=label_text, bg=PANEL_BG, fg=TEXT_MAIN, font=("Segoe UI", 8), anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
        var = tk.StringVar(value=default_val)

        # Tự động tính toán bề rộng combobox nếu có chuỗi dài để không bị che khuất chữ
        max_len = max((len(str(v)) for v in values), default=15) if values else 15
        if combo_width is None:
            calc_w = max(15, min(max_len + 1, 28))
        else:
            calc_w = combo_width

        cb = ttk.Combobox(row, textvariable=var, values=values, state="readonly", font=("Segoe UI", 8), width=calc_w)
        cb.pack(side=tk.RIGHT)
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_user_settings())

        # Mở rộng danh sách xổ xuống (Popdown) tương ứng với chuỗi dài nhất để đọc trọn vẹn 100% nội dung
        if max_len > 18:
            def _expand_popdown(event=None):
                def _do_resize():
                    try:
                        popdown = cb.tk.eval(f"ttk::combobox::PopdownWindow {cb}")
                        target_w = max(cb.winfo_width(), min(max_len * 8 + 35, 320))
                        cb.tk.eval(f"wm geometry {popdown} {target_w}x[winfo height {popdown}]")
                    except Exception:
                        pass
                cb.after(10, _do_resize)
            cb.bind("<ButtonPress-1>", _expand_popdown, add="+")
            cb.bind("<Down>", _expand_popdown, add="+")

        return var

    def _style_axes(self):
        self.ax.set_facecolor("#131822")
        self.ax.yaxis.set_ticks_position("both")
        self.ax.tick_params(axis="y", labelright=True, labelleft=True, colors=TEXT_MAIN, labelsize=8)
        self.ax.tick_params(axis="x", colors=TEXT_MAIN, labelsize=8)
        for spine in self.ax.spines.values():
            spine.set_color("#2b3548")
        self.ax.grid(True, linestyle="--", alpha=0.2, color="#4a5568")

    # =========================================================================
    # HỆ THỐNG GHI LOG RA FILE (NHẬT KÝ HOẠT ĐỘNG & KIỂM TRA LỖI APP / MT5)
    # =========================================================================
    def _get_daily_log_path(self) -> str:
        date_str = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(LOG_DIR, f"bot_activity_{date_str}.log")

    def _init_file_logger(self):
        """Khởi tạo thư mục lưu Log trong AppData, tự động dọn log cũ > 14 ngày và bắt mọi lỗi ngầm của Python/Tkinter."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            # Dọn dẹp các file log cũ quá 14 ngày hoặc cắt bớt nếu file lỗi vượt quá 5MB
            cutoff_ts = time.time() - 14 * 86400
            for fname in os.listdir(LOG_DIR):
                fpath = os.path.join(LOG_DIR, fname)
                if os.path.isfile(fpath) and fname.startswith("bot_activity_") and fname.endswith(".log"):
                    if os.path.getmtime(fpath) < cutoff_ts:
                        try:
                            os.remove(fpath)
                        except Exception:
                            pass
            if os.path.exists(ERROR_LOG_FILE) and os.path.getsize(ERROR_LOG_FILE) > 5 * 1024 * 1024:
                with open(ERROR_LOG_FILE, "rb") as f:
                    f.seek(-2 * 1024 * 1024, os.SEEK_END)
                    tail_bytes = f.read()
                with open(ERROR_LOG_FILE, "wb") as f:
                    f.write(tail_bytes)
        except Exception:
            pass

        def _tk_exception_handler(exc_type, exc_val, exc_tb):
            tb_str = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
            self._write_file_log(f"[TKINTER_ERROR] {exc_val}\n{tb_str}", is_error=True)

        def _thread_exception_handler(args):
            tb_str = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
            t_name = getattr(args.thread, "name", "BackgroundThread")
            self._write_file_log(f"[THREAD_ERROR - {t_name}] {args.exc_value}\n{tb_str}", is_error=True)

        self.root.report_callback_exception = _tk_exception_handler
        sys.excepthook = _tk_exception_handler
        threading.excepthook = _thread_exception_handler
        self._write_file_log("================================================================================")
        self._write_file_log("🚀 KHỞI ĐỘNG HỆ THỐNG SCALPING VÀNG XAUUSD (MT5 EXNESS)")

    def _write_file_log(self, text: str, is_error: bool = False):
        """Ghi nhật ký hoạt động và lỗi xuống ổ cứng an toàn đa luồng (Thread-Safe)."""
        ts_ms = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        clean_txt = str(text).rstrip("\r\n")
        log_entry = f"[{ts_ms}] {clean_txt}\n"
        try:
            with self._log_file_lock:
                os.makedirs(LOG_DIR, exist_ok=True)
                daily_path = self._get_daily_log_path()
                with open(daily_path, "a", encoding="utf-8") as f:
                    f.write(log_entry)
                if is_error:
                    with open(ERROR_LOG_FILE, "a", encoding="utf-8") as f_err:
                        f_err.write(log_entry)
        except Exception:
            pass

    def log_error(self, context: str, err: Exception = None, extra_info: str = "", show_in_ui: bool = True):
        """Ghi chi tiết lỗi của App hoặc lỗi giao tiếp MT5 (kèm Traceback & mt5.last_error) vào file app_error.log."""
        mt5_err_str = ""
        if MT5_AVAILABLE:
            try:
                with self._mt5_lock:
                    mt5_err_str = f" | MT5 last_error={mt5.last_error()}"
            except Exception:
                pass
        err_msg = f"❌ [LỖI - {context}] {err if err is not None else ''}{ (' | ' + extra_info) if extra_info else ''}{mt5_err_str}"
        tb_str = traceback.format_exc() if err is not None else ""
        full_err_log = f"{err_msg}\n{tb_str}" if (tb_str and "NoneType: None" not in tb_str) else err_msg
        self._write_file_log(full_err_log, is_error=True)
        if show_in_ui:
            ts = datetime.now().strftime("%H:%M:%S")
            self._ui_after(0, self._append_log, f"[{ts}] {err_msg}\n")

    def open_log_folder(self):
        """Mở trực tiếp thư mục chứa các file Log của App trên Windows Explorer."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            daily_path = self._get_daily_log_path()
            if not os.path.exists(daily_path):
                self._write_file_log("Mở thư mục kiểm tra Log.")
            if hasattr(os, "startfile"):
                os.startfile(LOG_DIR)
        except Exception as e:
            self.log_error("OPEN_LOG_FOLDER", e)

    def open_error_log_file(self):
        """Mở trực tiếp file Log lỗi (hoặc file Log hoạt động trong ngày nếu chưa có lỗi nào) bằng trình xem văn bản."""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            target_file = ERROR_LOG_FILE if (os.path.exists(ERROR_LOG_FILE) and os.path.getsize(ERROR_LOG_FILE) > 0) else self._get_daily_log_path()
            if not os.path.exists(target_file):
                self._write_file_log("Khởi tạo file nhật ký kiểm tra.")
            if hasattr(os, "startfile"):
                os.startfile(target_file)
        except Exception as e:
            self.log_error("OPEN_ERROR_LOG", e)

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        msg_str = str(msg)
        is_err = any(k in msg_str for k in ("❌", "⚠️", "Lỗi", "từ chối", "Không đóng được", "ERROR"))
        self._write_file_log(msg_str, is_error=is_err)
        self._ui_after(0, self._append_log, line)

    def _append_log(self, line: str):
        try:
            if hasattr(self, "log_text") and self.log_text.winfo_exists():
                self.log_text.insert("1.0", line)
                self.log_text.delete("150.0", tk.END)
        except Exception:
            pass

    # =========================================================================
    # LUỒNG REAL-TIME 1 GIÂY: CẬP NHẬT GIÁ, ĐẾM NGƯỢC, QUẢN LÝ LỆNH, CHART LIVE
    # =========================================================================
    def _realtime_tick_loop(self):
        if getattr(self, "_app_closing", False):
            return
        try:
            with self._mt5_lock:
                mt5_ok = bool(MT5_AVAILABLE and mt5.initialize())
            if mt5_ok:
                active_pk = self.active_profile_key
                symbol = self.symbol_var.get().strip()
                tf_str = self.tf_var.get().strip()
                with self._mt5_lock:
                    mt5.symbol_select(symbol, True)
                    acc = mt5.account_info()
                    tick = mt5.symbol_info_tick(symbol)

                self.heartbeat_toggle = not self.heartbeat_toggle
                dot = "🟢" if self.heartbeat_toggle else "🔵"
                now_str = datetime.now().strftime("%H:%M:%S")

                tf_sec_map = {"M1": 60, "M5": 300, "M15": 900, "H1": 3600}
                tf_sec = tf_sec_map.get(tf_str, 300)

                if tick is not None:
                    spread = tick.ask - tick.bid
                    rem_sec = tf_sec - (int(tick.time) % tf_sec)
                    mins, secs = divmod(rem_sec, 60)
                    scan_state = "ĐANG CANH" if self.auto_loop_running else "CHỜ"
                    self.live_clock_lbl.config(
                        text=f"{dot} {now_str} | [{active_pk}] Bid:{tick.bid:.2f} Ask:{tick.ask:.2f} (Spr:{spread:.2f}$) | ⏳Nến {tf_str}: {mins:02d}p:{secs:02d}s [{scan_state}]"
                    )

                if acc is not None:
                    algo_str = "Algo:BẬT" if acc.trade_expert else "Algo:TẮT"
                    self.mt5_status_lbl.config(
                        text=f"TK: {acc.login} | Dư: {acc.balance:.2f}$ | Eq: {acc.equity:.2f}$ | {algo_str}",
                        fg=GREEN_UP if acc.trade_expert else GOLD_ACCENT
                    )

                pos_count, total_profit = self._update_open_positions_and_manage(symbol, profile_key=active_pk)
                self._check_daily_profit_loss_limits(total_profit, tick, profile_key=active_pk)
                if tick is not None:
                    self._check_pending_pullback_order(symbol, tick, profile_key=active_pk)
                self._update_left_quick_info(symbol, tf_str, tick, pos_count, total_profit)
                self._update_bot_status_bar(active_pk)
                self._sync_self_to_active_ctx()

                if self.realtime_chart_var.get() and self.heartbeat_toggle and not self.is_analyzing and self._drag_start_px is None:
                    self._refresh_realtime_chart(symbol, tf_str)
        except Exception as e:
            self.log_error("REALTIME_TICK_LOOP", e, show_in_ui=False)
        finally:
            if not getattr(self, "_app_closing", False):
                self.root.after(1000, self._realtime_tick_loop)

    def _get_today_deals_stats(self, symbol: str = None, tick=None) -> tuple:
        """Tính tổng Lãi/Lỗ đã chốt (Realized PnL) và chuỗi lệnh thua liên tiếp trong ngày hôm nay theo Giờ Việt Nam (GMT+7)."""
        if not MT5_AVAILABLE:
            return 0.0, 0, None
        sym_key = symbol or "__ALL__"
        now_mono = time.monotonic()
        cached_entry = self._cached_deals_by_sym.get(sym_key)
        if cached_entry is not None and (now_mono - cached_entry[0]) < 4.0:
            return cached_entry[1]
        try:
            # Lấy ngày hôm nay theo Giờ Việt Nam (GMT+7, Asia/Ho_Chi_Minh)
            vn_now = datetime.now(tz=VN_TZ)
            vn_today_str = vn_now.strftime("%Y-%m-%d")
            # Điểm bắt đầu ngày hôm nay 00:00:00 (giờ VN) quy đổi sang timestamp UTC
            vn_today_start = vn_now.replace(hour=0, minute=0, second=0, microsecond=0)
            vn_today_start_ts = int(vn_today_start.timestamp())

            # Quét deals từ trước 00:00:00 VN (trừ 2 giờ dự phòng) đến hiện tại + 2 giờ
            query_from = datetime.fromtimestamp(vn_today_start_ts - 7200)
            query_to = datetime.fromtimestamp(int(time.time()) + 7200)
            with self._mt5_lock:
                deals = mt5.history_deals_get(query_from, query_to)
            if deals is None:
                return cached_entry[1] if cached_entry is not None else (0.0, 0, None)
            realized = 0.0
            consec_losses = 0
            last_loss_deal = None
            for d in deals:
                if symbol and d.symbol and d.symbol != symbol:
                    continue
                # Quy đổi thời gian thực hiện deal d.time sang Giờ Việt Nam (GMT+7)
                d_vn_dt = datetime.fromtimestamp(int(d.time), tz=VN_TZ)
                if d_vn_dt.strftime("%Y-%m-%d") != vn_today_str:
                    continue
                if d.type in (mt5.DEAL_TYPE_BUY, mt5.DEAL_TYPE_SELL) and d.entry in (
                    mt5.DEAL_ENTRY_OUT,
                    mt5.DEAL_ENTRY_INOUT,
                    getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
                ):
                    net_pnl = float(d.profit + d.swap + d.commission + getattr(d, "fee", 0.0))
                    realized += net_pnl
                    if net_pnl < -0.05:
                        consec_losses += 1
                        last_loss_deal = d
                    elif net_pnl > 0.05:
                        consec_losses = 0
                        last_loss_deal = None
            res_stats = (realized, consec_losses, last_loss_deal)
            self._cached_deals_by_sym[sym_key] = (now_mono, res_stats)
            self._cached_deals_stats = res_stats
            self._last_deals_check_ts = now_mono
            return res_stats
        except Exception as e:
            self.log_error("GET_TODAY_DEALS_STATS", e, show_in_ui=False)
            return cached_entry[1] if cached_entry is not None else (0.0, 0, None)

    def _get_today_closed_deals(self, symbol: str = None) -> list:
        """Lấy danh sách các lệnh đã đóng hôm nay (theo giờ VN GMT+7) kèm giờ vào/ra, giá vào/ra, lãi/lỗ và trạng thái."""
        if not MT5_AVAILABLE:
            return []
        sym_key = symbol or "__ALL__"
        now_mono = time.monotonic()
        if not hasattr(self, "_cached_closed_deals_map"):
            self._cached_closed_deals_map = {}
        cached = self._cached_closed_deals_map.get(sym_key)
        if cached is not None and (now_mono - cached[0]) < 2.0:
            return cached[1]

        try:
            vn_now = datetime.now(tz=VN_TZ)
            vn_today_str = vn_now.strftime("%Y-%m-%d")
            vn_today_start = vn_now.replace(hour=0, minute=0, second=0, microsecond=0)
            vn_today_start_ts = int(vn_today_start.timestamp())

            query_from = datetime.fromtimestamp(vn_today_start_ts - 7200)
            query_to = datetime.fromtimestamp(int(time.time()) + 7200)

            with self._mt5_lock:
                deals = mt5.history_deals_get(query_from, query_to)

            if not deals:
                self._cached_closed_deals_map[sym_key] = (now_mono, [])
                return []

            entry_by_pos = {}
            for d in deals:
                if d.entry == mt5.DEAL_ENTRY_IN:
                    entry_by_pos[d.position_id] = d

            closed_items = []
            closed_deals = [
                d for d in deals
                if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_INOUT, getattr(mt5, "DEAL_ENTRY_OUT_BY", 3))
            ]
            closed_deals.sort(key=lambda x: x.time, reverse=True)

            for d in closed_deals:
                if symbol and d.symbol and d.symbol != symbol:
                    continue
                d_vn_dt = datetime.fromtimestamp(int(d.time), tz=VN_TZ)
                if d_vn_dt.strftime("%Y-%m-%d") != vn_today_str:
                    continue

                ed = entry_by_pos.get(d.position_id)
                orig_type = "BUY" if (ed and ed.type == mt5.DEAL_TYPE_BUY) else ("SELL" if d.type == mt5.DEAL_TYPE_BUY else "BUY")
                open_p = ed.price if ed else 0.0
                close_p = d.price
                open_t = datetime.fromtimestamp(ed.time, tz=VN_TZ).strftime("%H:%M:%S") if ed else "---"
                close_t = d_vn_dt.strftime("%H:%M:%S")
                dur_sec = max(0, int(d.time) - int(ed.time)) if ed else -1
                dur_str = self._format_duration(dur_sec) if ed else "---"
                net_pnl = float(d.profit + d.swap + d.commission + getattr(d, "fee", 0.0))
                cmt = str(d.comment or "").lower()
                is_sl_loss = ("[sl" in cmt or "sl" in cmt) and (net_pnl < -0.05)

                if "[tp" in cmt:
                    status = f"ĐÃ CHỐT TP ({close_t})"
                elif "[sl" in cmt and net_pnl > 0.05:
                    status = f"DỜI SL CHỐT LÃI ({close_t})"
                elif "[sl" in cmt and net_pnl < -0.05:
                    status = f"ĐÃ CẮT LỖ SL ({close_t})"
                elif abs(net_pnl) <= 0.05:
                    status = f"HÒA VỐN ({close_t})"
                elif net_pnl > 0.05:
                    status = f"ĐÃ CHỐT LÃI ({close_t})"
                else:
                    status = f"ĐÃ ĐÓNG LỖ ({close_t})"

                closed_items.append({
                    "ticket": d.position_id,
                    "open_time": open_t,
                    "close_time": close_t,
                    "close_ts": int(d.time),
                    "time_display": f"{open_t} → {close_t}",
                    "duration": dur_str,
                    "duration_sec": dur_sec,
                    "is_sl_loss": is_sl_loss,
                    "symbol": d.symbol,
                    "type": orig_type,
                    "volume": d.volume,
                    "open_price": open_p,
                    "close_price": close_p,
                    "net_pnl": net_pnl,
                    "status": status,
                })

            self._cached_closed_deals_map[sym_key] = (now_mono, closed_items)
            return closed_items
        except Exception as e:
            self.log_error("GET_CLOSED_DEALS", e, show_in_ui=False)
            return []

    def _check_daily_profit_loss_limits(self, floating_profit: float, tick=None, profile_key: str = None):
        """Kiểm tra mục tiêu Lãi ngày / Cắt lỗ ngày (USD) & Tạm ngừng sau chuỗi thua liên tiếp cho XAUUSD theo Giờ Việt Nam (GMT+7)."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]
        today_str = datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")
        symbol = str(cfg.get("symbol", "")).strip()
        realized_pnl, consec_losses, last_loss_deal = self._get_today_deals_stats(symbol=symbol, tick=tick)
        total_daily_pnl = realized_pnl + floating_profit
        ctx["cached_daily_pnl"] = total_daily_pnl
        ctx["consec_loss_count"] = consec_losses

        try:
            daily_tp = max(float(cfg.get("daily_tp_usd", 0.0)), 0.0)
        except ValueError:
            daily_tp = 0.0
        try:
            daily_sl = max(abs(float(cfg.get("daily_sl_usd", 0.0))), 0.0)
        except ValueError:
            daily_sl = 0.0
        try:
            max_consec_loss = max(int(float(cfg.get("max_consec_loss", 0))), 0)
        except ValueError:
            max_consec_loss = 0
        try:
            cooldown_mins = max(float(cfg.get("cooldown_mins", 30.0)), 0.0)
        except ValueError:
            cooldown_mins = 0.0
        try:
            fast_sl_cd_mins = max(float(cfg.get("fast_sl_cooldown_mins", 15.0)), 0.0)
        except ValueError:
            fast_sl_cd_mins = 0.0

        is_auto_active = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))

        # 1. Xử lý Tạm ngừng sau chuỗi lệnh thua liên tiếp (= 0 là tắt)
        if max_consec_loss == 0 or cooldown_mins == 0.0:
            if ctx["cooldown_until"] is not None:
                ctx["cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.cooldown_until = None
                self.log(f"[{pk}] Đã tắt bộ đếm tạm ngừng sau chuỗi thua (thông số = 0).")
        else:
            if ctx["cooldown_until"] is not None and datetime.now() >= ctx["cooldown_until"]:
                ctx["cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.cooldown_until = None
                self.log(f"[{pk}] Hết thời gian tạm nghỉ chuỗi thua -> Tiếp tục giao dịch tự động.")
                self.show_toast(f"[{pk}] Hết tạm nghỉ chuỗi thua -> Tự động tiếp tục!", is_error=False, duration_ms=2500)
                if is_auto_active and not ctx.get("is_analyzing", False):
                    self.enqueue_ai_scan(profile_key=pk, reason="COOLDOWN_EXPIRED")

            if consec_losses >= max_consec_loss and last_loss_deal is not None:
                if ctx["last_cooldown_deal_ticket"] != last_loss_deal.ticket:
                    now_ts = int(time.time())
                    elapsed_sec = max(0, now_ts - int(last_loss_deal.time))
                    total_cd_sec = cooldown_mins * 60.0
                    ctx["last_cooldown_deal_ticket"] = last_loss_deal.ticket
                    if elapsed_sec < total_cd_sec:
                        rem_sec = total_cd_sec - elapsed_sec
                        ctx["cooldown_until"] = datetime.now() + timedelta(seconds=rem_sec)
                        ctx["pending_pullback_order"] = None
                        self.log(
                            f"[{pk} - Tạm nghỉ chuỗi thua] Thua liên tiếp {consec_losses} lệnh -> "
                            f"Tạm nghỉ {cooldown_mins:.0f}p (đến {ctx['cooldown_until'].strftime('%H:%M:%S')})."
                        )

        # 1.5. Xử lý Tạm ngừng khi có lệnh dính SL < 5 phút (= 0 là tắt)
        if fast_sl_cd_mins == 0.0:
            if ctx.get("fast_sl_cooldown_until") is not None:
                ctx["fast_sl_cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.fast_sl_cooldown_until = None
                self.log(f"[{pk}] Đã tắt tạm nghỉ do SL < 5 phút (thông số = 0).")
        else:
            if ctx.get("fast_sl_cooldown_until") is not None and datetime.now() >= ctx["fast_sl_cooldown_until"]:
                ctx["fast_sl_cooldown_until"] = None
                if pk == self.active_profile_key:
                    self.fast_sl_cooldown_until = None
                self.log(f"[{pk}] Hết thời gian tạm nghỉ SL < 5p -> Tiếp tục giao dịch tự động.")
                self.show_toast(f"[{pk}] Hết tạm nghỉ SL < 5p -> Tự động tiếp tục!", is_error=False, duration_ms=2500)
                if is_auto_active and not ctx.get("is_analyzing", False):
                    self.enqueue_ai_scan(profile_key=pk, reason="COOLDOWN_EXPIRED")

            closed_deals = self._get_today_closed_deals(symbol=symbol)
            for cd in closed_deals:
                if cd.get("is_sl_loss") and (0 <= cd.get("duration_sec", -1) < 300):
                    last_ticket = cd.get("ticket")
                    if ctx.get("last_fast_sl_deal_ticket") != last_ticket:
                        now_ts = int(time.time())
                        close_ts = cd.get("close_ts", now_ts)
                        elapsed_sec = max(0, now_ts - close_ts)
                        total_cd_sec = fast_sl_cd_mins * 60.0
                        ctx["last_fast_sl_deal_ticket"] = last_ticket
                        if elapsed_sec < total_cd_sec:
                            rem_sec = total_cd_sec - elapsed_sec
                            ctx["fast_sl_cooldown_until"] = datetime.now() + timedelta(seconds=rem_sec)
                            ctx["pending_pullback_order"] = None
                            dur_display = cd.get("duration", f"{cd.get('duration_sec', 0)}s")
                            self.log(
                                f"[{pk} - Tạm nghỉ SL < 5p] Lệnh #{last_ticket} dính SL sau {dur_display} (Lỗ: {cd.get('net_pnl', 0.0):+.2f}$) -> "
                                f"Tạm nghỉ {fast_sl_cd_mins:.0f}p (đến {ctx['fast_sl_cooldown_until'].strftime('%H:%M:%S')})."
                            )
                            self.show_toast(
                                f"[{pk}] Lệnh #{last_ticket} SL sau {dur_display} (< 5p) -> Tạm nghỉ {fast_sl_cd_mins:.0f}p (Tự động mở lại)",
                                is_error=True,
                                duration_ms=3000
                            )
                    break

        # 1.7. Xử lý Tạm nghỉ sau khi chốt lời chùm lệnh (Basket Cooldown)
        if ctx.get("basket_cooldown_until") is not None and datetime.now() >= ctx["basket_cooldown_until"]:
            ctx["basket_cooldown_until"] = None
            if pk == self.active_profile_key:
                self.basket_cooldown_until = None
            self.log(f"[{pk}] Hết thời gian nghỉ sau chốt chùm -> Tiếp tục giao dịch tự động.")
            self.show_toast(f"[{pk}] Hết tạm nghỉ chốt chùm -> Tự động tiếp tục!", is_error=False, duration_ms=2500)
            if is_auto_active and not ctx.get("is_analyzing", False):
                self.enqueue_ai_scan(profile_key=pk, reason="COOLDOWN_EXPIRED")

        # 2. Sang ngày mới theo giờ Việt Nam -> Tự động mở khóa giao dịch
        if ctx["daily_limit_locked_date"] is not None and ctx["daily_limit_locked_date"] != today_str:
            ctx["daily_limit_locked_date"] = None
            ctx["daily_limit_reason"] = ""
            if pk == self.active_profile_key:
                self.daily_limit_locked_date = None
                self.daily_limit_reason = ""
            self.log(f"[{pk}] Sang ngày giao dịch mới (00:00 GMT+7) -> Mở lại giao dịch tự động.")
            if is_auto_active and not ctx.get("is_analyzing", False):
                self.enqueue_ai_scan(profile_key=pk, reason="NEW_DAY_UNLOCKED")

        # Nếu người dùng chỉnh về 0 (chạy tẹt ga) hoặc nâng hạn mức cao hơn hiện tại -> Mở khóa ngay
        if ctx["daily_limit_locked_date"] == today_str:
            tp_still_hit = (daily_tp > 0.0 and total_daily_pnl >= daily_tp)
            sl_still_hit = (daily_sl > 0.0 and total_daily_pnl <= -daily_sl)
            if not tp_still_hit and not sl_still_hit:
                ctx["daily_limit_locked_date"] = None
                ctx["daily_limit_reason"] = ""
                if pk == self.active_profile_key:
                    self.daily_limit_locked_date = None
                    self.daily_limit_reason = ""
                self.log(f"🔓 [{pk}] Đã mở khóa giao dịch trong ngày (theo thông số Lãi/Lỗ ngày mới cài đặt).")
                if is_auto_active and not ctx.get("is_analyzing", False):
                    self.enqueue_ai_scan(profile_key=pk, reason="DAILY_LIMIT_UNLOCKED")

        # Kiểm tra chạm mức Chốt lời ngày (nếu > 0)
        if daily_tp > 0.0 and total_daily_pnl >= daily_tp and ctx["daily_limit_locked_date"] != today_str:
            ctx["daily_limit_locked_date"] = today_str
            ctx["daily_limit_reason"] = f"Đạt Lãi ngày ({total_daily_pnl:+.2f}$ >= +{daily_tp:.1f}$)"
            ctx["pending_pullback_order"] = None
            with self._mt5_lock:
                positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
            if positions:
                for pos in positions:
                    self._close_single_pos(pos)
            self.log(f"🏆 [{pk} - QUẢN LÝ VỐN NGÀY] {ctx['daily_limit_reason']} -> Đã chốt toàn bộ lệnh {symbol} & DỪNG GIAO DỊCH hết hôm nay!")

        # Kiểm tra chạm mức Cắt lỗ ngày (nếu > 0)
        elif daily_sl > 0.0 and total_daily_pnl <= -daily_sl and ctx["daily_limit_locked_date"] != today_str:
            ctx["daily_limit_locked_date"] = today_str
            ctx["daily_limit_reason"] = f"Chạm Cắt lỗ ngày ({total_daily_pnl:+.2f}$ <= -{daily_sl:.1f}$)"
            ctx["pending_pullback_order"] = None
            with self._mt5_lock:
                positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
            if positions:
                for pos in positions:
                    self._close_single_pos(pos)
            self.log(f"[{pk} - Quản lý vốn ngày] {ctx['daily_limit_reason']} -> Đã đóng toàn bộ lệnh {symbol} & dừng giao dịch hôm nay.")

        if pk == self.active_profile_key:
            self.cached_daily_pnl = ctx["cached_daily_pnl"]
            self.consec_loss_count = ctx["consec_loss_count"]
            self.cooldown_until = ctx["cooldown_until"]
            self.last_cooldown_deal_ticket = ctx["last_cooldown_deal_ticket"]
            self.fast_sl_cooldown_until = ctx.get("fast_sl_cooldown_until")
            self.last_fast_sl_deal_ticket = ctx.get("last_fast_sl_deal_ticket")
            self.daily_limit_locked_date = ctx["daily_limit_locked_date"]
            self.daily_limit_reason = ctx["daily_limit_reason"]
            self.pending_pullback_order = ctx["pending_pullback_order"]

    def _is_daily_limit_locked(self, profile_key: str = None) -> bool:
        pk = profile_key or self.active_profile_key
        locked_d = self.daily_limit_locked_date if pk == self.active_profile_key else self.profile_ctx[pk].get("daily_limit_locked_date")
        return locked_d == datetime.now(tz=VN_TZ).strftime("%Y-%m-%d")

    def _is_cooldown_active(self, profile_key: str = None) -> bool:
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx[pk]
        cd = self.cooldown_until if pk == self.active_profile_key else ctx.get("cooldown_until")
        bcd = ctx.get("basket_cooldown_until")
        fscd = self.fast_sl_cooldown_until if pk == self.active_profile_key else ctx.get("fast_sl_cooldown_until")
        now = datetime.now()
        is_loss_cd = (cd is not None and now < cd)
        is_basket_cd = (bcd is not None and now < bcd)
        is_fast_sl_cd = (fscd is not None and now < fscd)
        return is_loss_cd or is_basket_cd or is_fast_sl_cd

    def _get_cooldown_remaining_str(self, profile_key: str = None) -> str:
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx[pk]
        now = datetime.now()
        cd = self.cooldown_until if pk == self.active_profile_key else ctx.get("cooldown_until")
        bcd = ctx.get("basket_cooldown_until")
        fscd = self.fast_sl_cooldown_until if pk == self.active_profile_key else ctx.get("fast_sl_cooldown_until")
        parts = []
        if cd is not None and now < cd:
            losses = self.consec_loss_count if pk == self.active_profile_key else ctx.get("consec_loss_count", 0)
            rem = max(0, int((cd - now).total_seconds()))
            m, s = divmod(rem, 60)
            parts.append(f"Tạm ngưng giao dịch do chuỗi thua {losses} lệnh [{m:02d}p:{s:02d}s]")
        if bcd is not None and now < bcd:
            rem = max(0, int((bcd - now).total_seconds()))
            m, s = divmod(rem, 60)
            parts.append(f"Tạm ngưng giao dịch do sau chốt chùm [{m:02d}p:{s:02d}s]")
        if fscd is not None and now < fscd:
            rem = max(0, int((fscd - now).total_seconds()))
            m, s = divmod(rem, 60)
            parts.append(f"Tạm ngưng giao dịch do lệnh SL < 5 phút [{m:02d}p:{s:02d}s]")
        return " | ".join(parts)

    def _get_profile_magic_number(self, profile_key: str = None) -> int:
        """Lấy Magic Number do người dùng thiết lập cho XAUUSD (mặc định 20260601 nếu là instance BOT)."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        default_magic = 20260601 if INSTANCE_ID else 20260501
        raw_val = str(cfg.get("magic_number", default_magic)).strip()
        try:
            m_val = int(float(raw_val))
            return m_val if m_val > 0 else default_magic
        except Exception:
            return default_magic

    def _get_supported_filling_modes(self, sym_info) -> list:
        """Trả về danh sách các kiểu khớp lệnh (Filling Mode) theo thứ tự ưu tiên mà mã giao dịch trên MT5 hỗ trợ."""
        modes = []
        if sym_info is not None:
            fm = getattr(sym_info, "filling_mode", 0) or 0
            # Bitmask chuẩn của MT5: 1 = FOK, 2 = IOC
            if fm & 2:
                modes.append(mt5.ORDER_FILLING_IOC)
            if fm & 1:
                modes.append(mt5.ORDER_FILLING_FOK)
        for fallback_m in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK, getattr(mt5, "ORDER_FILLING_RETURN", 2)):
            if fallback_m not in modes:
                modes.append(fallback_m)
        return modes

    def _send_mt5_order_with_retry(self, request: dict, symbol: str, context_tag: str = "ORDER", max_retries: int = 3) -> tuple:
        """
        Gửi lệnh giao dịch MT5 (Mở lệnh / Đóng lệnh / Sửa SL-TP) an toàn tuyệt đối khi Scalping 2 mã cùng lúc:
        1. Khóa tuần tự qua self._mt5_lock + khoảng đệm 55ms giữa 2 lệnh để không bao giờ bị lỗi Trade Context Busy (10007).
        2. Tự động thử lại tối đa 3 lần (kèm cập nhật giá Bid/Ask mới nhất sau 85ms) nếu sàn báo Requote / Price Changed / Busy.
        3. Ghi nhật ký chi tiết vào file log khi có bất kỳ từ chối nào từ MT5.
        """
        if not MT5_AVAILABLE:
            return False, None, "Chưa cài đặt thư viện MetaTrader5"

        retryable_codes = {
            getattr(mt5, "TRADE_RETCODE_REQUOTE", 10004),
            getattr(mt5, "TRADE_RETCODE_BUSY", 10007),
            getattr(mt5, "TRADE_RETCODE_TIMEOUT", 10012),
            getattr(mt5, "TRADE_RETCODE_PRICE_CHANGED", 10020),
            getattr(mt5, "TRADE_RETCODE_PRICE_OFF", 10021),
            getattr(mt5, "TRADE_RETCODE_CONNECTION", 10031),
        }
        invalid_fill_code = getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030)
        done_codes = {
            getattr(mt5, "TRADE_RETCODE_DONE", 10009),
            getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010),
            getattr(mt5, "TRADE_RETCODE_PLACED", 10008),
        }

        with self._mt5_lock:
            # Khoảng đệm an toàn 55ms giữa 2 lệnh liên tiếp bắn vào MT5
            elapsed = time.monotonic() - self._last_mt5_order_mono
            if 0.0 <= elapsed < 0.055:
                time.sleep(0.055 - elapsed)

            sym_info = mt5.symbol_info(symbol) if symbol else None
            digits = sym_info.digits if sym_info is not None else 2
            is_deal = (request.get("action") == getattr(mt5, "TRADE_ACTION_DEAL", 1))
            filling_modes = self._get_supported_filling_modes(sym_info) if is_deal else [request.get("type_filling", 0)]

            last_res = None
            for attempt in range(1, max_retries + 1):
                if attempt > 1:
                    time.sleep(0.085)
                    if symbol and is_deal:
                        mt5.symbol_select(symbol, True)
                        fresh_tick = mt5.symbol_info_tick(symbol)
                        if fresh_tick is not None and fresh_tick.ask > 0 and fresh_tick.bid > 0:
                            is_buy_ord = (request.get("type") == getattr(mt5, "ORDER_TYPE_BUY", 0))
                            request["price"] = round(fresh_tick.ask if is_buy_ord else fresh_tick.bid, digits)

                for fill_m in filling_modes:
                    req_try = dict(request)
                    if is_deal:
                        req_try["type_filling"] = fill_m
                    res = mt5.order_send(req_try)
                    self._last_mt5_order_mono = time.monotonic()
                    last_res = res

                    if res is not None and res.retcode in done_codes:
                        if attempt > 1:
                            self._write_file_log(
                                f"[MT5_RETRY_OK - {context_tag}] Thành công ở lần thử {attempt}/{max_retries} ({symbol}, ticket={getattr(res, 'order', 0)})"
                            )
                        return True, res, ""

                    if res is not None and res.retcode == invalid_fill_code:
                        continue
                    break

                rc = getattr(last_res, "retcode", None)
                cmt = getattr(last_res, "comment", "")
                if attempt < max_retries and (last_res is None or rc in retryable_codes):
                    self._write_file_log(
                        f"[MT5_RETRY {attempt}/{max_retries} - {context_tag}] Sàn bận/nhảy giá ({symbol}, code={rc}, comment={cmt}) -> Lấy giá mới thử lại...",
                        is_error=True
                    )
                    continue
                break

            code_str = str(getattr(last_res, "retcode", "None"))
            comment_str = str(getattr(last_res, "comment", ""))
            self.log_error(
                context_tag,
                extra_info=f"symbol={symbol} | retcode={code_str} | comment={comment_str} | request={request}",
                show_in_ui=False
            )
            return False, last_res, f"Mã lỗi: {code_str} - {comment_str}"

    def _check_pending_pullback_order(self, symbol: str, tick, profile_key: str = None):
        """Kiểm tra mỗi 1 giây: Nếu đang có tín hiệu chờ giá hồi về <= SL tối đa thì khớp lệnh ngay khi đạt (Có khóa chống đúp lệnh)."""
        pk = profile_key or self.active_profile_key
        if self._order_in_flight.get(pk, False):
            return
        ctx = self.profile_ctx[pk]
        p = self.pending_pullback_order if pk == self.active_profile_key else ctx.get("pending_pullback_order")
        if p is None:
            return
        if self._is_daily_limit_locked(pk) or self._is_cooldown_active(pk):
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None
            return

        if p.get("symbol") != symbol:
            return

        cfg = self._get_profile_cfg(pk)
        try:
            max_sl_price = max(float(cfg.get("max_sl_price", 5.0)), 0.0)
            max_spread = float(cfg.get("max_spread", 0.35))
            lot = float(cfg.get("lot", 0.01))
            max_pos = int(cfg.get("max_pos", 3))
            rr = max(float(cfg.get("rr", 2.0)), 0.0)
        except ValueError:
            max_sl_price, max_spread, lot, max_pos, rr = 5.0, 0.35, 0.01, 3, 2.0

        action = p["action"]
        sl_price = float(p["sl_price"])
        cur_price = tick.ask if action == "BUY" else tick.bid
        spread_usd = tick.ask - tick.bid
        tols = self._get_symbol_tolerances(symbol, tick)

        cur_sl_dist = (cur_price - sl_price) if action == "BUY" else (sl_price - cur_price)

        # 1. Nếu giá chạy ngược xuyên qua luôn mức SL của AI -> Hủy lệnh chờ hồi vì kịch bản đã gãy
        if cur_sl_dist <= tols["sl_break_tol"]:
            self.log(f"❌ [{pk} - Hủy Lệnh Chờ Hồi {action}] Giá ({cur_price:.2f}) đã chạm/phá qua mức SL AI ({sl_price:.2f}).")
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None
            return

        # 2. Nếu giá đã hồi về vùng cho phép: Khoảng cách SL hiện tại <= SL tối đa (hoặc max_sl_cap == 0 là theo AI)
        max_sl_cap = p.get("effective_max_sl", max_sl_price)
        if max_sl_cap <= 0.0 or cur_sl_dist <= max_sl_cap + tols["max_sl_tol"]:
            is_auto_on = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))
            should_execute = is_auto_on or p.get("manual_queued", False)
            if not should_execute:
                return

            # Kiểm tra xem có lệnh ngược chiều đang âm chờ thoát hòa vốn không
            with self._mt5_lock:
                open_positions = mt5.positions_get(symbol=symbol)
            if open_positions:
                for pos in open_positions:
                    is_buy_pos = (pos.type == mt5.POSITION_TYPE_BUY)
                    if ((action == "BUY" and not is_buy_pos) or (action == "SELL" and is_buy_pos)) and pos.profit <= 0:
                        return

            if spread_usd > max_spread:
                return

            cached_tp_val = p.get("target_tp_price") or (self.cached_tp if pk == self.active_profile_key else ctx.get("cached_tp"))
            min_rr = 2.0 if p.get("sl_tightened_for_2r") else (rr if rr > 0.0 else 2.0)
            target_tp_dist = round(cur_sl_dist * max(rr, min_rr), 2)
            if cached_tp_val is not None:
                cached_dist = abs(float(cached_tp_val) - cur_price)
                if cached_dist >= target_tp_dist:
                    tp_price = float(cached_tp_val)
                else:
                    tp_price = round(cur_price + target_tp_dist, 2) if action == "BUY" else round(cur_price - target_tp_dist, 2)
            else:
                tp_price = round(cur_price + target_tp_dist, 2) if action == "BUY" else round(cur_price - target_tp_dist, 2)
            ctx["cached_tp"] = tp_price
            if pk == self.active_profile_key:
                self.cached_tp = tp_price

            # Thu hồi nguyên tử (Atomic Pop) pending_pullback_order TRƯỚC khi gửi lệnh để tuyệt đối không vào đúp 2 lệnh
            ctx["pending_pullback_order"] = None
            if pk == self.active_profile_key:
                self.pending_pullback_order = None

            ok_ord, ord_msg = self.execute_mt5_order(symbol, action, lot, sl_price, tp_price, max_pos, profile_key=pk)
            if ok_ord and ctx.get("last_candle_time") is not None:
                self._last_order_candle_ts[pk] = ctx.get("last_candle_time")
            if not ok_ord and "SL hiện tại cách" in ord_msg:
                # Nếu trong tích tắc gửi lệnh giá giật nhẹ ra ngoài vùng SL tối đa (nhưng chưa phá SL), khôi phục trạng thái canh hồi
                ctx["pending_pullback_order"] = p
                if pk == self.active_profile_key:
                    self.pending_pullback_order = p
                return
            self.log(f"[{pk} - Khớp Lệnh Chờ Hồi SL <= {max_sl_cap:.2f}$ (RR >= 2R)] Giá đã hồi về {cur_price:.2f} (Cách SL {cur_sl_dist:.2f}$) -> {ord_msg}")

    def _update_left_quick_info(self, symbol, tf_str, tick, pos_count, total_profit):
        bid_str = f"{tick.bid:.2f}" if tick else "---"
        ask_str = f"{tick.ask:.2f}" if tick else "---"
        spr_str = f"{(tick.ask - tick.bid):.2f}$" if tick else "---"

        if self.cached_mean_df is not None:
            end_pred = float(self.cached_mean_df["close"].iloc[-1])
            hi_pred = float(self.cached_mean_df["high"].max())
            lo_pred = float(self.cached_mean_df["low"].min())
            if self.pending_pullback_order is not None:
                p = self.pending_pullback_order
                try:
                    max_sl_p = float(p.get("effective_max_sl") or self.max_sl_price_var.get())
                except (ValueError, TypeError):
                    max_sl_p = 5.0
                lim_entry = (p["sl_price"] + max_sl_p) if p["action"] == "BUY" else (p["sl_price"] - max_sl_p)
                rr_tag = " (RR>=2R)" if p.get("sl_tightened_for_2r") else ""
                ai_state = f"Chờ hồi {p['action']}{rr_tag} @ {lim_entry:.2f}"
            else:
                ai_state = "Đã có dự báo AI"
            trend_str = self.cached_action if self.cached_action else "ĐỨNG NGOÀI (WAIT)"
            target_str = f"{end_pred:.2f} (Đỉnh:{hi_pred:.2f} / Đáy:{lo_pred:.2f})"
            sltp_str = f"SL: {self.cached_sl:.2f} | TP: {self.cached_tp:.2f}" if self.cached_sl else "---"
        else:
            ai_state = "Đang phân tích AI..." if self.is_analyzing else "Chưa quét"
            trend_str = "---"
            target_str = "---"
            sltp_str = "---"

        ana_tf = self.analysis_tf_var.get().strip() or tf_str
        tf_display = f"{tf_str}" if ana_tf == tf_str else f"AI:{ana_tf} -> Scalp:{tf_str}"
        bot_run_state = f"● ĐANG CHẠY (Canh {tf_str})" if self.auto_loop_running else "● ĐANG DỪNG (Tắt Auto)"
        if self._is_daily_limit_locked():
            cap_state = f"{self.daily_limit_reason}"
        elif self._is_cooldown_active():
            cap_state = self._get_cooldown_remaining_str()
        else:
            cap_state = f"Bình thường (Thua liên tiếp: {self.consec_loss_count})"

        pred_n = self.pred_len_var.get().strip() or "6"
        magic_num = self._get_profile_magic_number(self.active_profile_key)
        wr_display = f"{self.cached_win_rate:.1f}% ({self.cached_win_count}W - {self.cached_loss_count}L)" if self.cached_closed_count > 0 else "---"
        lic_txt = "---"
        if hasattr(self, "lic_mgr") and self.lic_mgr is not None:
            st = self.lic_mgr.check_status()
            lic_txt = st.get("badge_text", "").replace("[", "").replace("]", "").strip()
            self._update_license_badge_ui()

        info_txt = (
            f"• Tab đang chọn: {self.active_profile_key} ({symbol} - {tf_display})\n"
            f"• Magic Number : {magic_num}\n"
            f"• Bid / Ask    : {bid_str} / {ask_str} ({spr_str})\n"
            f"• Lệnh đang mở : {pos_count} lệnh ({total_profit:+.2f}$)\n"
            f"• Lãi/Lỗ Ngày (VN): {self.cached_daily_pnl:+.2f} USD\n"
            f"• Win Rate Ngày: {wr_display}\n"
            f"• Bản quyền     : {lic_txt}\n"
            f"• Trạng thái Bot: {bot_run_state}\n"
            f"• Quản lý Vốn  : {cap_state}\n"
            f"────────────────────────────\n"
            f"• Trạng thái AI: {ai_state}\n"
            f"• Tín hiệu AI  : {trend_str}\n"
            f"• Dự báo {pred_n} nến : {target_str}\n"
            f"• Gợi ý AI     : {sltp_str}"
        )
        self.quick_info_lbl.config(text=info_txt)

    def _calc_target_pnl(self, pos, target_price: float) -> float:
        """Tính số tiền Lãi/Lỗ (USD) khi giá chạm SL hoặc TP."""
        if target_price <= 0:
            return 0.0
        try:
            with self._mt5_lock:
                pnl = mt5.order_calc_profit(pos.type, pos.symbol, pos.volume, pos.price_open, target_price)
            if pnl is not None:
                return float(pnl)
        except Exception:
            pass
        # Dự phòng tính theo contract size của mã giao dịch
        with self._mt5_lock:
            sym_info = mt5.symbol_info(pos.symbol) if MT5_AVAILABLE else None
        contract_sz = float(sym_info.trade_contract_size) if (sym_info and sym_info.trade_contract_size > 0) else 100.0
        diff = (target_price - pos.price_open) if pos.type == mt5.POSITION_TYPE_BUY else (pos.price_open - target_price)
        return float(diff * pos.volume * contract_sz)

    def _get_pos_one_r_dist(self, pos, profile_key: str = None) -> float:
        """Tính khoảng cách 1R (|Entry - SL ban đầu|) của lệnh."""
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]
        is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
        # Nếu SL đang nằm ở vùng cắt lỗ ban đầu thì ghi nhận chính xác 1R = |Entry - SL|
        if is_buy and 0 < pos.sl < pos.price_open - 0.1:
            self.initial_r_cache[pos.ticket] = abs(pos.price_open - pos.sl)
        elif (not is_buy) and pos.sl > pos.price_open + 0.1:
            self.initial_r_cache[pos.ticket] = abs(pos.sl - pos.price_open)

        if pos.ticket in self.initial_r_cache:
            return self.initial_r_cache[pos.ticket]

        # Nếu cài R:R = 0 (TP Theo AI), ưu tiên lấy khoảng cách SL gần nhất do AI tính toán
        try:
            raw_rr = float(cfg.get("rr", 2.0))
        except ValueError:
            raw_rr = 2.0

        last_rec = self.last_recommendation if pk == self.active_profile_key else ctx.get("last_recommendation")
        if raw_rr <= 0.0 and last_rec and last_rec.get("sl_dist", 0) > 0.5:
            one_r = float(last_rec["sl_dist"])
            self.initial_r_cache[pos.ticket] = one_r
            return one_r

        # Nếu lệnh đã dời SL về Entry trước khi mở App, suy ra 1R từ khoảng cách TP / RR
        if pos.tp > 0:
            rr_val = raw_rr if raw_rr > 0.0 else 1.8
            one_r = abs(pos.tp - pos.price_open) / rr_val
            if one_r > 0.2:
                self.initial_r_cache[pos.ticket] = one_r
                return one_r
        return 5.0

    def _update_open_positions_and_manage(self, symbol: str, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]
        tree_widget = self.pos_tree_by_profile.get(pk, self.pos_tree)
        summary_widget = self.pos_summary_lbl_by_profile.get(pk, self.pos_summary_lbl)

        with self._mt5_lock:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if positions is None:
            positions = []

        selected_tickets = [tree_widget.item(item, "values")[0] for item in tree_widget.selection()]
        for row in tree_widget.get_children():
            tree_widget.delete(row)

        total_profit = 0.0
        auto_be = bool(cfg.get("auto_be", True))
        auto_trail = bool(cfg.get("auto_trail", True))
        default_be_plus = 0.2
        try:
            be_trigger_r = float(cfg.get("be_trigger_r", 1.0))
            trail_dist_r = float(cfg.get("trail_dist_r", 0.35))
        except ValueError:
            be_trigger_r, trail_dist_r = 1.0, 0.35
        try:
            be_plus_price = max(float(cfg.get("be_plus_price", default_be_plus)), 0.0)
        except ValueError:
            be_plus_price = default_be_plus

        try:
            max_hold_mins = max(float(cfg.get("max_hold_mins", 0.0)), 0.0)
        except ValueError:
            max_hold_mins = 0.0
        max_hold_action = str(cfg.get("max_hold_action", "Chốt lời (nếu lãi) & Dời TP về hòa (nếu lỗ)")).strip()

        tols = self._get_symbol_tolerances(symbol)
        eps_eq = tols["eps_equal"]
        min_stop_d = tols["min_stop_dist"]
        min_trail_step = tols["min_trail_step"]

        for pos in positions:
            total_profit += pos.profit
            is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
            type_str = "BUY" if is_buy else "SELL"
            open_time = datetime.fromtimestamp(pos.time).strftime("%H:%M:%S")
            elapsed_sec = max(0, int(time.time()) - int(pos.time))
            dur_str = self._format_duration(elapsed_sec)
            price_move = (pos.price_current - pos.price_open) if is_buy else (pos.price_open - pos.price_current)

            one_r = self._get_pos_one_r_dist(pos, profile_key=pk)
            current_r = price_move / max(one_r, 0.1)
            trail_dist_price = one_r * trail_dist_r

            status_str = f"Đang chạy ({current_r:+.2f}R)"
            with self._mt5_lock:
                sym_info = mt5.symbol_info(pos.symbol)
            digits = sym_info.digits if sym_info else 2
            entry_exact = round(pos.price_open, digits)
            be_target_price = round((pos.price_open + be_plus_price) if is_buy else (pos.price_open - be_plus_price), digits)
            be_label_tag = f"Entry+{be_plus_price:g}" if be_plus_price > 0 else "Entry"

            # Quy tắc Giữ lệnh tối đa (Time-Stop): Nếu lệnh đã mở vượt quá số phút cài đặt
            if max_hold_mins > 0.0 and (elapsed_sec / 60.0) >= max_hold_mins:
                if "Chốt lời" in max_hold_action:
                    if pos.profit > 0.0:
                        ok_h, msg_h = self._close_single_pos(pos)
                        if ok_h:
                            self.log(f"⌛ [{pk} - Giữ lệnh tối đa {max_hold_mins:g}p] Lệnh #{pos.ticket} đã giữ {dur_str} >= {max_hold_mins:g}p & có lãi ({pos.profit:+.2f}$) -> {msg_h}")
                            continue
                    else:
                        if pos.ticket not in self.pending_be_exit_tickets:
                            self.pending_be_exit_tickets.add(pos.ticket)
                            if abs(pos.tp - be_target_price) >= eps_eq:
                                self._modify_position_sltp(pos, pos.sl, be_target_price)
                                self.log(f"⌛ [{pk} - Giữ lệnh tối đa {max_hold_mins:g}p] Lệnh #{pos.ticket} giữ {dur_str} (đang âm {pos.profit:+.2f}$) -> Đã dời TP về {be_label_tag} ({be_target_price:.2f}) để tự thoát hòa vốn!")
                elif "Dời SL" in max_hold_action:
                    if is_buy and (pos.sl == 0 or pos.sl < be_target_price - eps_eq * 0.5):
                        if be_target_price < pos.price_current - min_stop_d:
                            if self._modify_position_sltp(pos, be_target_price, pos.tp):
                                self.log(f"⌛ [{pk} - Giữ lệnh tối đa {max_hold_mins:g}p] Lệnh BUY #{pos.ticket} giữ {dur_str} -> Đã dời SL về {be_label_tag} ({be_target_price:.2f})")
                    elif (not is_buy) and (pos.sl == 0 or pos.sl > be_target_price + eps_eq * 0.5):
                        if be_target_price > pos.price_current + min_stop_d:
                            if self._modify_position_sltp(pos, be_target_price, pos.tp):
                                self.log(f"⌛ [{pk} - Giữ lệnh tối đa {max_hold_mins:g}p] Lệnh SELL #{pos.ticket} giữ {dur_str} -> Đã kéo SL về {be_label_tag} ({be_target_price:.2f})")
                elif "Đóng tất cả" in max_hold_action:
                    ok_h, msg_h = self._close_single_pos(pos)
                    if ok_h:
                        self.log(f"⌛ [{pk} - Giữ lệnh tối đa {max_hold_mins:g}p - Đóng khẩn cấp] Lệnh #{pos.ticket} giữ {dur_str} -> {msg_h}")
                        continue

            # Kiểm tra nếu lệnh đang ở chế độ "Quy tắc 3: Chờ giá hồi về Entry (+ lãi nhỏ) để thoát khi AI báo đảo chiều"
            is_waiting_be_exit = (
                pos.ticket in self.pending_be_exit_tickets
                or (pos.tp > 0 and (abs(pos.tp - be_target_price) < eps_eq or abs(pos.tp - entry_exact) < eps_eq))
            )
            if is_waiting_be_exit:
                status_str = f"Chờ về {be_label_tag} thoát ({current_r:+.2f}R)"
                # Nếu giá đã hồi về chạm/vượt mức Entry + lãi nhỏ (be_target_price) mà sàn chưa khớp TP -> Đóng ngay lập tức
                if price_move >= be_plus_price and (pos.profit >= 0.0 or be_plus_price > 0.0):
                    ok_be, msg_be = self._close_single_pos(pos)
                    if ok_be:
                        self.pending_be_exit_tickets.discard(pos.ticket)
                        self.log(f"[{pk} - Thoát {be_label_tag}] Giá hồi về {be_target_price:.2f} -> {msg_be}")
                        continue
                # Nếu TP chưa được đặt về be_target_price (do lúc trước giá quá sát), tự động đặt TP = be_target_price khi đủ khoảng cách
                elif abs(pos.tp - be_target_price) >= eps_eq and abs(pos.price_current - be_target_price) > min_stop_d:
                    if (is_buy and pos.price_current < be_target_price - min_stop_d) or ((not is_buy) and pos.price_current > be_target_price + min_stop_d):
                        if self._modify_position_sltp(pos, pos.sl, be_target_price):
                            self.log(f"[{pk} - Quy tắc 3] Đặt TP lệnh #{pos.ticket} về {be_label_tag} ({be_target_price:.2f})")

            if is_buy:
                if abs(pos.sl - be_target_price) < eps_eq or abs(pos.sl - entry_exact) < eps_eq:
                    status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"
                elif pos.sl > be_target_price:
                    status_str = f"Trailing ({current_r:+.2f}R)"

                if auto_be and current_r >= be_trigger_r and (pos.sl == 0 or pos.sl < be_target_price - eps_eq * 0.5):
                    if be_target_price < pos.price_current - min_stop_d:
                        if self._modify_position_sltp(pos, be_target_price, pos.tp):
                            self.log(f"[{pk} - Auto-SL {be_label_tag} {current_r:.2f}R] Nâng SL BUY #{pos.ticket} lên {be_target_price:.2f} (+{be_plus_price:g} giá)")
                            status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"

                if auto_trail and current_r >= (be_trigger_r + 0.3):
                    desired_sl = round(pos.price_current - trail_dist_price, digits)
                    if desired_sl > be_target_price and desired_sl > pos.sl + min_trail_step and desired_sl < pos.price_current - min_stop_d:
                        if self._modify_position_sltp(pos, desired_sl, pos.tp):
                            self.log(f"[{pk} - Trailing {current_r:.2f}R] Nâng SL BUY #{pos.ticket} lên {desired_sl:.2f}")
                            status_str = f"Trailing ({current_r:+.2f}R)"
            else:
                if pos.sl > 0 and (abs(pos.sl - be_target_price) < eps_eq or abs(pos.sl - entry_exact) < eps_eq):
                    status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"
                elif 0 < pos.sl < be_target_price:
                    status_str = f"Trailing ({current_r:+.2f}R)"

                if auto_be and current_r >= be_trigger_r and (pos.sl == 0 or pos.sl > be_target_price + eps_eq * 0.5):
                    if be_target_price > pos.price_current + min_stop_d:
                        if self._modify_position_sltp(pos, be_target_price, pos.tp):
                            self.log(f"[{pk} - Auto-SL {be_label_tag} {current_r:.2f}R] Kéo SL SELL #{pos.ticket} xuống {be_target_price:.2f} (+{be_plus_price:g} giá)")
                            status_str = f"SL = {be_label_tag} ({current_r:+.2f}R)"

                if auto_trail and current_r >= (be_trigger_r + 0.3):
                    desired_sl = round(pos.price_current + trail_dist_price, digits)
                    if desired_sl < be_target_price and (pos.sl == 0 or desired_sl < pos.sl - min_trail_step) and desired_sl > pos.price_current + min_stop_d:
                        if self._modify_position_sltp(pos, desired_sl, pos.tp):
                            self.log(f"[{pk} - Trailing {current_r:.2f}R] Kéo SL SELL #{pos.ticket} xuống {desired_sl:.2f}")
                            status_str = f"Trailing ({current_r:+.2f}R)"

            tag = "profit_pos" if pos.profit >= 0 else "profit_neg"
            item_id = tree_widget.insert(
                "", tk.END,
                values=(
                    str(pos.ticket), open_time, dur_str, pos.symbol, type_str, f"{pos.volume:.2f}",
                    f"{pos.price_open:.2f}", f"{pos.price_current:.2f}",
                    f"{pos.sl:.2f}" if pos.sl > 0 else "---",
                    f"{pos.tp:.2f}" if pos.tp > 0 else "---",
                    f"{pos.profit:+.2f} $", status_str
                ),
                tags=(tag,)
            )
            if str(pos.ticket) in selected_tickets:
                tree_widget.selection_add(item_id)

        # Thêm toàn bộ các lệnh đã chốt hôm nay (hiển thị chung trong cùng bảng, xếp mới nhất lên đầu)
        closed_deals = self._get_today_closed_deals(symbol=symbol)
        closed_profit_sum = 0.0
        for cd in closed_deals:
            closed_profit_sum += cd["net_pnl"]
            tag = "closed_pos" if cd["net_pnl"] > 0.05 else ("closed_neg" if cd["net_pnl"] < -0.05 else "closed_be")
            item_id = tree_widget.insert(
                "", tk.END,
                values=(
                    str(cd["ticket"]),
                    cd["time_display"],
                    cd.get("duration", "---"),
                    cd["symbol"],
                    cd["type"],
                    f"{cd['volume']:.2f}",
                    f"{cd['open_price']:.2f}" if cd["open_price"] > 0 else "---",
                    f"{cd['close_price']:.2f} [Đóng]",
                    "---",
                    "---",
                    f"{cd['net_pnl']:+.2f} $",
                    cd["status"]
                ),
                tags=(tag,)
            )
            if str(cd["ticket"]) in selected_tickets:
                tree_widget.selection_add(item_id)

        # Chốt lời chùm lệnh (Basket Take Profit): Nếu tổng lãi các lệnh đang chạy đạt mức cài đặt (basket_tp_usd > 0)
        try:
            basket_tp_usd = max(float(cfg.get("basket_tp_usd", 0.0)), 0.0)
        except ValueError:
            basket_tp_usd = 0.0

        if basket_tp_usd > 0.0 and len(positions) > 0 and total_profit >= basket_tp_usd:
            if not ctx.get("is_basket_closing", False):
                ctx["is_basket_closing"] = True
                threading.Thread(
                    target=self._execute_basket_tp_close,
                    args=(pk, symbol, total_profit, basket_tp_usd, cfg),
                    daemon=True
                ).start()

        pnl_color = GREEN_UP if total_profit >= 0 else RED_DOWN
        daily_pnl_val = self.cached_daily_pnl if pk == self.active_profile_key else ctx.get("cached_daily_pnl", 0.0)
        daily_reason_val = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
        is_locked = self._is_daily_limit_locked(pk)
        daily_lock_tag = f"  |  ĐÃ DỪNG HÔM NAY ({daily_reason_val})" if is_locked else ""
        closed_count = len(closed_deals)
        win_count = sum(1 for cd in closed_deals if cd.get("net_pnl", 0.0) > 0.05)
        loss_count = sum(1 for cd in closed_deals if cd.get("net_pnl", 0.0) < -0.05)
        be_count = max(0, closed_count - win_count - loss_count)
        if closed_count > 0:
            win_rate = (win_count / closed_count) * 100.0
            win_detail = f"{win_count}W - {loss_count}L" if be_count == 0 else f"{win_count}W - {loss_count}L - {be_count}BE"
            win_rate_tag = f"  |  Win Rate: {win_rate:.1f}% ({win_detail})"
        else:
            win_rate_tag = "  |  Win Rate: ---"

        ctx["cached_win_rate"] = (win_count / closed_count * 100.0) if closed_count > 0 else 0.0
        ctx["cached_win_count"] = win_count
        ctx["cached_loss_count"] = loss_count
        ctx["cached_closed_count"] = closed_count
        if pk == self.active_profile_key:
            self.cached_win_rate = ctx["cached_win_rate"]
            self.cached_win_count = win_count
            self.cached_loss_count = loss_count
            self.cached_closed_count = closed_count

        summary_widget.config(
            text=f"[{pk} - {symbol}] Đang mở: {len(positions)} lệnh ({total_profit:+.2f}$)  |  Đã chốt hôm nay: {closed_count} lệnh ({closed_profit_sum:+.2f}$){win_rate_tag}  |  Tổng Lãi/Lỗ Ngày (VN): {daily_pnl_val:+.2f}${daily_lock_tag}",
            fg=GOLD_ACCENT if is_locked else (pnl_color if len(positions) > 0 else CYAN_LIVE)
        )
        return len(positions), total_profit

    def _modify_position_sltp(self, pos, new_sl: float, new_tp: float) -> bool:
        with self._mt5_lock:
            sym_info = mt5.symbol_info(pos.symbol)
            digits = sym_info.digits if sym_info else 2
            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "sl": round(new_sl, digits),
                "tp": round(new_tp, digits) if new_tp > 0 else 0.0
            }
            ok, _, _ = self._send_mt5_order_with_retry(req, pos.symbol, context_tag=f"MODIFY_SLTP_#{pos.ticket}", max_retries=2)
            return ok

    def _close_single_pos(self, pos) -> tuple:
        with self._mt5_lock:
            tick = mt5.symbol_info_tick(pos.symbol)
            sym_info = mt5.symbol_info(pos.symbol)
            if tick is None or sym_info is None:
                self.log_error(f"CLOSE_POS_#{pos.ticket}", extra_info=f"Không lấy được tick/symbol_info cho {pos.symbol}", show_in_ui=False)
                return False, f"Không lấy được giá đóng cho #{pos.ticket}"
            digits = sym_info.digits
            is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
            close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
            price = tick.bid if is_buy else tick.ask

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "position": pos.ticket,
                "symbol": pos.symbol,
                "volume": pos.volume,
                "type": close_type,
                "price": round(price, digits),
                "deviation": 25,
                "magic": pos.magic,
                "comment": "AI_Close",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }
            ok, _, err_detail = self._send_mt5_order_with_retry(req, pos.symbol, context_tag=f"CLOSE_POS_#{pos.ticket}", max_retries=3)
            if not ok:
                return False, f"Không đóng được #{pos.ticket} ({err_detail})"
            return True, f"Đã đóng lệnh #{pos.ticket} ({pos.symbol}) | Lãi/Lỗ: {pos.profit:+.2f}$"

    def breakeven_all_positions(self):
        """Dời SL về giá Entry + khoản lãi nhỏ (be_plus_price) cho các lệnh đang có lãi của mã giao dịch."""
        pk = self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        symbol = self.symbol_var.get().strip()
        default_be_plus = 0.2
        try:
            be_plus_price = max(float(cfg.get("be_plus_price", default_be_plus)), 0.0)
        except ValueError:
            be_plus_price = default_be_plus
        with self._mt5_lock:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            messagebox.showinfo("Thông báo", f"Hiện không có lệnh {symbol} nào đang mở trên MT5.")
            return
        tols = self._get_symbol_tolerances(symbol)
        min_be_d = max(tols["min_manual_be_dist"], tols["min_stop_dist"])
        count = 0
        for pos in positions:
            is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
            target_sl = (pos.price_open + be_plus_price) if is_buy else (pos.price_open - be_plus_price)
            if is_buy and pos.price_current > target_sl + min_be_d:
                if self._modify_position_sltp(pos, target_sl, pos.tp):
                    count += 1
            elif (not is_buy) and pos.price_current < target_sl - min_be_d:
                if self._modify_position_sltp(pos, target_sl, pos.tp):
                    count += 1
        plus_tag = f" (+{be_plus_price:g} giá)" if be_plus_price > 0 else ""
        self.log(f"🛡️ Đã dời SL về Entry{plus_tag} cho {count} lệnh {symbol} đang có lãi.")
        self._update_open_positions_and_manage(symbol, profile_key=pk)

    def close_selected_position(self):
        sel = self.pos_tree.selection()
        if not sel:
            messagebox.showwarning("Chưa chọn lệnh", "Hãy bấm chọn 1 dòng lệnh trong bảng bên dưới trước khi bấm Đóng!")
            return
        ticket_raw = str(self.pos_tree.item(sel[0], "values")[0]).replace("#", "").strip()
        try:
            ticket_int = int(ticket_raw)
        except ValueError:
            return
        with self._mt5_lock:
            positions = mt5.positions_get(ticket=ticket_int)
        if positions:
            ok, msg = self._close_single_pos(positions[0])
            self.log(msg)
            symbol = self.symbol_var.get().strip()
            self._update_open_positions_and_manage(symbol, profile_key=self.active_profile_key)
        else:
            self.show_toast(f"Lệnh #{ticket_int} là lệnh đã đóng trong lịch sử, không thể đóng lại.", is_error=True, duration_ms=2500)

    def _close_all_positions_for_profile(self, profile_key: str, symbol: str) -> int:
        """Đóng an toàn toàn bộ lệnh của một mã cụ thể trên MT5 (dùng cho cả nút bấm tay và chốt chùm tự động)."""
        with self._mt5_lock:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            return 0
        closed = 0
        for pos in positions:
            ok, msg = self._close_single_pos(pos)
            self.log(msg)
            if ok:
                closed += 1
        self.log(f"🔥 [{profile_key}] Đã đóng toàn bộ {closed}/{len(positions)} lệnh {symbol} trên MT5.")
        self._ui_after(100, lambda: self._update_open_positions_and_manage(symbol, profile_key=profile_key))
        return closed

    def _execute_basket_tp_close(self, pk: str, symbol: str, total_profit: float, basket_tp_usd: float, cfg: dict):
        """Đóng toàn bộ các lệnh đang mở khi đạt mục tiêu Chốt lời Chùm Lệnh và kích hoạt thời gian tạm nghỉ (Basket Cooldown)."""
        try:
            self.log(f"🎉 [{pk}] ĐẠT MỤC TIÊU CHỐT LỜI CHÙM LỆNH (+{total_profit:.2f}$ >= {basket_tp_usd:.2f}$) -> Đang đóng toàn bộ lệnh...")
            closed_cnt = self._close_all_positions_for_profile(pk, symbol)
            cooldown_m = 0.0
            try:
                cooldown_m = max(float(cfg.get("basket_cooldown_mins", 15.0)), 0.0)
            except ValueError:
                cooldown_m = 15.0

            ctx = self.profile_ctx[pk]
            if cooldown_m > 0.0:
                ctx["basket_cooldown_until"] = datetime.now() + timedelta(minutes=cooldown_m)
                ctx["pending_pullback_order"] = None
                if pk == self.active_profile_key:
                    self.pending_pullback_order = None
                cd_end_str = ctx["basket_cooldown_until"].strftime("%H:%M:%S")
                self.log(f"[{pk}] Tạm nghỉ vào lệnh mới {cooldown_m:g} phút sau khi chốt chùm (đến {cd_end_str}).")
                self.show_toast(f"[{pk}] Chốt chùm +{total_profit:.2f}$! Tạm nghỉ {cooldown_m:g}p.", is_error=False, duration_ms=3000)
            else:
                self.show_toast(f"[{pk}] Chốt chùm +{total_profit:.2f}$ ({closed_cnt} lệnh) thành công!", is_error=False, duration_ms=2500)
        except Exception as e:
            self.log_error("BASKET_TP_CLOSE", e)
        finally:
            self.profile_ctx[pk]["is_basket_closing"] = False

    def close_all_positions(self):
        pk = self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        symbol = str(cfg.get("symbol", self.symbol_var.get())).strip()
        with self._mt5_lock:
            positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
        if not positions:
            self.show_toast(f"Hiện không có lệnh {symbol} nào đang mở trên MT5.", is_error=False)
            return
        self._close_all_positions_for_profile(pk, symbol)

    def check_mt5_connection(self):
        if not MT5_AVAILABLE:
            self.mt5_status_lbl.config(text="Chưa cài MetaTrader5", fg=RED_DOWN)
            return False
        with self._mt5_lock:
            if not mt5.initialize():
                err_c = mt5.last_error()
                self.mt5_status_lbl.config(text=f"Chưa mở MT5 Exness ({err_c})", fg=RED_DOWN)
                self.log_error("MT5_INIT", extra_info=f"Chưa mở MT5 Exness ({err_c})", show_in_ui=False)
                return False
            acc = mt5.account_info()
        if acc is None:
            self.mt5_status_lbl.config(text="Chưa đăng nhập TK Exness", fg=RED_DOWN)
            return False
        algo_str = "Algo:BẬT" if acc.trade_expert else "Algo:TẮT"
        self.mt5_status_lbl.config(
            text=f"TK: {acc.login} | Dư: {acc.balance:.2f}$ | Eq: {acc.equity:.2f}$ | {algo_str}",
            fg=GREEN_UP if acc.trade_expert else GOLD_ACCENT
        )
        self.log(f"Kết nối MT5 thành công: TK {acc.login} ({acc.server}) | Số dư {acc.balance:.2f} {acc.currency}")
        return True

    def get_local_predictor(self, model_choice: str):
        norm_choice = model_choice.replace("Kronos-", "AI-", 1) if model_choice.startswith("Kronos-") else model_choice
        cfg = MODEL_CONFIGS.get(norm_choice, list(MODEL_CONFIGS.values())[0])
        key = cfg["model"]
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        if key not in self.local_cache:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            weights_root = os.path.join(base_dir, "model", "weights")
            os.makedirs(weights_root, exist_ok=True)

            tok_name = cfg["tokenizer"].split("/")[-1]
            mod_name = cfg["model"].split("/")[-1]
            local_tok_dir = os.path.join(weights_root, tok_name)
            local_mod_dir = os.path.join(weights_root, mod_name)

            fb_tok = cfg.get("fallback_tokenizer", cfg["tokenizer"])
            fb_mod = cfg.get("fallback_model", cfg["model"])

            self.log(f"Đang nạp mô hình {norm_choice} vào {device.upper()}...")
            if os.path.exists(os.path.join(local_tok_dir, "model.safetensors")):
                tokenizer = KronosTokenizer.from_pretrained(local_tok_dir)
            else:
                if cfg.get("is_custom", False):
                    self.log(f"ℹ️ Chưa có trọng số fine-tune '{tok_name}', tự động dùng '{fb_tok}'.")
                tokenizer = KronosTokenizer.from_pretrained(fb_tok)
                try:
                    tokenizer.save_pretrained(local_tok_dir)
                except Exception:
                    pass

            if os.path.exists(os.path.join(local_mod_dir, "model.safetensors")):
                model = Kronos.from_pretrained(local_mod_dir)
            else:
                if cfg.get("is_custom", False):
                    self.log(f"ℹ️ Chưa có trọng số fine-tune '{mod_name}', tự động dùng '{fb_mod}'.")
                model = Kronos.from_pretrained(fb_mod)
                try:
                    model.save_pretrained(local_mod_dir)
                except Exception:
                    pass

            tokenizer.eval()
            model.eval()
            self.local_cache[key] = KronosPredictor(model, tokenizer, device=device, max_context=cfg["max_context"])
            self.log(f"Đã nạp xong mô hình AI trực tiếp từ thư mục máy ({device.upper()}).")
        return self.local_cache[key]

    def _fast_predict_scenarios(self, predictor, df_in: pd.DataFrame, x_timestamp: pd.Series, y_timestamp: pd.Series, pred_len: int = 6, num_scenarios: int = 8, T: float = 1.0, top_k: int = 0, top_p: float = 0.9):
        """
        Tăng tốc dự báo AI gấp 15x-25x (từ ~18 giây xuống ~0.8 giây trên CPU) mà giữ nguyên 100% độ chính xác toán học:
        - Single-Encode (batch=1): Chỉ mã hóa chuỗi nến lịch sử đúng 1 lần thay vì lặp lại 8 lần.
        - Single-Prefill + KV-Cache (batch=1 -> batch=N): Chạy 12 tầng Transformer trên lịch sử đúng 1 lần và lưu bộ đệm Key/Value (KV-Cache),
          sau đó ở mỗi bước dự báo chỉ tính toán trên đúng 1 nến mới sinh ra (seq_len=1).
        - KV-Cache cho cả Cross-Attention (decode_s2) và Tokenizer Decoder.
        """
        device = predictor.device
        max_hist = max(32, min(len(df_in), predictor.max_context - pred_len))
        if len(df_in) > max_hist:
            df_in = df_in.iloc[-max_hist:].reset_index(drop=True)
            x_timestamp = x_timestamp.iloc[-max_hist:].reset_index(drop=True)

        df_c = df_in[["open", "high", "low", "close", "volume"]].copy()
        df_c["amount"] = df_c["volume"] * df_c[["open", "high", "low", "close"]].mean(axis=1)
        x_np = df_c[["open", "high", "low", "close", "volume", "amount"]].values.astype(np.float32)
        x_mean, x_std = np.mean(x_np, axis=0), np.std(x_np, axis=0)
        x_norm = np.clip((x_np - x_mean) / (x_std + 1e-5), -predictor.clip, predictor.clip)

        x_stamp_np = calc_time_stamps(x_timestamp).values.astype(np.float32)
        y_stamp_np = calc_time_stamps(y_timestamp).values.astype(np.float32)

        # Cố định Seed độc lập trong ngữ cảnh fork_rng theo dữ liệu OHLC của nến cuối cùng (không làm ảnh hưởng luồng khác)
        last_r = x_np[-1]
        data_seed = int(abs(hash((len(df_in), round(float(last_r[0]), 2), round(float(last_r[1]), 2), round(float(last_r[2]), 2), round(float(last_r[3]), 2))))) % (2**31 - 1)

        with torch.random.fork_rng(), torch.no_grad():
            torch.manual_seed(data_seed)
            x_t = torch.from_numpy(x_norm[np.newaxis, :]).to(device)
            x_st = torch.from_numpy(x_stamp_np[np.newaxis, :]).to(device)
            y_st = torch.from_numpy(y_stamp_np[np.newaxis, :]).to(device)
            full_stamp_1 = torch.cat([x_st, y_st], dim=1)

            # 1. Single Tokenizer Encode (batch=1)
            x_token_1 = predictor.tokenizer.encode(x_t, half=True)
            init_len = x_t.size(1)

            # 2. Single Transformer Prefill trên chuỗi nến lịch sử (batch=1)
            x_pre = predictor.model.embedding([x_token_1[0], x_token_1[1]]) + predictor.model.time_emb(x_st)
            kv_cache = []
            for layer in predictor.model.transformer:
                res = x_pre
                xn = layer.norm1(x_pre)
                sa = layer.self_attn
                q = sa.q_proj(xn).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                k = sa.k_proj(xn).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                v = sa.v_proj(xn).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                q, k = sa.rotary(q, k)
                kv_cache.append((k.expand(num_scenarios, -1, -1, -1), v.expand(num_scenarios, -1, -1, -1)))
                ao = F.scaled_dot_product_attention(q, k, v, dropout_p=0.0, is_causal=True)
                ao = ao.transpose(1, 2).contiguous().view(1, init_len, sa.d_model)
                x_pre = res + sa.out_proj(ao)
                x_pre = x_pre + layer.ffn(layer.norm2(x_pre))
            ctx_hist = predictor.model.norm(x_pre)
            s1_logits_0 = predictor.model.head(ctx_hist[:, -1:, :])[:, 0, :].expand(num_scenarios, -1).contiguous()

            # Tiền tính toán Key/Value cho DependencyAwareLayer (decode_s2) trên lịch sử (batch=1)
            ca = predictor.model.dep_layer.cross_attn
            cos_1, sin_1 = ca.rotary._update_cos_sin_cache(ctx_hist, 1)
            k_dep_h = ca.k_proj(ctx_hist).view(1, init_len, ca.n_heads, ca.head_dim).transpose(1, 2)
            v_dep_h = ca.v_proj(ctx_hist).view(1, init_len, ca.n_heads, ca.head_dim).transpose(1, 2)
            k_dep_h = (k_dep_h * cos_1) + (ca.rotary._rotate_half(k_dep_h) * sin_1)
            k_dep_cache = k_dep_h.expand(num_scenarios, -1, -1, -1)
            v_dep_cache = v_dep_h.expand(num_scenarios, -1, -1, -1)
            last_ctx = ctx_hist[:, -1:, :].expand(num_scenarios, -1, -1)

            generated_pre = x_token_1[0].new_empty(num_scenarios, pred_len)
            generated_post = x_token_1[1].new_empty(num_scenarios, pred_len)

            for i in range(pred_len):
                if i == 0:
                    s1_logits = s1_logits_0
                else:
                    pos = init_len + i - 1
                    prev_s1 = generated_pre[:, i - 1:i]
                    prev_s2 = generated_post[:, i - 1:i]
                    st_step = full_stamp_1[:, pos:pos + 1, :].expand(num_scenarios, -1, -1)
                    x_step = predictor.model.embedding([prev_s1, prev_s2]) + predictor.model.time_emb(st_step)
                    for idx_l, layer in enumerate(predictor.model.transformer):
                        res = x_step
                        xn = layer.norm1(x_step)
                        sa = layer.self_attn
                        q = sa.q_proj(xn).view(num_scenarios, 1, sa.n_heads, sa.head_dim).transpose(1, 2)
                        k_new = sa.k_proj(xn).view(num_scenarios, 1, sa.n_heads, sa.head_dim).transpose(1, 2)
                        v_new = sa.v_proj(xn).view(num_scenarios, 1, sa.n_heads, sa.head_dim).transpose(1, 2)
                        cos, sin = sa.rotary._update_cos_sin_cache(q, pos + 1)
                        cos_t, sin_t = cos[:, :, pos:pos + 1, :], sin[:, :, pos:pos + 1, :]
                        q = (q * cos_t) + (sa.rotary._rotate_half(q) * sin_t)
                        k_new = (k_new * cos_t) + (sa.rotary._rotate_half(k_new) * sin_t)
                        k_all = torch.cat([kv_cache[idx_l][0], k_new], dim=2)
                        v_all = torch.cat([kv_cache[idx_l][1], v_new], dim=2)
                        kv_cache[idx_l] = (k_all, v_all)
                        ao = F.scaled_dot_product_attention(q, k_all, v_all, dropout_p=0.0, is_causal=False)
                        ao = ao.transpose(1, 2).contiguous().view(num_scenarios, 1, sa.d_model)
                        x_step = res + sa.out_proj(ao)
                        x_step = x_step + layer.ffn(layer.norm2(x_step))
                    last_ctx = predictor.model.norm(x_step)
                    s1_logits = predictor.model.head(last_ctx)[:, 0, :]

                    k_dep_new = ca.k_proj(last_ctx).view(num_scenarios, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
                    v_dep_new = ca.v_proj(last_ctx).view(num_scenarios, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
                    k_dep_new = (k_dep_new * cos_1) + (ca.rotary._rotate_half(k_dep_new) * sin_1)
                    k_dep_cache = torch.cat([k_dep_cache, k_dep_new], dim=2)
                    v_dep_cache = torch.cat([v_dep_cache, v_dep_new], dim=2)

                sample_pre = sample_from_logits(s1_logits, temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)
                sib = predictor.model.embedding.emb_s1(sample_pre)
                q_dep = ca.q_proj(sib).view(num_scenarios, 1, ca.n_heads, ca.head_dim).transpose(1, 2)
                q_dep = (q_dep * cos_1) + (ca.rotary._rotate_half(q_dep) * sin_1)
                ao_dep = F.scaled_dot_product_attention(q_dep, k_dep_cache, v_dep_cache, dropout_p=0.0, is_causal=False)
                ao_dep = ca.out_proj(ao_dep.transpose(1, 2).contiguous().view(num_scenarios, 1, ca.d_model))
                x2_last = predictor.model.dep_layer.norm(last_ctx + ao_dep)
                s2_logits = predictor.model.head.cond_forward(x2_last)[:, 0, :]
                sample_post = sample_from_logits(s2_logits, temperature=T, top_k=top_k, top_p=top_p, sample_logits=True)

                generated_pre[:, i] = sample_pre.squeeze(-1)
                generated_post[:, i] = sample_post.squeeze(-1)

            # 3. Single-Prefill + KV-Cache cho Tokenizer Decoder
            q_hist = predictor.tokenizer.indices_to_bits([x_token_1[0], x_token_1[1]], half=True)
            z_h = predictor.tokenizer.post_quant_embed(q_hist)
            q_gen = predictor.tokenizer.indices_to_bits([generated_pre, generated_post], half=True)
            z_g = predictor.tokenizer.post_quant_embed(q_gen)
            attn_mask = torch.ones((pred_len, init_len + pred_len), dtype=torch.bool, device=device)
            attn_mask[:, init_len:] = torch.tril(torch.ones((pred_len, pred_len), dtype=torch.bool, device=device))
            for layer in predictor.tokenizer.decoder:
                res_h = z_h
                xh = layer.norm1(z_h)
                sa = layer.self_attn
                q_h = sa.q_proj(xh).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                k_h = sa.k_proj(xh).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                v_h = sa.v_proj(xh).view(1, init_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                q_h, k_h = sa.rotary(q_h, k_h)
                ao_h = F.scaled_dot_product_attention(q_h, k_h, v_h, dropout_p=0.0, is_causal=True)
                z_h = res_h + sa.out_proj(ao_h.transpose(1, 2).contiguous().view(1, init_len, sa.d_model))
                z_h = z_h + layer.ffn(layer.norm2(z_h))

                res_g = z_g
                xg = layer.norm1(z_g)
                q_g = sa.q_proj(xg).view(num_scenarios, pred_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                k_g = sa.k_proj(xg).view(num_scenarios, pred_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                v_g = sa.v_proj(xg).view(num_scenarios, pred_len, sa.n_heads, sa.head_dim).transpose(1, 2)
                cos, sin = sa.rotary._update_cos_sin_cache(q_g, init_len + pred_len)
                cos_g, sin_g = cos[:, :, init_len:init_len + pred_len, :], sin[:, :, init_len:init_len + pred_len, :]
                q_g = (q_g * cos_g) + (sa.rotary._rotate_half(q_g) * sin_g)
                k_g = (k_g * cos_g) + (sa.rotary._rotate_half(k_g) * sin_g)
                k_all = torch.cat([k_h.expand(num_scenarios, -1, -1, -1), k_g], dim=2)
                v_all = torch.cat([v_h.expand(num_scenarios, -1, -1, -1), v_g], dim=2)
                ao_g = F.scaled_dot_product_attention(q_g, k_all, v_all, attn_mask=attn_mask, dropout_p=0.0, is_causal=False)
                z_g = res_g + sa.out_proj(ao_g.transpose(1, 2).contiguous().view(num_scenarios, pred_len, sa.d_model))
                z_g = z_g + layer.ffn(layer.norm2(z_g))
            preds = predictor.tokenizer.head(z_g).cpu().numpy()

        scenarios = []
        cols = ["open", "high", "low", "close", "volume", "amount"]
        for b in range(num_scenarios):
            p_b = preds[b] * (x_std + 1e-5) + x_mean
            p_df = pd.DataFrame(p_b, columns=cols)
            p_df.insert(0, "timestamps", y_timestamp.values)
            scenarios.append(p_df)
        return scenarios

    def fetch_mt5_candles(self, symbol: str, timeframe_str: str, count: int, include_forming: bool = False):
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1
        }
        tf = tf_map.get(timeframe_str, mt5.TIMEFRAME_M5)
        start_pos = 0 if include_forming else 1

        rates = None
        for attempt in range(3):
            with self._mt5_lock:
                if not mt5.initialize():
                    raise RuntimeError("Không kết nối được phần mềm MT5 Exness.")
                if not mt5.symbol_select(symbol, True):
                    raise RuntimeError(f"Không tìm thấy mã '{symbol}' trên MT5.")
                rates = mt5.copy_rates_from_pos(symbol, tf, start_pos, count)
            if rates is not None and len(rates) > 0:
                break
            if attempt < 2:
                time.sleep(0.04)

        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Không lấy được nến {symbol} ({timeframe_str}) từ MT5.")

        df = pd.DataFrame(rates)
        df["timestamps"] = pd.to_datetime(df["time"], unit="s")
        df["volume"] = df["tick_volume"].astype(float)
        df = df[["timestamps", "open", "high", "low", "close", "volume"]].dropna()
        return df.sort_values("timestamps").reset_index(drop=True)

    def _refresh_realtime_chart(self, symbol: str, tf_str: str, force_full_redraw: bool = False):
        active_pk = self.active_profile_key
        ana_tf = self.analysis_tf_var.get().strip() or tf_str
        view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(active_pk, tf_str)
        active_tf = view_tf

        # Fast-path: Nếu vẫn đang cùng 1 cây nến hiện tại và không có thay đổi lệnh/SL/TP/lệnh chọn -> chỉ cập nhật tại chỗ cây nến cuối (< 2ms, siêu mượt 60 FPS)
        if (
            not force_full_redraw
            and self._chart_hist_df is not None
            and len(self._chart_hist_df) > 0
            and self._forming_wick_line is not None
            and self._forming_body_rect is not None
        ):
            try:
                df_tail = self.fetch_mt5_candles(symbol, active_tf, 2, include_forming=True)
                last_row = df_tail.iloc[-1]
                last_ts = last_row["timestamps"]
                cur_key = (symbol, active_tf, self.chart_mode, last_ts)

                with self._mt5_lock:
                    open_positions = mt5.positions_get(symbol=symbol) if (self.chart_mode == "LIVE_POSITIONS" and MT5_AVAILABLE) else None
                sel_sig = tuple(sorted(self._get_selected_tickets_for_profile(active_pk)))
                pos_sig = (
                    (tuple((p.ticket, round(p.sl, 2), round(p.tp, 2)) for p in open_positions) if open_positions else ()),
                    sel_sig
                )
                pb_sig = (
                    (self.pending_pullback_order["action"], round(self.pending_pullback_order["sl_price"], 2))
                    if self.pending_pullback_order is not None else None
                )

                if cur_key == self._last_drawn_chart_key and (pos_sig, pb_sig) == self._last_drawn_pos_sig:
                    idx_last = len(self._chart_hist_df) - 1
                    o = float(last_row["open"])
                    h = float(last_row["high"])
                    l = float(last_row["low"])
                    c = float(last_row["close"])
                    v = float(last_row["volume"])
                    self._chart_hist_df.loc[idx_last, ["open", "high", "low", "close", "volume"]] = [o, h, l, c, v]

                    col = CYAN_LIVE if c >= o else "#ff4081"
                    self._forming_wick_line.set_ydata([l, h])
                    self._forming_wick_line.set_color(col)
                    bottom = min(o, c)
                    height = max(abs(c - o), 0.05)
                    self._forming_body_rect.set_y(bottom)
                    self._forming_body_rect.set_height(height)
                    self._forming_body_rect.set_facecolor(col)
                    self._forming_body_rect.set_edgecolor(col)

                    last_candle_ts = last_row["timestamps"]
                    cd_str = self._get_candle_countdown_str(last_candle_ts, active_tf, symbol=symbol)

                    if self._live_price_hline is not None:
                        self._live_price_hline.set_ydata([c, c])
                    if self._live_price_annot is not None:
                        self._live_price_annot.xy = (1.0, c)
                        self._live_price_annot.set_text(f" {c:.2f} ")
                    if self._candle_countdown_annot is not None:
                        self._candle_countdown_annot.xy = (1.0, c)
                        self._candle_countdown_annot.set_text(f" {cd_str} ")

                    # Đảm bảo trục Y tự động co giãn khi giá đâm xuống dưới hoặc vượt lên trên, không bao giờ mất giá
                    try:
                        cur_y0, cur_y1 = self.ax.get_ylim()
                        cur_span = cur_y1 - cur_y0 if cur_y1 > cur_y0 else 1.0
                        safe_pad = max(cur_span * 0.15, 2.0)
                        if l < cur_y0 + safe_pad or h > cur_y1 - safe_pad or c < cur_y0 + safe_pad or c > cur_y1 - safe_pad:
                            new_y0 = min(cur_y0, l - safe_pad, c - safe_pad)
                            new_y1 = max(cur_y1, h + safe_pad, c + safe_pad)
                            self.ax.set_ylim((new_y0, new_y1))
                            if self.user_zoomed and self.saved_ylim is not None:
                                self.saved_ylim = (new_y0, new_y1)
                    except Exception:
                        pass

                    # Cập nhật số Lãi/Lỗ ($) đang nhảy trên nhãn Entry của các lệnh đang mở (giữ nguyên nhãn lệnh chọn nếu có)
                    if open_positions and self._pos_text_artists:
                        eps_eq = self._get_symbol_tolerances(symbol)["eps_equal"]
                        cfg_live = self._get_profile_cfg(self.active_profile_key)
                        def_be_p = 0.2
                        try:
                            be_p_live = max(float(cfg_live.get("be_plus_price", def_be_p)), 0.0)
                        except ValueError:
                            be_p_live = def_be_p
                        pos_by_idx = {i: p for i, p in enumerate(open_positions)}
                        sel_tickets_live = self._get_selected_tickets_for_profile(self.active_profile_key)
                        for txt_artist, col_idx in self._pos_text_artists:
                            txt_str = txt_artist.get_text()
                            if "Entry:" in txt_str and col_idx in pos_by_idx:
                                p_obj = pos_by_idx[col_idx]
                                is_b = (p_obj.type == mt5.POSITION_TYPE_BUY)
                                be_t = (p_obj.price_open + be_p_live) if is_b else (p_obj.price_open - be_p_live)
                                is_sl_be = (p_obj.sl > 0 and (abs(p_obj.sl - be_t) < eps_eq or abs(p_obj.sl - p_obj.price_open) < eps_eq))
                                is_tp_be = (p_obj.tp > 0 and (abs(p_obj.tp - be_t) < eps_eq or abs(p_obj.tp - p_obj.price_open) < eps_eq))
                                be_tag = f" [SL=Entry+{be_p_live:g}]" if (is_sl_be and be_p_live > 0) else (" [SL=0]" if is_sl_be else (" [Chờ Entry+]" if is_tp_be else ""))
                                prefix = f"👉 [LỆNH #{p_obj.ticket}] " if p_obj.ticket in sel_tickets_live else ""
                                txt_artist.set_text(f" {prefix}Entry: {p_obj.price_open:.2f} ({p_obj.profit:+.1f}$){be_tag} ")

                    mode_label = "BIỂU ĐỒ REAL-TIME"
                    zoom_hint = " [Nháy đúp để Reset]" if self.user_zoomed else ""
                    if self.chart_mode == "AI_FORECAST" and active_tf != ana_tf:
                        zoom_hint = f" [⚠ Nến dự báo AI thuộc khung {ana_tf} — bấm nút {ana_tf} để xem]"
                    self.ax.set_title(
                        f"[{mode_label}] {symbol} ({active_tf}) — Giá Live: {c:.2f} ({cd_str}){zoom_hint}",
                        color=GOLD_ACCENT, fontsize=9, fontweight="bold"
                    )
                    self._request_canvas_redraw()
                    return
            except Exception:
                pass

        try:
            chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
        except ValueError:
            chart_bars = 450
        df_live = self.fetch_mt5_candles(symbol, active_tf, chart_bars, include_forming=True)
        if active_tf == ana_tf:
            self.cached_df_analysis = df_live
        if active_tf == tf_str:
            self.cached_df_hist = df_live
        self._draw_candlestick_chart(
            df_hist=df_live,
            scenarios=None,
            mean_df=self.cached_mean_df,
            action=self.cached_action,
            sl_price=self.cached_sl,
            tp_price=self.cached_tp,
            symbol=symbol,
            tf=active_tf,
            has_forming_candle=True
        )

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high = df["high"]
        low = df["low"]
        close = df["close"].shift(1)
        tr = pd.concat([high - low, (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
        return float(tr.iloc[-period:].mean())

    def execute_mt5_order(self, symbol: str, action: str, lot: float, sl_price: float, tp_price: float, max_positions: int, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        cfg = self._get_profile_cfg(pk)
        ctx = self.profile_ctx[pk]

        # Khóa chống vào đúp 2 lệnh trên cùng 1 mã trong cùng thời điểm
        with self._order_guard_lock:
            if self._order_in_flight.get(pk, False):
                return False, f"⏳ [{pk}] Đang gửi lệnh trước đó, tự động chặn đúp lệnh."
            if (time.monotonic() - self._last_order_open_mono.get(pk, 0.0)) < 3.0:
                return False, f"⏳ [{pk}] Vừa khớp lệnh cách đây < 3.0s, tự động chặn đúp lệnh."
            self._order_in_flight[pk] = True

        try:
            with self._mt5_lock:
                if not mt5.initialize():
                    self.log_error(f"OPEN_{action}_{pk}", extra_info="Chưa kết nối MT5", show_in_ui=False)
                    return False, "Chưa kết nối MT5."
                if hasattr(self, "lic_mgr") and self.lic_mgr is not None:
                    lic_st = self.lic_mgr.check_status()
                    if not lic_st.get("is_valid", False):
                        return False, str(lic_st.get('message', 'Bản quyền chưa kích hoạt hoặc đã hết hạn!'))
                if self._is_daily_limit_locked(pk):
                    reason = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
                    return False, f"Đã tự động dừng giao dịch hết hôm nay ({reason})."
                if self._is_cooldown_active(pk):
                    return False, f"{self._get_cooldown_remaining_str(pk)} — Chưa được phép vào lệnh mới."

                mt5.symbol_select(symbol, True)
                positions = mt5.positions_get(symbol=symbol)
                if positions is not None and len(positions) >= max_positions:
                    return False, f"Đã có {len(positions)}/{max_positions} lệnh đang mở. Bỏ qua lệnh mới."

                # Kiểm tra các quy tắc nhồi lệnh (Multi-Entry Guard) khi đã có ít nhất 1 lệnh đang chạy
                if positions is not None and len(positions) > 0:
                    # 1. Giới hạn 1 lệnh mỗi cây nến
                    one_per_candle = bool(cfg.get("one_order_per_candle", True))
                    last_c_time = ctx.get("last_candle_time")
                    if one_per_candle and last_c_time is not None:
                        if self._last_order_candle_ts.get(pk) == last_c_time:
                            return False, f"🛡️ [{pk}] Đã có 1 lệnh mở trong nến {last_c_time}. Chờ nến tiếp theo mới xét nhồi lệnh."

                    # 2. Khoảng cách nhồi lệnh tối thiểu (min_entry_spacing)
                    default_spacing = 1.0
                    try:
                        min_spacing = max(float(cfg.get("min_entry_spacing", default_spacing)), 0.0)
                    except ValueError:
                        min_spacing = default_spacing

                    tick_check = mt5.symbol_info_tick(symbol)
                    cur_chk_price = (tick_check.ask if action == "BUY" else tick_check.bid) if tick_check else 0.0
                    if min_spacing > 0.0 and cur_chk_price > 0.0:
                        for p in positions:
                            dist = abs(cur_chk_price - p.price_open)
                            if dist < (min_spacing - 1e-4):
                                return False, f"⚠️ [{pk}] Giá hiện tại ({cur_chk_price:.2f}) quá sát lệnh #{p.ticket} (Entry {p.price_open:.2f}, cách {dist:.2f} < tối thiểu {min_spacing:.2f} giá). Bỏ qua nhồi lệnh."

                    # 3. Chế độ nhồi lệnh (entry_mode)
                    entry_mode = str(cfg.get("entry_mode", "Chỉ nhồi khi đã khóa lãi lệnh trước (Risk-Free)")).strip()
                    if "Risk-Free" in entry_mode:
                        # Quy tắc Risk-Free Pyramiding: Lệnh trước cùng chiều PHẢI đã được dời SL về hòa vốn (BE+)
                        for p in positions:
                            is_p_buy = (p.type == mt5.POSITION_TYPE_BUY)
                            if is_p_buy != (action == "BUY"):
                                continue
                            is_at_be = (p.sl > 0 and p.sl >= (p.price_open - 1e-4)) if is_p_buy else (p.sl > 0 and p.sl <= (p.price_open + 1e-4))
                            if not is_at_be:
                                dir_tag = "BUY" if is_p_buy else "SELL"
                                return False, f"🛡️ [{pk} - Risk-Free] Lệnh #{p.ticket} ({dir_tag} @ {p.price_open:.2f}) chưa dời SL về hòa vốn BE (SL: {p.sl:.2f}). Chưa đủ điều kiện nhồi lệnh an toàn."

                    elif "DCA" in entry_mode and cur_chk_price > 0.0:
                        # Quy tắc DCA: Lệnh BUY mới phải thấp hơn lệnh BUY trước; Lệnh SELL mới phải cao hơn lệnh SELL trước
                        same_dir_pos = [p for p in positions if (p.type == mt5.POSITION_TYPE_BUY) == (action == "BUY")]
                        if same_dir_pos:
                            if action == "BUY":
                                best_price = min(p.price_open for p in same_dir_pos)
                                if cur_chk_price > (best_price - min_spacing + 1e-4):
                                    return False, f"⚠️ [{pk} - DCA] Giá BUY hiện tại ({cur_chk_price:.2f}) không thấp hơn lệnh BUY thấp nhất ({best_price:.2f}) ít nhất {min_spacing:.2f} giá. Bỏ qua nhồi lệnh."
                            else:
                                best_price = max(p.price_open for p in same_dir_pos)
                                if cur_chk_price < (best_price + min_spacing - 1e-4):
                                    return False, f"⚠️ [{pk} - DCA] Giá SELL hiện tại ({cur_chk_price:.2f}) không cao hơn lệnh SELL cao nhất ({best_price:.2f}) ít nhất {min_spacing:.2f} giá. Bỏ qua nhồi lệnh."

                tick = mt5.symbol_info_tick(symbol)
                sym_info = mt5.symbol_info(symbol)
                if tick is None or sym_info is None:
                    self.log_error(f"OPEN_{action}_{pk}", extra_info=f"Không đọc được giá Bid/Ask của {symbol}", show_in_ui=False)
                    return False, f"Không đọc được giá Bid/Ask của {symbol}."

                digits = sym_info.digits
                order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
                price = tick.ask if action == "BUY" else tick.bid

                # Kiểm tra cứng điều kiện khoảng cách SL tối đa & tối ưu tỷ lệ RR >= 2R
                try:
                    max_sl_price = max(float(cfg.get("max_sl_price", 5.0)), 0.0)
                except ValueError:
                    max_sl_price = 5.0

                tols = self._get_symbol_tolerances(symbol, tick)
                actual_sl_dist = (price - sl_price) if action == "BUY" else (sl_price - price)
                dist_tp = abs(tp_price - price)

                eff_max_sl = max_sl_price
                if actual_sl_dist > 0.001 and dist_tp < 2.0 * actual_sl_dist:
                    eff_max_sl = round(max_sl_price / 2.0, 2) if max_sl_price > 0.0 else round(actual_sl_dist / 2.0, 2)

                if eff_max_sl > 0.0 and actual_sl_dist > eff_max_sl + tols["max_sl_tol"]:
                    limit_entry = (sl_price + eff_max_sl) if action == "BUY" else (sl_price - eff_max_sl)
                    reason_tag = " (Tự động giảm 1/2 Max SL do RR < 2R)" if eff_max_sl < max_sl_price else ""
                    return (
                        False,
                        f"⏳ SL hiện tại cách {actual_sl_dist:.2f} giá > SL tối đa ({eff_max_sl:.2f} giá{reason_tag}). "
                        f"Phải chờ giá hồi về {'<=' if action == 'BUY' else '>='} {limit_entry:.2f} mới vào lệnh!"
                    )

                if actual_sl_dist > 0.001 and dist_tp < 2.0 * actual_sl_dist:
                    min_2r_tp = round(actual_sl_dist * 2.0, digits)
                    tp_price = round(price + min_2r_tp, digits) if action == "BUY" else round(price - min_2r_tp, digits)

                magic_num = self._get_profile_magic_number(pk)
                request = {
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "volume": float(lot),
                    "type": order_type,
                    "price": round(price, digits),
                    "sl": round(sl_price, digits),
                    "tp": round(tp_price, digits),
                    "deviation": 25,
                    "magic": magic_num,
                    "comment": f"AI_{pk}",
                    "type_time": mt5.ORDER_TIME_GTC,
                    "type_filling": mt5.ORDER_FILLING_IOC,
                }
                ok_send, result, err_detail = self._send_mt5_order_with_retry(
                    request, symbol, context_tag=f"OPEN_{action}_{pk}", max_retries=3
                )
                if not ok_send:
                    return False, f"❌ [{pk}] MT5 từ chối lệnh ({err_detail})"

                self._last_order_open_mono[pk] = time.monotonic()
                if ctx.get("last_candle_time") is not None:
                    self._last_order_candle_ts[pk] = ctx.get("last_candle_time")
                exec_price = float(getattr(result, "price", 0.0) or price)
                ticket_id = getattr(result, "order", 0) or getattr(result, "deal", 0)
                return (
                    True,
                    f"[{pk}] ĐÃ KHỚP LỆNH #{ticket_id} ({action} {lot} lot {symbol} @ {exec_price:.2f}) | "
                    f"SL: {sl_price:.2f} (-{actual_sl_dist:.2f} giá) | TP: {tp_price:.2f} | Magic: {magic_num}"
                )
        finally:
            with self._order_guard_lock:
                self._order_in_flight[pk] = False

    def enqueue_ai_scan(self, profile_key: str = None, reason: str = "MANUAL", closed_candle_ts=None) -> bool:
        """Đưa yêu cầu phân tích AI của XAUUSD vào Hàng Đợi Tuần Tự (FIFO Queue)."""
        pk = profile_key or self.active_profile_key
        if pk not in self.profile_ctx:
            return False

        with self._ai_queue_lock:
            if pk in self._ai_queued_profiles or pk == self._ai_current_running_pk:
                return False
            self._ai_queued_profiles.add(pk)
            self.profile_ctx[pk]["is_analyzing"] = True
            running_other = self._ai_current_running_pk if (self._ai_current_running_pk and self._ai_current_running_pk != pk) else None

            if pk == self.active_profile_key:
                self.is_analyzing = True
                self._ui_after(0, self._start_scan_progress_anim)

            if running_other is not None:
                wait_title = f"⏳ [{pk}] ĐANG XẾP HÀNG CHỜ AI PHÂN TÍCH XONG [{running_other}]..."
                self.profile_ctx[pk]["_sig_title"] = wait_title
                self.profile_ctx[pk]["_sig_color"] = GOLD_ACCENT
                if pk == self.active_profile_key:
                    self._sig_title = wait_title
                    self._sig_color = GOLD_ACCENT
                    self._ui_after(
                        0,
                        lambda t=wait_title: self.signal_title_lbl.config(text=t, fg=GOLD_ACCENT)
                        if hasattr(self, "signal_title_lbl") and self.signal_title_lbl.winfo_exists()
                        else None
                    )
                self.log(f"⏳ [HÀNG ĐỢI AI - {pk}] Cùng nhịp quét -> Đã xếp hàng chờ AI phân tích xong [{running_other}] trước (Tuần tự FIFO, không trộn dữ liệu)...")

            self._ai_queue.put((pk, reason, closed_candle_ts, time.monotonic()))
        return True

    def _ai_queue_consumer_loop(self):
        """Luồng duy nhất chuyên xử lý Hàng Đợi Phân Tích AI theo đúng thứ tự FIFO (First-In-First-Out)."""
        while not getattr(self, "_app_closing", False):
            try:
                item = self._ai_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            pk, reason, closed_candle_ts, enq_mono = item
            with self._ai_queue_lock:
                self._ai_queued_profiles.discard(pk)
                self._ai_current_running_pk = pk
            try:
                wait_sec = max(0.0, time.monotonic() - enq_mono)
                if wait_sec >= 0.25:
                    self.log(f"[HÀNG ĐỢI AI - {pk}] Tới lượt phân tích AI (sau {wait_sec:.1f}s xếp hàng)...")
                self._run_analysis_worker(profile_key=pk, reason=reason, closed_candle_ts=closed_candle_ts)
            except Exception as e:
                self.log_error(f"AI_QUEUE_WORKER_{pk}", e)
            finally:
                with self._ai_queue_lock:
                    if self._ai_current_running_pk == pk:
                        self._ai_current_running_pk = None
                try:
                    self._ai_queue.task_done()
                except Exception:
                    pass

    def trigger_single_scan(self, profile_key: str = None):
        pk = profile_key or self.active_profile_key
        self.enqueue_ai_scan(profile_key=pk, reason="MANUAL")

    def _update_bot_status_bar(self, profile_key: str = None):
        """
        Cập nhật thanh trạng thái dừng/nghỉ của Bot:
        - Lỗi hoặc dính rủi ro cắt lỗ -> Chữ màu đỏ (RED_DOWN).
        - Trạng thái dừng bình thường (tắt tự động, chốt chùm, lãi ngày) -> Chữ màu trắng (#ffffff).
        - Khi Bot đang chạy bình thường (RUNNING) -> Không hiển thị (ẩn thanh trạng thái).
        """
        if not hasattr(self, "bot_status_frame") or not self.bot_status_frame.winfo_exists():
            return
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx.get(pk, {})
        now = datetime.now()

        is_running = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))
        cd = self.cooldown_until if pk == self.active_profile_key else ctx.get("cooldown_until")
        bcd = ctx.get("basket_cooldown_until")
        fscd = self.fast_sl_cooldown_until if pk == self.active_profile_key else ctx.get("fast_sl_cooldown_until")

        status_text = ""
        text_color = "#ffffff"

        # 1. Khóa do chạm giới hạn Lãi ngày / Cắt lỗ ngày
        if self._is_daily_limit_locked(pk):
            reason = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
            status_text = f"[{pk}] Tạm ngưng giao dịch do {reason} [Mở lại 00:00]"
            text_color = "#ffffff" if "LÃI" in reason.upper() else RED_DOWN

        # 2. Tạm nghỉ do lệnh dính SL < 5 phút (Lỗi / Cắt lỗ -> Màu đỏ)
        elif fscd is not None and now < fscd:
            rem_sec = max(0, int((fscd - now).total_seconds()))
            m, s = divmod(rem_sec, 60)
            status_text = f"[{pk}] Tạm ngưng giao dịch do lệnh SL < 5 phút [{m:02d}p:{s:02d}s]"
            text_color = RED_DOWN

        # 3. Tạm nghỉ sau chuỗi thua liên tiếp (Lỗi / Cắt lỗ -> Màu đỏ)
        elif cd is not None and now < cd:
            losses = self.consec_loss_count if pk == self.active_profile_key else ctx.get("consec_loss_count", 0)
            rem_sec = max(0, int((cd - now).total_seconds()))
            m, s = divmod(rem_sec, 60)
            status_text = f"[{pk}] Tạm ngưng giao dịch do chuỗi thua {losses} lệnh [{m:02d}p:{s:02d}s]"
            text_color = RED_DOWN

        # 4. Tạm nghỉ sau khi chốt lời chùm lệnh (Bình thường sau chốt lời -> Màu trắng)
        elif bcd is not None and now < bcd:
            rem_sec = max(0, int((bcd - now).total_seconds()))
            m, s = divmod(rem_sec, 60)
            status_text = f"[{pk}] Tạm ngưng giao dịch do sau chốt chùm [{m:02d}p:{s:02d}s]"
            text_color = "#ffffff"

        # 5. Bot AUTO đang TẮT (Thủ công / Dừng bình thường -> Màu trắng)
        elif not is_running:
            status_text = f"[{pk}] Tạm ngưng giao dịch (Tắt tự động)"
            text_color = "#ffffff"

        # 6. Bot đang chạy bình thường (is_running = True và không bị vi phạm/tạm ngưng) -> Ẩn thanh trạng thái
        else:
            status_text = ""

        if not status_text:
            if self.bot_status_frame.winfo_ismapped():
                self.bot_status_frame.pack_forget()
        else:
            if not self.bot_status_frame.winfo_ismapped():
                self.bot_status_frame.pack(fill=tk.X, pady=(4, 0))
            self.bot_status_text_lbl.config(text=status_text, fg=text_color)

    def _get_live_bot_status_text(self, profile_key: str = None) -> str:
        """Lấy chuỗi trạng thái thực tế của Bot theo đúng Tab để hiển thị đồng bộ trên thanh tín hiệu."""
        pk = profile_key or self.active_profile_key
        ctx = self.profile_ctx[pk]
        if self._is_daily_limit_locked(pk):
            reason = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
            return f"Tạm ngưng giao dịch do {reason} [Mở lại 00:00]"
        if self._is_cooldown_active(pk):
            return self._get_cooldown_remaining_str(pk)
        is_running = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))
        if is_running:
            return ""
        return f"Bot {pk} tạm ngưng giao dịch (Tắt tự động)"

    def _sync_signal_detail_status(self):
        """Cập nhật ngay lập tức dòng trạng thái Bot trên thanh tín hiệu khi người dùng gạt công tắc AUTO."""
        self._update_bot_status_bar(self.active_profile_key)

    def toggle_bot_start_stop(self):
        """Gộp chung START và STOP trên 1 công tắc gạt thay đổi trạng thái riêng cho Tab đang mở."""
        if self.auto_loop_running:
            self.stop_auto_scan()
        else:
            self.start_auto_scan()

    def start_auto_scan(self):
        pk = self.active_profile_key
        if self.auto_loop_running:
            return
        if hasattr(self, "lic_mgr") and self.lic_mgr is not None:
            lic_st = self.lic_mgr.check_status(force_refresh=True)
            if not lic_st.get("is_valid", False):
                self.show_toast("Bản quyền chưa kích hoạt hoặc đã hết hạn!", is_error=True, duration_ms=3000)
                self._open_license_dialog()
                return
        self.auto_loop_running = True
        self.profile_ctx[pk]["auto_loop_running"] = True
        self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
        worker_token = self._auto_worker_token[pk]
        self.auto_trade_var.set(True)
        # Giữ nguyên mốc nến đã đóng hiện tại (hoặc cập nhật mốc nến đã đóng gần nhất) để không báo giả "Nến mới vừa đóng" trên cây nến cũ!
        sym = self.symbol_var.get().strip()
        tf_val = self.tf_var.get().strip()
        if self.profile_ctx[pk].get("last_candle_time") is None:
            try:
                df_cur = self.fetch_mt5_candles(sym, tf_val, 3, include_forming=False)
                cur_closed_ts = df_cur["timestamps"].iloc[-1]
                self.profile_ctx[pk]["last_candle_time"] = cur_closed_ts
                self.last_candle_time = cur_closed_ts
            except Exception:
                pass
        self._draw_toggle_switch()
        self._sync_signal_detail_status()
        self.log(f"[{pk} - {sym}] Đã BẬT công tắc AUTO (Canh nến {tf_val} để tự động giao dịch)...")
        if self.profile_ctx[pk].get("cached_mean_df") is None and not self.profile_ctx[pk].get("is_analyzing", False):
            self.enqueue_ai_scan(profile_key=pk, reason="MANUAL")
        threading.Thread(target=self._auto_loop_worker, args=(pk, worker_token), daemon=True).start()

    def stop_auto_scan(self):
        pk = self.active_profile_key
        self.auto_loop_running = False
        self.profile_ctx[pk]["auto_loop_running"] = False
        self._auto_worker_token[pk] = self._auto_worker_token.get(pk, 0) + 1
        self.auto_trade_var.set(False)
        self.pending_pullback_order = None
        self.profile_ctx[pk]["pending_pullback_order"] = None
        self._draw_toggle_switch()
        self._sync_signal_detail_status()
        sym = self.symbol_var.get().strip()
        self.log(f"⏹️ [{pk} - {sym}] Đã TẮT công tắc AUTO (Ngừng canh nến & tắt tự động vào lệnh).")

    def _auto_loop_worker(self, profile_key: str = None, worker_token: int = None):
        pk = profile_key or self.active_profile_key
        if worker_token is None:
            worker_token = self._auto_worker_token.get(pk, 0)
        conn_err_logged = False
        while (
            not getattr(self, "_app_closing", False)
            and self.profile_ctx[pk].get("auto_loop_running", False)
            and self._auto_worker_token.get(pk, 0) == worker_token
        ):
            sleep_chunks = 4
            try:
                cfg = self._get_profile_cfg(pk)
                ctx = self.profile_ctx[pk]
                symbol = str(cfg.get("symbol", "")).strip()
                tf = str(cfg.get("tf", "M5")).strip()
                df_check = self.fetch_mt5_candles(symbol, tf, 3, include_forming=False)
                if (
                    not self.profile_ctx[pk].get("auto_loop_running", False)
                    or self._auto_worker_token.get(pk, 0) != worker_token
                ):
                    break

                if conn_err_logged:
                    conn_err_logged = False
                    self.log(f"🟢 [{pk} - {symbol}] Đã kết nối lại thành công với MT5 Exness! Tiếp tục tự động canh nến.")

                latest_time = df_check["timestamps"].iloc[-1]
                last_ct = ctx.get("last_candle_time")
                is_ana = ctx.get("is_analyzing", False)
                if last_ct != latest_time and not is_ana:
                    ctx["last_candle_time"] = latest_time
                    if pk == self.active_profile_key:
                        self.last_candle_time = latest_time
                    self.log(f"🔔 [{pk} - {symbol}] Nến {tf} mới vừa đóng ({latest_time}). Đưa vào hàng đợi phân tích AI...")
                    self.enqueue_ai_scan(profile_key=pk, reason="AUTO_CANDLE", closed_candle_ts=latest_time)
            except Exception as e:
                if not conn_err_logged:
                    conn_err_logged = True
                    self.log(f"⚠️ [{pk}] Đang chờ kết nối MT5 Exness (Máy chủ sàn đang bảo trì hoặc mất kết nối: {e}). Bot sẽ tự kết nối lại ngay khi sàn mở lại...")
                    self.log_error(f"AUTO_CANDLE_WATCH_{pk}", e, show_in_ui=False)
                else:
                    self.log_error(f"AUTO_CANDLE_WATCH_{pk}", e, show_in_ui=False)
                sleep_chunks = 10  # Khi mất kết nối: giãn cách 5 giây (10 * 0.5s) thay vì 2s để tránh nghẽn CPU
            for _ in range(sleep_chunks):
                if (
                    getattr(self, "_app_closing", False)
                    or not self.profile_ctx[pk].get("auto_loop_running", False)
                    or self._auto_worker_token.get(pk, 0) != worker_token
                ):
                    return
                time.sleep(0.5)

    def _run_analysis_worker(self, profile_key: str = None, reason: str = "MANUAL", closed_candle_ts=None):
        pk = profile_key or self.active_profile_key
        self.profile_ctx[pk]["is_analyzing"] = True
        if pk == self.active_profile_key:
            self.is_analyzing = True
            self._ui_after(0, self._start_scan_progress_anim)
        try:
            with self._ai_scan_lock:
                # Chụp bản sao cấu hình của hồ sơ XAUUSD tại thời điểm bắt đầu phân tích
                cfg = dict(self._get_profile_cfg(pk))
                ctx = self.profile_ctx[pk]
                default_sym = DEFAULT_PROFILE_SETTINGS.get(pk, {}).get("symbol", "XAUUSDm")
                symbol = str(cfg.get("symbol", default_sym)).strip() or default_sym
                scalp_tf = str(cfg.get("tf", "M5")).strip() or "M5"
                analysis_tf = str(cfg.get("analysis_tf", scalp_tf)).strip() or scalp_tf
                tf = scalp_tf
                tf_label = scalp_tf if analysis_tf == scalp_tf else f"AI:{analysis_tf} -> Scalp:{scalp_tf}"
                lookback = int(cfg.get("lookback", 450))
                pred_len = int(cfg.get("pred_len", 6))
                num_scenarios = int(cfg.get("scenarios", 8))
                min_consensus = float(cfg.get("consensus", 75.0))
                max_dispersion = float(cfg.get("max_dispersion", 2.5))
                min_move = float(cfg.get("min_move", 1.2))
                try:
                    rev_consensus = float(cfg.get("rev_consensus", min_consensus))
                except ValueError:
                    rev_consensus = min_consensus
                try:
                    rev_min_move = max(float(cfg.get("rev_min_move", 0.0)), 0.0)
                except ValueError:
                    rev_min_move = 0.0
                default_be_plus = 0.2
                try:
                    be_plus_price = max(float(cfg.get("be_plus_price", default_be_plus)), 0.0)
                except ValueError:
                    be_plus_price = default_be_plus
                max_spread = float(cfg.get("max_spread", 0.35))
                lot = float(cfg.get("lot", 0.01))
                sl_atr = float(cfg.get("sl_atr", 1.5))
                sl_buffer = max(float(cfg.get("sl_buffer", 0.5)), 0.0)
                max_sl_price = max(float(cfg.get("max_sl_price", 5.0)), 0.0)
                rr = max(float(cfg.get("rr", 2.0)), 0.0)
                max_pos = int(cfg.get("max_pos", 3))
                auto_rev_close = bool(cfg.get("auto_rev_close", True))
                model_choice = str(cfg.get("model", "AI-base (102.3M - Mạnh nhất)"))
                engine_mode = str(cfg.get("engine", "Chạy trực tiếp máy Local"))
                hf_space = str(cfg.get("hf_space", "")).strip()
                hf_token = str(cfg.get("hf_token", "")).strip()

                # 1. Lấy dữ liệu nến của Khung Phân Tích AI (analysis_tf) riêng cho mã symbol của pk
                if pk == self.active_profile_key:
                    self._set_loading_stage(28.0, f"Đang tải dữ liệu nến {symbol} ({analysis_tf}) từ MT5...")
                chart_bars = max(lookback, 450)
                df_with_forming = self.fetch_mt5_candles(symbol, analysis_tf, chart_bars + 1, include_forming=True)
                with self._mt5_lock:
                    tick = mt5.symbol_info_tick(symbol)
                tols = self._get_symbol_tolerances(symbol, tick)
                server_now_ts = pd.to_datetime(int(tick.time), unit="s") if tick is not None else df_with_forming["timestamps"].iloc[-1]
                forming_open_ts = df_with_forming["timestamps"].iloc[-1]
                forming_age_sec = (server_now_ts - forming_open_ts).total_seconds()

                # Nếu kích hoạt tự động khi đóng nến (AUTO_CANDLE), giữ ngưỡng cắt nến mới mở 60s cho cả đơn khung & đa khung
                # để dù mã thứ 2 có xếp hàng chờ mã thứ 1 phân tích xong (~2-5s) thì vẫn phân tích chính xác cây nến vừa đóng!
                age_cutoff_sec = 60.0 if reason == "AUTO_CANDLE" else 15.0
                if forming_age_sec < age_cutoff_sec and len(df_with_forming) > 35:
                    df = df_with_forming.iloc[:-1].reset_index(drop=True)
                else:
                    df = df_with_forming.iloc[-chart_bars:].reset_index(drop=True)

                ai_lookback = min(max(lookback, 50), 500)
                # Sao chép độc lập (Deep Copy) bảng dữ liệu nến đưa vào AI để không bao giờ bị tham chiếu chéo
                df_ai = df.iloc[-ai_lookback:][["timestamps", "open", "high", "low", "close", "volume"]].copy(deep=True).reset_index(drop=True)
                last_close = float(df_ai["close"].iloc[-1])

                # 2. Lấy nến Khung Scalping để canh thời điểm đóng nến & kết hợp ATR đa khung
                if analysis_tf == scalp_tf:
                    df_scalp = self.fetch_mt5_candles(symbol, scalp_tf, 60, include_forming=False)
                else:
                    if pk == self.active_profile_key:
                        self._set_loading_stage(40.0, f"Đang đồng bộ khung vào lệnh Scalping ({scalp_tf})...")
                    df_scalp = self.fetch_mt5_candles(symbol, scalp_tf, 60, include_forming=False)

                last_time = df_scalp["timestamps"].iloc[-1]
                ctx["last_candle_time"] = last_time
                if pk == self.active_profile_key:
                    self.last_candle_time = last_time
                atr_scalp = self.calculate_atr(df_scalp, 14)
                atr_ana = self.calculate_atr(df, 14) if analysis_tf != scalp_tf else atr_scalp
                atr = max(atr_scalp, atr_ana * 0.65)

                spread_usd = (tick.ask - tick.bid) if tick else 0.0

                x_timestamp = df_ai["timestamps"].copy(deep=True).reset_index(drop=True)
                delta = x_timestamp.iloc[-1] - x_timestamp.iloc[-2] if len(x_timestamp) >= 2 else pd.Timedelta(minutes=5)
                future_dates = [x_timestamp.iloc[-1] + delta * (i + 1) for i in range(pred_len)]
                y_timestamp = pd.Series(pd.to_datetime(future_dates))

                scenarios = None
                used_engine = "GTX 1650 Local" if torch.cuda.is_available() else "CPU Local (KV-Cache)"

                if engine_mode.startswith("Ưu tiên Hugging Face") and HF_CLIENT_AVAILABLE and hf_space:
                    try:
                        if pk == self.active_profile_key:
                            self._set_loading_stage(55.0, f"Đang gọi Hugging Face Space ({hf_space})...")
                        self.log(f"[{pk}] Đang gọi Hugging Face Space ({hf_space})...")

                        # TỰ ĐỘNG ĐÁNH THỨC SPACE NẾU ĐANG NGỦ (AUTO-WAKE SPACE)
                        if hf_token:
                            try:
                                from huggingface_hub import HfApi
                                hf_api = HfApi(token=hf_token)
                                runtime = hf_api.get_space_runtime(hf_space)
                                if runtime and getattr(runtime, "stage", None) in ("PAUSED", "SLEEPING", "STOPPED"):
                                    self.log(f"🔔 [{pk}] Phát hiện HF Space đang ngủ ({runtime.stage}) -> Đã gửi lệnh tự động đánh thức!")
                                    hf_api.restart_space(hf_space)
                                    self.log(f"⏳ [{pk}] Máy chủ HF đang khởi động (ước tính 30-45s)... Tạm chuyển sang Local CPU để không lỡ nhịp nến!")
                                    raise RuntimeError(f"HF Space đang được đánh thức ({runtime.stage}), tạm chuyển Local")
                            except Exception as wake_err:
                                if "đang được đánh thức" in str(wake_err):
                                    raise

                        client_kwargs = {}
                        if hf_token:
                            try:
                                import inspect
                                params = inspect.signature(Client.__init__).parameters
                                if "token" in params:
                                    client_kwargs["token"] = hf_token
                                elif "hf_token" in params:
                                    client_kwargs["hf_token"] = hf_token
                                else:
                                    client_kwargs["headers"] = {"Authorization": f"Bearer {hf_token}"}
                            except Exception:
                                client_kwargs["token"] = hf_token
                        client = Client(hf_space, **client_kwargs)
                        payload = df_ai.copy(deep=True)
                        payload["timestamps"] = payload["timestamps"].dt.strftime("%Y-%m-%d %H:%M:%S")
                        hf_model_arg = model_choice.replace("AI-", "Kronos-", 1) if model_choice.startswith("AI-") else model_choice
                        res_json = client.predict(
                            candles_json=payload.to_json(orient="records"),
                            model_choice=hf_model_arg,
                            pred_len=pred_len,
                            num_scenarios=num_scenarios,
                            temperature=1.0,
                            top_p=0.9,
                            api_name="/scalping_m5"
                        )
                        data = json.loads(res_json)
                        scenarios = []
                        for s_rec in data["scenarios"]:
                            s_df = pd.DataFrame(s_rec)
                            s_df["timestamps"] = pd.to_datetime(s_df["timestamps"])
                            scenarios.append(s_df)
                        used_engine = "Hugging Face A100"
                    except Exception as e:
                        err_str = str(e)
                        if "401" in err_str or "Repository Not Found" in err_str:
                            self.log(f"⚠️ [{pk}] Space '{hf_space}' là Private nhưng chưa nhập HF Token -> Chuyển sang chạy Local máy nhà")
                        elif "đang được đánh thức" in err_str:
                            pass
                        else:
                            self.log(f"⚠️ [{pk}] Chuyển sang chạy trực tiếp trên máy nhà ({err_str})")

                if scenarios is None:
                    if pk == self.active_profile_key:
                        self._set_loading_stage(62.0, f"Đang nạp mô hình {model_choice}...")
                    predictor = self.get_local_predictor(model_choice)
                    if pk == self.active_profile_key:
                        self._set_loading_stage(82.0, f"Đang phân tích {num_scenarios} kịch bản xu hướng AI ({symbol})...")
                    scenarios = self._fast_predict_scenarios(
                        predictor=predictor,
                        df_in=df_ai,
                        x_timestamp=x_timestamp,
                        y_timestamp=y_timestamp,
                        pred_len=pred_len,
                        num_scenarios=num_scenarios,
                        T=1.0,
                        top_k=0,
                        top_p=0.9
                    )

                bull_count = sum(1 for s in scenarios if float(s["close"].iloc[-1]) > last_close)
                bear_count = sum(1 for s in scenarios if float(s["close"].iloc[-1]) < last_close)
                total = len(scenarios)
                bull_pct = (bull_count / total) * 100.0
                bear_pct = (bear_count / total) * 100.0

                # Tính đường giá đóng cửa trung bình của cả 8 kịch bản
                mean_closes = np.mean([s["close"].values for s in scenarios], axis=0)
                avg_end_close = float(mean_closes[-1])

                # Chọn Kịch bản Đại diện Chuẩn nhất (Medoid)
                best_idx = int(np.argmin([np.mean(np.abs(s["close"].values - mean_closes)) for s in scenarios]))
                mean_df = scenarios[best_idx][["timestamps", "open", "high", "low", "close"]].copy(deep=True)

                # Phân tích Độ phân tán & Bất định kịch bản (Scenario Uncertainty Quantification)
                end_closes = np.array([float(s["close"].iloc[-1]) for s in scenarios], dtype=float)
                scenario_std = float(np.std(end_closes))
                dispersion_r = float(scenario_std / max(atr, 0.01))
                dir_pct = max(bull_pct, bear_pct)
                disp_penalty = max(0.0, min(0.45, (dispersion_r - 1.0) * 0.25)) if dispersion_r > 1.0 else 0.0
                confidence_pct = max(10.0, min(100.0, dir_pct * (1.0 - disp_penalty)))
                is_high_uncertainty = (max_dispersion > 0.0 and dispersion_r > max_dispersion)

                expected_move = avg_end_close - last_close
                ai_pred_high = float(mean_df["high"].max())
                ai_pred_low = float(mean_df["low"].min())
                ai_swing_low_dist = max(last_close - ai_pred_low, 0.0)
                ai_swing_high_dist = max(ai_pred_high - last_close, 0.0)
                base_atr_sl = max(atr * sl_atr, tols["min_atr_sl_floor"])

                ai_tp_dist_up = max(ai_pred_high - last_close, abs(expected_move), tols["min_tp_floor"])
                ai_tp_dist_down = max(last_close - ai_pred_low, abs(expected_move), tols["min_tp_floor"])

                action = None
                sig_color = "#ffffff"  # Chờ/Đứng ngoài: chữ trắng
                ref_tag = ""
                if bull_pct >= min_consensus and expected_move >= min_move:
                    if is_high_uncertainty:
                        action = None
                        sig_color = "#ffffff"  # Chờ/Đứng ngoài: chữ trắng
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BẤT ĐỊNH CAO) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% | Phân tán: {dispersion_r:.2f}x ATR > {max_dispersion:.1f}x | Tin cậy: {confidence_pct:.0f}%"
                    else:
                        action = "BUY"
                        sig_color = GREEN_UP
                        sig_title = f"🟢 TÍN HIỆU [{pk}]: BUY {symbol} ({tf_label}) — Đồng thuận {bull_pct:.0f}% ({bull_count}/{total}, cần ≥ {min_consensus:.0f}%) | Tin cậy: {confidence_pct:.0f}% (Phân tán: {dispersion_r:.2f}x ATR)"
                    entry_ref = tick.ask if tick else last_close
                    sl_ai_raw_dist = max(base_atr_sl, ai_swing_low_dist)
                    total_sl_dist = sl_ai_raw_dist + sl_buffer
                    sl_price = round(entry_ref - total_sl_dist, 2)
                    eff_entry = min(entry_ref, round(sl_price + max_sl_price, 2)) if max_sl_price > 0.0 else entry_ref
                    if rr > 0.0:
                        tp_price = round(eff_entry + (eff_entry - sl_price) * rr, 2)
                    else:
                        tp_price = round(max(ai_pred_high, eff_entry + ai_tp_dist_up), 2)
                elif bear_pct >= min_consensus and (-expected_move) >= min_move:
                    if is_high_uncertainty:
                        action = None
                        sig_color = "#ffffff"  # Chờ/Đứng ngoài: chữ trắng
                        sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI (BẤT ĐỊNH CAO) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% | Phân tán: {dispersion_r:.2f}x ATR > {max_dispersion:.1f}x | Tin cậy: {confidence_pct:.0f}%"
                    else:
                        action = "SELL"
                        sig_color = RED_DOWN
                        sig_title = f"🔴 TÍN HIỆU [{pk}]: SELL {symbol} ({tf_label}) — Đồng thuận {bear_pct:.0f}% ({bear_count}/{total}, cần ≥ {min_consensus:.0f}%) | Tin cậy: {confidence_pct:.0f}% (Phân tán: {dispersion_r:.2f}x ATR)"
                    entry_ref = tick.bid if tick else last_close
                    sl_ai_raw_dist = max(base_atr_sl, ai_swing_high_dist)
                    total_sl_dist = sl_ai_raw_dist + sl_buffer
                    sl_price = round(entry_ref + total_sl_dist, 2)
                    eff_entry = max(entry_ref, round(sl_price - max_sl_price, 2)) if max_sl_price > 0.0 else entry_ref
                    if rr > 0.0:
                        tp_price = round(eff_entry - (sl_price - eff_entry) * rr, 2)
                    else:
                        tp_price = round(min(ai_pred_low, eff_entry - ai_tp_dist_down), 2)
                else:
                    entry_ref = last_close
                    is_bear_bias = (bear_pct > bull_pct) or (bear_pct == bull_pct and expected_move < 0)
                    if is_bear_bias:
                        ref_tag = " (Tham khảo SELL)"
                        sl_ai_raw_dist = max(base_atr_sl, ai_swing_high_dist)
                        total_sl_dist = sl_ai_raw_dist + sl_buffer
                        eff_sl_for_tp = min(total_sl_dist, max_sl_price) if max_sl_price > 0.0 else total_sl_dist
                        tp_dist = (eff_sl_for_tp * rr) if rr > 0.0 else ai_tp_dist_down
                        sl_price = round(last_close + total_sl_dist, 2)
                        tp_price = round(last_close - tp_dist, 2)
                    else:
                        ref_tag = " (Tham khảo BUY)"
                        sl_ai_raw_dist = max(base_atr_sl, ai_swing_low_dist)
                        total_sl_dist = sl_ai_raw_dist + sl_buffer
                        eff_sl_for_tp = min(total_sl_dist, max_sl_price) if max_sl_price > 0.0 else total_sl_dist
                        tp_dist = (eff_sl_for_tp * rr) if rr > 0.0 else ai_tp_dist_up
                        sl_price = round(last_close - total_sl_dist, 2)
                        tp_price = round(last_close + tp_dist, 2)
                    sig_title = f"⚪ [{pk} - {symbol}] ĐỨNG NGOÀI ({tf_label}) — Đồng thuận: Tăng {bull_pct:.0f}% | Giảm {bear_pct:.0f}% (cần ≥ {min_consensus:.0f}%) | Phân tán: {dispersion_r:.2f}x ATR | Tin cậy: {confidence_pct:.0f}%"

                tp_dist = abs(tp_price - entry_ref)
                ctx["last_recommendation"] = {"sl_dist": sl_ai_raw_dist, "tp_dist": tp_dist}
                ctx["cached_scenarios"] = scenarios
                ctx["cached_mean_df"] = mean_df
                ctx["cached_action"] = action
                ctx["cached_sl"] = sl_price
                ctx["cached_tp"] = tp_price
                ctx["_sig_title"] = sig_title
                ctx["_sig_color"] = sig_color

                # XỬ LÝ 3 QUY TẮC BẢO VỆ KHI AI BÁO ĐẢO CHIỀU NGƯỢC LỆNH ĐANG GIỮ
                rev_action = action
                if rev_action is None and not is_high_uncertainty:
                    if bull_pct >= rev_consensus and (rev_min_move <= 0.0 or expected_move >= rev_min_move):
                        rev_action = "BUY"
                    elif bear_pct >= rev_consensus and (rev_min_move <= 0.0 or (-expected_move) >= rev_min_move):
                        rev_action = "SELL"

                has_losing_opposite_pos = False
                if rev_action is not None:
                    with self._mt5_lock:
                        open_positions = mt5.positions_get(symbol=symbol)
                        sym_info = mt5.symbol_info(symbol) if open_positions else None
                    if open_positions:
                        digits = sym_info.digits if sym_info else 2
                        eps_eq = tols["eps_equal"]
                        for pos in open_positions:
                            is_buy_pos = (pos.type == mt5.POSITION_TYPE_BUY)
                            is_opposite = (rev_action == "BUY" and not is_buy_pos) or (rev_action == "SELL" and is_buy_pos)
                            if not is_opposite:
                                continue

                            # QUY TẮC 1: Lệnh ngược chiều đang CÓ LÃI (> 0$) -> Chốt lời ngay để đảo chiều
                            if pos.profit > 0.0:
                                if auto_rev_close:
                                    ok_c, msg_c = self._close_single_pos(pos)
                                    self.pending_be_exit_tickets.discard(pos.ticket)
                                    self.log(f"🔄 [{pk} - Quy tắc 1 - AI Đảo Chiều sang {rev_action}] Lệnh cũ đang lãi -> {msg_c}")
                            else:
                                # QUY TẮC 2 & 3: Lệnh ngược chiều đang ÂM (<= 0$) -> Chờ hồi về Entry + khoản lãi nhỏ (be_plus_price) để thoát có lãi nhẹ
                                has_losing_opposite_pos = True
                                if auto_rev_close:
                                    be_target_price = round((pos.price_open + be_plus_price) if is_buy_pos else (pos.price_open - be_plus_price), digits)
                                    be_label_tag = f"Entry+{be_plus_price:g}" if be_plus_price > 0 else "Entry"
                                    self.pending_be_exit_tickets.add(pos.ticket)
                                    if abs(pos.tp - be_target_price) >= eps_eq:
                                        if self._modify_position_sltp(pos, pos.sl, be_target_price):
                                            self.log(
                                                f"⏳ [{pk} - Quy tắc 2 & 3 - AI Đảo Chiều sang {rev_action}] Giữ lệnh #{pos.ticket} ({pos.profit:+.2f}$), "
                                                f"đã kéo điểm thoát về {be_label_tag} ({be_target_price:.2f}) chờ hồi thoát có lãi nhẹ!"
                                            )
                                        else:
                                            self.log(
                                                f"⏳ [{pk} - Quy tắc 2 & 3 - AI Đảo Chiều sang {rev_action}] Đã đánh dấu lệnh #{pos.ticket} ({pos.profit:+.2f}$) "
                                                f"tự động thoát ngay khi giá hồi về {be_label_tag} ({be_target_price:.2f})!"
                                            )

                # KIỂM TRA ĐIỀU KIỆN KHOẢNG CÁCH TỐI ĐA SL & TỶ LỆ REWARD/RISK >= 2R
                cur_sl_dist = abs(entry_ref - sl_price)
                dist_tp = abs(tp_price - entry_ref)

                effective_max_sl = max_sl_price
                sl_tightened_for_2r = False
                if action in ("BUY", "SELL") and cur_sl_dist > 0.001:
                    # Kiểm tra khoảng cách từ entry đến TP có đạt mức >= 2R (dist_tp >= 2.0 * cur_sl_dist) không
                    if dist_tp < 2.0 * cur_sl_dist:
                        # Tự động giảm SL tối đa do người dùng cài chia 2 (ví dụ 5.0 -> 2.50) để thu gọn khoảng cách & nâng tỷ lệ RR
                        base_sl_cap = max_sl_price if max_sl_price > 0.0 else cur_sl_dist
                        effective_max_sl = round(base_sl_cap / 2.0, 2)
                        sl_tightened_for_2r = True
                        self.log(
                            f"⚖️ [{pk} - Tối Ưu RR ≥ 2R] Khoảng cách TP ({dist_tp:.2f}$) < 2R của SL ({cur_sl_dist:.2f}$) -> "
                            f"Tự động giảm SL tối đa: {base_sl_cap:.2f}$ ÷ 2 = {effective_max_sl:.2f}$ để thu gọn khoảng cách & tối ưu RR ≥ 2R!"
                        )

                limit_entry_price = round((sl_price + effective_max_sl) if action == "BUY" else (sl_price - effective_max_sl), 2) if effective_max_sl > 0.0 else entry_ref
                is_auto_on = self.auto_loop_running if pk == self.active_profile_key else bool(ctx.get("auto_loop_running", False))

                trade_msg = self._get_live_bot_status_text(pk)
                if action is not None:
                    if self._is_daily_limit_locked(pk):
                        ctx["pending_pullback_order"] = None
                        reason_lk = self.daily_limit_reason if pk == self.active_profile_key else ctx.get("daily_limit_reason", "")
                        trade_msg = f"Đã dừng giao dịch hôm nay ({reason_lk})"
                        self.log(f"[{pk}] {trade_msg}")
                    elif self._is_cooldown_active(pk):
                        ctx["pending_pullback_order"] = None
                        trade_msg = self._get_cooldown_remaining_str(pk)
                        self.log(f"[{pk}] {trade_msg}")
                    elif has_losing_opposite_pos:
                        ctx["pending_pullback_order"] = None
                        trade_msg = f"⏳ Chờ lệnh ngược chiều hồi về Entry thoát (Không mở {action} đè)"
                        self.log(f"⏳ [{pk} - Quy tắc 2] Đang có lệnh ngược chiều chờ về Entry hòa vốn -> Tạm ngưng mở lệnh {action} mới.")
                    elif effective_max_sl > 0.0 and cur_sl_dist > effective_max_sl + tols["max_sl_tol"]:
                        ctx["pending_pullback_order"] = {
                            "symbol": symbol,
                            "action": action,
                            "sl_price": sl_price,
                            "manual_queued": False,
                            "effective_max_sl": effective_max_sl,
                            "sl_tightened_for_2r": sl_tightened_for_2r,
                            "target_tp_price": tp_price,
                        }
                        cond_op = "<=" if action == "BUY" else ">="
                        tight_tag = " [Siết 1/2 Max SL do RR < 2R]" if sl_tightened_for_2r else ""
                        trade_msg = (
                            f"⏳ SL = {cur_sl_dist:.2f} giá > Max ({effective_max_sl:.2f} giá{tight_tag}) -> "
                            f"Đang chờ giá hồi về {cond_op} {limit_entry_price:.2f} mới vào {action}"
                        )
                        self.log(
                            f"⏳ [{pk} - Chờ Giá Hồi <= SL Max{tight_tag}] AI báo {action} (SL AI+{sl_buffer:.1f} = {sl_price:.2f}, cách {cur_sl_dist:.2f} giá > SL Max {effective_max_sl:.2f} giá). "
                            f"Đang canh giá hồi về {cond_op} {limit_entry_price:.2f} mới vào lệnh!"
                        )
                    elif spread_usd > max_spread:
                        trade_msg = f"⚠️ Khóa lệnh do Spread cao ({spread_usd:.2f}$ > {max_spread}$)"
                        self.log(f"[{pk}] {trade_msg}")
                    elif is_auto_on and self._last_order_candle_ts.get(pk) == last_time:
                        ctx["pending_pullback_order"] = None
                        trade_msg = f"🛡️ Đã vào lệnh trên nến {last_time} — Chờ cây nến {scalp_tf} tiếp theo"
                        self.log(f"🛡️ [{pk}] Đã mở lệnh trên cây nến {last_time}, tự động chặn mở thêm lệnh thứ 2 trên cùng một cây nến.")
                    elif is_auto_on:
                        ctx["pending_pullback_order"] = None
                        # Nếu khoảng cách SL đã đạt chuẩn nhỏ <= effective_max_sl nhưng TP chưa đạt 2R -> tự động căn chỉnh TP để chắc chắn đạt >= 2R
                        if sl_tightened_for_2r and dist_tp < 2.0 * cur_sl_dist:
                            min_tp_dist = round(cur_sl_dist * 2.0, 2)
                            tp_price = round(entry_ref + min_tp_dist, 2) if action == "BUY" else round(entry_ref - min_tp_dist, 2)
                            dist_tp = min_tp_dist
                        ok_ord, ord_msg = self.execute_mt5_order(symbol, action, lot, sl_price, tp_price, max_pos, profile_key=pk)
                        if ok_ord:
                            self._last_order_candle_ts[pk] = last_time
                        trade_msg = ord_msg
                        self.log(ord_msg)
                    else:
                        ctx["pending_pullback_order"] = None
                        self.log(f"[{pk}] Có tín hiệu {action} {symbol} @ {entry_ref:.2f} (SL: {sl_price:.2f} [-{cur_sl_dist:.2f} giá], TP: {tp_price:.2f}) — Gạt công tắc AUTO sang RUNNING để Bot tự vào lệnh.")
                else:
                    ctx["pending_pullback_order"] = None
                    if is_high_uncertainty:
                        trade_msg = f"⚠️ Chặn mở lệnh: Độ phân tán kịch bản AI cao ({dispersion_r:.2f}x ATR > {max_dispersion:.1f}x ATR) — Bất định cao, tránh bẫy giá"
                        self.log(f"⚠️ [{pk}] Độ phân tán kịch bản AI cao ({dispersion_r:.2f}x ATR > {max_dispersion:.1f}x ATR, độ lệch {scenario_std:.2f}$). Tự động chặn vào lệnh mới để tránh bẫy giá bất định.")
                    else:
                        self.log(f"[{pk} - {symbol}] Phân tích xong nến {last_time}: Chưa đủ điều kiện vào lệnh.")

                max_sl_str = f"Max: {effective_max_sl:.2f} giá" if effective_max_sl > 0.0 else "Theo AI"
                if sl_tightened_for_2r:
                    max_sl_str += " (RR<2R)"
                actual_rr = (dist_tp / cur_sl_dist) if cur_sl_dist > 0.001 else 0.0
                rr_str = f"1:{actual_rr:.2f}R" if actual_rr > 0 else "---"
                detail_prefix = (
                    f"Giá đóng nến: {last_close:.2f}  |  Dự báo {pred_len} nến: {avg_end_close:.2f} ({expected_move:+.2f}$)  |  Phân tán: {dispersion_r:.2f}x ATR ({scenario_std:.2f}$)  |  Tin cậy AI: {confidence_pct:.0f}%\n"
                    f"Gợi ý AI{ref_tag} ->  Entry: {entry_ref:.2f}   |   SL: {sl_price:.2f} (SL AI: {cur_sl_dist:.2f} giá [{max_sl_str}])   |   TP: {tp_price:.2f} (Cách {dist_tp:.2f} giá)   |   Tỷ lệ: {rr_str}"
                )
                ctx["_last_detail_prefix"] = detail_prefix
                detail_text = detail_prefix

                if pk == self.active_profile_key:
                    self._set_loading_stage(96.0, f"Đang dựng biểu đồ nến Real-Time ({symbol})...")
                chart_bars = min(max(lookback, 450), 500)
                df_live_scalp = self.fetch_mt5_candles(symbol, scalp_tf, chart_bars, include_forming=True)
                df_live_ana = df_live_scalp if analysis_tf == scalp_tf else self.fetch_mt5_candles(symbol, analysis_tf, chart_bars, include_forming=True)
                ctx["cached_df_hist"] = df_live_scalp
                ctx["cached_df_analysis"] = df_live_ana

                if pk == self.active_profile_key:
                    for attr in (
                        "last_recommendation", "pending_pullback_order", "cached_scenarios",
                        "cached_mean_df", "cached_action", "cached_sl", "cached_tp",
                        "cached_df_hist", "cached_df_analysis", "_last_detail_prefix",
                        "_sig_title", "_sig_color"
                    ):
                        setattr(self, attr, ctx[attr])

                self._ui_after(
                    0, self._update_ui_results, pk, df_live_scalp, df_live_ana,
                    scenarios, mean_df, sig_title, sig_color, detail_text,
                    action, sl_price, tp_price, symbol, scalp_tf, analysis_tf
                )
        except Exception as e:
            self.log_error(f"ANALYSIS_WORKER_{pk}", e)
            if self._is_initial_loading:
                self._ui_after(0, self._finish_startup_loading)
        finally:
            self.profile_ctx[pk]["is_analyzing"] = False
            if pk == self.active_profile_key:
                self.is_analyzing = False
                self._ui_after(0, self._finish_scan_progress_anim)

    def _update_ui_results(self, profile_key, df_live_scalp, df_live_ana, scenarios, mean_df, sig_title, sig_color, detail_text, action, sl_price, tp_price, symbol, scalp_tf, analysis_tf):
        # Chỉ cập nhật lên giao diện chính nếu kết quả thuộc về đúng Tab mà người dùng đang mở!
        if profile_key == self.active_profile_key:
            for attr in (
                "last_recommendation", "pending_pullback_order", "cached_scenarios",
                "cached_mean_df", "cached_action", "cached_sl", "cached_tp",
                "cached_df_hist", "cached_df_analysis", "_last_detail_prefix",
                "_sig_title", "_sig_color"
            ):
                setattr(self, attr, self.profile_ctx[profile_key].get(attr))
            self._update_forecast_tab_header()
            self.signal_title_lbl.config(text=sig_title, fg=sig_color)
            self.signal_detail_lbl.config(text=detail_text)
            self._update_bot_status_bar(profile_key)

            if getattr(self, "tree", None) is not None:
                for row in self.tree.get_children():
                    self.tree.delete(row)
                for _, r in mean_df.iterrows():
                    ts_str = r["timestamps"].strftime("%Y-%m-%d %H:%M")
                    self.tree.insert("", tk.END, values=(ts_str, f"{r['open']:.2f}", f"{r['high']:.2f}", f"{r['low']:.2f}", f"{r['close']:.2f}"))

            view_tf = getattr(self, "chart_view_tf_by_profile", {}).get(profile_key, scalp_tf)
            if view_tf == scalp_tf:
                chart_df = df_live_scalp
                chart_tf = scalp_tf
            elif view_tf == analysis_tf:
                chart_df = df_live_ana
                chart_tf = analysis_tf
            else:
                try:
                    chart_bars = min(max(int(self.lookback_var.get() or 450), 400), 500)
                except ValueError:
                    chart_bars = 450
                chart_df = self.fetch_mt5_candles(symbol, view_tf, chart_bars, include_forming=True)
                chart_tf = view_tf

            self._highlight_active_tf_btn(chart_tf)
            self._draw_candlestick_chart(
                chart_df,
                None,
                mean_df,
                action,
                sl_price,
                tp_price,
                symbol,
                chart_tf,
                has_forming_candle=True
            )

        if self._is_initial_loading:
            self._finish_startup_loading()

    # =========================================================================
    # VẼ BIỂU ĐỒ THEO 2 CHẾ ĐỘ TAB (400 NẾN LỊCH SỬ + CROSSHAIR TRADINGVIEW)
    # =========================================================================
    def _draw_candlestick_chart(self, df_hist, scenarios, mean_df, action, sl_price, tp_price, symbol, tf, has_forming_candle=True):
        self.ax.clear()
        self._style_axes()
        self._highlight_active_tf_btn(tf)
        self._pos_text_artists = []

        # Nạp tối đa 450 nến lịch sử phía sau để khi thu nhỏ (Zoom Out) hoặc kéo lùi không bị khoảng trống đen
        max_hist_bars = 450
        hist = df_hist.iloc[-max_hist_bars:].reset_index(drop=True)
        n_hist = len(hist)
        # Luôn tính và vẽ cụm nến dự báo AI khi có dữ liệu (tích hợp trực tiếp lên biểu đồ Real-Time chính)
        n_pred = len(mean_df) if (mean_df is not None and len(mean_df) > 0) else 0
        PRED_GAP = 4 if n_pred > 0 else 0
        total_x = n_hist + PRED_GAP + n_pred

        self._chart_hist_df = hist
        self._chart_mean_df = mean_df
        self._chart_n_hist = n_hist
        self._chart_total_x = total_x
        self._chart_pred_gap = PRED_GAP

        default_vis = 170
        if self.user_zoomed and self.saved_xlim is not None:
            view_xlim = self.saved_xlim
        else:
            view_xlim = (max(-1.5, n_hist - default_vis - 0.5), total_x + 4.0)

        # 1. Vẽ toàn bộ nến lịch sử bằng Vectorized Collection (gộp 399 nến đã đóng thành 2 Artist duy nhất giúp tăng tốc 30x)
        opens = hist["open"].to_numpy(dtype=float)
        highs = hist["high"].to_numpy(dtype=float)
        lows = hist["low"].to_numpy(dtype=float)
        closes = hist["close"].to_numpy(dtype=float)

        n_closed = (n_hist - 1) if (has_forming_candle and n_hist >= 1) else n_hist
        if n_closed > 0:
            xs_c = np.arange(n_closed, dtype=float)
            o_c = opens[:n_closed]
            h_c = highs[:n_closed]
            l_c = lows[:n_closed]
            c_c = closes[:n_closed]
            colors_c = [GREEN_UP if c_c[k] >= o_c[k] else RED_DOWN for k in range(n_closed)]

            wick_segs = [((xs_c[k], l_c[k]), (xs_c[k], h_c[k])) for k in range(n_closed)]
            wick_coll = LineCollection(wick_segs, colors=colors_c, linewidths=1.2, zorder=2)
            self.ax.add_collection(wick_coll)

            bottoms_c = np.minimum(o_c, c_c)
            heights_c = np.maximum(np.abs(c_c - o_c), 0.05)
            tops_c = bottoms_c + heights_c
            # Thân nến rộng 0.66 (±0.33) giúp khoảng cách giữa các cây nến gần lại 50% (từ 0.68 xuống 0.34)
            body_verts = [
                [
                    (xs_c[k] - 0.33, bottoms_c[k]),
                    (xs_c[k] + 0.33, bottoms_c[k]),
                    (xs_c[k] + 0.33, tops_c[k]),
                    (xs_c[k] - 0.33, tops_c[k]),
                ]
                for k in range(n_closed)
            ]
            body_coll = PolyCollection(body_verts, facecolors=colors_c, edgecolors=colors_c, linewidths=0.5, zorder=3)
            self.ax.add_collection(body_coll)

        # Vẽ riêng cây nến đang nhảy (Forming Candle) để có thể cập nhật tại chỗ mỗi 2 giây mà không cần vẽ lại biểu đồ
        self._forming_wick_line = None
        self._forming_body_rect = None
        if has_forming_candle and n_hist >= 1:
            i_f = n_hist - 1
            o_f, h_f, l_f, c_f = opens[i_f], highs[i_f], lows[i_f], closes[i_f]
            col_f = CYAN_LIVE if c_f >= o_f else "#ff4081"
            (self._forming_wick_line,) = self.ax.plot([i_f, i_f], [l_f, h_f], color=col_f, linewidth=1.4, zorder=4)
            bot_f = min(o_f, c_f)
            hgt_f = max(abs(c_f - o_f), 0.05)
            self._forming_body_rect = Rectangle((i_f - 0.33, bot_f), 0.66, hgt_f, facecolor=col_f, edgecolor=col_f, zorder=4)
            self.ax.add_patch(self._forming_body_rect)

        live_price = float(closes[-1])
        self._live_price_hline = self.ax.axhline(
            y=live_price, color=CYAN_LIVE, linestyle="-.", linewidth=0.9, alpha=0.85
        )

        last_candle_ts = hist["timestamps"].iloc[-1]
        cd_str = self._get_candle_countdown_str(last_candle_ts, tf, symbol=symbol)

        # Hiển thị Nhãn Mức Giá Hiện Tại trực tiếp trên Thanh Giá bên phải (như TradingView)
        self._live_price_annot = self.ax.annotate(
            f" {live_price:.2f} ",
            xy=(1.0, live_price),
            xycoords=("axes fraction", "data"),
            xytext=(2, 0),
            textcoords="offset points",
            color="#000000",
            fontsize=8.5,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="square,pad=0.22", facecolor=CYAN_LIVE, edgecolor="#00b8d4", lw=1),
            annotation_clip=False,
            zorder=20
        )

        # Đồng hồ đếm ngược thời gian đóng cây nến hiện tại trực tiếp dưới nhãn giá (chuẩn TradingView)
        self._candle_countdown_annot = self.ax.annotate(
            f" {cd_str} ",
            xy=(1.0, live_price),
            xycoords=("axes fraction", "data"),
            xytext=(2, -13),
            textcoords="offset points",
            color="#00e5ff",
            fontsize=8.0,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="square,pad=0.20", facecolor="#131722", edgecolor="#2a2e39", lw=1),
            annotation_clip=False,
            zorder=19
        )

        span_x = max(view_xlim[1] - view_xlim[0], 10.0)
        base_label_x = view_xlim[0] + span_x * 0.018
        col_step_x = span_x * 0.18

        # 2. Hiển thị nhãn Entry, SL, TP kèm cả Mức Giá & Số Lãi/Lỗ ($) của các lệnh đang mở
        open_positions = None
        if MT5_AVAILABLE:
            with self._mt5_lock:
                open_positions = mt5.positions_get(symbol=symbol)
            if open_positions:
                sel_tickets = self._get_selected_tickets_for_profile(self.active_profile_key)
                has_sel = len(sel_tickets) > 0
                eps_eq = self._get_symbol_tolerances(symbol)["eps_equal"]
                cfg_chart = self._get_profile_cfg(self.active_profile_key)
                def_be_p = 0.2
                try:
                    be_p_chart = max(float(cfg_chart.get("be_plus_price", def_be_p)), 0.0)
                except ValueError:
                    be_p_chart = def_be_p
                for idx_p, pos in enumerate(open_positions):
                    is_buy = (pos.type == mt5.POSITION_TYPE_BUY)
                    is_sel = (pos.ticket in sel_tickets)
                    be_target_c = (pos.price_open + be_p_chart) if is_buy else (pos.price_open - be_p_chart)
                    is_sl_at_entry = (pos.sl > 0 and abs(pos.sl - pos.price_open) < eps_eq)
                    is_tp_at_entry = (pos.tp > 0 and abs(pos.tp - pos.price_open) < eps_eq)
                    is_sl_at_be_plus = (pos.sl > 0 and abs(pos.sl - be_target_c) < eps_eq)
                    is_tp_at_be_plus = (pos.tp > 0 and abs(pos.tp - be_target_c) < eps_eq)
                    be_tag = (
                        f" [SL=Entry+{be_p_chart:g}]" if (is_sl_at_be_plus and be_p_chart > 0)
                        else (" [SL=0]" if is_sl_at_entry else (" [Chờ Entry+]" if (is_tp_at_be_plus or is_tp_at_entry) else ""))
                    )
                    offset_x = base_label_x + (idx_p * col_step_x)

                    # Tính toán trước số Lãi/Lỗ ($) dự kiến khi chạm SL hoặc TP
                    sl_pnl = self._calc_target_pnl(pos, pos.sl) if pos.sl > 0 else 0.0
                    tp_pnl = self._calc_target_pnl(pos, pos.tp) if pos.tp > 0 else 0.0
                    is_profit_sl = (sl_pnl >= 0)

                    # Tùy biến màu sắc, độ dày nét và nhãn khi người dùng chọn lệnh trong danh sách
                    if is_sel:
                        # 1. LỆNH ĐƯỢC CHỌN TRONG BẢNG: NỔI BẬT ĐẶC BIỆT (Cyan rực rỡ, nét dày, icon & số ticket)
                        entry_col = "#00e5ff"  # Neon Cyan
                        entry_w = 2.4
                        entry_ls = "-"
                        entry_alpha = 1.0
                        entry_bg = "#00e5ff"
                        entry_fg = "#000000"
                        entry_txt = f" 👉 [LỆNH #{pos.ticket}] Entry: {pos.price_open:.2f} ({pos.profit:+.1f}$){be_tag} "
                        entry_fs = 8.5
                        z_line, z_badge = 28, 32

                        sl_col = "#ffea00" if is_profit_sl else "#ff1744"  # Neon Amber nếu khóa lãi, Neon Red nếu cắt lỗ
                        sl_w = 2.2
                        sl_ls = "--"
                        sl_alpha = 1.0
                        sl_bg = "#ffea00" if is_profit_sl else "#ff1744"
                        sl_fg = "#000000" if is_profit_sl else "#ffffff"
                        sl_txt = f" 👉 [#{pos.ticket}] SL: {pos.sl:.2f} ({sl_pnl:+.1f}$) "
                        sl_fs = 8.0

                        tp_col = "#00e676"  # Neon Green
                        tp_w = 2.2
                        tp_ls = "--"
                        tp_alpha = 1.0
                        tp_bg = "#00e676"
                        tp_fg = "#000000"
                        tp_txt = f" 👉 [#{pos.ticket}] TP: {pos.tp:.2f} ({tp_pnl:+.1f}$) "
                        tp_fs = 8.0
                    elif has_sel:
                        # 2. CÁC LỆNH KHÁC KHI ĐANG CÓ LỆNH ĐƯỢC CHỌN: Làm mờ nhẹ để mắt người dùng tập trung vào lệnh chọn
                        entry_col = "#00e676" if is_buy else "#ff5252"
                        entry_w = 0.9
                        entry_ls = ":"
                        entry_alpha = 0.40
                        entry_bg = "#0f3923" if is_buy else "#451a1a"
                        entry_fg = "#7ec89e" if is_buy else "#c87e7e"
                        entry_txt = f" Entry: {pos.price_open:.2f} ({pos.profit:+.1f}$){be_tag} "
                        entry_fs = 7.0
                        z_line, z_badge = 10, 12

                        sl_col = "#cca000" if is_profit_sl else "#8a2a35"
                        sl_w = 0.8
                        sl_ls = ":"
                        sl_alpha = 0.35
                        sl_bg = "#3a2c10" if is_profit_sl else "#301317"
                        sl_fg = "#b39860" if is_profit_sl else "#9c6068"
                        sl_txt = f" SL: {pos.sl:.2f} ({sl_pnl:+.1f}$) "
                        sl_fs = 7.0

                        tp_col = "#2a6644"
                        tp_w = 0.8
                        tp_ls = ":"
                        tp_alpha = 0.35
                        tp_bg = "#112e1e"
                        tp_fg = "#699e7d"
                        tp_txt = f" TP: {pos.tp:.2f} ({tp_pnl:+.1f}$) "
                        tp_fs = 7.0
                    else:
                        # 3. KHÔNG CÓ LỆNH NÀO ĐƯỢC CHỌN: Hiển thị giao diện tiêu chuẩn cân bằng
                        entry_col = "#00e676" if is_buy else "#ff5252"
                        entry_w = 1.2
                        entry_ls = "--"
                        entry_alpha = 0.90
                        entry_bg = "#00e676" if is_buy else "#ff5252"
                        entry_fg = "#000000" if is_buy else "#ffffff"
                        entry_txt = f" Entry: {pos.price_open:.2f} ({pos.profit:+.1f}$){be_tag} "
                        entry_fs = 7.5
                        z_line, z_badge = 15, 18

                        sl_col = GOLD_ACCENT if is_profit_sl else "#ff5252"
                        sl_w = 1.1
                        sl_ls = ":"
                        sl_alpha = 0.85
                        sl_bg = GOLD_ACCENT if is_profit_sl else "#6e1b26"
                        sl_fg = "#000000" if is_profit_sl else "#ffffff"
                        sl_txt = f" SL: {pos.sl:.2f} ({sl_pnl:+.1f}$) "
                        sl_fs = 7.5

                        tp_col = "#69f0ae"
                        tp_w = 1.1
                        tp_ls = ":"
                        tp_alpha = 0.85
                        tp_bg = "#69f0ae"
                        tp_fg = "#000000"
                        tp_txt = f" TP: {pos.tp:.2f} ({tp_pnl:+.1f}$) "
                        tp_fs = 7.5

                    # Đường Entry kèm mức giá & số lãi/lỗ hiện tại
                    self.ax.axhline(y=pos.price_open, color=entry_col, linestyle=entry_ls, linewidth=entry_w, alpha=entry_alpha, zorder=z_line)
                    t_ent = self.ax.text(
                        offset_x, pos.price_open, entry_txt,
                        color=entry_fg, backgroundcolor=entry_bg,
                        fontsize=entry_fs, fontweight="bold", va="center", clip_on=True, zorder=z_badge
                    )
                    self._pos_text_artists.append((t_ent, idx_p))

                    # Đường SL kèm mức giá SL & số lỗ/lãi dự kiến
                    if pos.sl > 0 and not is_sl_at_entry:
                        self.ax.axhline(y=pos.sl, color=sl_col, linestyle=sl_ls, linewidth=sl_w, alpha=sl_alpha, zorder=z_line)
                        t_sl = self.ax.text(
                            offset_x, pos.sl, sl_txt,
                            color=sl_fg, backgroundcolor=sl_bg,
                            fontsize=sl_fs, fontweight="bold", va="center", clip_on=True, zorder=z_badge
                        )
                        self._pos_text_artists.append((t_sl, idx_p))

                    # Đường TP kèm mức giá TP & số lãi dự kiến
                    if pos.tp > 0 and not is_tp_at_entry:
                        self.ax.axhline(y=pos.tp, color=tp_col, linestyle=tp_ls, linewidth=tp_w, alpha=tp_alpha, zorder=z_line)
                        t_tp = self.ax.text(
                            offset_x, pos.tp, tp_txt,
                            color=tp_fg, backgroundcolor=tp_bg,
                            fontsize=tp_fs, fontweight="bold", va="center", clip_on=True, zorder=z_badge
                        )
                        self._pos_text_artists.append((t_tp, idx_p))

        # Vẽ đường Chờ Giá Hồi về <= SL Tối Đa (nếu đang có tín hiệu chờ hồi)
        if self.pending_pullback_order is not None:
            p = self.pending_pullback_order
            try:
                max_sl_p = float(p.get("effective_max_sl") or self.max_sl_price_var.get())
            except (ValueError, TypeError):
                max_sl_p = 5.0
            lim_entry = (p["sl_price"] + max_sl_p) if p["action"] == "BUY" else (p["sl_price"] - max_sl_p)
            self.ax.axhline(y=lim_entry, color=GOLD_ACCENT, linestyle="--", linewidth=1.2, alpha=0.9)
            tight_tag = " (RR≥2R)" if p.get("sl_tightened_for_2r") else ""
            t_wait = self.ax.text(
                base_label_x, lim_entry,
                f" ⏳ Chờ {p['action']}{tight_tag} <= {max_sl_p:.2f} giá (@ {lim_entry:.2f}) ",
                color="#000000", backgroundcolor=GOLD_ACCENT,
                fontsize=7.5, fontweight="bold", va="center", clip_on=True
            )
            self._pos_text_artists.append((t_wait, 0))

        # 3. Cụm Dự báo Kronos AI (Tích hợp trực tiếp lên biểu đồ chính, cách xa nến đang chạy một khoảng PRED_GAP và loại bỏ đường tơ nhện)
        all_times = list(hist["timestamps"].dt.strftime("%H:%M"))
        if mean_df is not None and len(mean_df) > 0:
            pred_start_x = n_hist + PRED_GAP
            for j in range(n_pred):
                idx_x = pred_start_x + j
                o, h, l, c = mean_df.loc[j, ["open", "high", "low", "close"]]
                color = "#ffd54f" if c >= o else "#ff8a65"
                self.ax.plot([idx_x, idx_x], [l, h], color=color, linewidth=1.5, zorder=5)
                bottom = min(o, c)
                height = max(abs(c - o), 0.05)
                self.ax.add_patch(
                    Rectangle(
                        (idx_x - 0.33, bottom), 0.66, height,
                        facecolor=color, edgecolor=color, zorder=6,
                        label=""
                    )
                )

            # Phân tách trực quan giữa nến Live và cụm nến Dự Báo
            self.ax.axvline(x=n_hist - 0.5, color="#f0b90b", linestyle="--", linewidth=1.0, alpha=0.6)

            all_times += [""] * PRED_GAP
            all_times += list(mean_df["timestamps"].dt.strftime("%H:%M"))

            if sl_price is not None and tp_price is not None:
                try:
                    lot_val = float(self.lot_var.get())
                except ValueError:
                    lot_val = 0.01
                with self._mt5_lock:
                    sym_info = mt5.symbol_info(symbol) if MT5_AVAILABLE else None
                contract_sz = float(sym_info.trade_contract_size) if (sym_info and sym_info.trade_contract_size > 0) else 100.0
                est_sl_usd = -abs(live_price - sl_price) * lot_val * contract_sz
                est_tp_usd = abs(tp_price - live_price) * lot_val * contract_sz
                self.ax.axhline(y=sl_price, color=RED_DOWN, linestyle=":", linewidth=1.3, label=f"SL: {sl_price:.2f} ({est_sl_usd:+.1f}$)")
                self.ax.axhline(y=tp_price, color=GREEN_UP, linestyle=":", linewidth=1.3, label=f"TP: {tp_price:.2f} ({est_tp_usd:+.1f}$)")

        self._chart_all_times = all_times

        # 4. Khởi tạo bộ Crosshair nét đứt + Nhãn giá tối bên phải kiểu TradingView (Ảnh 1)
        self.cross_vline = self.ax.axvline(x=n_hist - 1, color="#8b98a9", linestyle="--", linewidth=0.85, alpha=0.85, visible=False, zorder=20)
        self.cross_hline = self.ax.axhline(y=live_price, color="#8b98a9", linestyle="--", linewidth=0.85, alpha=0.85, visible=False, zorder=20)
        self.cross_price_annot = self.ax.annotate(
            "",
            xy=(1.0, live_price),
            xycoords=("axes fraction", "data"),
            xytext=(2, 0),
            textcoords="offset points",
            color="#ffffff",
            fontsize=8.5,
            fontweight="bold",
            va="center",
            ha="left",
            bbox=dict(boxstyle="square,pad=0.24", facecolor="#131722", edgecolor="#4c525e", lw=1),
            annotation_clip=False,
            visible=False,
            zorder=25
        )
        self.cross_time_annot = self.ax.annotate(
            "",
            xy=(n_hist - 1, 0.0),
            xycoords=("data", "axes fraction"),
            xytext=(0, -14),
            textcoords="offset points",
            color="#ffffff",
            fontsize=8,
            fontweight="bold",
            va="center",
            ha="center",
            bbox=dict(boxstyle="square,pad=0.22", facecolor="#131722", edgecolor="#4c525e", lw=1),
            annotation_clip=False,
            visible=False,
            zorder=25
        )

        mode_label = "BIỂU ĐỒ REAL-TIME"
        zoom_hint = " [Nháy đúp để Reset]" if self.user_zoomed else ""
        self.ax.set_title(
            f"[{mode_label}] {symbol} ({tf}) — Giá Live: {live_price:.2f} ({cd_str}){zoom_hint}",
            color=GOLD_ACCENT, fontsize=9, fontweight="bold"
        )
        handles, labels = self.ax.get_legend_handles_labels()
        if handles and any(bool(lbl and not lbl.startswith("_")) for lbl in labels):
            self.ax.legend(loc="upper left", facecolor=PANEL_BG, edgecolor="#2b3548", labelcolor=TEXT_MAIN, fontsize=8, framealpha=0.85)

        if self.user_zoomed and self.saved_xlim is not None and self.saved_ylim is not None:
            self.ax.set_xlim(self.saved_xlim)
            self.ax.set_ylim(self.saved_ylim)
            self._sync_xlim_dependents(self.saved_xlim)
        else:
            self.ax.set_xlim(view_xlim)
            auto_ylim = self._compute_visible_ylim(view_xlim[0], view_xlim[1])
            if auto_ylim is not None:
                self.ax.set_ylim(auto_ylim)
            self._sync_xlim_dependents(view_xlim)

        if self._last_hover_xy is not None:
            self._apply_crosshair_xy(self._last_hover_xy[0], self._last_hover_xy[1])

        sel_sig = tuple(sorted(self._get_selected_tickets_for_profile(self.active_profile_key)))
        pos_sig = (
            (tuple((p.ticket, round(p.sl, 2), round(p.tp, 2)) for p in open_positions) if open_positions else ()),
            sel_sig
        )
        pb_sig = (
            (self.pending_pullback_order["action"], round(self.pending_pullback_order["sl_price"], 2))
            if self.pending_pullback_order is not None else None
        )
        self._last_drawn_chart_key = (symbol, tf, self.chart_mode, hist["timestamps"].iloc[-1])
        self._last_drawn_pos_sig = (pos_sig, pb_sig)

        self.fig.subplots_adjust(left=0.045, right=0.945, top=0.93, bottom=0.07)
        self._request_canvas_redraw()


def run_first_time_setup():
    """Thiết lập thông số mặc định và tự động tạo Shortcut chạy phần mềm trong thư mục & ngoài Desktop."""
    import subprocess
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    default_settings = dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"])
    default_settings["profiles"] = {
        "XAUUSD": dict(DEFAULT_PROFILE_SETTINGS["XAUUSD"]),
    }
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, ensure_ascii=False, indent=2)

    # Nếu chưa có Icon trong AppData, tự động tạo Icon tròn vàng/đen sắc nét
    if not os.path.exists(ICON_ICO_FILE) or not os.path.exists(ICON_PNG_FILE):
        try:
            import base64, io
            from PIL import Image
            img = Image.open(io.BytesIO(base64.b64decode("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"))).resize((256, 256), Image.Resampling.LANCZOS)
            img.save(ICON_PNG_FILE, format="PNG")
            img.save(ICON_ICO_FILE, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
        except Exception:
            pass

    work_dir = os.path.dirname(os.path.abspath(__file__))
    weights_root = os.path.join(work_dir, "model", "weights")
    os.makedirs(weights_root, exist_ok=True)
    for repo_id, cls in [
        ("NeoQuasar/Kronos-Tokenizer-base", KronosTokenizer),
        ("NeoQuasar/Kronos-Tokenizer-2k", KronosTokenizer),
        ("NeoQuasar/Kronos-base", Kronos),
        ("NeoQuasar/Kronos-small", Kronos),
        ("NeoQuasar/Kronos-mini", Kronos),
    ]:
        w_name = repo_id.split("/")[-1]
        w_dir = os.path.join(weights_root, w_name)
        if not os.path.exists(os.path.join(w_dir, "model.safetensors")):
            try:
                obj = cls.from_pretrained(repo_id)
                obj.save_pretrained(w_dir)
            except Exception:
                pass

    bot_py = os.path.abspath(__file__)
    py_dir = os.path.dirname(sys.executable)
    pyw_exe = os.path.join(py_dir, "pythonw.exe")
    if not os.path.exists(pyw_exe):
        pyw_exe = sys.executable

    shortcut_basename = f"AI Scalping M5 ({INSTANCE_ID})" if INSTANCE_ID else "AI Scalping M5"
    ps_cmd = (
        "$WshShell = New-Object -ComObject WScript.Shell; "
        f"$workDir = '{work_dir}'; "
        f"$botPy = '{bot_py}'; "
        f"$pywExe = '{pyw_exe}'; "
        f"$icoPath = '{ICON_ICO_FILE}'; "
        f"$sName = '{shortcut_basename}.lnk'; "
        "$deskDir = [Environment]::GetFolderPath('Desktop'); "
        "foreach ($lnk in @((Join-Path $workDir $sName), (Join-Path $deskDir $sName))) { "
        "$s = $WshShell.CreateShortcut($lnk); "
        "$s.TargetPath = $pywExe; "
        "$s.Arguments = '-B \"' + $botPy + '\"'; "
        "$s.WorkingDirectory = $workDir; "
        "if (Test-Path $icoPath) { $s.IconLocation = $icoPath + ',0' }; "
        f"$s.Description = 'AI (KhaTran) - Phần Mềm Scalping Vàng & Quản Lý Lệnh Real-Time ({shortcut_basename})'; "
        "$s.Save() }"
    )
    try:
        CREATE_NO_WINDOW = 0x08000000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            check=False, creationflags=CREATE_NO_WINDOW, startupinfo=si, stdin=subprocess.DEVNULL
        )
    except Exception:
        pass


def _find_other_running_bot_pids() -> list:
    """Tìm toàn bộ các tiến trình python/pythonw khác đang chạy mt5_scalping_bot.py thuộc thư mục hiện tại trên máy."""
    import subprocess
    my_pid = os.getpid()
    found_pids = set()
    my_script = os.path.abspath(__file__).lower()
    my_dir = os.path.dirname(my_script).lower()

    # 1. Kiểm tra PID lưu trong INSTANCE_LOCK_FILE trước (nhanh nhất, < 1ms)
    try:
        if os.path.exists(INSTANCE_LOCK_FILE):
            with open(INSTANCE_LOCK_FILE, "r", encoding="utf-8") as f:
                lock_data = json.load(f)
            locked_pid = int(lock_data.get("pid", 0))
            if locked_pid > 0 and locked_pid != my_pid:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h_proc = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, locked_pid)
                if h_proc:
                    exit_code = ctypes.c_ulong(0)
                    if ctypes.windll.kernel32.GetExitCodeProcess(h_proc, ctypes.byref(exit_code)) and exit_code.value == 259:
                        found_pids.add(locked_pid)
                    ctypes.windll.kernel32.CloseHandle(h_proc)
    except Exception:
        pass

    # 2. Quét danh sách tiến trình Windows bằng PowerShell CIM (ẩn cửa sổ hoàn toàn) để phát hiện mọi tiến trình chạy ngầm CỦA ĐÚNG BẢN NÀY
    try:
        CREATE_NO_WINDOW = 0x08000000
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        ps_query = (
            "Get-CimInstance Win32_Process | "
            "Where-Object { ($_.Name -match '^pythonw?\\.exe$') -and ($_.CommandLine -like '*mt5_scalping_bot*' -or $_.CommandLine -like '*launcher*') } | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"
        )
        res = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_query],
            capture_output=True, text=True, timeout=4, creationflags=CREATE_NO_WINDOW,
            startupinfo=si, stdin=subprocess.DEVNULL
        )
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if "|" in line:
                parts = line.split("|", 1)
                if parts[0].isdigit():
                    pid_val = int(parts[0])
                    cmd_val = parts[1].lower()
                    # Chỉ coi là trùng tiến trình nếu chạy đúng từ thư mục này (my_dir), không can thiệp phiên bản khác
                    if pid_val > 0 and pid_val != my_pid and my_dir in cmd_val:
                        found_pids.add(pid_val)
    except Exception:
        pass

    return sorted(found_pids)


def _terminate_pids(pids: list):
    """Đóng dứt khoát danh sách các tiến trình App cũ đang chạy ngầm."""
    import subprocess
    PROCESS_TERMINATE = 0x0001
    for pid in pids:
        killed = False
        try:
            h_proc = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
            if h_proc:
                ctypes.windll.kernel32.TerminateProcess(h_proc, 0)
                ctypes.windll.kernel32.CloseHandle(h_proc)
                killed = True
        except Exception:
            pass
        if not killed:
            try:
                CREATE_NO_WINDOW = 0x08000000
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=3, creationflags=CREATE_NO_WINDOW,
                    startupinfo=si, stdin=subprocess.DEVNULL
                )
            except Exception:
                pass


def ensure_single_app_instance():
    """
    Kiểm tra khi khởi động xem có phiên bản App nào thuộc thư mục này đang mở sẵn hoặc bị treo chạy ngầm không:
    - Nếu phát hiện tiến trình cũ thuộc bản này: Hiển thị hộp thoại cho phép người dùng ĐÓNG tiến trình ngầm cũ để mở phiên mới sạch sẽ,
      hoặc chuyển sang cửa sổ đang mở sẵn (tuyệt đối không để 2 App cùng thư mục chạy song song gây xung đột MT5 / vào đúp lệnh).
    """
    global _SINGLETON_MUTEX_HANDLE
    mutex_name = f"Local\\KhaTran_Kronos_MT5_Scalping_Bot_Singleton_{INSTANCE_ID}" if INSTANCE_ID else "Local\\KhaTran_Kronos_MT5_Scalping_Bot_Singleton_v2"
    already_exists = False
    try:
        _SINGLETON_MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            already_exists = True
    except Exception:
        pass

    other_pids = _find_other_running_bot_pids()
    if not already_exists and not other_pids:
        return

    pids_label = ", ".join(str(p) for p in other_pids) if other_pids else "Đang hoạt động"
    inst_label = f"[{INSTANCE_ID}] " if INSTANCE_ID else ""
    tmp_root = tk.Tk()
    tmp_root.withdraw()
    try:
        tmp_root.attributes("-topmost", True)
    except Exception:
        pass

    choice_kill_old = messagebox.askyesno(
        f"⚠️ Phát Hiện App {inst_label}Đang Chạy Ngầm / Mở Trùng",
        f"Phát hiện phần mềm AI Scalping Bot {inst_label}(PID: {pids_label}) đang chạy sẵn hoặc đang chạy ngầm trên máy!\n"
        f"Nếu để 2 App cùng bản chạy cùng lúc sẽ gây xung đột kết nối MT5 và vào đúp lệnh.\n\n"
        f"• Bấm [YES / Có]: Tự động ĐÓNG toàn bộ tiến trình cũ của bản này đang chạy ngầm và khởi động phiên mới này.\n"
        f"• Bấm [NO / Không]: Giữ nguyên App đang chạy (không mở thêm cửa sổ mới).",
        parent=tmp_root
    )
    tmp_root.destroy()

    if choice_kill_old:
        if other_pids:
            _terminate_pids(other_pids)
            time.sleep(0.45)
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            ts_ms = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            daily_path = os.path.join(LOG_DIR, f"bot_activity_{datetime.now().strftime('%Y-%m-%d')}.log")
            with open(daily_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts_ms}] 🛡️ [CHỐNG XUNG ĐỘT NGẦM] Đã tự động đóng tiến trình App cũ (PID: {pids_label}) để khởi động phiên mới sạch sẽ.\n")
        except Exception:
            pass
    else:
        # Đưa cửa sổ App đang chạy sẵn lên tiền cảnh nếu có lưu HWND
        try:
            if os.path.exists(INSTANCE_LOCK_FILE):
                with open(INSTANCE_LOCK_FILE, "r", encoding="utf-8") as f:
                    lock_data = json.load(f)
                hwnd = int(lock_data.get("hwnd", 0))
                if hwnd > 0:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass
        sys.exit(0)


if __name__ == "__main__":
    if "--setup" in sys.argv:
        run_first_time_setup()
        sys.exit(0)
    ensure_single_app_instance()

    from kronos_license import LicenseManager, show_activation_dialog
    lic_mgr = LicenseManager()
    lic_status = lic_mgr.check_status()

    root = tk.Tk()
    if not lic_status.get("is_valid", False):
        root.withdraw()
        # Chặn mở phần mềm khi chưa kích hoạt hoặc hết hạn! Hiển thị ngay cửa sổ nhập key
        activated = show_activation_dialog(root, lic_mgr, is_startup_block=True)
        if not activated:
            sys.exit(0)
        lic_status = lic_mgr.check_status(force_refresh=True)
        if not lic_status.get("is_valid", False):
            sys.exit(0)
        root.deiconify()

    app = KronosDesktopApp(root, lic_mgr=lic_mgr)
    root.mainloop()
