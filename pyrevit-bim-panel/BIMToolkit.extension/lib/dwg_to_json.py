# -*- coding: utf-8 -*-
"""
dwg_to_json.py —— 把 DWG/DXF 读成中性 model_plan.json（翻模第一步）。

运行环境：装了 ezdxf + pywin32 的 CPython，**不是** Revit 内。
  python dwg_to_json.py <图纸.dwg|dxf> [--out model_plan.json]
  python dwg_to_json.py --selftest

关于 DWG 读取（重要）：
  ezdxf 只能读 DXF（文本），**读不了二进制 DWG**（R2000+ 都是二进制）。
  真实图纸多为 AC1032(R2018+) 等二进制 DWG，AutoCAD 2010 也打不开新格式。
  所以本脚本对二进制 DWG 自动走"转 DXF 再读"的后端（按优先级回退）：
    1) ODA File Converter（命令行，免费、无需激活；推荐。默认在 Program Files
       下找，也可用环境变量 BIMTOOLKIT_ODA 直接指到 exe）
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
        os.environ.get("BIMTOOLKIT_ODA", u""),
        r"C:\Program Files\ODAFileConverter\ODAFileConverter.exe",
        r"C:\Program Files (x86)\ODAFileConverter\ODAFileConverter.exe",
    ):
        if os.path.exists(cand):
            exe = cand
            break
    if exe is None:
        import glob as _g
        for pat in (r"C:\Program Files\ODAFileConverter*\ODAFileConverter.exe",
                    os.path.expanduser(u"~/AppData/Local/Programs/ODAFileConverter*/ODAFileConverter.exe")):
            hits = _g.glob(pat)
            if hits:
                exe = hits[0]
                break
    if exe is None:
        import shutil as _s
        exe = _s.which("ODAFileConverter.exe") or _s.which("ODAFileConverter")
    if exe is None:
        raise RuntimeError("未找到 ODA FileConverter.exe（请先安装 ODA File Converter，"
                             u"或把环境变量 BIMTOOLKIT_ODA 指到它的 exe）")
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
            if ent2 is not None:
                ent2["src_block"] = name
                ent2["exploded"] = True
                out.append(ent2)
                ctx.n += 1
        except Exception:
            continue
    ctx.blocks_done += 1


def read_dwg(path, explode_blocks=True):
    """读 DWG/DXF → (layers, blocks, entities)。二进制 DWG 自动转 DXF。

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
    xctx = _ExplodeCtx()
    skipped_ann = 0
    skipped_short = 0
    for e in msp:
        t = e.dxftype()
        handle = e.dxf.handle if hasattr(e.dxf, "handle") else ""
        layer = e.dxf.layer
        lu = layer.upper()
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
        # 其它图元（ARC/TEXT/DIMENSION 等）暂不纳入翻模，避免噪声
    if skipped_ann or skipped_short:
        sys.stderr.write("[dwg_to_json] 过滤: %d 注释图层 + %d 碎线(<%smm)\n"
                         % (skipped_ann, skipped_short, _MIN_LINE_MM))
    if explode_blocks:
        sys.stderr.write("[dwg_to_json] v2.11 块炸开: %d 实体 / %d 块实例"
                         " (上限 %d, 截断 %d)\n"
                         % (xctx.n, xctx.blocks_done, xctx.max_n,
                            xctx.skipped_capped))
    return layers, blocks, entities, skipped_ann, skipped_short


def convert(path, out_path=None, keep_dxf=False):
    layers, blocks, entities, skipped_ann, skipped_short = read_dwg(path)
    plan = {
        "source": os.path.basename(path),
        "format": "dwg" if path.lower().endswith(".dwg") else "dxf",
        "layers": layers,
        "blocks": blocks,
        "entities": entities,
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
    print("[selftest] dwg_to_json OK: %d entities (%s)" % (len(plan["entities"]), by_type))
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
