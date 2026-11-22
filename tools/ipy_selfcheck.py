# -*- coding: utf-8 -*-
"""ipy_selfcheck.py —— 用**真的** IronPython 2.7 跑的两项检查。

必须由 IronPython 2.7 自己执行（ci.ps1 的 ipy gate 负责调用），不是 CPython：

    ipy.exe tools\\ipy_selfcheck.py

为什么需要它：仓库原本的"第 4 关 IronPython 2.7 实跑"其实是在 CPython 3.13 上
exec 脚本，于是它既测不出 f-string 这类语法不兼容，也测不出整数除法这类**语义**
不兼容（CPython3 默认真除）。2026-10-07 在真 IPY2.7 上实测：
`50/770*100.0` -> `0.0`，bimhealth 的警告密度扣分恒为 0 —— 这类问题只有这个
解释器能看见。

本文件自身必须保持 Py2/Py3 双兼容。
"""
from __future__ import print_function

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXT = os.path.join(REPO, "pyrevit-bim-panel", "BIMToolkit.extension")
LIB = os.path.join(EXT, "lib")

FAILURES = []


def check_engine():
    if not sys.version.startswith("2.7"):
        FAILURES.append("必须用 IronPython 2.7 跑本脚本，当前是 %s" % sys.version)
    print("  interpreter : %s" % sys.version.split()[0])
    impl = getattr(sys, "implementation", None)
    print("  impl        : %s" % (getattr(impl, "name", "n/a") if impl else "n/a"))
    # 注意：这里跑到的往往是独立安装的 IronPython（实测 2.7.3），
    # 而 pyRevit 内置引擎目录叫 IPY2712PR（2.7.12）。两者在语法与除法语义上
    # 一致，所以本关能测到目标问题；但别把它当成"与 Revit 内完全同一解释器"。


def check_syntax():
    """把扩展里每个 .py 用真 IPY 编译一遍（内存编译，不落 .pyc）。"""
    n = 0
    for dirpath, dirnames, filenames in os.walk(EXT):
        if "__pycache__" in dirpath:
            continue
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            n += 1
            try:
                with open(path, "rb") as f:
                    src = f.read()
            except Exception as e:
                FAILURES.append("读不了 %s: %s" % (path, e))
                continue
            try:
                compile(src, path, "exec")
            except SyntaxError as e:
                FAILURES.append("IPY2.7 语法不兼容 %s:%s %s"
                                % (os.path.relpath(path, EXT), e.lineno, e.msg))
            except Exception as e:
                FAILURES.append("编译异常 %s: %s" % (path, e))
    print("  compiled    : %d 个 .py" % n)


def check_division_semantics():
    """真除语义必须在真 IPY 上成立 —— CPython3 测不到这一条。"""
    sys.path.insert(0, LIB)
    import bimhealth
    clean = bimhealth.health_score(770, 0, 0, 0, 0)
    low = bimhealth.health_score(770, 20, 0, 0, 0)
    high = bimhealth.health_score(770, 60, 0, 0, 0)
    print("  health_score: clean=%s warn20=%s warn60=%s" % (clean, low, high))
    if not (low < clean and high < low):
        FAILURES.append(
            "bimhealth 警告密度扣分不随警告数变化 —— 整数除法又回来了。"
            "检查 lib/bimhealth.py 的 from __future__ import division")

    import standards_checker as sc
    if not hasattr(sc, "division"):
        FAILURES.append(
            "standards_checker 缺 from __future__ import division"
            "（它要算层高平均值/跨厚比，整数除法会静默偏小）")


def main():
    print("=== IronPython 2.7 真机自检 ===")
    check_engine()
    check_syntax()
    check_division_semantics()
    if FAILURES:
        print("\n[FAIL] %d 项：" % len(FAILURES))
        for f in FAILURES:
            print("  - %s" % f)
        return 1
    print("\n[OK] 真 IronPython 2.7 下语法与除法语义全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
