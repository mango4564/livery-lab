"""Writable app directory is beside the EXE, never PyInstaller's internal tree."""
from pathlib import Path
import sys

FROZEN=bool(getattr(sys,'frozen',False))
APP_DIR=Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
