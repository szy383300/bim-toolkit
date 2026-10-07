# -*- coding: utf-8 -*-
u"""v16: single-variable bisection for L2+ window deaths.
Place window variants on L2 south wall (334552) at x=3600,
capture in-txn bbox z, commit, check survival. Cleanup after."""
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


V16 = u'''# -*- coding: utf-8 -*-
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

syms = []
for e in FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Windows).ToElements():
    if isinstance(e, FamilySymbol):
        syms.append(e)
for s in syms:
    try:
        s.Activate()
    except Exception:
        pass

def sym_by_w(want):
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

def sym_info(s):
    d = {}
    try:
        wp = s.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
        hp = s.get_Parameter(BuiltInParameter.FAMILY_HEIGHT_PARAM)
        d["w"] = round(wp.AsDouble() / ft) if (wp is not None and
                                               wp.HasValue) else None
        d["h"] = round(hp.AsDouble() / ft) if (hp is not None and
                                               wp is not None and
                                               hp.HasValue) else None
    except Exception:
        pass
    try:
        d["fam"] = to_text(s.FamilyName)
    except Exception:
        d["fam"] = "?"
    try:
        tp = s.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        d["tn"] = to_text(tp.AsString()) if tp is not None else "?"
    except Exception:
        d["tn"] = "?"
    return d

def bbox_z(el):
    try:
        bb = el.get_BoundingBox(None)
        return [round(bb.Min.Z / ft), round(bb.Max.Z / ft)]
    except Exception:
        return None

wall2 = doc.GetElement(ElementId(334552))
wall1 = doc.GetElement(ElementId(334548))
out["wall2_bbox"] = bbox_z(wall2)
out["wall1_bbox"] = bbox_z(wall1)
s15 = sym_by_w(1500)
s61 = sym_by_w(610)
out["sym15"] = sym_info(s15)
out["sym61"] = sym_info(s61)

made_ids = []

def variant(tag, wall, sym, x, z_mm, sill=None, setw=None, seth=None):
    rec = {"tag": tag}
    t = Transaction(doc, "v16 " + tag)
    pre = _Pre()
    t.Start()
    fo = t.GetFailureHandlingOptions()
    fo.SetFailuresPreprocessor(pre)
    t.SetFailureHandlingOptions(fo)
    del SEEN[:]
    try:
        ppt = XYZ(x * ft, 0, z_mm * ft)
        inst = doc.Create.NewFamilyInstance(ppt, sym, wall,
                                            StructuralType.NonStructural)
        iid = int(inst.Id.IntegerValue)
        rec["id"] = iid
        made_ids.append(iid)
        rec["bb_place"] = bbox_z(inst)
        if sill is not None:
            ip = inst.LookupParameter(u"\\u5e95\\u9ad8\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(sill * ft)
            rec["sill_set"] = sill
        if setw is not None:
            ip = inst.LookupParameter(u"\\u5bbd\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(setw * ft)
        if seth is not None:
            ip = inst.LookupParameter(u"\\u9ad8\\u5ea6")
            if ip is not None and not ip.IsReadOnly:
                ip.Set(seth * ft)
        if sill is not None or setw is not None:
            rec["bb_after"] = bbox_z(inst)
        t.Commit()
        rec["committed"] = True
        el = doc.GetElement(ElementId(iid))
        rec["alive"] = el is not None
        if el is not None:
            rec["bb_alive"] = bbox_z(el)
    except Exception as ex:
        rec["err"] = to_text(ex)[:110]
        try:
            t.RollBack()
        except Exception:
            pass
    rec["fails"] = list(SEEN)[:6]
    out["variants"].append(rec)

variant("V1_t1500_s900", wall2, s15, 3600, 3000, sill=900,
        setw=1500, seth=1500)
variant("V2_t1500_s0", wall2, s15, 3600, 3000, sill=0,
        setw=1500, seth=1500)
variant("V3_t1500_s1500", wall2, s15, 3600, 3000, sill=1500,
        setw=1500, seth=1500)
variant("V4_t1500_stock_z3000", wall2, s15, 3600, 3000)
variant("V5_t1500_stock_z3900", wall2, s15, 3600, 3900)
variant("V6_t610_s900", wall2, s61, 3600, 3000, sill=900,
        setw=600, seth=1500)
variant("V7_L1_t1500_s900", wall1, s15, 8000, 0, sill=900,
        setw=1500, seth=1500)

# cleanup: delete surviving probe instances
t = Transaction(doc, "v16 cleanup")
t.Start()
n = 0
for iid in made_ids:
    try:
        el = doc.GetElement(ElementId(iid))
        if el is not None:
            doc.Delete(ElementId(iid))
            n += 1
    except Exception:
        pass
t.Commit()
out["cleaned"] = n
_result = out
'''

res = run_full(V16, "v16-二分实验")

for v in (res.get("variants") or []):
    print(u"%s alive=%s bb_place=%s bb_after=%s bb_alive=%s fails=%s %s" % (
        v.get("tag"), v.get("alive"), v.get("bb_place"), v.get("bb_after"),
        v.get("bb_alive"), v.get("fails"), v.get("err", "")))
print(u"sym15=%s" % json.dumps(res.get("sym15"), ensure_ascii=False))
print(u"sym61=%s" % json.dumps(res.get("sym61"), ensure_ascii=False))
print(u"wall2_bbox=%s wall1_bbox=%s cleaned=%s" % (
    res.get("wall2_bbox"), res.get("wall1_bbox"), res.get("cleaned")))
