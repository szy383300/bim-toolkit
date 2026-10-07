# -*- coding: utf-8 -*-
u"""Final villa patch: load proper door/window families, replace ALL
doors/windows with width-matched types, L2/L3 landings, roof x=0-plane
test + move, save, verify. Reuses build_villa2 templates."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
import build_villa2 as B  # noqa: E402

# widen B.DOORS width-matcher: nearest width instead of +/-60 exact
OLD_MATCH = '''            sym = None
            for s in syms:
                try:
                    wp = s.get_Parameter(
                        BuiltInParameter.FAMILY_WIDTH_PARAM)
                    if wp is not None and \\
                            abs(wp.AsDouble() / ft - want_w) < 60:
                        sym = s
                        break
                except Exception:
                    pass
            if sym is None:
                sym = syms[0]'''
NEW_MATCH = '''            sym = None
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
            sym = best_s if best_s is not None else syms[0]'''
assert OLD_MATCH in B.DOORS, "matcher block not found in B.DOORS"
DOORS_N = B.DOORS.replace(OLD_MATCH, NEW_MATCH)

# --- 1. load proper families (paths get \uXXXX-escaped by B.run) ---
LOAD = u'''# -*- coding: utf-8 -*-
out = {}
paths = [u"C:\\\\ProgramData\\\\Autodesk\\\\RVT 2019\\\\Libraries\\\\China\\\\建筑\\\\门\\\\普通门\\\\平开门\\\\单扇\\\\单嵌板木门 1.rfa",
         u"C:\\\\ProgramData\\\\Autodesk\\\\RVT 2019\\\\Libraries\\\\China\\\\建筑\\\\门\\\\普通门\\\\平开门\\\\双扇\\\\双面嵌板木门 1.rfa",
         u"C:\\\\ProgramData\\\\Autodesk\\\\RVT 2019\\\\Libraries\\\\China\\\\建筑\\\\窗\\\\普通窗\\\\固定窗\\\\固定窗.rfa"]
import os
for p in paths:
    k = u"\\\\".join(p.split(u"\\\\")[-2:])
    if not os.path.exists(p):
        out[k] = "FILE_MISSING"
        continue
    try:
        ok = doc.LoadFamily(p)
        out[k] = ("loaded" if ok else "already/failed")
    except Exception as ex:
        out[k] = to_text(ex)[:80]
_result = out
'''
r = B.run(LOAD, u"加载门窗族")
print("[加载详情]", json.dumps(r, ensure_ascii=False)[:600])

# --- 2. delete ALL existing door/window instances (wrong-width ones) ---
WIPE_DW = u'''# -*- coding: utf-8 -*-
__PRE__
ids = []
for cat in (BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        ids.append(e.Id)
t = Transaction(doc, "wipe doors windows")
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
B.run(WIPE_DW, u"清空旧门窗")

# --- 3. L2/L3 landings ---
for b in (1, 2):
    B.run(B.LANDING.replace("__B__", str(b)), u"平台%d" % (b + 1))

# --- 4. roof: x=0-plane profile + move hypothesis ---
ROOF_X0 = u'''# -*- coding: utf-8 -*-
__PRE__
out = {"variants": []}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl = lvls[-1]
rts = list(FilteredElementCollector(doc).OfClass(RoofType).ToElements())
views = list(FilteredElementCollector(doc).OfClass(ViewPlan).ToElements())
v = views[0]
ft = 0.00328084
z0 = lvl.Elevation

def roof_try(tag, bx, ex, profx, move_x):
    t = Transaction(doc, "roof " + tag)
    p = _prep(t)
    t.Start()
    try:
        try:
            rt2 = rts[0].Duplicate("VillaRoof_" + tag)
        except Exception:
            rt2 = rts[0]
        rp = doc.Create.NewReferencePlane2(
            XYZ(bx * ft, -600 * ft, 0), XYZ(bx * ft, 9600 * ft, 0),
            XYZ(0, 0, 1), v)
        ca = CurveArray()
        a = XYZ(profx * ft, -600 * ft, z0)
        bb = XYZ(profx * ft, 4500 * ft, z0 + 1700.0 * ft)
        c = XYZ(profx * ft, 9600 * ft, z0)
        ca.Append(Line.CreateBound(a, bb))
        ca.Append(Line.CreateBound(bb, c))
        ca.Append(Line.CreateBound(c, a))
        roof = doc.Create.NewExtrusionRoof(
            ca, rp, lvl, rt2, -6600.0 * ft, 6600.0 * ft)
        if move_x:
            doc.Move(roof.Id, XYZ(move_x * ft, 0, 0))
        t.Commit()
        return {"tag": tag, "id": int(roof.Id.IntegerValue)}
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"tag": tag, "err": to_text(ex)[:110]}

out["variants"].append(roof_try("x0", 0, 0, 0, 6000))
if not any("id" in v for v in out["variants"]):
    out["variants"].append(roof_try("x0b", 0, 0, 0, 6000))
made = None
for vr in out["variants"]:
    if "id" in vr:
        made = vr
        break
out["made"] = made
_result = out
'''
res_roof = B.run(ROOF_X0, u"屋面x0假设")
roof_id = (res_roof.get("made") or {}).get("id")

# --- 5. re-place ALL doors & windows with matched types ---
li_map = {B.LVL[0]: 0, B.LVL[1]: 1, B.LVL[2]: 2, B.LVL[3]: 3}
for kind in ("door", "window"):
    ops = [o for o in B.openings() if o["kind"] == kind]
    for o in ops:
        o["li"] = li_map[o["level"]]
    code = DOORS_N.replace("__OPS__", json.dumps(ops)).replace(
        "__KIND__", kind)
    B.run(code, u"门窗重放-" + kind)

# --- 6. save + verify ---
res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])

FINAL = u'''# -*- coding: utf-8 -*-
out = {}
for cname in ["OST_Walls", "OST_Doors", "OST_Windows", "OST_Floors",
              "OST_Roofs", "OST_Stairs", "OST_Railings"]:
    try:
        bic = getattr(BuiltInCategory, cname)
        n = 0
        for e in FilteredElementCollector(doc).OfCategory(bic).ToElements():
            if not isinstance(e, ElementType):
                n += 1
        out[cname] = n
    except Exception:
        out[cname] = "ERR"
out["rail_by_id"] = []
for rid in (334955, 334988):
    out["rail_by_id"].append(
        {str(rid): doc.GetElement(ElementId(rid)) is not None})
try:
    sts = list(FilteredElementCollector(doc).OfClass(Level).ToElements())
    out["elevs"] = [round(l.Elevation / 0.00328084)
                    for l in sorted(sts, key=lambda x: x.Elevation)]
except Exception:
    pass
_result = out
'''
B.run(FINAL, u"终验")
