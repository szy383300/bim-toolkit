# -*- coding: utf-8 -*-
"""IronPython 2.7 行为模拟器（在 CPython 上复刻三个不兼容契约）。

目的：在没有真实 IronPython 2.7 运行时的环境下，仍能在「部署前」抓出
pyRevit(IronPython 2.7) 才会抛的运行期错误。复刻的契约：
  1) open() 不接受 encoding / newline / errors 关键字（IronPython 2.7 的
     open 签名是 open(name[, mode[, buffering]])）-> 旧代码 open(path, encoding=...)
     会 TypeError。
  2) FilteredElementCollector 无 .Count 属性（见 mock/db.py）。
  3) FilteredElementCollector 不可直接 list()（见 mock/db.py）。

说明：这是「行为模拟器」而非真实 IronPython 解释器。真实 IronPython 2.7
（ipy.exe）装不上本机（Py3.13 host 无对应分发），但模拟器覆盖了踩过的
三类坑，足以在部署前拦截同类错误。若日后有 ipy，直接 `ipy tests/harness.py`
即可走真实解释器（脚本本身 Py2/Py3 兼容）。
"""
import sys

try:
    import __builtin__ as _bk
except ImportError:
    import builtins as _bk

_ORIG_OPEN = _bk.open


def _iron_open(name, mode="r", buffering=-1, **kwargs):
    for bad in ("encoding", "newline", "errors"):
        if bad in kwargs:
            raise TypeError(
                "open() got an unexpected keyword argument '%s'" % bad)
    return _ORIG_OPEN(name, mode, buffering, **kwargs)


_bk.open = _iron_open
