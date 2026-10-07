# -*- coding: utf-8 -*-
u"""probe v12: (A) landing slab with Append fix; (C) single L2 door with
batch-style suppressor + LOGGING to capture the exact failure texts that
cause L2+ hosted instances to be resolved-deleted at commit."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [9000]


def run(code, label, timeout=600):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=timeout)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2800])
    return res


LANDING_FIX = r'''# -*- coding: utf-8 -*-
__PRE__
out = {}
lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
bl = lvls[0]
ft = 0.00328084
ftypes = list(FilteredElementCollector(doc).OfClass(FloorType)
              .ToElements())
t = Transaction(doc, "villa landing fix")
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

DOOR_LOG = r'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List as _DL
out = {"seen": [], "passes": [0]}

class Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        out["passes"][0] += 1
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        for m in ms:
            try:
                sev = to_text(m.GetSeverity())
                txt = to_text(m.GetDescriptionText())[:200]
                out["seen"].append(sev + " | " + txt)
            except Exception:
                pass
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
                if out["passes"][0] >= 3:
                    return FailureProcessingResult.ProceedWithRollBack
                return FailureProcessingResult.ProceedWithCommit
        except Exception:
            pass
        return FailureProcessingResult.Continue

lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
bl = lvls[1]
ft = 0.00328084
cat = BuiltInCategory.OST_Doors
syms = [e for e in FilteredElementCollector(doc).OfCategory(cat)
        .ToElements() if isinstance(e, FamilySymbol)]
t = Transaction(doc, "one door L2 log")
pre = Pre()
fo = t.GetFailureHandlingOptions()
fo.SetFailuresPreprocessor(pre)
t.SetFailureHandlingOptions(fo)
t.Start()
try:
    px, py = 2400 * ft, 4800 * ft
    lvl = bl
    best = None
    best_d = 1e18
    for w in FilteredElementCollector(doc).OfClass(Wall).ToElements():
        p = w.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
        if p is None or p.AsElementId() is None:
            continue
        if p.AsElementId().IntegerValue != lvl.Id.IntegerValue:
            continue
        c = w.Location.Curve
        r = c.Project(XYZ(px, py, lvl.Elevation))
        if r is None:
            continue
        dxy = (r.XYZPoint.X - px) ** 2 + (r.XYZPoint.Y - py) ** 2
        if dxy < best_d:
            best_d = dxy
            best = (w, r.XYZPoint)
    out["found"] = best is not None
    if best is None:
        t.RollBack()
        out["abort"] = "no wall"
    else:
        w, ppt = best
        out["wall"] = int(w.Id.IntegerValue)
        sym = None
        for s in syms:
            try:
                sp = s.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
                if sp is not None and abs(sp.AsDouble() / ft - 900) < 60:
                    sym = s
                    break
            except Exception:
                pass
        if sym is None:
            sym = syms[0]
        try:
            sym.Activate()
        except Exception:
            pass
        inst = doc.Create.NewFamilyInstance(
            ppt, sym, w, StructuralType.NonStructural)
        out["inst"] = int(inst.Id.IntegerValue)
        st = t.Commit()
        out["commit"] = to_text(st)
        alive = doc.GetElement(inst.Id)
        out["alive"] = alive is not None
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
_result = out
'''

run(LANDING_FIX.replace("__PRE__", ""), u"v12-A 平台楼板(Append修正)")
run(DOOR_LOG, u"v12-C 单个L2门+日志抑制器")
