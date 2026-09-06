"""Handload Fetcher — by PeterCottontail2023 (github.com/PeterCottontail2023).

Looks up reloading load data for a cartridge across manufacturer sites.
"""
import sys
from pathlib import Path

# pdfplumber (and its own transitive deps) are vendored into ./vendor rather
# than a venv, matching ../ballistics' setup -- see README.md. This runs for
# every entry point (python3 -m handloadfetcher, or `import handloadfetcher`
# from a script) since it lives in the package's own __init__.
sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

__title__ = "Handload Fetcher"
__author__ = "PeterCottontail2023"
__author_url__ = "https://github.com/PeterCottontail2023"
