# -*- coding: utf-8 -*-
"""Mock of pyrevit.forms."""


def alert(msg, *args, **kwargs):
    # 测试台：返回 None（假值）→ 调用方一律走「取消」分支，不建构件
    return None


class CommandSwitchWindow(object):
    """测试台桩：兼容 CommandSwitchWindow(...) 与 .show(...) 两种调用，均返回 None。"""

    def __init__(self, *args, **kwargs):
        self.response = None

    @staticmethod
    def show(context=None, *args, **kwargs):
        return None


def SelectFromList(*args, **kwargs):
    return None


def save_file(*args, **kwargs):
    return None


def open_file(*args, **kwargs):
    # 测试台：返回 None → 调用方走「未选文件」分支提前 return，不建构件
    return None


def pick_file(*args, **kwargs):
    # 测试台：返回 None → 调用方走「未选文件」分支提前 return，不建构件
    return None
