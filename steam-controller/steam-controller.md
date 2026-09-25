# Steam Controller Jingler

Consists of the following tools:
- Backup manager
- todo

Tested on Linux (Bazzite 44/Fedora) and Windows 11 (todo)

## Requirements:

1. put the following 4 files  [`(click here)`](tool/)  in a folder
- scbackup.py
- schid.py
- requirements.txt
- firmware-table.json
2. install requirements for your system below

### Windows (not tested yet)

1. Install Python (3.11 or newer), either way:
   - from python.org/downloads: run the installer and tick "Add python.exe to PATH" on the first screen;
   - or in a terminal: winget install Python.Python.3.13.
2. Check it in a new terminal: py --version and py -m pip --version.
3. From the project folder: py -m pip install -r guide\requirements.txt


### Linux

1. Python is almost always installed already: python3 --version. # needs python 3.11 or newer
2. pip, if it's missing:
   - Debian/Ubuntu/Mint: sudo apt install python3-pip python3-venv
   - Fedora: sudo dnf install python3-pip
   - Arch: sudo pacman -S python-pip
3. Many distributions block pip for the whole system (error: externally-managed-environment).
On my system I ran a small environment inside the project folder, which also needs no sudo:
- python3 -m venv .venv
- source .venv/bin/activate         
- python -m pip install -r guide/requirements.txt
4.USB access: Users may need one extra permission rule (a udev rule).

## Run the backup-tool (backup firmware first)
- python scbackup.py
the tool will guilde you through the backup process
