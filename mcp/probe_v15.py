# -*- coding: utf-8 -*-
u"""v15: full census - window/door survivors + dead-op matching,
railing inst-vs-type, walls curves, roof plate. Full JSON out."""
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


CENSUS_DW = u'''# -*- coding: utf-8 -*-
out = {}
ft = 0.00328084
items = []
for cat, kind in ((BuiltInCategory.OST_Doors, "door"),
                  (BuiltInCategory.OST_Windows, "window")):
    for e in FilteredElementCollector(doc).OfCategory(cat).ToElements():
        if isinstance(e, ElementType):
            continue
        d = {"k": kind, "id": int(e.Id.IntegerValue)}
        try:
            tp = e.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
            d["type"] = to_text(tp.AsString()) if tp is not None else "?"
        except Exception:
            d["type"] = "?"
        try:
            d["host"] = int(e.Host.Id.IntegerValue)
        except Exception:
            d["host"] = 0
        try:
            p = e.Location.Point
            d["xyz"] = [round(p.X / ft), round(p.Y / ft), round(p.Z / ft)]
        except Exception:
            d["xyz"] = None
        try:
            wp = e.LookupParameter(u"\\u5bbd\\u5ea6")
            d["w"] = round(wp.AsDouble() / ft) if (wp is not None and
                                                   wp.HasValue) else None
        except Exception:
            d["w"] = None
        try:
            sp = e.get_Parameter(BuiltInParameter.INSTANCE_ELEVATION_PARAM)
            d["sill"] = round(sp.AsDouble() / ft) if (sp is not None and
                                                      sp.HasValue) else None
        except Exception:
            d["sill"] = None
        items.append(d)
out["items"] = items
_result = out
'''

CENSUS_ENV = u'''# -*- coding: utf-8 -*-
out = {}
ft = 0.00328084
out["lvls"] = [[int(l.Id.IntegerValue), to_text(l.Name),
                round(l.Elevation / ft)]
               for l in sorted(FilteredElementCollector(doc)
                               .OfClass(Level).ToElements(),
                               key=lambda x: x.Elevation)]
ws = []
for w in FilteredElementCollector(doc).OfClass(Wall).ToElements():
    p = w.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
    lid = 0
    if p is not None and p.AsElementId() is not None:
        lid = p.AsElementId().IntegerValue
    c = w.Location.Curve
    ws.append([int(w.Id.IntegerValue), lid,
               [round(c.GetEndPoint(0).X / ft), round(c.GetEndPoint(0).Y / ft),
                round(c.GetEndPoint(0).Z / ft)],
               [round(c.GetEndPoint(1).X / ft), round(c.GetEndPoint(1).Y / ft),
                round(c.GetEndPoint(1).Z / ft)]])
out["walls"] = ws
out["rail"] = []
for rid in (334955, 334988):
    el = doc.GetElement(ElementId(rid))
    if el is None:
        out["rail"].append([rid, "none"])
    elif isinstance(el, ElementType):
        out["rail"].append([rid, "type"])
    else:
        out["rail"].append([rid, "inst"])
n = 0
for e in FilteredElementCollector(doc).OfCategory(
        BuiltInCategory.OST_Railings).ToElements():
    if not isinstance(e, ElementType):
        n += 1
out["rail_inst"] = n
el = doc.GetElement(ElementId(339814))
if el is None:
    out["roof_plate"] = "none"
else:
    bb = el.get_BoundingBox(None)
    out["roof_plate"] = [to_text(el.GetType().Name),
                         round(bb.Min.Z / ft), round(bb.Max.Z / ft)]
_result = out
'''

res_dw = run_full(CENSUS_DW, "普查-门窗")
res_env = run_full(CENSUS_ENV, "普查-环境")

# ---- client-side analysis ----
items = (res_dw.get("items") or [])
walls = (res_env.get("walls") or [])
lvls = (res_env.get("lvls") or [])
wmap = dict((l[0], l) for l in lvls)

ops = [o for o in B.openings()]
li_map = {B.LVL[0]: 0, B.LVL[1]: 1, B.LVL[2]: 2, B.LVL[3]: 3}

print(u"==== 分析 ====")
# door coincident pairs
doors = [d for d in items if d["k"] == "door"]
wins = [d for d in items if d["k"] == "window"]
print(u"doors_alive=%d windows_alive=%d" % (len(doors), len(wins)))
seen = {}
dups = []
for d in doors:
    key = tuple(d["xyz"] or ())
    if key in seen:
        dups.append((seen[key], d["id"], key))
    else:
        seen[key] = d["id"]
print(u"door_exact_dup_pairs=%d %s" % (len(dups), dups[:6]))

# dead ops: op point with no alive instance within 60mm on same level
dead = []
for o in ops:
    li = li_map[o["level"]]
    z_exp = lvls[li][2] if li < len(lvls) else 0
    hit = None
    for it in items:
        if it["k"] != o["kind"] or not it["xyz"]:
            continue
        dx = it["xyz"][0] - o["point"][0]
        dy = it["xyz"][1] - o["point"][1]
        dz = it["xyz"][2] - z_exp
        if dx * dx + dy * dy < 60 * 60 and abs(dz) < 1600:
            hit = it
            break
    if hit is None:
        dead.append((o["kind"], o["point"], o["width_mm"], o.get("sill_mm", 0),
                     z_exp))
print(u"dead_ops=%d" % len(dead))
for k, pt, w, sill, z in dead:
    # find nearest wall curve to the op point among walls based at that lvl
    lvl_id = lvls[li_map[[o["level"] for o in ops][0]]][0] if False else None
    print(u"  dead %s pt=%s w=%s sill=%s z_exp=%s" % (k, pt, w, sill, z))

# for each dead window op: wall fit check (nearest wall among that level)
by_lvl = {}
for l in lvls:
    by_lvl[l[2]] = l[0]
for k, pt, w, sill, z in dead:
    lid = by_lvl.get(z)
    best = None
    bd = 1e18
    for wid, base, s, e in walls:
        if base != lid:
            continue
        ax, ay = float(s[0]), float(s[1])
        bx, by = float(e[0]), float(e[1])
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        t = max(0.0, min(1.0, ((pt[0] - ax) * vx + (pt[1] - ay) * vy) / L2))
        cx, cy = ax + t * vx, ay + t * vy
        d2 = (cx - pt[0]) ** 2 + (cy - pt[1]) ** 2
        if d2 < bd:
            bd = d2
            best = (wid, s, e, round(t, 3), round(L2 ** 0.5))
    if best:
        wid, s, e, t, L = best
        print(u"  host_fit %s pt=%s w=%s -> wall %s len=%s t=%s "
              u"margin_s=%s margin_e=%s" % (
                  k, pt, w, wid, L, t,
                  round(t * L), round((1 - t) * L)))
