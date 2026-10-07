# -*- coding: utf-8 -*-
"""Mock of pyrevit.script (output window)."""


class _Output(object):
    def __init__(self):
        self.lines = []

    def print_md(self, s):
        self.lines.append(s)

    def print_html(self, s):
        self.lines.append(s)


_OUT = _Output()


def get_output():
    return _OUT
