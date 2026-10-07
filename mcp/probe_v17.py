# -*- coding: utf-8 -*-
u"""v17: measure alive instances (level binding + absolute bb z),
then fix-path experiments on L2 wall (explicit schedule level)."""
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
    print(u"[%s]" % label)
    print(json.dumps(res, ensure_ascii=False))
    return res


MEASURE = u'''# -*- coding: utf-8 -*-
out = {"items": []}
ft = 0.00328084

def lvl_name(eid):
    try:
        el = doc.GetElement(eid)
        return to_text(el.Name) if el is not None else "?"
    except Exception:
        return "?"

for cat, kind in ((BuiltInCategory.OST_Doors, "d"),
                  (BuiltInCategory.OST_Windows, "w")):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        d = {"k": kind, "id": int(e.Id.IntegerValue)}
        try:
            d["host"] = int(e.Host.Id.IntegerValue)
        except Exception:
            d["host"] = 0
        try:
            lp = e.get_Parameter(
                BuiltInParameter.INSTANCE_SCHEDULE_LEVEL_PARAM)
            d["lvl"] = (lvl_name(lp.AsElementId())
                        if (lp is not None and lp.AsElementId() is not None)
                        else "NA")
        except Exception:
            d["lvl"] = "ERR"
        try:
            sp = e.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM)
            d["sill"] = round(sp.AsDouble() / ft) if (
                sp is not None and sp.HasValue) else None
        except Exception:
            d["sill"] = None
        try:
            bb = e.get_BoundingBox(None)
            d["bbz"] = [round(bb.Min.Z / ft), round(bb.Max.Z / ft)]
        except Exception:
            d["bbz"] = None
        try:
            p = e.Location.Point
            d["lz"] = round(p.Z / ft)
        except Exception:
            d["lz"] = None
        out["items"].append(d)
_result = out
'''

res_m = run_full(MEASURE, "v17-幸存者测量")
items = res_m.get("items") or []
for d in items:
    print(u"  %s id=%s host=%s lvl=%s sill=%s lz=%s bbz=%s" % (
        d["k"], d["id"], d["host"], d["lvl"], d["sill"], d["lz"], d["bbz"]))
