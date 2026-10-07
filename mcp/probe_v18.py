# -*- coding: utf-8 -*-
u"""v18: fix-path test - NewFamilyInstance WITH explicit Level arg,
then measure absolute bbz. Expect glass inside host wall [3000,6000]."""
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


V18 = u'''# -*- coding: utf-8 -*-
out = {"variants": []}
ft = 0.00328084
SEEN = []

from Autodesk.Revit.DB import (IFailuresPreprocessor,
                               FailureProcessingResult, FailureSeverity,
                               FailureMessageAccessor)
from System.Collections.Generic import List as _DL
from Autodesk.Revit.DB.Structure import StructuralType

class _Pre(IFailuresPreprocessor):
    def PreprocessFailures(self, fa):
        try:
            ms = list(fa.GetFailureMessages())
        except Exception:
            ms = []
        for m in ms:
            try:
                SEEN.append(to_text(m.GetSeverity()) + " | " +
                            to_text(m.GetDescriptionText())[:110])
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
                return FailureProcessingResult.ProceedWithCommit
        except Exception:
            pass
        return FailureProcessingResult.Continue

lvls = sorted(FilteredElementCollector(doc).OfClass(Level).ToElements(),
              key=lambda l: l.Elevation)
lvl1, lvl2 = lvls[0], lvls[1]

def syms_of(cat):
    r = []
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, FamilySymbol):
            r.append(e)
    for s in r:
        try:
            s.Activate()
        except Exception:
            pass
    return r

wsyms = syms_of(BuiltInCategory.OST_Windows)
dsyms = syms_of(BuiltInCategory.OST_Doors)

def sym_by_w(syms, want):
    best = None
    bd = 1e18
    for s in syms:
        try:
            wp = s.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
            if wp is not None and wp.HasValue:
                d = abs(wp.AsDouble() / ft - want)
                if d < bd:
                    bd = d
                    best = s
        except Exception:
            pass
    return best

def bbox_z(el):
    try:
        bb = el.get_BoundingBox(None)
        return [round(bb.Min.Z / ft), round(bb.Max.Z / ft)]
    except Exception:
        return None

wall2 = doc.GetElement(ElementId(334552))
made = []

def variant(tag, sym, wall, lvl, x, z_mm, sill, setw, seth):
    rec = {"tag": tag}
    t = Transaction(doc, "v18 " + tag)
    pre = _Pre()
    t.Start()
    fo = t.GetFailureHandlingOptions()
    fo.SetFailuresPreprocessor(pre)
    t.SetFailureHandlingOptions(fo)
    del SEEN[:]
    try:
        ppt = XYZ(x * ft, 0, z_mm * ft)
        try:
            inst = doc.Create.NewFamilyInstance(ppt, sym, wall, lvl,
                                                StructuralType.NonStructural)
            rec["call"] = "5arg"
        except Exception as ex1:
            inst = doc.Create.NewFamilyInstance(ppt, sym, wall,
                                                StructuralType.NonStructural)
            rec["call"] = "4arg"
            lp = inst.get_Parameter(
                BuiltInParameter.INSTANCE_SCHEDULE_LEVEL_PARAM)
            if lp is not None and not lp.IsReadOnly:
                lp.Set(lvl.Id)
        iid = int(inst.Id.IntegerValue)
        made.append(iid)
        rec["id"] = iid
        if sill is not None:
            ip = inst.LookupParameter(u"\\u5e95\\u9ad8\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(sill * ft)
        if setw is not None:
            ip = inst.LookupParameter(u"\\u5bbd\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(setw * ft)
        if seth is not None:
            ip = inst.LookupParameter(u"\\u9ad8\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(seth * ft)
        t.Commit()
        el = doc.GetElement(ElementId(iid))
        rec["alive"] = el is not None
        if el is not None:
            rec["bbz"] = bbox_z(el)
            rec["bbxy"] = None
            try:
                bb = el.get_BoundingBox(None)
                rec["bbxy"] = [round(bb.Min.X / ft), round(bb.Min.Y / ft),
                               round(bb.Max.X / ft), round(bb.Max.Y / ft)]
            except Exception:
                pass
    except Exception as ex:
        rec["err"] = to_text(ex)[:110]
        try:
            t.RollBack()
        except Exception:
            pass
    rec["fails"] = list(SEEN)[:4]
    out["variants"].append(rec)

s15 = sym_by_w(wsyms, 1500)
s61 = sym_by_w(wsyms, 610)
d90 = sym_by_w(dsyms, 900)

variant("F1a_t1500_lvl2_s900", s15, wall2, lvl2, 6000, 3000, 900,
        1500, 1500)
variant("F1b_t1500_lvl2_z3900", s15, wall2, lvl2, 7000, 3900, None,
        1500, 1500)
variant("F2_t610_lvl2_s1500", s61, wall2, lvl2, 6500, 3000, 1500,
        600, 1500)
variant("F4_door_lvl2_s0", d90, wall2, lvl2, 9500, 3000, 0, None, None)

t = Transaction(doc, "v18 cleanup")
t.Start()
n = 0
for iid in made:
    try:
        if doc.GetElement(ElementId(iid)) is not None:
            doc.Delete(ElementId(iid))
            n += 1
    except Exception:
        pass
t.Commit()
out["cleaned"] = n
_result = out
'''

res = run_full(V18, "v18-修复实验")
for v in (res.get("variants") or []):
    print(u"%s call=%s alive=%s bbz=%s fails=%s %s" % (
        v.get("tag"), v.get("call"), v.get("alive"), v.get("bbz"),
        v.get("fails"), v.get("err", "")))
print(u"cleaned=%s" % res.get("cleaned"))
