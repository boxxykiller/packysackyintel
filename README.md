# The Packy Sacky Intel Tool

A Windows desktop tool for EVE Online that watches your Local chat and your intel channels.

- **Finds Local by itself.** Pure screen OCR (Windows built-in OCR) - it does not attach to or read the game.
  Works with several clients; each is labelled with its account name and can be locked to.
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
    winrt-Windows.Globalization
python screenwatch.py
```

## Build the exe

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "The Packy Sacky Intel Tool" --collect-submodules winrt ^
    --add-data "data;data" screenwatch.py
```

## Map data

`data/` holds three CSV tables from CCP's Static Data Export (via the Fuzzwork SDE dump):
`mapSolarSystems.csv`, `mapSolarSystemJumps.csv`, `mapRegions.csv`. EVE Online and its data are
property of CCP hf.
