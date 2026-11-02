"""Make the deployment-owned integration importable under pytest's importlib mode."""

from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
