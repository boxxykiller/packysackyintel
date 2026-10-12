"""Build the exe and write latest.json (version, size, SHA-256) next to it.

To publish a new version:  1) bump VERSION in version.py   2) python build.py   3) git add -A, commit, push.
Running copies of the app see the new latest.json on launch and update themselves."""
import hashlib
import json
import os
import subprocess
import sys

from version import ASSET_NAME, VERSION

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
subprocess.check_call([sys.executable, "-m", "PyInstaller", "--onefile", "--windowed", "--name", ASSET_NAME[:-4],
                       "--collect-submodules", "winrt", "--add-data", os.path.join(here, "data") + ";data",
                       "--distpath", here, "--workpath", os.path.join(here, "build"),
                       "--specpath", os.path.join(here, "build"), "screenwatch.py"])
exe = os.path.join(here, ASSET_NAME)
data = open(exe, "rb").read()
meta = {"version": VERSION, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
with open("latest.json", "w") as fh:
    json.dump(meta, fh, indent=2)
    fh.write("\n")
print("built", ASSET_NAME, meta)
