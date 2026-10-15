"""Double-click to run Vocalm from the source code without a console window. The built app is Vocalm.exe."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vocalm.app import run  # noqa: E402

run()
