# -*- coding: utf-8 -*-
u"""probe v9: read-only inspection of the two stairs committed by v7.
Answers: do committed stairs auto-contain landings? + reflect Stairs
run/landing method names for 2019."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [6000]


def run(code, label):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=60)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2600])
    return res


CODE = r'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
import clr
out = {}
try:
    meths = sorted(set(
        m.Name for m in clr.GetClrType(ARCH.Stairs).GetMethods()
        if ("Run" in m.Name or "Land" in m.Name)))
    out["methods"] = meths
except Exception as ex:
    out["meth_err"] = to_text(ex)[:100]
sts = []
try:
    sts = list(FilteredElementCollector(doc).OfClass(ARCH.Stairs)
               .ToElements())
except Exception as ex:
    out["coll_err"] = to_text(ex)[:100]
out["n"] = len(sts)
out["stairs"] = []
for st in sts:
    d = {"id": int(st.Id.IntegerValue)}
    try:
        bb = st.get_BoundingBox(None)
        d["zmin"] = round(bb.Min.Z / 0.00328084)
        d["zmax"] = round(bb.Max.Z / 0.00328084)
    except Exception as ex:
        d["bb_err"] = to_text(ex)[:80]
    for mn, key in (("GetStairsRuns", "runs"),
                    ("GetStairsLandings", "landings")):
        try:
            d[key] = len(list(getattr(st, mn)()))
        except Exception as ex:
            d[key + "_err"] = to_text(ex)[:80]
    out["stairs"].append(d)
_result = out
'''

run(CODE, u"v9 检视 v7 建成的两部楼梯")
