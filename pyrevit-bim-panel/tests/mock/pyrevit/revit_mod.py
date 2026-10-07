# -*- coding: utf-8 -*-
"""Mock of pyrevit.revit module (Document + Transaction)."""
from . import db


class FakeDocument(object):
    def GetWarnings(self):
        return [
            db.FakeFailureMessage(u"重叠的墙", db.FailureSeverity.Error,
                                  [db.ElementId(10), db.ElementId(11)]),
            db.FakeFailureMessage(u"未约束的轴线", db.FailureSeverity.Warning,
                                  [db.ElementId(12)]),
            db.FakeFailureMessage(u"重叠的墙", db.FailureSeverity.Error,
                                  [db.ElementId(13)]),
        ]

    def GetElement(self, eid):
        return db.FakeLevel(1)


doc = FakeDocument()


class Transaction(object):
    def __init__(self, name):
        self.name = name

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False
