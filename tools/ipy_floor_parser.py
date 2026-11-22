# -*- coding: utf-8 -*-
"""Run the floor-number parser under REAL IronPython 2.7 and print ASCII only.

Compile-checking proves syntax; it does NOT prove that lookbehind, named groups
and str.isdigit() behave the same on .NET's regex engine as on CPython.
"""
from __future__ import print_function
import sys, os

LIB = r"E:\bim-toolkit\pyrevit-bim-panel\BIMToolkit.extension\lib"
sys.path.insert(0, LIB)
import bimconv_rules as br

LV = [u"\u5730\u4e0b\u4e00\u5c42", u"\u5730\u4e0b\u4e8c\u5c42", u"\u9996\u5c42",
      u"\u4e8c\u5c42", u"\u4e94\u5c42", u"\u516d\u5c42", u"\u4e03\u5c42",
      u"\u516b\u5c42", u"\u5341\u4e00\u5c42", u"\u5341\u4e8c\u5c42",
      u"\u4e8c\u5341\u56db\u5c42", u"\u4e8c\u5341\u516d\u5c42",
      u"\u6807\u51c6\u5c42", u"\u5c4b\u9762\u5c42"]
CASES = [
    (u"\u4e8c\u5341\u56db\u5c42\u5e73\u9762\u56fe", u"\u4e8c\u5341\u56db\u5c42"),
    (u"\u5341\u4e8c\u5c42\u5e73\u9762\u56fe", u"\u5341\u4e8c\u5c42"),
    (u"\u4e8c\u5341\u516d\u5c42\u5e73\u9762", u"\u4e8c\u5341\u516d\u5c42"),
    (u"\u9996\u5c42\u5e73\u9762\u56fe", u"\u9996\u5c42"),
    (u"-1\u5c42\u5e73\u9762", u"\u5730\u4e0b\u4e00\u5c42"),
    (u"7-27\u5355\u6570\u5c42\u5e73\u9762", u"\u4e03\u5c42"),
    (u"\u5730\u4e0b\u5ba4\u4e8c\u5c42\u6218\u65f6\u4eba\u9632\u5e73\u9762\u56fe", u"\u5730\u4e0b\u4e8c\u5c42"),
    (u"5#\u6807\u51c6\u5c42\u5e73\u9762 1:100", u"\u6807\u51c6\u5c42"),
    (u"\u673a\u623f\u5c4b\u9876\u5e73\u9762", u"\u5c4b\u9762\u5c42"),
    (u"\u897f\u7acb\u9762\u56fe", None),
    (u"1-1\u5256\u9762\u56fe", None),
    (u"29\u5c42\u5e73\u9762", None),
]
bad = 0
out = [u"engine=%s" % sys.version.split()[0]]
for t, want in CASES:
    got, ev = br.level_from_text(t, LV)
    ok = (got == want)
    if not ok:
        bad += 1
    out.append(u"%s in=%s want=%s got=%s ev=%s" % (
        u"OK " if ok else u"BAD", t, u"%s" % want, u"%s" % got, ev))
# _cn_to_int semantics that differ by engine risk: isdigit on unicode
for s, want in [(u"\u4e8c\u5341\u516d", 26), (u"\u5341", 10), (u"\u4e5d", 9),
                (u"\u4e8c\u5341", 20), (u"\u4e00\u5341", 10), (u"99", 99),
                (u"\u5341\u4e00", 11), (u"\u4e5d\u5341\u4e5d", 99)]:
    v = br._cn_to_int(s)
    if v != want:
        bad += 1
        out.append(u"BAD cn_to_int %s -> %s want %s" % (s, v, want))
out.append(u"BAD=%d" % bad)
# 结果写 UTF-8 文件：ipy2.7 的 unicode_escape 与 CPython 不同（实测吐 \x00），
# 而 GBK 控制台又打不出中文——落文件再读是唯一不打折的读法。
import io
io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                    "_tools", "ipy_floor_parser.txt"), "w",
        encoding="utf-8").write(u"\n".join(out))
print("BAD=%d" % bad)
sys.exit(1 if bad else 0)
