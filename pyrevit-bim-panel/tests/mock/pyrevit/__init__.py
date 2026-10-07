# -*- coding: utf-8 -*-
"""Fake pyrevit package — headless stand-in for IronPython 2.7 testing.

Inject tests/mock onto sys.path so `import pyrevit` resolves here instead of
the real (Revit-only) pyRevit. Models only the surface BIMToolkit uses.
"""
from . import db as DB
from . import revit_mod as revit
from . import ui_mod as UI
from . import output_mod as script
from . import forms_mod as forms
