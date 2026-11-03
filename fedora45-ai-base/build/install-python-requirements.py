#!/usr/bin/python3
"""Update image application packages in /usr/local, leaving Fedora RPMs intact.

Hide only the distro's site-packages from this pip process. This lets pip
upgrade its own previous installs normally, without uninstalling RPM files or
leaving duplicate pip metadata via --ignore-installed. Runtime paths are unchanged.
"""
import re
import runpy
import sys

sys.path[:] = [path for path in sys.path if not re.match(
    r"^/usr/lib(?:64)?/python[^/]+/(?:site|dist)-packages(?:/|$)", path)]
sys.argv = ["pip", "install", "--upgrade", "--prefix=/usr/local", *sys.argv[1:]]
runpy.run_module("pip", run_name="__main__")
