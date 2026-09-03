from __future__ import annotations

import argparse
import calendar
import ctypes
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, messagebox, simpledialog, ttk

from audiodigest import __version__
from audiodigest.config import Settings, load_settings
from audiodigest.cost_guard import CostSafetyError, write_spark_confirmation
from audiodigest.database import StateDatabase
from audiodigest.gmail_client import (
    GmailClient,
    GmailConfigurationError,
    GmailTokenStore,
)
from audiodigest.models import EpisodeScript
from audiodigest.player import PlaybackError, PreparedPlayback, WindowsAudioPlayer
from audiodigest.preferences import (
    DIALOGUE_STYLE_BY_ID,
    DIALOGUE_STYLE_LABELS,
    EDITORIAL_TONES,
    PERSONALITY_CHOICES,
    PUBLISHING_MODE_BY_ID,
    PUBLISHING_MODE_LABELS,
    PreferenceValidationError,
    controls_for_voice_id,
    save_preferences,
    validate_gmail_label,
    voice_id_for_controls,
)
from audiodigest.publishing_setup import (
    configure_private_publishing,
    enable_private_publishing,
)
from audiodigest.runtime_environment import local_tool_environment

PALETTE = {
    "background": "#110B07",
    "panel": "#21130C",
    "panel_alt": "#2D190E",
    "ink": "#F4E2B8",
    "cream": "#FFF0C7",
    "muted": "#B99160",
    "amber": "#F28C18",
    "bright": "#FFB43B",
    "rust": "#9F3E10",
    "line": "#70401F",
    "black": "#090603",
    "good": "#E4A23D",
    "error": "#E35D35",
}

HOST_COUNT_LABELS = {
    "1 host - choose presenter": 1,
    "2 hosts - Dalia + Nox": 2,
}
HOST_COUNT_BY_VALUE = {value: label for label, value in HOST_COUNT_LABELS.items()}
TONE_LABELS = {item.display_name: item.tone_id for item in EDITORIAL_TONES}
TONE_LABEL_BY_ID = {item.tone_id: item.display_name for item in EDITORIAL_TONES}
STAGE_PATTERN = re.compile(r"^Stage\s+(\d+)/(\d+):\s*(.+)$")
WINDOW_TITLE = "The Daily Nexus"
WINDOWS_APP_USER_MODEL_ID = "DarioNovelli.TheDailyNexus.Desktop"
HEADER_SUBTITLE = "NEXUS CONSOLE 06 // PRIVATE MORNING INTELLIGENCE"


def previous_local_day() -> str:
    return (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()


def _set_windows_app_identity() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except (AttributeError, OSError):
        return


def sanitized_environment(
    source: dict[str, str],
    project_dir: Path,
) -> dict[str, str]:
    runtime_dir = Path(source.get("LOCALAPPDATA", str(project_dir))) / "AudioDigest"
    return local_tool_environment(source, runtime_dir)


def build_run_command(
    python: str,
    config_path: Path,
    episode_date: str,
    *,
    local_only: bool,
) -> list[str]:
    command = [
        python,
        "-m",
        "audiodigest",
        "--config",
        str(config_path),
        "run",
        "--date",
        episode_date,
    ]
    if local_only:
        command.append("--dry-run")
    return command


def build_publish_command(
    python: str,
    config_path: Path,
    episode_date: str,
) -> list[str]:
    return [
        python,
        "-m",
        "audiodigest",
        "--config",
        str(config_path),
        "publish",
        "--date",
        episode_date,
    ]


def _clock_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _date_with_weekday(value: str) -> str:
    try:
        weekday = datetime.strptime(value, "%Y-%m-%d").strftime("%A").upper()
    except ValueError:
        return value
    return f"{value} // {weekday}"


def _episode_date_title(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value.upper()
    return (
        f"{parsed.strftime('%A, %B').upper()} {parsed.day}, {parsed.year}"
    )


def _player_surface_height(viewport_height: int, requested_height: int) -> int:
    return max(1, viewport_height, requested_height)


def _level_fraction(value: float, minimum: float, maximum: float) -> float:
    if maximum <= minimum:
        return 0.0
    return max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


class NexusLevelBar(tk.Canvas):
    def __init__(
        self,
        parent: tk.Widget,
        *,
        variable: tk.DoubleVar,
        from_: float,
        to: float,
        length: int,
        resolution: float,
        command,
    ) -> None:
        super().__init__(
            parent,
            width=length,
            height=18,
            background=PALETTE["panel"],
            highlightbackground=PALETTE["line"],
            highlightcolor=PALETTE["amber"],
            highlightthickness=1,
            borderwidth=0,
            relief="flat",
            cursor="hand2",
            takefocus=True,
        )
        self.variable = variable
        self.minimum = float(from_)
        self.maximum = float(to)
        self.resolution = max(float(resolution), 0.0001)
        self.command = command
        self.bind("<Configure>", self._draw_level, add="+")
        self.bind("<ButtonPress-1>", self._set_from_pointer, add="+")
        self.bind("<B1-Motion>", self._set_from_pointer, add="+")
        self.bind("<Left>", lambda _event: self._nudge(-self.resolution))
        self.bind("<Right>", lambda _event: self._nudge(self.resolution))
        self.variable.trace_add("write", self._variable_changed)
        self.after_idle(self._draw_level)

    def _variable_changed(self, *_args) -> None:
        self._draw_level()

    def _value(self) -> float:
        try:
            return float(self.variable.get())
        except (tk.TclError, ValueError):
            return self.minimum

    def _draw_level(self, _event=None) -> None:
        if not self.winfo_exists():
            return
        self.delete("level")
        width = max(14, self.winfo_width())
        height = max(10, self.winfo_height())
        left = 7
        right = max(left + 1, width - 7)
        center = height / 2
        fraction = _level_fraction(
            self._value(),
            self.minimum,
            self.maximum,
        )
        selected_x = left + ((right - left) * fraction)
        disabled = str(self.cget("state")) == "disabled"
        fill_color = PALETTE["muted"] if disabled else PALETTE["amber"]
        thumb_color = PALETTE["line"] if disabled else PALETTE["bright"]
        self.create_line(
            left,
            center,
            right,
            center,
            fill=PALETTE["black"],
            width=7,
            capstyle="round",
            tags="level",
        )
        self.create_line(
            left,
            center,
            right,
            center,
            fill=PALETTE["line"],
            width=2,
            capstyle="round",
            tags="level",
        )
        if selected_x > left:
            self.create_line(
                left,
                center,
                selected_x,
                center,
                fill=fill_color,
                width=6,
                capstyle="round",
                tags="level",
            )
        radius = 4
        self.create_oval(
            selected_x - radius,
            center - radius,
            selected_x + radius,
            center + radius,
            fill=thumb_color,
            outline=PALETTE["cream"] if not disabled else PALETTE["line"],
            width=1,
            tags="level",
        )

    def _set_value(self, raw_value: float) -> None:
        if str(self.cget("state")) == "disabled":
            return
        steps = round((raw_value - self.minimum) / self.resolution)
        value = self.minimum + (steps * self.resolution)
        value = max(self.minimum, min(self.maximum, value))
        self.variable.set(value)
        self._draw_level()
        if self.command is not None:
            self.command(str(value))

    def _set_from_pointer(self, event) -> str:
        self.focus_set()
        width = max(14, self.winfo_width())
        fraction = max(0.0, min(1.0, (event.x - 7) / max(1, width - 14)))
        self._set_value(
            self.minimum + ((self.maximum - self.minimum) * fraction)
        )
        return "break"

    def _nudge(self, amount: float) -> str:
        self._set_value(self._value() + amount)
        return "break"


class DailyNexusApp:
    def __init__(self, root: Tk, project_dir: Path) -> None:
        self.root = root
        self.project_dir = project_dir.resolve()
        self.config_path = self.project_dir / "config.toml"
        self.runtime_dir = Path(os.getenv("LOCALAPPDATA", self.project_dir)) / "AudioDigest"
        self.command_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.identity_worker: threading.Thread | None = None
        self.preference_worker: threading.Thread | None = None
        self.current_settings: Settings | None = None
        self.database: StateDatabase | None = None
        self.action_started_at: datetime | None = None
        self.estimated_seconds = 1500
        self.current_stage = 0
        self.total_stages = 8
        self.player = WindowsAudioPlayer()
        self.playback_worker: threading.Thread | None = None
        self.playback_request_id = 0
        self.episode_records: list[dict] = []
        self.read_records: list[dict] = []
        self.playback_paused = False
        self.playback_seeking = False
        self.playback_is_playing = False
        self.playback_active = False
        self.playback_wave_phase = 0
        self.playback_wave_items: list[int] = []
        self.mini_wave_items: list[int] = []
        self.playback_transcript_segments: list[dict[str, object]] = []
        self.playback_transcript_index = -1
        self.pending_playback_seek_ms: int | None = None
        self.play_content_buttons: dict[str, tk.Button] = {}
        self.active_run_date = ""
        self.read_preview_paths: list[Path] = []
        self.read_page_index = 0
        self.read_zoom = 1.0

        self.episode_date = StringVar(value=previous_local_day())
        self.local_only = BooleanVar(value=True)
        self.status = StringVar(value="READY // SELECT A DATE")
        self.runtime_state = StringVar(value="IDLE")
        self.elapsed_status = StringVar(value="ELAPSED 00:00:00")
        self.estimate_status = StringVar(value="ESTIMATE --:--:--")
        self.configuration = StringVar(value="Checking local systems...")
        self.gmail_account = StringVar(value="Not signed in")
        self.gmail_status = StringVar(value="Checking Gmail authorization...")
        self.gmail_label = StringVar(value="AudioDigest/Source")
        self.host_count_choice = StringVar(value=HOST_COUNT_BY_VALUE[1])
        self.solo_host_choice = StringVar(value="Dalia")
        self.dialogue_style_choice = StringVar(
            value=DIALOGUE_STYLE_BY_ID["broadcast"]
        )
        self.dalia_personality_choice = StringVar(value="Warm and engaging")
        self.dalia_tone_choice = StringVar(value=TONE_LABEL_BY_ID["warm"])
        self.nox_personality_choice = StringVar(value="Warm and engaging")
        self.nox_tone_choice = StringVar(value=TONE_LABEL_BY_ID["dry_wit"])
        self.publishing_mode_choice = StringVar(value=PUBLISHING_MODE_BY_ID["manual"])
        self.playback_speed_choice = StringVar(value="1.0x")
        self.playback_speed_value = tk.DoubleVar(value=100)
        self.playback_speed_status = StringVar(value="SPEED 1.00x")
        self.playback_volume = tk.DoubleVar(value=80)
        self.playback_volume_status = StringVar(value="VOLUME 80%")
        self.preference_status = StringVar(value="Preferences remain in this local project.")
        self.publishing_status = StringVar(value="Publishing is not configured.")
        self.play_title = StringVar(value="Select an episode")
        self.play_detail = StringVar(value="Your local archive appears here.")
        self.play_time = StringVar(value="00:00 / 00:00")
        self.mini_play_title = StringVar(value="")
        self.mini_play_time = StringVar(value="00:00 / 00:00")
        self.play_content_mode = StringVar(value="references")
        self.read_title = StringVar(value="Select a two- or three-page edition")
        self.read_page_status = StringVar(value="PAGE -- / --")
        self.read_zoom_status = StringVar(value="ZOOM 100%")
        self.mode = "gen"

        self._configure_window()
        self._build_shell()
        self._refresh_configuration()
        self._refresh_library()
        self.root.after(100, self._poll_queue)
        self.root.after(1000, self._runtime_tick)
        self.root.after(250, self._playback_tick)
        self.root.after(50, self._wave_tick)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(0, self._apply_windows_title_bar)

    def _configure_window(self) -> None:
        self.root.title(WINDOW_TITLE)
        self.root.geometry("1280x820")
        self.root.minsize(1040, 700)
        self.root.configure(background=PALETTE["background"])
        self.root.option_add("*Font", ("Segoe UI", 10))
        self.root.option_add("*TCombobox*Listbox.background", PALETTE["black"])
        self.root.option_add("*TCombobox*Listbox.foreground", PALETTE["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", PALETTE["rust"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", PALETTE["cream"])
        self.root.option_add("*TCombobox*Listbox.font", ("Cascadia Mono", 9))
        icon_path = self.project_dir / "assets" / "tdn-retrofuture.ico"
        if icon_path.exists():
            self.root.iconbitmap(default=str(icon_path))
        taskbar_icon = self._load_image(
            "assets/tdn-icon-transparent.png",
            (256, 256),
        )
        if taskbar_icon is not None:
            self.taskbar_icon = taskbar_icon
            self.root.iconphoto(True, taskbar_icon)

        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "Nexus.TButton",
            background=PALETTE["amber"],
            foreground=PALETTE["black"],
            bordercolor=PALETTE["bright"],
            font=("Segoe UI Semibold", 9),
            padding=(14, 9),
        )
        style.map(
            "Nexus.TButton",
            background=[("active", PALETTE["bright"])],
            foreground=[("disabled", PALETTE["muted"])],
        )
        style.configure(
            "Quiet.TButton",
            background=PALETTE["panel_alt"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            padding=(10, 7),
        )
        style.map(
            "Quiet.TButton",
            background=[("active", PALETTE["rust"])],
        )
        style.configure(
            "ActionQuiet.TButton",
            background=PALETTE["panel_alt"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            font=("Segoe UI Semibold", 9),
            padding=(14, 9),
        )
        style.map(
            "ActionQuiet.TButton",
            background=[("active", PALETTE["rust"])],
        )
        style.configure(
            "Nexus.TEntry",
            fieldbackground=PALETTE["black"],
            foreground=PALETTE["ink"],
            bordercolor=PALETTE["line"],
            insertcolor=PALETTE["bright"],
            padding=7,
        )
        style.map(
            "Nexus.TEntry",
            fieldbackground=[("readonly", PALETTE["black"])],
            foreground=[("readonly", PALETTE["ink"])],
        )
        style.configure(
            "Nexus.TCombobox",
            fieldbackground=PALETTE["black"],
            background=PALETTE["panel_alt"],
            foreground=PALETTE["ink"],
            arrowcolor=PALETTE["amber"],
            bordercolor=PALETTE["line"],
            padding=6,
        )
        style.map(
            "Nexus.TCombobox",
            fieldbackground=[("readonly", PALETTE["black"])],
            foreground=[("readonly", PALETTE["ink"])],
        )
        style.configure(
            "Nexus.Horizontal.TProgressbar",
            background=PALETTE["amber"],
            troughcolor=PALETTE["black"],
            bordercolor=PALETTE["line"],
        )
        style.configure(
            "Activity.Horizontal.TProgressbar",
            background=PALETTE["bright"],
            troughcolor=PALETTE["panel_alt"],
            bordercolor=PALETTE["rust"],
        )
        for orientation in ("Vertical", "Horizontal"):
            scrollbar_style = f"Nexus.{orientation}.TScrollbar"
            style.configure(
                scrollbar_style,
                background=PALETTE["rust"],
                troughcolor=PALETTE["black"],
                bordercolor=PALETTE["line"],
                arrowcolor=PALETTE["bright"],
                darkcolor=PALETTE["panel_alt"],
                lightcolor=PALETTE["amber"],
                relief="flat",
                borderwidth=0,
                width=13,
            )
            style.map(
                scrollbar_style,
                background=[
                    ("pressed", PALETTE["bright"]),
                    ("active", PALETTE["amber"]),
                ],
                arrowcolor=[
                    ("pressed", PALETTE["black"]),
                    ("active", PALETTE["cream"]),
                ],
            )
        style.configure(
            "Nexus.TCheckbutton",
            background=PALETTE["panel"],
            foreground=PALETTE["ink"],
            indicatorcolor=PALETTE["black"],
            font=("Segoe UI", 9),
        )
        style.map(
            "Nexus.TCheckbutton",
            background=[("active", PALETTE["panel"])],
            foreground=[("active", PALETTE["cream"])],
            indicatorcolor=[
                ("selected", PALETTE["amber"]),
                ("!selected", PALETTE["black"]),
            ],
        )
        style.configure(
            "Nexus.Treeview",
            background=PALETTE["black"],
            foreground=PALETTE["ink"],
            fieldbackground=PALETTE["black"],
            rowheight=30,
            bordercolor=PALETTE["line"],
        )
        style.map(
            "Nexus.Treeview",
            background=[("selected", PALETTE["rust"])],
            foreground=[("selected", PALETTE["cream"])],
        )

    def _apply_windows_title_bar(self) -> None:
        if os.name != "nt":
            return
        try:
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())  # type: ignore[attr-defined]
            dark = ctypes.c_int(1)
            for attribute in (20, 19):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                    hwnd,
                    attribute,
                    ctypes.byref(dark),
                    ctypes.sizeof(dark),
                )

            def colorref(value: str) -> ctypes.c_int:
                red, green, blue = (
                    int(value[1:3], 16),
                    int(value[3:5], 16),
                    int(value[5:7], 16),
                )
                return ctypes.c_int(red | (green << 8) | (blue << 16))

            for attribute, color in (
                (34, PALETTE["line"]),
                (35, PALETTE["black"]),
                (36, PALETTE["ink"]),
            ):
                rendered = colorref(color)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                    hwnd,
                    attribute,
                    ctypes.byref(rendered),
                    ctypes.sizeof(rendered),
                )
        except (AttributeError, OSError, tk.TclError):
            return

    def _build_shell(self) -> None:
        self.shell = tk.Frame(self.root, background=PALETTE["background"])
        self.shell.pack(fill="both", expand=True)

        self.header = tk.Frame(
            self.shell,
            background=PALETTE["black"],
            padx=24,
            pady=14,
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
        )
        self.header.pack(fill="x")
        self.header.columnconfigure(1, weight=1)
        self.brand_icon = self._load_image("assets/tdn-icon-transparent.png", (72, 72))
        if self.brand_icon:
            tk.Label(
                self.header,
                image=self.brand_icon,
                background=PALETTE["black"],
            ).grid(row=0, column=0, rowspan=2, padx=(0, 14))
        tk.Label(
            self.header,
            text="THE DAILY NEXUS",
            background=PALETTE["black"],
            foreground=PALETTE["ink"],
            font=("Bahnschrift Condensed", 21, "bold"),
        ).grid(row=0, column=1, sticky="sw")
        tk.Label(
            self.header,
            text=HEADER_SUBTITLE,
            background=PALETTE["black"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 8),
        ).grid(row=1, column=1, sticky="nw")

        nav = tk.Frame(self.header, background=PALETTE["black"])
        nav.grid(row=0, column=2, rowspan=2, padx=(16, 0))
        self.nav_buttons: dict[str, tk.Button] = {}
        for key, label in (
            ("gen", "GEN"),
            ("play", "PLAY"),
            ("read", "READ"),
            ("about", "ABOUT"),
        ):
            button = tk.Button(
                nav,
                text=label,
                command=lambda selected=key: self._show_mode(selected),
                background=PALETTE["panel_alt"],
                foreground=PALETTE["ink"],
                activebackground=PALETTE["amber"],
                activeforeground=PALETTE["black"],
                relief="flat",
                borderwidth=0,
                font=("Cascadia Mono", 10, "bold"),
                padx=14,
                pady=9,
                cursor="hand2",
            )
            button.pack(side="left", padx=3)
            self.nav_buttons[key] = button
        self.mini_player = self._build_mini_player(self.shell)
        self.view_container = tk.Frame(
            self.shell,
            background=PALETTE["background"],
        )
        self.view_container.pack(fill="both", expand=True, padx=18, pady=14)
        self.view_container.pack_propagate(False)
        self.views: dict[str, tk.Frame] = {}
        self.views["gen"] = self._build_gen_view(self.view_container)
        self.views["play"] = self._build_play_view(self.view_container)
        self.views["read"] = self._build_read_view(self.view_container)
        self.views["about"] = self._build_about_view(self.view_container)

        footer = tk.Frame(
            self.shell,
            background=PALETTE["black"],
            padx=20,
            pady=9,
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
        )
        footer.pack(side="bottom", fill="x", before=self.view_container)
        tk.Label(
            footer,
            textvariable=self.runtime_state,
            background=PALETTE["rust"],
            foreground=PALETTE["ink"],
            font=("Cascadia Mono", 8, "bold"),
            padx=9,
            pady=4,
        ).pack(side="left")
        tk.Label(
            footer,
            textvariable=self.elapsed_status,
            background=PALETTE["black"],
            foreground=PALETTE["ink"],
            font=("Cascadia Mono", 8),
        ).pack(side="left", padx=(14, 0))
        tk.Label(
            footer,
            textvariable=self.estimate_status,
            background=PALETTE["black"],
            foreground=PALETTE["muted"],
            font=("Cascadia Mono", 8),
        ).pack(side="left", padx=(16, 0))
        self.footer_progress = ttk.Progressbar(
            footer,
            style="Nexus.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            length=220,
        )
        self.footer_progress.pack(side="right", padx=(10, 0))
        self.footer_activity = ttk.Progressbar(
            footer,
            style="Activity.Horizontal.TProgressbar",
            mode="indeterminate",
            maximum=100,
            length=92,
        )
        self.footer_activity.pack(side="right", padx=(10, 0))
        ttk.Button(
            footer,
            text="REFRESH STATUS",
            command=self.refresh_runtime_status,
            style="Quiet.TButton",
        ).pack(side="right")
        self._show_mode("gen")

    def _themed_scale(
        self,
        parent: tk.Widget,
        *,
        variable: tk.DoubleVar,
        from_: float,
        to: float,
        length: int,
        resolution: float,
        command,
    ) -> NexusLevelBar:
        return NexusLevelBar(
            parent,
            variable=variable,
            from_=from_,
            to=to,
            length=length,
            resolution=resolution,
            command=command,
        )

    @staticmethod
    def _nexus_scrolled_text(
        parent: tk.Widget,
        **text_options,
    ) -> tuple[tk.Frame, tk.Text, ttk.Scrollbar]:
        shell = tk.Frame(parent, background=PALETTE["black"])
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        text = tk.Text(shell, **text_options)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            shell,
            orient="vertical",
            command=text.yview,
            style="Nexus.Vertical.TScrollbar",
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        return shell, text, scrollbar

    def _build_mini_player(self, parent: tk.Widget) -> tk.Frame:
        frame = tk.Frame(
            parent,
            background=PALETTE["panel"],
            padx=18,
            pady=7,
            highlightbackground=PALETTE["amber"],
            highlightthickness=1,
        )
        frame.columnconfigure(1, weight=1)
        self.mini_wave = tk.Canvas(
            frame,
            width=94,
            height=30,
            background=PALETTE["black"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
        )
        self.mini_wave.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 10))
        tk.Label(
            frame,
            textvariable=self.mini_play_title,
            background=PALETTE["panel"],
            foreground=PALETTE["ink"],
            font=("Bahnschrift Condensed", 11, "bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="ew")
        tk.Label(
            frame,
            textvariable=self.mini_play_time,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 7, "bold"),
            anchor="w",
        ).grid(row=1, column=1, sticky="ew")
        self.mini_progress = ttk.Progressbar(
            frame,
            style="Nexus.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
            length=200,
        )
        self.mini_progress.grid(row=0, column=2, rowspan=2, sticky="ew", padx=12)
        self.mini_progress.configure(cursor="hand2")
        self.mini_progress.bind("<ButtonPress-1>", self._begin_playback_seek)
        self.mini_progress.bind("<B1-Motion>", self._preview_playback_seek)
        self.mini_progress.bind("<ButtonRelease-1>", self._commit_playback_seek)
        volume = self._themed_scale(
            frame,
            variable=self.playback_volume,
            from_=0,
            to=100,
            length=76,
            resolution=1,
            command=self._change_playback_volume,
        )
        volume.grid(row=0, column=3, rowspan=2, padx=(0, 8))
        self.mini_pause_button = tk.Button(
            frame,
            text="\u275a\u275a",
            command=self.pause_or_resume,
            background=PALETTE["panel_alt"],
            foreground=PALETTE["bright"],
            activebackground=PALETTE["rust"],
            activeforeground=PALETTE["cream"],
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Symbol", 12, "bold"),
            width=3,
            cursor="hand2",
        )
        self.mini_pause_button.grid(row=0, column=4, rowspan=2, padx=3)
        self.mini_stop_button = tk.Button(
            frame,
            text="\u25a0",
            command=self.stop_playback,
            background=PALETTE["panel_alt"],
            foreground=PALETTE["bright"],
            activebackground=PALETTE["rust"],
            activeforeground=PALETTE["cream"],
            relief="flat",
            borderwidth=0,
            font=("Segoe UI Symbol", 12, "bold"),
            width=3,
            cursor="hand2",
        )
        self.mini_stop_button.grid(row=0, column=5, rowspan=2, padx=3)
        speed = self._themed_scale(
            frame,
            variable=self.playback_speed_value,
            from_=75,
            to=200,
            length=76,
            resolution=25,
            command=self._preview_playback_speed,
        )
        speed.bind("<ButtonRelease-1>", self._commit_playback_speed_scale)
        speed.grid(row=0, column=6, rowspan=2, padx=(8, 0))
        self.playback_speed_scales = [speed]
        return frame

    def _build_gen_view(self, parent: tk.Widget) -> tk.Frame:
        view = tk.Frame(parent, background=PALETTE["background"])
        view.columnconfigure(0, weight=7)
        view.columnconfigure(1, minsize=14)
        view.columnconfigure(2, weight=5, minsize=360)
        view.rowconfigure(0, weight=1)
        left = tk.Frame(view, background=PALETTE["background"])
        left.grid(row=0, column=0, sticky="nsew")
        right_shell = tk.Frame(view, background=PALETTE["background"])
        right_shell.grid(row=0, column=2, sticky="nsew")
        right_shell.columnconfigure(0, weight=1)
        right_shell.rowconfigure(0, weight=1)
        right_canvas = tk.Canvas(
            right_shell,
            background=PALETTE["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.gen_options_canvas = right_canvas
        right_canvas.grid(row=0, column=0, sticky="nsew")
        right_scrollbar = ttk.Scrollbar(
            right_shell,
            orient="vertical",
            command=right_canvas.yview,
            style="Nexus.Vertical.TScrollbar",
        )
        right_scrollbar.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        right_canvas.configure(yscrollcommand=right_scrollbar.set)
        right = tk.Frame(right_canvas, background=PALETTE["background"])
        right_window = right_canvas.create_window((0, 0), window=right, anchor="nw")

        def resize_right_content(_event=None) -> None:
            right_canvas.configure(scrollregion=right_canvas.bbox("all"))
            right_canvas.itemconfigure(
                right_window,
                width=max(1, right_canvas.winfo_width()),
            )

        right.bind("<Configure>", resize_right_content)
        right_canvas.bind("<Configure>", resize_right_content)
        def scroll_right_pane(event) -> str | None:
            if not event.delta:
                return None
            steps = max(1, abs(int(event.delta)) // 120)
            direction = -steps if event.delta > 0 else steps
            right_canvas.yview_scroll(direction, "units")
            return "break"

        create_card, create_body = self._panel(
            left,
            "GEN // CREATE EPISODE",
            "Collect, edit, verify, render, and optionally publish.",
        )
        create_card.pack(fill="x")
        row = tk.Frame(create_body, background=PALETTE["panel"])
        row.pack(fill="x")
        self._field_label(row, "NEWSLETTER DATE").pack(side="left")
        self.date_entry = ttk.Entry(
            row,
            width=17,
            textvariable=self.episode_date,
            justify="center",
            style="Nexus.TEntry",
            state="readonly",
        )
        self.date_entry.pack(side="left", padx=(12, 8))
        tk.Button(
            row,
            text="\u25a6",
            command=self._open_calendar,
            background=PALETTE["panel_alt"],
            foreground=PALETTE["bright"],
            activebackground=PALETTE["rust"],
            activeforeground=PALETTE["cream"],
            font=("Segoe UI Symbol", 18, "bold"),
            relief="flat",
            borderwidth=0,
            width=3,
            height=1,
            cursor="hand2",
        ).pack(side="left")
        ttk.Checkbutton(
            create_body,
            text="Keep this run local (overrides automatic publishing)",
            variable=self.local_only,
            style="Nexus.TCheckbutton",
        ).pack(anchor="w", pady=(13, 12))
        buttons = tk.Frame(create_body, background=PALETTE["panel"])
        buttons.pack(fill="x")
        self.run_yesterday_button = ttk.Button(
            buttons,
            text="YESTERDAY",
            command=self.run_yesterday,
            style="Quiet.TButton",
            width=16,
        )
        self.run_yesterday_button.pack(side="left")
        self.run_button = ttk.Button(
            buttons,
            text="RUN",
            command=self.run_or_stop,
            style="Nexus.TButton",
            width=16,
        )
        self.run_button.pack(side="left", padx=(8, 0))

        activity_card, activity_body = self._panel(
            left,
            "PROCESS MONITOR",
            "Live stage messages. Refresh status at any time without interrupting the run.",
        )
        activity_card.pack(fill="both", expand=True, pady=(12, 0))
        tk.Label(
            activity_body,
            textvariable=self.status,
            background=PALETTE["panel"],
            foreground=PALETTE["bright"],
            font=("Cascadia Mono", 9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        output_shell, self.output, self.output_scrollbar = self._nexus_scrolled_text(
            activity_body,
            wrap="word",
            state="disabled",
            font=("Cascadia Mono", 8),
            background=PALETTE["black"],
            foreground=PALETTE["ink"],
            insertbackground=PALETTE["bright"],
            relief="flat",
            padx=12,
            pady=10,
        )
        output_shell.pack(fill="both", expand=True)

        account_card, account_body = self._panel(
            right,
            "GOOGLE ACCOUNT",
            "Only the configured Gmail label is read.",
        )
        account_card.pack(fill="x")
        tk.Label(
            account_body,
            textvariable=self.gmail_account,
            background=PALETTE["panel"],
            foreground=PALETTE["ink"],
            font=("Segoe UI Semibold", 10),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            account_body,
            textvariable=self.gmail_status,
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 8),
            wraplength=380,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(2, 9))
        account_buttons = tk.Frame(account_body, background=PALETTE["panel"])
        account_buttons.pack(fill="x")
        self.google_logo = self._load_image("assets/google-g.png", (20, 20))
        self.gmail_sign_in_button = tk.Button(
            account_buttons,
            text="  SIGN IN WITH GOOGLE",
            image=self.google_logo if self.google_logo else "",
            compound="left",
            command=self.sign_in_gmail,
            background=PALETTE["panel_alt"],
            foreground=PALETTE["ink"],
            activebackground=PALETTE["rust"],
            activeforeground=PALETTE["cream"],
            font=("Cascadia Mono", 8, "bold"),
            relief="solid",
            borderwidth=1,
            highlightbackground=PALETTE["amber"],
            highlightcolor=PALETTE["bright"],
            padx=10,
            pady=6,
            cursor="hand2",
        )
        self.gmail_sign_in_button.pack(side="left")
        self.gmail_sign_out_button = ttk.Button(
            account_buttons,
            text="DISCONNECT",
            command=self.sign_out_gmail,
            style="Quiet.TButton",
        )
        self.gmail_sign_out_button.pack(side="right")

        pref_card, pref_body = self._panel(
            right,
            "HOST + EPISODE CONTROLS",
            "Dario Novelli edits and produces. Dalia and Nox present.",
        )
        pref_card.pack(fill="x", pady=(12, 0))
        self._field_label(pref_body, "GMAIL SOURCE LABEL").pack(anchor="w")
        self.gmail_label_entry = ttk.Entry(
            pref_body,
            textvariable=self.gmail_label,
            style="Nexus.TEntry",
        )
        self.gmail_label_entry.pack(fill="x", pady=(3, 7))
        self._field_label(pref_body, "HOST FORMAT").pack(anchor="w")
        self.host_count_combo = ttk.Combobox(
            pref_body,
            textvariable=self.host_count_choice,
            values=list(HOST_COUNT_LABELS),
            state="readonly",
            style="Nexus.TCombobox",
        )
        self.host_count_combo.pack(fill="x", pady=(3, 7))
        self.host_count_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._update_host_controls(),
        )
        self._field_label(pref_body, "SOLO PRESENTER").pack(anchor="w")
        self.solo_host_combo = ttk.Combobox(
            pref_body,
            textvariable=self.solo_host_choice,
            values=("Dalia", "Nox"),
            state="readonly",
            style="Nexus.TCombobox",
        )
        self.solo_host_combo.pack(fill="x", pady=(3, 7))
        self.dialogue_style_label = self._field_label(
            pref_body,
            "TWO-HOST DELIVERY",
        )
        self.dialogue_style_label.pack(anchor="w")
        self.dialogue_style_combo = ttk.Combobox(
            pref_body,
            textvariable=self.dialogue_style_choice,
            values=list(DIALOGUE_STYLE_LABELS),
            state="disabled",
            style="Nexus.TCombobox",
        )
        self.dialogue_style_combo.pack(fill="x", pady=(3, 7))
        self._host_control_row(
            pref_body,
            "DALIA",
            self.dalia_personality_choice,
            self.dalia_tone_choice,
            secondary=False,
        )
        self._host_control_row(
            pref_body,
            "NOX",
            self.nox_personality_choice,
            self.nox_tone_choice,
            secondary=True,
        )
        self._field_label(pref_body, "PUBLISHING").pack(anchor="w", pady=(3, 0))
        self.publishing_mode_combo = ttk.Combobox(
            pref_body,
            textvariable=self.publishing_mode_choice,
            values=list(PUBLISHING_MODE_LABELS),
            state="readonly",
            style="Nexus.TCombobox",
        )
        self.publishing_mode_combo.pack(fill="x", pady=(3, 7))
        self.preferences_save_button = ttk.Button(
            pref_body,
            text="SAVE + VERIFY",
            command=self.save_episode_preferences,
            style="Nexus.TButton",
        )
        self.preferences_save_button.pack(anchor="w")
        tk.Label(
            pref_body,
            textvariable=self.preference_status,
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 7),
            wraplength=380,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(5, 0))

        publish_card, publish_body = self._panel(
            right,
            "APPLE PRIVATE FEED",
            "Private-by-link RSS on Firebase Spark. Keep the feed URL secret.",
        )
        publish_card.pack(fill="x", pady=(12, 0))
        tk.Label(
            publish_body,
            textvariable=self.publishing_status,
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 8),
            wraplength=380,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 7))
        publish_buttons = tk.Frame(publish_body, background=PALETTE["panel"])
        publish_buttons.pack(fill="x")
        self.publish_button = ttk.Button(
            publish_buttons,
            text="PUBLISH SELECTED DATE",
            command=self.publish_selected_date,
            style="Nexus.TButton",
        )
        self.publish_button.pack(side="left")
        self.copy_feed_button = ttk.Button(
            publish_buttons,
            text="COPY APPLE URL",
            command=self.copy_private_feed_url,
            style="Quiet.TButton",
        )
        self.copy_feed_button.pack(side="right")
        publish_setup_buttons = tk.Frame(
            publish_body,
            background=PALETTE["panel"],
        )
        publish_setup_buttons.pack(fill="x", pady=(5, 0))
        self.publishing_configure_button = ttk.Button(
            publish_setup_buttons,
            text="CONFIGURE",
            command=self.configure_apple_publishing,
            style="Quiet.TButton",
        )
        self.publishing_configure_button.pack(side="left", padx=(0, 4))
        self.firebase_sign_in_button = ttk.Button(
            publish_setup_buttons,
            text="FIREBASE SIGN-IN",
            command=self.sign_in_firebase,
            style="Quiet.TButton",
        )
        self.firebase_sign_in_button.pack(side="left", padx=(0, 4))
        publish_enable_buttons = tk.Frame(
            publish_body,
            background=PALETTE["panel"],
        )
        publish_enable_buttons.pack(fill="x", pady=(5, 0))
        self.publishing_enable_button = ttk.Button(
            publish_enable_buttons,
            text="CONFIRM SPARK + ENABLE",
            command=self.confirm_spark_and_enable,
            style="Quiet.TButton",
        )
        self.publishing_enable_button.pack(side="left")
        ttk.Button(
            publish_enable_buttons,
            text="GUIDE",
            command=self.open_publishing_guide,
            style="Quiet.TButton",
        ).pack(side="right")

        system_card, system_body = self._panel(
            right,
            "SYSTEM",
            "",
        )
        system_card.pack(fill="x", pady=(12, 0))
        tk.Label(
            system_body,
            textvariable=self.configuration,
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
            font=("Segoe UI", 7),
            wraplength=380,
            justify="left",
            anchor="w",
        ).pack(fill="x", pady=(0, 6))
        system_buttons = tk.Frame(system_body, background=PALETTE["panel"])
        system_buttons.pack(fill="x")
        for text, action in (
            ("DOCTOR", self.run_doctor),
            ("EPISODES", self.open_episodes),
            ("LOGS", self.open_logs),
            ("CONFIG", self.open_configuration),
        ):
            ttk.Button(
                system_buttons,
                text=text,
                command=action,
                style="Quiet.TButton",
            ).pack(side="left", padx=(0, 4))

        def bind_scroll_tree(widget: tk.Widget) -> None:
            widget.bind("<MouseWheel>", scroll_right_pane, add="+")
            for child in widget.winfo_children():
                bind_scroll_tree(child)

        right_canvas.bind("<MouseWheel>", scroll_right_pane, add="+")
        bind_scroll_tree(right)
        return view

    def _host_control_row(
        self,
        parent: tk.Widget,
        host_name: str,
        personality_variable: StringVar,
        tone_variable: StringVar,
        *,
        secondary: bool,
    ) -> None:
        block = tk.Frame(parent, background=PALETTE["panel"])
        block.pack(fill="x", pady=(2, 8))
        self._field_label(block, host_name).pack(anchor="w")
        headings = tk.Frame(block, background=PALETTE["panel"])
        headings.pack(fill="x", pady=(3, 0))
        headings.columnconfigure(0, weight=3)
        headings.columnconfigure(1, weight=2)
        self._field_label(headings, "PERSONALITY").grid(
            row=0,
            column=0,
            sticky="w",
        )
        self._field_label(headings, "TONE").grid(
            row=0,
            column=1,
            sticky="w",
            padx=(8, 0),
        )
        row = tk.Frame(block, background=PALETTE["panel"])
        row.pack(fill="x", pady=(2, 0))
        personality = ttk.Combobox(
            row,
            textvariable=personality_variable,
            values=PERSONALITY_CHOICES,
            state="readonly",
            width=22,
            style="Nexus.TCombobox",
        )
        personality.pack(side="left", fill="x", expand=True)
        tone = ttk.Combobox(
            row,
            textvariable=tone_variable,
            values=list(TONE_LABELS),
            state="readonly",
            width=12,
            style="Nexus.TCombobox",
        )
        tone.pack(side="left", fill="x", expand=True, padx=(6, 0))
        if secondary:
            self.nox_personality_combo = personality
            self.nox_tone_combo = tone
        else:
            self.dalia_personality_combo = personality
            self.dalia_tone_combo = tone

    def _build_play_view(self, parent: tk.Widget) -> tk.Frame:
        view = tk.Frame(parent, background=PALETTE["background"])
        view.columnconfigure(0, weight=2)
        view.columnconfigure(1, minsize=14)
        view.columnconfigure(2, weight=3)
        view.rowconfigure(0, weight=1)
        library_card, library_body = self._panel(
            view,
            "PLAY // LOCAL ARCHIVE",
            "Completed episodes stored on this computer.",
        )
        library_card.grid(row=0, column=0, sticky="nsew")
        self.play_list = tk.Listbox(
            library_body,
            background=PALETTE["black"],
            foreground=PALETTE["ink"],
            selectbackground=PALETTE["rust"],
            selectforeground=PALETTE["ink"],
            font=("Cascadia Mono", 9),
            relief="flat",
            borderwidth=0,
            activestyle="none",
        )
        self.play_list.pack(fill="both", expand=True)
        self.play_list.bind("<<ListboxSelect>>", self._select_play_episode)
        ttk.Button(
            library_body,
            text="REFRESH LIBRARY",
            command=self._refresh_library,
            style="Quiet.TButton",
        ).pack(anchor="w", pady=(9, 0))

        player_shell = tk.Frame(view, background=PALETTE["background"])
        player_shell.grid(row=0, column=2, sticky="nsew")
        player_shell.columnconfigure(0, weight=1)
        player_shell.rowconfigure(0, weight=1)
        player_canvas = tk.Canvas(
            player_shell,
            background=PALETTE["background"],
            borderwidth=0,
            highlightthickness=0,
        )
        self.player_canvas = player_canvas
        player_canvas.grid(row=0, column=0, sticky="nsew")
        player_scrollbar = ttk.Scrollbar(
            player_shell,
            orient="vertical",
            command=player_canvas.yview,
            style="Nexus.Vertical.TScrollbar",
        )
        player_scrollbar.grid(row=0, column=1, sticky="ns", padx=(5, 0))
        player_canvas.configure(yscrollcommand=player_scrollbar.set)
        player_surface = tk.Frame(player_canvas, background=PALETTE["background"])
        self.player_surface = player_surface
        player_window = player_canvas.create_window(
            (0, 0),
            window=player_surface,
            anchor="nw",
        )

        def resize_player_content(_event=None) -> None:
            viewport_height = max(1, player_canvas.winfo_height())
            requested_height = max(1, player_surface.winfo_reqheight())
            surface_height = _player_surface_height(
                viewport_height,
                requested_height,
            )
            player_canvas.itemconfigure(
                player_window,
                width=max(1, player_canvas.winfo_width()),
                height=surface_height,
            )
            player_canvas.configure(scrollregion=player_canvas.bbox("all"))
            if requested_height <= viewport_height:
                player_canvas.yview_moveto(0)

        def scroll_player(event) -> str | None:
            if not event.delta:
                return None
            steps = max(1, abs(int(event.delta)) // 120)
            player_canvas.yview_scroll(-steps if event.delta > 0 else steps, "units")
            return "break"

        player_surface.bind("<Configure>", resize_player_content)
        player_canvas.bind("<Configure>", resize_player_content)
        player_canvas.bind("<MouseWheel>", scroll_player, add="+")

        player_card, player_body = self._panel(
            player_surface,
            "AUDIO TERMINAL",
            "Playback uses the local Windows media system. No stream is uploaded.",
        )
        self.player_card = player_card
        player_card.pack(fill="both", expand=True)
        self.play_cover = tk.Label(
            player_body,
            background=PALETTE["black"],
            foreground=PALETTE["muted"],
            text="TDN",
            font=("Bahnschrift Condensed", 36, "bold"),
            width=12,
            height=5,
        )
        self.play_cover.pack(pady=(0, 12))
        tk.Label(
            player_body,
            textvariable=self.play_title,
            background=PALETTE["panel"],
            foreground=PALETTE["ink"],
            font=("Bahnschrift Condensed", 19, "bold"),
        ).pack()
        tk.Label(
            player_body,
            textvariable=self.play_detail,
            background=PALETTE["panel"],
            foreground=PALETTE["muted"],
            font=("Cascadia Mono", 8),
        ).pack(pady=(3, 12))
        self.play_wave = tk.Canvas(
            player_body,
            height=66,
            background=PALETTE["black"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
        )
        self.play_wave.pack(fill="x", pady=(0, 12))
        controls = tk.Frame(player_body, background=PALETTE["panel"])
        controls.pack(fill="x")
        controls.columnconfigure(0, weight=1)
        controls.columnconfigure(1, weight=0)
        controls.columnconfigure(2, weight=1)
        volume_group = tk.Frame(controls, background=PALETTE["panel"])
        volume_group.grid(row=0, column=0, sticky="w")
        tk.Label(
            volume_group,
            textvariable=self.playback_volume_status,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 7, "bold"),
        ).pack(anchor="w")
        self.playback_volume_scale = self._themed_scale(
            volume_group,
            variable=self.playback_volume,
            from_=0,
            to=100,
            length=106,
            resolution=1,
            command=self._change_playback_volume,
        )
        self.playback_volume_scale.pack(anchor="w", pady=(2, 0))
        buttons = tk.Frame(controls, background=PALETTE["panel"])
        buttons.grid(row=0, column=1, padx=14)
        icon_button = {
            "background": PALETTE["panel_alt"],
            "foreground": PALETTE["bright"],
            "activebackground": PALETTE["rust"],
            "activeforeground": PALETTE["cream"],
            "relief": "flat",
            "borderwidth": 0,
            "font": ("Segoe UI Symbol", 18, "bold"),
            "width": 3,
            "height": 1,
            "cursor": "hand2",
        }
        self.play_button = tk.Button(
            buttons,
            text="\u25b6",
            command=self.play_selected_episode,
            **icon_button,
        )
        self.play_button.pack(side="left")
        self.pause_button = tk.Button(
            buttons,
            text="\u275a\u275a",
            command=self.pause_or_resume,
            **icon_button,
        )
        self.pause_button.pack(side="left", padx=7)
        self.stop_button = tk.Button(
            buttons,
            text="\u25a0",
            command=self.stop_playback,
            **icon_button,
        )
        self.stop_button.pack(side="left")
        speed_group = tk.Frame(controls, background=PALETTE["panel"])
        speed_group.grid(row=0, column=2, sticky="e")
        tk.Label(
            speed_group,
            textvariable=self.playback_speed_status,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 7, "bold"),
            anchor="e",
        ).pack(anchor="e")
        self.playback_speed_scale = self._themed_scale(
            speed_group,
            variable=self.playback_speed_value,
            from_=75,
            to=200,
            length=116,
            resolution=25,
            command=self._preview_playback_speed,
        )
        self.playback_speed_scale.pack(anchor="e", pady=(2, 0))
        self.playback_speed_scale.bind(
            "<ButtonRelease-1>",
            self._commit_playback_speed_scale,
        )
        self.playback_speed_scales.append(self.playback_speed_scale)
        tk.Label(
            player_body,
            textvariable=self.play_time,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 9, "bold"),
        ).pack(pady=(10, 4))
        self.play_progress = ttk.Progressbar(
            player_body,
            style="Nexus.Horizontal.TProgressbar",
            mode="determinate",
            maximum=100,
        )
        self.play_progress.pack(fill="x")
        self.play_progress.configure(cursor="hand2")
        self.play_progress.bind("<ButtonPress-1>", self._begin_playback_seek)
        self.play_progress.bind("<B1-Motion>", self._preview_playback_seek)
        self.play_progress.bind("<ButtonRelease-1>", self._commit_playback_seek)
        content_switch = tk.Frame(player_body, background=PALETTE["panel"])
        content_switch.pack(fill="x", pady=(12, 0))
        for mode, label in (
            ("references", "REFERENCES"),
            ("transcript", "TRANSCRIPT"),
        ):
            selected = mode == self.play_content_mode.get()
            button = tk.Button(
                content_switch,
                text=label,
                command=lambda chosen=mode: self._set_play_content_mode(chosen),
                background=PALETTE["amber"] if selected else PALETTE["panel_alt"],
                foreground=PALETTE["black"] if selected else PALETTE["ink"],
                activebackground=PALETTE["bright"],
                activeforeground=PALETTE["black"],
                relief="flat",
                borderwidth=0,
                font=("Cascadia Mono", 8, "bold"),
                padx=10,
                pady=5,
                cursor="hand2",
            )
            button.pack(side="left", padx=(0, 5))
            self.play_content_buttons[mode] = button
        notes_shell, self.play_notes, self.play_notes_scrollbar = (
            self._nexus_scrolled_text(
                player_body,
                height=18,
                wrap="word",
                state="disabled",
                background=PALETTE["black"],
                foreground=PALETTE["ink"],
                font=("Segoe UI", 8),
                relief="flat",
                padx=10,
                pady=8,
            )
        )
        notes_shell.pack(fill="both", expand=True, pady=(7, 0))
        links = tk.Frame(player_body, background=PALETTE["panel"])
        links.pack(fill="x", pady=(8, 0))
        ttk.Button(
            links,
            text="OPEN EPISODE FOLDER",
            command=self.open_selected_episode_folder,
            style="Quiet.TButton",
        ).pack(side="left")
        ttk.Button(
            links,
            text="OPEN PRIVATE FEED",
            command=self.open_private_feed,
            style="Quiet.TButton",
        ).pack(side="right")

        def bind_player_scroll_tree(widget: tk.Widget) -> None:
            if not isinstance(widget, tk.Text):
                widget.bind("<MouseWheel>", scroll_player, add="+")
            for child in widget.winfo_children():
                bind_player_scroll_tree(child)

        bind_player_scroll_tree(player_surface)
        return view

    def _build_read_view(self, parent: tk.Widget) -> tk.Frame:
        view = tk.Frame(parent, background=PALETTE["background"])
        view.columnconfigure(0, weight=1)
        view.columnconfigure(1, minsize=14)
        view.columnconfigure(2, weight=4)
        view.rowconfigure(0, weight=1)
        library_card, library_body = self._panel(
            view,
            "READ // 2-3 PAGE EDITIONS",
            "Two pages are preferred; a third preserves readable news when needed.",
        )
        library_card.grid(row=0, column=0, sticky="nsew")
        self.read_list = tk.Listbox(
            library_body,
            background=PALETTE["black"],
            foreground=PALETTE["ink"],
            selectbackground=PALETTE["rust"],
            selectforeground=PALETTE["ink"],
            font=("Cascadia Mono", 9),
            relief="flat",
            borderwidth=0,
            activestyle="none",
        )
        self.read_list.pack(fill="both", expand=True)
        self.read_list.bind("<<ListboxSelect>>", self._select_read_episode)
        ttk.Button(
            library_body,
            text="REFRESH EDITIONS",
            command=self._refresh_library,
            style="Quiet.TButton",
        ).pack(anchor="w", pady=(9, 0))

        reader_card, reader_body = self._panel(
            view,
            "DOCUMENT VIEWER",
            "Browse every page in place or open the complete PDF.",
        )
        reader_card.grid(row=0, column=2, sticky="nsew")
        tk.Label(
            reader_body,
            textvariable=self.read_title,
            background=PALETTE["panel"],
            foreground=PALETTE["bright"],
            font=("Cascadia Mono", 9, "bold"),
        ).pack(anchor="w", pady=(0, 8))
        reader_surface = tk.Frame(reader_body, background=PALETTE["black"])
        reader_surface.pack(fill="both", expand=True)
        reader_surface.columnconfigure(0, weight=1)
        reader_surface.rowconfigure(0, weight=1)
        self.read_canvas = tk.Canvas(
            reader_surface,
            background=PALETTE["black"],
            highlightthickness=0,
        )
        self.read_canvas.grid(row=0, column=0, sticky="nsew")
        read_vertical = ttk.Scrollbar(
            reader_surface,
            orient="vertical",
            command=self.read_canvas.yview,
            style="Nexus.Vertical.TScrollbar",
        )
        read_vertical.grid(row=0, column=1, sticky="ns")
        read_horizontal = ttk.Scrollbar(
            reader_surface,
            orient="horizontal",
            command=self.read_canvas.xview,
            style="Nexus.Horizontal.TScrollbar",
        )
        read_horizontal.grid(row=1, column=0, sticky="ew")
        self.read_canvas.configure(
            yscrollcommand=read_vertical.set,
            xscrollcommand=read_horizontal.set,
        )
        self.read_canvas.bind("<Configure>", lambda _event: self._render_read_preview())
        self.read_canvas.bind("<MouseWheel>", self._scroll_read_vertical)
        self.read_canvas.bind("<Shift-MouseWheel>", self._scroll_read_horizontal)
        self.read_canvas.bind(
            "<ButtonPress-1>",
            lambda event: self.read_canvas.scan_mark(event.x, event.y),
        )
        self.read_canvas.bind(
            "<B1-Motion>",
            lambda event: self.read_canvas.scan_dragto(event.x, event.y, gain=1),
        )
        read_controls = tk.Frame(reader_body, background=PALETTE["panel"])
        read_controls.pack(fill="x", pady=(8, 0))
        ttk.Button(
            read_controls,
            text="◀",
            command=lambda: self._change_read_page(-1),
            style="Quiet.TButton",
        ).pack(side="left")
        tk.Label(
            read_controls,
            textvariable=self.read_page_status,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 8, "bold"),
        ).pack(side="left", padx=8)
        ttk.Button(
            read_controls,
            text="▶",
            command=lambda: self._change_read_page(1),
            style="Quiet.TButton",
        ).pack(side="left")
        ttk.Button(
            read_controls,
            text="−",
            command=lambda: self._change_read_zoom(-0.15),
            style="Quiet.TButton",
        ).pack(side="left", padx=(18, 4))
        tk.Label(
            read_controls,
            textvariable=self.read_zoom_status,
            background=PALETTE["panel"],
            foreground=PALETTE["ink"],
            font=("Cascadia Mono", 8),
        ).pack(side="left")
        ttk.Button(
            read_controls,
            text="+",
            command=lambda: self._change_read_zoom(0.15),
            style="Quiet.TButton",
        ).pack(side="left", padx=(4, 0))
        ttk.Button(
            read_controls,
            text="OPEN PDF",
            command=self.open_selected_pdf,
            style="Nexus.TButton",
        ).pack(side="right")
        self.read_preview_image = None
        return view

    def _build_about_view(self, parent: tk.Widget) -> tk.Frame:
        view = tk.Frame(parent, background=PALETTE["background"])
        view.columnconfigure(0, weight=3)
        view.columnconfigure(1, minsize=14)
        view.columnconfigure(2, weight=2)
        view.rowconfigure(0, weight=1)

        story_card, story_body = self._panel(
            view,
            "ABOUT // THE DAILY NEXUS",
            f"Version {__version__} // private morning intelligence",
        )
        story_card.grid(row=0, column=0, sticky="nsew")
        logo = self._load_image("assets/tdn-icon-transparent.png", (126, 126))
        if logo:
            logo_label = tk.Label(
                story_body,
                image=logo,
                background=PALETTE["panel"],
            )
            logo_label.image = logo
            logo_label.pack(anchor="w", pady=(0, 10))
        about_copy = (
            "I am Dario Novelli, the editor and producer behind The Daily Nexus.\n\n"
            "I turn a deliberately limited Gmail label into a private daily briefing. "
            "Dalia, Nox, or both can present it as a structured broadcast or a more "
            "natural conversation. Alongside the episode, I create a two-page "
            "executive edition for fast reading, reserving a third page only when "
            "readability requires it.\n\n"
            "Nothing is published unless you enable and confirm private publishing. "
            "The archive, audio, transcript, and editions stay on this computer by "
            "default."
        )
        tk.Label(
            story_body,
            text=about_copy,
            background=PALETTE["panel"],
            foreground=PALETTE["ink"],
            font=("Segoe UI", 11),
            justify="left",
            anchor="nw",
            wraplength=650,
        ).pack(fill="x")

        flow_card, flow_body = self._panel(
            view,
            "HOW I BUILD AN EDITION",
            "A short map of the local-first process.",
        )
        flow_card.grid(row=0, column=2, sticky="nsew")
        steps = (
            ("01", "COLLECT", "Read only messages inside your chosen Gmail label."),
            ("02", "RESEARCH", "Retrieve safe public context and rank useful signals."),
            ("03", "EDIT", "Use Antigravity to draft, challenge, and verify the edition."),
            ("04", "RENDER", "Create speech locally with Kokoro and assemble it with FFmpeg."),
            (
                "05",
                "PACKAGE",
                "Store audio, transcript, references, and a 2-3 page PDF.",
            ),
            ("06", "PUBLISH", "Upload only after private publishing is configured and allowed."),
        )
        for number, title, detail in steps:
            row = tk.Frame(
                flow_body,
                background=PALETTE["black"],
                highlightbackground=PALETTE["line"],
                highlightthickness=1,
                padx=10,
                pady=8,
            )
            row.pack(fill="x", pady=(0, 7))
            tk.Label(
                row,
                text=number,
                background=PALETTE["rust"],
                foreground=PALETTE["cream"],
                font=("Cascadia Mono", 8, "bold"),
                padx=6,
                pady=3,
            ).pack(side="left", anchor="n")
            copy = tk.Frame(row, background=PALETTE["black"])
            copy.pack(side="left", fill="x", expand=True, padx=(9, 0))
            tk.Label(
                copy,
                text=title,
                background=PALETTE["black"],
                foreground=PALETTE["amber"],
                font=("Cascadia Mono", 8, "bold"),
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                copy,
                text=detail,
                background=PALETTE["black"],
                foreground=PALETTE["ink"],
                font=("Segoe UI", 8),
                justify="left",
                anchor="w",
                wraplength=340,
            ).pack(fill="x", pady=(2, 0))
        tk.Label(
            flow_body,
            text=(
                "COST GUARD // Google AI Pro authentication only; G1 credits off; "
                "telemetry off; local speech; Firebase Spark only when enabled."
            ),
            background=PALETTE["panel"],
            foreground=PALETTE["bright"],
            font=("Cascadia Mono", 7, "bold"),
            justify="left",
            wraplength=380,
        ).pack(fill="x", pady=(4, 0))
        return view

    def _panel(
        self,
        parent: tk.Widget,
        title: str,
        subtitle: str,
    ) -> tuple[tk.Frame, tk.Frame]:
        panel = tk.Frame(
            parent,
            background=PALETTE["panel"],
            highlightbackground=PALETTE["line"],
            highlightthickness=1,
            padx=16,
            pady=13,
        )
        tk.Label(
            panel,
            text=title,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 10, "bold"),
            anchor="w",
        ).pack(fill="x")
        if subtitle:
            tk.Label(
                panel,
                text=subtitle,
                background=PALETTE["panel"],
                foreground=PALETTE["muted"],
                font=("Segoe UI", 8),
                anchor="w",
                justify="left",
                wraplength=680,
            ).pack(fill="x", pady=(2, 10))
        body = tk.Frame(panel, background=PALETTE["panel"])
        body.pack(fill="both", expand=True)
        return panel, body

    @staticmethod
    def _field_label(parent: tk.Widget, text: str) -> tk.Label:
        return tk.Label(
            parent,
            text=text,
            background=PALETTE["panel"],
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 7, "bold"),
        )

    def _load_image(
        self,
        relative_path: str,
        size: tuple[int, int],
    ):
        path = self.project_dir / relative_path
        if not path.is_file():
            return None
        try:
            from PIL import Image, ImageTk

            image = Image.open(path).convert("RGBA")
            image.thumbnail(size, Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)
        except (ImportError, OSError, tk.TclError):
            return None

    def _show_mode(self, mode: str) -> None:
        self.mode = mode
        for key, view in self.views.items():
            view.pack_forget()
            self.nav_buttons[key].configure(
                background=(PALETTE["amber"] if key == mode else PALETTE["panel_alt"]),
                foreground=(PALETTE["black"] if key == mode else PALETTE["ink"]),
            )
        self.views[mode].pack(fill="both", expand=True)
        self._refresh_mini_player_visibility()
        if mode in {"play", "read"}:
            self._refresh_library()

    def _refresh_configuration(self) -> None:
        if not self.config_path.exists():
            self.configuration.set("CONFIG MISSING // Run setup-windows.ps1")
            self.local_only.set(True)
            return
        try:
            settings = load_settings(self.config_path)
        except Exception as exc:
            self.configuration.set(f"CONFIG ERROR // {exc}")
            self.preference_status.set(f"Preferences need attention: {exc}")
            self.local_only.set(True)
            return

        self.current_settings = settings
        self.runtime_dir = settings.app.runtime_dir
        self.database = StateDatabase(settings.database_path)
        self.estimated_seconds = self.database.estimated_run_seconds()
        self.gmail_label.set(settings.app.gmail_label)
        self.host_count_choice.set(HOST_COUNT_BY_VALUE[settings.hosts.count])
        self.solo_host_choice.set(settings.hosts.solo_name)
        self.dialogue_style_choice.set(
            DIALOGUE_STYLE_BY_ID[settings.hosts.dialogue_style]
        )
        dalia_gender, dalia_personality = controls_for_voice_id(
            settings.hosts.primary_voice
        )
        nox_gender, nox_personality = controls_for_voice_id(
            settings.hosts.secondary_voice
        )
        if dalia_gender != "Female":
            dalia_personality = "Warm and engaging"
        self.dalia_personality_choice.set(dalia_personality)
        self.dalia_tone_choice.set(TONE_LABEL_BY_ID[settings.hosts.primary_tone])
        if nox_gender != "Male":
            nox_personality = "Warm and engaging"
        self.nox_personality_choice.set(nox_personality)
        self.nox_tone_choice.set(TONE_LABEL_BY_ID[settings.hosts.secondary_tone])
        self.publishing_mode_choice.set(PUBLISHING_MODE_BY_ID[settings.firebase.publish_mode])
        self._update_host_controls()
        publishing_configured = (
            bool(settings.firebase.project_id)
            and "REPLACE_" not in settings.firebase.project_id
            and bool(settings.firebase.base_url)
            and len(settings.firebase.secret_path) >= 32
        )
        if settings.firebase.publish_enabled:
            self.configuration.set("SYSTEM READY // Publishing controls are enabled.")
            self.publishing_status.set(
                f"APPLE READY // {settings.firebase.publish_mode.upper()} // "
                f"{settings.firebase.base_url} // Private URL hidden"
            )
        elif publishing_configured:
            self.configuration.set(
                "SYSTEM READY // Local generation enabled; Apple publishing awaits approval."
            )
            self.publishing_status.set(
                f"SETUP PENDING // {settings.firebase.project_id} // "
                "Sign in, verify Spark with no billing, then enable."
            )
        else:
            self.configuration.set("SYSTEM READY // Local generation enabled; publishing disabled.")
            self.publishing_status.set(
                "OFFLINE // Configure a dedicated no-billing Firebase Spark project."
            )
            self.local_only.set(True)
        self.publish_button.configure(
            state="normal" if settings.firebase.publish_enabled else "disabled"
        )
        self.copy_feed_button.configure(
            state="normal" if settings.firebase.publish_enabled else "disabled"
        )
        self.firebase_sign_in_button.configure(
            state="normal" if publishing_configured else "disabled"
        )
        self.publishing_enable_button.configure(
            state=(
                "normal"
                if publishing_configured and not settings.firebase.publish_enabled
                else "disabled"
            )
        )

        try:
            token_store = GmailTokenStore(settings)
            signed_in = token_store.exists()
            cached_email = token_store.get_account_email() if signed_in else None
        except GmailConfigurationError as exc:
            self.gmail_account.set("Account unavailable")
            self.gmail_status.set(str(exc))
            signed_in = False
        else:
            if signed_in:
                self.gmail_account.set(cached_email or "Google account connected")
                self.gmail_status.set("Gmail read-only // Credentials secured by Windows.")
                if self.identity_worker is None or not self.identity_worker.is_alive():
                    self.identity_worker = threading.Thread(
                        target=self._load_gmail_identity,
                        args=(settings,),
                        daemon=True,
                    )
                    self.identity_worker.start()
            else:
                self.gmail_account.set("Not signed in")
                self.gmail_status.set("No mailbox access is available.")
        self.gmail_sign_in_button.configure(state="disabled" if signed_in else "normal")
        self.gmail_sign_out_button.configure(state="normal" if signed_in else "disabled")

    def _load_gmail_identity(self, settings: Settings) -> None:
        try:
            email = GmailClient(settings).account_email()
        except Exception as exc:
            self.command_queue.put(("gmail_identity_error", str(exc)))
            return
        self.command_queue.put(("gmail_identity", email))

    def _update_host_controls(self) -> None:
        count = HOST_COUNT_LABELS.get(self.host_count_choice.get(), 1)
        if hasattr(self, "solo_host_combo"):
            self.solo_host_combo.configure(
                state="readonly" if count == 1 else "disabled"
            )
        if hasattr(self, "dialogue_style_combo"):
            self.dialogue_style_combo.configure(
                state="readonly" if count == 2 else "disabled"
            )

    def save_episode_preferences(self) -> None:
        settings = self.current_settings
        if settings is None:
            messagebox.showerror(
                "Configuration unavailable",
                "Fix config.toml before saving preferences.",
                parent=self.root,
            )
            return
        if self.process is not None or (
            self.preference_worker is not None and self.preference_worker.is_alive()
        ):
            messagebox.showinfo(
                "The Daily Nexus is busy",
                "Wait for the current action to finish.",
                parent=self.root,
            )
            return
        try:
            label = validate_gmail_label(self.gmail_label.get())
            host_count = HOST_COUNT_LABELS[self.host_count_choice.get()]
            solo_host = self.solo_host_choice.get()
            dialogue_style = DIALOGUE_STYLE_LABELS[
                self.dialogue_style_choice.get()
            ]
            dalia_voice = voice_id_for_controls(
                "Female",
                self.dalia_personality_choice.get(),
            )
            dalia_tone = TONE_LABELS[self.dalia_tone_choice.get()]
            nox_voice = voice_id_for_controls(
                "Male",
                self.nox_personality_choice.get(),
            )
            nox_tone = TONE_LABELS[self.nox_tone_choice.get()]
            publishing_mode = PUBLISHING_MODE_LABELS[self.publishing_mode_choice.get()]
        except (PreferenceValidationError, KeyError) as exc:
            messagebox.showerror(
                "Check your preferences",
                str(exc) or "Choose valid host and publishing options.",
                parent=self.root,
            )
            return

        self.preference_status.set("Verifying the Gmail label and saving...")
        self._set_preferences_busy(True)
        self.preference_worker = threading.Thread(
            target=self._save_preferences_worker,
            args=(
                settings,
                label,
                host_count,
                solo_host,
                dialogue_style,
                dalia_voice,
                dalia_tone,
                nox_voice,
                nox_tone,
                publishing_mode,
            ),
            daemon=True,
        )
        self.preference_worker.start()

    def _save_preferences_worker(
        self,
        settings: Settings,
        gmail_label: str,
        host_count: int,
        solo_host: str,
        dialogue_style: str,
        dalia_voice: str,
        dalia_tone: str,
        nox_voice: str,
        nox_tone: str,
        publishing_mode: str,
    ) -> None:
        verified = False
        try:
            client = GmailClient(settings)
            if client.is_authenticated():
                client.verify_label(gmail_label)
                verified = True
            save_preferences(
                self.config_path,
                gmail_label=gmail_label,
                host_count=host_count,
                solo_name=solo_host,
                dialogue_style=dialogue_style,
                primary_voice_id=dalia_voice,
                primary_tone_id=dalia_tone,
                secondary_voice_id=nox_voice,
                secondary_tone_id=nox_tone,
                publishing_mode=publishing_mode,
            )
        except Exception as exc:
            self.command_queue.put(("preferences_error", str(exc)))
            return
        self.command_queue.put(("preferences_saved", verified))

    def _set_preferences_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        readonly = "disabled" if busy else "readonly"
        self.gmail_label_entry.configure(state=state)
        self.host_count_combo.configure(state=readonly)
        self.solo_host_combo.configure(state=readonly)
        self.dialogue_style_combo.configure(state=readonly)
        self.dalia_personality_combo.configure(state=readonly)
        self.dalia_tone_combo.configure(state=readonly)
        self.nox_personality_combo.configure(state=readonly)
        self.nox_tone_combo.configure(state=readonly)
        self.publishing_mode_combo.configure(state=readonly)
        self.preferences_save_button.configure(state=state)
        if not busy:
            self._update_host_controls()

    def _open_calendar(self) -> None:
        try:
            selected = datetime.strptime(self.episode_date.get(), "%Y-%m-%d").date()
        except ValueError:
            selected = datetime.now().astimezone().date()

        popup = tk.Toplevel(self.root)
        popup.title("Choose newsletter date")
        popup.configure(background=PALETTE["black"])
        popup.resizable(False, False)
        popup.transient(self.root)
        popup.grab_set()
        shown_year = selected.year
        shown_month = selected.month

        header = tk.Frame(popup, background=PALETTE["black"], padx=10, pady=10)
        header.pack(fill="x")
        grid = tk.Frame(popup, background=PALETTE["panel"], padx=10, pady=10)
        grid.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        month_label = tk.Label(
            header,
            background=PALETTE["black"],
            foreground=PALETTE["bright"],
            font=("Cascadia Mono", 10, "bold"),
            width=21,
        )
        month_label.pack(side="left", padx=6)

        def choose(day_number: int) -> None:
            self.episode_date.set(
                f"{shown_year:04d}-{shown_month:02d}-{day_number:02d}"
            )
            popup.destroy()

        def draw() -> None:
            for child in grid.winfo_children():
                child.destroy()
            month_label.configure(
                text=f"{calendar.month_name[shown_month].upper()} {shown_year}"
            )
            weekdays = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
            for column, weekday in enumerate(weekdays):
                tk.Label(
                    grid,
                    text=weekday,
                    background=PALETTE["panel"],
                    foreground=PALETTE["muted"],
                    font=("Cascadia Mono", 7, "bold"),
                    width=4,
                    pady=4,
                ).grid(row=0, column=column)
            for row_number, week in enumerate(
                calendar.Calendar(firstweekday=0).monthdayscalendar(
                    shown_year,
                    shown_month,
                ),
                start=1,
            ):
                for column, day_number in enumerate(week):
                    if day_number == 0:
                        tk.Label(
                            grid,
                            text="",
                            background=PALETTE["panel"],
                            width=4,
                        ).grid(row=row_number, column=column, padx=2, pady=2)
                        continue
                    is_selected = (
                        shown_year == selected.year
                        and shown_month == selected.month
                        and day_number == selected.day
                    )
                    tk.Button(
                        grid,
                        text=str(day_number),
                        command=lambda value=day_number: choose(value),
                        background=(
                            PALETTE["amber"] if is_selected else PALETTE["panel_alt"]
                        ),
                        foreground=PALETTE["black"] if is_selected else PALETTE["ink"],
                        activebackground=PALETTE["rust"],
                        activeforeground=PALETTE["cream"],
                        font=("Cascadia Mono", 8, "bold"),
                        relief="flat",
                        width=4,
                        pady=5,
                        cursor="hand2",
                    ).grid(row=row_number, column=column, padx=2, pady=2)

        def change_month(delta: int) -> None:
            nonlocal shown_year, shown_month
            shown_month += delta
            if shown_month < 1:
                shown_year -= 1
                shown_month = 12
            elif shown_month > 12:
                shown_year += 1
                shown_month = 1
            draw()

        tk.Button(
            header,
            text="◀",
            command=lambda: change_month(-1),
            background=PALETTE["panel_alt"],
            foreground=PALETTE["bright"],
            activebackground=PALETTE["rust"],
            activeforeground=PALETTE["cream"],
            relief="flat",
            width=3,
        ).pack(side="left")
        tk.Button(
            header,
            text="▶",
            command=lambda: change_month(1),
            background=PALETTE["panel_alt"],
            foreground=PALETTE["bright"],
            activebackground=PALETTE["rust"],
            activeforeground=PALETTE["cream"],
            relief="flat",
            width=3,
        ).pack(side="right")
        draw()
        popup.update_idletasks()
        popup.geometry(
            f"+{self.root.winfo_rootx() + 90}+{self.root.winfo_rooty() + 150}"
        )

    def _validate_date(self, value: str) -> str | None:
        try:
            return (
                datetime.strptime(
                    value.strip(),
                    "%Y-%m-%d",
                )
                .date()
                .isoformat()
            )
        except ValueError:
            messagebox.showerror(
                "Invalid date",
                "Choose a newsletter date from the calendar.",
                parent=self.root,
            )
            return None

    def run_yesterday(self) -> None:
        self.episode_date.set(previous_local_day())

    def run_or_stop(self) -> None:
        if self.process is not None:
            self.cancel_run()
        else:
            self.run_selected_date()

    def run_selected_date(self) -> None:
        episode_date = self._validate_date(self.episode_date.get())
        if not episode_date:
            return
        command = build_run_command(
            sys.executable,
            self.config_path,
            episode_date,
            local_only=self.local_only.get(),
        )
        self._start(
            command,
            f"GENERATING // {_date_with_weekday(episode_date)}",
            episode_date=episode_date,
        )

    def publish_selected_date(self) -> None:
        episode_date = self._validate_date(self.episode_date.get())
        if not episode_date:
            return
        if not messagebox.askyesno(
            "Publish private episode?",
            (
                f"Publish the completed {episode_date} episode to the configured "
                "secret Firebase RSS feed?"
            ),
            parent=self.root,
        ):
            return
        self._start(
            build_publish_command(
                sys.executable,
                self.config_path,
                episode_date,
            ),
            f"PUBLISHING // {_date_with_weekday(episode_date)}",
            episode_date=episode_date,
        )

    def configure_apple_publishing(self) -> None:
        current_project = ""
        if self.current_settings and "REPLACE_" not in self.current_settings.firebase.project_id:
            current_project = self.current_settings.firebase.project_id
        if (
            self.current_settings
            and self.current_settings.firebase.publish_enabled
            and not messagebox.askyesno(
                "Change the publishing project?",
                (
                    "Reconfiguring will immediately disable uploads until the new "
                    "project's Spark status is confirmed. Continue?"
                ),
                parent=self.root,
            )
        ):
            return
        project_id = simpledialog.askstring(
            "Configure Apple private publishing",
            (
                "First create a dedicated Firebase project on the free Spark plan "
                "with Analytics and billing disabled.\n\n"
                "Enter its exact Project ID:"
            ),
            initialvalue=current_project,
            parent=self.root,
        )
        if project_id is None:
            return
        try:
            result = configure_private_publishing(self.config_path, project_id)
            self._refresh_configuration()
        except (OSError, ValueError) as exc:
            messagebox.showerror(
                "Publishing setup could not be saved",
                str(exc),
                parent=self.root,
            )
            return
        secret_note = (
            "A new private feed secret was generated and stored in your operating "
            "system credential vault."
            if result.created_new_secret
            else "Your existing private feed secret was preserved in the credential vault."
        )
        messagebox.showinfo(
            "Firebase project saved",
            (
                f"Project: {result.project_id}\n"
                f"Host: {result.base_url}\n\n"
                f"{secret_note}\n\n"
                "Publishing remains OFF. Next choose Firebase Sign-in, verify the "
                "project still says Spark with no billing, then choose Confirm "
                "Spark + Enable."
            ),
            parent=self.root,
        )

    def sign_in_firebase(self) -> None:
        script = self.project_dir / "scripts" / "authenticate-firebase.ps1"
        if not script.exists():
            messagebox.showerror(
                "Firebase sign-in unavailable",
                f"Missing sign-in helper: {script}",
                parent=self.root,
            )
            return
        if not messagebox.askokcancel(
            "Firebase sign-in",
            (
                "A temporary PowerShell window will open because Firebase sign-in "
                "is interactive. Use the Google account that owns the dedicated "
                "Spark project. The helper enforces optional Firebase CLI telemetry "
                "and Gemini features off. Close the window after it reports success."
            ),
            parent=self.root,
        ):
            return
        try:
            powershell = (
                Path(os.environ.get("WINDIR", "C:/Windows"))
                / "System32"
                / "WindowsPowerShell"
                / "v1.0"
                / "powershell.exe"
            )
            subprocess.Popen(
                [
                    str(powershell),
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                ],
                cwd=self.project_dir,
                env=sanitized_environment(
                    dict(os.environ),
                    self.project_dir,
                ),
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as exc:
            messagebox.showerror(
                "Firebase sign-in could not start",
                str(exc),
                parent=self.root,
            )

    def confirm_spark_and_enable(self) -> None:
        if not self.current_settings:
            return
        if not messagebox.askyesno(
            "Zero-cost confirmation",
            (
                "Enable private publishing only if all three statements are true:\n\n"
                "• Firebase Console shows the Spark plan.\n"
                "• No Cloud Billing account is linked.\n"
                "• You will not upgrade this project to Blaze.\n\n"
                "Have you personally verified all three now?"
            ),
            parent=self.root,
        ):
            return
        try:
            settings = load_settings(self.config_path)
            write_spark_confirmation(settings)
            enable_private_publishing(self.config_path)
            self._refresh_configuration()
        except (CostSafetyError, OSError, ValueError) as exc:
            messagebox.showerror(
                "Publishing remains disabled",
                str(exc),
                parent=self.root,
            )
            return
        messagebox.showinfo(
            "Private publishing enabled",
            (
                "The safety checks passed. Start with a manual publication of a "
                "reviewed episode. The app will verify the live Apple RSS feed and "
                "audio before marking it published."
            ),
            parent=self.root,
        )

    def run_doctor(self) -> None:
        self._start(
            [
                sys.executable,
                "-m",
                "audiodigest",
                "--config",
                str(self.config_path),
                "doctor",
            ],
            "SYSTEM DIAGNOSTIC",
        )

    def sign_in_gmail(self) -> None:
        self._start(
            [
                sys.executable,
                "-m",
                "audiodigest",
                "--config",
                str(self.config_path),
                "authenticate-gmail",
            ],
            "GOOGLE AUTHORIZATION",
        )

    def sign_out_gmail(self) -> None:
        if not messagebox.askyesno(
            "Disconnect Google account?",
            (
                "Revoke Gmail authorization at Google and remove the saved "
                "token and account identity from Windows?"
            ),
            parent=self.root,
        ):
            return
        self._start(
            [
                sys.executable,
                "-m",
                "audiodigest",
                "--config",
                str(self.config_path),
                "logout-gmail",
            ],
            "DISCONNECTING GOOGLE",
        )

    def _start(
        self,
        command: list[str],
        label: str,
        *,
        episode_date: str = "",
    ) -> None:
        if self.process is not None:
            messagebox.showinfo(
                "The Daily Nexus is busy",
                "Wait for the current action to finish or press Stop.",
                parent=self.root,
            )
            return
        if not self.config_path.exists():
            messagebox.showerror(
                "Setup required",
                "config.toml is missing.",
                parent=self.root,
            )
            return
        self._show_mode("gen")
        self._clear_output()
        self._append_output(f"{label}\n\n")
        self.status.set(label)
        self.active_run_date = episode_date
        self.runtime_state.set(
            f"RUNNING // {_date_with_weekday(episode_date)}"
            if episode_date
            else "RUNNING"
        )
        self.action_started_at = datetime.now()
        self.current_stage = 0
        self.footer_progress["value"] = 1
        self._set_running(True)
        self.worker = threading.Thread(
            target=self._run_command,
            args=(command,),
            daemon=True,
        )
        self.worker.start()

    def _run_command(self, command: list[str]) -> None:
        self.runtime_dir.joinpath("logs").mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = self.runtime_dir / "logs" / f"launcher-{stamp}.log"
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            with log_path.open("w", encoding="utf-8") as log:
                self.process = subprocess.Popen(
                    command,
                    cwd=self.project_dir,
                    env=sanitized_environment(
                        dict(os.environ),
                        self.project_dir,
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creation_flags,
                )
                if self.process.stdout is None:
                    raise RuntimeError("The subprocess output stream is unavailable.")
                for line in self.process.stdout:
                    log.write(line)
                    log.flush()
                    self.command_queue.put(("line", line))
                return_code = self.process.wait()
        except Exception as exc:
            self.command_queue.put(("line", f"\nLauncher error: {exc}\n"))
            return_code = 1
        finally:
            self.process = None
        self.command_queue.put(("done", (return_code, log_path)))

    def _poll_queue(self) -> None:
        try:
            while True:
                event, payload = self.command_queue.get_nowait()
                if event == "line":
                    line = str(payload)
                    self._append_output(line)
                    match = STAGE_PATTERN.match(line.strip())
                    if match:
                        self.current_stage = int(match.group(1))
                        self.total_stages = int(match.group(2))
                        prefix = (
                            f"{_date_with_weekday(self.active_run_date)} // "
                            if self.active_run_date
                            else ""
                        )
                        self.status.set(f"{prefix}{match.group(3).upper()}")
                elif event == "gmail_identity":
                    self.gmail_account.set(str(payload))
                    self.gmail_status.set("Gmail read-only // Credentials secured by Windows.")
                elif event == "gmail_identity_error":
                    self.gmail_status.set(f"Connected; identity refresh failed: {payload}")
                elif event == "preferences_saved":
                    self._set_preferences_busy(False)
                    self._refresh_configuration()
                    self.preference_status.set(
                        "Saved // Gmail label verified in the connected account."
                        if bool(payload)
                        else "Saved locally // Sign in to verify the Gmail label."
                    )
                elif event == "preferences_error":
                    self._set_preferences_busy(False)
                    self.preference_status.set("Preferences were not changed.")
                    messagebox.showerror(
                        "Could not save preferences",
                        str(payload),
                        parent=self.root,
                    )
                elif event == "playback_prepared":
                    self.playback_worker = None
                    self.play_button.configure(state="normal")
                    self._set_playback_controls_enabled(True)
                    request_id, episode_date, prepared = payload
                    if request_id != self.playback_request_id:
                        continue
                    try:
                        self.player.play_prepared(prepared)
                    except PlaybackError as exc:
                        messagebox.showerror(
                            "Playback failed",
                            str(exc),
                            parent=self.root,
                        )
                        continue
                    self.playback_paused = False
                    self.playback_is_playing = True
                    self.playback_active = True
                    self._set_pause_visual(False)
                    if self.pending_playback_seek_ms is not None:
                        try:
                            self.player.seek_ms(self.pending_playback_seek_ms)
                        except PlaybackError:
                            pass
                        self.pending_playback_seek_ms = None
                    dated_title = _episode_date_title(str(episode_date))
                    self.mini_play_title.set(
                        f"THE DAILY NEXUS // {dated_title}"
                    )
                    self.play_detail.set(
                        f"{_date_with_weekday(str(episode_date))} // PLAYING // "
                        "PITCH-PRESERVED "
                        f"{self.playback_speed_choice.get()}"
                    )
                    self._refresh_mini_player_visibility()
                elif event == "playback_error":
                    self.playback_worker = None
                    self.play_button.configure(state="normal")
                    self._set_playback_controls_enabled(True)
                    request_id, detail = payload
                    if request_id != self.playback_request_id:
                        continue
                    messagebox.showerror(
                        "Playback failed",
                        str(detail),
                        parent=self.root,
                    )
                elif event == "playback_speed_prepared":
                    self.playback_worker = None
                    self._set_playback_controls_enabled(True)
                    request_id, speed, prepared = payload
                    if request_id != self.playback_request_id:
                        continue
                    try:
                        self.player.switch_prepared(prepared)
                    except PlaybackError as exc:
                        messagebox.showerror(
                            "Speed unavailable",
                            str(exc),
                            parent=self.root,
                        )
                        continue
                    self.play_detail.set(
                        f"PITCH-PRESERVED PLAYBACK // {float(speed):.2f}x"
                    )
                elif event == "playback_speed_error":
                    self.playback_worker = None
                    self._set_playback_controls_enabled(True)
                    request_id, detail = payload
                    if request_id != self.playback_request_id:
                        continue
                    messagebox.showerror(
                        "Speed unavailable",
                        str(detail),
                        parent=self.root,
                    )
                elif event == "done":
                    return_code, log_path = payload
                    completed_date = self.active_run_date
                    self._set_running(False)
                    if return_code == 0:
                        self.runtime_state.set(
                            f"COMPLETE // {_date_with_weekday(completed_date)}"
                            if completed_date
                            else "COMPLETE"
                        )
                        self.status.set(
                            f"{_date_with_weekday(completed_date)} // "
                            "ACTION COMPLETED SUCCESSFULLY"
                            if completed_date
                            else "ACTION COMPLETED SUCCESSFULLY"
                        )
                        self.footer_progress["value"] = 100
                        self._append_output(f"\nFinished. Log: {log_path}\n")
                    else:
                        self.runtime_state.set(
                            f"FAILED // {_date_with_weekday(completed_date)}"
                            if completed_date
                            else "FAILED"
                        )
                        self.status.set(
                            f"{_date_with_weekday(completed_date)} // "
                            "ACTION FAILED // REVIEW DETAILS"
                            if completed_date
                            else "ACTION FAILED // REVIEW DETAILS"
                        )
                        self._append_output(f"\nLog: {log_path}\n")
                    self.action_started_at = None
                    self.active_run_date = ""
                    self._refresh_configuration()
                    self._refresh_library()
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _runtime_tick(self) -> None:
        if self.action_started_at and self.process is not None:
            elapsed = (datetime.now() - self.action_started_at).total_seconds()
            remaining = max(0, self.estimated_seconds - elapsed)
            self.elapsed_status.set(f"ELAPSED {_clock_text(elapsed)}")
            self.estimate_status.set(f"EST. REMAINING {_clock_text(remaining)}")
            elapsed_progress = min(
                94,
                (elapsed / max(1, self.estimated_seconds)) * 100,
            )
            stage_progress = (
                ((self.current_stage - 1) / max(1, self.total_stages)) * 100
                if self.current_stage
                else 1
            )
            self.footer_progress["value"] = max(
                elapsed_progress,
                stage_progress,
            )
        elif self.action_started_at is None:
            self.elapsed_status.set("ELAPSED 00:00:00")
            self.estimate_status.set(f"TYPICAL RUN {_clock_text(self.estimated_seconds)}")
        self.root.after(1000, self._runtime_tick)

    def refresh_runtime_status(self) -> None:
        if self.process is not None and self.action_started_at:
            elapsed = (datetime.now() - self.action_started_at).total_seconds()
            active_label = (
                _date_with_weekday(self.active_run_date)
                if self.active_run_date
                else "SYSTEM"
            )
            self.status.set(
                "RUNNING // "
                f"{active_label} // "
                f"{_clock_text(elapsed)} // "
                f"STAGE {self.current_stage or '?'} OF {self.total_stages}"
            )
            return
        if self.database is None:
            self.status.set("STATUS DATABASE UNAVAILABLE")
            return
        episode_date = self._validate_date(self.episode_date.get())
        if not episode_date:
            return
        selected_day = datetime.strptime(episode_date, "%Y-%m-%d").date()
        record = self.database.episode_for_date(selected_day)
        if record:
            self.status.set(
                f"ARCHIVE // {_date_with_weekday(episode_date)} // "
                f"{record['status'].upper()}"
            )
            return
        run = self.database.run_for_date(selected_day)
        if run and run["status"] == "running":
            self.status.set(
                f"INTERRUPTED // {_date_with_weekday(episode_date)} // "
                "NO GENERATOR PROCESS IS ACTIVE"
            )
        elif run:
            self.status.set(
                f"LAST RUN // {_date_with_weekday(episode_date)} // "
                f"{run['status'].upper()}"
            )
        else:
            self.status.set(f"NO RUN FOUND // {_date_with_weekday(episode_date)}")

    def cancel_run(self) -> None:
        process = self.process
        if process is None:
            return
        if not messagebox.askyesno(
            "Stop current action?",
            "The existing feed and last completed episode will remain unchanged.",
            parent=self.root,
        ):
            return
        try:
            subprocess.run(
                [
                    str(Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                check=False,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.status.set("STOPPING")
        except OSError:
            process.terminate()

    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        if running:
            self.footer_activity.start(9)
        else:
            self.footer_activity.stop()
            self.footer_activity["value"] = 0
        self.run_yesterday_button.configure(state=state)
        self.date_entry.configure(state=state)
        self.run_button.configure(
            text="STOP" if running else "RUN",
            style="ActionQuiet.TButton" if running else "Nexus.TButton",
            state="normal",
        )
        self.gmail_sign_in_button.configure(state=state)
        self.gmail_sign_out_button.configure(state=state)
        self.publish_button.configure(
            state=(
                "disabled"
                if running
                else (
                    "normal"
                    if self.current_settings and self.current_settings.firebase.publish_enabled
                    else "disabled"
                )
            )
        )
        publishing_configured = bool(
            self.current_settings
            and self.current_settings.firebase.project_id
            and "REPLACE_" not in self.current_settings.firebase.project_id
            and self.current_settings.firebase.base_url
            and len(self.current_settings.firebase.secret_path) >= 32
        )
        for button in (
            self.publishing_configure_button,
            self.firebase_sign_in_button,
            self.publishing_enable_button,
            self.copy_feed_button,
        ):
            button.configure(state="disabled" if running else "normal")
        if not running:
            self.firebase_sign_in_button.configure(
                state="normal" if publishing_configured else "disabled"
            )
            self.publishing_enable_button.configure(
                state=(
                    "normal"
                    if publishing_configured
                    and self.current_settings
                    and not self.current_settings.firebase.publish_enabled
                    else "disabled"
                )
            )
            self.copy_feed_button.configure(
                state=(
                    "normal"
                    if self.current_settings
                    and self.current_settings.firebase.publish_enabled
                    else "disabled"
                )
            )
        self._set_preferences_busy(running)

    def _clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _append_output(self, value: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", value)
        self.output.see("end")
        self.output.configure(state="disabled")

    def _refresh_library(self) -> None:
        if self.database is None:
            return
        try:
            self.episode_records = self.database.list_episodes()
        except Exception:
            return
        self.play_list.delete(0, "end")
        for record in self.episode_records:
            minutes = int(float(record["duration_seconds"]) // 60)
            self.play_list.insert(
                "end",
                (
                    f"{_date_with_weekday(record['episode_date'])}  //  "
                    f"{minutes:02d} MIN  //  "
                    f"{record['status'].upper()}"
                ),
            )
        self.read_records = [
            record
            for record in self.episode_records
            if record.get("newspaper_path") and Path(record["newspaper_path"]).is_file()
        ]
        self.read_list.delete(0, "end")
        for record in self.read_records:
            self.read_list.insert(
                "end",
                f"{_date_with_weekday(record['episode_date'])}  //  NEXUS EDITION",
            )

    def _selected_record(self, listbox: tk.Listbox, records: list[dict]):
        selection = listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        return records[index] if index < len(records) else None

    def _select_play_episode(self, _event=None) -> None:
        record = self._selected_record(self.play_list, self.episode_records)
        if not record:
            return
        dated_title = _episode_date_title(record["episode_date"])
        self.play_title.set(f"THE DAILY NEXUS // {dated_title}")
        self.play_detail.set(
            f"{_date_with_weekday(record['episode_date'])} // "
            f"{record['status'].upper()}"
        )
        self.playback_transcript_segments = self._load_episode_transcript(record)
        self.playback_transcript_index = -1
        self._render_play_content(record)
        cover = self._load_image("assets/cover-retrofuture.jpg", (270, 270))
        if cover:
            self.play_cover.configure(image=cover, text="")
            self.play_cover.image = cover

    def _set_play_content_mode(self, mode: str) -> None:
        if mode not in {"references", "transcript"}:
            return
        self.play_content_mode.set(mode)
        for key, button in self.play_content_buttons.items():
            selected = key == mode
            button.configure(
                background=PALETTE["amber"] if selected else PALETTE["panel_alt"],
                foreground=PALETTE["black"] if selected else PALETTE["ink"],
            )
        record = self._selected_record(self.play_list, self.episode_records)
        if record:
            self._render_play_content(record)

    def _render_play_content(self, record: dict) -> None:
        if self.play_content_mode.get() == "transcript":
            self._render_transcript()
        else:
            self._render_references(record)

    def _render_references(self, record: dict) -> None:
        widget = self.play_notes
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        notes = record.get("show_notes", [])
        if not notes:
            widget.insert("end", "No references were stored.")
        for note_index, note in enumerate(notes):
            cursor = 0
            for url_index, match in enumerate(
                re.finditer(r"https://[^\s<>()]+", str(note))
            ):
                widget.insert("end", str(note)[cursor : match.start()])
                url = match.group(0).rstrip(".,);]")
                tag = f"reference-{note_index}-{url_index}"
                widget.insert("end", url, (tag,))
                widget.tag_configure(
                    tag,
                    foreground=PALETTE["bright"],
                    underline=True,
                )
                widget.tag_bind(
                    tag,
                    "<Button-1>",
                    lambda _event, address=url: self._open_safe_web_link(address),
                )
                widget.tag_bind(
                    tag,
                    "<Enter>",
                    lambda _event: widget.configure(cursor="hand2"),
                )
                widget.tag_bind(
                    tag,
                    "<Leave>",
                    lambda _event: widget.configure(cursor=""),
                )
                cursor = match.end()
            widget.insert("end", str(note)[cursor:])
            widget.insert("end", "\n\n")
        widget.configure(state="disabled")

    @staticmethod
    def _spoken_script_blocks(script: EpisodeScript) -> list[tuple[str, str, bool]]:
        lead_host = script.hosts[0]
        blocks: list[tuple[str, str, bool]] = [
            (lead_host, script.disclosure, False)
        ]
        blocks.extend((turn.host, turn.text, False) for turn in script.introduction)
        for section in script.sections:
            blocks.append((lead_host, section.name.value, True))
            blocks.extend((turn.host, turn.text, False) for turn in section.dialogue)
        blocks.extend((turn.host, turn.text, False) for turn in script.conclusion)
        blocks.extend((turn.host, turn.text, False) for turn in script.sign_off)
        return blocks

    def _load_episode_transcript(self, record: dict) -> list[dict[str, object]]:
        episode_dir = Path(str(record.get("audio_path", ""))).parent
        transcript_path = episode_dir / "transcript.json"
        if transcript_path.is_file():
            try:
                payload = json.loads(transcript_path.read_text(encoding="utf-8"))
                raw_segments = payload.get("segments", [])
                if isinstance(raw_segments, list):
                    result = [
                        item
                        for item in raw_segments
                        if isinstance(item, dict)
                        and isinstance(item.get("text"), str)
                        and isinstance(item.get("start_ms"), (int, float))
                        and isinstance(item.get("end_ms"), (int, float))
                    ]
                    if result:
                        return result
            except (OSError, json.JSONDecodeError):
                pass

        script_path = episode_dir / "script.json"
        if not script_path.is_file():
            return []
        try:
            script_payload = json.loads(script_path.read_text(encoding="utf-8"))
            raw_sections = script_payload.get("sections", [])
            persisted_order = tuple(
                str(item.get("name", "")).strip()
                for item in raw_sections
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            )
            script = EpisodeScript.from_dict(
                script_payload,
                section_order=persisted_order or None,
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        blocks = self._spoken_script_blocks(script)
        weights = [max(1, len(text.split())) for _host, text, _heading in blocks]
        total_weight = max(1, sum(weights))
        duration_ms = max(
            1,
            round(float(record.get("duration_seconds", 0) or 0) * 1000),
        )
        result: list[dict[str, object]] = []
        position = 0
        for index, ((host, text, is_heading), weight) in enumerate(
            zip(blocks, weights, strict=True)
        ):
            end = (
                duration_ms
                if index == len(blocks) - 1
                else min(duration_ms, position + round(duration_ms * weight / total_weight))
            )
            result.append(
                {
                    "host": host,
                    "text": text,
                    "start_ms": position,
                    "end_ms": end,
                    "is_heading": is_heading,
                }
            )
            position = end
        return result

    def _render_transcript(self) -> None:
        widget = self.play_notes
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.tag_configure(
            "transcript-host",
            foreground=PALETTE["amber"],
            font=("Cascadia Mono", 8, "bold"),
        )
        widget.tag_configure(
            "transcript-heading",
            foreground=PALETTE["bright"],
            font=("Bahnschrift Condensed", 11, "bold"),
            spacing1=5,
        )
        widget.tag_configure(
            "transcript-active",
            background=PALETTE["rust"],
            foreground=PALETTE["cream"],
        )
        if not self.playback_transcript_segments:
            widget.insert(
                "end",
                "A timed transcript was not stored for this older episode.",
            )
        for index, segment in enumerate(self.playback_transcript_segments):
            host = str(segment.get("host", "HOST")).upper()
            text_value = str(segment.get("text", "")).strip()
            is_heading = bool(segment.get("is_heading", False))
            start = widget.index("end-1c")
            if is_heading:
                widget.insert(
                    "end",
                    f"{text_value.upper()}\n",
                    ("transcript-heading",),
                )
            else:
                widget.insert("end", f"{host}\n", ("transcript-host",))
                widget.insert("end", f"{text_value}\n\n")
            end = widget.index("end-1c")
            tag = f"transcript-{index}"
            widget.tag_add(tag, start, end)
            widget.tag_bind(
                tag,
                "<Button-1>",
                lambda _event, selected=index: self._seek_transcript_segment(
                    selected
                ),
            )
            widget.tag_bind(
                tag,
                "<Enter>",
                lambda _event: widget.configure(cursor="hand2"),
            )
            widget.tag_bind(
                tag,
                "<Leave>",
                lambda _event: widget.configure(cursor=""),
            )
        widget.configure(state="disabled")
        try:
            position = self.player.position_ms()
        except PlaybackError:
            position = 0
        self._highlight_transcript(position)

    def _seek_transcript_segment(self, index: int) -> str:
        if not 0 <= index < len(self.playback_transcript_segments):
            return "break"
        position = int(
            self.playback_transcript_segments[index].get("start_ms", 0) or 0
        )
        if self.player.current_path is None:
            self.pending_playback_seek_ms = position
            self.play_selected_episode()
            return "break"
        try:
            self.player.seek_ms(position)
        except PlaybackError as exc:
            messagebox.showerror(
                "Seek unavailable",
                str(exc),
                parent=self.root,
            )
        else:
            self._highlight_transcript(position)
        return "break"

    def _highlight_transcript(self, position_ms: int) -> None:
        if self.play_content_mode.get() != "transcript":
            return
        active_index = -1
        for index, segment in enumerate(self.playback_transcript_segments):
            start = int(segment.get("start_ms", 0) or 0)
            end = int(segment.get("end_ms", start) or start)
            if start <= position_ms < end:
                active_index = index
                break
        if active_index == self.playback_transcript_index:
            return
        self.playback_transcript_index = active_index
        widget = self.play_notes
        widget.configure(state="normal")
        widget.tag_remove("transcript-active", "1.0", "end")
        if active_index >= 0:
            ranges = widget.tag_ranges(f"transcript-{active_index}")
            if len(ranges) == 2:
                widget.tag_add("transcript-active", ranges[0], ranges[1])
                widget.see(ranges[0])
        widget.configure(state="disabled")

    @staticmethod
    def _open_safe_web_link(url: str) -> None:
        if url.startswith("https://"):
            webbrowser.open(url)

    def play_selected_episode(self) -> None:
        record = self._selected_record(self.play_list, self.episode_records)
        if not record:
            messagebox.showinfo(
                "Select an episode",
                "Choose an episode from the local archive first.",
                parent=self.root,
            )
            return
        if self.playback_worker is not None and self.playback_worker.is_alive():
            return
        try:
            speed = float(self.playback_speed_value.get()) / 100
            self.player.request_speed(speed)
        except (PlaybackError, ValueError) as exc:
            messagebox.showerror("Speed unavailable", str(exc), parent=self.root)
            return
        self.playback_request_id += 1
        request_id = self.playback_request_id
        self.play_button.configure(state="disabled")
        self._set_playback_controls_enabled(False)
        self.play_detail.set(
            f"{_date_with_weekday(record['episode_date'])} // "
            "PREPARING PITCH-PRESERVED AUDIO"
        )
        self.playback_worker = threading.Thread(
            target=self._play_episode_worker,
            args=(
                Path(record["audio_path"]),
                record["episode_date"],
                speed,
                request_id,
            ),
            daemon=True,
        )
        self.playback_worker.start()

    def _play_episode_worker(
        self,
        audio_path: Path,
        episode_date: str,
        speed: float,
        request_id: int,
    ) -> None:
        try:
            prepared = self.player.prepare(audio_path, speed)
        except PlaybackError as exc:
            self.command_queue.put(("playback_error", (request_id, str(exc))))
            return
        self.command_queue.put(
            ("playback_prepared", (request_id, episode_date, prepared))
        )

    def pause_or_resume(self) -> None:
        if not self.playback_active:
            self.play_selected_episode()
            return
        try:
            if self.playback_paused:
                self.player.resume()
                self.playback_paused = False
            else:
                self.player.pause()
                self.playback_paused = True
            self._set_pause_visual(self.playback_paused)
            self._refresh_mini_player_visibility()
        except PlaybackError as exc:
            messagebox.showerror("Playback failed", str(exc), parent=self.root)

    def stop_playback(self) -> None:
        self.playback_request_id += 1
        try:
            self.player.stop()
        except PlaybackError:
            pass
        self.play_button.configure(state="normal")
        self._set_playback_controls_enabled(True)
        self.play_time.set("00:00 / 00:00")
        self.mini_play_time.set("00:00 / 00:00")
        self.play_progress["value"] = 0
        self.mini_progress["value"] = 0
        self.playback_paused = False
        self.playback_is_playing = False
        self.playback_active = False
        self.pending_playback_seek_ms = None
        self._set_pause_visual(False)
        self._refresh_mini_player_visibility()

    def _change_playback_speed(self, _event=None) -> None:
        try:
            speed = float(self.playback_speed_value.get()) / 100
        except ValueError as exc:
            messagebox.showerror("Speed unavailable", str(exc), parent=self.root)
            return
        if self.playback_worker is not None and self.playback_worker.is_alive():
            return
        try:
            self.player.request_speed(speed)
        except PlaybackError as exc:
            messagebox.showerror("Speed unavailable", str(exc), parent=self.root)
            return
        source = self.player.current_path
        if source is None:
            self.play_detail.set(f"PLAYBACK SPEED READY // {speed:.2f}x")
            return
        self.playback_request_id += 1
        request_id = self.playback_request_id
        self._set_playback_controls_enabled(False)
        self.play_detail.set("PRESERVING VOICE PITCH // PREPARING TEMPO")
        self.playback_worker = threading.Thread(
            target=self._change_playback_speed_worker,
            args=(source, speed, request_id),
            daemon=True,
        )
        self.playback_worker.start()

    def _change_playback_speed_worker(
        self,
        source: Path,
        speed: float,
        request_id: int,
    ) -> None:
        try:
            prepared: PreparedPlayback = self.player.prepare(source, speed)
        except PlaybackError as exc:
            self.command_queue.put(
                ("playback_speed_error", (request_id, str(exc)))
            )
            return
        self.command_queue.put(
            ("playback_speed_prepared", (request_id, speed, prepared))
        )

    def _preview_playback_speed(self, value: str) -> None:
        try:
            speed = round(float(value) / 100, 2)
        except ValueError:
            return
        self.playback_speed_choice.set(f"{speed:.2f}x")
        self.playback_speed_status.set(f"SPEED {speed:.2f}x")

    def _commit_playback_speed_scale(self, _event=None) -> None:
        self._preview_playback_speed(str(self.playback_speed_value.get()))
        self._change_playback_speed()

    def _set_playback_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for scale in getattr(self, "playback_speed_scales", []):
            scale.configure(state=state)

    def _set_pause_visual(self, paused: bool) -> None:
        text = "\u25b6" if paused else "\u275a\u275a"
        background = PALETTE["amber"] if paused else PALETTE["panel_alt"]
        foreground = PALETTE["black"] if paused else PALETTE["bright"]
        for button in (
            getattr(self, "pause_button", None),
            getattr(self, "mini_pause_button", None),
        ):
            if button is not None:
                button.configure(
                    text=text,
                    background=background,
                    foreground=foreground,
                )

    def _refresh_mini_player_visibility(self) -> None:
        if not hasattr(self, "mini_player"):
            return
        visible = self.playback_active and self.mode != "play"
        if visible and not self.mini_player.winfo_manager():
            self.mini_player.pack(fill="x", after=self.header)
        elif not visible and self.mini_player.winfo_manager():
            self.mini_player.pack_forget()

    def _change_playback_volume(self, value: str) -> None:
        try:
            volume = max(0, min(100, round(float(value))))
        except (PlaybackError, ValueError):
            return
        self.playback_volume_status.set(f"VOLUME {volume}%")
        try:
            self.player.set_volume(volume)
        except PlaybackError:
            return

    def _draw_wave_canvas(
        self,
        canvas: tk.Canvas,
        *,
        item_attribute: str,
        bar_count: int,
        playing: bool,
    ) -> None:
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        center = height / 2
        gap = width / (bar_count + 1)
        items: list[int] = getattr(self, item_attribute)
        if len(items) != bar_count:
            canvas.delete("wave")
            items = [
                canvas.create_line(
                    0,
                    center,
                    0,
                    center,
                    fill=PALETTE["line"],
                    width=2,
                    capstyle="round",
                    tags="wave",
                )
                for _index in range(bar_count)
            ]
            setattr(self, item_attribute, items)
        for index in range(bar_count):
            if playing:
                envelope = 0.34 + 0.66 * (
                    0.5
                    + 0.5
                    * math.sin((index * 0.48) + self.playback_wave_phase)
                )
                pulse = 0.62 + 0.38 * (
                    0.5
                    + 0.5
                    * math.sin((index * 0.19) - (self.playback_wave_phase * 0.63))
                )
                amplitude = 3 + (height * 0.34) * envelope * pulse
            else:
                amplitude = 2.5 + 1.5 * (
                    0.5
                    + 0.5 * math.sin((index * 0.42) + self.playback_wave_phase)
                )
            x = gap * (index + 1)
            canvas.coords(
                items[index],
                x,
                center - amplitude,
                x,
                center + amplitude,
            )
            canvas.itemconfigure(
                items[index],
                fill=(
                    PALETTE["bright"]
                    if playing and index % 3
                    else PALETTE["amber"] if playing else PALETTE["line"]
                ),
            )

    def _draw_playback_wave(self, playing: bool) -> None:
        if not hasattr(self, "play_wave"):
            return
        self.playback_wave_phase += 0.14 if playing else 0.035
        self._draw_wave_canvas(
            self.play_wave,
            item_attribute="playback_wave_items",
            bar_count=43,
            playing=playing,
        )
        if hasattr(self, "mini_wave"):
            self._draw_wave_canvas(
                self.mini_wave,
                item_attribute="mini_wave_items",
                bar_count=19,
                playing=playing,
            )

    def _playback_tick(self) -> None:
        playing = False
        try:
            mode = self.player.status()
            playing = mode == "playing"
            length = self.player.length_ms()
            position = self.player.position_ms()
            if length and not self.playback_seeking:
                time_text = (
                    f"{_clock_text(position / 1000)[3:]} / {_clock_text(length / 1000)[3:]}"
                )
                progress = min(
                    100,
                    (position / length) * 100,
                )
                self.play_time.set(time_text)
                self.mini_play_time.set(time_text)
                self.play_progress["value"] = progress
                self.mini_progress["value"] = progress
                self._highlight_transcript(position)
                if (
                    self.playback_active
                    and mode == "stopped"
                    and not self.playback_paused
                    and position >= max(0, length - 750)
                ):
                    self.playback_active = False
                    self._refresh_mini_player_visibility()
        except (PlaybackError, ValueError):
            pass
        self.playback_is_playing = playing
        self.root.after(250, self._playback_tick)

    def _wave_tick(self) -> None:
        self._draw_playback_wave(self.playback_is_playing)
        self.root.after(50, self._wave_tick)

    def _seek_fraction(self, event) -> float:
        widget = event.widget
        width = max(1, widget.winfo_width())
        return min(1.0, max(0.0, event.x / width))

    def _show_seek_preview(self, fraction: float) -> None:
        length = self.player.length_ms()
        if not length:
            return
        target = round(length * fraction)
        self.play_progress["value"] = fraction * 100
        self.mini_progress["value"] = fraction * 100
        time_text = (
            f"{_clock_text(target / 1000)[3:]} / {_clock_text(length / 1000)[3:]}"
        )
        self.play_time.set(time_text)
        self.mini_play_time.set(time_text)

    def _begin_playback_seek(self, event) -> str:
        self.playback_seeking = True
        self._show_seek_preview(self._seek_fraction(event))
        return "break"

    def _preview_playback_seek(self, event) -> str:
        if self.playback_seeking:
            self._show_seek_preview(self._seek_fraction(event))
        return "break"

    def _commit_playback_seek(self, event) -> str:
        fraction = self._seek_fraction(event)
        try:
            length = self.player.length_ms()
            if length:
                self.player.seek_ms(round(length * fraction))
        except PlaybackError as exc:
            messagebox.showerror("Seek unavailable", str(exc), parent=self.root)
        finally:
            self.playback_seeking = False
        return "break"

    def _select_read_episode(self, _event=None) -> None:
        record = self._selected_record(self.read_list, self.read_records)
        if not record:
            return
        preview_value = record.get("preview_path")
        preview_path = Path(preview_value) if preview_value else None
        candidates: list[Path] = []
        if preview_path:
            page_previews = sorted(preview_path.parent.glob("edition-[0-9]*.png"))
            if page_previews:
                candidates.extend(page_previews)
            elif preview_path.is_file():
                candidates.append(preview_path)
        self.read_preview_paths = candidates
        page_label = (
            f"{len(candidates)} PAGE{'S' if len(candidates) != 1 else ''}"
            if candidates
            else "PDF"
        )
        self.read_title.set(
            f"EDITION // {_date_with_weekday(record['episode_date'])} // {page_label}"
        )
        self.read_page_index = 0
        self.read_zoom = 1.0
        self.read_zoom_status.set("ZOOM 100%")
        if not candidates:
            self.read_page_status.set("PAGE -- / --")
            self.read_canvas.delete("all")
            self.read_canvas.create_text(
                20,
                20,
                anchor="nw",
                text="Preview unavailable. Use OPEN PDF.",
                fill=PALETTE["ink"],
                font=("Cascadia Mono", 10),
            )
            return
        self._render_read_preview()

    def _render_read_preview(self) -> None:
        if not self.read_preview_paths:
            return
        self.read_page_index = min(
            max(0, self.read_page_index),
            len(self.read_preview_paths) - 1,
        )
        preview_path = self.read_preview_paths[self.read_page_index]
        self.root.update_idletasks()
        width = max(400, self.read_canvas.winfo_width() - 30)
        height = max(500, self.read_canvas.winfo_height() - 30)
        try:
            from PIL import Image, ImageTk

            image = Image.open(preview_path).convert("RGB")
            image.thumbnail(
                (round(width * self.read_zoom), round(height * self.read_zoom)),
                Image.Resampling.LANCZOS,
            )
            self.read_preview_image = ImageTk.PhotoImage(image)
        except (ImportError, OSError) as exc:
            messagebox.showerror("Preview failed", str(exc), parent=self.root)
            return
        self.read_canvas.delete("all")
        canvas_width = max(1, self.read_canvas.winfo_width())
        image_x = max(canvas_width / 2, image.width / 2 + 10)
        self.read_canvas.create_image(
            image_x,
            10,
            anchor="n",
            image=self.read_preview_image,
        )
        self.read_canvas.configure(
            scrollregion=(0, 0, max(canvas_width, image.width + 20), image.height + 20)
        )
        self.read_page_status.set(
            f"PAGE {self.read_page_index + 1} / {len(self.read_preview_paths)}"
        )

    def _change_read_page(self, delta: int) -> None:
        if not self.read_preview_paths:
            return
        self.read_page_index = (
            self.read_page_index + delta
        ) % len(self.read_preview_paths)
        self.read_canvas.xview_moveto(0)
        self.read_canvas.yview_moveto(0)
        self._render_read_preview()

    def _change_read_zoom(self, delta: float) -> None:
        self.read_zoom = min(2.0, max(0.7, self.read_zoom + delta))
        self.read_zoom_status.set(f"ZOOM {round(self.read_zoom * 100)}%")
        self._render_read_preview()

    def _scroll_read_vertical(self, event) -> str:
        direction = -1 if event.delta > 0 else 1
        self.read_canvas.yview_scroll(direction * 3, "units")
        return "break"

    def _scroll_read_horizontal(self, event) -> str:
        direction = -1 if event.delta > 0 else 1
        self.read_canvas.xview_scroll(direction * 3, "units")
        return "break"

    def open_selected_pdf(self) -> None:
        record = self._selected_record(self.read_list, self.read_records)
        if record and record.get("newspaper_path"):
            self._open_path(Path(record["newspaper_path"]))

    def open_selected_episode_folder(self) -> None:
        record = self._selected_record(self.play_list, self.episode_records)
        if not record:
            messagebox.showinfo(
                "Select an episode",
                "Choose an episode from the local archive first.",
                parent=self.root,
            )
            return
        audio_value = str(record.get("audio_path", "")).strip()
        manifest_value = str(record.get("manifest_path", "")).strip()
        folder = (
            Path(audio_value).parent
            if audio_value
            else Path(manifest_value).parent
            if manifest_value
            else self.runtime_dir / "episodes" / str(record["episode_date"])
        )
        self._open_path(folder)

    def open_private_feed(self) -> None:
        settings = self.current_settings
        if (
            not settings
            or not settings.firebase.publish_enabled
            or not settings.firebase.secret_path
        ):
            messagebox.showinfo(
                "Private feed not configured",
                "Complete the Firebase Spark publishing setup first.",
                parent=self.root,
            )
            return
        url = f"{settings.firebase.base_url}/p/{settings.firebase.secret_path}/feed.xml"
        webbrowser.open(url)

    def copy_private_feed_url(self) -> None:
        settings = self.current_settings
        if (
            not settings
            or not settings.firebase.publish_enabled
            or not settings.firebase.secret_path
        ):
            messagebox.showinfo(
                "Private feed not configured",
                "Complete the Firebase Spark publishing setup first.",
                parent=self.root,
            )
            return
        url = f"{settings.firebase.base_url}/p/{settings.firebase.secret_path}/feed.xml"
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update()
        messagebox.showinfo(
            "Private Apple URL copied",
            (
                "The RSS URL is now on the clipboard. Treat it like a password. "
                "It will start working after the first successful publication."
            ),
            parent=self.root,
        )

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", value)
        widget.configure(state="disabled")

    def _open_path(self, path: Path, create: bool = False) -> None:
        try:
            resolved = path.expanduser().resolve()
            if create:
                resolved.mkdir(parents=True, exist_ok=True)
            if not resolved.exists():
                raise FileNotFoundError(resolved)
            os.startfile(str(resolved))  # type: ignore[attr-defined]  # noqa: S606
        except (AttributeError, OSError, ValueError) as exc:
            messagebox.showerror("Cannot open", str(exc), parent=self.root)

    def open_episodes(self) -> None:
        self._open_path(self.runtime_dir / "episodes", create=True)

    def open_logs(self) -> None:
        self._open_path(self.runtime_dir / "logs", create=True)

    def open_setup_guide(self) -> None:
        self._open_path(self.project_dir / "docs" / "SETUP.md")

    def open_publishing_guide(self) -> None:
        self._open_path(self.project_dir / "docs" / "PUBLISHING.md")

    def open_configuration(self) -> None:
        self._open_path(self.config_path)

    def _on_close(self) -> None:
        if self.process is not None:
            messagebox.showinfo(
                "The Daily Nexus is still running",
                "Stop the current action before closing the app.",
                parent=self.root,
            )
            return
        self.stop_playback()
        self.root.destroy()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open The Daily Nexus local app.")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path.cwd(),
        help="The Daily Nexus project directory",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    _set_windows_app_identity()
    if os.name == "nt":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        instance_handle = ctypes.windll.kernel32.CreateMutexW(
            None,
            False,
            "Local\\TheDailyNexusDesktopApp",
        )
        if not instance_handle or ctypes.windll.kernel32.GetLastError() == 183:
            root = Tk()
            root.withdraw()
            messagebox.showinfo(
                "The Daily Nexus is already open",
                "Use the existing app window. Only one signal console runs at a time.",
                parent=root,
            )
            root.destroy()
            return
    args = build_parser().parse_args(argv)
    root = Tk()
    DailyNexusApp(root, args.project_dir)
    root.mainloop()
