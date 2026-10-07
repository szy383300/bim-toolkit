# -*- coding: utf-8 -*-
u"""
batch_all.py —— BIMToolkit V1 批量分析脚本（在 Revit 内由 pyrevit run 驱动）。

用法（见桌面 BIM批量导出.bat）：
    pyrevit run "<ext>/lib/batch_all.py" --models="<清单.txt>" --revit=2019 --purge

本脚本：
  - 读取 pyrevit run 注入的全局 __models__（模型路径列表）
  - 逐个用 HOST_APP.app.OpenDocumentFile 打开（只读分析，不改模型、不保存）
  - 对每模型执行：一键统计 + 楼层清点 + BIM体检 三项分析
  - 把结果写成 UTF-8 BOM CSV 到环境变量 BIM_BATCH_CSV_DIR 指定目录
      文件名：<模型名>_一键统计.csv / <模型名>_一键统计_类别.csv
              <模型名>_楼层清点.csv / <模型名>_BIM体检.csv

重要约定（来自 pyRevit CLI 官方说明）：
  pyrevit run 不会自动打开模型，也不会把 revit.doc 设成每个模型。
  模型必须由脚本自行打开 —— 这里用 HOST_APP.app.OpenDocumentFile。
  因此本脚本只应在 `pyrevit run ... --models=` 下运行。

兼容 IronPython 2.7（Revit 2019 / pyRevit）：全程无 f-string，CSV 用 bimlib 工具。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from pyrevit import HOST_APP, DB
from bimlib import (all_model_elements, to_element_list, write_csv_unicode,
                    to_text)
from bimhealth import (scan_warnings, scan_families, scan_parameters,
                       health_score, health_grade)
from collections import Counter


# 批量输出目录：由 .bat 通过环境变量传入；缺失时兜底到脚本同级 batch_output
OUT_DIR = os.environ.get("BIM_BATCH_CSV_DIR", "")
if not OUT_DIR:
    OUT_DIR = os.path.join(HERE, "batch_output")
if not os.path.isdir(OUT_DIR):
    os.makedirs(OUT_DIR)

# Windows 文件名非法字符（用于把模型名清洗成安全文件名）
_BAD_CHARS = u'\\/:*?"<>|'


def safe_base(model_path):
    u"""模型文件名（去扩展名）清洗为文件系统安全名称。"""
    # 模型名常含中文：必须先转 unicode，否则下面 `c in _BAD_CHARS` 这个成员判断
    # 会对中文字节按 ascii 解码而抛 UnicodeDecodeError。
    base = to_text(os.path.splitext(os.path.basename(model_path))[0])
    clean = u"".join((u"_" if (c in _BAD_CHARS) else c) for c in base)
    return clean or u"model"


def process_one(doc, model_path):
    u"""对单个已打开的模型执行三项分析并写出 CSV。返回模型名（去扩展名）。"""
    base = safe_base(model_path)

    # ---------- ① 一键统计 ----------
    els = to_element_list(all_model_elements(doc))
    cat = Counter(e.Category.Name if e.Category else u"(无类别)" for e in els)
    levels = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.Level))
    views = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.View))
    sheets = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.ViewSheet))
    families = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.Family))
    warnings = to_element_list(doc.GetWarnings())

    stat_headers = [u"指标", u"数值"]
    stat_rows = [
        [u"模型构件总数", len(els)],
        [u"楼层数", len(levels)],
        [u"视图数（含图纸/明细表）", len(views)],
        [u"图纸数", len(sheets)],
        [u"已载入族数", len(families)],
        [u"当前警告数", len(warnings)],
    ]
    write_csv_unicode(os.path.join(OUT_DIR, base + u"_一键统计.csv"),
                      stat_headers, stat_rows)

    # 类别 Top30 明细
    cat_headers = [u"类别", u"数量"]
    cat_rows = [[k, v] for k, v in cat.most_common(30)]
    write_csv_unicode(os.path.join(OUT_DIR, base + u"_一键统计_类别.csv"),
                      cat_headers, cat_rows)

    # ---------- ② 楼层清点 ----------
    lv_coll = (DB.FilteredElementCollector(doc).OfClass(DB.Level)
               .WherePasses(DB.ElementIsElementTypeFilter(True)))
    levels2 = to_element_list(lv_coll)
    levels2.sort(key=lambda l: l.Elevation)
    lv_rows = []
    lv_total = 0
    for lv in levels2:
        n = len(to_element_list(
            DB.FilteredElementCollector(doc)
            .WherePasses(DB.ElementLevelFilter(lv.Id))
            .WherePasses(DB.ElementIsElementTypeFilter(True))))
        lv_rows.append([lv.Name, n])
        lv_total += n
    all_n = len(els)
    lv_rows.append([u"（无关联楼层）", all_n - lv_total])
    lv_rows.append([u"合计（全部模型构件）", all_n])
    write_csv_unicode(os.path.join(OUT_DIR, base + u"_楼层清点.csv"),
                      [u"楼层", u"构件数"], lv_rows)

    # ---------- ③ BIM体检 ----------
    warn = scan_warnings(doc)
    fam = scan_families(doc)
    param = scan_parameters(doc, all_model_elements)
    score = health_score(len(els), warn["warnings"], warn["errors"],
                         len(fam["orphan_families"]), param["missing_mark"])
    grade = health_grade(score)
    health_headers = [u"模型", u"健康分", u"等级", u"构件总数", u"楼层数",
                      u"警告数", u"错误数", u"已载入族数", u"孤儿族数",
                      u"漏标实例数", u"族类型数"]
    health_rows = [[base, score, grade, len(els), len(levels),
                    warn["warnings"], warn["errors"], fam["families"],
                    len(fam["orphan_families"]), param["missing_mark"],
                    fam["symbols"]]]
    write_csv_unicode(os.path.join(OUT_DIR, base + u"_BIM体检.csv"),
                      health_headers, health_rows)

    return base


def main():
    u"""入口：遍历 __models__，逐个打开并分析。"""
    models = globals().get("__models__", [])
    if not models:
        # 兜底：交互式（revit.doc 已打开）场景，仅处理当前文档
        try:
            from pyrevit import revit
            if revit.doc:
                models = [revit.doc.PathName]
        except Exception:
            models = []
    if not models:
        print(u"未找到目标模型（__models__ 为空，且非交互文档）。")
        return

    errors = []
    done = 0
    for mpath in models:
        try:
            doc = HOST_APP.app.OpenDocumentFile(mpath)
        except Exception as _e:
            errors.append([to_text(os.path.basename(mpath)),
                           u"打开失败: %s" % to_text(_e)])
            continue
        try:
            base = process_one(doc, mpath)
            done += 1
            print(u"完成: %s" % base)
        except Exception as _e:
            errors.append([to_text(os.path.basename(mpath)),
                           u"分析异常: %s" % to_text(_e)])
        finally:
            try:
                doc.Close(False)  # 只读，不保存
            except Exception:
                pass

    if errors:
        write_csv_unicode(os.path.join(OUT_DIR, u"批量错误日志.csv"),
                          [u"模型", u"错误信息"], errors)
        for _m, _msg in errors:
            print(u"[错误] %s -> %s" % (to_text(_m), to_text(_msg)))

    print(u"批量分析结束：成功 %d / 共 %d，输出目录 %s"
          % (done, len(models), OUT_DIR))


main()
