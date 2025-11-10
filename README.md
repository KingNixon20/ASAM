ASA Dedicated Server Launcher (Windows)

This small Python utility provides a simple setup GUI for locating an ASA Dedicated Server installation, selecting a backup folder, and setting a cluster ID. After setup it opens a server manager window that can start/stop the server executable and open folders.

Requirements
- Windows 10 or later
- Python 3.8+ (Tkinter required — usually included with the standard Windows installer)

How to run
1. Open PowerShell in this folder:

```powershell
cd "./ASAM"
python launcher.py
```

What it does
- On start the setup window will attempt to auto-detect and/or install the Ark Ascended Dedicated Server
- The launcher will work to run and manage clusters, view activity, and load mods/settings

