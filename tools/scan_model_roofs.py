# -*- coding: utf-8 -*-
u"""扫描本机已有的真实项目模型，看能不能"取现成的真屋面"当模板。

安全设计：
  * 每次只处理一个文件，**先复制到 _survey/_scan_tmp/ 再打开**，原件绝不触碰
  * 每文件独立超时；失败/超时记录后继续
  * 结果**增量写入** _survey/roof_inventory.txt，便于外部监控
  * 顺带统计体量(Mass)元素 —— 自有工程里有个「体量.rvt」

用法：python _scan_existing_models.py
"""
import io
import json
import os
import shutil
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")
sys.path.insert(0, os.path.join(_REPO, "mcp"))
from genbuild.bridge import BridgeClient  # noqa: E402

TMPDIR = os.path.join(_REPO, "_survey", "_scan_tmp")
OUT = os.path.join(_REPO, "_survey", "roof_inventory.txt")

MODELS = [
    (os.path.join(_REPO, u"_models", u"3f046c573d_model.rvt"), u"3f046c573d"),
    (os.path.join(_REPO, u"_models", u"同济大学联合广场_多层_v1.rvt"), u"同济联合广场"),
    (os.path.join(_REPO, u"_models", u"城投大厦主楼平面_多层_v1.rvt"), u"城投大厦(多层)"),
    (os.path.join(_REPO, u"_models", u"城投大厦主楼平面_model_v2.rvt"), u"城投大厦v2"),
    (os.path.join(_REPO, u"_models", u"城投大厦主楼平面_model.rvt"), u"城投大厦"),
    (os.path.join(_DESKTOP, u"自有工程", u"体量.rvt"), u"自有工程-体量"),
    (os.path.join(_DESKTOP, u"自有工程", u"楼梯.rvt"), u"自有工程-楼梯"),
    (os.path.join(_DESKTOP, u"自有工程", u"项目1.rvt"), u"自有工程-项目1"),
    (os.path.join(_REPO, u"_snapshots", u"ay793_pre_del_多层_v1.rvt"), u"snapshot-多层v1"),
]

SCAN = u'''# -*- coding: utf-8 -*-
import json
# 注意：Revit 2019 **没有** DB.Mass 类（只有 MassInstanceUtils）。
# 顶层 import 一个不存在的名字会让整个载荷瞬间失败 —— 上一版就是栽在这，
# 表现为每个文件 0~1 秒返回 None。体量改用 OST_Mass 类别收集。
from Autodesk.Revit.DB import (RoofBase, FilteredElementCollector,
                               BuiltInParameter, BuiltInCategory)
out = {}
app = __revit__.Application
ft = 0.00328084

def NM(e):
    for a in ("Name", "get_Name"):
        try:
            v = getattr(e, a)
            return to_text(v() if callable(v) else v)
        except Exception:
            pass
    return u"?"

def area_m2(el):
    try:
        p = el.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
        return round(p.AsDouble() * 0.09290304, 2) if p else None
    except Exception:
        return None

d2 = app.OpenDocumentFile(r\'__P__\')
try:
    out["title"] = to_text(d2.Title)
    roofs = list(FilteredElementCollector(d2).OfClass(RoofBase).ToElements())
    out["roof_count"] = len(roofs)
    det = []
    for r in roofs[:12]:
        rec = {"id": int(r.Id.IntegerValue),
               "class": to_text(type(r).__name__),
               "area_m2": area_m2(r)}
        try:
            rec["type"] = NM(d2.GetElement(r.GetTypeId()))
        except Exception:
            pass
        try:
            rec["level"] = NM(d2.GetElement(r.LevelId))
        except Exception:
            pass
        try:
            p = r.get_Parameter(BuiltInParameter.ROOF_SLOPE)
            if p is not None:
                rec["slope"] = p.AsValueString() or p.AsDouble()
        except Exception:
            pass
        det.append(rec)
    out["roofs"] = det
    # 体量：用类别收集（DB.Mass 在 2019 不存在）
    try:
        ms = list(FilteredElementCollector(d2)
                  .OfCategory(BuiltInCategory.OST_Mass)
                  .WhereElementIsNotElementType().ToElements())
        out["mass_count"] = len(ms)
    except Exception as ex:
        out["mass_count"] = None
        out["mass_err"] = to_text(ex)[:120]
    # 屋面类型清单（即使没有实例，类型也可能有用）
    try:
        from Autodesk.Revit.DB import RoofType
        rts = list(FilteredElementCollector(d2).OfClass(RoofType).ToElements())
        out["roof_types"] = [NM(t) for t in rts[:20]]
    except Exception:
        pass
finally:
    try:
        d2.Close(False)
    except Exception:
        pass

_result = out
'''


def log(s):
    line = u"%s\n" % s
    with io.open(OUT, "a", encoding="utf-8") as f:
        f.write(line)
    print(line.rstrip())


def main():
    if not os.path.isdir(TMPDIR):
        os.makedirs(TMPDIR)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(u"=== 本机已有模型 · 真屋面盘点 ===\n")

    c = BridgeClient()
    summary = []
    for path, label in MODELS:
        if not os.path.isfile(path):
            log(u"[跳过] %s —— 文件不存在" % label)
            continue
        tmp = os.path.join(TMPDIR, u"scan.rvt")
        try:
            shutil.copyfile(path, tmp)
        except Exception as e:
            log(u"[失败] %s —— 复制失败: %s" % (label, e))
            continue
        code = SCAN.replace("__P__", tmp)
        t0 = time.time()
        try:
            r = c.call({"type": "execute_code", "code": code,
                        "no_transaction": True}, timeout=180)
            res = r.get("result", r)
            if isinstance(res, dict) and isinstance(res.get("result"), dict):
                res = res["result"]
        except Exception as e:
            log(u"[超时/失败] %s —— %s: %s（%.0fs）"
                % (label, type(e).__name__, str(e)[:80], time.time() - t0))
            summary.append((label, u"失败", u"-"))
            break
        if res.get("err"):
            log(u"[异常] %s —— %s" % (label, json.dumps(res, ensure_ascii=False)[:260]))
        n = res.get("roof_count")
        mc = res.get("mass_count")
        log(u"[%s] %s  -> 真屋面 %s 个，体量 %s 个（%.0fs）"
            % (label, path.split("\\")[-1], n, mc, time.time() - t0))
        if n:
            for d in res.get("roofs", []):
                log(u"        id=%s %s 面积=%s㎡ 类型=%s 标高=%s 坡度=%s"
                    % (d.get("id"), d.get("class"), d.get("area_m2"),
                       d.get("type"), d.get("level"), d.get("slope")))
        if res.get("roof_types"):
            log(u"        屋面类型: %s" % u", ".join(res["roof_types"][:8]))
        summary.append((label, n, mc))
        try:
            os.remove(tmp)
        except Exception:
            pass

    log(u"")
    log(u"=== 汇总 ===")
    for label, n, mc in summary:
        log(u"  %-16s 真屋面=%-6s 体量=%s" % (label, n, mc))
    log(u"DONE")


if __name__ == "__main__":
    main()
