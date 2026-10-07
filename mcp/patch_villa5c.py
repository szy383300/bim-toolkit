# -*- coding: utf-8 -*-
u"""patch v5c: re-target 0610x0610 windows to 0610x1220 exact type."""
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


RETYPE = u'''# -*- coding: utf-8 -*-
__PRE__
out = {"swapped": 0, "audit": []}
ft = 0.00328084
target = None
tn_want = u"0610 x 1220mm"
for e in FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Windows).ToElements():
    if isinstance(e, FamilySymbol):
        tp = e.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        tn = to_text(tp.AsString()) if tp is not None else ""
        if tn == tn_want:
            target = e
            out["target_w"] = None
            wp = e.get_Parameter(BuiltInParameter.FAMILY_WIDTH_PARAM)
            hp = e.get_Parameter(BuiltInParameter.FAMILY_HEIGHT_PARAM)
            out["target_w"] = [round(wp.AsDouble() / ft),
                               round(hp.AsDouble() / ft)]
            break
out["target"] = int(target.Id.IntegerValue) if target is not None else None
if target is not None:
    try:
        target.Activate()
    except Exception:
        pass
    t = Transaction(doc, "retarget 610 windows")
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
            if "0610" not in tn or "1220" in tn:
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
res = run_full(RETYPE, u"高窗精换")
sw = res.get("result", res)
print(u"==== 高窗精换: swapped=%s target=%s 尺寸=%s err=%s ====" % (
    sw.get("swapped"), sw.get("target"), sw.get("target_w"),
    sw.get("err", "")))
bad = [a for a in (sw.get("audit") or []) if a[-1] != "OK"]
print(u"窗终审计: n=%d 越界=%d %s" % (
    len(sw.get("audit") or []), len(bad), bad[:5]))

res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])
run_full(B.VERIFY, u"终验收")
