# -*- coding: utf-8 -*-
u"""patch v4: root-cause fix for hosted instances.
Bug: 4-arg NewFamilyInstance binds instance level to L1, ppt.z ignored
vertically -> all L2+ doors/windows physically at z~0 (uncut floaters,
13 t1500 windows deleted on cut attempt).
Fix: 5-arg overload NewFamilyInstance(ppt, sym, wall, lvl, st) with
ppt.z = lvl.Elevation + sill (verified probe_v18 F1a/F1b/F2/F4).
Steps: rail audit -> wipe d/w -> re-place all 49 -> per-instance bbz
audit inside host wall -> save -> verify."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
import build_villa2 as B  # noqa: E402


def run_full(code, label, timeout=280):
    code = code.replace("__PRE__", B.PRE_SRC)
    B.ID[0] += 1
    r = B.call({"type": "execute_code", "id": B.ID[0],
                "code": B.esc(code), "no_transaction": True},
               timeout=timeout)
    res = r.get("result", r)
    print(u"[%s] %s" % (label, json.dumps(res, ensure_ascii=False)))
    return res


# --- 0. railing audit + recreate if none ---
RAIL_AUDIT = u'''# -*- coding: utf-8 -*-
out = {}
out["ids"] = []
for rid in (334955, 334988):
    el = doc.GetElement(ElementId(rid))
    if el is None:
        out["ids"].append([rid, "none"])
    else:
        try:
            cn = to_text(el.GetType().Name)
        except Exception:
            cn = "?"
        try:
            ctn = to_text(el.Category.Name)
        except Exception:
            ctn = "?"
        out["ids"].append([rid, cn, ctn])
n = 0
for e in FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Railings).ToElements():
    if not isinstance(e, ElementType):
        n += 1
out["inst"] = n
_result = out
'''
res_ra = run_full(RAIL_AUDIT, u"栏杆甄别")
rail_ok = (res_ra.get("inst") or 0) >= 2
if not rail_ok:
    for b in (1, 2):
        code = B.RAILING.replace("__B__", str(b)).replace(
            "__PATH__", str(B.RAIL_PATH))
        run_full(code, u"栏杆补建%d" % (b + 1))

# --- 1. wipe doors/windows ---
WIPE_DW = u'''# -*- coding: utf-8 -*-
__PRE__
ids = []
for cat in (BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        ids.append(e.Id)
t = Transaction(doc, "wipe dw v4")
pre = _prep(t)
t.Start()
n = 0
for i in ids:
    try:
        doc.Delete(i)
        n += 1
    except Exception:
        pass
t.Commit()
_result = {"found": len(ids), "deleted": n}
'''
run_full(WIPE_DW, u"清空门窗")

# --- 2. re-place all with 5-arg overload ---
DOORS_V2 = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB.Structure import StructuralType
__PRE__
out = {"placed": 0, "fails": [], "audit": []}
ft = 0.00328084
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
ops = __OPS__
kind = "__KIND__"
cat = BuiltInCategory.OST_Doors if kind == "door" \\
    else BuiltInCategory.OST_Windows
syms = []
for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
    if isinstance(e, FamilySymbol):
        syms.append(e)
if len(syms) == 0:
    out["fails"].append("no symbols")
    _result = out
else:
    for s in syms:
        try:
            s.Activate()
        except Exception:
            pass
    t = Transaction(doc, "villa v2 " + kind)
    pre = _prep(t)
    t.Start()
    placed_ids = []
    for op in ops:
        try:
            px, py = op["point"][0] * ft, op["point"][1] * ft
            want_w = op.get("width_mm", 900)
            want_h = op.get("height_mm", 2100)
            sill = op.get("sill_mm", 0) or 0
            lvl = lvls[op.get("li", 0)]
            best = None
            best_d = 1e18
            for w in FilteredElementCollector(doc).OfClass(Wall) \\
                    .ToElements():
                p = w.get_Parameter(
                    BuiltInParameter.WALL_BASE_CONSTRAINT)
                if p is None or p.AsElementId() is None:
                    continue
                if p.AsElementId().IntegerValue != lvl.Id.IntegerValue:
                    continue
                c = w.Location.Curve
                r = c.Project(XYZ(px, py, lvl.Elevation))
                if r is None:
                    continue
                dxy = (r.XYZPoint.X - px) ** 2 + \\
                    (r.XYZPoint.Y - py) ** 2
                if dxy < best_d:
                    best_d = dxy
                    best = (w, r.XYZPoint)
            if best is None or best_d > (300 * ft) ** 2:
                out["fails"].append(
                    "op %s: no wall" % str(op["point"]))
                continue
            w, ppt = best
            sym = None
            best_s = None
            best_d2 = 1e18
            for s in syms:
                try:
                    wp = s.get_Parameter(
                        BuiltInParameter.FAMILY_WIDTH_PARAM)
                    if wp is not None and wp.HasValue:
                        d2 = abs(wp.AsDouble() / ft - want_w)
                        if d2 < best_d2:
                            best_d2 = d2
                            best_s = s
                except Exception:
                    pass
            sym = best_s if best_s is not None else syms[0]
            z = lvl.Elevation + sill * ft
            ppt2 = XYZ(ppt.X, ppt.Y, z)
            inst = doc.Create.NewFamilyInstance(
                ppt2, sym, w, lvl, StructuralType.NonStructural)
            if inst is None:
                out["fails"].append("op %s: none" % str(op["point"]))
                continue
            for pn, val in ((u"\\u5bbd\\u5ea6", want_w * ft),
                            (u"\\u9ad8\\u5ea6", want_h * ft),
                            (u"\\u5e95\\u9ad8\\u5ea6", sill * ft)):
                try:
                    ip = inst.LookupParameter(pn)
                    if ip is not None and not ip.IsReadOnly:
                        ip.Set(val)
                except Exception:
                    pass
            placed_ids.append(int(inst.Id.IntegerValue))
            out["placed"] += 1
        except Exception as ex:
            out["fails"].append("op %s: %s" % (
                str(op["point"]), to_text(ex)[:100]))
    t.Commit()
    out["fails_seen"] = []
    for iid in placed_ids:
        try:
            el = doc.GetElement(ElementId(iid))
            if el is None:
                out["fails_seen"].append("id %d died" % iid)
                continue
            bb = el.get_BoundingBox(None)
            h = el.Host
            hb = h.get_BoundingBox(None) if h is not None else None
            zmin = round(bb.Min.Z / ft)
            zmax = round(bb.Max.Z / ft)
            hz = ([round(hb.Min.Z / ft), round(hb.Max.Z / ft)]
                  if hb is not None else None)
            ok = (hb is not None and bb.Min.Z >= hb.Min.Z - ft and
                  bb.Max.Z <= hb.Max.Z + ft)
            tn = "?"
            try:
                tpe = doc.GetElement(el.GetTypeId())
                tp = tpe.get_Parameter(
                    BuiltInParameter.ALL_MODEL_TYPE_NAME)
                tn = to_text(tp.AsString()) if tp is not None else "?"
            except Exception:
                pass
            out["audit"].append([iid, zmin, zmax, hz,
                                 "OK" if ok else "OUT", tn])
        except Exception as ex:
            out["audit"].append([iid, "ERR", to_text(ex)[:60]])
    _result = out
'''

li_map = {B.LVL[0]: 0, B.LVL[1]: 1, B.LVL[2]: 2, B.LVL[3]: 3}
all_audit = []
for kind in ("door", "window"):
    ops = [o for o in B.openings() if o["kind"] == kind]
    for o in ops:
        o["li"] = li_map[o["level"]]
    code = DOORS_V2.replace("__OPS__", json.dumps(ops)).replace(
        "__KIND__", kind)
    res = run_full(code, u"重放-" + kind)
    all_audit += res.get("audit") or []

# --- 3. audit summary ---
bad = [a for a in all_audit if len(a) < 5 or a[4] != "OK"]
print(u"==== 逐实例审计: 总=%d 越界/异常=%d ====" % (
    len(all_audit), len(bad)))
for a in bad:
    print(u"  BAD %s" % json.dumps(a, ensure_ascii=False))

# --- 4. save + verify ---
res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])
res_v = run_full(B.VERIFY, u"验收")
