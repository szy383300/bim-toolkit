# -*- coding: utf-8 -*-
u"""check_env.py —— 解释器与依赖的**契约校验**。

为什么需要它
------------
2026-10-01 调查发现本机有 **6 个 Python 解释器**在用，用途各不相同：

| 用途 | 解释器 |
|---|---|
| pyRevit 扩展 headless 校验 / 部署 | `E:\\AI-pyenvs\\bim-dev` |
| genbuild（「配置建模」按钮的子进程） | 同上 |
| DWG 转 JSON / DWG 降版 / CAD AI 指令（桌面 .bat） | `.workbuddy\\…\\envs\\default` |
| AutoCAD MCP | `E:\\video-gen\\venv` |
| MCP 适配器（`.workbuddy\\mcp.json`） | 任意 Python 3（纯标准库） |
| 平面图模型评估 | `.workbuddy\\…\\envs\\default` |

问题不是"有 6 个"，而是**没人知道哪个该装什么** —— 于是出现
"按 README 用 `bim-dev` 跑 genbuild，结果 `import yaml` 失败"这种事（S9 实测）。

本脚本把那层隐含约定变成**可执行的契约**：谁该有什么包，缺了就报出来。
这是"解释器收敛"最实用的一半 —— 收敛前先让碎片**可见、可验收**；
真要把 6 个并成 1 个，是需要你拍板的运维决定（见脚本末尾说明）。

用法
----
    <任意 python3> tools/check_env.py
    <任意 python3> tools/check_env.py --json
"""
import argparse
import json
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")

# ---------------------------------------------------------------------------
# 契约：用途 -> (解释器路径, 必须存在的 import 名, 说明)
# 改这里就是改契约；改完跑一遍即可验收。
# ---------------------------------------------------------------------------
CONTRACT = [
    {
        "purpose": u"pyRevit 扩展 headless 校验 / 部署",
        "python": os.path.join(REPO, "..", "AI-pyenvs", "bim-dev",
                               "Scripts", "python.exe"),
        "requires": ["ezdxf", "clr", "win32com", "PIL", "openpyxl", "pandas",
                     "yaml"],
        "note": u"selftest.py / deploy.py / genbuild 都用它；yaml 是 S9 补的",
    },
    {
        "purpose": u"genbuild（配置建模按钮的子进程）",
        "python": os.path.join(REPO, "..", "AI-pyenvs", "bim-dev",
                               "Scripts", "python.exe"),
        "requires": ["yaml"],
        "note": u"genbuild/spec.py 需要 PyYAML",
    },
    {
        "purpose": u"DWG 转 JSON / 降版 / CAD AI 指令（桌面 .bat）",
        "python": os.path.join(HOME, ".workbuddy", "binaries", "python",
                               "envs", "default", "Scripts", "python.exe"),
        "requires": ["ezdxf", "win32com", "yaml"],
        "note": u"桌面 桌面工具\\*.bat 里的 %PY% 指向它",
    },
    {
        "purpose": u"平面图模型评估（tools/floorplan_model_eval.py）",
        "python": os.path.join(HOME, ".workbuddy", "binaries", "python",
                               "envs", "default", "Scripts", "python.exe"),
        "requires": ["ultralytics", "cv2", "fitz"],
        "note": u"fitz=PyMuPDF，只有这个 env 齐备",
    },
    {
        "purpose": u"AutoCAD MCP",
        "python": os.path.join(REPO, "..", "video-gen", "venv", "Scripts",
                               "python.exe"),
        "requires": ["win32com"],
        "note": u"桌面\\启动AutoCAD-MCP.bat",
    },
    {
        "purpose": u"MCP 适配器（.workbuddy\\mcp.json）",
        "python": os.path.join(HOME, ".workbuddy", "binaries", "python",
                               "versions", "3.13.12", "python.exe"),
        "requires": [],
        "note": u"revit_mcp_gateway / bimtoolkit_mcp 都是纯标准库，不需要第三方包",
    },
]

PROBE = (u"import importlib.util as u, json, sys;"
         u"print(json.dumps([m for m in sys.argv[1:]"
         u" if u.find_spec(m) is not None]))")


def probe(python_exe, modules):
    u"""返回 (存在?, 已装的模块列表, 说明)。"""
    if not os.path.isfile(python_exe):
        return False, [], u"解释器不存在"
    try:
        p = subprocess.run([python_exe, u"-c", PROBE] + list(modules),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=120)
    except Exception as e:
        return True, [], u"探测失败: %s" % e
    if p.returncode != 0:
        return True, [], u"探测返回 %d: %s" % (
            p.returncode, p.stderr.decode("utf-8", "replace")[:200])
    try:
        return True, json.loads(p.stdout.decode("utf-8").strip() or u"[]"), u""
    except Exception as e:
        return True, [], u"解析探测输出失败: %s" % e


def main(argv=None):
    ap = argparse.ArgumentParser(description=u"解释器与依赖契约校验")
    ap.add_argument("--json", action="store_true", help=u"输出 JSON")
    args = ap.parse_args(argv)

    results = []
    bad = 0
    for c in CONTRACT:
        exists, have, err = probe(c["python"], c["requires"])
        missing = [m for m in c["requires"] if m not in have]
        ok = exists and not missing and not err
        if not ok:
            bad += 1
        results.append({
            "purpose": c["purpose"],
            "python": c["python"],
            "exists": exists,
            "requires": c["requires"],
            "missing": missing,
            "ok": ok,
            "note": c["note"],
            "error": err,
        })

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 1 if bad else 0

    print(u"=== 解释器 / 依赖契约校验 ===")
    print(u"")
    for r in results:
        mark = u"OK  " if r["ok"] else u"FAIL"
        print(u"[%s] %s" % (mark, r["purpose"]))
        print(u"       解释器: %s" % r["python"])
        if not r["exists"]:
            print(u"       ⚠️ 解释器不存在")
        elif r["error"]:
            print(u"       ⚠️ %s" % r["error"])
        if r["missing"]:
            print(u"       缺少  : %s" % u", ".join(r["missing"]))
            print(u"       修法  : \"%s\" -m pip install %s"
                  % (r["python"], u" ".join(
                      [u"pyyaml" if m == u"yaml" else
                       u"opencv-python" if m == u"cv2" else
                       u"PyMuPDF" if m == u"fitz" else m
                       for m in r["missing"]])))
        elif r["requires"]:
            print(u"       依赖  : %s（齐全）" % u", ".join(r["requires"]))
        else:
            print(u"       依赖  : 无需第三方包")
    print(u"")
    if bad:
        print(u"[ENV] FAIL —— %d 项契约不满足" % bad)
    else:
        print(u"[ENV] OK —— %d 项契约全部满足" % len(results))
    print(u"")
    print(u"说明：本脚本只**校验**，不改任何环境。")
    print(u"      把 6 个解释器并成 1 个是运维决定，需要改桌面 .bat 与 "
          u"%USERPROFILE%\\.workbuddy\\mcp.json —— 那些不在仓库内，属你的机器配置。")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
