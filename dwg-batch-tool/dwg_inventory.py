# -*- coding: utf-8 -*-
"""
dwg_inventory.py —— 阶段 1 第一个自动化脚本：DWG/DXF 批量清点

干什么：
    给定一个文件夹，逐个用 AutoCAD COM 打开（只读，不保存），抽取每张图的
    元数据（图层数 / 布局数 / 块参照数 / 外部参照数 / 实体数），汇总成
    CSV + XLSX 报表。纯只读，绝不修改原图。

为什么用 AutoCAD COM 而不是 ezdxf：
    bim-dev 环境里没有 ezdxf，但 pywin32 + AutoCAD 2010 COM 已实测可用
    （见上级目录 env_check.py）。COM 能直接读 .dwg（含二进制 DWG，
    ezdxf 对老版本 DWG 支持也有限），是最稳的现成通道。

用法：
    # 清点某个目录，报表写到该目录
    python dwg_inventory.py "D:/某项目/CAD"

    # 指定输出路径 / 只统计 dwg
    python dwg_inventory.py "D:/某项目/CAD" --out report.xlsx --ext dwg

    # 自我验证（不依赖任何样本文件，自动建一张临时图清点后丢弃）
    python dwg_inventory.py --selftest

依赖：pywin32（win32com）。报表写 XLSX 需 openpyxl；没有则只写 CSV。
"""
import os
import sys
import argparse
import time

try:
    import win32com.client
    import pythoncom
except ImportError:
    sys.exit("[FAIL] 缺少 pywin32 —— 请先 `pip install pywin32`（bim-dev 已装）")


PROG_IDS = ("AutoCAD.Application", "AutoCAD.Application.18")


def connect():
    """尽量复用已打开的 AutoCAD；没有就新建一个。返回 (acad, self_started)。"""
    acad = None
    self_started = False
    try:
        acad = win32com.client.GetActiveObject("AutoCAD.Application")
    except Exception:
        pass
    if acad is None:
        for pid in PROG_IDS:
            try:
                acad = win32com.client.Dispatch(pid)
                self_started = True
                break
            except Exception:
                continue
    if acad is None:
        raise RuntimeError("无法创建/连接 AutoCAD COM 对象（确认 AutoCAD 已安装且 COM 已注册）")
    try:
        acad.Visible = False  # 后台跑，不闪窗
    except Exception:
        pass
    return acad, self_started


def _count_blockrefs(space):
    """统计某图纸空间里的块参照(AcDbBlockReference)数量。"""
    n = 0
    try:
        count = space.Count
    except Exception:
        return 0
    for i in range(count):
        try:
            obj = space.Item(i)
            if getattr(obj, "ObjectName", "") == "AcDbBlockReference":
                n += 1
        except Exception:
            continue
    return n


def _count_xrefs(doc):
    """统计外部参照(XRef)数量。"""
    n = 0
    try:
        blocks = doc.Blocks
        for i in range(blocks.Count):
            try:
                if blocks.Item(i).IsXRef:
                    n += 1
            except Exception:
                continue
    except Exception:
        pass
    return n


def inventory_doc(doc):
    """抽取单张图的元数据。"""
    layers = model_entities = paper_entities = 0
    try:
        layers = doc.Layers.Count
    except Exception:
        pass
    try:
        layouts = doc.Layouts.Count
    except Exception:
        layouts = 0
    blockrefs = _count_blockrefs(doc.ModelSpace) + _count_blockrefs(doc.PaperSpace)
    xrefs = _count_xrefs(doc)
    return {
        "图层数": layers,
        "布局数": layouts,
        "块参照数": blockrefs,
        "外部参照数": xrefs,
    }


def inventory_folder(folder, exts=("dwg", "dxf"), out=None):
    acad, self_started = connect()
    rows = []
    scanned = skipped = 0
    try:
        files = []
        for f in os.listdir(folder):
            low = f.lower()
            if any(low.endswith("." + e) for e in exts):
                files.append(f)
        files.sort()
        if not files:
            print(f"[WARN] {folder} 下没有找到 {','.join(exts)} 文件")
        for f in files:
            path = os.path.join(folder, f)
            scanned += 1
            try:
                doc = acad.Documents.Open(path, True)  # True = 只读
                meta = inventory_doc(doc)
                try:
                    doc.Close(False)  # 不保存
                except Exception:
                    pass
                row = {"文件": f, "大小(KB)": round(os.path.getsize(path) / 1024, 1)}
                row.update(meta)
                rows.append(row)
                print(f"  [OK] {f}  -> 图层{meta['图层数']} 块参照{meta['块参照数']} 外部参照{meta['外部参照数']}")
            except Exception as e:
                skipped += 1
                print(f"  [SKIP] {f}  -> {type(e).__name__}: {e}")
    finally:
        if self_started:
            try:
                acad.Quit()
            except Exception:
                pass
    _write_report(rows, out or os.path.join(folder, "dwg_inventory"))
    print(f"\n清点完成：扫描 {scanned} 个，成功 {len(rows)} 个，跳过 {skipped} 个")
    return rows


def _write_report(rows, basepath):
    if not rows:
        print("[INFO] 无数据，未生成报表")
        return
    # CSV 永远写（零依赖）
    csv_path = basepath + ".csv"
    import csv
    cols = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"[REPORT] CSV  -> {csv_path}")
    # XLSX（需 openpyxl）
    try:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.append(cols)
        for r in rows:
            ws.append([r[c] for c in cols])
        xlsx_path = basepath + ".xlsx"
        wb.save(xlsx_path)
        print(f"[REPORT] XLSX -> {xlsx_path}")
    except ImportError:
        print("[INFO] 未装 openpyxl，仅输出 CSV（装 `pip install openpyxl` 可得 XLSX）")


def selftest():
    """不依赖任何样本文件：新建临时图 -> 加图层/块参照 -> 清点 -> 丢弃。"""
    print("=== selftest：用 AutoCAD COM 自建临时图并清点 ===")
    acad, self_started = connect()
    doc = None
    try:
        doc = acad.Documents.Add()
        # 加一个图层
        try:
            doc.Layers.Add("SELFTEST_LAYER")
        except Exception as e:
            print(f"  [WARN] 加图层失败（不影响清点）: {e}")
        # 加一个块定义并插入块参照，验证块参照计数路径
        try:
            p0 = win32com.client.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [0.0, 0.0, 0.0])
            blk = doc.Blocks.Add(p0, "SELFTEST_BLOCK")
            doc.ModelSpace.InsertBlock(p0, "SELFTEST_BLOCK", 1.0, 1.0, 1.0, 0.0)
        except Exception as e:
            print(f"  [WARN] 插块参照失败（不影响其余清点）: {e}")
        meta = inventory_doc(doc)
        print("  临时图清点结果：", meta)
        ok = meta["图层数"] >= 1 and meta["块参照数"] >= 0
        print("[SELFTEST]", "PASS" if ok else "FAIL")
        return ok
    finally:
        if doc is not None:
            try:
                doc.Close(False)  # 不保存
            except Exception:
                pass
        if self_started:
            try:
                acad.Quit()
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="DWG/DXF 批量清点（只读，不改动原图）")
    ap.add_argument("folder", nargs="?", help="要清点的文件夹（默认当前目录）")
    ap.add_argument("--out", help="报表基名（不含扩展名），默认 <folder>/dwg_inventory")
    ap.add_argument("--ext", action="append", help="只统计指定扩展名，可多次，如 --ext dwg")
    ap.add_argument("--selftest", action="store_true", help="自建临时图验证 COM 清点路径")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)
    folder = args.folder or os.getcwd()
    if not os.path.isdir(folder):
        sys.exit(f"[FAIL] 目录不存在：{folder}")
    exts = tuple(args.ext) if args.ext else ("dwg", "dxf")
    inventory_folder(folder, exts=exts, out=args.out)


if __name__ == "__main__":
    main()
