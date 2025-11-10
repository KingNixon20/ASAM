"""
ASA Dedicated Server Launcher (Windows)

Features:
- Auto-detects an "ASA Dedicated Server" folder in common locations
- Lets user browse for server folder or install (opens a download page)
- Lets user choose backup directory and set cluster ID
- Persists settings to %LOCALAPPDATA%\\ASA_Server_Launcher\\config.json
- After initial setup opens a Server Manager window with controls to start/stop the server and open folders

Usage: python launcher.py

This is a small, self-contained script that uses Tkinter and the standard library.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Optional
import time
import shlex
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import urllib.request
import zipfile
import socket
import struct
import re


class ToolTip:
    """Simple tooltip using a transient Toplevel; minimal, works on focus/enter events."""
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tipwin = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)
        widget.bind("<FocusIn>", self.show)
        widget.bind("<FocusOut>", self.hide)

    def show(self, _=None):
        if self.tipwin or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + 20
        self.tipwin = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = ttk.Label(tw, text=self.text, background="#ffffe0", relief=tk.SOLID, borderwidth=1, padding=4)
        lbl.pack()

    def hide(self, _=None):
        if self.tipwin:
            try:
                self.tipwin.destroy()
            except Exception:
                pass
            self.tipwin = None


def presets_path() -> Path:
    p = get_local_appdata_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p / "presets.json"


def load_custom_presets() -> dict:
    p = presets_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_custom_presets(d: dict):
    p = presets_path()
    try:
        p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    except Exception:
        pass


APP_NAME = "ASA_Server_Launcher"
CONFIG_FILENAME = "config.json"
DEFAULT_INSTALL_URL = "https://example.com/asa-dedicated-server-download"  # replace with real URL if known


def get_local_appdata_dir() -> Path:
    if platform.system() == "Windows":
        path = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if path:
            return Path(path) / APP_NAME
    # fallback to user home
    return Path.home() / f".{APP_NAME}"


def config_path() -> Path:
    d = get_local_appdata_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / CONFIG_FILENAME


def app_list_cache_path() -> Path:
    p = get_local_appdata_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p / "steam_app_list.json"


def fetch_steam_app_list(force: bool = False) -> list[dict]:
    """Fetches and caches the Steam GetAppList data. Returns a list of {'appid': int, 'name': str}.
    This can be large; we cache it in local appdata for reuse.
    """
    cache = app_list_cache_path()
    # TTL: 7 days
    ttl = 60 * 60 * 24 * 7
    if cache.exists() and not force:
        try:
            mtime = cache.stat().st_mtime
            if time.time() - mtime < ttl:
                data = json.loads(cache.read_text(encoding="utf-8"))
                return data.get("applist", {}).get("apps", [])
        except Exception:
            pass

    url = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            text = resp.read()
            ds = json.loads(text)
            # save raw to cache
            try:
                cache.write_text(json.dumps(ds), encoding="utf-8")
            except Exception:
                pass
            return ds.get("applist", {}).get("apps", [])
    except Exception:
        # fallback: try to read cache even if expired
        try:
            if cache.exists():
                ds = json.loads(cache.read_text(encoding="utf-8"))
                return ds.get("applist", {}).get("apps", [])
        except Exception:
            pass
    return []


def auto_guess_appid(target_path: Path, cluster_id: str | None = None) -> Optional[tuple[str, str]]:
    """Try to automatically guess the Steam AppID by matching the target folder name and cluster_id
    against the Steam app list. Returns (appid, name) or None.
    """
    # Prepare tokens from folder name and cluster id
    hints = []
    try:
        name = target_path.name
        hints.extend([t for t in name.replace('-', ' ').replace('_', ' ').split() if t])
    except Exception:
        name = ''
    if cluster_id:
        hints.extend([t for t in str(cluster_id).split() if t])

    hints = [h.lower() for h in hints if len(h) > 1]
    if not hints:
        # fallback: use folder basename entirely
        if name:
            hints = [name.lower()]

    apps = fetch_steam_app_list()
    if not apps:
        return None

    # Scoring: prefer entries that contain more hint tokens
    best = None
    best_score = -1
    for a in apps:
        aname = (a.get('name') or '').lower()
        if not aname:
            continue
        score = 0
        for h in hints:
            if h in aname:
                score += 10
        # prefer shorter name if equal
        score -= len(aname) / 100.0
        # boost exact phrase matches
        joined = ' '.join(hints)
        if joined and joined in aname:
            score += 20
        if score > best_score:
            best_score = score
            best = a

    if best and best_score > 0:
        return None
    # If nothing scored positively, return the top popular-looking app (best heuristic)
    # Choose first app with name containing any hint as fallback
    for a in apps:
        aname = (a.get('name') or '').lower()
        for h in hints:
            if h in aname:
                return None
    return None


class AppIDSearchDialog:
    """Dialog to search the Steam app list and pick an AppID."""

    def __init__(self, parent: tk.Tk | tk.Toplevel):
        self.parent = parent
        self.result: Optional[tuple[str, str]] = None
        self.win = tk.Toplevel(parent)
        self.win.title("Find Steam AppID")
        self.win.geometry("720x480")
        frm = ttk.Frame(self.win, padding=8)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Search for the app name (partial match):").pack(anchor=tk.W)
        self.query_var = tk.StringVar()
        q_entry = ttk.Entry(frm, textvariable=self.query_var, width=60)
        q_entry.pack(fill=tk.X, pady=6)
        q_entry.focus()

        list_frame = ttk.Frame(frm)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(list_frame)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(frm)
        btn_frame.pack(fill=tk.X, pady=8)
        ttk.Button(btn_frame, text="Use Selected", command=self._use_selected).pack(side=tk.RIGHT, padx=6)
        ttk.Button(btn_frame, text="Manual Entry", command=self._manual).pack(side=tk.RIGHT)

        # Load list in background
        self.apps: list[dict] = []
        threading.Thread(target=self._load_apps, daemon=True).start()
        # bind typing to filter
        self.query_var.trace_add("write", lambda *_: self._filter())

        self.win.transient(parent)
        self.win.grab_set()

    def _load_apps(self):
        # Fetch app list in background thread, but schedule UI updates on the main thread
        apps = fetch_steam_app_list()
        def finish_load():
            self.apps = apps
            # populate listbox with a few popular matches first
            try:
                self._filter()
            except Exception:
                pass

        try:
            # schedule on the dialog's event loop
            self.win.after(0, finish_load)
        except Exception:
            # if window is already closed, ignore
            pass

    def _filter(self):
        q = (self.query_var.get() or "").strip().lower()
        # Guard against listbox being destroyed while background tasks run
        if not hasattr(self, 'listbox'):
            return
        try:
            if not self.listbox.winfo_exists():
                return
        except Exception:
            return
        self.listbox.delete(0, tk.END)
        if not q:
            # show a short sample to avoid huge list
            for a in (self.apps or [])[:200]:
                self.listbox.insert(tk.END, f"{a.get('name')} ({a.get('appid')})")
            return
        count = 0
        for a in (self.apps or []):
            name = (a.get("name") or "").lower()
            if q in name:
                self.listbox.insert(tk.END, f"{a.get('name')} ({a.get('appid')})")
                count += 1
                if count >= 500:
                    break

    def _use_selected(self):
        sel = self.listbox.curselection()
        if not sel:
            messagebox.showinfo("Select", "Please select an entry from the list.")
            return
        text = self.listbox.get(sel[0])
        # parse name (appid)
        if text.endswith(")") and "(" in text:
            name = text.rsplit(" (", 1)[0]
            appid = text.rsplit(" (", 1)[1][:-1]
            self.result = (appid, name)
            self.win.destroy()

    def _manual(self):
        self.win.destroy()
        appid = simpledialog.askstring("Steam AppID", "Enter the Steam AppID for the dedicated server:")
        if appid:
            self.result = (appid, "(manual)")


class CreateServerDialog:
    """Modal dialog to create a new server entry for the Cluster list."""
    def __init__(self, parent: tk.Tk | tk.Toplevel):
        self.parent = parent
        self.result: Optional[dict] = None
        self.win = tk.Toplevel(parent)
        self.win.title("Create Server")
        self.win.geometry("640x240")
        frm = ttk.Frame(self.win, padding=8)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Server name:").grid(row=0, column=0, sticky=tk.W)
        self.name_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.name_var, width=48).grid(row=0, column=1, columnspan=3, sticky=tk.W, pady=6)

        ttk.Label(frm, text="Server folder:").grid(row=1, column=0, sticky=tk.W)
        self.path_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.path_var, width=48).grid(row=1, column=1, sticky=tk.W, pady=6)
        ttk.Button(frm, text="Browse...", command=self.browse_path).grid(row=1, column=2, padx=6)

        ttk.Label(frm, text="Cluster ID:").grid(row=2, column=0, sticky=tk.W)
        self.cluster_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.cluster_var, width=36).grid(row=2, column=1, sticky=tk.W, pady=6)

        ttk.Label(frm, text="Backup folder:").grid(row=3, column=0, sticky=tk.W)
        self.backup_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.backup_var, width=48).grid(row=3, column=1, sticky=tk.W, pady=6)
        ttk.Button(frm, text="Browse...", command=self.browse_backup).grid(row=3, column=2, padx=6)

        btn_frame = ttk.Frame(frm)
        btn_frame.grid(row=4, column=0, columnspan=4, pady=(12,0))
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.RIGHT, padx=6)
        ttk.Button(btn_frame, text="Cancel", command=self._cancel).pack(side=tk.RIGHT)

        self.win.transient(parent)
        self.win.grab_set()
        try:
            self.win.focus_force()
        except Exception:
            pass

    def browse_path(self):
        p = filedialog.askdirectory(title="Select server folder")
        if p:
            self.path_var.set(p)

    def browse_backup(self):
        p = filedialog.askdirectory(title="Select backup folder")
        if p:
            self.backup_var.set(p)

    def _save(self):
        name = (self.name_var.get() or "").strip()
        path = (self.path_var.get() or "").strip()
        if not name:
            messagebox.showerror("Validation", "Please enter a server name.")
            return
        if not path:
            messagebox.showerror("Validation", "Please select a server folder.")
            return
        # Verify the folder contains a server executable; if not, offer to auto-install
        ppath = Path(path)
        exe = pick_exe_in_folder(ppath) if ppath.exists() else None
        if not exe:
            answer = messagebox.askyesno("Install server", f"No server executable was found in {path}.\nWould you like the launcher to install the server into this folder using SteamCMD? (recommended)")
            if answer:
                # ensure target folder exists
                try:
                    ppath.mkdir(parents=True, exist_ok=True)
                except Exception:
                    messagebox.showerror('Folder error', f'Could not create target folder: {path}')
                    return
                # locate steamcmd (local first, then PATH)
                steamcmd = None
                try:
                    found_local = list(ppath.glob('**/steamcmd.exe'))
                    if found_local:
                        steamcmd = str(found_local[0])
                except Exception:
                    steamcmd = None
                if not steamcmd:
                    steamcmd = shutil.which('steamcmd')
                # if still not found, attempt to download steamcmd into the folder
                if not steamcmd:
                    try:
                        steamcmd_url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
                        dl = ppath / 'steamcmd.zip'
                        with urllib.request.urlopen(steamcmd_url, timeout=30) as resp, open(dl, 'wb') as out:
                            out.write(resp.read())
                        with zipfile.ZipFile(dl, 'r') as zf:
                            zf.extractall(path=ppath)
                        try:
                            dl.unlink()
                        except Exception:
                            pass
                        # try to find steamcmd again
                        found_local = list(ppath.glob('**/steamcmd.exe'))
                        if found_local:
                            steamcmd = str(found_local[0])
                    except Exception:
                        # ask user if they have steamcmd elsewhere
                        if not messagebox.askyesno('SteamCMD', 'Failed to download SteamCMD automatically. Do you have SteamCMD installed and want to continue?'):
                            return
                        steamcmd = shutil.which('steamcmd')
                if not steamcmd:
                    messagebox.showwarning('SteamCMD missing', 'SteamCMD not found. Please install SteamCMD or retry Create and provide a folder containing the server.')
                    return
                # run steamcmd to install appid 2430930 into the folder
                try:
                    cmd = [steamcmd, '+login', 'anonymous', '+force_install_dir', str(ppath), '+app_update', '2430930', 'validate', '+quit']
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                except Exception as e:
                    messagebox.showerror('Install failed', f'Failed to start SteamCMD installer: {e}')
                    return
                # show progress window and stream output
                outwin = tk.Toplevel()
                outwin.title('Installing server')
                txt = tk.Text(outwin, height=16, width=80)
                txt.pack(fill=tk.BOTH, expand=True)
                def _reader():
                    try:
                        assert proc.stdout is not None
                        for line in proc.stdout:
                            try:
                                txt.insert(tk.END, line)
                                txt.see(tk.END)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        proc.wait()
                        btn = ttk.Button(outwin, text='Close', command=outwin.destroy)
                        btn.pack()
                    except Exception:
                        pass
                threading.Thread(target=_reader, daemon=True).start()
                # wait for the installer dialog to finish
                self.win.wait_window(outwin)
                # re-check for exe
                exe = pick_exe_in_folder(ppath)
                if not exe:
                    messagebox.showwarning('Missing exe', 'Install completed but no server executable was found. You may need to point to the correct folder or install the correct appid.')
                    return
        self.result = {
            'name': name,
            'path': path,
            'cluster_id': self.cluster_var.get().strip(),
            'backup_dir': self.backup_var.get().strip(),
            'status': 'stopped',
            'server_settings': {},
        }
        try:
            self.win.destroy()
        except Exception:
            pass

    def _cancel(self):
        self.result = None
        try:
            self.win.destroy()
        except Exception:
            pass


def load_config() -> dict:
    p = config_path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_config(cfg: dict) -> None:
    p = config_path()
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def find_candidate_paths() -> list[Path]:
    # Build a list of reasonable places to check for a server installation
    candidates: list[Path] = []
    user = os.getenv("USERNAME") or os.getenv("USER")

    # Common Program Files
    for base in [os.getenv("ProgramFiles"), os.getenv("ProgramFiles(x86)")]:
        if base:
            candidates.append(Path(base))

    # User folders
    if user:
        candidates.append(Path.home())
        candidates.append(Path.home() / "Documents")

    # OneDrive (common on Windows machines)
    onedrive = os.getenv("OneDrive")
    if onedrive:
        candidates.append(Path(onedrive))

    # Common workspace / downloads
    downloads = Path.home() / "Downloads"
    if downloads.exists():
        candidates.append(downloads)

    # The current workspace (script folder)
    candidates.append(Path(__file__).resolve().parent)

    # Filter unique and existing
    unique = []
    seen = set()
    for p in candidates:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen and p.exists():
            seen.add(key)
            unique.append(p)
    return unique


def auto_search_for_server() -> Optional[Path]:
    """Look for a folder named like 'ASA Dedicated Server' or similar in a few candidate locations.
    This is intentionally lightweight and won't do a deep recursive scan across whole drives.
    """
    candidates = find_candidate_paths()
    possible_names = ["ASA Dedicated Server", "ASA Dedicated", "ASA Server", "ASA"]
    for base in candidates:
        # check subfolders
        try:
            for child in base.iterdir():
                if child.is_dir():
                    name = child.name.lower()
                    for want in possible_names:
                        if want.lower() in name:
                            return child
        except Exception:
            continue
    return None


def pick_exe_in_folder(folder: Path) -> Optional[Path]:
    # Highest priority: Look for ArkAscendedServer.exe in the standard Steam installation path
    # Path: ShooterGame/Binaries/Win64/ArkAscendedServer.exe
    shooter_game_path = folder / "ShooterGame" / "Binaries" / "Win64" / "ArkAscendedServer.exe"
    if shooter_game_path.exists():
        return shooter_game_path
    
    # Also check for win64 folder directly (alternative location)
    win64_path = folder / "win64"
    if win64_path.exists() and win64_path.is_dir():
        ark_exe = win64_path / "ArkAscendedServer.exe"
        if ark_exe.exists():
            return ark_exe
    
    # Also check root folder for ArkAscendedServer.exe
    ark_exe_root = folder / "ArkAscendedServer.exe"
    if ark_exe_root.exists():
        return ark_exe_root
    
    # Recursively search for ArkAscendedServer.exe anywhere in the folder tree
    ark_exe_found = list(folder.glob("**/ArkAscendedServer.exe"))
    if ark_exe_found:
        # Prefer the one in ShooterGame/Binaries/Win64 if multiple found
        for exe in ark_exe_found:
            if "ShooterGame" in str(exe) and "Binaries" in str(exe) and "Win64" in str(exe):
                return exe
        # Otherwise return the first one found
        return ark_exe_found[0]
    
    # Fallback: search for executables in folder and subdirectories
    exes = list(folder.glob("**/*.exe"))  # Recursive search
    if not exes:
        return None
    
    # Filter out Steam utility executables
    excluded_names = {
        'steamcmd.exe', 'steamerrorreporter.exe', 'steam.exe', 
        'steamservice.exe', 'steamerrorreporter64.exe', 'vcredist_x64.exe',
        'vcredist_x86.exe', 'dxsetup.exe', 'directx_installer.exe'
    }
    exes_filtered = [e for e in exes if e.name.lower() not in excluded_names]
    
    if not exes_filtered:
        # If all exes were filtered, return None rather than a utility exe
        return None
    
    # Prefer known server executable names
    preferred_tokens = ["shootergameserver", "shooter", "shooter_game", "server", "dedicated", "arkascended"]
    
    # Exact preference: common ARK/ASA server exe names
    for e in exes_filtered:
        n = e.name.lower()
        if 'arkascendedserver' in n or 'shootergameserver' in n or ('shooter' in n and 'server' in n):
            return e
    
    # Next prefer tokens like 'server' or 'dedicated'
    for e in exes_filtered:
        n = e.name.lower()
        for t in preferred_tokens:
            if t in n:
                return e
    
    # If we still have candidates, prefer larger executables (server exes are typically larger)
    # Sort by file size descending
    try:
        exes_filtered.sort(key=lambda x: x.stat().st_size, reverse=True)
    except Exception:
        pass
    
    # Return the largest (most likely to be the server) or first if size check failed
    return exes_filtered[0] if exes_filtered else None


class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("ASA Dedicated Server Launcher - Setup")
        self.config = load_config()
        icon = tk.PhotoImage(file="images/ark.png")  # relative path to your image
        self.root.iconphoto(True, icon)  # sets the window icon
        # GUI variables
        self.server_path_var = tk.StringVar(value=self.config.get("server_path", ""))
        self.backup_dir_var = tk.StringVar(value=self.config.get("backup_dir", str(Path.home() / "ASABackups")))
        self.cluster_id_var = tk.StringVar(value=self.config.get("cluster_id", "default_cluster"))

        self._build_widgets()

        # Use a custom attribute on the root window to track if a LauncherApp is attached
        # Instead of setting a direct attribute, we'll use a more standard approach
        if not hasattr(self.root, '_launcher_app_attached'):
            self.root._launcher_app_attached = False

        self.root._launcher_app_attached = True
        # If server path empty, try an auto-detect
        if not self.server_path_var.get():
            found = auto_search_for_server()
            if found:
                self.server_path_var.set(str(found))

    def _build_widgets(self):
        self.root.geometry("760x320")
        frm = ttk.Frame(self.root, padding=(14, 14))
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="ASA Dedicated Server Folder:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(frm, textvariable=self.server_path_var, width=72).grid(row=1, column=0, columnspan=4, sticky=tk.W)
        ttk.Button(frm, text="Browse...", command=self.browse_server).grid(row=1, column=4, padx=6)
        ttk.Button(frm, text="Auto-detect", command=self.run_auto_detect).grid(row=1, column=5)
        ttk.Button(frm, text="Install (SteamCMD)", command=self.install_link).grid(row=1, column=6, padx=6)
        # Quick-install for ARK Dedicated Server (AppID 2430930) - placed below SteamCMD button
        ttk.Button(frm, text="Install ASA Dedicated", command=lambda: self.install_link(appid_override='2430930')).grid(row=2, column=6, padx=6, pady=(6,0))

        ttk.Label(frm, text="Backup directory:").grid(row=2, column=0, sticky=tk.W, pady=(12, 0))
        ttk.Entry(frm, textvariable=self.backup_dir_var, width=72).grid(row=3, column=0, columnspan=4, sticky=tk.W)
        ttk.Button(frm, text="Browse...", command=self.browse_backup).grid(row=3, column=4, padx=6)

        ttk.Label(frm, text="Cluster ID:").grid(row=4, column=0, sticky=tk.W, pady=(12, 0))
        ttk.Entry(frm, textvariable=self.cluster_id_var, width=36).grid(row=5, column=0, sticky=tk.W)

        btn_save = ttk.Button(frm, text="Save & Open Manager", command=self.save_and_open_manager)
        btn_save.grid(row=6, column=0, pady=(18, 0), sticky=tk.W)

        # status area
        self.status_var = tk.StringVar(value="Ready")
        status_lbl = ttk.Label(frm, textvariable=self.status_var, foreground="#0b5394")
        status_lbl.grid(row=7, column=0, columnspan=8, sticky=tk.W, pady=(12, 0))

        # visually separate
        for c in range(8):
            frm.grid_columnconfigure(c, pad=4)

    def browse_server(self):
        p = filedialog.askdirectory(title="Select ASA Dedicated Server folder")
        if p:
            self.server_path_var.set(p)

    def browse_backup(self):
        p = filedialog.askdirectory(title="Select backup folder")
        if p:
            self.backup_dir_var.set(p)

    def run_auto_detect(self):
        self.status_var.set("Searching...")

        def job():
            found = auto_search_for_server()
            if found:
                self.server_path_var.set(str(found))
                self.status_var.set(f"Found: {found}")
            else:
                self.status_var.set("Not found. Please browse or install.")

        threading.Thread(target=job, daemon=True).start()

    def install_link(self, appid_override: Optional[str] = None):
        """Install via SteamCMD: auto-download SteamCMD if needed, ensure a target folder is chosen, run SteamCMD and allow cancel."""
        # Ensure target folder is selected
        target = self.server_path_var.get().strip()
        if not target:
            messagebox.showinfo("Choose folder", "Please select or enter the target folder for the server before installing.")
            return

        target_path = Path(target)
        target_path.mkdir(parents=True, exist_ok=True)

        # If an override AppID is supplied (quick-install), use it
        if appid_override:
            appid = appid_override
            self.status_var.set(f"Using override AppID {appid}")
        else:
            # Try to auto-guess the AppID from the target folder and cluster id
            guessed = auto_guess_appid(target_path, self.cluster_id_var.get())
            appid = None
            if guessed:
                appid, guessed_name = guessed
                self.status_var.set(f"Auto-guessed AppID {appid} ({guessed_name})")
            else:
                # If no automatic guess, allow a silent fallback search (no user input) by trying a few heuristics
                # If still none, fall back to the search dialog to avoid silent mistakes
                if messagebox.askyesno("Find AppID", "Could not auto-guess the AppID. Would you like to search the Steam app list? (recommended)"):
                    dlg = AppIDSearchDialog(self.root)
                    # Wait for dialog to close
                    self.root.wait_window(dlg.win)
                    if dlg.result:
                        appid = dlg.result[0]
                if not appid:
                    appid = simpledialog.askstring("Steam AppID", "Enter the Steam AppID for the dedicated server:")
        if not appid:
            messagebox.showinfo("Cancelled", "Install cancelled: no AppID provided.")
            return

        # Define installer runner so downloader can call it after extracting steamcmd
        def run_steamcmd_install(steamcmd_path: str):
            install_win = tk.Toplevel(self.root)
            install_win.title("Installing via SteamCMD")
            # Progress bar for SteamCMD download/install progress
            progress_var = tk.DoubleVar(value=0.0)
            try:
                pb = ttk.Progressbar(install_win, orient=tk.HORIZONTAL, mode='determinate', maximum=100.0, variable=progress_var)
                pb.pack(fill=tk.X, padx=6, pady=(6,0))
            except Exception:
                pb = None
            txt = tk.Text(install_win, height=16, width=90)
            txt.pack(fill=tk.BOTH, expand=True, pady=(6,0))
            btn_frame = ttk.Frame(install_win)
            btn_frame.pack(fill=tk.X)
            cancel_event = threading.Event()

            def cancel():
                cancel_event.set()
                # Attempt to terminate the running process if present
                try:
                    if proc and proc.poll() is None:
                        proc.terminate()
                except Exception:
                    pass

            ttk.Button(btn_frame, text="Cancel", command=cancel).pack(side=tk.RIGHT, padx=6, pady=6)

            def installer_job():
                nonlocal proc
                try:
                    self.status_var.set("Running SteamCMD...")
                    # Guard: don't run installs into the Steam client folder itself
                    def _is_under_steam_dir(pth: Path) -> bool:
                        try:
                            rp = Path(pth).resolve()
                        except Exception:
                            return False
                        # check ancestors for common Steam folder names
                        for a in [rp] + list(rp.parents):
                            name = a.name.lower()
                            if 'steam' in name or 'steamapps' in name:
                                return True
                            # check for steam.exe presence
                            try:
                                if any(a.glob('steam.exe')):
                                    return True
                            except Exception:
                                pass
                        return False

                    if _is_under_steam_dir(target_path):
                        safe_append("\nError: The target folder appears to be inside a Steam client folder.\nPlease choose a different install directory (e.g., C:\\Games\\ARKServer).\n")
                        self.status_var.set("Install aborted: target is inside Steam folder")
                        try:
                            install_win.after(1200, install_win.destroy)
                        except Exception:
                            pass
                        return

                    # Ensure SteamCMD logs in (anonymous) so app info can be queried
                    cmd = [steamcmd_path, "+login", "anonymous", "+force_install_dir", str(target_path), "+app_update", str(appid), "validate", "+quit"]
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    assert proc.stdout is not None
                    def safe_append(s: str):
                        try:
                            if not txt.winfo_exists():
                                return
                        except Exception:
                            return
                        def do():
                            try:
                                txt.insert(tk.END, s)
                                txt.see(tk.END)
                            except Exception:
                                pass
                        try:
                            txt.after(0, do)
                        except Exception:
                            pass

                    # Parse steamcmd output in chunks so we handle '\r' updates and partial lines.
                    import re
                    buf = ""
                    def _update_progress_from_text(text_line: str):
                        try:
                            m = re.search(r"(\d{1,3}(?:\.\d+)?)%", text_line)
                            if m and pb:
                                try:
                                    pct = float(m.group(1))
                                    pct = max(0.0, min(100.0, pct))
                                    try:
                                        install_win.after(0, lambda v=pct: progress_var.set(v))
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        except Exception:
                            pass

                    # Helper to replace last line in the text widget (used for '\r' updates)
                    def _replace_last_line(new_text: str):
                        try:
                            if not txt.winfo_exists():
                                return
                        except Exception:
                            return
                        def do_replace():
                            try:
                                # find start of last line
                                last_start = txt.index("end-1c linestart")
                                txt.delete(last_start, tk.END)
                                txt.insert(tk.END, new_text)
                                txt.see(tk.END)
                            except Exception:
                                pass
                        try:
                            txt.after(0, do_replace)
                        except Exception:
                            pass

                    while True:
                        try:
                            chunk = proc.stdout.read(1024)
                        except Exception:
                            chunk = ''
                        if not chunk:
                            if proc.poll() is not None:
                                # drain any remaining buffer
                                if buf:
                                    # treat remaining buffer as a line
                                    safe_append(buf + "\n")
                                    _update_progress_from_text(buf)
                                    buf = ""
                                break
                            # no data right now; yield briefly
                            time.sleep(0.06)
                            continue

                        buf += chunk

                        # Handle full newline-terminated lines
                        while "\n" in buf:
                            line, buf = buf.split("\n", 1)
                            safe_append(line + "\n")
                            _update_progress_from_text(line)

                        # Handle carriage-return updates (in-place progress lines)
                        if "\r" in buf:
                            parts = buf.split("\r")
                            # everything except the last part are interim updates we can append
                            for p in parts[:-1]:
                                safe_append(p + "\n")
                                _update_progress_from_text(p)
                            # replace the last line with the final part (partial)
                            last = parts[-1]
                            _replace_last_line(last)
                            _update_progress_from_text(last)
                            buf = ''

                        if cancel_event.is_set():
                            try:
                                proc.terminate()
                            except Exception:
                                pass
                            safe_append("\nInstallation cancelled.\n")
                            try:
                                install_win.after(400, install_win.destroy)
                            except Exception:
                                pass
                            return
                    proc.wait()
                    if proc.returncode == 0:
                        safe_append("\nInstall finished successfully.\n")
                        self.server_path_var.set(str(target_path))
                        save_config({
                            "server_path": str(target_path),
                            "backup_dir": self.backup_dir_var.get().strip(),
                            "cluster_id": self.cluster_id_var.get().strip(),
                        })
                        self.status_var.set("Install complete and settings saved.")
                        # close install window after short delay so user sees success
                        try:
                            install_win.after(800, install_win.destroy)
                        except Exception:
                            pass
                    else:
                        safe_append(f"\nSteamCMD exited with code {proc.returncode}\n")
                        self.status_var.set("Install failed.")
                        try:
                            install_win.after(1200, install_win.destroy)
                        except Exception:
                            pass
                except Exception as e:
                    safe_append(f"\nError: {e}\n")
                    self.status_var.set("Install error")
                    try:
                        install_win.after(1200, install_win.destroy)
                    except Exception:
                        pass

            proc: Optional[subprocess.Popen] = None
            threading.Thread(target=installer_job, daemon=True).start()

        # Locate steamcmd or download it into the target folder
        steamcmd = shutil.which("steamcmd")
        if not steamcmd:
            # If steamcmd.exe exists inside the target folder (from a prior install), use it
            try:
                found_local = list(target_path.glob('**/steamcmd.exe'))
                if found_local:
                    steamcmd = str(found_local[0])
            except Exception:
                found_local = []

        if not steamcmd:
            if not messagebox.askyesno("SteamCMD", "SteamCMD was not found on PATH or in the target folder. Do you want the launcher to download and install SteamCMD now? (it will be placed inside the chosen target folder)"):
                return

            # Download SteamCMD archive for Windows from official Valve location
            # Official URL: https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip
            steamcmd_url = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"

            progress = tk.Toplevel(self.root)
            progress.title("Downloading SteamCMD")
            txt = tk.Text(progress, height=16, width=80)
            txt.pack(fill=tk.BOTH, expand=True)
            cancel_flag = threading.Event()

            def download_and_extract():
                def safe_append_download(s: str):
                    try:
                        if not txt.winfo_exists():
                            return
                    except Exception:
                        return
                    def do():
                        try:
                            txt.insert(tk.END, s)
                            txt.see(tk.END)
                        except Exception:
                            pass
                    try:
                        txt.after(0, do)
                    except Exception:
                        pass

                try:
                    safe_append_download(f"Downloading SteamCMD from {steamcmd_url}...\n")
                    # download to memory (small file) or stream to disk
                    dl_path = target_path / "steamcmd.zip"
                    with urllib.request.urlopen(steamcmd_url) as resp, open(dl_path, "wb") as out:
                        total = resp.length if hasattr(resp, 'length') else None
                        chunk = resp.read(8192)
                        while chunk:
                            if cancel_flag.is_set():
                                safe_append_download("\nDownload cancelled by user.\n")
                                try:
                                    out.close()
                                except Exception:
                                    pass
                                try:
                                    dl_path.unlink()
                                except Exception:
                                    pass
                                return
                            out.write(chunk)
                            chunk = resp.read(8192)
                    safe_append_download("Download complete, extracting...\n")
                    with zipfile.ZipFile(dl_path, 'r') as zf:
                        zf.extractall(path=target_path)
                    try:
                        dl_path.unlink()
                    except Exception:
                        pass
                    # steamcmd.exe should now exist in target_path
                    found = list(target_path.glob('**/steamcmd.exe'))
                    if found:
                        nonlocal_steam = str(found[0])
                        safe_append_download(f"SteamCMD installed to: {nonlocal_steam}\n")
                        # close the download progress window and start the installer window
                        try:
                            progress.after(200, progress.destroy)
                        except Exception:
                            pass
                        run_steamcmd_install(str(found[0]))
                    else:
                        safe_append_download("Could not find steamcmd.exe after extraction.\n")
                except Exception as e:
                    safe_append_download(f"Error downloading/extracting SteamCMD: {e}\n")

            def cancel_download():
                cancel_flag.set()

            btn_cancel = ttk.Button(progress, text="Cancel", command=cancel_download)
            btn_cancel.pack(side=tk.BOTTOM, pady=6)
            threading.Thread(target=download_and_extract, daemon=True).start()
            return

        # If we already had steamcmd on PATH
        run_steamcmd_install(steamcmd)

    def save_and_open_manager(self):
        cfg = {
            "server_path": self.server_path_var.get().strip(),
            "backup_dir": self.backup_dir_var.get().strip(),
            "cluster_id": self.cluster_id_var.get().strip(),
        }
        save_config(cfg)
        self.status_var.set("Settings saved.")
        # open server manager window
        self.open_manager(cfg)

    def open_manager(self, cfg: dict):
        # Open a new top-level window and hide the setup window
        self.root.withdraw()
        mgr = tk.Toplevel()
        ServerManager(mgr, cfg, parent_root=self.root)


class ServerManager:
    def __init__(self, root: tk.Toplevel, config: dict, parent_root: tk.Tk):
        self.root = root
        self.root.title("ASA Server Manager")
        icon = tk.PhotoImage(file="images/ark.png")  # relative path to your image
        self.root.iconphoto(True, icon)  # sets the window icon
        self.config = config
        self.parent_root = parent_root
        self.server_proc: Optional[subprocess.Popen] = None
        # track running server procs by path and backup workers
        self.server_procs: dict[str, subprocess.Popen] = {}
        self.backup_workers: dict[str, threading.Event] = {}
        # schedule runner state: stop event and last-run tracking to avoid duplicate triggers
        self._schedule_stop_event: Optional[threading.Event] = None
        self._schedule_last_run: dict[str, str] = {}

        # bind key methods to instance to ensure UI callbacks resolve even if
        # method lookups behave unexpectedly in some environments
        try:
            self.start_server = self.start_server
            self.stop_server = self.stop_server
            self.open_server_folder = self.open_server_folder
            self.open_backup_folder = self.open_backup_folder
            self.edit_settings = self.edit_settings
            self.browse_backup = self.browse_backup
        except Exception:
            pass

        self._build()
    
    @staticmethod
    def write_game_user_settings(server_folder: Path, sdict: dict):
        # Write a minimal GameUserSettings.ini
        cfg_dir = server_folder / "Saved" / "Config" / "WindowsServer"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        gus = cfg_dir / "GameUserSettings.ini"
        lines = []
        # Recommended header so some tools/servers don't overwrite the file
        lines.append("[/Script/ShooterGame.ShooterGameUserSettings]")
        lines.append("Version=5")
        lines.append("")
        # Server settings (common key names used by ARK)
        lines.append("[ServerSettings]")
        lines.append(f"MaxPlayers={sdict.get('max_players', 70)}")
        spw = sdict.get('server_password') or ""
        lines.append(f"ServerPassword={spw}")
        # RCON / ports
        if sdict.get('query_port') is not None:
            lines.append(f"QueryPort={sdict.get('query_port')}")
        if sdict.get('rcon_port') is not None:
            lines.append(f"RCONPort={sdict.get('rcon_port')}")
        if sdict.get('game_port') is not None:
            lines.append(f"Port={sdict.get('game_port')}")
        # Common boolean server flags (use canonical names where possible)
        # Note: GameUserSettings.ini uses camelCase/case-sensitive keys historically
        lines.append(f"noTributeDownloads={(1 if sdict.get('no_tribute_downloads') else 0)}")
        # Some servers expect ShowMapPlayerLocation key
        if sdict.get('show_map_location') is not None:
            lines.append(f"ShowMapPlayerLocation={(1 if sdict.get('show_map_location') else 0)}")
        lines.append("")
        # Session / gameplay multipliers and settings
        lines.append("[SessionSettings]")
        # Core session timing and difficulty
        if sdict.get('difficulty_offset') is not None:
            lines.append(f"DifficultyOffset={sdict.get('difficulty_offset')}")
        if sdict.get('day_speed') is not None:
            # canonical name per wiki: DayTimeSpeedScale
            lines.append(f"DayTimeSpeedScale={sdict.get('day_speed')}")
        if sdict.get('night_speed') is not None:
            lines.append(f"NightTimeSpeedScale={sdict.get('night_speed')}")

        # Player / global multipliers (canonical names)
        try:
            # XP / taming / harvest
            if sdict.get('player_xp_multiplier') is not None:
                lines.append(f"XPMultiplier={sdict.get('player_xp_multiplier')}")
            if sdict.get('player_taming_speed') is not None:
                lines.append(f"TamingSpeedMultiplier={sdict.get('player_taming_speed')}")
            # Player health / recovery multiplier (canonical server key)
            if sdict.get('player_health_multiplier') is not None:
                lines.append(f"PlayerCharacterHealthRecoveryMultiplier={sdict.get('player_health_multiplier')}")

            # Dino multipliers
            if sdict.get('dino_xp_multiplier') is not None:
                # common community key used for dino XP scaling
                lines.append(f"DinoXPMultiplier={sdict.get('dino_xp_multiplier')}")
            if sdict.get('dino_health_multiplier') is not None:
                lines.append(f"DinoCharacterHealthRecoveryMultiplier={sdict.get('dino_health_multiplier')}")
            if sdict.get('dino_taming_speed') is not None:
                # TamingSpeedMultiplier is global (applies to dinos)
                lines.append(f"TamingSpeedMultiplier={sdict.get('dino_taming_speed')}")

            # Damage/Combat multipliers (canonical keys)
            if sdict.get('global_damage_multiplier') is not None:
                # Map to PlayerDamageMultiplier as a sensible global damage toggle
                lines.append(f"PlayerDamageMultiplier={sdict.get('global_damage_multiplier')}")
            if sdict.get('melee_damage_multiplier') is not None:
                lines.append(f"MeleeDamageMultiplier={sdict.get('melee_damage_multiplier')}")
            if sdict.get('resource_harvest_multiplier') is not None:
                lines.append(f"HarvestAmountMultiplier={sdict.get('resource_harvest_multiplier')}")
            if sdict.get('spawn_rate_multiplier') is not None:
                # spawn-related canonical name: DinoCountMultiplier (affects dino spawns)
                lines.append(f"DinoCountMultiplier={sdict.get('spawn_rate_multiplier')}")
            # Structure/health multipliers
            if sdict.get('structure_health_multiplier') is not None:
                # there is no universal "StructureHealthMultiplier" key widely documented, add StructureDamageMultiplier as common server-side damage scaling
                lines.append(f"StructureDamageMultiplier={sdict.get('structure_health_multiplier')}")
            if sdict.get('structure_build_cost_multiplier') is not None:
                # canonical crafting/cost overrides live in Game.ini; include a best-effort alias here
                lines.append(f"StructureResistanceMultiplier={sdict.get('structure_build_cost_multiplier')}")
            # Loot respawn interval — keep as a comment if present (hours expected)
            if sdict.get('loot_respawn_interval') is not None:
                lines.append(f"ResourcesRespawnPeriodMultiplier={sdict.get('loot_respawn_interval')}")
        except Exception:
            pass

        gus.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def write_game_ini(server_folder: Path, sdict: dict):
        cfg_dir = server_folder / "Saved" / "Config" / "WindowsServer"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        gi = cfg_dir / "Game.ini"
        lines = []
        # Use the canonical Game.ini section for gameplay overrides
        lines.append("[/script/shootergame.shootergamemode]")
        # Override official difficulty (canonical key)
        if sdict.get('override_official_difficulty') is not None:
            lines.append(f"OverrideOfficialDifficulty={sdict.get('override_official_difficulty')}")
        # Max players is commonly applied in SessionSettings but include here as well for completeness
        if sdict.get('max_players') is not None:
            lines.append(f"MaxPlayers={sdict.get('max_players')}")

        # Player / Dino per-level and base stat multipliers go into Game.ini using the PerLevelStatsMultiplier and PlayerBaseStatMultipliers templates.
        # We will write a few sensible defaults based on the provided fields.
        try:
            # Example: adjust base stat multipliers for players (Health=0 index)
            if sdict.get('player_health_multiplier') is not None:
                lines.append(f"PlayerBaseStatMultipliers[0]={sdict.get('player_health_multiplier')}")

            # Per-level stat multipliers for players (example mapping)
            if sdict.get('player_xp_multiplier') is not None:
                # There is no single-canonical per-level XP var; providing KillXPMultiplier as an additional helpful key
                lines.append(f"KillXPMultiplier={sdict.get('player_xp_multiplier')}")

            # Dino-specific entries
            if sdict.get('dino_xp_multiplier') is not None:
                lines.append(f"KillXPMultiplier={sdict.get('dino_xp_multiplier')}")
            if sdict.get('dino_health_multiplier') is not None:
                lines.append(f"DinoCharacterHealthRecoveryMultiplier={sdict.get('dino_health_multiplier')}")

            # Damage multipliers and class-specific multipliers
            if sdict.get('global_damage_multiplier') is not None:
                lines.append(f"PlayerDamageMultiplier={sdict.get('global_damage_multiplier')}")
            if sdict.get('melee_damage_multiplier') is not None:
                lines.append(f"MeleeDamageMultiplier={sdict.get('melee_damage_multiplier')}")

        except Exception:
            pass

        gi.write_text("\n".join(lines), encoding="utf-8")

    @staticmethod
    def read_game_user_settings(server_folder: Path) -> dict:
        """Read GameUserSettings.ini and parse it back into a settings dictionary."""
        settings = {}
        try:
            cfg_dir = server_folder / "Saved" / "Config" / "WindowsServer"
            gus = cfg_dir / "GameUserSettings.ini"
            if not gus.exists():
                return settings
            
            content = gus.read_text(encoding="utf-8")
            current_section = None
            
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Check for section headers
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    continue
                
                # Parse key=value pairs
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Map INI keys back to our settings dictionary keys
                    if current_section == "ServerSettings":
                        if key == "MaxPlayers":
                            try:
                                settings['max_players'] = int(value)
                            except ValueError:
                                pass
                        elif key == "ServerPassword":
                            settings['server_password'] = value
                        elif key == "QueryPort":
                            try:
                                settings['query_port'] = int(value)
                            except ValueError:
                                pass
                        elif key == "RCONPort":
                            try:
                                settings['rcon_port'] = int(value)
                            except ValueError:
                                pass
                        elif key == "Port":
                            try:
                                settings['game_port'] = int(value)
                            except ValueError:
                                pass
                        elif key == "noTributeDownloads":
                            settings['no_tribute_downloads'] = (value == "1")
                        elif key == "ShowMapPlayerLocation":
                            settings['show_map_location'] = (value == "1")
                    
                    elif current_section == "SessionSettings":
                        if key == "DifficultyOffset":
                            try:
                                settings['difficulty_offset'] = float(value)
                            except ValueError:
                                pass
                        elif key == "DayTimeSpeedScale":
                            try:
                                settings['day_speed'] = float(value)
                            except ValueError:
                                pass
                        elif key == "NightTimeSpeedScale":
                            try:
                                settings['night_speed'] = float(value)
                            except ValueError:
                                pass
                        elif key == "XPMultiplier":
                            try:
                                settings['player_xp_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "TamingSpeedMultiplier":
                            try:
                                settings['player_taming_speed'] = float(value)
                            except ValueError:
                                pass
                        elif key == "PlayerCharacterHealthRecoveryMultiplier":
                            try:
                                settings['player_health_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "DinoXPMultiplier":
                            try:
                                settings['dino_xp_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "DinoCharacterHealthRecoveryMultiplier":
                            try:
                                settings['dino_health_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "PlayerDamageMultiplier":
                            try:
                                settings['global_damage_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "MeleeDamageMultiplier":
                            try:
                                settings['melee_damage_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "HarvestAmountMultiplier":
                            try:
                                settings['resource_harvest_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "DinoCountMultiplier":
                            try:
                                settings['spawn_rate_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "StructureDamageMultiplier":
                            try:
                                settings['structure_health_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "StructureResistanceMultiplier":
                            try:
                                settings['structure_build_cost_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "ResourcesRespawnPeriodMultiplier":
                            try:
                                settings['loot_respawn_interval'] = float(value)
                            except ValueError:
                                pass
        except Exception:
            pass
        
        return settings

    @staticmethod
    def read_game_ini(server_folder: Path) -> dict:
        """Read Game.ini and parse it back into a settings dictionary."""
        settings = {}
        try:
            cfg_dir = server_folder / "Saved" / "Config" / "WindowsServer"
            gi = cfg_dir / "Game.ini"
            if not gi.exists():
                return settings
            
            content = gi.read_text(encoding="utf-8")
            current_section = None
            
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                # Check for section headers
                if line.startswith('[') and line.endswith(']'):
                    current_section = line[1:-1]
                    continue
                
                # Parse key=value pairs
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    # Map INI keys back to our settings dictionary keys
                    if current_section == "[/script/shootergame.shootergamemode]":
                        if key == "OverrideOfficialDifficulty":
                            try:
                                settings['override_official_difficulty'] = float(value)
                            except ValueError:
                                pass
                        elif key == "MaxPlayers":
                            try:
                                settings['max_players'] = int(value)
                            except ValueError:
                                pass
                        elif key == "PlayerBaseStatMultipliers[0]":
                            try:
                                settings['player_health_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "KillXPMultiplier":
                            # Could be player or dino, check if we already have player_xp_multiplier
                            if 'player_xp_multiplier' not in settings:
                                try:
                                    settings['player_xp_multiplier'] = float(value)
                                except ValueError:
                                    pass
                            else:
                                try:
                                    settings['dino_xp_multiplier'] = float(value)
                                except ValueError:
                                    pass
                        elif key == "DinoCharacterHealthRecoveryMultiplier":
                            try:
                                settings['dino_health_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "PlayerDamageMultiplier":
                            try:
                                settings['global_damage_multiplier'] = float(value)
                            except ValueError:
                                pass
                        elif key == "MeleeDamageMultiplier":
                            try:
                                settings['melee_damage_multiplier'] = float(value)
                            except ValueError:
                                pass
        except Exception:
            pass
        
        return settings

    @staticmethod
    def read_server_settings(server_folder: Path) -> dict:
        """Read both INI files and merge them into a settings dictionary."""
        settings = {}
        # Read GameUserSettings.ini
        gus_settings = ServerManager.read_game_user_settings(server_folder)
        settings.update(gus_settings)
        # Read Game.ini (will override any duplicate keys)
        gi_settings = ServerManager.read_game_ini(server_folder)
        settings.update(gi_settings)
        return settings

    @staticmethod
    def get_player_count_from_logs(server_folder: Path) -> Optional[int]:
        """Try to get player count from server log files."""
        try:
            # ARK servers typically log to Saved/Logs/
            log_dir = server_folder / "Saved" / "Logs"
            if not log_dir.exists():
                return None
            
            # Find the most recent log file
            log_files = sorted(log_dir.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True)
            if not log_files:
                return None
            
            # Read the last few lines of the most recent log
            latest_log = log_files[0]
            try:
                with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = f.readlines()
                    # Look for player count in recent lines (last 200 lines)
                    for line in reversed(lines[-200:]):
                        # Look for patterns like "Players: X" or "X players" or "PlayerCount: X"
                        line_lower = line.lower()
                        if 'player' in line_lower and ('count' in line_lower or 'online' in line_lower or 'connected' in line_lower):
                            # Try to extract number
                            match = re.search(r'(\d+)\s*(?:players?|online|connected)', line_lower)
                            if match:
                                count = int(match.group(1))
                                # Sanity check - player count should be reasonable
                                if 0 <= count <= 200:
                                    return count
            except Exception:
                pass
            
            return None
        except Exception:
            return None

    @staticmethod
    def build_server_command(server_settings: dict, cluster_id: str = "") -> list[str]:
        """Build the server command line with map, session name, ports, and flags."""
        # Get settings with defaults
        map_name = server_settings.get('map', 'TheIsland')
        session_name = server_settings.get('session_name') or cluster_id or server_settings.get('name', 'MyServer')
        game_port = server_settings.get('game_port', 7777)
        query_port = server_settings.get('query_port', 27015)
        rcon_port = server_settings.get('rcon_port', 32330)
        rcon_password = server_settings.get('rcon_password') or server_settings.get('server_password', '')
        
        # Build the map parameter with _WP suffix
        map_param = f"{map_name}_WP"
        
        # Build the query string with all parameters
        query_parts = [
            f"SessionName={session_name}",
            f"Port={game_port}",
            f"QueryPort={query_port}",
            "RCONEnabled=True",
            f"RCONPort={rcon_port}",
        ]
        
        # Add RCON password if provided
        if rcon_password:
            query_parts.append(f"ServerAdminPassword={rcon_password}")
        
        # Combine map and query string
        map_query = f"{map_param}?{'?'.join(query_parts)}"
        
        # Required flags
        flags = ["-server", "-log", "-NoBattlEye"]
        
        # Return as a single argument (the map query) followed by flags
        return [map_query] + flags
    
    def _build(self):
        # Make manager window larger and use a Notebook (tabs) for Manager / Settings / Cluster
        try:
            self.root.geometry("1100x700")
        except Exception:
            pass

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        tabs = ttk.Notebook(main)
        tabs.pack(fill=tk.BOTH, expand=True)

        # Create cluster tab first so it's the initially visible tab
        cluster_tab = ttk.Frame(tabs)
        mgr_tab = ttk.Frame(tabs)
        settings_tab = ttk.Frame(tabs)
        tabs.add(cluster_tab, text="Cluster")
        tabs.add(mgr_tab, text="Manager")
        tabs.add(settings_tab, text="Settings")

        # Left controls and log live in mgr_tab
        left = ttk.Frame(mgr_tab)
        left.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Keep references to these labels so we can update them when the user selects a server from the cluster
        self.lbl_server_folder = ttk.Label(left, text=f"Server folder: {self.config.get('server_path')}")
        self.lbl_server_folder.grid(row=0, column=0, sticky=tk.W)
        self.lbl_backup_folder = ttk.Label(left, text=f"Backup folder: {self.config.get('backup_dir')}")
        self.lbl_backup_folder.grid(row=1, column=0, sticky=tk.W)
        self.lbl_cluster_id = ttk.Label(left, text=f"Cluster ID: {self.config.get('cluster_id')}")
        self.lbl_cluster_id.grid(row=2, column=0, sticky=tk.W)

        ttk.Button(left, text="Start Server", command=self.start_server).grid(row=3, column=0, pady=(12, 0), sticky=tk.W)
        ttk.Button(left, text="Stop Server", command=self.stop_server).grid(row=3, column=1, pady=(12, 0), sticky=tk.W)
        ttk.Button(left, text="Open Server Folder", command=self.open_server_folder).grid(row=4, column=0, pady=(8, 0), sticky=tk.W)
        ttk.Button(left, text="Open Backup Folder", command=self.open_backup_folder).grid(row=4, column=1, pady=(8, 0), sticky=tk.W)
        ttk.Button(left, text="Edit Setup", command=self.edit_settings).grid(row=5, column=0, pady=(12, 0), sticky=tk.W)

        self.log_text = tk.Text(left, height=18, width=100)
        self.log_text.grid(row=6, column=0, columnspan=4, pady=(12, 0))
        self.log("Manager ready.")

        # Settings panel on the settings_tab
        settings_frame = ttk.LabelFrame(settings_tab, text="Server Settings", padding=8)
        settings_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # Load existing server settings from config
        s_cfg = self.config.get("server_settings", {}) if isinstance(self.config, dict) else {}
        self.settings_vars = {}

        def sv(name, default=None, kind="str"):
            val = s_cfg.get(name, default)
            if kind == "bool":
                v = tk.BooleanVar(value=bool(val))
            elif kind == "int":
                try:
                    v = tk.IntVar(value=int(val) if val is not None else 0)
                except Exception:
                    v = tk.IntVar(value=0)
            else:
                v = tk.StringVar(value="" if val is None else str(val))
            self.settings_vars[name] = v
            return v

        # Create a small style for Entry widgets to make them look more modern where possible
        try:
            style = ttk.Style()
            style.configure('Rounded.TEntry', padding=6)
            style.configure('Rounded.TCombobox', padding=6)
        except Exception:
            pass

        row = 0

        # Presets across the top (span both columns)
        presets = {
            "Default": {},
            "PvP": {"enable_pvp": True, "max_players": 70, "no_tribute_downloads": False},
            "PvE": {"enable_pvp": False, "max_players": 50, "no_tribute_downloads": True},
            "Hardcore": {"enable_pvp": True, "max_players": 30, "difficulty_offset": "1.5", "day_speed": "0.8", "night_speed": "0.8"},
        }

        preset_var = tk.StringVar(value="Default")
        ttk.Label(settings_frame, text="Presets:").grid(row=row, column=0, sticky=tk.W)
        preset_combobox = ttk.Combobox(settings_frame, values=list(presets.keys()), textvariable=preset_var, width=12, style='Rounded.TCombobox')
        preset_combobox.grid(row=row, column=1, sticky=tk.W, pady=6)

        def apply_preset():
            p = presets.get(preset_var.get(), {})
            for k, v in p.items():
                if k in self.settings_vars:
                    var = self.settings_vars[k]
                    try:
                        var.set(v)
                    except Exception:
                        pass

        ttk.Button(settings_frame, text="Apply", command=apply_preset).grid(row=row, column=2, padx=6)

        # Custom preset controls (top)
        custom_presets = load_custom_presets()
        custom_var = tk.StringVar(value="")
        ttk.Label(settings_frame, text="Custom Presets:").grid(row=row, column=3, sticky=tk.W, padx=(12,0))
        custom_cb = ttk.Combobox(settings_frame, values=list(custom_presets.keys()), textvariable=custom_var, width=16, style='Rounded.TCombobox')
        custom_cb.grid(row=row, column=4, sticky=tk.W, pady=6)

        def save_custom():
            name = simpledialog.askstring("Preset name", "Enter a name for this preset:")
            if not name:
                return
            s = {k: (v.get() if isinstance(v, (tk.StringVar, tk.IntVar, tk.BooleanVar)) else v) for k, v in self.settings_vars.items()}
            custom_presets[name] = s
            save_custom_presets(custom_presets)
            custom_cb['values'] = list(custom_presets.keys())
            messagebox.showinfo("Saved", f"Custom preset '{name}' saved.")

        def load_custom():
            name = custom_var.get()
            if not name or name not in custom_presets:
                messagebox.showinfo("Select", "Select a custom preset to load.")
                return
            s = custom_presets[name]
            for k, v in s.items():
                if k in self.settings_vars:
                    try:
                        self.settings_vars[k].set(v)
                    except Exception:
                        pass

        def delete_custom():
            name = custom_var.get()
            if not name or name not in custom_presets:
                return
            if messagebox.askyesno("Delete", f"Delete custom preset '{name}'?"):
                del custom_presets[name]
                save_custom_presets(custom_presets)
                custom_cb['values'] = list(custom_presets.keys())

        ttk.Button(settings_frame, text="Save Preset", command=save_custom).grid(row=row, column=5, padx=6)
        ttk.Button(settings_frame, text="Load Preset", command=load_custom).grid(row=row, column=6, padx=6)
        ttk.Button(settings_frame, text="Delete", command=delete_custom).grid(row=row, column=7, padx=6)
        row += 1

        # Inline help area
        help_var = tk.StringVar(value="Hover a control or select it to see help here.")
        help_lbl = ttk.Label(settings_frame, textvariable=help_var, wraplength=420, foreground="#333333")
        help_lbl.grid(row=row, column=0, columnspan=8, sticky=tk.W, pady=(4,8))
        row += 1

        # Helper to create collapsible sections inside settings_frame
        def make_section(title, start_row):
            header = ttk.Frame(settings_frame)
            # collapsed by default
            toggle_var = tk.BooleanVar(value=False)
            def _toggle():
                if toggle_var.get():
                    toggle_var.set(False)
                    content.grid_remove()
                    btn.config(text='+')
                else:
                    toggle_var.set(True)
                    content.grid()
                    btn.config(text='-')
            btn = ttk.Button(header, width=2, text='+', command=_toggle)
            lbl = ttk.Label(header, text=title, font=('Segoe UI', 10, 'bold'))
            btn.pack(side=tk.LEFT)
            lbl.pack(side=tk.LEFT, padx=(6,0))
            header.grid(row=start_row, column=0, columnspan=8, sticky=tk.W, pady=(8,2))
            content = ttk.Frame(settings_frame)
            content.grid(row=start_row+1, column=0, columnspan=8, sticky=tk.NSEW, pady=(0,8))
            # start collapsed
            content.grid_remove()
            return content

        # Core settings section with left/right columns
        core_content = make_section('Core Settings', row)
        row += 2
        left_col = ttk.Frame(core_content)
        right_col = ttk.Frame(core_content)
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,8))
        right_col.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8,0))

        # Prepare a few extra vars for new sections
        sv('additional_args', '', 'str')
        sv('auto_backup', False, 'bool')
        sv('backup_interval', 24, 'int')
        sv('autosave_settings', True, 'bool')
        sv('mods_enabled', False, 'bool')
        sv('mods_list', '', 'str')

        # Checkboxes (right column)
        ttk.Checkbutton(right_col, text="No Tributes/No Tribute Downloads", variable=sv("no_tribute_downloads", False, "bool")).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(right_col, text="Enable PvP", variable=sv("enable_pvp", True, "bool")).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(right_col, text="Crossplay Enabled", variable=sv("crossplay", False, "bool")).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(right_col, text="Allow Fly", variable=sv("allow_fly", True, "bool")).pack(anchor=tk.W, pady=2)
        ttk.Checkbutton(right_col, text="Show Map Player Location", variable=sv("show_map_location", True, "bool")).pack(anchor=tk.W, pady=2)

        # Numeric entries and text (left column)
        ttk.Label(left_col, text="Max Players:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("max_players", 70, "int"), width=12, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, pady=4, padx=(6,6))
        ttk.Label(left_col, text="Game Port:").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("game_port", 7777, "int"), width=12, style='Rounded.TEntry').grid(row=1, column=1, sticky=tk.W, pady=4, padx=(6,6))
        ttk.Label(left_col, text="Query Port:").grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("query_port", 27015, "int"), width=12, style='Rounded.TEntry').grid(row=2, column=1, sticky=tk.W, pady=4, padx=(6,6))
        ttk.Label(left_col, text="RCON Port:").grid(row=3, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("rcon_port", 32330, "int"), width=12, style='Rounded.TEntry').grid(row=3, column=1, sticky=tk.W, pady=4, padx=(6,6))
        ttk.Label(left_col, text="Server Password:").grid(row=4, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("server_password", "", "str"), width=20, show="*", style='Rounded.TEntry').grid(row=4, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="RCON Password:").grid(row=5, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("rcon_password", "", "str"), width=20, show="*", style='Rounded.TEntry').grid(row=5, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="Session Name:").grid(row=6, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("session_name", "", "str"), width=20, style='Rounded.TEntry').grid(row=6, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="Map:").grid(row=7, column=0, sticky=tk.W)
        ttk.Combobox(left_col, values=["TheIsland", "ScorchedEarth", "Aberration", "Extinction", "Genesis", "Ragnarok", "Valguero", "CrystalIsles"], textvariable=sv("map", "TheIsland"), style='Rounded.TCombobox').grid(row=7, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="Difficulty Scale:").grid(row=8, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("difficulty_offset", 1.0, "str"), width=12, style='Rounded.TEntry').grid(row=8, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="Day Speed:").grid(row=9, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("day_speed", 1.0, "str"), width=12, style='Rounded.TEntry').grid(row=9, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="Night Speed:").grid(row=10, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("night_speed", 1.0, "str"), width=12, style='Rounded.TEntry').grid(row=10, column=1, sticky=tk.W, pady=4)
        ttk.Label(left_col, text="Override Official Difficulty:").grid(row=11, column=0, sticky=tk.W)
        ttk.Entry(left_col, textvariable=sv("override_official_difficulty", 1.0, "str"), width=12, style='Rounded.TEntry').grid(row=11, column=1, sticky=tk.W, pady=4)

        # Additional Arguments section
        add_args_content = make_section('Additional Arguments', row)
        row += 2
        ttk.Label(add_args_content, text="Server startup arguments (single line):").pack(anchor=tk.W)
        ttk.Entry(add_args_content, textvariable=sv('additional_args', '', 'str'), width=80, style='Rounded.TEntry').pack(fill=tk.X, pady=6)

        # Backups section
        backups_content = make_section('Backups', row)
        row += 2
        b_left = ttk.Frame(backups_content)
        b_right = ttk.Frame(backups_content)
        b_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        b_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        ttk.Label(b_left, text="Backup folder:").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(b_left, textvariable=sv('backup_dir', ''), width=48, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, padx=(6,6))
        ttk.Button(b_left, text="Browse...", command=self.browse_backup).grid(row=0, column=2, padx=6)
        ttk.Checkbutton(b_right, text="Enable auto backups", variable=sv('auto_backup', False, 'bool')).pack(anchor=tk.W, pady=2)
        ttk.Label(b_right, text="Interval (hours):").pack(anchor=tk.W, pady=(6,0))
        ttk.Entry(b_right, textvariable=sv('backup_interval', 24, 'int'), width=8, style='Rounded.TEntry').pack(anchor=tk.W)

        # Backup schedules list (multiple schedules allowed) placed under the Backup folder input
        sched_frame = ttk.Frame(b_left)
        # place under the first row of b_left which used grid; put schedules on row 1
        try:
            sched_frame.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=(8,0))
        except Exception:
            sched_frame.pack(fill=tk.X, pady=(8,0))
        ttk.Label(sched_frame, text="Scheduled tasks:").grid(row=0, column=0, sticky=tk.W)
        schedule_listbox = tk.Listbox(sched_frame, height=4)
        schedule_listbox.grid(row=1, column=0, columnspan=3, sticky=tk.W+tk.E, pady=4)

        # Use a JSON string var to persist schedules
        sched_var = sv('backup_schedules_json', '[]', 'str')
        try:
            schedules_parsed = json.loads(sched_var.get() or '[]')
        except Exception:
            schedules_parsed = []
        for it in schedules_parsed:
            schedule_listbox.insert(tk.END, f"{it.get('action')} @ {it.get('time')}")

        def _save_schedules():
            try:
                sched_var.set(json.dumps(schedules_parsed))
            except Exception:
                pass

        def add_schedule():
            dlg = tk.Toplevel(self.root)
            dlg.title('Add schedule')
            ttk.Label(dlg, text='Action:').grid(row=0, column=0, sticky=tk.W)
            act_var = tk.StringVar(value='backup')
            ttk.Combobox(dlg, values=['backup', 'restart'], textvariable=act_var, width=12).grid(row=0, column=1, sticky=tk.W)
            ttk.Label(dlg, text='Time (HH:MM, 24h):').grid(row=1, column=0, sticky=tk.W)
            time_var = tk.StringVar(value='03:00')
            ttk.Entry(dlg, textvariable=time_var, width=12).grid(row=1, column=1, sticky=tk.W)
            def _add():
                it = {'action': act_var.get(), 'time': time_var.get()}
                schedules_parsed.append(it)
                schedule_listbox.insert(tk.END, f"{it.get('action')} @ {it.get('time')}")
                _save_schedules()
                try:
                    # auto-persist schedules into settings and known_servers
                    save_settings_and_persist()
                except Exception:
                    pass
                try:
                    dlg.destroy()
                except Exception:
                    pass
            ttk.Button(dlg, text='Add', command=_add).grid(row=2, column=1, sticky=tk.E, pady=6)

        def remove_schedule():
            sel = schedule_listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            schedule_listbox.delete(idx)
            try:
                schedules_parsed.pop(idx)
            except Exception:
                pass
            _save_schedules()
            try:
                save_settings_and_persist()
            except Exception:
                pass

        btns = ttk.Frame(sched_frame)
        try:
            btns.grid(row=2, column=0, columnspan=3, sticky=tk.W)
        except Exception:
            btns.pack(fill=tk.X)
        ttk.Button(btns, text='Add schedule', command=add_schedule).pack(side=tk.LEFT)
        ttk.Button(btns, text='Remove selected', command=remove_schedule).pack(side=tk.LEFT, padx=6)

        # Mods section
        mods_content = make_section('Mods', row)
        row += 2
        ttk.Checkbutton(mods_content, text="Enable Mods", variable=sv('mods_enabled', False, 'bool')).pack(anchor=tk.W, pady=4)
        ttk.Label(mods_content, text="Mods (comma-separated IDs or folder names):").pack(anchor=tk.W)
        # Represent mods as a listbox for visibility and management
        mods_listbox = tk.Listbox(mods_content, height=6)
        mods_listbox.pack(fill=tk.X, pady=4)
        mods_var = sv('mods_list', '')
        mods_disabled_var = sv('mods_disabled_json', '[]')
        try:
            mlist = [m.strip() for m in (mods_var.get() or '').split(',') if m.strip()]
        except Exception:
            mlist = []
        try:
            disabled = json.loads(mods_disabled_var.get() or '[]')
        except Exception:
            disabled = []
        for m in mlist:
            label = f"[disabled] {m}" if m in disabled else m
            mods_listbox.insert(tk.END, label)

        def _refresh_mods_listbox():
            mods_listbox.delete(0, tk.END)
            try:
                mlist = [m.strip() for m in (mods_var.get() or '').split(',') if m.strip()]
            except Exception:
                mlist = []
            try:
                disabled = json.loads(mods_disabled_var.get() or '[]')
            except Exception:
                disabled = []
            for m in mlist:
                label = f"[disabled] {m}" if m in disabled else m
                mods_listbox.insert(tk.END, label)

        def open_mod_folder():
            # try server Mods folder or fallback to config mods_folder
            p = self.config.get('mods_folder') or Path(self.config.get('server_path', '') ) / 'Mods'
            try:
                if isinstance(p, str):
                    pth = Path(p)
                else:
                    pth = p
                if pth.exists():
                    os.startfile(str(pth))
                    return
            except Exception:
                pass
            messagebox.showinfo('Not found', 'Mods folder not found.')

        def toggle_disable_mod():
            sel = mods_listbox.curselection()
            if not sel:
                return
            idx = sel[0]
            try:
                mlist = [m.strip() for m in (mods_var.get() or '').split(',') if m.strip()]
            except Exception:
                mlist = []
            if idx >= len(mlist):
                return
            mod = mlist[idx]
            try:
                disabled = json.loads(mods_disabled_var.get() or '[]')
            except Exception:
                disabled = []
            if mod in disabled:
                disabled.remove(mod)
            else:
                disabled.append(mod)
            try:
                mods_disabled_var.set(json.dumps(disabled))
            except Exception:
                pass
            _refresh_mods_listbox()
            try:
                # persist mods disabled state immediately
                save_settings_and_persist()
            except Exception:
                pass

        mbtns = ttk.Frame(mods_content)
        mbtns.pack(fill=tk.X)
        ttk.Button(mbtns, text='Open Mods folder', command=open_mod_folder).pack(side=tk.LEFT)
        ttk.Button(mbtns, text='Toggle Disable', command=toggle_disable_mod).pack(side=tk.LEFT, padx=6)

        # Extended settings sections
        # PLAYER STAT MULTIPLIERS
        pstats = make_section('PLAYER STAT MULTIPLIERS', row)
        row += 2
        p_left = ttk.Frame(pstats)
        p_right = ttk.Frame(pstats)
        p_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0,8))
        p_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(8,0))
        # Example player multipliers
        sv('player_xp_multiplier', 1.0, 'str')
        sv('player_taming_speed', 1.0, 'str')
        sv('player_health_multiplier', 1.0, 'str')
        ttk.Label(p_left, text='XP Multiplier:').grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(p_left, textvariable=sv('player_xp_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(p_left, text='Taming Speed:').grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(p_left, textvariable=sv('player_taming_speed', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(p_left, text='Health Multiplier:').grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(p_left, textvariable=sv('player_health_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=2, column=1, sticky=tk.W, padx=6, pady=4)

        # DINO STAT MULTIPLIERS
        dstats = make_section('DINO STAT MULTIPLIERS', row)
        row += 2
        d_left = ttk.Frame(dstats)
        d_left.pack(fill=tk.BOTH, expand=True)
        sv('dino_xp_multiplier', 1.0, 'str')
        sv('dino_health_multiplier', 1.0, 'str')
        sv('dino_taming_speed', 1.0, 'str')
        ttk.Label(d_left, text='Dino XP Multiplier:').grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(d_left, textvariable=sv('dino_xp_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(d_left, text='Dino Health Multiplier:').grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(d_left, textvariable=sv('dino_health_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(d_left, text='Dino Taming Speed:').grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(d_left, textvariable=sv('dino_taming_speed', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=2, column=1, sticky=tk.W, padx=6, pady=4)

        # STRUCTURES AND BUILDING
        struct = make_section('STRUCTURES AND BUILDING', row)
        row += 2
        sv('structure_health_multiplier', 1.0, 'str')
        sv('structure_build_cost_multiplier', 1.0, 'str')
        ttk.Label(struct, text='Structure Health Multiplier:').grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(struct, textvariable=sv('structure_health_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(struct, text='Build Cost Multiplier:').grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(struct, textvariable=sv('structure_build_cost_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)

        # COMBAT AND DAMAGE
        combat = make_section('COMBAT AND DAMAGE', row)
        row += 2
        sv('global_damage_multiplier', 1.0, 'str')
        sv('melee_damage_multiplier', 1.0, 'str')
        ttk.Label(combat, text='Global Damage Multiplier:').grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(combat, textvariable=sv('global_damage_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(combat, text='Melee Damage Multiplier:').grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(combat, textvariable=sv('melee_damage_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)

        # RESOURCE, LOOT, AND SPAWN SETTINGS
        res = make_section('RESOURCE, LOOT, AND SPAWN SETTINGS', row)
        row += 2
        sv('resource_harvest_multiplier', 1.0, 'str')
        sv('loot_respawn_interval', 24, 'int')
        sv('spawn_rate_multiplier', 1.0, 'str')
        ttk.Label(res, text='Resource Harvest Multiplier:').grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(res, textvariable=sv('resource_harvest_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=0, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(res, text='Loot Respawn Interval (hours):').grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(res, textvariable=sv('loot_respawn_interval', 24, 'int'), width=12, style='Rounded.TEntry').grid(row=1, column=1, sticky=tk.W, padx=6, pady=4)
        ttk.Label(res, text='Spawn Rate Multiplier:').grid(row=2, column=0, sticky=tk.W)
        ttk.Entry(res, textvariable=sv('spawn_rate_multiplier', 1.0, 'str'), width=12, style='Rounded.TEntry').grid(row=2, column=1, sticky=tk.W, padx=6, pady=4)

        # Restore defaults
        def restore_defaults():
            for name, var in self.settings_vars.items():
                # set to baseline defaults similar to sv() defaults
                defaults = {
                    "no_tribute_downloads": False,
                    "enable_pvp": True,
                    "crossplay": False,
                    "allow_fly": True,
                    "show_map_location": True,
                    "max_players": 70,
                    "game_port": 7777,
                    "query_port": 27015,
                    "rcon_port": 32330,
                    "server_password": "",
                    "map": "TheIsland",
                    "difficulty_offset": "1.0",
                    "day_speed": "1.0",
                    "night_speed": "1.0",
                    "override_official_difficulty": "1.0",
                }
                if name in defaults:
                    try:
                        var.set(defaults[name])
                    except Exception:
                        pass

        # Save button and write to server config files
        def validate_settings(sdict: dict) -> tuple[bool, Optional[str]]:
            # Basic validation for integer fields and ports
            try:
                mp = int(sdict.get("max_players", 0))
                if mp <= 0 or mp > 1000:
                    return False, "Max players must be between 1 and 1000"
                for port_field in ("game_port", "query_port", "rcon_port"):
                    p = int(sdict.get(port_field, 0))
                    if p <= 0 or p > 65535:
                        return False, f"{port_field} must be between 1 and 65535"
                # Validate multipliers as floats
                float_fields = [
                    'player_xp_multiplier','player_taming_speed','player_health_multiplier',
                    'dino_xp_multiplier','dino_health_multiplier','dino_taming_speed',
                    'structure_health_multiplier','structure_build_cost_multiplier',
                    'global_damage_multiplier','melee_damage_multiplier',
                    'resource_harvest_multiplier','spawn_rate_multiplier'
                ]
                for fn in float_fields:
                    if fn in sdict and sdict.get(fn) not in (None, ''):
                        try:
                            float(sdict.get(fn))
                        except Exception:
                            return False, f"{fn} must be a number"
                # Loot respawn interval should be a positive integer
                if 'loot_respawn_interval' in sdict and sdict.get('loot_respawn_interval') not in (None, ''):
                    try:
                        li = int(sdict.get('loot_respawn_interval'))
                        if li < 0:
                            return False, "Loot respawn interval must be 0 or positive"
                    except Exception:
                        return False, "Loot respawn interval must be an integer"
            except Exception as e:
                return False, f"Validation error: {e}"
            return True, None

        def save_settings(silent=False):
            s = {k: (v.get() if isinstance(v, (tk.StringVar, tk.IntVar, tk.BooleanVar)) else v) for k, v in self.settings_vars.items()}
            ok, msg = validate_settings(s)
            if not ok:
                messagebox.showerror("Validation failed", msg)
                return
            # persist to in-memory config (do not write to disk here; persistence is
            # handled by save_settings_and_persist to correctly update known_servers)
            try:
                self.config["server_settings"] = s
            except Exception:
                pass

            # write to server folder (support both launcher config shape and per-server entries)
            server_folder = Path(self.config.get("server_path") or self.config.get("path") or "")
            if server_folder and server_folder.exists():
                try:
                    ServerManager.write_game_user_settings(server_folder, s)
                    ServerManager.write_game_ini(server_folder, s)
                    if not silent:
                        messagebox.showinfo("Saved", "Server settings saved and INI files written.")
                except Exception as e:
                    if not silent:
                        messagebox.showwarning("Saved with error", f"Settings saved to launcher config, but failed to write INI files: {e}")
            else:
                if not silent:
                    messagebox.showwarning("Saved", "Settings saved to launcher config, but server folder not found so INI files were not written.")

        # When saving settings, also persist them to the currently selected server entry (if any)
        def save_settings_and_persist(silent=False):
            save_settings(silent=silent)
            # Persist into selected server if we are editing one
            cur_path = self.config.get('path') or self.config.get('server_path')
            if not cur_path:
                return
            # find matching server in self.servers and update its server_settings and metadata
            updated = False
            for s in self.servers:
                if str(s.get('path')) == str(cur_path):
                    # ensure server_settings reflects the latest saved values
                    s['server_settings'] = self.config.get('server_settings', {})
                    s['cluster_id'] = self.config.get('cluster_id', s.get('cluster_id'))
                    s['backup_dir'] = self.config.get('backup_dir', s.get('backup_dir'))
                    updated = True
                    break
            if updated:
                try:
                    base = load_config()
                    # preserve any other keys but update known_servers
                    base['known_servers'] = self.servers
                    save_config(base)
                    self.log(f"Persisted settings to server entry for {cur_path}")
                except Exception as e:
                    self.log(f"Failed to persist known_servers: {e}")

        # Place Save/Restore buttons in a bottom bar so they stay anchored
        try:
            btn_bar = ttk.Frame(settings_tab)
            btn_bar.pack(fill=tk.X, side=tk.BOTTOM, padx=6, pady=8)
            ttk.Button(btn_bar, text="Restore Defaults", command=restore_defaults).pack(side=tk.LEFT)
            ttk.Button(btn_bar, text="Save Settings", command=save_settings_and_persist).pack(side=tk.RIGHT)
        except Exception:
            # fallback: place the save button in the settings_frame
            try:
                ttk.Button(settings_frame, text="Save Settings", command=save_settings_and_persist).grid(row=row, column=0, columnspan=3, pady=(12, 0))
            except Exception:
                pass

        # Autosave wiring: if autosave enabled, debounce writes to save_settings_and_persist
        try:
            autosave_after = {'id': None}
            def _schedule_autosave(*args):
                try:
                    if not (self.settings_vars.get('autosave_settings') and self.settings_vars['autosave_settings'].get()):
                        return
                except Exception:
                    pass
                try:
                    if autosave_after['id']:
                        settings_tab.after_cancel(autosave_after['id'])
                except Exception:
                    pass
                try:
                    autosave_after['id'] = settings_tab.after(1000, lambda: save_settings_and_persist(silent=True))
                except Exception:
                    pass
            for nm, v in list(self.settings_vars.items()):
                try:
                    if isinstance(v, (tk.StringVar, tk.IntVar, tk.BooleanVar)):
                        v.trace_add('write', _schedule_autosave)
                except Exception:
                    pass
        except Exception:
            pass

        # Cluster tab: list configured servers
        self.servers = self.config.get("known_servers", []) if isinstance(self.config, dict) else []
        # If no known servers, include the current server as a single entry
        if not self.servers:
            self.servers = [{
                "name": self.config.get("cluster_id", "local"),
                "path": self.config.get("server_path", ""),
                "cluster_id": self.config.get("cluster_id", ""),
                "backup_dir": self.config.get("backup_dir", ""),
                "status": "stopped",
                "server_settings": self.config.get("server_settings", {}),
            }]

        cluster_list = ttk.Treeview(cluster_tab, columns=("name","path","status","players"), show='headings')
        cluster_list.heading('name', text='Name')
        cluster_list.heading('path', text='Path')
        cluster_list.heading('status', text='Status')
        cluster_list.heading('players', text='Players')
        cluster_list.column('name', width=150)
        cluster_list.column('path', width=300)
        cluster_list.column('status', width=80)
        cluster_list.column('players', width=80)
        cluster_list.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        def refresh_cluster():
            for r in cluster_list.get_children():
                cluster_list.delete(r)
            for s in self.servers:
                player_count = s.get('player_count', '?')
                cluster_list.insert('', tk.END, values=(s.get('name'), s.get('path'), s.get('status', 'stopped'), player_count))
        refresh_cluster()

        # backup workers map: path -> stop_event
        # (initialized in __init__ as self.backup_workers)

        def update_statuses():
            # update status based on running procs and player counts
            changed = False
            for s in self.servers:
                ppath = str(s.get('path') or '')
                proc = self.server_procs.get(ppath)
                status = 'stopped'
                if proc and proc.poll() is None:
                    status = 'running'
                if s.get('status') != status:
                    s['status'] = status
                    changed = True
                
                # Update player count if server is running
                if status == 'running':
                    try:
                        server_folder = Path(ppath)
                        # Try to get player count from logs
                        player_count = ServerManager.get_player_count_from_logs(server_folder)
                        if player_count is not None:
                            old_count = s.get('player_count')
                            s['player_count'] = player_count
                            if old_count != player_count:
                                changed = True
                        else:
                            # If we can't get count, show "?" or keep previous
                            if 'player_count' not in s:
                                s['player_count'] = '?'
                                changed = True
                    except Exception:
                        if 'player_count' not in s:
                            s['player_count'] = '?'
                            changed = True
                else:
                    # Server is stopped, clear player count
                    if 'player_count' in s:
                        s['player_count'] = '0'
                        changed = True
            if changed:
                refresh_cluster()
            # schedule next poll
            try:
                cluster_tab.after(5000, update_statuses)  # Poll every 5 seconds
            except Exception:
                pass

        # start periodic polling
        update_statuses()

        def persist_known_servers():
            # store known_servers into the launcher config and save
            try:
                base = load_config()
                base['known_servers'] = self.servers
                save_config(base)
            except Exception:
                pass

        # Instance-level persistence helper so methods can call it
        def _persist_known_servers():
            try:
                base = load_config()
                base['known_servers'] = self.servers
                save_config(base)
            except Exception:
                pass

        # Expose a method on self for other methods to persist
        try:
            self.persist_known_servers = _persist_known_servers
        except Exception:
            pass

        def start_selected():
            sel = cluster_list.selection()
            if not sel:
                return
            vals = cluster_list.item(sel[0])['values']
            path = vals[1]
            # Start server for any entry
            self.start_server_for(path)

        def stop_selected():
            sel = cluster_list.selection()
            if not sel:
                return
            vals = cluster_list.item(sel[0])['values']
            path = vals[1]
            self.stop_server_for(path)

        # start/stop helper implementations for arbitrary server entries
        def pick_exe_for_path(ppath: str) -> Optional[Path]:
            try:
                p = Path(ppath)
                if not p.exists():
                    return None
                exe = pick_exe_in_folder(p)
                return exe
            except Exception:
                return None

        def start_server_for(path_str: str):
            ppath = str(path_str or '')
            if not ppath:
                messagebox.showerror("Start failed", "No path for server")
                return
            if ppath in self.server_procs and self.server_procs[ppath].poll() is None:
                self.log(f"Server already running at {ppath}")
                return
            exe = pick_exe_for_path(ppath)
            if not exe:
                messagebox.showinfo("Select exe", f"No server exe found in {ppath}. Please select the exe manually.")
                p = filedialog.askopenfilename(title="Select server executable", filetypes=[("EXE files", "*.exe")], initialdir=str(ppath))
                if not p:
                    return
                exe = Path(p)
            try:
                # Find the server entry to get its settings
                server_entry = None
                for s in self.servers:
                    if str(s.get('path')) == str(ppath):
                        server_entry = s
                        break
                
                # Write INI files to this server's folder before starting
                server_folder = Path(ppath)
                if server_folder.exists():
                    # Get this server's settings (per-server settings take precedence)
                    server_settings = server_entry.get('server_settings', {}) if server_entry else {}
                    if not server_settings:
                        # Fallback to global config settings
                        server_settings = self.config.get('server_settings', {})
                    
                    # Write INI files to this server's folder
                    try:
                        ServerManager.write_game_user_settings(server_folder, server_settings)
                        ServerManager.write_game_ini(server_folder, server_settings)
                        self.log(f"INI files written to {server_folder}")
                    except Exception as e:
                        self.log(f"Warning: Failed to write INI files to {server_folder}: {e}")
                
                env = os.environ.copy()
                # if the path belongs to a known server entry, prefer its cluster_id
                cid = None
                if server_entry:
                    cid = server_entry.get('cluster_id')
                env["ASA_CLUSTER_ID"] = cid or self.config.get("cluster_id", "")
                
                # Build the server command with map, session name, ports, and flags
                server_cmd = ServerManager.build_server_command(server_settings, cid or self.config.get("cluster_id", ""))
                
                # include additional_args if present for this server (per-server settings take precedence)
                args_list = []
                try:
                    # look up server-level settings
                    add_args = None
                    for s in self.servers:
                        if str(s.get('path')) == str(ppath):
                            ss = s.get('server_settings', {}) or {}
                            add_args = ss.get('additional_args')
                            break
                    if add_args is None:
                        # fallback to current config
                        add_args = (self.config.get('server_settings') or {}).get('additional_args') or self.config.get('additional_args')
                    if add_args:
                        try:
                            args_list = shlex.split(str(add_args))
                        except Exception:
                            args_list = [str(add_args)]
                except Exception:
                    args_list = []

                # Combine executable, server command, and additional args
                cmd = [str(exe)] + server_cmd + args_list
                # Don't redirect stdout/stderr - let ARK server use its own console window
                proc = subprocess.Popen(cmd, cwd=str(Path(ppath)), env=env)
                self.server_procs[ppath] = proc
                # start backup worker if enabled for this server
                try:
                    auto = False
                    interval = 0
                    bdir = None
                    for s in self.servers:
                        if str(s.get('path')) == str(ppath):
                            ss = s.get('server_settings', {}) or {}
                            auto = bool(ss.get('auto_backup'))
                            interval = int(ss.get('backup_interval') or 0)
                            bdir = s.get('backup_dir') or ss.get('backup_dir')
                            break
                    if not auto:
                        # fallback to config
                        ss = self.config.get('server_settings', {}) or {}
                        auto = bool(ss.get('auto_backup') or self.config.get('auto_backup'))
                        interval = int(ss.get('backup_interval') or self.config.get('backup_interval') or 0)
                        bdir = self.config.get('backup_dir') or ss.get('backup_dir')
                    if auto and interval > 0:
                        # start worker
                        try:
                            self.start_backup_worker(ppath, Path(bdir) if bdir else None, interval)
                        except Exception:
                            pass
                except Exception:
                    pass
                # ARK server opens its own console window, so we don't capture stdout/stderr
                self.log(f"Started {exe} PID {proc.pid}")
                # Update status immediately
                for s in self.servers:
                    if str(s.get('path')) == str(ppath):
                        s['status'] = 'running'
                        break
                refresh_cluster()
            except Exception as e:
                messagebox.showerror("Start failed", str(e))

        def stop_server_for(path_str: str):
            ppath = str(path_str or '')
            proc = self.server_procs.get(ppath)
            if not proc or proc.poll() is not None:
                self.log(f"No running server at {ppath}")
                # Update status anyway
                for s in self.servers:
                    if str(s.get('path')) == str(ppath):
                        s['status'] = 'stopped'
                        s['player_count'] = '0'
                        break
                refresh_cluster()
                return
            try:
                proc.terminate()
                proc.wait(timeout=6)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            finally:
                self.server_procs.pop(ppath, None)
                # stop any backup worker
                try:
                    self.stop_backup_worker(ppath)
                except Exception:
                    pass
                self.log(f"Stopped server at {ppath}")
                # Update status
                for s in self.servers:
                    if str(s.get('path')) == str(ppath):
                        s['status'] = 'stopped'
                        s['player_count'] = '0'
                        break
                refresh_cluster()

        # Backup worker management
        def _do_backup(server_path: str, dest_dir: Optional[Path]):
            try:
                src = Path(server_path)
                if not src.exists() or not dest_dir:
                    return
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                out_name = f"backup_{timestamp}.zip"
                out_path = Path(dest_dir) / out_name
                # Simple zip of the 'Saved' folder if present
                saved = src / 'Saved'
                if not saved.exists():
                    return
                shutil.make_archive(str(out_path.with_suffix('')), 'zip', root_dir=str(saved))
                self.log(f"Backup created: {out_path}")
            except Exception as e:
                self.log(f"Backup failed for {server_path}: {e}")

        def start_backup_worker(self_path: str, dest_dir: Optional[Path], hours: int):
            # hours: interval in hours
            stop_ev = threading.Event()
            self.backup_workers[self_path] = stop_ev

            def worker():
                try:
                    while not stop_ev.wait(hours * 3600):
                        _do_backup(self_path, dest_dir)
                except Exception:
                    pass

            t = threading.Thread(target=worker, daemon=True)
            t.start()

        def stop_backup_worker(self_path: str):
            ev = self.backup_workers.get(self_path)
            if not ev:
                return
            try:
                ev.set()
            except Exception:
                pass

        # expose the helpers as instance methods so other callbacks can call them
        try:
            self.start_server_for = start_server_for
            self.stop_server_for = stop_server_for
        except Exception:
            pass

        # Schedule runner: checks schedule list every minute and triggers actions
        def _parse_schedules() -> list:
            try:
                raw = (self.config.get('server_settings') or {}).get('backup_schedules_json') or ''
                if not raw:
                    return []
                return json.loads(raw)
            except Exception:
                return []

        def _run_schedule_once(action: str, server_path: str):
            try:
                if action == 'backup':
                    # use configured backup dir for the server or global
                    bdir = None
                    for s in self.servers:
                        if str(s.get('path')) == str(server_path):
                            bdir = s.get('backup_dir') or (s.get('server_settings') or {}).get('backup_dir')
                            break
                    if not bdir:
                        bdir = self.config.get('backup_dir') or (self.config.get('server_settings') or {}).get('backup_dir')
                    self.log(f"Scheduled backup triggered for {server_path}, dest={bdir}")
                    _do_backup(server_path, Path(bdir) if bdir else None)
                elif action == 'restart':
                    # stop then start
                    self.log(f"Scheduled restart for {server_path}")
                    try:
                        stop_server_for(server_path)
                    except Exception:
                        pass
                    time.sleep(1)
                    try:
                        start_server_for(server_path)
                    except Exception:
                        pass
            except Exception as e:
                self.log(f"Schedule action failed: {e}")

        def _schedule_worker(server_path: str):
            stop_ev = threading.Event()
            self._schedule_stop_event = stop_ev
            last_run_map = self._schedule_last_run
            try:
                while not stop_ev.wait(60):
                    # check schedules for this server (global schedules applied to all servers)
                    sch = _parse_schedules()
                    now = datetime.now(timezone.utc).strftime('%H:%M')
                    for it in sch:
                        act = it.get('action')
                        tstr = (it.get('time') or '').strip()
                        if not act or not tstr:
                            continue
                        # avoid retriggering within same minute
                        key = f"{server_path}|{act}|{tstr}"
                        if last_run_map.get(key) == now:
                            continue
                        if tstr == now:
                            # trigger
                            try:
                                _run_schedule_once(act, server_path)
                            except Exception:
                                pass
                            last_run_map[key] = now
            except Exception:
                pass

        def start_schedule_runner(server_path: str):
            # Stop any existing schedule worker first
            try:
                if self._schedule_stop_event:
                    try:
                        self._schedule_stop_event.set()
                    except Exception:
                        pass
                self._schedule_last_run = {}
                t = threading.Thread(target=_schedule_worker, args=(server_path,), daemon=True)
                t.start()
            except Exception:
                pass

        def stop_schedule_runner():
            try:
                if self._schedule_stop_event:
                    self._schedule_stop_event.set()
                    self._schedule_stop_event = None
            except Exception:
                pass
        # expose as instance methods
        try:
            self.start_schedule_runner = start_schedule_runner
            self.stop_schedule_runner = stop_schedule_runner
        except Exception:
            pass

        btn_frame = ttk.Frame(cluster_tab)
        btn_frame.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(btn_frame, text="Start", command=start_selected).pack(side=tk.LEFT, padx=6, pady=6)
        ttk.Button(btn_frame, text="Stop", command=stop_selected).pack(side=tk.LEFT, padx=6, pady=6)
        # Additional cluster controls: Create, Select/Edit, Delete
        def create_server():
            dlg = CreateServerDialog(self.root)
            self.root.wait_window(dlg.win)
            if not dlg.result:
                return
            new = dlg.result
            self.servers.append(new)
            persist_known_servers()
            try:
                start_schedule_runner(new.get('path'))
            except Exception:
                pass
            refresh_cluster()

        def select_server():
            sel = cluster_list.selection()
            if not sel:
                messagebox.showinfo("Select", "Select a server to edit.")
                return
            vals = cluster_list.item(sel[0])['values']
            path = vals[1]
            # find server entry
            chosen = None
            for s in self.servers:
                if str(s.get('path')) == str(path):
                    chosen = s
                    break
            if not chosen:
                messagebox.showerror("Not found", "Selected server entry not found.")
                return
            # set current config to the chosen server and update UI
            self.config = chosen
            # update labels
            try:
                self.lbl_server_folder.config(text=f"Server folder: {self.config.get('path')}")
                self.lbl_backup_folder.config(text=f"Backup folder: {self.config.get('backup_dir')}")
                self.lbl_cluster_id.config(text=f"Cluster ID: {self.config.get('cluster_id')}")
            except Exception:
                pass
            # Load server_settings from INI files (actual saved values) first
            server_folder = Path(path)
            ss_from_ini = {}
            if server_folder.exists():
                try:
                    ss_from_ini = ServerManager.read_server_settings(server_folder)
                except Exception:
                    pass
            
            # Fall back to in-memory server_settings if INI files don't have values
            ss = chosen.get('server_settings', {}) or {}
            # Merge: INI values take precedence, then in-memory values, then defaults
            merged_settings = {}
            # Start with defaults - get defaults from restore_defaults function
            defaults = {
                "no_tribute_downloads": False,
                "enable_pvp": True,
                "crossplay": False,
                "allow_fly": True,
                "show_map_location": True,
                "max_players": 70,
                "game_port": 7777,
                "query_port": 27015,
                "rcon_port": 32330,
                "server_password": "",
                "rcon_password": "",
                "session_name": "",
                "map": "TheIsland",
                "difficulty_offset": 1.0,
                "day_speed": 1.0,
                "night_speed": 1.0,
                "override_official_difficulty": 1.0,
                "player_xp_multiplier": None,
                "player_taming_speed": None,
                "player_health_multiplier": None,
                "dino_xp_multiplier": None,
                "dino_health_multiplier": None,
                "dino_taming_speed": None,
                "global_damage_multiplier": None,
                "melee_damage_multiplier": None,
                "resource_harvest_multiplier": None,
                "spawn_rate_multiplier": None,
                "structure_health_multiplier": None,
                "structure_build_cost_multiplier": None,
                "loot_respawn_interval": None,
                "additional_args": "",
                "auto_backup": False,
                "backup_interval": 24,
                "autosave_settings": True,
                "mods_enabled": False,
                "mods_list": "",
            }
            merged_settings.update(defaults)
            # Then in-memory settings (filter out None values)
            for k, v in ss.items():
                if v is not None:
                    merged_settings[k] = v
            # Finally INI file settings (highest priority - actual saved values, filter out None)
            for k, v in ss_from_ini.items():
                if v is not None:
                    merged_settings[k] = v
            
            # Load merged settings into settings_vars (only set non-None values)
            for k, var in self.settings_vars.items():
                if k in merged_settings:
                    value = merged_settings[k]
                    if value is not None:
                        try:
                            var.set(value)
                        except Exception:
                            pass
            try:
                start_schedule_runner(chosen.get('path'))
            except Exception:
                pass
            # switch to Settings tab for editing
            try:
                tabs.select(settings_tab)
            except Exception:
                pass

        # Double-click on a cluster entry should select it and switch to the Manager/Settings tab
        def _on_cluster_double_click(event):
            # Determine the item under the cursor
            iid = cluster_list.identify_row(event.y)
            if not iid:
                return
            # select it in the treeview
            cluster_list.selection_set(iid)
            # call the select_server flow
            try:
                select_server()
                # after selecting, switch to the Settings tab so the fields are visible
                tabs.select(settings_tab)
            except Exception:
                pass

        cluster_list.bind('<Double-1>', _on_cluster_double_click)

        def delete_server():
            sel = cluster_list.selection()
            if not sel:
                return
            vals = cluster_list.item(sel[0])['values']
            name = vals[0]
            if not messagebox.askyesno("Delete", f"Delete server '{name}' from cluster list?"):
                return
            # remove
            self.servers = [s for s in self.servers if s.get('name') != name or s.get('path') != vals[1]]
            persist_known_servers()
            try:
                stop_schedule_runner()
            except Exception:
                pass
            refresh_cluster()

        ttk.Button(btn_frame, text="Create", command=create_server).pack(side=tk.RIGHT, padx=6)
        ttk.Button(btn_frame, text="Select/Edit", command=select_server).pack(side=tk.RIGHT, padx=6)
        ttk.Button(btn_frame, text="Delete", command=delete_server).pack(side=tk.RIGHT, padx=6)

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def log(self, *parts):
        self.log_text.insert(tk.END, " ".join(map(str, parts)) + "\n")
        self.log_text.see(tk.END)

    def open_server_folder(self):
        p = self.config.get("path") or self.config.get("server_path")
        if p and Path(p).exists():
            try:
                os.startfile(p)
                return
            except Exception:
                pass
        # if current config not helpful, try first known server
        try:
            if self.servers:
                p2 = self.servers[0].get('path')
                if p2 and Path(p2).exists():
                    try:
                        os.startfile(p2)
                        return
                    except Exception:
                        pass
        except Exception:
            pass
        messagebox.showinfo("Not found", "Server folder not found on disk.")

    def open_backup_folder(self):
        p = self.config.get("backup_dir") or self.config.get('backup_dir')
        if p and Path(p).exists():
            try:
                os.startfile(p)
                return
            except Exception:
                pass
        # fallback: check first known server
        try:
            if self.servers:
                p2 = self.servers[0].get('backup_dir')
                if p2 and Path(p2).exists():
                    try:
                        os.startfile(p2)
                        return
                    except Exception:
                        pass
        except Exception:
            pass
        messagebox.showinfo("Not found", "Backup folder not found on disk.")

    def edit_settings(self):
        # Open a new launcher setup window
        cfg = {
            "server_path": self.config.get("server_path") or self.config.get("path", ""),
            "backup_dir": self.config.get("backup_dir", ""),
            "cluster_id": self.config.get("cluster_id", ""),
            "server_settings": self.config.get("server_settings", {})
        }
        setup = tk.Toplevel(self.root)
        LauncherApp(setup)

    def start_server(self):
        # Support both shapes: top-level launcher config uses 'server_path',
        # while per-server entries use 'path'. Try both for robustness.
        server_folder = self.config.get("server_path") or self.config.get("path")
        if not server_folder:
            messagebox.showerror("Error", "Server folder not configured.")
            return
        folder = Path(server_folder)
        if not folder.exists():
            messagebox.showerror("Error", "Server folder does not exist.")
            return

        exe = pick_exe_in_folder(folder)
        if not exe:
            # ask user for exe
            p = filedialog.askopenfilename(title="Select server executable", filetypes=[("EXE files", "*.exe")], initialdir=str(folder))
            if not p:
                return
            exe = Path(p)

        # Start the server process
        try:
            # Write INI files to this server's folder before starting
            if folder.exists():
                # Get this server's settings
                server_settings = self.config.get('server_settings', {})
                
                # Write INI files to this server's folder
                try:
                    ServerManager.write_game_user_settings(folder, server_settings)
                    ServerManager.write_game_ini(folder, server_settings)
                    self.log(f"INI files written to {folder}")
                except Exception as e:
                    self.log(f"Warning: Failed to write INI files to {folder}: {e}")
            
            # Save current working dir and cluster id as env variables if needed
            env = os.environ.copy()
            cluster_id = self.config.get("cluster_id", "")
            env["ASA_CLUSTER_ID"] = cluster_id
            
            # Build the server command with map, session name, ports, and flags
            server_cmd = ServerManager.build_server_command(server_settings, cluster_id)
            
            # Ensure backup dir exists
            backup_dir = Path(self.config.get("backup_dir", ""))
            if backup_dir and not backup_dir.exists():
                backup_dir.mkdir(parents=True, exist_ok=True)

            self.log(f"Starting: {exe}")
            self.log(f"Command: {' '.join([str(exe)] + server_cmd)}")
            # Use Popen so GUI stays responsive
            # Don't redirect stdout/stderr - let ARK server use its own console window
            self.server_proc = subprocess.Popen([str(exe)] + server_cmd, cwd=str(folder), env=env)
            self.log(f"PID: {self.server_proc.pid}")
            # Store process in server_procs for status tracking
            server_path = str(folder)
            self.server_procs[server_path] = self.server_proc
            # ARK server opens its own console window, so we don't create a ServerConsole
            # Update status in cluster list if this server is in the list
            for s in self.servers:
                if str(s.get('path')) == server_path or str(s.get('server_path')) == server_path:
                    s['status'] = 'running'
                    break
            # Refresh cluster list if it exists
            try:
                if hasattr(self, 'servers') and hasattr(self, '_build'):
                    # Try to refresh cluster list
                    pass  # Will be handled by update_statuses polling
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror("Start failed", str(e))

    def stop_server(self):
        server_path = str(self.config.get("server_path") or self.config.get("path") or "")
        if self.server_proc and self.server_proc.poll() is None:
            self.log("Stopping server PID", self.server_proc.pid)
            try:
                self.server_proc.terminate()
            except Exception:
                try:
                    self.server_proc.kill()
                except Exception:
                    pass
            # Remove from server_procs
            if server_path in self.server_procs:
                del self.server_procs[server_path]
            # Update status in cluster list
            for s in self.servers:
                if str(s.get('path')) == server_path or str(s.get('server_path')) == server_path:
                    s['status'] = 'stopped'
                    s['player_count'] = '0'
                    break
            # ARK server uses its own console window, so no ServerConsole to update
        else:
            self.log("No running server process.")
            # Update status anyway
            for s in self.servers:
                if str(s.get('path')) == server_path or str(s.get('server_path')) == server_path:
                    s['status'] = 'stopped'
                    s['player_count'] = '0'
                    break

    def browse_backup(self):
        # Allow user to choose or change backup folder from within the Manager settings
        p = filedialog.askdirectory(title="Select backup folder")
        if p:
            try:
                # update current config and label
                if isinstance(self.config, dict):
                    self.config['backup_dir'] = p
                try:
                    # Ensure we're in the ServerManager context
                    if hasattr(self, 'lbl_backup_folder'):
                        self.lbl_backup_folder.config(text=f"Backup folder: {p}")
                except Exception as e:
                    self.log(f"Error updating backup folder label: {e}")
                # persist change to known servers or top-level config
                try:
                    self.persist_known_servers()
                except Exception as e:
                    self.log(f"Error persisting known servers: {e}")
                    # fallback: save top-level config
                    try:
                        base = load_config()
                        base['backup_dir'] = p
                        save_config(base)
                    except Exception as e:
                        self.log(f"Error saving config: {e}")
            except Exception as e:
                self.log(f"Error in browse_backup: {e}")
                messagebox.showerror("Error", f"Failed to set backup folder: {e}")

    def _on_console_stop(self):
        try:
            self.log('Server process ended')
            # ARK server uses its own console window, so no ServerConsole to update
        except Exception:
            pass
        try:
            self.server_proc = None
        except Exception:
            pass

    def on_close(self):
        # Make sure server not left orphaned
        if self.server_proc and self.server_proc.poll() is None:
            if not messagebox.askyesno("Exit", "A server process is still running. Stop it and exit?"):
                return
            self.stop_server()
        try:
            # stop any schedule runner running
            try:
                self.stop_schedule_runner()
            except Exception:
                pass
        except Exception:
            pass
        self.root.destroy()
        # also exit the whole app
        try:
            self.parent_root.destroy()
        except Exception:
            pass


class ServerConsole:
    """A simple Tk window that displays server stdout/stderr, allows input, and shows status.

    It attaches to a subprocess.Popen instance with text mode pipes.
    """
    def __init__(self, master, cluster_id, proc, on_stop=None):
        self.master = master
        self.cluster_id = cluster_id
        self.proc = proc
        self.on_stop = on_stop
        self.win = tk.Toplevel(master)
        self.win.title(str(cluster_id))
        self.win.geometry('800x400')
        self.text = tk.Text(self.win, state='disabled', wrap='none')
        self.text.pack(fill=tk.BOTH, expand=True)
        ctrl = ttk.Frame(self.win)
        ctrl.pack(fill=tk.X)
        self.status_lbl = ttk.Label(ctrl, text='Stopped')
        self.status_lbl.pack(side=tk.LEFT, padx=6)
        self.start_btn = ttk.Button(ctrl, text='Start', command=self._on_start)
        self.start_btn.pack(side=tk.RIGHT, padx=6)
        self.stop_btn = ttk.Button(ctrl, text='Stop', command=self._on_stop)
        self.stop_btn.pack(side=tk.RIGHT)
        self.input_entry = ttk.Entry(self.win)
        self.input_entry.pack(fill=tk.X)
        self.input_entry.bind('<Return>', self._on_enter)
        # small canvas for colored status
        self.canvas = tk.Canvas(ctrl, width=16, height=16)
        self.canvas.pack(side=tk.LEFT)
        self.status_circle = self.canvas.create_oval(2,2,14,14, fill='red')
        self._reader_thread = None
        # if a process was provided, attach and start reader
        if self.proc:
            self.set_status('running' if self.proc.poll() is None else 'stopped')
            self._start_reader()

    def show(self):
        try:
            self.win.deiconify()
            self.win.lift()
        except Exception:
            pass

    def attach_process(self, proc):
        self.proc = proc
        if self.proc and self.proc.poll() is None:
            self.set_status('running')
            self._start_reader()

    def _start_reader(self):
        if self._reader_thread and self._reader_thread.is_alive():
            return
        def _reader():
            try:
                assert self.proc.stdout is not None
                buf = ''
                while True:
                    try:
                        # Read in chunks for faster updates
                        chunk = self.proc.stdout.read(1024)
                    except Exception:
                        chunk = ''
                    
                    if not chunk:
                        if self.proc.poll() is not None:
                            # Process ended, flush remaining buffer
                            if buf:
                                self._append_safe(buf + "\n")
                                buf = ""
                            break
                        # No data right now; yield briefly
                        time.sleep(0.05)
                        continue
                    
                    buf += chunk
                    
                    # Handle carriage-return updates (in-place progress lines) first
                    if "\r" in buf:
                        parts = buf.split("\r")
                        # Everything except the last part are interim updates
                        for p in parts[:-1]:
                            if p.strip():  # Only append non-empty parts
                                self._append_safe(p + "\n")
                        # Keep the last part as partial line
                        buf = parts[-1]
                    
                    # Handle full newline-terminated lines
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        self._append_safe(line + "\n")
            except Exception:
                pass
            finally:
                # when reader ends, mark stopped
                try:
                    self.win.after(0, lambda: self.set_status('stopped'))
                    if self.on_stop:
                        self.win.after(0, lambda: self.on_stop() if self.on_stop else None)
                except Exception:
                    pass
        self._reader_thread = threading.Thread(target=_reader, daemon=True)
        self._reader_thread.start()

    def _append_safe(self, text):
        """Thread-safe append that schedules GUI update on main thread."""
        try:
            self.win.after(0, lambda: self._append(text))
        except Exception:
            pass

    def _append(self, text):
        """Append text to the console (must be called from main thread)."""
        try:
            self.text.configure(state='normal')
            self.text.insert(tk.END, text)
            self.text.see(tk.END)
            self.text.configure(state='disabled')
        except Exception:
            pass

    def _on_enter(self, event=None):
        val = self.input_entry.get()
        if not val or not self.proc:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.write(val + '\n')
                self.proc.stdin.flush()
        except Exception:
            pass
        self.input_entry.delete(0, tk.END)

    def _on_start(self):
        # Starting a process from here is outside the console's scope
        messagebox.showinfo('Start', 'Start is managed from the launcher UI.')

    def _on_stop(self):
        try:
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
        except Exception:
            pass

    def set_status(self, st):
        st = st.lower()
        try:
            if st == 'running':
                self.status_lbl.configure(text='Running')
                self.canvas.itemconfig(self.status_circle, fill='green')
            elif st == 'stopping':
                self.status_lbl.configure(text='Stopping')
                self.canvas.itemconfig(self.status_circle, fill='yellow')
            else:
                self.status_lbl.configure(text='Stopped')
                self.canvas.itemconfig(self.status_circle, fill='red')
        except Exception:
            pass

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    def __del__(self):
        try:
            self.destroy()
        except Exception:
            pass


def main():
    # Load config and determine whether to skip setup
    cfg = load_config()
    server_path = cfg.get("server_path")
    if server_path:
        sp = Path(server_path)
        if sp.exists():
            # quick checks: look for steamcmd.exe or any server exe
            steam_found = list(sp.glob('**/steamcmd.exe'))
            exe_found = list(sp.glob('*.exe'))
            if steam_found or exe_found:
                # skip setup and open manager directly
                root = tk.Tk()
                root.withdraw()
                mgr = tk.Toplevel()
                ServerManager(mgr, cfg, parent_root=root)
                root.mainloop()
                return

    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()