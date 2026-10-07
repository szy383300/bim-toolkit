# -*- coding: utf-8 -*-
u"""
bimconv_rules.py —— 翻模「规则与计划」层：读 mapping_rules.csv → 匹配 → 生成建设计划。

纯 Python，不依赖 Revit，可在 headless 环境单独测试（selftest 第 5 关即跑此层）。

数据流：
  mapping_rules.csv  →  load_rules()     读成规则列表
  model_plan.json    →  plan_from_json() 按规则把每个图元映射成「计划建什么」
                                         返回 (planned, unmatched)
"""

try:
    from bimlib import read_csv_unicode
except Exception:
    # headless 校验环境也能单独 import 本模块做 plan 测试（plan 不依赖 bimlib）
    def read_csv_unicode(path):
        import csv
        import io
        rows = []
        with open(path, 'rb') as f:
            text = f.read().decode('utf-8-sig')
        for r in csv.DictReader(io.StringIO(text)):
            rows.append(dict(r))
        return rows


# 各构件类型成熟度（用于预览里打标签、提示风险）：
#   mature       = 接口稳、可直接建
#   experimental = 接口试建，逐个 try/except 上报，失败不中断整体
RELIABILITY = {
    "grid": "mature",
    "wall": "mature",
    "column": "mature",
    "door": "experimental",
    "window": "experimental",
    "room": "experimental",
    "beam": "experimental",
    "slab": "experimental",
    "stair": "experimental",
}


def _to_float(v):
    u"""宽松转浮点，失败返回 0.0。"""
    try:
        return float(str(v).strip())
    except Exception:
        return 0.0


def _fnmatch(name, pattern):
    u"""简单大小写不敏感 glob（* 通配）。"""
    import fnmatch
    return fnmatch.fnmatch(name.lower(), pattern.lower())


def _infer_level(layer, level_names):
    u"""层名里含楼层线索（1F/2F/L1/B1 等）就返回对应楼层名，否则返回 None（调用方取首层）。

    V2.3d 增强：token 覆盖扩到中文一~十层/RF/夹层等；
    图层名 "3-12F" 这类区间取首层（3F）——详图层名常见写法。
    注意：CSV 规则的 level 列是具体楼层名（如 1F）时不做推断，仅 "*" 时生效。
    """
    low = (layer or "").lower()
    for name in level_names:
        nl = name.lower()
        # 常见中文/英文楼层标记
        for token in ("1f", "2f", "3f", "4f", "5f", "6f", "7f", "8f", "9f",
                      "b1", "b2", "b3", "l1", "l2", "l3",
                      "level 1", "level 2", "level 3",
                      u"一层", u"二层", u"三层", u"四层", u"五层", u"六层",
                      u"七层", u"八层", u"九层", u"十层",
                      u"地下一层", u"地下二层", u"屋面", u"天面", u"夹层"):
            if token in low and token in nl:
                return name
        # 层名带号（"3F"/"F3"/"12层"）时与图层名中的同号楼层匹配
        import re
        m_name = re.search(r'^(?:f)?(\d{1,2})f$|^(\d{1,2})层$', nl)
        if m_name:
            num = m_name.group(1) or m_name.group(2)
            if num:
                m_low = (re.search(r'(?:^|[^0-9])(\d{1,2})\s*f', low)
                         or re.search(r'(?:^|[^0-9])f(\d{1,2})(?:$|[^0-9])', low)
                         or re.search(r'(?:^|[^0-9])(\d{1,2})层', low))
                if m_low and m_low.group(1) == num:
                    return name
    # 层名都不带号时，退而求其次：图层名"3-12F"区间取首层
    import re
    m = re.search(r'(?:^|[^0-9])(\d{1,2})\s*-\s*\d{1,2}\s*f', low)
    if m:
        first = m.group(1) + "f"
        for name in level_names:
            if name.lower().replace(" ", "") == first:
                return name
    return None


# v2.8: insert 图层兜底只放行「点定位即可建」的构件类型。
# 真图 05.dwg 实锤：31 个窗的块名全是匿名块（_DBLK/_B0MCL 等），块名规则全落空，
# 而窗靠图层 WINDOW 就能识别。轴线圈（insert AXIS）绝不能兜底成 grid——
# grid 需要起终点线几何，insert 只有定位点，兜底进去只会堆失败记录。
_INSERT_LAYER_FALLBACK = ("door", "window", "column")


def match_rule(entity, rules):
    u"""给一个 JSON 实体找匹配规则。line/lwpolyline 看图层(layer)；insert 看块名(block)。

    v2.8: insert 块名不中时回退按图层匹配一轮（仅限 door/window/column）——
    天正/匿名块（_DBLK 等）的块名毫无语义，图层才是可靠线索。
    """
    etype = entity.get("type")
    if etype == "insert":
        key = entity.get("block") or ""
        for ru in rules:
            if ru["match_type"] == "block" and _fnmatch(key, ru["pattern"]):
                return ru
        layer = entity.get("layer") or ""
        if layer:
            for ru in rules:
                if (ru["match_type"] == "layer" and _fnmatch(layer, ru["pattern"])
                        and ru["element_type"] in _INSERT_LAYER_FALLBACK):
                    return ru
        return None
    # line / lwpolyline / polyline / arc → 按图层
    layer = entity.get("layer") or ""
    for ru in rules:
        if ru["match_type"] == "layer" and _fnmatch(layer, ru["pattern"]):
            return ru
    return None


def load_rules(path):
    u"""读 mapping_rules.csv → 规则列表。

    列：match_type(layer/block), pattern(大小写不敏感glob), element_type,
        level(目标楼层名或 *), height(毫米, 0=用默认), type_hint(Revit类型名模糊匹配), note
    """
    rows = read_csv_unicode(path)
    rules = []
    for r in rows:
        if not (r.get("element_type") or "").strip():
            continue
        rules.append({
            "match_type": (r.get("match_type") or "layer").strip().lower(),
            "pattern": (r.get("pattern") or "*").strip(),
            "element_type": (r.get("element_type") or "").strip().lower(),
            "level": (r.get("level") or "*").strip(),
            "height": _to_float(r.get("height")),
            "type_hint": (r.get("type_hint") or "").strip(),
            "note": (r.get("note") or "").strip(),
        })
    return rules


def plan_from_json(plan_json, rules, level_names):
    u"""把 model_plan.json 的实体按规则映射成「计划建哪些构件」。

    返回 (planned, unmatched)：
      planned  : 每项 {element_type, reliability, level, height, type_hint,
                       source, geom, note}
      unmatched: 每项 {type, layer/block, handle}（没匹配到规则的实体）
    level_names 用于把规则里的 "*" / 层名线索解析成真实楼层名。
    """
    planned, unmatched = [], []
    for e in plan_json.get("entities", []):
        ru = match_rule(e, rules)
        if not ru:
            unmatched.append({
                "type": e.get("type"),
                "layer": e.get("layer"),
                "block": e.get("block"),
                "handle": e.get("handle"),
            })
            continue
        # 楼梯只对「轮廓多段线 / 块插入」建模；纯注释线（上行箭头/踏步线/剖断线）不建模，
        # 归入未匹配并标注，避免预览里堆出几千条细长占位盒与失败记录。
        if ru["element_type"] == "stair" and not (e.get("points")
                                                  or e.get("point") or e.get("block")):
            unmatched.append({
                "type": u"楼梯-注释线",
                "layer": e.get("layer"),
                "block": e.get("block"),
                "handle": e.get("handle"),
            })
            continue
        level = ru["level"]
        if level in ("*", ""):
            inferred = _infer_level(e.get("layer"), level_names)
            level = inferred if inferred else (level_names[0] if level_names else "")
        planned.append({
            "element_type": ru["element_type"],
            "reliability": RELIABILITY.get(ru["element_type"], "experimental"),
            "level": level,
            "height": ru["height"],
            "type_hint": ru["type_hint"],
            "source": "%s#%s" % (e.get("type"), e.get("handle")),
            "geom": e,
            "note": ru["note"],
        })
    return _refine_door_window(planned), unmatched


def _refine_door_window(planned):
    u"""v2.9 门窗改判：块定义含 ARC（门扇开启弧）的 insert 实际是门，
    即使图层/规则把它判成了窗。实测 05.dwg：31 个 WINDOW 层匿名块插入，
    其中 _DBLK(×18) 含开启弧 = 门。门高 2100（窗 1500 不适用于门）。
    """
    n_flip = 0
    for p in planned:
        if (p.get("element_type") == "window"
                and (p.get("geom") or {}).get("has_arc")):
            p["element_type"] = "door"
            p["reliability"] = RELIABILITY.get("door", "experimental")
            p["height"] = 2100.0  # 门高不沿用窗规则的 1500
            p["note"] = ((p.get("note") or "") +
                         u"；v2.9按块内开启弧由窗改判为门").strip(u"； ")
            n_flip += 1
    if n_flip:
        import sys
        sys.stderr.write("[rules] v2.9 门窗改判: %d 个含开启弧的块 窗→门\n" % n_flip)
    return planned
