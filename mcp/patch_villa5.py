# -*- coding: utf-8 -*-
u"""patch v5: (1) full Railing-class census (handrail vs railing,
host check); (2) swap 600x600 stubby windows to 0610x1220 type;
(3) save."""
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


RAIL_CENSUS = u'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB.Architecture as ARCH
out = {"rails": []}
ft = 0.00328084
for e in FilteredElementCollector(doc).OfClass(ARCH.Railing).ToElements():
    d = {"id": int(e.Id.IntegerValue)}
    try:
        d["cat"] = to_text(e.Category.Name)
    except Exception:
        d["cat"] = "?"
    try:
        p = e.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
        if p is None or p.AsElementId() is None:
            p = e.get_Parameter(
                BuiltInParameter.INSTANCE_SCHEDULE_LEVEL_PARAM)
        if p is not None and p.AsElementId() is not None:
            lv = doc.GetElement(p.AsElementId())
            d["lvl"] = to_text(lv.Name) if lv is not None else "?"
        else:
            d["lvl"] = "NA"
    except Exception:
        d["lvl"] = "ERR"
    try:
        hp = e.get_Parameter(BuiltInParameter.HOST_ID_PARAM)
        if hp is not None and hp.AsElementId() is not None:
            h = doc.GetElement(hp.AsElementId())
            d["host"] = (to_text(h.GetType().Name) if h is not None
                         else "gone")
        else:
            d["host"] = "none"
    except Exception:
        d["host"] = "ERR"
    out["rails"].append(d)
_result = out
'''
res_rc = run_full(RAIL_CENSUS, u"栏杆家族普查")
rails = (res_rc.get("result", res_rc).get("rails") or [])
real = [r for r in rails if u"栏杆" in (r.get("cat") or "")
        and u"扶手" not in (r.get("cat") or "")]
print(u"==== 栏杆普查: Railing类元素=%d 真栏杆(OST_Railings)=%d ====" % (
    len(rails), len(real)))
for r in rails:
    print(u"  id=%s cat=%s lvl=%s host=%s" % (
        r.get("id"), r.get("cat"), r.get("lvl"), r.get("host")))

# --- swap stubby 600x600 windows to taller type ---
SWAP = u'''# -*- coding: utf-8 -*-
out = {"swapped": 0, "audit": []}
ft = 0.00328084
target = None
for e in FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Windows).ToElements():
    if isinstance(e, FamilySymbol):
        tp = e.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        tn = to_text(tp.AsString()) if tp is not None else ""
        if tn.replace(" ", "").startswith("0610"):
            target = e
            break
out["target"] = to_text(target.Id.IntegerValue) if False else (
    int(target.Id.IntegerValue) if target is not None else None)
if target is None:
    _result = out
else:
    try:
        target.Activate()
    except Exception:
        pass
    t = Transaction(doc, "swap 600 windows")
    pre = _prep(t)
    t.Start()
    try:
        for e in FilteredElementCollector(doc).OfCategory(
                BuiltInCategory.OST_Windows).ToElements():
            if isinstance(e, ElementType):
                continue
            tp = doc.GetElement(e.GetTypeId())
            tn = "?"
            if tp is not None:
                p = tp.get_Parameter(
                    BuiltInParameter.ALL_MODEL_TYPE_NAME)
                tn = to_text(p.AsString()) if p is not None else "?"
            if tn != "600 x 600mm":
                continue
            iid = int(e.Id.IntegerValue)
            sill = 0
            sp = e.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM)
            if sp is not None and sp.HasValue:
                sill = sp.AsDouble()
            e.ChangeTypeId(target.Id)
            e2 = doc.GetElement(ElementId(iid))
            ip = e2.LookupParameter(u"\\u5bbd\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(600 * ft)
            ip = e2.LookupParameter(u"\\u5e95\\u9ad8\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(sill)
            out["swapped"] += 1
        t.Commit()
    except Exception as ex:
        out["err"] = to_text(ex)[:120]
        try:
            t.RollBack()
        except Exception:
            pass
    for e in FilteredElementCollector(doc).OfCategory(
            BuiltInCategory.OST_Windows).ToElements():
        if isinstance(e, ElementType):
            continue
        bb = e.get_BoundingBox(None)
        h = e.Host
        hb = h.get_BoundingBox(None) if h is not None else None
        if bb is None or hb is None:
            continue
        ok = bb.Min.Z >= hb.Min.Z - ft and bb.Max.Z <= hb.Max.Z + ft
        out["audit"].append([int(e.Id.IntegerValue),
                             round(bb.Min.Z / ft), round(bb.Max.Z / ft),
                             "OK" if ok else "OUT"])
    _result = out
'''
res_sw = run_full(SWAP, u"方窗换型")
sw = res_sw.get("result", res_sw)
print(u"==== 方窗换型: swapped=%s target=%s ====" % (
    sw.get("swapped"), sw.get("target")))
bad = [a for a in (sw.get("audit") or []) if a[-1] != "OK"]
print(u"窗审计: n=%d 越界=%d %s" % (
    len(sw.get("audit") or []), len(bad), bad[:5]))

res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])
res_v = run_full(B.VERIFY, u"验收")
