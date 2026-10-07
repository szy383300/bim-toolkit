# -*- coding: utf-8 -*-
u"""genbuild.spec - 配方载入 + 静态校验 + 纯几何。

职责: 在碰桥之前把一切能在干跑期发现的问题找出来。
  load_spec       UTF-8 载入 YAML, 包装解析错误为 SpecError (含行号)
  wall_segs       墙组展开为逐段记录 (按 标高/厚/高 分组的前置)
  stair_geometry  楼梯两跑+平台布局按方向旋转变换
  validate        全量校验 -> (致命问题, 警告, 统计)

校验分级原则: 结构性错误 (会让运行期必然失败) 记 prob;
可运行但不理想 (族路径不存在 / 未知 expect 键) 记 warn。
所有旧配方的合法写法 (单 roof / roofs 列表 / 旧式 materials dict /
dwg 骨架) 均保持兼容。
"""
import io
import math

import yaml

from genbuild import config
from genbuild.errors import SpecError

VALID_OPENING_KINDS = (u"door", u"window")
VALID_EXPECT_KEYS = (u"walls", u"doors", u"windows", u"floors", u"roofs",
                     u"stairs", u"railings", u"columns", u"generic_models")
VALID_ROOF_AXES = (u"x", u"y")
# 2026-10-02 新增：屋面 macro 取值。
#   此前这个键**不校验、不透传、文档里也没有**，但它决定走哪条发射路径：
#     freeform -> 双坡棱柱 DirectShape（归 OST_Roofs，唯一能用的路）
#     gable    -> NewExtrusionRoof/NewFootPrintRoof 老路（Revit 2019 必死，
#                 失败后静默退化成**楼板**）
#   默认值曾经是 gable，于是"不写 macro"就会悄悄建出一块楼板当屋面。
#   现在 engine 的默认值已改回 freeform，这里再把取值纳入校验。
VALID_ROOF_MACROS = (u"freeform", u"gable")
DEFAULT_ROOF_MACRO = u"freeform"


# ----------------------------------------------------------------- load
def load_spec(path):
    u"""载入配方 YAML。解析/IO 错误统一包装为 SpecError (带文件名)。"""
    try:
        with io.open(path, encoding=u"utf-8") as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as ex:
        mark = getattr(ex, u"problem_mark", None)
        loc = (u" 第 %d 行" % (mark.line + 1)) if mark else u""
        raise SpecError(u"YAML 解析失败 %s%s: %s" % (path, loc, ex))
    except (IOError, OSError) as ex:
        raise SpecError(u"配方文件不可读: %s (%s)" % (path, ex))
    if not isinstance(cfg, dict):
        raise SpecError(u"配方顶层必须是映射 (dict), got %s"
                        % type(cfg).__name__)
    return cfg


# -------------------------------------------------------------- 几何
def wall_segs(cfg):
    u"""walls 组展开为逐段记录列表 (level/t/h/seg)。"""
    segs = []
    for w in cfg.get(u"walls") or []:
        for s in w.get(u"segs") or []:
            segs.append({u"level": w[u"level"],
                         u"t": w.get(u"t", 100),
                         u"h": w.get(u"h", config.WALL_H_DEFAULT),
                         u"seg": s})
    return segs


def stair_geometry(item):
    u"""按 dir 方向旋转变换楼梯布局, 返回绝对坐标的两跑+平台。

    dir 必须是 [±1,0] 或 [0,±1], 否则 ValueError (校验与运行期共用)。
    """
    dx, dy = item.get(u"dir", [0, 1])
    key = (int(dx), int(dy))
    if key not in config.STAIR_ROT:
        raise ValueError(u"stairs.dir 必须是 [±1,0]/[0,±1], got %s"
                         % item.get(u"dir"))
    rot = config.STAIR_ROT[key]
    ax, ay = item[u"at"]

    def rp(seg):
        x1, y1, x2, y2 = seg
        a = rot(x1, y1)
        b = rot(x2, y2)
        return [a[0] + ax, a[1] + ay, b[0] + ax, b[1] + ay]

    def rpts(pts):
        return [[rot(x, y)[0] + ax, rot(x, y)[1] + ay]
                for x, y in pts]

    return {u"from": item[u"from"], u"to": item[u"to"],
            u"run1": rp(config.STAIR_RUN1),
            u"run2": rp(config.STAIR_RUN2),
            u"landing": rpts(config.STAIR_LANDING),
            u"landing_offset": config.STAIR_LANDING_OFFSET}


# ------------------------------------------------------------- 校验
def _check_roof_spec(rf, where, probs):
    u"""单条屋面规格: level/span/cross 必填且形状正确, axis ∈ {x,y}。"""
    if not isinstance(rf, dict):
        probs.append(u"%s 须为映射, got %s" % (where, type(rf).__name__))
        return
    for k in (u"level", u"span", u"cross"):
        if k not in rf:
            probs.append(u"%s 缺必填键 %s" % (where, k))
    for k in (u"span", u"cross"):
        v = rf.get(k)
        if v is not None and (not isinstance(v, (list, tuple))
                              or len(v) != 2):
            probs.append(u"%s.%s 须为 [a, b] 两元素" % (where, k))
    axis = rf.get(u"axis", u"x")
    if axis not in VALID_ROOF_AXES:
        probs.append(u"%s.axis 须为 x/y, got %s" % (where, axis))
    # 2026-10-02：macro 必须校验。它是决定走哪条发射路径的键，
    #   写错了以前会静默掉进老路（然后屋面变楼板）。
    macro = rf.get(u"macro")
    if macro is not None:
        m = u"%s" % macro
        if m.strip().lower() not in VALID_ROOF_MACROS:
            probs.append(
                u"%s.macro 须为 %s 之一, got %s"
                % (where, u"/".join(VALID_ROOF_MACROS), m))


def validate(cfg):
    u"""全量静态校验。返回 (probs, warns, stats)。"""
    probs = []
    warns = []
    if cfg.get(u"schema") != 1:
        probs.append(u"schema 必须为 1")

    # ---- 标高
    levels = cfg.get(u"levels") or []
    lvls = []
    for l in levels:
        if not isinstance(l, dict) or u"name" not in l:
            probs.append(u"levels 项缺 name: %s" % l)
            continue
        lvls.append(l[u"name"])
        elev = l.get(u"elev")
        if elev is not None and not isinstance(elev, (int, float)):
            probs.append(u"levels %s.elev 须为数值, got %r"
                         % (l[u"name"], elev))
    if len(set(lvls)) != len(lvls):
        probs.append(u"levels 名称重复")

    def ref(where, name):
        if name not in lvls:
            probs.append(u"%s 引用未定义标高: %s" % (where, name))

    # ---- 轴网
    for g in cfg.get(u"grids") or []:
        if not isinstance(g, dict) or u"name" not in g \
                or u"from" not in g or u"to" not in g:
            probs.append(u"grids 项须含 name/from/to: %s" % g)
        elif len(g[u"from"]) != 2 or len(g[u"to"]) != 2:
            probs.append(u"grids %s from/to 须为 [x, y] 两元素"
                         % g.get(u"name"))

    # ---- 墙
    segs = wall_segs(cfg)
    walls_by_lvl = {}
    for s in segs:
        ref(u"walls", s[u"level"])
        if len(s[u"seg"]) != 4:
            probs.append(u"wall seg 必须 [x1,y1,x2,y2]: %s" % s[u"seg"])
        for k in (u"t", u"h"):
            v = s[k]
            if not isinstance(v, (int, float)) or v <= 0:
                probs.append(u"wall %s 须为正数, got %r" % (k, v))
        walls_by_lvl.setdefault(s[u"level"], []).append(s)

    # ---- 楼板
    for f in cfg.get(u"floors") or []:
        ref(u"floors", f[u"level"])
        if len(f.get(u"points") or []) < 3:
            probs.append(u"floors points 至少 3 点: %s" % f.get(u"points"))

    # ---- 楼梯
    for st in cfg.get(u"stairs") or []:
        ref(u"stairs", st.get(u"from"))
        ref(u"stairs", st.get(u"to"))
        try:
            stair_geometry(st)
        except Exception as ex:
            probs.append(u"stairs 几何错误: %s" % ex)

    # ---- 屋面 (兼容单 roof 与 roofs 列表)
    if cfg.get(u"roof"):
        _check_roof_spec(cfg[u"roof"], u"roof", probs)
        ref(u"roof", (cfg[u"roof"] or {}).get(u"level", u""))
    for i, rf in enumerate(cfg.get(u"roofs") or []):
        _check_roof_spec(rf, u"roofs[%d]" % i, probs)
        if isinstance(rf, dict):
            ref(u"roofs[%d]" % i, rf.get(u"level", u""))

    # ---- 栏杆
    for r in cfg.get(u"railing") or []:
        ref(u"railing", r[u"level"])
        if len(r.get(u"path") or []) < 2:
            probs.append(u"railing path 至少 2 点")

    # ---- 开洞
    ops = cfg.get(u"openings") or []
    checked = 0
    for o in ops:
        ref(u"openings", o[u"level"])
        kind = o.get(u"kind")
        if kind not in VALID_OPENING_KINDS:
            probs.append(u"openings kind 须为 door/window, got %r @%s"
                         % (kind, o.get(u"at")))
        w = o.get(u"w", 900)
        if not isinstance(w, (int, float)) or w <= 0:
            probs.append(u"openings w 须为正数, got %r" % w)
        at = o.get(u"at")
        if not at or len(at) != 2:
            probs.append(u"openings at 必须 [x,y]: %s" % o)
            continue
        if o[u"level"] not in walls_by_lvl or cfg.get(u"dwg"):
            continue  # dwg 骨架标高 -> 运行期校验(骨架墙此时尚不可知)
        px, py = float(at[0]), float(at[1])
        best = None
        bd = 1e18
        for s in walls_by_lvl[o[u"level"]]:
            x1, y1, x2, y2 = [float(v) for v in s[u"seg"]]
            vx, vy = x2 - x1, y2 - y1
            l2 = vx * vx + vy * vy
            if l2 == 0:
                continue
            tt = ((px - x1) * vx + (py - y1) * vy) / l2
            tt = max(0.0, min(1.0, tt))
            cx, cy = x1 + tt * vx, y1 + tt * vy
            d2 = (cx - px) ** 2 + (cy - py) ** 2
            if d2 < bd:
                bd = d2
                best = (s, math.sqrt(l2), tt)
        if best is None or bd > config.OPENING_MAX_WALL_DIST ** 2:
            probs.append(u"openings %s@%s %.0fmm 内无同标高墙"
                         % (kind, at, config.OPENING_MAX_WALL_DIST))
            continue
        checked += 1
        s, length, tt = best
        m1 = tt * length
        m2 = (1 - tt) * length
        half = w / 2.0
        if m1 < half - 1 or m2 < half - 1:
            probs.append(u"openings %s@%s w=%s 开洞越界 (端距 %.0f/%.0f, "
                         u"需≥%.0f)" % (kind, at, w, m1, m2, half))
        elif m1 < half + config.OPENING_JAMB_MARGIN \
                or m2 < half + config.OPENING_JAMB_MARGIN:
            warns.append(u"openings %s@%s w=%s 零窗垛贴边 "
                         u"(端距 %.0f/%.0f)" % (kind, at, w, m1, m2))

    # ---- 族 / 柱 / 饰面 / 期望 / 保存路径 (仅 warn: 可运行但不理想)
    for f in cfg.get(u"families") or []:
        p = (f or {}).get(u"path")
        if not p:
            probs.append(u"families 项缺 path: %s" % f)
        elif not _path_exists(p):
            warns.append(u"families 路径不存在 (运行期加载将报错): %s" % p)

    for c in cfg.get(u"columns") or []:
        for k in (u"fam", u"level", u"pts"):
            if k not in (c or {}):
                probs.append(u"columns 项缺必填键 %s: %s" % (k, c))

    for f in cfg.get(u"finishes") or []:
        if not (f or {}).get(u"material"):
            warns.append(u"finishes 项缺 material (运行期跳过): %s"
                         % (f.get(u"type_name") or f.get(u"target")))

    for k in (cfg.get(u"expect") or {}):
        if k not in VALID_EXPECT_KEYS:
            warns.append(u"expect 含未知键 %s (验收时按 '?' 处理)" % k)

    sp = cfg.get(u"save_path")
    if sp and not _dir_exists(_parent(sp)):
        warns.append(u"save_path 父目录不存在: %s" % _parent(sp))

    dwg = cfg.get(u"dwg")
    if dwg:
        jp = dwg.get(u"json_path")
        if jp and not _path_exists(jp):
            warns.append(u"dwg.json_path 不存在: %s" % jp)
        warns.append(u"存在 dwg 骨架步骤: 开洞落墙校验以运行期为准")

    # 屋面宏观路径也要在 stats 里可见 —— 干跑时就能看出会走哪条路，
    # 而不是等建完发现屋面是一块楼板。
    _roof_specs = ([cfg[u"roof"]] if cfg.get(u"roof") else []) \
        + (cfg.get(u"roofs") or [])
    roof_macros = []
    for _rf in _roof_specs:
        if isinstance(_rf, dict):
            _m = _rf.get(u"macro")
            roof_macros.append(u"%s" % _m if _m is not None
                               else DEFAULT_ROOF_MACRO)
        else:
            roof_macros.append(u"?")

    stats = {u"levels": len(lvls), u"grids": len(cfg.get(u"grids") or []),
             u"wall_segs": len(segs),
             u"openings": len(ops), u"openings_checked": checked,
             u"floors": len(cfg.get(u"floors") or []),
             u"stairs": len(cfg.get(u"stairs") or []),
             u"roofs": len(_roof_specs),
             u"roof_macros": roof_macros}
    return probs, warns, stats


def _path_exists(p):
    import os
    return os.path.isfile(p)


def _dir_exists(p):
    import os
    return os.path.isdir(p)


def _parent(p):
    import os
    return os.path.dirname(p)
