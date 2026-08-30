import sys
import os

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR_DIR = os.path.join(_CURRENT_DIR, '_vendor')
if os.path.exists(_VENDOR_DIR):
    if sys.platform != 'win32':
        if _VENDOR_DIR not in sys.path:
            sys.path.insert(0, _VENDOR_DIR)
    else:
        if _VENDOR_DIR not in sys.path:
            sys.path.append(_VENDOR_DIR)

from main import app

if __name__ == "__main__":
    app.run()
