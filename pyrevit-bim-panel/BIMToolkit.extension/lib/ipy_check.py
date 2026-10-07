# -*- coding: utf-8 -*-
"""用 pyRevit 自带的 IronPython 2.7.12 引擎（Revit 2019 legacy 加载器同款）
对扩展里所有 .py 做真实语法编译检查。用法：
    python ipy_check.py <目录或文件...>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clr  # pythonnet

IPY_DIR = r"C:\Program Files\pyRevit-Master\bin\netfx\engines\IPY2712PR"
from System.Reflection import Assembly  # noqa: E402

for _dll in ("pyRevitLabs.Microsoft.Scripting.dll",
             "pyRevitLabs.Microsoft.Dynamic.dll",
             "pyRevitLabs.Microsoft.Scripting.Metadata.dll",
             "pyRevitLabs.IronPython.dll",
             "pyRevitLabs.IronPython.Modules.dll"):
    Assembly.LoadFile(os.path.join(IPY_DIR, _dll))

from IronPython.Hosting import Python          # noqa: E402
from Microsoft.Scripting import SourceCodeKind # noqa: E402

engine = Python.CreateEngine()


def check_file(path):
    """返回 None=通过，否则错误信息。"""
    with open(path, "rb") as f:
        raw = f.read()
    # 模拟 pyRevit：按源文件字节交给 IronPython 解析（保留 coding 声明语义）
    text = raw.decode("utf-8-sig")
    src = engine.CreateScriptSourceFromString(text, path, SourceCodeKind.File)
    try:
        compiled = src.Compile()
        if compiled is not None:
            return None
        return "(compile 返回 None 且无异常?)"
    except Exception as ex:
        return str(ex)


def collect(targets):
    files = []
    for t in targets:
        if os.path.isfile(t) and t.endswith(".py"):
            files.append(t)
        elif os.path.isdir(t):
            for root, _dirs, names in os.walk(t):
                for n in names:
                    if n.endswith(".py"):
                        files.append(os.path.join(root, n))
    return files


def main():
    targets = sys.argv[1:] or ["."]
    files = collect(targets)
    bad = 0
    for f in sorted(files):
        err = check_file(f)
        if err:
            bad += 1
            print("[FAIL] " + f)
            print("       " + err.replace("\n", "\n       "))
        else:
            print("[ OK ] " + f)
    print("")
    print("结果: %d 个文件, %d 个失败" % (len(files), bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
