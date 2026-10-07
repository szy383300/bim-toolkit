# -*- coding: utf-8 -*-
u"""
进度关联 —— 阶段5 4D 关联基线：把进度计划里的任务 ↔ 模型构件按 楼层/类别/类型 对齐。

只读、安全。

数据来源：
  - 构件：直接读当前 Revit 模型（与「构件清单导出」同口径，无需先导出文件）。
  - 进度计划：本按钮目录下的 schedule_summary.csv（UTF-8，仓库已附带模板，按列填好）。

对齐规则（任务 ↔ 构件），全部满足才算覆盖：
  1) 任务.楼层 为空 或 构件.Level == 任务.楼层
  2) 任务.类别 为空 或 构件.Category == 任务.类别
  3) 任务.类型 为空 或 任务.类型 子串 ∈ 构件.FamilyType

输出：
  - 概览：任务数 / 构件总数 / 已覆盖构件数 / 覆盖率 / 缺漏任务数 / 游离构件数
  - 各任务覆盖构件数表
  - ⚠️ 缺漏任务（进度列了但模型无对应构件，可能楼层/类别写错或构件未建模）
  - 游离构件（模型有但进度计划未安排，可能漏排）
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "lib"))

from pyrevit import revit, DB, script
from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
from bimlib import (all_model_elements, element_level_name, family_type_name,
                    read_csv_unicode, to_element_list, html_table)

doc = revit.doc
output = script.get_output()


def _msg_box(msg):
    dlg = TaskDialog(u"进度关联")
    dlg.MainInstruction = msg
    dlg.CommonButtons = TaskDialogCommonButtons.Ok
    dlg.Show()


def main():
    schedule_path = os.path.join(os.path.dirname(__file__), "schedule_summary.csv")
    if not os.path.isfile(schedule_path):
        _msg_box(u"未找到进度计划：\n%s\n\n本按钮目录需有 schedule_summary.csv"
                 u"（仓库已附带模板，按列 uid,名称,楼层,类别,类型,计划开始,计划完成,状态 填好）。"
                 % schedule_path)
        return

    tasks = read_csv_unicode(schedule_path)
    if not tasks:
        _msg_box(u"进度计划 schedule_summary.csv 为空，请先填好任务行。")
        return

    # 收集构件（与「构件清单导出」同口径）
    elements = []
    for e in to_element_list(all_model_elements(doc)):
        name = ""
        try:
            name = e.Name
        except Exception:
            name = ""
        elements.append({
            "ElementId": e.Id.IntegerValue,
            "Category": e.Category.Name if e.Category else "",
            "FamilyType": family_type_name(e),
            "Level": element_level_name(e),
            "Name": name,
        })

    if not elements:
        _msg_box(u"当前模型未收集到任何非类型构件（可能模型为空或权限受限）。")
        return

    def element_matches(task, el):
        u"""构件是否满足任务的 楼层/类别/类型 过滤条件。"""
        fl = (task.get(u"楼层") or "").strip()
        cat = (task.get(u"类别") or "").strip()
        typ = (task.get(u"类型") or "").strip()
        if fl and el["Level"] != fl:
            return False
        if cat and el["Category"] != cat:
            return False
        if typ and typ.lower() not in el["FamilyType"].lower():
            return False
        return True

    output.print_md(u"## 进度关联（4D 基线）")
    output.print_md(u"_进度计划 %d 个任务 ↔ 模型 %d 个构件，按 楼层/类别/类型 对齐。_"
                    % (len(tasks), len(elements)))

    covered_ids = set()
    per_task = []
    missing_tasks = []
    for t in tasks:
        uid = (t.get("uid") or "").strip()
        name = (t.get(u"名称") or "").strip()
        matched = [el for el in elements if element_matches(t, el)]
        per_task.append((uid, name, (t.get(u"楼层") or "").strip(),
                         (t.get(u"类别") or "").strip(), len(matched)))
        for el in matched:
            covered_ids.add(el["ElementId"])
        if len(matched) == 0:
            missing_tasks.append((uid, name, (t.get(u"楼层") or "").strip(),
                                  (t.get(u"类别") or "").strip()))

    free = [el for el in elements if el["ElementId"] not in covered_ids]
    covered_n = len(covered_ids)
    rate = (covered_n / len(elements) * 100.0) if elements else 0.0

    # ---- 概览 ----
    output.print_md(u"### 概览")
    ov = [
        [u"进度任务数", len(tasks)],
        [u"模型构件数", len(elements)],
        [u"已覆盖构件数", covered_n],
        [u"覆盖率", "%.1f%%" % rate],
        [u"缺漏任务数", len(missing_tasks)],
        [u"游离构件数", len(free)],
    ]
    output.print_html(html_table([u"指标", u"数值"], ov))

    # ---- 各任务覆盖 ----
    output.print_md(u"### 各任务覆盖构件数")
    rows = [[u, n, fl, cat, c] for (u, n, fl, cat, c) in per_task]
    output.print_html(html_table([u"任务uid", u"名称", u"楼层", u"类别", u"覆盖构件数"], rows))

    # ---- 缺漏任务 ----
    output.print_md(u"### ⚠️ 缺漏任务（进度列了但模型无对应构件）")
    if missing_tasks:
        output.print_md(u"_共 %d 个任务在模型里匹配不到构件，请核对楼层/类别是否写错，或构件尚未建模：_"
                        % len(missing_tasks))
        output.print_html(html_table([u"任务uid", u"名称", u"楼层", u"类别"],
                                     [[u, n, fl, cat] for (u, n, fl, cat) in missing_tasks]))
    else:
        output.print_md(u"_无缺漏任务。_")

    # ---- 游离构件 ----
    output.print_md(u"### 游离构件（模型有但进度计划未安排）")
    if free:
        shown = free[:200]
        output.print_md(u"_共 %d 个构件未被任何进度任务覆盖（可能漏排进度）：_" % len(free))
        output.print_html(html_table(
            ["ElementId", u"类别", u"楼层", u"名称"],
            [[el["ElementId"], el["Category"], el["Level"], el["Name"]] for el in shown]))
        if len(free) > 200:
            output.print_md(u"_（仅显示前 200 个，共 %d 个）_" % len(free))
    else:
        output.print_md(u"_无游离构件，所有构件都已纳入进度计划。_")

    output.print_md(u"\n_— BIMToolkit · 进度关联（只读）—_")

    # ---- 第 2 段：生成 4D 播放文件（S15）----
    _emit_4d_steps(output, tasks, per_task)


def _out_dir():
    u"""4D 播放文件的输出目录。

    刻意**不写按钮目录**：那里是部署产物，下次 deploy 的 /MIR 会把它删掉
    （排除表只保护 bridge.token / 审计日志那几个）。放用户文档目录，部署不影响。
    取不到 Documents 就退回用户主目录。
    """
    home = os.path.expanduser("~")
    for cand in (os.path.join(home, "Documents", "BIMToolkit"),
                 os.path.join(home, "BIMToolkit")):
        try:
            if not os.path.isdir(cand):
                os.makedirs(cand)
            return cand
        except Exception:
            continue
    return home


def _doc_path():
    try:
        return revit.doc.PathName or u""
    except Exception:
        return u""


def _emit_4d_steps(output, tasks, per_task):
    u"""把进度计划转成桥认识的导览任务树，写成 JSON，并告诉用户怎么播放。"""
    try:
        import schedule_to_steps
    except Exception as e:
        output.print_md(u"\n_（未生成 4D 播放文件：加载 schedule_to_steps 失败 %s）_" % e)
        return

    counts = {}
    for row in per_task:
        try:
            uid = row[0]
            if uid:
                counts[uid] = row[4]
        except Exception:
            pass

    doc_name = u""
    try:
        doc_name = revit.doc.Title or u""
    except Exception:
        pass

    try:
        tree, warns = schedule_to_steps.build_task_tree(
            tasks, matched_counts=counts, group=u"auto",
            task_name=u"4D 施工导览 · %s" % (doc_name or u"当前模型"),
            model_path=_doc_path())
    except Exception as e:
        output.print_md(u"\n_（未生成 4D 播放文件：%s）_" % e)
        return

    if not tree:
        output.print_md(u"\n_（未生成 4D 播放文件：%s）_"
                        % (u"；".join(warns) if warns else u"没有可用任务"))
        return

    outdir = _out_dir()
    out = os.path.join(outdir, u"4d_steps.json")
    try:
        import json
        with open(out, "wb") as f:
            f.write(json.dumps(tree, ensure_ascii=False,
                               indent=2).encode("utf-8"))
    except Exception as e:
        output.print_md(u"\n_（写 4D 播放文件失败：%s）_" % e)
        return

    output.print_md(u"")
    output.print_md(u"### 4D 播放文件已生成")
    output.print_md(u"步骤 **%d** 步（首步「全楼总览」、末步「竣工全貌」）" % len(tree["steps"]))
    output.print_md(u"文件：`%s`" % out)
    for w in warns:
        output.print_md(u"⚠️ %s" % w)
    output.print_md(u"")
    output.print_md(u"**怎么播**（Revit 里需已点开 MCP Bridge）：")
    output.print_md(u"```")
    output.print_md(u"%s \"E:\\bim-toolkit\\mcp\\play_4d.py\" \"%s\" --out frames"
                    % (u"E:\\AI-pyenvs\\bim-dev\\Scripts\\python.exe", out))
    output.print_md(u"```")


if __name__ == "__main__":
    main()
else:
    main()
