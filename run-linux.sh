#!/bin/bash
# Run this to start Handload Fetcher -- no typing python commands required.
# It handles first-time setup (vendoring dependencies) itself.
#
# Double-click behavior varies by desktop environment (some file managers
# run a .sh directly, some open it in a text editor, some ask first) -- if
# double-clicking doesn't launch it, right-click -> "Run" / "Run in
# Terminal", or open a terminal here and run: ./run-linux.sh

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 was not found on this computer."
    echo "Install it with your package manager, e.g.: sudo apt install python3 python3-pip"
    echo
    read -r -p "Press Enter to close..."
    exit 1
fi

if [ ! -d "handloadfetcher/vendor/pdfplumber" ]; then
    echo "Setting up Handload Fetcher for the first time -- this only happens once..."
    python3 -m pip install --target=handloadfetcher/vendor -r requirements.txt \
        || python3 -m pip install --break-system-packages --target=handloadfetcher/vendor -r requirements.txt
    echo
fi

if ! command -v pdftotext >/dev/null 2>&1; then
    echo "Note: \"pdftotext\" wasn't found. A couple of manufacturers' data won't"
    echo "load without it. Install it with: sudo apt install poppler-utils"
    echo "(or your distro's equivalent package -- poppler-utils on Fedora/Arch too)"
    echo
fi

python3 -m handloadfetcher "$@"
echo
read -r -p "Press Enter to close..."
