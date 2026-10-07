# -*- coding: utf-8 -*-
"""
MCP Bridge v7.4 - thin launcher.

ALL logic lives in bridge_core.py (an imported module). Why: pyRevit clears
the command script's module globals after each run, which killed every
post-command callback with UnboundNameException (verified 2026-09-10).
Imported-module globals survive, so the WinForms Timer tick handler keeps
working after this command exits. See bridge_core.py docstring for details.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# v7.4: force a fresh import so a simple re-click (no pyRevit Reload)
# picks up edits to bridge_core.py from disk - IronPython caches modules
# in sys.modules. The old running instance is NOT disturbed: it is
# retired through the shared owner_id handshake in start().
sys.modules.pop("bridge_core", None)

import bridge_core

bridge_core.start(__revit__)
