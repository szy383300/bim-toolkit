# -*- coding: utf-8 -*-
"""
dwg_to_json.py —— 把 DWG/DXF 读成中性 model_plan.json（翻模第一步）。

运行环境：本机 bim-dev venv（有 ezdxf + pywin32），**不是** Revit 内。
  E:/AI-pyenvs/bim-dev/Scripts/python.exe dwg_to_json.py <图纸.dwg|dxf> [--out model_plan.json]
  E:/AI-pyenvs/bim-dev/Scripts/python.exe dwg_to_json.py --selftest

关于 DWG 读取（重要）：
  ezdxf 只能读 DXF（文本），**读不了二进制 DWG**（R2000+ 都是二进制）。
  真实图纸多为 AC1032(R2018+) 等二进制 DWG，AutoCAD 2010 也打不开新格式。
  所以本脚本对二进制 DWG 自动走"转 DXF 再读"的后端（按优先级回退）：
    1) ODA File Converter（命令行，免费、无需激活；推荐，已落地到
       E:/bim-toolkit/_tools/ODA/app/ODAFileConverter.exe）
    2) AutoCAD COM（仅能开到自身版本，老 DWG 才有用；AC1032 打不开）
    3) ZWCAD COM（中望 CAD；用户已说明无激活码、非长久之计，仅作兜底）
  都没装上时给出明确指引：先用任意 CAD / ODA File Converter 把图纸另存为
  DXF(R2000+) 再跑本脚本。

  注：ZWCAD 无激活码不可持续，已弃用为主力；ODA File Converter 免费、无需激活，
  是把任意版本 DWG 降级为 DXF(R2010)/DWG(R2010) 让 AutoCAD 2010 能吃下的正解。
  命令行（批量）：ODAFileConverter.exe <输入目录> <输出目录> ACAD2010 DXF 0

输出 JSON schema（DWG翻模.pushbutton 直接消费）：
{
  "source": "xxx.dwg", "format": "dwg",
  "layers": [...], "blocks": [...],
  "entities": [
    {"type":"line","layer":"GRID","handle":"1A","start":[x,y,z],"end":[x,y,z]},
    {"type":"lwpolyline","layer":"WALL","handle":"1B","closed":false,
     "points":[[x,y,z],...]},
    {"type":"insert","layer":"COL","handle":"1C","block":"COL-A","point":[x,y,z]}
  ]
}
说明：DWG 是 2D 平面图（z 多为 0）。楼层/高度不在这里决定，由 Revit 侧的
mapping_rules.csv 按层名/规则映射到具体楼层与层高。本脚本只负责"抽几何"。
"""
import os
import sys
import json
import math
import tempfile

# 注释/标注图层关键词（大写匹配）：命中则跳过，不参与翻模
_ANNOTATION_KW = {
    # 英文注释/标注类
    "TEXT", "MTEXT", "DIM", "DIMENSION", "HATCH", "ANNOT",
    "NOTE", "LABEL", "TAG", "TITLE", "TTLB", "SYMB", "SYMBO",
    "FURN", "EQPM", "PLMB", "ELEC", "PIPE",
    "REIN", "REIND",           # 钢筋
    "LINK",                    # 外部参照链接
    "TAB",                     # 表格
    "SURFACE",                 # 面层
    "THIN",                    # 细线/标注
    "DEFPOINT",                # 标注定义点 (Defpoints/defpoint)
    "BGB",                     # 标高表
    # 中文注释/标注类
    "引线", "标签", "注释",       # 标注
    "格子",                    # 表格网格
    "钢筋", "配筋",              # 钢筋/配筋
    "面层",                    # 面层
    "标高",                    # 标高标注
    "填充",                    # 填充图案
    "计算",                    # 计算结果
}
_MIN_LINE_MM = 50.0  # 短于 50mm 的 LINE 视为标注碎线，跳过


def _pt(v):
    """ezdxf 顶点 → [x,y,z]。DWG 多为 2D（无 z），缺省补 0。"""
    v = list(v)
    while len(v) < 3:
        v.append(0.0)
    return [float(v[0]), float(v[1]), float(v[2])]


def _is_binary_dwg(path):
    """二进制 DWG 文件头为 'AC10xx'（如 AC1024/AC1032）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(6)
    except Exception:
        return False
    return head[:4] == b"AC10" and head[4:6].isdigit()


def _is_dxf_text(path):
    """DXF 是文本，通常以 '0\\nSECTION' 开头；二进制 DWG 头是 'AC10'。"""
    try:
        with open(path, "rb") as f:
            head = f.read(80)
    except Exception:
        return False
    if head[:4] == b"AC10":
        return False
    return b"SECTION" in head or head[:2] in (b"0\n", b"0\r")


# --------------------------------------------------------------------------
# DWG → DXF 后端（按可用性回退）
# --------------------------------------------------------------------------
def _zwcad_convert(dwg_path, dxf_path):
    """用中望 CAD COM 打开 DWG 并 SaveAs 为 DXF。需安装 ZWCAD。"""
    import win32com.client
    zw = win32com.client.Dispatch("ZWCAD.Application")
    try:
        zw.Visible = False
    except Exception:
        pass
    try:
        doc = zw.Documents.Open(os.path.abspath(dwg_path))
        doc.SaveAs(os.path.abspath(dxf_path))  # .dxf 扩展名 → 导出 DXF
        doc.Close()
    finally:
        try:
            zw.Quit()
        except Exception:
            pass
    if not _is_dxf_text(dxf_path):
        raise RuntimeError("ZWCAD 输出不是 DXF 文本: %s" % dxf_path)
    return dxf_path


def _oda_convert(dwg_path, dxf_path):
    """用 ODA File Converter 命令行批量转换。免费、无需激活，推荐后端。"""
    exe = None
    for cand in (
        r"E:/bim-toolkit/_tools/ODA/app/ODAFileConverter.exe",
        r"C:\Program Files\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODAFileConverter\ODAFileConverter.exe",
    ):
        if os.path.exists(cand):
            exe = cand
            break
    if exe is None:
        import glob as _g
        for pat in (r"C:\Program Files\ODAFileConverter*\ODAFileConverter.exe",
                    r"E:/bim-toolkit/_tools/ODA*/app/ODAFileConverter.exe"):
            hits = _g.glob(pat)
            if hits:
                exe = hits[0]
                break
    if exe is None:
        import shutil as _s
        exe = _s.which("ODAFileConverter.exe") or _s.which("ODAFileConverter")
    if exe is None:
        raise RuntimeError("未找到 ODA FileConverter.exe（请先安装 ODA File Converter，或确认 E:/bim-toolkit/_tools/ODA/app/ODAFileConverter.exe 存在）")
    # v2.12: 单文件隔离转换。旧版把 dwg 所在整个目录喂给 ODA 批转——实测
    # 16 个 DWG（多个 5MB 级）120s 超时跑不完 + .dwl 锁干扰，全线失败。
    # 改为把目标 DWG 复制进独立临时目录，转完取回（实测 1.2s/个）。
    import subprocess
    import shutil
    indir = tempfile.mkdtemp(prefix="oda_one_")
    outdir = tempfile.mkdtemp(prefix="oda_out_")
    try:
        shutil.copy(dwg_path, os.path.join(
            indir, os.path.basename(dwg_path)))
        # ODAFileConverter <in> <out> <版本> <DXF/DWG> <递归0/1> <audit0/1>
        # 必须带第 6 个 audit 参数才走静默批处理，否则弹 GUI 卡死（2026-09-11 实录）。
        proc = subprocess.run([exe, indir, outdir, "ACAD2010", "DXF", "0", "1"],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            sys.stderr.write("[dwg_to_json] ODA 退出码 %s: %s\n"
                             % (proc.returncode, (proc.stderr or proc.stdout or "")[:300]))
        # ODA 输出文件名与原 DWG 同名但 .dxf
        guess = os.path.join(outdir, os.path.splitext(
            os.path.basename(dwg_path))[0] + ".dxf")
        if not os.path.exists(guess):
            raise RuntimeError("ODA 未生成 DXF: %s" % guess)
        shutil.copy(guess, dxf_path)
        return dxf_path
    finally:
        try:
            shutil.rmtree(indir, ignore_errors=True)
            shutil.rmtree(outdir, ignore_errors=True)
        except Exception:
            pass


def _acad_convert(dwg_path, dxf_path):
    """用 AutoCAD COM 打开并 SaveAs 为 DXF。仅能开到自身版本（旧 DWG）。"""
    import win32com.client
    ac = win32com.client.Dispatch("AutoCAD.Application")
    try:
        ac.Visible = False
    except Exception:
        pass
    try:
        doc = ac.Documents.Open(os.path.abspath(dwg_path))
        doc.SaveAs(os.path.abspath(dxf_path))
        doc.Close()
    finally:
        try:
            ac.Quit()
        except Exception:
            pass
    if not _is_dxf_text(dxf_path):
        raise RuntimeError("AutoCAD 输出不是 DXF 文本: %s" % dxf_path)
    return dxf_path


def _convert_dwg_to_dxf(dwg_path):
    """二进制 DWG → DXF，按后端顺序回退。返回生成的 dxf 路径。"""
    import tempfile
    base = os.path.splitext(os.path.basename(dwg_path))[0]
    dxf_path = os.path.join(tempfile.gettempdir(), base + "_converted.dxf")
    errs = []
    for name, fn in (("ODA", _oda_convert),
                     ("AutoCAD", _acad_convert),
                     ("ZWCAD", _zwcad_convert)):
        try:
            return fn(dwg_path, dxf_path)
        except Exception as e:
            errs.append("%s: %s" % (name, e))
    raise RuntimeError(
        "无法把二进制 DWG 转为 DXF，已尝试的后端全部失败：\n  " + "\n  ".join(errs) +
        "\n\n建议：用 ODA File Converter（免费、无需激活）或 AutoCAD / ZWCAD 把图纸另存为 "
        "DXF(R2000 以上)，再运行本脚本（dwg_to_json.py xxx.dxf）。"
    )


def _xf(pt, ins, rot, sx, sy):
    """2D 仿射: p' = ins + Rot(rot)·(sx,sy)⊙p。pt/ins 为 [x,y,(z)]。"""
    x = pt[0] * sx
    y = pt[1] * sy
    if rot:
        c, s = math.cos(rot), math.sin(rot)
        x, y = x * c - y * s, x * s + y * c
    return [ins[0] + x, ins[1] + y, 0.0]


class _ExplodeCtx(object):
    """块炸开上下文：计数上限 + 产出统计。"""

    def __init__(self, max_n=30000):
        self.max_n = max_n
        self.n = 0
        self.blocks_done = 0
        self.skipped_capped = 0


def _explode_insert(iref, out, blk_map, ctx, depth=0, path=(),
                    acc=None, inh_layer=None):
    u"""递归炸开 INSERT 为世界坐标 line/lwpolyline（v2.11）。

    acc: (ins, rot_rad, sx, sy) 累计仿射；inh_layer: 顶层 insert 图层
    （内层实体在 "0" 层时继承，DXF BYBLOCK 语义）。
    门块（含开启弧）由调用方跳过，不进这里。
    """
    if depth > 5 or ctx.n >= ctx.max_n:
        ctx.skipped_capped += 1
        return
    name = iref.dxf.name
    blk = blk_map.get(name)
    if blk is None or name in path:
        return
    path = path + (name,)
    ins = iref.dxf.insert
    rot = math.radians(iref.dxf.rotation or 0.0)
    sx = iref.dxf.xscale or 1.0
    sy = iref.dxf.yscale or 1.0
    if acc is None:
        acc = (ins, rot, sx, sy)
        inh_layer = iref.dxf.layer
    else:
        p0, r0, x0, y0 = acc
        # 组合: World = ParentT ∘ ChildT（非等比缩放+旋转为近似）
        nins = _xf(ins, p0, r0, x0, y0)
        acc = (nins, r0 + rot, x0 * sx, y0 * sy)

    def _layer_of(l2):
        if l2 and l2 != "0":
            return l2
        return inh_layer

    for e2 in blk:
        if ctx.n >= ctx.max_n:
            return
        t2 = e2.dxftype()
        try:
            if t2 == "INSERT":
                _explode_insert(e2, out, blk_map, ctx, depth + 1, path,
                                acc, inh_layer)
                continue
            ent2 = None
            if t2 == "LINE":
                s = _xf(e2.dxf.start, *acc)
                en = _xf(e2.dxf.end, *acc)
                L = math.hypot(en[0] - s[0], en[1] - s[1])
                if L < _MIN_LINE_MM:
                    continue
                ent2 = {"type": "line", "layer": _layer_of(e2.dxf.layer),
                        "handle": e2.dxf.handle,
                        "start": s, "end": en}
            elif t2 in ("LWPOLYLINE", "POLYLINE"):
                try:
                    pts = [list(p) for p in e2.get_points("xy")]
                except Exception:
                    try:
                        pts = [_pt(v.dxf.location) for v in e2.vertices]
                    except Exception:
                        pts = [_pt(v) for v in e2.vertices()]
                pts = [_xf(p, *acc) for p in pts]
                closed = bool(getattr(e2, "is_closed", False))
                ent2 = {"type": "lwpolyline",
                        "layer": _layer_of(e2.dxf.layer),
                        "handle": e2.dxf.handle,
                        "closed": closed, "points": pts}
            elif t2 == "SOLID":
                # SOLID = 实心填充四边形。天正的柱符号块（实测 _FZH）里就是一个
                # 1x1mm 的 SOLID，靠 insert 的 xscale/yscale 放大成真实柱截面
                # （实测 r6: 240x240；r2: 400x400 / 300x400 / 300x500）。
                # 以前这里不处理 SOLID，缩放后的轮廓被丢掉，只剩一个插入点
                # ——于是柱既量不到尺寸、也拿不到截面。
                # 顶点顺序是 vtx0,vtx1,vtx3,vtx2（DXF SOLID 的边是 0-1-3-2-0）。
                pts = [_xf(_pt(getattr(e2.dxf, "vtx%d" % i)), *acc)
                       for i in (0, 1, 3, 2)]
                ent2 = {"type": "lwpolyline",
                        "layer": _layer_of(e2.dxf.layer),
                        "handle": e2.dxf.handle,
                        "closed": True, "points": pts}
            if ent2 is not None:
                ent2["src_block"] = name
                ent2["exploded"] = True
                out.append(ent2)
                ctx.n += 1
        except Exception:
            continue
    ctx.blocks_done += 1


# --------------------------------------------------------------------------
# v2.14: 图框检测（Task #33 实测结论的产品化）
# 一张 DWG 并排画 N 张子图（各层平面+立面+剖面+详图），翻模一直把它们
# 全叠进同一楼层。9 张真图实测：图框 = 闭合轴对齐多段线（块引用全语料 0 命中），
# 住宅语料 93% 大簇有框、37 个平面图名 36 个恰落 1 框、去重后框互不重叠 0 对。
# 图框多画在标题层（PUB_TITLE 等），恰是注释过滤器要滤掉的关键字——
# 所以框检测必须放在注释过滤【之前】，检测到框就旁路输出、不进 entities。
# --------------------------------------------------------------------------
_FRAME_MIN_SHORT_MM = 20000.0    # 短边下限（A3@1:100 ≈ 42x30m；20m 兜小档）
_FRAME_MAX_LONG_MM = 300000.0    # 长边上限（再大是总图外框）


def _frame_rect_of(e):
    u"""闭合轴对齐多段线 → (x0, y0, x1, y1)；尺寸落在图框区间才返回，否则 None。

    判定与 _tools/probe_title_frames.py 完全同款（去重前的单框判定）：
    显式闭合点去重后 4~12 顶点、每条边水平或垂直（1mm 容差）、
    短边 20~300m（超长边只挡 >300m 的总图框）。
    """
    try:
        if not bool(getattr(e, "is_closed", False)):
            return None
        pts = [(float(p[0]), float(p[1])) for p in e.get_points("xy")]
    except Exception:
        return None
    if len(pts) < 4:
        return None
    q = []
    for p in pts:
        if not q or abs(p[0] - q[-1][0]) > 0.01 or abs(p[1] - q[-1][1]) > 0.01:
            q.append(p)
    if len(q) > 1 and abs(q[0][0] - q[-1][0]) <= 0.01 \
            and abs(q[0][1] - q[-1][1]) <= 0.01:
        q.pop()
    if len(q) < 4 or len(q) > 12:
        return None
    for i in range(len(q)):
        x1, y1 = q[i]
        x2, y2 = q[(i + 1) % len(q)]
        if abs(x2 - x1) > 1.0 and abs(y2 - y1) > 1.0:
            return None
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    rect = (min(xs), min(ys), max(xs), max(ys))
    short_ = min(rect[2] - rect[0], rect[3] - rect[1])
    long_ = max(rect[2] - rect[0], rect[3] - rect[1])
    if short_ < _FRAME_MIN_SHORT_MM or long_ > _FRAME_MAX_LONG_MM:
        return None
    return rect


def _dedup_frames(frames):
    u"""双线框去重（1m 网格键，内外边差 5~10mm）+ 嵌套保最外（500mm 容差）。"""
    seen = {}
    for fr in frames:
        r = fr["rect"]
        key = (int(round(r[0] / 1000.0)), int(round(r[1] / 1000.0)),
               int(round(r[2] / 1000.0)), int(round(r[3] / 1000.0)))
        if key not in seen:
            seen[key] = fr
    out = []

    def _contains(big, small):
        return (big["rect"][0] - 500.0 <= small["rect"][0]
                and big["rect"][1] - 500.0 <= small["rect"][1]
                and small["rect"][2] <= big["rect"][2] + 500.0
                and small["rect"][3] <= big["rect"][3] + 500.0)

    for fr in sorted(seen.values(),
                     key=lambda fr: -((fr["rect"][2] - fr["rect"][0])
                                      * (fr["rect"][3] - fr["rect"][1]))):
        if not any(_contains(b, fr) for b in out):
            out.append(fr)
    return out


def read_dwg(path, explode_blocks=True, texts_out=None, frames_out=None):
    """读 DWG/DXF → (layers, blocks, entities)。二进制 DWG 自动转 DXF。

    v2.13: texts_out 传一个 list，则把图面文字（TEXT/MTEXT）以
    {"text","x","y","layer","handle"} 追加进去。**不传就完全保持原行为** ——
    read_dwg 有 40 个调用点全在解 5 元组，改返回宽度会一片全断。
    文字单独收、不进 entities：它是"门窗编号在哪"的证据，
    而不是可建构件，混进 entities 会污染分类。

    v2.14: frames_out 同理——图框矩形（闭合轴对齐多段线，20~300m）以
    {"rect":[x0,y0,x1,y1],"layer"} 追加。检测先于注释过滤（图框恰画在
    PUB_TITLE 这类会被滤掉的层），检测到框的多段线不再进 entities。

    v2.11: explode_blocks=True 时把 INSERT 递归炸开为世界坐标线段
    （含开启弧的门块不炸——它们以 insert 实体按块名/图层规则建模）。
    炸出的实体带 src_block/exploded 标记，供上层按长度改派图层。
    """
    import ezdxf
    src = path
    if _is_binary_dwg(path):
        sys.stderr.write("[dwg_to_json] 检测到二进制 DWG，先转 DXF ...\n")
        src = _convert_dwg_to_dxf(path)
    doc = ezdxf.readfile(src)
    layers = [l.dxf.name for l in doc.layers]
    blocks = [b.name for b in doc.blocks]

    # v2.9 块定义特征分析：块内含 ARC（门扇开启弧，含嵌套块）→ 门；
    # 纯 LINE（玻璃线）→ 窗。实测 05.dwg：_DBLK(×18)=LINE+ARC=门，
    # 其余匿名块纯 LINE=窗。匿名块名无语义，这是唯一可靠线索。
    arc_blocks = set()
    _blk_map = {b.name: b for b in doc.blocks}

    def _has_arc(name, _seen=None):
        if name in arc_blocks:
            return True
        if _seen is None:
            _seen = set()
        if name in _seen:
            return False
        _seen.add(name)
        b = _blk_map.get(name)
        if b is None:
            return False
        for e in b:
            t = e.dxftype()
            if t == "ARC":
                arc_blocks.add(name)
                return True
            if t == "INSERT" and _has_arc(e.dxf.name, _seen):
                arc_blocks.add(name)
                return True
        return False

    for bname in blocks:
        _has_arc(bname)

    entities = []
    msp = doc.modelspace()
    # 空壳闸门用：先记下 modelspace 的原始图元数，好把"转换失败产出空 DXF"
    # 和"图有东西但被我们的规则全滤掉了"分开报 —— 这两种情况的处置方完全不同，
    # 而以前都只表现为"没找到墙"。
    raw_msp = 0
    try:
        raw_msp = sum(1 for _ in msp)
    except Exception:
        raw_msp = -1
    xctx = _ExplodeCtx()
    skipped_ann = 0
    skipped_short = 0
    frames_raw = []
    for e in msp:
        t = e.dxftype()
        handle = e.dxf.handle if hasattr(e.dxf, "handle") else ""
        layer = e.dxf.layer
        lu = layer.upper()
        # v2.14: 图框检测必须先于注释过滤——图框恰画在 PUB_TITLE 这类
        # 含 TITLE 关键字的层，过滤之后就再也看不到了。命中框的多段线
        # 旁路输出、不进 entities（它是分组证据，不是可建几何）。
        if t in ("LWPOLYLINE", "POLYLINE"):
            fr = _frame_rect_of(e)
            if fr is not None:
                frames_raw.append({"rect": [fr[0], fr[1], fr[2], fr[3]],
                                   "layer": layer})
                continue
        if any(kw in lu for kw in _ANNOTATION_KW):
            skipped_ann += 1
            continue
        if t == "LINE":
            sx, sy = e.dxf.start[0], e.dxf.start[1]
            ex, ey = e.dxf.end[0], e.dxf.end[1]
            length = math.sqrt((ex - sx) ** 2 + (ey - sy) ** 2)
            if length < _MIN_LINE_MM:
                skipped_short += 1
                continue
            entities.append({
                "type": "line", "layer": layer, "handle": handle,
                "start": _pt(e.dxf.start), "end": _pt(e.dxf.end),
            })
        elif t in ("LWPOLYLINE", "POLYLINE"):
            # v2.10: LWPOLYLINE 有 get_points；老式 POLYLINE 的 vertices 是
            # 属性(list of Vertex)不是方法，e.vertices() 会崩（实测 a7b0a24750）。
            try:
                pts = [list(p) for p in e.get_points("xy")]
            except Exception:
                try:
                    pts = [_pt(v.dxf.location) for v in e.vertices]
                except Exception:
                    pts = [_pt(v) for v in e.vertices()]
            closed = bool(getattr(e, "is_closed", False))
            entities.append({
                "type": "lwpolyline", "layer": layer, "handle": handle,
                "closed": closed, "points": [_pt(p) for p in pts],
            })
        elif t == "INSERT":
            ent = {
                "type": "insert", "layer": layer, "handle": handle,
                "block": e.dxf.name, "point": _pt(e.dxf.insert),
            }
            is_door_block = e.dxf.name in arc_blocks
            if is_door_block:
                ent["has_arc"] = True  # 块内含开启弧 → 门扇特征
            entities.append(ent)
            # v2.11: 非门块递归炸开（墙/家具等块内几何入模型空间坐标系）
            if explode_blocks and not is_door_block:
                _explode_insert(e, entities, _blk_map, xctx)
        elif t in ("TEXT", "MTEXT") and texts_out is not None:
            # 门窗编号（M0921 / C1515 / M1）是文字，不是几何。真图实测：
            # r4 有 722 条编号样式文字、覆盖 72% 的门窗 INSERT，
            # 而编号本身就按分米编码洞口尺寸（M0921 = 900x2100）。
            try:
                if t == "TEXT":
                    txt = e.dxf.text or u""
                    pos = e.dxf.insert
                else:
                    txt = e.plain_text() if hasattr(e, "plain_text") else e.text
                    pos = e.dxf.insert
                txt = (u"%s" % txt).strip()
                if txt:
                    texts_out.append({"text": txt, "layer": layer,
                                      "handle": handle,
                                      "x": float(pos.x), "y": float(pos.y)})
            except Exception:
                pass
        # 其它图元（ARC/DIMENSION 等）暂不纳入翻模，避免噪声
    if skipped_ann or skipped_short:
        sys.stderr.write("[dwg_to_json] 过滤: %d 注释图层 + %d 碎线(<%smm)\n"
                         % (skipped_ann, skipped_short, _MIN_LINE_MM))
    if frames_out is not None:
        frames_out.extend(_dedup_frames(frames_raw))
        if frames_out:
            sys.stderr.write("[dwg_to_json] 图框: %d 个（去重后，20~300m 闭合轴对齐）\n"
                             % len(frames_out))
    if explode_blocks:
        sys.stderr.write("[dwg_to_json] v2.11 块炸开: %d 实体 / %d 块实例"
                         " (上限 %d, 截断 %d)\n"
                         % (xctx.n, xctx.blocks_done, xctx.max_n,
                            xctx.skipped_capped))
    if not entities:
        try:
            src_kb = os.path.getsize(path) // 1024
        except Exception:
            src_kb = 0
        if raw_msp == 0:
            raise RuntimeError(
                u"转换结果里一个图元都没有（源文件 %s，%d KB，modelspace 空）。\n"
                u"这几乎一定是 DWG→DXF 转换产出了空壳，而不是这张图没东西 —— "
                u"已知 LibreDWG(dwg2dxf) 在建筑图上会这样：退出码 0、DXF 格式合法、"
                u"块表上千条，但块内实体为 0；同样 6 张图用 ODA 能转出 3 千~3 万个图元。\n"
                u"处理：改用 ODA File Converter，或在 AutoCAD 里另存为 DXF(R2000+) "
                u"后再翻。" % (os.path.basename(path), src_kb))
        if raw_msp > 0:
            raise RuntimeError(
                u"图里有 %d 个图元，但全部被翻模规则滤掉了"
                u"（注释图层 %d、短线 %d）。\n"
                u"这是分类规则没覆盖到本图的图层命名，不是图纸的问题 —— "
                u"请对照本图图层名检查 mapping_rules.csv 的关键字。"
                u"\n本图图层：%s" % (
                    raw_msp, skipped_ann, skipped_short,
                    u", ".join(layers[:25])))
    return layers, blocks, entities, skipped_ann, skipped_short


def convert(path, out_path=None, keep_dxf=False):
    texts = []
    frames = []
    layers, blocks, entities, skipped_ann, skipped_short = read_dwg(
        path, texts_out=texts, frames_out=frames)
    plan = {
        "source": os.path.basename(path),
        "format": "dwg" if path.lower().endswith(".dwg") else "dxf",
        "layers": layers,
        "blocks": blocks,
        "entities": entities,
        # 门窗编号靠它定位与解码尺寸（见 bimconv_rules.apply_opening_codes）
        "texts": texts,
        # v2.14: 图框矩形——Stage 4 按框路由的分组证据（Task #33 实测产品化）
        "frames": frames,
        "skipped_annotation": skipped_ann,
        "skipped_short_lines": skipped_short,
    }
    if out_path is None:
        base = os.path.splitext(path)[0]
        out_path = base + "_plan.json"
    with open(out_path, "wb") as f:
        f.write(b"\xef\xbb\xbf")
        # v2.10: 图层名偶含代理对字符（坏编码 DXF），errors='replace' 兜底
        f.write(json.dumps(plan, ensure_ascii=False)
                .encode("utf-8", "replace"))
    # 若中间生成了 DXF 且要求保留，顺手存一份（方便排查图层）
    if keep_dxf and _is_binary_dwg(path):
        dxf_copy = os.path.splitext(out_path)[0] + "_converted.dxf"
        try:
            import shutil
            shutil.copy(os.path.join(tempfile.gettempdir(),
                       os.path.splitext(os.path.basename(path))[0] + "_converted.dxf"),
                       dxf_copy)
            sys.stderr.write("[dwg_to_json] 已保留中间 DXF: %s\n" % dxf_copy)
        except Exception:
            pass
    return out_path, plan


def _selftest():
    """临时建一个 DXF（轴网线 + 墙多段线 + 柱块 + 门块）→ 转 JSON → 断言。
    注意：用 .dxf 扩展名，避免依赖 ZWCAD/ODA（保持自包含）。"""
    import ezdxf
    tmp = tempfile.mkdtemp(prefix="dwgconv_")
    dxf = os.path.join(tmp, "sample.dxf")  # DXF 文本，ezdxf 可直接读
    d = ezdxf.new("R2010")
    d.layers.add("GRID")
    d.layers.add("WALL")
    d.layers.add("COL")
    d.layers.add("DOOR")
    msp = d.modelspace()
    msp.add_line((0, 0, 0), (10000, 0, 0), dxfattribs={"layer": "GRID"})
    msp.add_lwpolyline([(0, 0), (5000, 0), (5000, 3000), (0, 3000)],
                       close=True, dxfattribs={"layer": "WALL"})
    blk = d.blocks.new("COL-A")
    blk.add_point((0, 0))
    msp.add_blockref("COL-A", (1000, 1000, 0), dxfattribs={"layer": "COL"})
    dblk = d.blocks.new("DOOR-S")
    dblk.add_point((0, 0))
    dblk.add_arc((0, 0), radius=900, start_angle=0, end_angle=90)  # 门扇开启弧
    msp.add_blockref("DOOR-S", (2500, 0, 0), dxfattribs={"layer": "DOOR"})
    # v2.14: 图框——84x59m 闭合轴对齐多段线，画在会被注释过滤器滤掉的
    # PUB_TITLE 层（真语料实测图框恰在标题层），验证"检测先于过滤"。
    msp.add_lwpolyline([(0, 0), (84000, 0), (84000, 59000), (0, 59000)],
                       close=True, dxfattribs={"layer": "PUB_TITLE"})
    # 双线框内边（差 10mm）：去重后应只剩 1 个框
    msp.add_lwpolyline([(10, 10), (83990, 10), (83990, 58990), (10, 58990)],
                       close=True, dxfattribs={"layer": "PUB_TITLE"})
    # 图名文字（框内）
    msp.add_text(u"三层平面图 1:100", dxfattribs={"layer": "PUB_TEXT"}
                 ).set_placement((70000, 2000))
    d.saveas(dxf)

    out, plan = convert(dxf)
    by_type = {}
    for e in plan["entities"]:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
    assert by_type.get("line") == 1, "grid line missing"
    assert by_type.get("lwpolyline") == 1, "wall polyline missing"
    assert by_type.get("insert") == 2, "column/door inserts missing"
    # v2.9: 门块（含 ARC）必须带上 has_arc 标签，柱块不带
    inserts = [e for e in plan["entities"] if e["type"] == "insert"]
    door_ents = [e for e in inserts if e["block"] == "DOOR-S"]
    col_ents = [e for e in inserts if e["block"] == "COL-A"]
    assert door_ents and door_ents[0].get("has_arc") is True, "door insert missing has_arc tag"
    assert not col_ents[0].get("has_arc"), "column insert must NOT have has_arc tag"
    assert "GRID" in plan["layers"] and "COL-A" in plan["blocks"]
    # v2.14: 图框旁路输出（TITLE 注释层挡不住），双线去重后恰 1 个；
    # 框不进 entities；图名文字进 texts
    frames = plan.get("frames") or []
    assert len(frames) == 1, "frame dedup failed: %d" % len(frames)
    fr = frames[0]["rect"]
    assert abs(fr[0]) < 1 and abs(fr[1]) < 1 and abs(fr[2] - 84000) < 1 \
        and abs(fr[3] - 59000) < 1, "frame rect wrong: %s" % (fr,)
    assert all(e.get("layer") != "PUB_TITLE" for e in plan["entities"]), \
        "frame polyline leaked into entities"
    texts = plan.get("texts") or []
    assert any(u"三层平面图" in (t.get("text") or u"") for t in texts), \
        "title text missing in texts[]"
    print("[selftest] dwg_to_json OK: %d entities (%s) frames=%d" % (
        len(plan["entities"]), by_type, len(frames)))
    print("[selftest] wrote %s" % out)
    return True


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        sys.exit(0)
    if len(sys.argv) < 2:
        print(u"用法: dwg_to_json.py <图纸.dwg|dxf> [--out model_plan.json] [--keep-dxf]".replace("\\", "/"))
        sys.exit(1)
    src = sys.argv[1]
    out = None
    keep = "--keep-dxf" in sys.argv
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    p, _ = convert(src, out, keep_dxf=keep)
    print(u"已生成: %s" % p)
