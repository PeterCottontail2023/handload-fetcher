#!/bin/bash
# Double-click this (in Finder) to run Handload Fetcher -- no terminal typing
# required. It handles first-time setup (vendoring dependencies) itself.
# The first double-click may get a Gatekeeper "unidentified developer"
# prompt (right-click -> Open once to clear it) -- that's macOS quarantining
# any downloaded script, not something specific to this one.

cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 was not found on this computer."
    echo "Install it from https://python.org/downloads, then double-click this file again."
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
    echo "load without it. Install it with: brew install poppler"
    echo "(no Homebrew? see https://brew.sh first)"
    echo
fi

python3 -m handloadfetcher "$@"
echo
read -r -p "Press Enter to close..."
