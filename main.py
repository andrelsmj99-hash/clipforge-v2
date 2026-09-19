"""
Clip Forge V2 — Standalone Desktop Executable Entry Point.
Used by Flet Pack / PyInstaller to bundle the application into an .exe.
"""

import multiprocessing
import sys
from clipforge.core.config import setup_playwright_browsers_path
from clipforge.ui.app import start_app

if __name__ == "__main__":
    # Required for Windows executables using multiprocessing/threads
    multiprocessing.freeze_support()
    setup_playwright_browsers_path()
    start_app()
