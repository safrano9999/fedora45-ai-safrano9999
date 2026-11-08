#!/usr/bin/python3
"""Add missing layer requirements while reusing packages from the parent image.

RPM metadata remains visible to pip. No --upgrade, --ignore-installed or forced
reinstall: a satisfied parent dependency must not get another copy in this layer.
Incompatible RPM requirements fail normally rather than hiding the RPM install.
"""
import subprocess
import sys

subprocess.run([sys.executable, "-m", "pip", "install", "--break-system-packages", *sys.argv[1:]], check=True)
subprocess.run([sys.executable, "/usr/local/libexec/check-python-packages"], check=True)
