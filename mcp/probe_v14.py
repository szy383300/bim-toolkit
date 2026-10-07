# -*- coding: utf-8 -*-
u"""probe v14: (1) door/window symbol inventory via ALL_MODEL_TYPE_NAME
(.Name is unreadable in exec); (2) roof variants: open triangle profile,
closed rectangle profile — is the closed-triangle the Invalid profile?"""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

ID = [11000]


def run(code, label, timeout=240):
    ID[0] += 1
    r = call({"type": "execute_code", "id": ID[0], "code": code,
              "no_transaction": True}, timeout=timeout)
    res = r.get("result", r)
    print(u"=== %s ===" % label)
    print(json.dumps(res, ensure_ascii=False)[:3400])
    return res


SYMS = r'''# -*- coding: utf-8 -*-
out = {}
for cat, key in ((BuiltInCategory.OST_Doors, "doors"),
                 (BuiltInCategory.OST_Windows, "windows")):
    lst = []
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if not isinstance(e, FamilySymbol):
            continue
        d = {}
        try:
            np = e.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
            d["name"] = to_text(np.AsString()) if np is not None else "?"
        except Exception:
            d["name"] = "?"
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
_result = out
'''

ROOF_V = r'''# -*- coding: utf-8 -*-
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
pre = None

def roof_try(tag, build_profile):
    t = Transaction(doc, "roof " + tag)
    p = _prep(t)
    t.Start()
    try:
        try:
            rt2 = rts[0].Duplicate("Villa_Roof_" + tag)
        except Exception:
            rt2 = rts[0]
        rp = doc.Create.NewReferencePlane2(
            XYZ(6000 * ft, -600 * ft, 0), XYZ(6000 * ft, 9600 * ft, 0),
            XYZ(0, 0, 1), v)
        prof = build_profile()
        roof = doc.Create.NewExtrusionRoof(
            prof, rp, lvl, rt2, -6600.0 * ft, 6600.0 * ft)
        t.Commit()
        return {"tag": tag, "id": int(roof.Id.IntegerValue)}
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"tag": tag, "err": to_text(ex)[:110]}

def open_tri():
    ca = CurveArray()
    a = XYZ(6000 * ft, -600 * ft, z0)
    b = XYZ(6000 * ft, 4500 * ft, z0 + 2944.7 * ft * 0.5774)
    c = XYZ(6000 * ft, 9600 * ft, z0)
    ca.Append(Line.CreateBound(a, b))
    ca.Append(Line.CreateBound(b, c))
    return ca

def closed_rect():
    ca = CurveArray()
    y0, y1, dz = -600 * ft, 9600 * ft, 1000 * ft
    p1 = XYZ(6000 * ft, y0, z0)
    p2 = XYZ(6000 * ft, y1, z0)
    p3 = XYZ(6000 * ft, y1, z0 + dz)
    p4 = XYZ(6000 * ft, y0, z0 + dz)
    ca.Append(Line.CreateBound(p1, p2))
    ca.Append(Line.CreateBound(p2, p3))
    ca.Append(Line.CreateBound(p3, p4))
    ca.Append(Line.CreateBound(p4, p1))
    return ca

def closed_tri():
    ca = CurveArray()
    a = XYZ(6000 * ft, -600 * ft, z0)
    b = XYZ(6000 * ft, 4500 * ft, z0 + 2944.7 * ft * 0.5774)
    c = XYZ(6000 * ft, 9600 * ft, z0)
    ca.Append(Line.CreateBound(a, b))
    ca.Append(Line.CreateBound(b, c))
    ca.Append(Line.CreateBound(c, a))
    return ca

out["variants"].append(roof_try("open_tri", open_tri))
out["variants"].append(roof_try("closed_rect", closed_rect))
out["variants"].append(roof_try("closed_tri", closed_tri))
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

run(SYMS, u"v14 门窗类型盘点(参数化取名)")
run(ROOF_V.replace("__PRE__", PRE_SRC), u"v14 屋面三变体")
