"""Test package — ensures web_ui is on sys.path for all discovery modes."""
import sys
from pathlib import Path

_WEB_UI = str(Path(__file__).resolve().parent.parent)
if _WEB_UI not in sys.path:
    sys.path.insert(0, _WEB_UI)
