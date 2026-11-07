#!/usr/bin/python3
"""Add missing layer requirements while reusing packages from the parent image.

RPM metadata remains visible to pip. No --upgrade, --ignore-installed or forced
reinstall: a satisfied parent dependency must not get another copy in this layer.
Incompatible RPM requirements fail normally rather than hiding the RPM install.
"""
import runpy
import sys

sys.argv = ["pip", "install", "--break-system-packages", *sys.argv[1:]]
runpy.run_module("pip", run_name="__main__")
