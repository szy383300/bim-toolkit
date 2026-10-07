# -*- coding: utf-8 -*-
u"""
merge_csv.py —— 合并 BIMToolkit 批量导出的 CSV（在 Revit 外、CPython 下运行）。

用法:
    python merge_csv.py <CSV目录>
或直接由桌面「BIM批量导出.bat」调用（目录取 BIM_BATCH_CSV_DIR）。

功能:
  扫描目录下所有 *.csv，按按钮种类把每个模型的明细纵向合并：
    *_一键统计.csv        -> 汇总_一键统计.csv
    *_一键统计_类别.csv   -> 汇总_一键统计_类别.csv
    *_楼层清点.csv        -> 汇总_楼层清点.csv
    *_BIM体检.csv         -> 汇总_BIM体检.csv   （同时即“每模型一行的体检总览”）

  汇总文件为 UTF-8 BOM（Excel 直开不乱码），列结构与各模型文件一致。
"""
import os
import sys
import io
import csv

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# bimlib 在 CPython 下仅置空 pyrevit，可安全导入；复用其 UTF-8 BOM 写出工具
from bimlib import write_csv_unicode

# 按钮种类 -> (文件名后缀, 汇总文件名)
GROUPS = [
    (u"_一键统计.csv", u"一键统计"),
    (u"_一键统计_类别.csv", u"一键统计_类别"),
    (u"_楼层清点.csv", u"楼层清点"),
    (u"_BIM体检.csv", u"BIM体检"),
]


def read_raw(path):
    u"""以 UTF-8 BOM 读取 CSV，返回 list of rows（首行为表头）。"""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8-sig")
    rows = []
    for r in csv.reader(io.StringIO(text)):
        rows.append(r)
    return rows


def merge_group(out_dir, suffix, label):
    u"""合并某按钮种类的所有模型 CSV；返回写出行数（无则 0）。"""
    fns = sorted(fn for fn in os.listdir(out_dir)
                 if fn.lower().endswith(".csv") and fn.endswith(suffix))
    if not fns:
        return 0
    headers = None
    all_rows = []
    for fn in fns:
        rows = read_raw(os.path.join(out_dir, fn))
        if not rows:
            continue
        if headers is None:
            headers = rows[0]
        all_rows.extend(rows[1:])
    if headers is None:
        return 0
    out_path = os.path.join(out_dir, u"汇总_" + label + u".csv")
    write_csv_unicode(out_path, headers, all_rows)
    print(u"汇总 %s: %d 行 -> %s" % (label, len(all_rows), out_path))
    return len(all_rows)


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BIM_BATCH_CSV_DIR", "")
    if not out_dir or not os.path.isdir(out_dir):
        print(u"用法: python merge_csv.py <CSV目录>")
        print(u"或设置环境变量 BIM_BATCH_CSV_DIR 后直接运行。")
        sys.exit(1)

    print(u"合并目录: %s" % out_dir)
    total = 0
    for suffix, label in GROUPS:
        n = merge_group(out_dir, suffix, label)
        total += n
    print(u"合并完成，共写出 %d 个数据行。" % total)
    print(u"提示：汇总_BIM体检.csv 即「每模型一行」的体检总览。")


if __name__ == "__main__":
    main()
