ASA Dedicated Server Launcher (Windows)

This small Python utility provides a simple setup GUI for locating an ASA Dedicated Server installation, selecting a backup folder, and setting a cluster ID. After setup it opens a server manager window that can start/stop the server executable and open folders.

Requirements
- Windows 10 or later
- Python 3.8+ (Tkinter required — usually included with the standard Windows installer)

How to run
1. Open PowerShell in this folder:

```powershell
cd "c:\Users\burge\OneDrive\Documents\ASAM"
python launcher.py
```

What it does
- On start the setup window will attempt to auto-detect a folder named like "ASA Dedicated Server" in common locations.
- If not found you can browse to the installation or open the download page.
 - If not found you can browse to the installation or install via SteamCMD (preferred). The launcher will look for `steamcmd` on PATH and if not found will ask you to locate `steamcmd.exe`.
- Configure the backup directory and cluster ID.
- Click "Save & Open Manager" to persist settings to %LOCALAPPDATA%\\ASA_Server_Launcher\\config.json and open the Server Manager window.

Where settings are stored
- %LOCALAPPDATA%\\ASA_Server_Launcher\\config.json

Notes & next steps
- This is a minimal launcher for development and testing. If you want, I can add:
  - A deeper recursive search option
  - An installer helper to download and extract the server
  - More robust process supervision and logs
  - Windows service support

To install via SteamCMD
- Make sure SteamCMD is available on the machine. You can download SteamCMD from Valve and place `steamcmd.exe` somewhere on the system and add it to PATH, or simply point the launcher to `steamcmd.exe` when prompted.
- When you click "Install (download)" the launcher will ask for the Steam AppID for the dedicated server, then run SteamCMD with `+force_install_dir <target> +app_update <appid> validate +quit` and stream output into the installer dialog.

Replace the download URL in `launcher.py` if you still need a direct web installer link — by default the launcher uses SteamCMD.
