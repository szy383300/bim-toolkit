# -*- coding: utf-8 -*-
u"""probe v11: WHY do L2+ hosted instances get deleted at commit?
(1) read-only inventory of surviving doors/windows/railings
(2) single L2 door with LOGGING preprocessor (rollback on error)
(3) single L2 window ditto
(4) single L2 railing ditto"""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [8000]


def run(code, label, timeout=240):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=timeout)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:2800])
    return res


INVENTORY = r'''# -*- coding: utf-8 -*-
out = {"doors": [], "windows": [], "railings": []}
ft = 0.00328084
lvls = {}
for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
    lvls[l.Id.IntegerValue] = l.Elevation / 0.00328084
for cat, key in ((BuiltInCategory.OST_Doors, "doors"),
                 (BuiltInCategory.OST_Windows, "windows")):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        d = {"id": int(e.Id.IntegerValue)}
        try:
            lp = e.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM)
            host = e.Host
            d["host"] = int(host.Id.IntegerValue) if host else None
            if host is not None:
                bp = host.get_Parameter(
                    BuiltInParameter.WALL_BASE_CONSTRAINT)
                if bp is not None:
                    bid = bp.AsElementId().IntegerValue
                    d["host_lvl_mm"] = round(lvls.get(bid, -9999))
        except Exception as ex:
            d["err"] = to_text(ex)[:60]
        out[key].append(d)
for rid in (334955, 334988):
    e = doc.GetElement(ElementId(rid))
    out["railings"].append({str(rid): "ALIVE" if e is not None else "GONE"})
_result = out
'''

TMPL = r'''# -*- coding: utf-8 -*-
import Autodesk.Revit.DB as DB
import Autodesk.Revit.DB.Architecture as ARCH
from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List as _DL
out = {"seen": []}

class Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        for m in ms:
            try:
                out["seen"].append(to_text(m.GetDescriptionText())[:160])
            except Exception:
                pass
        try:
            has_err = any(m.GetSeverity() == FailureSeverity.Error
                          for m in ms)
        except Exception:
            has_err = True
        if has_err:
            return FailureProcessingResult.ProceedWithRollBack
        try:
            fa.DeleteAllWarnings()
        except Exception:
            pass
        return FailureProcessingResult.Continue

lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
bl = lvls[1]
ft = 0.00328084
__BODY__
_result = out
'''

DOOR_ONE = r'''cat = BuiltInCategory.OST_Doors
syms = [e for e in FilteredElementCollector(doc).OfCategory(cat)
        .ToElements() if isinstance(e, FamilySymbol)]
t = Transaction(doc, "one door L2")
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
    else:
        w, ppt = best
        out["wall"] = int(w.Id.IntegerValue)
        wp = None
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
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
'''

WINDOW_ONE = r'''cat = BuiltInCategory.OST_Windows
syms = [e for e in FilteredElementCollector(doc).OfCategory(cat)
        .ToElements() if isinstance(e, FamilySymbol)]
t = Transaction(doc, "one window L2")
pre = Pre()
fo = t.GetFailureHandlingOptions()
fo.SetFailuresPreprocessor(pre)
t.SetFailureHandlingOptions(fo)
t.Start()
try:
    px, py = 3600 * ft, 0
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
    else:
        w, ppt = best
        out["wall"] = int(w.Id.IntegerValue)
        sym = None
        for s in syms:
            try:
                sp = s.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
                if sp is not None and abs(sp.AsDouble() / ft - 1500) < 60:
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
        try:
            ip = inst.LookupParameter(u"\u5e95\u9ad8\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(900 * ft)
        except Exception:
            pass
        st = t.Commit()
        out["commit"] = to_text(st)
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
'''

RAILING_ONE = r'''t = Transaction(doc, "one railing L2")
pre = Pre()
fo = t.GetFailureHandlingOptions()
fo.SetFailuresPreprocessor(pre)
t.SetFailureHandlingOptions(fo)
t.Start()
try:
    rts = list(FilteredElementCollector(doc).OfClass(ARCH.RailingType)
               .ToElements())
    out["types"] = len(rts)
    path = [(4200, 0), (4200, -1500), (9000, -1500), (9000, 0)]
    made = None
    for rt in rts:
        try:
            corners = [XYZ(x * ft, y * ft, bl.Elevation)
                       for x, y in path]
            cl = CurveLoop.Create(
                [Line.CreateBound(corners[i], corners[i + 1])
                 for i in range(len(corners) - 1)])
            r = ARCH.Railing.Create(doc, cl, rt.Id, bl.Id)
            made = int(r.Id.IntegerValue)
            break
        except Exception as ex:
            out.setdefault("type_errs", []).append(to_text(ex)[:80])
    out["made"] = made
    if made:
        st = t.Commit()
        out["commit"] = to_text(st)
    else:
        t.RollBack()
except Exception as ex:
    out["err"] = to_text(ex)[:200]
    try:
        t.RollBack()
    except Exception:
        pass
'''

run(INVENTORY, u"v11 幸存者盘点")
run(TMPL.replace("__BODY__", DOOR_ONE), u"v11 单个L2门")
run(TMPL.replace("__BODY__", WINDOW_ONE), u"v11 单个L2窗")
run(TMPL.replace("__BODY__", RAILING_ONE), u"v11 单个L2栏杆")
