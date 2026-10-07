# -*- coding: utf-8 -*-
"""
selftest.py —— 阶段3 pyRevit 扩展的 headless 校验（不需要 Revit GUI）。

做七件事：
  1) 对每个 script.py / lib/*.py / dwg_to_json.py / make_icons.py 做 py_compile 语法检查；
  2) 用 pythonnet 真实加载本机 RevitAPI.dll，核对脚本里用到的 Revit API
     类/枚举是否真的存在于 Revit 2019（捕捉拼写/版本错配）；
  3) 校验扩展目录结构（自动发现：每个 .pushbutton 都要有 script.py + icon.png）；
  4) IronPython 2.7 兼容实跑 tests/ironpython_harness.py（mock 测试台，覆盖全部按钮）；
  5) 图纸翻模转换器：dwg_to_json.py --selftest + bimconvert.plan_from_json 往返；
  6) 部署自校验器的自测：确认 deploy._verify_mirror 真的能抓出不一致；
  7) MCP Bridge 命令分档表：COMMAND_TIERS 恰好覆盖 COMMAND_HANDLERS（含自测）；
  8) MCP Bridge 策略闸门的行为矩阵（在桩环境里 exec 真实 bridge_core.py）。

用法：
  python selftest.py            # 常规：已登记缺陷计为"已知"，退出码 0
  python selftest.py --strict   # 严格：SKIP 一律 FAIL，已登记缺陷也不容忍
"""
import os
import sys
import py_compile
import importlib.util

# 控制台可能是 GBK（中文 Windows 默认）。第 4/5 关会把子进程输出原样打印出来，
# 其中可能含 GBK 装不下的字符（例如解码替换符 U+FFFD）—— 那会让整个校验
# 以 UnicodeEncodeError 崩掉，看起来像"测试失败"，其实只是打印问题。
# 2026-10-01 实测踩到：第 4 关之后直接 traceback。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(HERE, "BIMToolkit.extension")
LIB = os.path.join(EXT, "lib")

# 本扩展实际用到的 Revit 2019 API 符号（类 / 枚举）
NEEDED = [
    "Autodesk.Revit.DB.FilteredElementCollector",
    "Autodesk.Revit.DB.ElementIsElementTypeFilter",
    "Autodesk.Revit.DB.Transaction",
    "Autodesk.Revit.DB.Level",
    "Autodesk.Revit.DB.ElementLevelFilter",
    "Autodesk.Revit.DB.BuiltInParameter",
    "Autodesk.Revit.DB.BuiltInCategory",
    "Autodesk.Revit.DB.Category",
    "Autodesk.Revit.DB.ParameterType",
    "Autodesk.Revit.DB.View",
    "Autodesk.Revit.DB.ViewSheet",
    "Autodesk.Revit.DB.Family",
    "Autodesk.Revit.DB.FamilySymbol",
    "Autodesk.Revit.DB.GeometryCreationUtilities",
    "Autodesk.Revit.DB.DirectShape",
    "Autodesk.Revit.DB.CurveLoop",
    "Autodesk.Revit.DB.JoinGeometryUtils",
    "Autodesk.Revit.DB.FailureMessage",
    "Autodesk.Revit.DB.FailureSeverity",
    "Autodesk.Revit.DB.ElementId",
    "Autodesk.Revit.DB.StorageType",
]

# 需要存在的 BuiltInParameter 成员名（脚本里直接 getattr 引用）
BIP_MEMBERS = [
    "SCHEDULE_LEVEL_PARAM",
    "SYMBOL_FAMILY_AND_TYPE_NAMES_PARAM",
    "ALL_MODEL_INSTANCE_COMMENTS",
    "ALL_MODEL_MARK",
    "FAMILY_SHARED",
]

REVIT_API = r"D:/Autodesk/Revit 2019/RevitAPI.dll"


def check_syntax():
    files = []
    for root, _, fns in os.walk(EXT):
        for fn in fns:
            if fn.endswith(".py"):
                files.append(os.path.join(root, fn))
    files.append(os.path.join(HERE, "make_icons.py"))
    results = []
    for f in sorted(files):
        try:
            py_compile.compile(f, doraise=True)
            results.append((os.path.relpath(f, HERE), True, ""))
        except py_compile.PyCompileError as e:
            results.append((os.path.relpath(f, HERE), False, str(e)[:120]))
    return results


def check_api():
    try:
        import clr
        clr.AddReference(REVIT_API)
        from Autodesk.Revit import DB
    except Exception as e:
        return None, "RevitAPI 加载失败: %s" % e
    results = []
    for sym in NEEDED:
        mod, name = sym.rsplit(".", 1)
        try:
            m = __import__(mod, fromlist=["__name__"])
            ok = hasattr(m, name)
        except Exception as e:
            ok = False
        results.append((sym, ok, "" if ok else "缺失"))
    # BuiltInParameter 成员
    for bip in BIP_MEMBERS:
        ok = hasattr(DB.BuiltInParameter, bip)
        results.append(("BuiltInParameter.%s" % bip, ok, "" if ok else "枚举缺失"))
    return results, None


def check_structure():
    """校验 `BIMToolkit.tab` 下每个 .pushbutton 都有 script.py + icon.png。

    2026-10-01：从手写 7 个改为**自动发现**（实际 19 个），
    消除"加了按钮但测试没跟上"的静默漏测。
    """
    tab = os.path.join(EXT, "BIMToolkit.tab")
    results = []
    for root, dirs, _files in os.walk(tab):
        for d in sorted(dirs):
            if not d.endswith(".pushbutton"):
                continue
            full = os.path.join(root, d)
            rel = os.path.relpath(full, tab)
            has_script = os.path.isfile(os.path.join(full, "script.py"))
            has_icon = os.path.isfile(os.path.join(full, "icon.png"))
            ok = has_script and has_icon
            results.append(
                (rel, ok,
                 "" if ok else ("缺 script.py" if not has_script else "缺 icon.png")))
    return sorted(results)


def check_ironpython(strict=False):
    """第 4 关：用 IronPython 2.7 兼容模拟器实跑**全部**按钮脚本 + 逻辑校验。

    以子进程运行 tests/ironpython_harness.py，避免模拟器对 open() 的 monkeypatch
    污染主进程。返回 (all_pass, detail_lines)。
    """
    import subprocess
    harness = os.path.join(HERE, "tests", "ironpython_harness.py")
    if not os.path.isfile(harness):
        return False, ["    [WARN] 未找到 tests/ironpython_harness.py，跳过"]
    try:
        cmd = [sys.executable, harness]
        if strict:
            cmd.append("--strict")
        proc = subprocess.Popen(cmd,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        text = out.decode("utf-8", "replace")
        lines = text.strip().splitlines()
        ok = proc.returncode == 0 and (
            "[HARNESS] ALL PASS" in text
            or "[HARNESS] PASS WITH REGISTERED DEFECTS" in text)
        return ok, ["    " + ln for ln in lines]
    except Exception as e:
        return False, ["    [ERR] 运行 harness 失败: %s" % e]


def check_dwgconvert(strict=False):
    """第 5 关：图纸翻模转换器 headless 校验。

    (a) dwg_to_json.py --selftest：用 ezdxf 造临时 DWG → 转中性 JSON → 断言几何抽取正确；
    (b) bimconvert.plan_from_json 往返：用本扩展自带 mapping_rules.csv 做规则匹配。
    ezdxf 缺失（非 bim-dev venv）则 SKIP (a)，但 (b) 仍跑（纯 python）。
    `--strict` 下 SKIP 视为 FAIL —— 否则"没装依赖"会被伪装成"通过"。
    """
    import subprocess
    details = []
    ok = True

    # (a) dwg_to_json --selftest（需要 ezdxf）
    try:
        import ezdxf  # noqa: F401
    except Exception:
        if strict:
            ok = False
            details.append("    [FAIL] 当前环境无 ezdxf，--strict 下不再容忍跳过 "
                           "(a)（请在 bim-dev venv 里跑）")
        else:
            details.append("    [SKIP] 当前环境无 ezdxf（dwg_to_json 需在 bim-dev venv 跑），"
                           "跳过 (a)")
        a_ok = None
    else:
        conv = os.path.join(HERE, "dwg_to_json.py")
        try:
            proc = subprocess.Popen([sys.executable, conv, "--selftest"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out, _ = proc.communicate()
            text = out.decode("utf-8", "replace")
            a_ok = proc.returncode == 0
            ok = ok and bool(a_ok)
            for ln in text.strip().splitlines():
                details.append("    " + ln)
        except Exception as e:
            ok = False
            details.append("    [ERR] dwg_to_json --selftest 失败: %s" % e)

    # (b) plan_from_json 往返（纯 python，不需要 ezdxf）
    try:
        sys.path.insert(0, LIB)
        from bimconvert import load_rules, plan_from_json, consolidate_walls
        rules_csv = os.path.join(EXT, "BIMToolkit.tab", "BIM 工具.panel",
                                 "DWG翻模.pushbutton", "mapping_rules.csv")
        rules = load_rules(rules_csv)
        sample = {
            "source": "sample.dwg", "format": "dwg",
            "layers": ["GRID", "WALL", "COL", "DOOR"],
            "blocks": ["COL-A", "DOOR-S"],
            "entities": [
                {"type": "line", "layer": "GRID", "handle": "1",
                 "start": [0, 0, 0], "end": [10000, 0, 0]},
                {"type": "lwpolyline", "layer": "WALL", "handle": "2", "closed": True,
                 "points": [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0]]},
                {"type": "insert", "layer": "COL", "handle": "3",
                 "block": "COL-A", "point": [500, 500, 0]},
                {"type": "insert", "layer": "DOOR", "handle": "4",
                 "block": "DOOR-S", "point": [200, 0, 0]},
            ],
        }
        planned, unmatched = plan_from_json(sample, rules, ["1F", "2F"])
        assert unmatched == [], "unmatched=%s" % unmatched
        assert len(planned) == 4, "planned=%s" % planned
        details.append("    [PASS] bimconvert.plan_from_json 往返：%d 计划 / %d 未匹配"
                       % (len(planned), len(unmatched)))
    except Exception as e:
        ok = False
        details.append("    [FAIL] plan_from_json 往返: %s" % e)

    # (c) consolidate_walls：零散墙段链成连续墙线（纯 python，不需要 Revit）
    try:
        mkwall = lambda s, e: {
            "element_type": "wall", "reliability": "mature", "level": "1F",
            "height": 3000, "type_hint": "", "source": "w",
            "geom": {"type": "line", "start": list(s), "end": list(e), "layer": "WALL"},
            "note": ""}
        planned_in = [
            mkwall([0, 0], [1000, 0]),       # a：沿 x
            mkwall([1000, 0], [3000, 0]),    # b：共线续接
            mkwall([3000, 0], [3000, 2000]), # c：转角向上（同一链）
            mkwall([5000, 5000], [6000, 5000]),  # d：孤立段
            {"element_type": "grid", "reliability": "mature", "level": "1F",
             "height": 0, "type_hint": "", "source": "g",
             "geom": {"type": "line", "start": [0, 0], "end": [1000, 0]}, "note": ""},
        ]
        merged = consolidate_walls(planned_in)
        n_wall = sum(1 for p in merged if p["element_type"] == "wall")
        n_grid = sum(1 for p in merged if p["element_type"] == "grid")
        # v2.8b 新契约：L 转角拆为两道贴面墙（消墙重叠，崩溃实录驱动），
        # 4 段 -> 3 面墙；且两两带厚矩形零重叠（Revit「墙重叠」风暴的根源）
        assert n_wall == 3, "n_wall=%s" % n_wall
        assert n_grid == 1, "n_grid=%s" % n_grid
        rects = []
        for p in merged:
            if p["element_type"] != "wall":
                continue
            pts = p["geom"]["points"]
            if len(pts) != 2:
                continue
            (x1, y1), (x2, y2) = pts[0][:2], pts[1][:2]
            if abs(x1 - x2) > 1 and abs(y1 - y2) > 1:
                continue
            t = float(p.get("thickness_mm") or 240)
            if abs(y1 - y2) <= 1:
                rects.append((min(x1, x2), y1 - t / 2, max(x1, x2), y1 + t / 2))
            else:
                rects.append((x1 - t / 2, min(y1, y2), x1 + t / 2, max(y1, y2)))
        for a in range(len(rects)):
            for b in range(a + 1, len(rects)):
                ra, rb = rects[a], rects[b]
                ox = min(ra[2], rb[2]) - max(ra[0], rb[0])
                oy = min(ra[3], rb[3]) - max(ra[1], rb[1])
                assert ox <= 1 or oy <= 1, \
                    "墙带厚重叠 %s x %s (%.0fx%.0f)" % (ra, rb, ox, oy)
        details.append("    [PASS] consolidate_walls：4 段墙 -> %d 面贴面墙，零带厚重叠，非墙 plan 不变"
                       % n_wall)
    except Exception as e:
        ok = False
        details.append("    [FAIL] consolidate_walls: %s" % e)

    # (d) mapping_rules.csv 含 STAIR -> stair 规则
    try:
        rules_csv2 = os.path.join(EXT, "BIMToolkit.tab", "BIM 工具.panel",
                                  "DWG翻模.pushbutton", "mapping_rules.csv")
        rules2 = load_rules(rules_csv2)
        assert any(r["element_type"] == "stair" for r in rules2), "缺少 stair 规则"
        details.append("    [PASS] mapping_rules.csv 含 STAIR->stair 规则")
    except Exception as e:
        ok = False
        details.append("    [FAIL] STAIR 规则: %s" % e)
    return ok, details


def check_deploy_selfcheck():
    """第 6 关：部署自校验器**本身**的自测。

    一个"永远通过"的校验器毫无价值。这一关造 3 类不一致
    （缺失 / 内容不同 / 多余），断言 `deploy._verify_mirror` 都能报出来，
    并断言运行时状态与 version.txt 不会被误报。
    """
    import subprocess
    script = os.path.join(HERE, "tests", "test_deploy_selfcheck.py")
    if not os.path.isfile(script):
        return False, ["    [WARN] 未找到 tests/test_deploy_selfcheck.py"]
    try:
        proc = subprocess.Popen([sys.executable, script],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        text = out.decode("utf-8", "replace")
        ok = proc.returncode == 0 and "RESULT: OK" in text
        return ok, ["    " + ln for ln in text.strip().splitlines()]
    except Exception as e:
        return False, ["    [ERR] 运行部署自校验自测失败: %s" % e]


def _analyze_tier_source(src):
    """分析 bridge_core.py 源码文本，返回 (ok, details)。

    抽成独立函数是为了能喂"被故意改坏的源码"进来 ——
    一个永远通过的校验器毫无价值（同第 6 关的做法）。
    """
    import re
    m = re.search(r"COMMAND_HANDLERS\s*=\s*\{(.*?)\n\}", src, re.S)
    if not m:
        return False, ["    [FAIL] 解析不到 COMMAND_HANDLERS"]
    handlers = set(re.findall(r'"([a-z_][a-z0-9_]*)"\s*:', m.group(1)))

    m2 = re.search(r"COMMAND_TIERS\s*=\s*\{(.*?)\n\}", src, re.S)
    if not m2:
        return False, ["    [FAIL] 解析不到 COMMAND_TIERS"]
    tiers = {}
    for tm in re.finditer(
            r'"(read|write|dangerous)"\s*:\s*frozenset\(\[(.*?)\]\)',
            m2.group(1), re.S):
        tiers[tm.group(1)] = set(
            re.findall(r'"([a-z_][a-z0-9_]*)"', tm.group(2)))

    details = []
    ok = True
    for t in ("read", "write", "dangerous"):
        details.append("    [INFO] %-9s %2d 条" % (t, len(tiers.get(t, ()))))

    covered = set()
    for t in sorted(tiers):
        dup = covered & tiers[t]
        if dup:
            ok = False
            details.append("    [FAIL] 档位重叠: %s" % ", ".join(sorted(dup)))
        covered |= tiers[t]

    missing = sorted(handlers - covered)
    extra = sorted(covered - handlers)
    if missing:
        ok = False
        details.append("    [FAIL] %d 条命令未进分档表（safe/build 下会被拒）: %s"
                       % (len(missing), ", ".join(missing)))
    if extra:
        ok = False
        details.append("    [FAIL] 分档表含 %d 条不存在的命令: %s"
                       % (len(extra), ", ".join(extra)))
    if ok:
        details.append("    [PASS] %d 条命令全部且唯一分档" % len(handlers))
    return ok, details


def check_bridge_tiers():
    """第 7 关：MCP Bridge 的命令分档表是否**恰好**覆盖全部命令。

    分档表（COMMAND_TIERS）与命令表（COMMAND_HANDLERS）是两份清单。
    本项目已经吃过三次"两份清单不一致"的亏（部署排除表 / 按钮清单 / 测试清单），
    所以这一关把它们机械地钉在一起：
      新增命令忘了进分档表 -> FAIL（否则它在 safe/build 下会被静默拒绝）
      分档表里有已删除的命令 -> FAIL
      同一个命令出现在两个档 -> FAIL
    并**自测**这个校验器真的能报错（否则它就是个摆设）。
    """
    path = os.path.join(EXT, "BIMToolkit.tab", "BIM 工具.panel",
                        "MCP Bridge.pushbutton", "bridge_core.py")
    if not os.path.isfile(path):
        return False, ["    [WARN] 找不到 bridge_core.py"]
    with open(path, "rb") as f:
        src = f.read().decode("utf-8")

    ok, details = _analyze_tier_source(src)

    # ---- 自测：校验器必须能抓出"命令漏分档" ----
    broken = src.replace('"get_info",', "", 1)
    if broken == src:
        ok = False
        details.append("    [FAIL] 自测无法构造用例（get_info 不在分档表里？）")
    else:
        ok_bad, d_bad = _analyze_tier_source(broken)
        if ok_bad or not any("未进分档表" in d for d in d_bad):
            ok = False
            details.append("    [FAIL] 自测失败：漏掉一条命令时校验器没报错")
        else:
            details.append("    [PASS] 自测：漏分档/多余/重叠都能报错")
    return ok, details


def check_bridge_policy():
    """第 8 关：MCP Bridge 策略闸门的**行为**测试（在桩环境里 exec 真实代码）。

    与第 7 关的区别：第 7 关查"分档表覆盖全不全"（静态文本），
    这一关查"闸门真的按预期放行/拒绝吗"（把 bridge_core.py 跑起来断言行为矩阵）。

    必须走子进程：测试台会 monkeypatch `open()`，在主进程里 import 会污染其它关。
    """
    import subprocess
    script = os.path.join(HERE, "tests", "test_bridge_policy.py")
    if not os.path.isfile(script):
        return False, ["    [WARN] 未找到 tests/test_bridge_policy.py"]
    try:
        proc = subprocess.Popen([sys.executable, script],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        out, _ = proc.communicate()
        text = out.decode("utf-8", "replace")
        ok = proc.returncode == 0 and "RESULT: OK" in text
        return ok, ["    " + ln for ln in text.strip().splitlines()]
    except Exception as e:
        return False, ["    [ERR] 运行策略闸门测试失败: %s" % e]


def main():
    strict = "--strict" in sys.argv
    print("=== 阶段3/4/5 pyRevit 扩展 headless 校验 ===")
    if strict:
        print("[模式] --strict：跳过(SKIP)一律视为失败，已登记缺陷不容忍")
    print("")
    all_ok = True

    print("[1] 语法检查 (py_compile)")
    for rel, ok, msg in check_syntax():
        print("    [%s] %s %s" % ("PASS" if ok else "FAIL", rel, msg))
        all_ok = all_ok and ok

    print("\n[2] Revit API 符号核对 (RevitAPI.dll)")
    api_res, err = check_api()
    if err:
        # 以前这里只打印 SKIP 而不动 all_ok —— 换台机器/路径不对，
        # 这一关就形同虚设但退出码仍是 0。--strict 下改为 FAIL。
        print("    [%s] %s" % ("FAIL" if strict else "SKIP", err))
        if strict:
            all_ok = False
    else:
        for sym, ok, msg in api_res:
            print("    [%s] %s %s" % ("PASS" if ok else "FAIL", sym, msg))
            all_ok = all_ok and ok

    print("\n[3] 目录结构（自动发现全部 .pushbutton）")
    struct_res = check_structure()
    print("    共 %d 个按钮" % len(struct_res))
    for name, ok, msg in struct_res:
        print("    [%s] %s %s" % ("PASS" if ok else "FAIL", name, msg))
        all_ok = all_ok and ok

    print("\n[4] IronPython 2.7 兼容实跑 (tests/ironpython_harness.py)")
    ok4, lines4 = check_ironpython(strict)
    for ln in lines4:
        print(ln)
    all_ok = all_ok and ok4

    print("\n[5] 图纸翻模转换器 (dwg_to_json --selftest + plan_from_json 往返)")
    ok5, lines5 = check_dwgconvert(strict)
    for ln in lines5:
        print(ln)
    all_ok = all_ok and ok5

    print("\n[6] 部署自校验器的自测 (tests/test_deploy_selfcheck.py)")
    ok6, lines6 = check_deploy_selfcheck()
    for ln in lines6:
        print(ln)
    all_ok = all_ok and ok6

    print("\n[7] MCP Bridge 命令分档表覆盖校验")
    ok7, lines7 = check_bridge_tiers()
    for ln in lines7:
        print(ln)
    all_ok = all_ok and ok7

    print("\n[8] MCP Bridge 策略闸门行为测试")
    ok8, lines8 = check_bridge_policy()
    for ln in lines8:
        print(ln)
    all_ok = all_ok and ok8

    print("\n[SELFTEST]", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
