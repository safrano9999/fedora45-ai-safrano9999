#!/usr/bin/python3
"""Validate the Fedora system runtime independently of Hermes's private environment."""
import subprocess
import sys

raise SystemExit(subprocess.call([sys.executable, "-m", "pip", "check"]))
