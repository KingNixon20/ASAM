<img src="images/ark.png" alt="ark" width="25" padding="25">ASA Dedicated Server Launcher (Windows)

<p align="center">
  <img src="images/p1.png" alt="p1" width="200" style="margin-right:10px;">
  <img src="images/p2.png" alt="p2" width="200" style="margin-right:10px;">
  <img src="images/p3.png" alt="p3" width="200" style="margin-right:10px;">
  <img src="images/p4.png" alt="p4" width="200">
</p>

ASAM (Ark Server Ascended Manager) is a Windows launcher and manager for Ark: Survival Ascended dedicated servers. It lets you view servers, edit settings, handle backups, manage mods, schedule restarts, and more from one place.

The launcher can auto-detect common server folders, create missing server or backup folders when you choose a path, and install SteamCMD or the dedicated server when needed.

## Requirements
- Windows 10 or later
- Python 3.8+ (Tkinter required — usually included with the standard Windows installer)

## How to Run
1. Open PowerShell in this folder:

```powershell
cd "./ASAM"
python launcher.py
```

If your system uses `python3` instead of `python`, use that command name instead.

## Usage Notes
- Choose a server folder and backup folder from the launcher. Missing folders are created automatically when you save or open the manager.
- Use `Install (SteamCMD)` to download SteamCMD into the selected server folder if it is not already installed.
- Use `Save & Open Manager` to persist the current paths and open the server manager window.

## Download
<a href="https://sourceforge.net/p/asam/"><img alt="Download ASA Server Manager" src="https://sourceforge.net/sflogo.php?type=18&amp;group_id=3928931" width=200></a>

## Credit
<a target="_blank" href="https://icons8.com/icon/rLJtBvI9A7KS/ark-survival-evolved">Ark Survival Evolved</a> icon by <a target="_blank" href="https://icons8.com">Icons8</a>
