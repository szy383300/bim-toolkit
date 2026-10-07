# -*- coding: utf-8 -*-
"""
cad_annotate.py —— 阶段2：AutoCAD 智能标注（双后端，建立在成熟库之上）

为什么不再手搓 win32com：
  - ezdxf (1430★)：纯 Python 读写 DXF，无需 AutoCAD，headless 批量最方便。
  - pyautocad (609★)：封装 AutoCAD COM，原地改图最忠实（复用已验证的 COM 通道）。
  本脚本就建在这俩之上，不重复造底层轮子。

三件事（--apply 时全部执行，默认只报告）：
  1) 尺寸标注自动补全：给模型空间的 LINE 实体自动加对齐尺寸。
  2) 文字/编号标注：给每个块参照(INSERT)按序编号放置文字（无块则退化为给线段中点编号）。
  3) 图层/线型规范化：按国标风格命名+配色，重命名图层、归置实体、清空调层。

安全（硬规矩）：
  - 默认 --dry-run，只打印将做什么；加 --apply 才真正写文件。
  - 绝不覆盖原图：输出为 <原名>_annotated.<ext>，原图原封不动。
  - 写一份变更报告 CSV。

用法：
  python cad_annotate.py "D:/项目/CAD"                      # 只报告
  python cad_annotate.py "D:/项目/CAD" --apply              # ezdxf 后端批量标注 DXF
  python cad_annotate.py input.dwg --apply --backend pyautocad
  python cad_annotate.py --selftest                        # headless 自测（ezdxf，无需 AutoCAD）
"""
import os
import sys
import argparse
import tempfile

# ---- 后端库：按需导入（任一缺失都不影响另一后端） ----
try:
    import ezdxf
    HAVE_EZDXF = True
except ImportError:
    HAVE_EZDXF = False
try:
    import pyautocad
    HAVE_PYAUTOCAD = True
except ImportError:
    HAVE_PYAUTOCAD = False


# 图层规范化标准（关键字 -> (标准层名, 颜色索引 ACI)）
LAYER_STANDARD = {
    "wall": ("A-WALL", 1), "dim": ("A-DIM", 4), "text": ("A-TEXT", 7),
    "anno": ("A-ANNO", 7), "col": ("A-COLU", 5), "colu": ("A-COLU", 5),
    "door": ("A-DOOR", 2), "win": ("A-WIND", 3), "equ": ("A-EQUP", 3),
    "grid": ("A-GRID", 6), "axis": ("A-GRID", 6), "hatch": ("A-HATCH", 8),
    "title": ("A-TITL", 3), "beam": ("A-BEAM", 1), "slab": ("A-SLAB", 8),
}
DEFAULT_COLOR = 250  # 未匹配图层统一置灰，提示待整理


def layer_names(be):
    """返回后端当前所有图层名的集合（用于判断标准层是否已存在）。"""
    if be.name == "ezdxf":
        return {l.dxf.name for l in be.doc.layers}
    return {l.Name for l in be.doc.Layers}


# ============================ 后端抽象 ============================
class EzdxfAdapter:
    name = "ezdxf"

    def load(self, path):
        self.doc = ezdxf.readfile(path)
        self.msp = self.doc.modelspace()
        return self.doc

    def new(self):
        self.doc = ezdxf.new("R2010")
        self.msp = self.doc.modelspace()
        return self.doc

    def lines(self):
        return [e for e in self.msp if e.dxftype() == "LINE"]

    def inserts(self):
        return [e for e in self.msp if e.dxftype() == "INSERT"]

    def add_dim(self, p1, p2, offset=1.0, layer="A-DIM"):
        try:
            dim = self.msp.add_aligned_dim(p1, p2, distance=offset,
                                           dxfattribs={"layer": layer})
            if hasattr(dim, "render"):
                dim.render()
            return True
        except Exception as e:
            print("    [WARN] 加尺寸失败: %s" % e)
            return False

    def add_text(self, s, x, y, layer="A-TEXT", height=0.5):
        try:
            t = self.msp.add_text(s, dxfattribs={"layer": layer, "height": height})
            if hasattr(t, "set_placement"):
                t.set_placement((x, y, 0))
            return True
        except Exception as e:
            print("    [WARN] 加文字失败: %s" % e)
            return False

    def layers(self):
        return list(self.doc.layers)

    def ensure_layer(self, name, color):
        if name not in self.doc.layers:
            self.doc.layers.new(name)
        try:
            self.doc.layers.get(name).color = color
        except Exception:
            pass

    def rename_layer(self, old, new):
        try:
            self.doc.layers.rename(old, new)
            return True
        except Exception:
            return False

    def set_entity_layer(self, e, layer):
        try:
            e.dxf.layer = layer
            return True
        except Exception:
            return False

    def move_all(self, old, new):
        try:
            for e in self.msp:
                try:
                    if e.dxf.layer == old:
                        e.dxf.layer = new
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def remove_layer(self, name):
        try:
            if name != "0" and name in layer_names(self):
                self.doc.layers.remove(name)
                return True
        except Exception:
            pass
        return False

    def saveas(self, out):
        self.doc.saveas(out)

    def close(self):
        pass


class PyAcadAdapter:
    name = "pyautocad"

    def load(self, path):
        self.acad = pyautocad.Autocad(create_if_not_exists=True)
        self.acad.app.Visible = False
        self.doc = self.acad.app.Documents.Open(path)
        self.msp = self.doc.ModelSpace
        return self.doc

    def new(self):
        self.acad = pyautocad.Autocad(create_if_not_exists=True)
        self.acad.app.Visible = False
        self.doc = self.acad.app.Documents.Add()
        self.msp = self.doc.ModelSpace
        return self.doc

    def _pts(self, e):
        s = e.StartPoint
        en = e.EndPoint
        return (s[0], s[1]), (en[0], en[1])

    def lines(self):
        return [e for e in self.msp if e.ObjectName == "AcDbLine"]

    def inserts(self):
        return [e for e in self.msp if e.ObjectName == "AcDbBlockReference"]

    def add_dim(self, p1, p2, offset=1.0, layer="A-DIM"):
        try:
            mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
            dimPt = (mid[0], mid[1] + offset)
            self.msp.AddDimAligned(
                pyautocad.aDouble(p1[0], p1[1], 0, p2[0], p2[1], 0, dimPt[0], dimPt[1], 0))
            return True
        except Exception as e:
            print("    [WARN] 加尺寸失败: %s" % e)
            return False

    def add_text(self, s, x, y, layer="A-TEXT", height=0.5):
        try:
            self.msp.AddText(s, pyautocad.aDouble(x, y, 0), height)
            return True
        except Exception as e:
            print("    [WARN] 加文字失败: %s" % e)
            return False

    def layers(self):
        return [self.doc.Layers.Item(i) for i in range(self.doc.Layers.Count)]

    def ensure_layer(self, name, color):
        try:
            self.doc.Layers.Add(name)
        except Exception:
            pass
        try:
            self.doc.Layers(name).color = color
        except Exception:
            pass

    def rename_layer(self, old, new):
        try:
            self.doc.Layers(old).Name = new
            return True
        except Exception:
            return False

    def set_entity_layer(self, e, layer):
        try:
            e.Layer = layer
            return True
        except Exception:
            return False

    def move_all(self, old, new):
        try:
            for e in self.msp:
                try:
                    if e.Layer == old:
                        e.Layer = new
                except Exception:
                    pass
            return True
        except Exception:
            return False

    def remove_layer(self, name):
        return False  # pyautocad 删除图层风险高，留待人工处理

    def saveas(self, out):
        self.doc.SaveAs(out)

    def close(self):
        try:
            self.doc.Close(False)
        except Exception:
            pass


# ============================ 三项操作 ============================
def op_dimensions(be, report):
    lines = be.lines()
    n = 0
    for ln in lines:
        if be.name == "ezdxf":
            p1 = (ln.dxf.start[0], ln.dxf.start[1])
            p2 = (ln.dxf.end[0], ln.dxf.end[1])
        else:
            p1, p2 = be._pts(ln)
        if be.add_dim(p1, p2, offset=1.0):
            n += 1
    report["尺寸标注"] = n
    return n


def op_numbering(be, report):
    targets = be.inserts() or be.lines()
    n = 0
    for i, e in enumerate(targets, 1):
        if be.name == "ezdxf":
            if e.dxftype() == "INSERT":
                x, y = e.dxf.insert[0], e.dxf.insert[1]
            else:
                x = (e.dxf.start[0] + e.dxf.end[0]) / 2
                y = (e.dxf.start[1] + e.dxf.end[1]) / 2
        else:
            sp = e.InsertionPoint if e.ObjectName == "AcDbBlockReference" else e.StartPoint
            x, y = sp[0] + 1.5, sp[1] + 1.5
        if be.add_text("编号-%d" % i, x, y):
            n += 1
    report["文字编号"] = n
    return n


def op_normalize_layers(be, report):
    renamed = 0
    recolor = 0
    # 先快照层名，避免迭代途中改集合导致漏处理
    if be.name == "ezdxf":
        snapshot = [l.dxf.name for l in be.layers()]
    else:
        snapshot = [l.Name for l in be.layers()]
    for old in list(snapshot):
        if old == "0":
            continue
        low = old.lower()
        target = next(((s, c) for k, (s, c) in LAYER_STANDARD.items() if k in low), None)
        if not target:
            # 未匹配：统一置灰，提示待整理（不强行改名，避免误伤）
            try:
                if be.name == "ezdxf":
                    be.doc.layers.get(old).color = DEFAULT_COLOR
                else:
                    be.doc.Layers(old).color = DEFAULT_COLOR
                recolor += 1
            except Exception:
                pass
            continue
        std, col = target
        be.ensure_layer(std, col)          # 确保标准层存在并配色（ezdxf 无 rename，靠迁实体+删旧层实现改名）
        if std == old:
            recolor += 1                    # 已合规，仅确保配色
        else:
            be.move_all(old, std)           # 把所有实体迁到标准层
            be.remove_layer(old)            # 旧层已空则删除（pyautocad 端跳过）
            renamed += 1
    report["图层归置/重命名"] = renamed
    report["图层置灰/配色"] = recolor
    return renamed + recolor


OPS = {"dim": op_dimensions, "text": op_numbering, "layer": op_normalize_layers}


def process_file(path, backend, ops, apply, out_dir):
    be = backend()
    be.load(path)
    report = {"文件": os.path.basename(path)}
    for op in ops:
        OPS[op](be, report)
    if apply:
        base, ext = os.path.splitext(path)
        out = os.path.join(out_dir, os.path.basename(base) + "_annotated" + ext)
        try:
            be.saveas(out)
            report["输出"] = out
        except Exception as e:
            report["输出"] = "保存失败: %s" % e
    else:
        report["输出"] = "(dry-run 未写入)"
    be.close()
    return report


def selftest():
    if not HAVE_EZDXF:
        print("[FAIL] 未装 ezdxf，无法 headless 自测（pip install ezdxf）")
        return False
    print("=== selftest：ezdxf 建临时图，跑三项操作（无 AutoCAD） ===")
    be = EzdxfAdapter()
    be.new()
    # 乱图层 + 几条线 + 一个块参照
    be.doc.layers.new("wall_old")
    be.doc.layers.new("myDimLayer")
    be.msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "wall_old"})
    be.msp.add_line((0, 5), (8, 5), dxfattribs={"layer": "myDimLayer"})
    blk = be.doc.blocks.new("TESTBLK")
    be.msp.add_blockref("TESTBLK", (3, 3), dxfattribs={"layer": "wall_old"})

    rep = {}
    op_dimensions(be, rep)
    op_numbering(be, rep)
    op_normalize_layers(be, rep)
    print("  自测报告：", rep)

    # 落盘后回读校验
    fd, tmp = tempfile.mkstemp(suffix=".dxf")
    os.close(fd)
    be.saveas(tmp)
    chk = EzdxfAdapter()
    chk.load(tmp)
    layers = [l.dxftype() if False else l.dxf.name for l in chk.layers()]
    ok = (rep.get("尺寸标注", 0) >= 1 and rep.get("文字编号", 0) >= 1
          and "A-WALL" in layers and "A-DIM" in layers)
    print("  回读图层：", layers)
    print("[SELFTEST]", "PASS" if ok else "FAIL")
    os.remove(tmp)
    return ok


def main():
    ap = argparse.ArgumentParser(description="阶段2 AutoCAD 智能标注（双后端）")
    ap.add_argument("folder", nargs="?", help="要处理的文件夹或单个 DXF/DWG")
    ap.add_argument("--backend", choices=["ezdxf", "pyautocad"], default="ezdxf")
    ap.add_argument("--apply", action="store_true", help="真正写入（否则只报告）")
    ap.add_argument("--ops", default="dim,text,layer", help="逗号分隔：dim,text,layer")
    ap.add_argument("--out", help="输出目录（默认原文件同目录）")
    ap.add_argument("--selftest", action="store_true", help="headless 自测")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    if args.backend == "ezdxf" and not HAVE_EZDXF:
        sys.exit("[FAIL] 未装 ezdxf（pip install ezdxf）")
    if args.backend == "pyautocad" and not HAVE_PYAUTOCAD:
        sys.exit("[FAIL] 未装 pyautocad（pip install pyautocad）")

    backend = EzdxfAdapter if args.backend == "ezdxf" else PyAcadAdapter
    ops = [o.strip() for o in args.ops.split(",") if o.strip() in OPS]
    if not ops:
        sys.exit("[FAIL] --ops 必须是 dim,text,layer 的子集")

    targets = []
    if args.folder and os.path.isfile(args.folder):
        targets = [args.folder]
    elif args.folder and os.path.isdir(args.folder):
        for f in sorted(os.listdir(args.folder)):
            low = f.lower()
            if args.backend == "ezdxf" and low.endswith(".dxf"):
                targets.append(os.path.join(args.folder, f))
            elif args.backend == "pyautocad" and (low.endswith(".dwg") or low.endswith(".dxf")):
                targets.append(os.path.join(args.folder, f))
        if args.backend == "ezdxf" and not targets:
            print("[WARN] ezdxf 后端只吃 .dxf；DWG 请先转 DXF，或改用 --backend pyautocad")
    else:
        sys.exit("[FAIL] 请给文件夹或文件，或加 --selftest")

    out_dir = args.out or (os.path.dirname(targets[0]) or ".")
    os.makedirs(out_dir, exist_ok=True)
    rows = []
    for t in targets:
        print("\n>>> 处理：%s" % t)
        try:
            r = process_file(t, backend, ops, args.apply, out_dir)
            print("    " + " | ".join("%s=%s" % (k, v) for k, v in r.items()))
            rows.append(r)
        except Exception as e:
            print("    [SKIP] %s: %s" % (t, e))

    if args.apply and rows:
        import csv
        rep = os.path.join(out_dir, "annotate_report.csv")
        cols = list(rows[0].keys())
        with open(rep, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print("\n[REPORT] %s" % rep)
    print("\n完成。%s" % ("已写入带 _annotated 后缀的副本，原图未动" if args.apply else "dry-run 仅报告"))


if __name__ == "__main__":
    main()
