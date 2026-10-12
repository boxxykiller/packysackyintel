# The Packy Sacky Intel Tool

A Windows desktop tool for EVE Online that watches your Local chat and your intel channels.

- **Finds Local by itself.** Pure screen OCR (Windows built-in OCR) - it does not attach to or read the game.
  Works with several clients; each is labelled with its account name.
- **Lock to one client.** Pick an EVE client from the list and only that client's window is read - even when
  it is covered by another EVE client or any other window. This uses Windows Graphics Capture, the same window
  capture OBS and Discord use: Windows hands over the picture of the window it already has. It follows the
  window if it moves or resizes, and pauses while it is minimized or closed, resuming on its own.
- **No interaction with the game (EULA).** Clients are found from Windows' own list of open windows (title
  `EVE - <account>`, position, minimized or not). The tool never opens, attaches to, reads the memory of,
  injects into, or sends input or messages to the EVE client - it only looks at the window's pixels, the
  same picture a screen recorder gets.
- **Alerts on neutral / hostile only.** Reads the standing icon next to each name; corp (green), fleet (purple)
  and allies (blue) are ignored. Spoken message, siren, optional popup.
- **Local list** of everyone seen this session, newcomers highlighted.
- **Intel feed** tails EVE's own chat log files (`Documents/EVE/logs/Chatlogs`, channels with "intel" in the name).
  System names and pilot names are clickable links to zKillboard.
- **Mini map** of the region / nearby systems with your location (from the Local chat log), intel reports
  and gate-jump distances. Zoom, pan, pop out to its own window.

## Run from source

Requires Windows 10/11 and Python 3.11+.

```
pip install pillow winrt-runtime winrt-Windows.Media.Ocr winrt-Windows.Graphics.Imaging ^
    winrt-Windows.Storage.Streams winrt-Windows.Foundation winrt-Windows.Foundation.Collections ^
    winrt-Windows.Globalization winrt-Windows.Graphics winrt-Windows.Graphics.Capture ^
    winrt-Windows.Graphics.Capture.Interop winrt-Windows.Graphics.DirectX ^
    winrt-Windows.Graphics.DirectX.Direct3D11 winrt-Windows.Graphics.DirectX.Direct3D11.Interop
python screenwatch.py
```

## Build the exe

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "The Packy Sacky Intel Tool" --collect-submodules winrt ^
    --add-data "data;data" --add-data "assets;assets" --icon assets/logo.ico screenwatch.py
```

## Map data

`data/` holds three CSV tables from CCP's Static Data Export (via the Fuzzwork SDE dump):
`mapSolarSystems.csv`, `mapSolarSystemJumps.csv`, `mapRegions.csv`. EVE Online and its data are
property of CCP hf.
