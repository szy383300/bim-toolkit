# -*- coding: utf-8 -*-
u"""probe v13: (1) inventory of door/window FamilySymbols (name + width +
height params) to fix the symbol matcher; (2) find & delete the v11 junk
door at L2 (2400,4800); (3) landing slab with proper preprocessor."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [10000]


def run(code, label, timeout=240):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=timeout)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:3000])
    return res


SYMS = r'''# -*- coding: utf-8 -*-
out = {}
for cat, key in ((BuiltInCategory.OST_Doors, "doors"),
                 (BuiltInCategory.OST_Windows, "windows")):
    lst = []
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if not isinstance(e, FamilySymbol):
            continue
        d = {"name": to_text(e.Name)}
        try:
            wp = e.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
            hp = e.get_Parameter(BuiltInParameter.FAMILY_HEIGHT_PARAM)
            d["w_mm"] = round(wp.AsDouble() / 0.00328084) if wp is not \
                None and wp.HasValue else None
            d["h_mm"] = round(hp.AsDouble() / 0.00328084) if hp is not \
                None and hp.HasValue else None
        except Exception as ex:
            d["err"] = to_text(ex)[:60]
        lst.append(d)
    out[key] = lst
# current instance counts + any junk at L2 (2400,4800)
out["n_door_inst"] = 0
out["n_win_inst"] = 0
out["junk"] = []
ft = 0.00328084
for cat, key, nkey in ((BuiltInCategory.OST_Doors, "doors", "n_door_inst"),
                       (BuiltInCategory.OST_Windows, "windows",
                        "n_win_inst")):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        out[nkey] += 1
        try:
            loc = e.Location
            if loc is not None and hasattr(loc, "Point"):
                p = loc.Point
                mm = (round(p.X / ft), round(p.Y / ft),
                      round(p.Z / ft))
                if mm[0] == 2400 and mm[1] == 4800:
                    out["junk"].append({"id": int(e.Id.IntegerValue),
                                        "z": mm[2]})
        except Exception:
            pass
_result = out
'''

JUNK_CLEAN = r'''# -*- coding: utf-8 -*-
__PRE__
out = {"deleted": []}
ids = __JUNK__
t = Transaction(doc, "clean junk door")
pre = _prep(t)
t.Start()
for i in ids:
    try:
        doc.Delete(ElementId(int(i)))
        out["deleted"].append(int(i))
    except Exception:
        pass
t.Commit()
_result = out
'''

LANDING = r'''# -*- coding: utf-8 -*-
__PRE__
out = {}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
bl = lvls[0]
ft = 0.00328084
ftypes = list(FilteredElementCollector(doc).OfClass(FloorType)
              .ToElements())
t = Transaction(doc, "villa landing v13")
pre = _prep(t)
t.Start()
try:
    pts = [(0, 5100), (1200, 5100), (1200, 6300), (0, 6300)]
    arr = CurveArray()
    cs = [XYZ(x * ft, y * ft, bl.Elevation) for x, y in pts]
    for i in range(4):
        arr.Append(Line.CreateBound(cs[i], cs[(i + 1) % 4]))
    fl = doc.Create.NewFloor(arr, ftypes[0], bl, False)
    p = fl.get_Parameter(BuiltInParameter.FLOOR_HEIGHTABOVELEVEL_PARAM)
    if p is not None and not p.IsReadOnly:
        p.Set(1500 * ft)
        out["offset"] = 1500
    t.Commit()
    out["id"] = int(fl.Id.IntegerValue)
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
_result = out
'''

PRE_SRC = u'''from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from System.Collections.Generic import List as _DL

class _Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        if ms:
            try:
                errs = _DL[FailureMessageAccessor]()
                for m in ms:
                    if m.GetSeverity() == FailureSeverity.Error:
                        errs.Add(m)
                try:
                    fa.DeleteAllWarnings()
                except Exception:
                    pass
                if errs.Count > 0:
                    fa.ResolveFailures(errs)
                    return FailureProcessingResult.ProceedWithCommit
            except Exception:
                pass
        return FailureProcessingResult.Continue

def _prep(tr):
    pre = _Pre()
    try:
        fo = tr.GetFailureHandlingOptions()
        fo.SetFailuresPreprocessor(pre)
        tr.SetFailureHandlingOptions(fo)
    except Exception:
        pass
    return pre
'''

r1 = run(SYMS, u"v13 门窗类型盘点")

# delete junk door found at L2 (2400,4800) before re-placement
junk_ids = [j["id"] for j in (r1.get("junk") or [])]
if junk_ids:
    run(JUNK_CLEAN.replace("__JUNK__", str(junk_ids))
        .replace("__PRE__", PRE_SRC), u"v13 清理垃圾门")

run(LANDING.replace("__PRE__", PRE_SRC), u"v13 平台楼板")
