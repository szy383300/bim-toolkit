# -*- coding: utf-8 -*-
u"""element_factory.py —— 全仓唯一的"造墙"入口。

为什么要收这个口：同一套 `Wall.Create` 此前有 4 处独立实现，守卫参差不齐，
而**唯一由外部 AI 喂坐标的那一处（MCP Bridge 的 create_walls）恰好没有守卫**。

| 调用点 | NaN/退化守卫 | 接合抑制 | 事务 | 返回 |
|---|---|---|---|---|
| `bimconv_elements._create_wall` | **有**（严格） | 两端禁止 | 调用者批量 | 只给首段 |
| `model_builder` 外墙/内墙 | 无 | 无 | 自开 | 全部 |
| `bridge_core.do_create_walls` | **无** | 无 | 自开 | ids |
| `bridge_core` 栏杆薄墙 | 无 | 无 | 自开 | ids |

`bimconv_elements.py:149` 自己记着原因：喂给 `Wall.Create` 退化或 NaN 曲线会触发
**Revit 原生崩溃**，`try/except` 抓不住，表现是「Revit 遇到了意外错误」直接闪退。
所以守卫必须在 `Wall.Create` 之前，且必须在**所有**入口上。

本模块只做三件事：
  1. 把点序列切成合格线段（丢掉非有限坐标、零长/过短段，必要时闭合）；
  2. 逐段建墙，失败按段返回原因而不是抛（让调用方保留自己的计数口径）；
  3. 提供接合抑制开关 —— 翻模侧靠它躲开墙重叠风暴，别的场景不必默认开。

事务**一律由调用方管理**：Revit 禁止嵌套事务，而这三条链路的批量策略不同
（翻模分批提交、建模逐层提交、桥整批提交），工厂不替它们决定。

IronPython 2.7 兼容：无 f-string，无 math.isfinite。
"""
from __future__ import division

try:
    from pyrevit import DB
except Exception:
    DB = None

if DB is None:  # headless 语法校验 / 桥内 fallback
    try:
        import clr
        clr.AddReference("RevitAPI")
        from Autodesk.Revit import DB
    except Exception:
        DB = None

try:
    from bimlib import to_text
except Exception:
    def to_text(v):
        if isinstance(v, bytes):
            try:
                return v.decode("utf-8", "replace")
            except Exception:
                return v
        try:
            return unicode(v)
        except NameError:
            return str(v)


class BadInput(Exception):
    u"""入参本身不合法（类型/数量），该抛就抛，别装作建成功了。"""


def is_finite(v):
    u"""NaN / inf 一律 False。Python 2.7 没有 math.isfinite，手搓。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    if f != f:                      # NaN
        return False
    inf = float("inf")
    if f == inf or f == -inf:
        return False
    return True


def coords_of(p):
    u"""取点的 (x, y, z)；取不到返回 None。

    必须同时支持两种形状：Revit 的 `XYZ`（有 .X/.Y/.Z 与索引器，**没有 len()
    也不能切片**）和普通序列（list/tuple，测试与上游 JSON 用）。
    早先这里写的是 `len(p)` + `p[:3]`，在真 XYZ 上直接 TypeError —— 守卫自己
    比它要防的输入更早崩，是单元测试抓出来的。
    """
    if p is None:
        return None
    try:
        return (float(p.X), float(p.Y), float(p.Z))
    except Exception:
        pass
    try:
        x = float(p[0])
        y = float(p[1])
    except Exception:
        return None
    z = 0.0
    try:
        z = float(p[2])
    except Exception:
        try:
            if len(p) > 2:
                return None
        except Exception:
            pass
    return (x, y, z)


def point_ok(p):
    u"""一个点是否可安全喂给 Revit：三个坐标都取得到且都是有限值。"""
    c = coords_of(p)
    if c is None:
        return False
    return is_finite(c[0]) and is_finite(c[1]) and is_finite(c[2])


def segments_from_points(points, closed=False, min_len=None, total_min=None,
                         scale=1.0):
    u"""点序列 -> 合格线段 [(pa, pb), ...]。points 已是 Revit 内部单位（英尺）。

    scale 用于把"最小长度"阈值从外部单位换算到内部单位（默认 1.0 = 同单位）。

    抛 BadInput 的只有两种情况：点不够、或整条过短 —— 这两种都不该产出任何墙。
    单段退化是**跳过并记因**，因为一条折线里有一对重复点是常见输入。
    """
    pts = list(points or [])
    if len(pts) < 2:
        raise BadInput(u"点序列不足 2 个，无法成墙")
    for p in pts:
        if not point_ok(p):
            raise BadInput(u"点序列含非有限坐标（NaN/inf）或坐标缺失，整条跳过")
    seq = pts + [pts[0]] if closed else pts

    kept = []
    dropped = []
    total = 0.0
    for i in range(len(seq) - 1):
        pa, pb = seq[i], seq[i + 1]
        d = _dist(pa, pb)
        if not is_finite(d):
            dropped.append((i, u"段长度非有限"))
            continue
        if min_len is not None and d < min_len * scale:
            dropped.append((i, u"段长 %g 小于下限 %g" % (d / scale, min_len)))
            continue
        if d == 0:
            dropped.append((i, u"重合点，零长段"))
            continue
        kept.append((pa, pb))
        total += d

    if total_min is not None and total < total_min * scale:
        raise BadInput(u"整条总长 %g 小于下限 %g，跳过（噪声/退化轮廓）"
                       % (total / scale, total_min))
    if not kept:
        raise BadInput(u"所有线段都退化了：%s" % (u"; ".join(
            u"#%d %s" % (i, r) for i, r in dropped[:3]),))
    return kept, dropped


def _dist(pa, pb):
    u"""两点距离。优先用 Revit XYZ 的 DistanceTo，退化成手算（便于 headless 测试）。"""
    try:
        return pa.DistanceTo(pb)
    except Exception:
        try:
            s = 0.0
            for i in range(3):
                d = float(pa[i]) - float(pb[i])
                s += d * d
            return s ** 0.5
        except Exception:
            return float("nan")


def create_walls(doc, segments, level_id, wall_type_id, height_ft,
                 suppress_join=False, api=None, on_created=None):
    u"""逐段建墙。返回 (created, failures)。

    created   : [Wall, ...]  —— 调用方要 ids 就自己取 `.Id`，别再只回首段
    failures  : [(index, reason), ...]

    刻意不抛异常：一条折线里某段失败，不该让整条计划消失（翻模侧原来就是
    抛给上层记一条"失败"，但看不到是哪一段、为什么）。

    api: Revit DB 命名空间的替身，需带 `Line.CreateBound` / `Wall.Create`
    / `WallUtils.DisallowWallJoinAtEnd`。默认用真 DB；单元测试传桩，
    这样"守卫 + 逐段容错"这套逻辑能脱离 Revit 被真的跑起来。
    """
    if doc is None:
        raise BadInput(u"doc 为空")
    if not segments:
        raise BadInput(u"segments 为空，没有可建的段")
    if wall_type_id is None:
        raise BadInput(u"wall_type_id 为空：没有墙类型就不该调用本函数")
    if not is_finite(height_ft) or height_ft <= 0:
        raise BadInput(u"墙高 %r 非法（必须是正的有限值）" % (height_ft,))

    _api = api if api is not None else DB
    if _api is None:
        raise BadInput(u"没有 Revit API 可用；单元测试请显式传 api 桩")

    created = []
    failures = []
    for idx, (pa, pb) in enumerate(segments):
        # 双保险：即使调用方绕过 segments_from_points 自己拼了段，
        # 非有限坐标也绝不能走到 Wall.Create。
        if not point_ok(pa) or not point_ok(pb):
            failures.append((idx, u"端点含非有限坐标，拒绝建段"))
            continue
        try:
            curve = _api.Line.CreateBound(pa, pb)
            # 8 参重载是唯一无歧义的形式（Revit 2019 探针实证：曲线重载只有
            # 8 参与 3 参无类型两种；4 参静态版不存在）。参数顺序是
            # (doc, curve, wallTypeId, levelId, height, offset, flip, structural)，
            # 类型在前、楼层在后 —— 这个形式由本机「写入诊断」按钮实测可用。
            w = _api.Wall.Create(doc, curve, wall_type_id, level_id,
                                 float(height_ft), 0.0, False, False)
        except Exception as ex:
            failures.append((idx, to_text(ex)))
            continue
        if w is None:
            failures.append((idx, u"Wall.Create 返回 None"))
            continue
        if suppress_join:
            # 翻模侧实测：端点相触时 Revit 会自动接合并清理转角，这是
            # "墙重叠"警告与退化轮廓风暴的发生器（三崩实录）。纯外观收益，
            # 不值得拿稳定性去换，所以默认关掉接合。
            try:
                _api.WallUtils.DisallowWallJoinAtEnd(w, 0)
                _api.WallUtils.DisallowWallJoinAtEnd(w, 1)
            except Exception:
                pass
        created.append(w)
        if on_created is not None:
            try:
                on_created(w)
            except Exception:
                pass
    return created, failures


def describe_failures(failures, limit=3):
    u"""把 failures 压成一行给 UI/日志用。"""
    if not failures:
        return u""
    parts = []
    for idx, reason in failures[:limit]:
        parts.append(u"#%d %s" % (idx, reason))
    tail = u"" if len(failures) <= limit else u" 等 %d 段" % len(failures)
    return u"; ".join(parts) + tail
