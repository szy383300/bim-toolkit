# -*- coding: utf-8 -*-
u"""patch v5b: fix swap template (__PRE__), dedupe orphan balcony
guards (delete 334955/334988 if overlapping 339986/340018), save."""
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


RAIL_BB = u'''# -*- coding: utf-8 -*-
out = {}
ft = 0.00328084
for rid in (334955, 334988, 339986, 340018):
    el = doc.GetElement(ElementId(rid))
    if el is None:
        out[str(rid)] = None
        continue
    bb = el.get_BoundingBox(None)
    out[str(rid)] = [round(bb.Min.X / ft), round(bb.Min.Y / ft),
                     round(bb.Max.X / ft), round(bb.Max.Y / ft),
                     round(bb.Min.Z / ft), round(bb.Max.Z / ft)]
_result = out
'''
res_rb = run_full(RAIL_BB, u"护栏包围盒")
rbs = res_rb.get("result", res_rb)


def overlap(a, b):
    if a is None or b is None:
        return False
    ax0, ay0, ax1, ay1, az0, az1 = a
    bx0, by0, bx1, by1, bz0, bz1 = b
    return (min(ax1, bx1) - max(ax0, bx0) > 100 and
            min(ay1, by1) - max(ay0, by0) > 100 and
            min(az1, bz1) - max(az0, bz0) > -500)


del_ids = []
if overlap(rbs.get("334955"), rbs.get("339986")):
    del_ids.append(334955)
if overlap(rbs.get("334988"), rbs.get("340018")):
    del_ids.append(334988)
print(u"==== 护栏去重: 待删=%s ====" % del_ids)
if del_ids:
    DEL = u'''# -*- coding: utf-8 -*-
__PRE__
ids = __IDS__
t = Transaction(doc, "dedupe guards")
pre = _prep(t)
t.Start()
n = 0
for i in ids:
    try:
        if doc.GetElement(ElementId(i)) is not None:
            doc.Delete(ElementId(i))
            n += 1
    except Exception:
        pass
t.Commit()
_result = {"deleted": n}
'''
    run_full(DEL.replace("__IDS__", str(del_ids)), u"删旧护栏")

SWAP = u'''# -*- coding: utf-8 -*-
__PRE__
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
out["target"] = int(target.Id.IntegerValue) if target is not None else None
if target is not None:
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
res_sw = run_full(SWAP, u"方窗换型v2")
sw = res_sw.get("result", res_sw)
print(u"==== 方窗换型: swapped=%s target=%s err=%s ====" % (
    sw.get("swapped"), sw.get("target"), sw.get("err", "")))
bad = [a for a in (sw.get("audit") or []) if a[-1] != "OK"]
print(u"窗终审计: n=%d 越界=%d %s" % (
    len(sw.get("audit") or []), len(bad), bad[:5]))

res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])
run_full(B.VERIFY, u"终验收")
