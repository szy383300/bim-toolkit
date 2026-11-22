# -*- coding: utf-8 -*-
u"""
bimconv_rules.py —— 翻模「规则与计划」层：读 mapping_rules.csv → 匹配 → 生成建设计划。

纯 Python，不依赖 Revit，可在 headless 环境单独测试（selftest 第 5 关即跑此层）。

数据流：
  mapping_rules.csv  →  load_rules()     读成规则列表
  model_plan.json    →  plan_from_json() 按规则把每个图元映射成「计划建什么」
                                         返回 (planned, unmatched)
"""
import re

try:
    from bimlib import to_text
except Exception:
    def to_text(v):
        return u"%s" % (v,)

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


_CN_DIG = {u"一": 1, u"二": 2, u"三": 3, u"四": 4, u"五": 5,
           u"六": 6, u"七": 7, u"八": 8, u"九": 9}
_CN_CHARS = u"一二三四五六七八九十"

# 本模块要在 IronPython 2.7（Revit 宿主）和 CPython 3（headless 测试）两边跑，
# 所以正则一律用 u'' + 双反斜杠，不用 ur''（Py3 语法错误）也不写裸 \s \d
# （非 raw 字符串里是未定义转义，CPython 3 直接 SyntaxWarning）。
_NUM = u"(?:[0-9]{1,2}|[" + _CN_CHARS + u"]{1,3})"
# 层号数字串与"层/楼"之间允许出现的范围分隔符（含顿号：二、三、四层）
_SEP = u"[-~～－—–至到、，,及]"
# 范围里出现"单/双"说明这是一张奇偶标准层图（7-27单数层平面，真图 r4 实测）
_PAR = u"(单数|双数|奇数|偶数)"
# 词边界是这次修复的核心：数字串前面不能再紧跟数字/中文数字，
# 否则"二十四层"会从"四"起被匹配成 4。
_BOUND = u"(?<![0-9" + _CN_CHARS + u"])"
_FLOOR_RE = re.compile(
    _BOUND + u"(?P<neg>地下室|地下|负|[-－](?=[0-9]))?\\s*"
    u"(?P<lo>" + _NUM + u")"
    u"(?:\\s*" + _SEP + u"\\s*(?P<hi>" + _NUM + u"))?\\s*"
    u"(?P<par>" + _PAR + u")?\\s*"
    u"(?:层|楼)", re.UNICODE)
# "3F / F3 / L2 / B1" 这类字母编号；B 视为地下
_LETTER_RE = re.compile(u"(?<![0-9A-Za-z])([FLBflb])[ ]*([0-9]{1,2})(?![0-9])")
_TRAIL_F_RE = re.compile(u"(?<![0-9])([0-9]{1,2})[ ]*[Ff](?![A-Za-z0-9])")

# 没有层号的楼层，图名和项目楼层名各写各的，按"同一层的不同写法"成组比对。
# 每项必须是元组：写成 (u"标准层") 是**字符串**，迭代会退化成逐字匹配"标"。
_LEVEL_NAME_GROUPS = [
    (u"屋面", u"屋顶", u"天面", u"坡屋顶"),
    (u"机房", u"电梯机房", u"机房层"),
    (u"标准层",),
    (u"夹层",),
    (u"裙房",),
    (u"骑楼",),
]


def _cn_to_int(s):
    u"""中文数字 1~99：'二十六'->26，'二十'->20，'十五'->15，'七'->7；认不出 None。

    旧实现是一张只到「二十」的字典 + `{1,2}` 的正则，所以"二十四层"整个查不到，
    再由子串匹配退化成"四层"—— 真机实测：项目楼层 21~26 层全被解析成 11~16。
    """
    s = (s or u"").strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if u"十" in s:
        head, tail = s.split(u"十", 1)
        tens = 1 if not head else _CN_DIG.get(head, 0)
        if not tens or tens > 9:
            return None
        units = 0
        if tail:
            units = _CN_DIG.get(tail, 0)
            if not units:
                return None
        v = tens * 10 + units
        return v if 0 < v <= 99 else None
    return _CN_DIG.get(s, None)


def floor_refs(text):
    u"""抽出一段文字里的**完整层号引用** → [(lo, hi, neg, parity), ...]。

    lo==hi 是单层；hi>lo 是范围（标准层一张图管多层）；neg 是地下层；
    parity 是 u"单"/u"双"/u""。抽不到就返回空 —— 调用方据此**拒绝建**，
    宁可不建也不把图放错层（放错层要拆墙重查，比不建贵得多）。
    """
    t = text or u""
    refs = []
    for m in _FLOOR_RE.finditer(t):
        lo = _cn_to_int(m.group("lo"))
        if lo is None:
            continue
        hi = _cn_to_int(m.group("hi")) if m.group("hi") else lo
        if hi is None or hi < lo:
            hi = lo
        par = m.group("par") or u""
        if par in (u"奇数",):
            par = u"单"
        elif par in (u"偶数",):
            par = u"双"
        elif par:
            par = par[:1]
        refs.append((lo, hi, bool(m.group("neg")), par))
    for m in _LETTER_RE.finditer(t):
        v = int(m.group(2))
        refs.append((v, v, m.group(1).lower() == u"b", u""))
    for m in _TRAIL_F_RE.finditer(t):
        v = int(m.group(1))
        refs.append((v, v, False, u""))
    if u"首层" in t:
        refs.append((1, 1, False, u""))
    return refs


def _level_number_of(name):
    u"""楼层名 → 层号：'标高 3'->3，'F2'->2，'地下二层'->-2，'首层'->1，'屋面'->None。"""
    import re
    n = (name or u"").strip().lower()
    refs = floor_refs(n)
    if refs:
        lo, hi, neg, par = refs[0]
        return -lo if neg else lo
    # 项目楼层普遍叫「标高 3」「层3」「3」——没有"层"字尾，兜一层裸数字
    m = (re.search(u"(?:标高|level|层)[ ]*([0-9]{1,2})$", n)
         or re.search(u"([0-9]{1,2})$", n))
    if m:
        v = int(m.group(1))
        return -v if (u"地下" in n or n.startswith(u"b")) else v
    return None


def level_from_text(text, level_names):
    u"""把图名/文件名/图层名解析成项目楼层 → (楼层名 or None, 依据文字)。

    先按**层号数值**对（读得出就只认数值，避免子串抢层），
    再退回整词命中楼层名，最后退回「屋面/屋顶/机房/标准层/夹层/裙房」这类
    无层号的命名。都失败返回 (None, "读不出层号")，由调用方拒绝建。
    """
    want_list = []
    for lo, hi, neg, par in floor_refs(text):
        want_list.append((lo, hi, neg, par))
    for lo, hi, neg, par in want_list:
        want = -lo if neg else lo
        for name in level_names:
            if _level_number_of(name) == want:
                ev = u"层号 %d" % want
                if hi != lo:
                    ev += u"~%d" % ((-hi) if neg else hi)
                if par:
                    ev += u"（%s数层共用一图，取最低层）" % par
                return name, ev
    t = text or u""
    for name in level_names:
        n = (name or u"").strip()
        if n and n in t:
            return name, u"楼层名整词命中「%s」" % n
    # 无层号的楼层只能按名对名，而且"屋顶/屋面/天面"是同一层的三种写法：
    # 图名写「机房屋顶平面」、项目楼层叫「屋面层」时，逐字比会配不上。
    for group in _LEVEL_NAME_GROUPS:
        if any(a in t for a in group):
            for name in level_names:
                if any(a in (name or u"") for a in group):
                    return name, u"按「%s」同类名匹配" % (
                        u"/".join(group[:3]),)
    return None, u"读不出层号"


def _infer_level(layer, level_names):
    u"""图层名里读出层号就按层号对层，读不出返回 None（调用方决定兜底还是拒绝）。

    旧版是 `token in low and token in nl` 的纯子串匹配，而中文数字没有词边界：
    "二十四层"里含"四层"→ 24 层平面建到 4 层，"十二层"里含"二层"→ 建到地下二层
    （2026-10-13 真图实测：项目楼层 21~26 层全被读成 11~16）。
    现在两边都先抽**完整层号**，再按数值比对。
    """
    hit, _ev = level_from_text(layer or u"", level_names)
    if hit:
        return hit
    import re
    low = (layer or u"").lower()
    # 层名都不带号时，退而求其次：图层名"3-12F"区间取首层
    m = re.search(r'(?:^|[^0-9])(\d{1,2})\s*-\s*\d{1,2}\s*f', low)
    if m:
        first = m.group(1) + "f"
        for name in level_names:
            if name.lower().replace(" ", "") == first:
                return name
    return None


def level_from_filename(fn, level_names):
    u"""从图纸文件名推它属于哪一层；推不出返回 None。

    文件名是**整张图**的证据，比图层名强，所以命中时优先于图层名推断
    （`plan_from_json` 的定层优先级：规则 > 文件名 > 图层 > levels[0]）。
    注意：一张 DWG 里并排画多张图时文件名没有层号可用，那种情形由
    bimconv_titles 按图名逐簇定层，不在此函数职责内。
    """
    hit, _ev = level_from_text(fn or u"", level_names)
    return hit



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


# ---- v2.14 Stage 4: 按图框路由（Task #33 实测数据的产品化）----
# 一张 DWG 并排画 N 张子图时，"文件"不是归位单位、"10m 连通簇"也不可靠
# （簇内图名可建率：住宅 50%、教学楼 0%）。9 张真图实测可用的分组证据是
# **图框**：住宅 93% 大簇有框、37 个平面图名 36 个恰落 1 框、框互不重叠。
# 路由链（§8.7）：框 →（无框图元）图名就近 ≤15m → 拒绝并如实报告。
# 楼层判定在框内做：框内**平面类**图名抽层号；立面/剖面/详图/目录框整框排除；
# 一个框含多个平面图名（r1 实测"二至五层+一层"同框且框内几何连通）→ 整框拒绝。
_KIND_KW = (
    (u"平面", (u"平面图", u"平面", u"布置图")),
    (u"立面", (u"立面",)),
    (u"剖面", (u"剖面",)),
    (u"详图", (u"详图", u"大样", u"节点", u"门窗表", u"楼梯表")),
    (u"目录", (u"图纸目录", u"目录", u"设计说明", u"说明")),
)
# 图名可信判据：短文本 + （含层号引用 或 含比例尺）。没有这两条的"平面"
# 字样多为正文长句（r4 实测"8.46 平面内变形性能 V 级1"这类说明文字）。
_TITLE_MAX_LEN = 25
_SCALE_RE = re.compile(u"1\\s*[:：]\\s*[0-9]{2,4}")
_FRAME_MARGIN_MM = 2000.0     # 图元落框的边距（图框双线/图元贴边）
_TITLE_NEAR_MM = 15000.0      # 无框图元的图名就近半径（r5 实测图名距图元中位 4m、max 10m）


def classify_title(text):
    u"""一段文字 → (图种 or None, 层号文字 or None)。只认「可信图名」。

    可信判据分两类：
      · 立面/剖面/详图/目录：短文本即可信（它们不需要层号，整框排除）；
      · 平面：短文本 + （含层号引用 或 含比例尺）。没这两条的"平面"
        字样多为正文长句（r4 实测"8.46 平面内变形性能 V 级1"）。
    """
    s = (text or u"").strip()
    if not s or len(s) > _TITLE_MAX_LEN:
        return None, None
    kind = None
    for k, kws in _KIND_KW:
        hit = False
        for kw in kws:
            if kw in s:
                hit = True
                break
        if hit:
            kind = k
            break
    if kind is None:
        return None, None
    if kind == u"平面":
        if not (floor_refs(s) or _SCALE_RE.search(s)):
            return None, None
        fl = None
        for lo, hi, neg, par in floor_refs(s):
            fl = u"%d" % (-lo if neg else lo)
            if hi != lo:
                fl += u"~%d" % (-hi if neg else hi)
            break
        return kind, fl
    return kind, None


def frame_routes(frames, texts, level_names):
    u"""预分析每个框 → 路由表。

    返回 list，每项 {rect, kind, level, basis, status, titles}：
      status = "plan"    可建平面图（level 已解析为项目楼层名）
              "exclude"  立面/剖面/详图/目录框（整框不建）
              "reject"   无法安全定层（含多个平面图名 / 平面图名读不出层号）
    """
    routes = []
    for fr in (frames or []):
        r = fr.get("rect") or fr.get("bbox")
        if not r or len(r) < 4:
            continue
        x0, y0, x1, y1 = [float(v) for v in r[:4]]
        titles = []
        for t in (texts or []):
            s = to_text(t.get("text") or u"")
            kind, fl = classify_title(s)
            if kind is None:
                continue
            x, y = float(t.get("x") or 0), float(t.get("y") or 0)
            if x0 - _FRAME_MARGIN_MM <= x <= x1 + _FRAME_MARGIN_MM \
                    and y0 - _FRAME_MARGIN_MM <= y <= y1 + _FRAME_MARGIN_MM:
                titles.append((kind, fl, s))
        item = {"rect": (x0, y0, x1, y1), "kind": None, "level": None,
                "basis": u"", "status": "reject", "titles": titles}
        plan_titles = [t for t in titles if t[0] == u"平面"]
        if not titles:
            # 框内没有任何可信图名：大闭合矩形可能是建筑外轮廓等误报，
            # 无证据不路由（图元走后续回退链）
            item["status"] = "no_evidence"
        elif len(plan_titles) >= 2:
            item["status"] = "reject"
            item["basis"] = u"框内 %d 个平面图名无法分辨" % len(plan_titles)
        elif len(plan_titles) == 1:
            kind, fl, s = plan_titles[0]
            lv, ev = level_from_text(s, level_names)
            if lv is None:
                item["status"] = "reject"
                item["basis"] = (u"图名「%s」读不出层号（如为标准层，"
                                 u"需项目楼层表有对应楼层）" % s[:18])
            else:
                item["status"] = "plan"
                item["level"] = lv
                item["basis"] = u"按图框图名「%s」%s" % (s[:18], ev)
        else:
            # 有图名但没有平面类：立面/剖面/详图/目录
            item["status"] = "exclude"
            item["kind"] = titles[0][0]
            item["basis"] = u"%s框（%s）" % (titles[0][0], titles[0][2][:16])
        routes.append(item)
    return routes


# --------------------------------------------------------------------------- #
# v2.15 图层映射会话（成熟产品模式：把「猜图层」变成「用户配一次」）
# 真机实锤（2026-10-15 高层全套建施图）：13152 个图元落在 LAYER1/LAYER2/
# GROUND/0 这类无语义层名上，静态 mapping_rules.csv 全 miss——该建的墙
# 大头没建。红瓦/广联达的同款解法：翻模前扫图层 → 用户确认映射 → 记住。
# --------------------------------------------------------------------------- #
def unmatched_layer_stats(entities, rules, min_count=20, top=15):
    u"""统计没命中任何规则的 (图层, 实体类型) 组合 → (大头条目, 小层数, 小层图元数)。

    大头条目: [(layer, etype, count)] 按 count 降序、只留 >= min_count、
    截 top——映射对话框只烦用户配「大头」，小杂层折叠成一行说明。
    纯 Python，可 headless 测。
    """
    buckets = {}
    n_small = 0
    e_small = 0
    for e in (entities or []):
        if match_rule(e, rules):
            continue
        k = ((e.get("layer") or u""), e.get("type") or u"?")
        buckets[k] = buckets.get(k, 0) + 1
    rows = []
    for (lay, et), n in buckets.items():
        if n >= min_count:
            rows.append((lay, et, n))
    for (lay, et), n in buckets.items():
        if n < min_count:
            n_small += 1
            e_small += n
    rows.sort(key=lambda r: (-r[2], r[0]))
    return rows[:top], n_small, e_small


def rules_from_layer_map(layer_map):
    u"""用户映射 {图层名: element_type} → rules 列表（插到 CSV 规则之前）。

    pattern 是**精确层名**（不带通配符）——fnmatch 大小写不敏感匹配下
    精确名也命中，且不会误伤别的层。element_type=exclude 表示用户判
    「这层不建」；height 统一 0（跟随层高，与 CSV 墙规则同口径）。
    """
    out = []
    for lay, et in (layer_map or {}).items():
        if not lay or not et:
            continue
        out.append({
            "match_type": "layer",
            "pattern": lay,
            "element_type": et,
            "level": "*",
            "height": 0.0,
            "type_hint": "",
            "note": u"图层映射会话（用户配置）",
        })
    return out


# 简单启发式：给对话框的「建议」列。敢建议的才建议（含糊的不猜）。
_LAYER_HINTS = (
    (u"墙", (u"ground", u"wall", u"qiang", u"wq", u"砌体")),
    (u"柱", (u"col", u"zhubao", u"zhu_col")),
    (u"窗", (u"win", u"chuang", u"chuanghua")),
    (u"门", (u"door", u"men")),
    (u"轴网", (u"axis", u"zhou", u"dote", u"axi")),
    (u"梁", (u"beam", u"liang", u"kl")),
)


def layer_hint(layer_name):
    u"""图层名 → 建议 element_type or None（小写子串启发式，仅提示不自动套用）。"""
    low = (layer_name or u"").lower()
    for et, keys in _LAYER_HINTS:
        for k in keys:
            if k in low:
                return et
    return None


def title_floor_numbers(texts):
    u"""全部可信平面图名里的层号集合（正负整数）→ {层号: 图名样例}。

    范围图名（「六、八及十~十七层」）按 floor_refs 展开为 lo..hi；
    这是 BIMMake「识别楼层表」的轻量版：图纸**声称**画了哪些层，
    与项目楼层层号比对就能发现「楼层表缺层」（真机实锤：图纸有 3 层、
    项目楼层表从 V3-L4 起，缺层导致该层构件对不上而全数拒建）。
    """
    nums = {}
    for t in (texts or []):
        s = to_text(t.get("text") or u"")
        kind, _fl = classify_title(s)
        if kind != u"平面":
            continue
        for lo, hi, neg, par in floor_refs(s):
            for n in range(lo, min(hi, lo + 199) + 1):
                key = -n if neg else n
                if key not in nums:
                    nums[key] = s[:18]
    return nums


def missing_levels_report(texts, level_names):
    u"""图纸图名层号 vs 项目楼层层号 → (缺失层号列表, 图名样例 dict)。

    只报「图纸有、项目没有」的层号（反向没意义：标准层一张图管多层）。
    """
    have = set()
    for nm in (level_names or []):
        n = _level_number_of(nm)
        if n is not None:
            have.add(n)
    want = title_floor_numbers(texts)
    miss = {n: s for n, s in want.items() if n not in have}
    return sorted(miss.keys()), miss


def _plan_anchor(p):
    u"""plan 的路由锚点（毫米）：insert 点 / 线段中点 / 折线 bbox 中心。"""
    g = p.get("geom") or {}
    pt = g.get("point")
    if pt:
        return (float(pt[0]), float(pt[1]))
    s, en = g.get("start"), g.get("end")
    if s and en:
        return ((float(s[0]) + float(en[0])) * 0.5,
                (float(s[1]) + float(en[1])) * 0.5)
    pts = g.get("points") or []
    if pts:
        xs = [float(q[0]) for q in pts]
        ys = [float(q[1]) for q in pts]
        return ((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5)
    return None


def route_plans_by_frames(planned, unmatched, frames, texts, level_names):
    u"""把 planned 按图框路由：覆盖层号 / 排除非平面框 / 拒绝读不出的。

    只在 plan_json 带 frames 且至少一个框有图名证据时生效——旧 JSON /
    无框图完全走原路径，零回归。返回 (planned, unmatched, stats)：
    stats: {frames_total, plan_frames, exclude_frames, reject_frames,
            routed, excluded, rejected, near_fallback, kept}
    """
    stats = {"frames_total": 0, "plan_frames": 0, "exclude_frames": 0,
             "reject_frames": 0, "routed": 0, "excluded": 0,
             "rejected": 0, "near_fallback": 0, "kept": 0}
    routes = frame_routes(frames, texts, level_names)
    stats["frames_total"] = len(routes)
    for it in routes:
        k = it["status"]
        if k == "plan":
            stats["plan_frames"] += 1
        elif k == "exclude":
            stats["exclude_frames"] += 1
        elif k == "reject":
            stats["reject_frames"] += 1

    # 图名就近回退候选：全部「可建平面」图名（框内的 + 游离的）
    near_titles = []
    for t in (texts or []):
        s = to_text(t.get("text") or u"")
        kind, fl = classify_title(s)
        if kind == u"平面":
            lv, _ev = level_from_text(s, level_names)
            if lv is not None:
                near_titles.append((float(t.get("x") or 0),
                                    float(t.get("y") or 0), s, lv))

    usable = [it for it in routes if it["status"] != "no_evidence"]
    if not usable:
        # v2.14.1 无框文件的图名就近回退。真机实锤（2026-10-15「高层住宅楼
        # 全套建施图.dwg」）：整张只画 1 个无证据框 = 实质无框图，6 个子图簇
        # 全落兜底层。图名贴图是常态（r5 实测图名距图元中位 4m、max 10m），
        # 所以无框也走就近——但只覆盖「兜底取首个楼层」的 plan：
        # 文件名/规则/图层定到的不动（一张图=一层的文件名证据更强）。
        if not near_titles:
            return planned, unmatched, stats
        # 拒绝只对多图文件（≥2 个可建平面图名）生效：单图名文件可能就是
        # 一张大平面（图名在角落，远端图元距图名 50m+），够不着是几何事实
        # 不是归属不明，保留兜底。
        multi = len(near_titles) >= 2
        n_near = 0
        out = []
        for p in planned:
            if u"兜底" not in (p.get("level_basis") or u""):
                out.append(p)
                continue
            a = _plan_anchor(p)
            if a is None:
                out.append(p)
                continue
            best = None
            bd = None
            for tx, ty, s, lv in near_titles:
                d2 = (tx - a[0]) ** 2 + (ty - a[1]) ** 2
                if d2 <= _TITLE_NEAR_MM * _TITLE_NEAR_MM \
                        and (bd is None or d2 < bd):
                    bd = d2
                    best = (s, lv)
            if best is not None:
                p["level"] = best[1]
                p["level_basis"] = u"图名就近(无框)「%s」" % best[0][:16]
                out.append(p)
                n_near += 1
            elif multi:
                # 多图文件里仍读不出这片的层号：拒绝（宁缺勿叠）。
                # 真机实锤（高层全套建施图.dwg）：多单元户型图集 15m 就近
                # 只够着 14%，剩下 87% 若继续兜底会全叠进首个楼层——
                # 废模型比少建更糟。
                unmatched.append({
                    "type": u"拒建·图名够不着",
                    "layer": (p.get("geom") or {}).get("layer"),
                    "handle": (p.get("geom") or {}).get("handle"),
                    "excluded": True,
                    "why": (u"无框且距最近平面图名 >%.0fm"
                            % (_TITLE_NEAR_MM / 1000.0)),
                })
                stats["rejected"] += 1
            else:
                out.append(p)
        stats["near_fallback"] += n_near
        return out, unmatched, stats

    def _frame_of(ax, ay):
        for it in usable:
            x0, y0, x1, y1 = it["rect"]
            if x0 - _FRAME_MARGIN_MM <= ax <= x1 + _FRAME_MARGIN_MM \
                    and y0 - _FRAME_MARGIN_MM <= ay <= y1 + _FRAME_MARGIN_MM:
                return it
        return None

    out = []
    drop = []
    for p in planned:
        a = _plan_anchor(p)
        if a is None:
            out.append(p)
            stats["kept"] += 1
            continue
        ax, ay = a
        it = _frame_of(ax, ay)
        if it is not None:
            if it["status"] == "plan":
                p["level"] = it["level"]
                p["level_basis"] = it["basis"]
                out.append(p)
                stats["routed"] += 1
            elif it["status"] == "exclude":
                drop.append({
                    "type": u"不建·%s" % it["kind"],
                    "layer": (p.get("geom") or {}).get("layer"),
                    "handle": (p.get("geom") or {}).get("handle"),
                    "excluded": True,
                    "why": it["basis"],
                })
                stats["excluded"] += 1
            else:  # reject：读不出/多图名框
                drop.append({
                    "type": u"拒建·层号不可辨",
                    "layer": (p.get("geom") or {}).get("layer"),
                    "handle": (p.get("geom") or {}).get("handle"),
                    "excluded": True,
                    "why": it["basis"],
                })
                stats["rejected"] += 1
            continue
        # 框外图元：图名就近回退（救"4#标准层平面"这类没画框的图）
        best = None
        bd = None
        for tx, ty, s, lv in near_titles:
            d2 = (tx - ax) ** 2 + (ty - ay) ** 2
            if d2 <= _TITLE_NEAR_MM * _TITLE_NEAR_MM and (bd is None or d2 < bd):
                bd = d2
                best = (s, lv)
        if best is not None:
            p["level"] = best[1]
            p["level_basis"] = u"图名就近「%s」" % best[0][:16]
            out.append(p)
            stats["near_fallback"] += 1
        else:
            drop.append({
                "type": u"拒建·框外且无就近图名",
                "layer": (p.get("geom") or {}).get("layer"),
                "handle": (p.get("geom") or {}).get("handle"),
                "excluded": True,
                "why": u"距最近平面图名 >%.0fm" % (_TITLE_NEAR_MM / 1000.0),
            })
            stats["rejected"] += 1
    return out, unmatched + drop, stats


def plan_from_json(plan_json, rules, level_names, file_hint=None):
    u"""把 model_plan.json 的实体按规则映射成「计划建哪些构件」。

    返回 (planned, unmatched)：
      planned  : 每项 {element_type, reliability, level, level_basis, height,
                       type_hint, source, geom, note}
      unmatched: 每项 {type, layer/block, handle}（没匹配到规则的实体）
    level_names 用于把规则里的 "*" / 层名线索解析成真实楼层名。
    file_hint 传图纸文件名（如「三层平面图.dwg」）：文件名是整张图的证据，
      比图层名更强，命中后本图所有构件统一落到该层。
    """
    planned, unmatched = [], []
    file_level = level_from_filename(
        file_hint or plan_json.get("source") or u"", level_names)
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
        # element_type=exclude：图纸上有、但我们**判定不建**（如屋面）。
        # 它必须和"规则没覆盖到"分开 —— 前者是设计决定，后者才是要修的缺陷。
        # 混在一起会让"未匹配 N 个"这个数字既像漏洞又像决定，谁也读不出信息。
        # 仍走 unmatched（加 excluded 标记）是为了不破坏 (planned, unmatched) 契约。
        if ru["element_type"] == "exclude":
            unmatched.append({
                "type": u"不建·" + (ru["note"] or ru["pattern"]),
                "layer": e.get("layer"),
                "block": e.get("block"),
                "handle": e.get("handle"),
                "excluded": True,
            })
            continue
        # 门窗同楼梯：只对「块插入 / 闭合多段线」建模，裸线段是细节线不是洞口。
        # 真图实测（2026-10-11，6 张施工图，按实体类型拆门窗 plan 的构成）：
        #   r4  窗: insert 299 / 闭合线 646 / **裸线 5488**   门: insert 608
        #   r2  窗: insert 371 / 闭合线 1675 / **裸线 2491**  门: insert 368
        #   r5  窗: insert 174 / 闭合线 24  / **裸线 3416**   门: insert 726
        # 也就是"窗"里 85~95% 是玻璃线/框线/对角示意线，不是窗。
        # r4 因此被灌进 6433 个窗，真实约 950 个 —— 这是本轮最大的过度建造，
        # 比编号缺失严重得多。门基本都是 insert，不受影响。
        # 裸线归入未匹配并标注，让预览能报出"这些是细节线，已按设计排除"。
        if (ru["element_type"] in ("door", "window")
                and not (e.get("block") or e.get("point")
                         or (e.get("closed") and e.get("points")))):
            unmatched.append({
                "type": u"门窗-细节线",
                "layer": e.get("layer"),
                "block": e.get("block"),
                "handle": e.get("handle"),
                "excluded": True,
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
        level_basis = u"规则指定"
        if level in ("*", ""):
            # 优先级：文件名（整张图的证据）> 图层名 > 项目第一个楼层。
            # 以前只有图层名一条，配不上就静默落到 levels[0]，
            # 真机实测 2048 堵墙全建在标高 1。
            if file_level:
                level, level_basis = file_level, u"按图纸文件名"
            else:
                inferred = _infer_level(e.get("layer"), level_names)
                if inferred:
                    level, level_basis = inferred, u"按图层名"
                else:
                    level = level_names[0] if level_names else ""
                    level_basis = u"兜底取首个楼层"
        planned.append({
            "element_type": ru["element_type"],
            "reliability": RELIABILITY.get(ru["element_type"], "experimental"),
            "level": level,
            "level_basis": level_basis,
            "height": ru["height"],
            "type_hint": ru["type_hint"],
            "source": "%s#%s" % (e.get("type"), e.get("handle")),
            "geom": e,
            "note": ru["note"],
        })
    planned = _consolidate_columns(_refine_door_window(planned))
    # v2.14 Stage 4：图框路由。只在 JSON 带 frames 时生效——旧 JSON /
    # 无框图（dwg_to_json < v2.14 产物）完全走原路径。路由会覆盖
    # 文件名/图层推断的层号：框内图名是"这张图画的是哪层"的直接证据，
    # 比文件名（整文件的间接证据）强。
    frames = plan_json.get("frames") or []
    if frames:
        planned, unmatched, _stats = route_plans_by_frames(
            planned, unmatched, frames, plan_json.get("texts") or [],
            level_names)
    return planned, unmatched

# ---- v2.13: 门窗编号 -> 洞口尺寸 ----
# 真图实测（2026-10-11，6 张 ODA 转换后的施工图）：
#   编号样式文字数 / 门窗 INSERT 数 / 4000mm 内有编号的占比
#     r4  722 /  875 / 72%   ← 规范施工图：M0921、C1515 这种分米命名
#     r2  133 /  689 / 35%
#     r6   28 /   72 / 38%   （2000mm 内 0% —— 编号离插入点远，半径不能太小）
#     r5    0 /  900 /  0%   ← 这张图根本不标编号，是硬上限，不是算法问题
# 所以"读编号拿尺寸"对规范图有效，但必须如实报告覆盖率，不能假装全准。
_CODE_RE = re.compile(r"^([MTLC])(\d{4})([A-Za-z]?)$", re.IGNORECASE)
_CODE_LOOSE = re.compile(r"^([MTLC])[\s\-]*[C]?[\s\-]*\d{1,4}[A-Za-z]?$",
                         re.IGNORECASE)
_OPENING_RADIUS_MM = 4000.0


def decode_opening_code(code):
    u"""分米命名法解码：'M0921' -> (900, 2100)，'C1515' -> (1500, 1500)。

    只认**恰好 4 位数字**的编号。3 位（M921）和 1~2 位（M1、C12）歧义太大——
    M1 到底是 100 宽还是 1 号门？读不出来就交给门窗表查，不在这里猜。
    """
    m = _CODE_RE.match((code or u"").strip())
    if not m:
        return None
    digits = m.group(2)
    w = int(digits[:2]) * 100
    h = int(digits[2:]) * 100
    if not (200 <= w <= 9000 and 200 <= h <= 9000):
        return None
    return (w, h)


def apply_opening_codes(planned, texts, radius_mm=_OPENING_RADIUS_MM):
    u"""把图面编号就近配给门窗 plan，能解码的直接写上洞口宽高。

    返回 (planned, stats)。stats 里 coded/decoded/uncoded 三个数必须进预览——
    没编号的洞口会继续吃 CSV 的默认尺寸，那是**猜的**，得让人看见。
    """
    stats = {"coded": 0, "decoded": 0, "uncoded": 0, "openings": 0}
    if not planned:
        return planned, stats
    cands = []
    for t in (texts or []):
        s = to_text(t.get("text") or u"").strip()
        if s and _CODE_LOOSE.match(s):
            cands.append((float(t.get("x") or 0.0), float(t.get("y") or 0.0), s))
    if not cands:
        stats["openings"] = sum(1 for p in planned
                                if p.get("element_type") in ("door", "window"))
        stats["uncoded"] = stats["openings"]
        return planned, stats

    r2 = radius_mm * radius_mm
    for p in planned:
        if p.get("element_type") not in ("door", "window"):
            continue
        stats["openings"] += 1
        g = p.get("geom") or {}
        pt = g.get("point")
        if not pt and g.get("start") and g.get("end"):
            pt = [(g["start"][0] + g["end"][0]) * 0.5,
                  (g["start"][1] + g["end"][1]) * 0.5]
        if not pt:
            stats["uncoded"] += 1
            continue
        px, py = float(pt[0]), float(pt[1])
        best = None
        bd = None
        for x, y, s in cands:
            d2 = (x - px) ** 2 + (y - py) ** 2
            if d2 <= r2 and (bd is None or d2 < bd):
                bd = d2
                best = s
        if not best:
            stats["uncoded"] += 1
            continue
        stats["coded"] += 1
        p["code"] = best
        wh = decode_opening_code(best)
        if wh:
            p["width_mm"] = wh[0]
            p["height_mm"] = wh[1]
            p["size_source"] = u"编号解码"
            stats["decoded"] += 1
        else:
            p["size_source"] = u"编号短，需查门窗表"
    return planned, stats

def _consolidate_columns(planned):
    u"""把"只有插入点的柱标记"和"它自己炸出来的实心轮廓"并成一根柱，带上实测截面。

    真图实锤（2026-10-08）：天正柱符号块 `_FZH` 内部只有一个 1x1mm 的 SOLID，
    真实截面靠 insert 的 xscale/yscale 放大（r6 全 240x240；r2 有 400x400 /
    300x500 / 300x300 三种）。dwg_to_json 现在会把 SOLID 炸成闭合轮廓，于是
    **同一根柱出现两条 plan**：insert（只有点）+ 轮廓（有形状）。r2 实测
    80 根柱 = 160 个 plan。

    配对规则：轮廓中心落在某个 insert 点附近（半径取该轮廓自身最大边长）即认定
    同一个实物——不会误配，因为相邻柱间距远大于柱宽（实测 5000 vs 400）。

    **顺序必须保持**：`plan_from_json` 的返回顺序是契约的一部分，
    `bimconv_build` 靠"results 与 planned 同序"做逐件补救定位。
    所以这里做原位替换，不重排。（第一版返回 out + others，被 selftest
    的 `planned[0] is grid` 断言抓到了。）

    既不是插入点也不是闭合轮廓的柱 plan（线段柱等）原样保留——
    第一版把这类静默丢掉了，r4 实测柱 plan 从 5248 掉到 99。
    """
    idx_of = {}
    for i, p in enumerate(planned):
        if p.get("element_type") == "column":
            idx_of[id(p)] = i

    outlines = []   # (cx, cy, w, d, planned_index)
    inserts = []    # (x, y, planned_index)
    for i, p in enumerate(planned):
        if p.get("element_type") != "column":
            continue
        g = p.get("geom") or {}
        pts = g.get("points") or []
        if g.get("type") == "insert":
            pt = g.get("point")
            if pt:
                inserts.append((float(pt[0]), float(pt[1]), i))
            continue
        if not (g.get("closed") and len(pts) >= 3):
            continue          # 线段柱等：不进任何配对，原样留在原位
        xs = [q[0] for q in pts]
        ys = [q[1] for q in pts]
        w = max(xs) - min(xs)
        d = max(ys) - min(ys)
        if w <= 0 or d <= 0:
            continue
        outlines.append(((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5,
                         w, d, i))

    drop = set()
    replace = {}
    for cx, cy, w, d, oi in outlines:
        r = max(w, d)
        partner = None
        for k, (ix, iy, ii) in enumerate(inserts):
            if ii in drop:
                continue
            if abs(ix - cx) <= r and abs(iy - cy) <= r:
                partner = ii
                break
        base = planned[oi]
        np_ = dict(base)
        np_["width_mm"] = int(round(w))
        np_["depth_mm"] = int(round(d))
        if partner is not None:
            drop.add(partner)
            ip = planned[partner]
            # 规则元数据以 insert 那条为准（它是按块名命中的），几何用轮廓
            for k in ("level", "height", "type_hint", "reliability", "note"):
                if ip.get(k) not in (None, ""):
                    np_[k] = ip[k]
            np_["source"] = u"%s+%s" % (ip.get("source") or u"",
                                        base.get("source") or u"")
        np_["note"] = ((np_.get("note") or u"") +
                       u"；柱截面实测 %dx%dmm" % (np_["width_mm"],
                                                 np_["depth_mm"])).strip(u"； ")
        replace[oi] = np_

    out = []
    for i, p in enumerate(planned):
        if i in drop:
            continue
        out.append(replace.get(i, p))
    return out


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
