# -*- coding: utf-8 -*-
u"""Patch-up for villa rebuild: landings x3 + roof retry + doors/windows +
materials + save + verify. Walls/stairs/floors/railings already in place
from the build_villa2 run (2026-09-12) — do NOT wipe, do NOT rebuild."""
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
import build_villa2 as B  # noqa: E402

# --- 1. landing slabs (CurveArray fix already applied in B.LANDING) ---
for b in (0, 1, 2):
    B.run(B.LANDING.replace("__B__", str(b)), u"平台%d" % (b + 1))

# --- 2. roof retry (cutVector=Z fix already applied in B.ROOF) ---
res_roof = B.run(B.ROOF, u"屋面重试")
roof_id = (res_roof.get("made") or {}).get("id")

# --- 3. doors & windows (StructuralType import fix already applied) ---
li_map = {B.LVL[0]: 0, B.LVL[1]: 1, B.LVL[2]: 2, B.LVL[3]: 3}
dw_res = {}
for kind in ("door", "window"):
    ops = [o for o in B.openings() if o["kind"] == kind]
    for o in ops:
        o["li"] = li_map[o["level"]]
    code = B.DOORS.replace("__OPS__", json.dumps(ops)).replace(
        "__KIND__", kind)
    dw_res[kind] = B.run(code, u"门窗-" + kind)

# --- 4. materials (bridge set_material; writable-instance-param only) ---
probe = B.bridge("set_material", {"name": "___probe___"}, u"材质探测")
avail = []
err = probe.get("error", "")
if u"available" in err:
    avail = [s.strip() for s in
             err.split(u"available:")[-1].split(u",") if s.strip()]
print(u"[可用材质数]", len(avail))


def pick(kws):
    for kw in kws:
        for a in avail:
            if kw in a:
                return a
    return None


def apply(m, ids, label):
    if not m or not ids:
        print(u"[材质] %s 跳过" % label)
        return
    res = B.bridge("set_material", {"name": m, "ids": ids}, u"材质" + label)
    print(u"[材质] %s <- %s assigned=%s" % (
        label, m, res.get("assigned")))


ext_ids = [334548, 334549, 334550, 334551, 334552, 334553, 334554,
           334555, 334556, 334557, 334558, 334559, 334560, 334561,
           334562, 334563]
int_ids = list(range(334564, 334570)) + list(range(334570, 334576)) + \
    list(range(334576, 334582)) + list(range(334582, 334587))
bal_ids = [334622, 334629]
apply(pick([u"砖", u"砌块", u"混凝土"]), ext_ids, u"外墙")
apply(pick([u"涂料", u"石膏"]), int_ids, u"内墙")
if roof_id:
    apply(pick([u"瓦", u"沥青", u"混凝土"]), [roof_id], u"屋面")
apply(pick([u"木", u"混凝土"]), bal_ids, u"阳台")

# --- 5. save + verify ---
res = B.bridge("save_document", {"path": B.SAVE_PATH}, u"保存")
print(u"[保存]", json.dumps(res, ensure_ascii=False)[:200])
B.run(B.VERIFY, u"验收")
