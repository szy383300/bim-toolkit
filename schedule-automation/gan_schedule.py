# -*- coding: utf-8 -*-
"""
gan_schedule.py —— 阶段5：进度计划自动化（GanttProject .gan → 横道图 → 周报）

为什么自己写解析器而不是调库：
  GanttProject 的 .gan 就是**纯 XML**，没有现成 Python 解析库（ganttproject 本体是 Java/Swing）。
  但结构极规整：<tasks> 嵌套 <task>、<depend> 表达前置关系、<resources>/<allocations> 表达资源，
  <roles> 表达角色。用标准库 xml.etree 就能稳定解析，无需任何外部依赖，headless 可跑。

三件事（--apply 时全部写出，默认只打印概览）：
  1) 解析 .gan：拍平任务层级、前置依赖、资源分配、角色；
  2) 自动横道图：matplotlib 画甘特条（已完成段实心、未完成段淡色）、里程碑菱形、今日线；
  3) 一键周报：markdown 概览 + 阶段进度 + 逾期清单 + 近两周待办 + 资源负荷。

安全（硬规矩）：
  - 默认 --dry-run，只打印概览，不写任何文件；加 --apply 才生成 PNG/MD/CSV/JSON。
  - 绝不改原图：所有产物写到独立输出目录，原 .gan 原封不动。
  - 4D 关联（任务↔Revit 构件）列为扩展点：本脚本只把 task.uid 留好接口，不碰 Revit。

依赖：仅标准库 + matplotlib（横道图）。pandas/openpyxl 非必需，可选增强 CSV/Excel。

用法：
  python gan_schedule.py "D:/CAD/GanttProject/HouseBuildingSample.gan"            # 仅概览
  python gan_schedule.py "D:/CAD/GanttProject/HouseBuildingSample.gan" --apply     # 出图+周报+CSV+JSON
  python gan_schedule.py plan.gan --apply --today 2024-07-01 --out ./out           # 指定基准日/输出目录
  python gan_schedule.py --selftest                                            # headless 自测（临时 .gan）
"""
import os
import sys
import csv
import json
import tempfile
import argparse
import datetime as dt
from xml.etree import ElementTree as ET

# 横道图需要绘图；无显示环境用 Agg 后端
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm

# 注册系统中文字体，避免横道图中文变方框（DejaVu 不含 CJK 字形）
def setup_cjk_font():
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/NotoSansCJK-Regular.ttc",
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                fm.fontManager.addfont(p)
                name = fm.FontProperties(fname=p).get_name()
                plt.rcParams["font.sans-serif"] = [name]
                plt.rcParams["axes.unicode_minus"] = False
                return True
            except Exception:
                pass
    return False

HAS_CJK = setup_cjk_font()

def L(zh, en):
    """有中文字体用中文，否则退化为英文，保证横道图永远可读。"""
    return zh if HAS_CJK else en

# GanttProject <depend type="..."> 枚举（源码 Dependency 常量）
# 0=完成-开始(FS) 1=开始-开始(SS) 2=完成-完成(FF) 3=开始-完成(SF)
DEP_TYPE = {0: "完成-开始(FS)", 1: "开始-开始(SS)",
            2: "完成-完成(FF)", 3: "开始-完成(SF)"}

# 状态颜色（PNG 用），与中文状态对应
STATUS_COLOR = {
    "已完成":   "#2e7d32",
    "进行中":   "#1565c0",
    "已逾期":   "#c62828",
    "未开始":   "#9e9e9e",
    "里程碑":   "#e65100",
}


# ============================ 解析 ============================
def _parse_task(elem, level, parent_id, tasks, id_map):
    """递归解析 <task>，拍平成带层级/父子关系的平面表。"""
    t = {
        "id": elem.get("id"),
        "uid": elem.get("uid"),
        "name": (elem.get("name") or "").strip(),
        "color": elem.get("color") or "#99ccff",
        "meeting": (elem.get("meeting") == "true"),
        "start": elem.get("start"),
        "duration": int(elem.get("duration") or 0),
        "complete": int(elem.get("complete") or 0),
        "cost": elem.get("cost-manual-value"),
        "level": level,
        "parent_id": parent_id,
        "notes": "",
        "deps": [],          # [{pred_id, type, hardness, lag}]
        "custom": {},        # taskproperty-id -> value
    }
    for ch in elem:
        if ch.tag == "depend":
            try:
                dtype = int(ch.get("type") or 0)
            except ValueError:
                dtype = 0
            t["deps"].append({
                "pred_id": ch.get("id"),
                "type": DEP_TYPE.get(dtype, "未知(%s)" % dtype),
                "hardness": ch.get("hardness"),
                "lag": ch.get("difference"),
            })
        elif ch.tag == "customproperty":
            t["custom"][ch.get("taskproperty-id")] = ch.get("value")
        elif ch.tag == "notes" and ch.text:
            t["notes"] = " ".join(ch.text.split())
    tasks.append(t)
    id_map[t["id"]] = t
    for sub in elem.findall("task"):
        _parse_task(sub, level + 1, t["id"], tasks, id_map)


def parse_gan(path):
    """解析 .gan，返回 (meta, tasks, resources, allocations, roles)。"""
    tree = ET.parse(path)
    root = tree.getroot()
    meta = {
        "name": root.get("name", ""),
        "company": root.get("company", ""),
        "view_date": root.get("view-date"),
        "version": root.get("version"),
        "locale": root.get("locale"),
    }
    tasks, id_map = [], {}
    troot = root.find("tasks")
    if troot is not None:
        for top in troot.findall("task"):
            _parse_task(top, 0, None, tasks, id_map)

    resources = []
    rroot = root.find("resources")
    if rroot is not None:
        for r in rroot.findall("resource"):
            rate = None
            rc = r.find("rate")
            if rc is not None:
                rate = rc.get("value")
            resources.append({
                "id": r.get("id"),
                "name": r.get("name"),
                "function": r.get("function"),
                "phone": r.get("phone"),
                "rate": rate,
            })

    allocations = []
    aroot = root.find("allocations")
    if aroot is not None:
        for a in aroot.findall("allocation"):
            allocations.append({
                "task_id": a.get("task-id"),
                "resource_id": a.get("resource-id"),
                "function": a.get("function"),
                "responsible": (a.get("responsible") == "true"),
                "load": a.get("load"),
            })

    roles = []
    for roles_tag in root.findall("roles"):
        for r in roles_tag.findall("role"):
            roles.append({"id": r.get("id"), "name": r.get("name")})

    return meta, tasks, resources, allocations, roles


# ============================ 派生计算 ============================
def _to_date(s):
    try:
        return dt.datetime.strptime(s, "%Y-%m-%d").date() if s else None
    except Exception:
        return None


def enrich(tasks, today):
    """补全 start/end(date)、status、逾期天数、资源名。in-place 返回 tasks。"""
    for t in tasks:
        sd = _to_date(t["start"])
        t["start_d"] = sd
        t["end_d"] = (sd + dt.timedelta(days=t["duration"])) if sd else None
        if t["meeting"] or t["duration"] == 0:
            st = "里程碑"
        elif t["complete"] >= 100:
            st = "已完成"
        elif t["end_d"] and t["end_d"] < today:
            st = "已逾期"
        elif sd and sd > today:
            st = "未开始"
        else:
            st = "进行中"
        t["status"] = st
        t["overdue_days"] = ((today - t["end_d"]).days
                             if st == "已逾期" else 0)
    return tasks


def build_resource_index(resources, allocations):
    """task_id -> [resource names]，resource_id -> resource。"""
    r_by_id = {r["id"]: r for r in resources}
    task_res = {}
    for a in allocations:
        nm = r_by_id.get(a["resource_id"], {}).get("name") or ("R#" + str(a["resource_id"]))
        task_res.setdefault(a["task_id"], []).append(nm)
    load_by_res = {}
    for a in allocations:
        r = r_by_id.get(a["resource_id"])
        if not r:
            continue
        load_by_res.setdefault(r["name"], {"tasks": 0, "load": 0.0})
        load_by_res[r["name"]]["tasks"] += 1
        try:
            load_by_res[r["name"]]["load"] += float(a.get("load") or 0)
        except ValueError:
            pass
    return task_res, load_by_res


def phase_progress(tasks):
    """按顶层任务聚合子树的加权完成率（按 duration 加权）。"""
    children_of = {}
    for t in tasks:
        children_of.setdefault(t["parent_id"], []).append(t)
    out = []
    for root in children_of.get(None, []):
        sub = children_of.get(root["id"], [])
        # GanttProject 的父任务 duration 是子任务的跨度汇总，再加会重复加权，
        # 所以阶段完成率只按子任务聚合；无子任务时退化为父任务自身。
        if sub:
            tot_d = sum(s["duration"] for s in sub)
            acc = sum(s["complete"] * s["duration"] for s in sub)
        else:
            tot_d = root["duration"]
            acc = root["complete"] * root["duration"]
        w = (acc / tot_d) if tot_d else root["complete"]
        out.append({
            "name": root["name"],
            "start": root["start_d"],
            "end": root["end_d"],
            "complete": round(w, 1),
            "n_sub": len(sub),
            "status": root["status"],
        })
    return out


# ============================ 输出：横道图 ============================
def draw_gantt(tasks, today, out_png, title="项目横道图"):
    # 仅画有日期的任务，按开始日期排序
    rows = [t for t in tasks if t["start_d"]]
    rows.sort(key=lambda t: (t["start_d"], t["level"]))
    if not rows:
        print("    [WARN] 无可用日期的任务，跳过横道图")
        return False

    fig_h = max(4.0, min(0.42 * len(rows) + 1.5, 42))
    fig, ax = plt.subplots(figsize=(15, fig_h))
    y = list(range(len(rows)))[::-1]  # 顶部为第一个任务
    ym = {id(t): yy for t, yy in zip(rows, y)}

    dmin = min(t["start_d"] for t in rows)
    dmax = max(t["end_d"] for t in rows)
    pad = max((dmax - dmin).days * 0.03, 2)
    span0 = mdates.date2num(dmin - dt.timedelta(days=pad))
    span1 = mdates.date2num(dmax + dt.timedelta(days=pad))

    for t, yy in zip(rows, y):
        s = mdates.date2num(t["start_d"])
        e = mdates.date2num(t["end_d"])
        dur = max(e - s, 0.4)  # 最小可见宽度
        col = t["color"] if t["color"].startswith("#") else "#99ccff"
        if t["meeting"] or t["duration"] == 0:
            ax.scatter([s], [yy], marker="D", s=70, color="#e65100",
                       edgecolor="black", zorder=5)
            continue
        # 未完成段（淡）
        ax.barh(yy, dur, left=s, height=0.55, color=col, alpha=0.30,
                edgecolor=col, linewidth=0.6, zorder=3)
        # 已完成段（实心）
        done = dur * (t["complete"] / 100.0)
        if done > 0:
            ax.barh(yy, done, left=s, height=0.55, color=col, alpha=1.0,
                    edgecolor=col, linewidth=0.6, zorder=4)
        # 标签：层级缩进 + 完成率
        label = ("  " * t["level"] + t["name"]
                 + ("  %d%%" % t["complete"] if not t["meeting"] else " ◆"))
        ax.text(span0 - 0.5, yy, label, va="center", ha="right",
                fontsize=8, color="#222222")

        # 今日线
    if dmin <= today <= dmax:
        td = mdates.date2num(today)
        ax.axvline(td, color="#c62828", linestyle="--", linewidth=1.2, zorder=6)
        ax.text(td, len(rows) - 0.3, L(" 今日", " Today"), color="#c62828",
                fontsize=8, va="bottom")

    ax.set_ylim(-0.7, len(rows) - 0.3)
    ax.set_xlim(span0, span1)
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_minor_locator(mdates.WeekdayLocator(byweekday=mdates.MO))
    ax.grid(axis="x", which="minor", color="#eeeeee", linewidth=0.5)
    ax.grid(axis="x", which="major", color="#cccccc", linewidth=0.7)
    ax.set_yticks([])
    ax.set_title(L("%s（基准日 %s）" % (title, today.isoformat()),
                   "%s (as of %s)" % (title, today.isoformat())), fontsize=12)
    # 图例：颜色仅表示“完成状态/里程碑/今日线”，具体任务颜色各自不同
    leg = [mpatches.Patch(color="#1976d2", alpha=0.3, label=L("未完成段", "Pending")),
           mpatches.Patch(color="#1976d2", alpha=1.0, label=L("已完成段", "Done")),
           mpatches.Patch(color="#e65100", label=L("里程碑 ◆", "Milestone")),
           mpatches.Patch(color="#c62828", label=L("今日线", "Today"))]
    ax.legend(handles=leg, loc="lower right", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=130, bbox_inches="tight")
    plt.close(fig)
    return True


# ============================ 输出：周报 ============================
def render_report(meta, tasks, today, phases, task_res, load_by_res):
    n = len([t for t in tasks if t["duration"] > 0 or not t["meeting"]])
    n_all = len(tasks)
    cnt = {"已完成": 0, "进行中": 0, "已逾期": 0, "未开始": 0, "里程碑": 0}
    for t in tasks:
        cnt[t["status"]] = cnt.get(t["status"], 0) + 1
    done_w = sum(t["complete"] * max(t["duration"], 1) for t in tasks)
    tot_w = sum(max(t["duration"], 1) for t in tasks)
    overall = round(done_w / tot_w, 1) if tot_w else 0

    L = []
    L.append("# 进度周报 · %s" % (meta.get("name") or "未命名项目"))
    L.append("")
    L.append("- 生成时间：%s" % dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    L.append("- 基准日（今日）：%s" % today.isoformat())
    L.append("- 数据源：GanttProject %s" % (meta.get("version") or "未知"))
    L.append("")
    L.append("## 一、项目概览")
    L.append("")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append("| 任务总数 | %d |" % n_all)
    L.append("| 整体完成率（按工期加权） | **%s%%** |" % overall)
    L.append("| 已完成 | %d |" % cnt["已完成"])
    L.append("| 进行中 | %d |" % cnt["进行中"])
    L.append("| 已逾期 | %d |" % cnt["已逾期"])
    L.append("| 未开始 | %d |" % cnt["未开始"])
    L.append("| 里程碑 | %d |" % cnt["里程碑"])
    L.append("")

    L.append("## 二、各阶段进度")
    L.append("")
    L.append("| 阶段 | 起 | 止 | 完成率 | 子任务数 | 状态 |")
    L.append("|---|---|---|---|---|---|")
    for p in phases:
        s = p["start"].isoformat() if p["start"] else "-"
        e = p["end"].isoformat() if p["end"] else "-"
        L.append("| %s | %s | %s | %s%% | %d | %s |" %
                 (p["name"], s, e, p["complete"], p["n_sub"], p["status"]))
    L.append("")

    L.append("## 三、逾期任务清单")
    L.append("")
    overdue = [t for t in tasks if t["status"] == "已逾期"]
    if not overdue:
        L.append("（无逾期任务 🎉）")
    else:
        L.append("| 任务 | 责任资源 | 计划完成 | 实际完成 | 逾期天数 |")
        L.append("|---|---|---|---|---|")
        for t in sorted(overdue, key=lambda x: x["overdue_days"], reverse=True):
            res = "、".join(task_res.get(t["id"], [])) or "-"
            L.append("| %s | %s | %s | %d%% | %d |" %
                     (t["name"], res,
                      t["end_d"].isoformat() if t["end_d"] else "-",
                      t["complete"], t["overdue_days"]))
    L.append("")

    L.append("## 四、近两周待办（基准日起 14 天内启动）")
    L.append("")
    upcoming = [t for t in tasks
                if t["status"] in ("未开始", "进行中")
                and t["start_d"]
                and today <= t["start_d"] <= today + dt.timedelta(days=14)]
    if not upcoming:
        L.append("（近两周无新增启动项）")
    else:
        L.append("| 任务 | 启动 | 工期 | 完成率 | 责任资源 |")
        L.append("|---|---|---|---|---|")
        for t in sorted(upcoming, key=lambda x: x["start_d"]):
            res = "、".join(task_res.get(t["id"], [])) or "-"
            L.append("| %s | %s | %d天 | %d%% | %s |" %
                     (t["name"], t["start_d"].isoformat(), t["duration"],
                      t["complete"], res))
    L.append("")

    L.append("## 五、资源负荷")
    L.append("")
    if load_by_res:
        L.append("| 资源 | 承担任务数 | 累计负荷(%) |")
        L.append("|---|---|---|")
        for nm, v in sorted(load_by_res.items(), key=lambda kv: -kv[1]["load"]):
            L.append("| %s | %d | %.0f |" % (nm, v["tasks"], v["load"]))
    else:
        L.append("（无资源分配）")
    L.append("")

    L.append("## 六、4D 关联提示（扩展点）")
    L.append("")
    L.append("- 每个任务的 `uid` 已导出到 `tasks.csv` / `schedule_summary.json`，"
             "可作为与 Revit 构件 ID 关联的键。")
    L.append("- 下一步（`bim-health-check` / pyRevit）可把 `task.uid` 映射到构件"
             " `ElementId`，实现 4D 推进模拟（本脚本不依赖 Revit，保持 headless）。")
    L.append("")
    return "\n".join(L)


# ============================ 汇总 / 自测 / main ============================
def summarize(tasks, today, phases):
    cnt = {}
    for t in tasks:
        cnt[t["status"]] = cnt.get(t["status"], 0) + 1
    done_w = sum(t["complete"] * max(t["duration"], 1) for t in tasks)
    tot_w = sum(max(t["duration"], 1) for t in tasks)
    overall = round(done_w / tot_w, 1) if tot_w else 0
    print("  任务总数: %d | 整体完成率: %s%%" % (len(tasks), overall))
    print("  状态分布: " + "  ".join("%s=%d" % (k, cnt.get(k, 0))
                                     for k in ["已完成", "进行中", "已逾期", "未开始", "里程碑"]))
    print("  阶段数: %d" % len(phases))
    for p in phases:
        print("    - %-22s 完成率 %5s%%  %s ~ %s" % (
            p["name"], p["complete"],
            p["start"].isoformat() if p["start"] else "-",
            p["end"].isoformat() if p["end"] else "-"))


def selftest():
    print("=== selftest：临时构造最小 .gan，跑解析→横道图→周报（无 GanttProject GUI） ===")
    sample = '''<?xml version="1.0" encoding="UTF-8"?>
<project name="自测项目" view-date="2024-06-01" version="3.3.3316" locale="zh">
  <tasks empty-milestones="true">
    <task id="0" uid="u0" name="设计" color="#99ccff" start="2024-06-01" duration="20" complete="80">
      <task id="1" uid="u1" name="方案" color="#99ccff" start="2024-06-01" duration="10" complete="100">
        <depend id="2" type="2" difference="0" hardness="Strong"/>
        <customproperty taskproperty-id="tpc0" value="true"/>
      </task>
      <task id="2" uid="u2" name="施工图" color="#ff0066" start="2024-06-15" duration="10" complete="60"/>
    </task>
    <task id="3" uid="u3" name="开工" color="#000000" meeting="true" start="2024-07-01" duration="0" complete="0"/>
  </tasks>
  <resources>
    <resource id="1" name="张三"><rate name="standard" value="100"/></resource>
    <resource id="2" name="李四"><rate name="standard" value="80"/></resource>
  </resources>
  <allocations>
    <allocation task-id="1" resource-id="1" function="0" responsible="true" load="100.0"/>
    <allocation task-id="2" resource-id="2" function="0" responsible="true" load="100.0"/>
  </allocations>
  <roles><role id="0" name=" architect"/></roles>
</project>'''
    fd, tmp = tempfile.mkstemp(suffix=".gan")
    os.write(fd, sample.encode("utf-8"))
    os.close(fd)
    try:
        out_dir = tempfile.mkdtemp(prefix="gan_st_")
        meta, tasks, resources, allocations, roles = parse_gan(tmp)
        today = _to_date(meta["view_date"]) or dt.date.today()
        enrich(tasks, today)
        task_res, load_by_res = build_resource_index(resources, allocations)
        phases = phase_progress(tasks)

        # 校验解析正确性
        checks = []
        checks.append(("任务数=4", len(tasks) == 4))
        checks.append(("资源数=2", len(resources) == 2))
        checks.append(("分配数=2", len(allocations) == 2))
        u2 = next(t for t in tasks if t["id"] == "2")
        checks.append(("施工图结束日=2024-06-25",
                       u2["end_d"] == dt.date(2024, 6, 25)))
        # 施工图 2024-06-15 启动，晚于基准日 2024-06-01 → 应为 未开始
        checks.append(("施工图状态=未开始", u2["status"] == "未开始"))
        m = next(t for t in tasks if t["id"] == "3")
        checks.append(("开工=里程碑", m["status"] == "里程碑"))

        # 真正写出产物（验证绘图/渲染代码路径）
        png = os.path.join(out_dir, "gantt_chart.png")
        md = os.path.join(out_dir, "schedule_report.md")
        csvp = os.path.join(out_dir, "tasks.csv")
        jp = os.path.join(out_dir, "schedule_summary.json")
        ok_png = draw_gantt(tasks, today, png, title=meta["name"])
        rep = render_report(meta, tasks, today, phases, task_res, load_by_res)
        with open(md, "w", encoding="utf-8") as f:
            f.write(rep)
        # CSV
        cols = ["id", "uid", "name", "level", "parent_id", "start", "end",
                "duration", "complete", "status", "meeting", "overdue_days",
                "deps", "resources"]
        with open(csvp, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(cols)
            for t in tasks:
                w.writerow([t["id"], t["uid"], t["name"], t["level"], t["parent_id"],
                            t["start_d"], t["end_d"], t["duration"], t["complete"],
                            t["status"], t["meeting"], t["overdue_days"],
                            ";".join(d["pred_id"] or "" for d in t["deps"]),
                            ";".join(task_res.get(t["id"], []))])
        summary = {"meta": meta, "tasks": tasks, "resources": resources,
                   "allocations": allocations, "roles": roles}
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, default=str, indent=2)

        checks.append(("PNG 生成", ok_png and os.path.getsize(png) > 1000))
        checks.append(("MD 生成", os.path.getsize(md) > 200))
        checks.append(("CSV 生成", os.path.getsize(csvp) > 100))
        checks.append(("JSON 生成", os.path.getsize(jp) > 100))

        ok = all(v for _, v in checks)
        print("  自测校验：")
        for name, v in checks:
            print("    [%s] %s" % ("PASS" if v else "FAIL", name))
        print("[SELFTEST]", "PASS" if ok else "FAIL")
        return ok
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="阶段5 进度计划自动化（.gan→横道图+周报）")
    ap.add_argument("gan", nargs="?", help="GanttProject .gan 文件路径")
    ap.add_argument("--today", help="基准日 YYYY-MM-DD（默认用 .gan 的 view-date）")
    ap.add_argument("--out", help="输出目录（默认 <gan同级>/schedule_out）")
    ap.add_argument("--apply", action="store_true",
                    help="真正写出 PNG/MD/CSV/JSON（默认 dry-run 仅打印概览）")
    ap.add_argument("--no-chart", action="store_true", help="不画横道图")
    ap.add_argument("--selftest", action="store_true", help="headless 自测")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    if not args.gan or not os.path.isfile(args.gan):
        sys.exit("[FAIL] 请给一个存在的 .gan 文件，或加 --selftest")

    meta, tasks, resources, allocations, roles = parse_gan(args.gan)
    today = (_to_date(args.today) or _to_date(meta["view_date"])
             or dt.date.today())
    enrich(tasks, today)
    task_res, load_by_res = build_resource_index(resources, allocations)
    phases = phase_progress(tasks)

    print("\n>>> 解析：%s" % args.gan)
    print("    资源 %d | 分配 %d | 角色 %d" %
          (len(resources), len(allocations), len(roles)))
    summarize(tasks, today, phases)

    if not args.apply:
        print("\n[dry-run] 仅打印概览，原 .gan 未改动。"
              "加 --apply 生成 横道图/周报/CSV/JSON。")
        return

    out_dir = args.out or os.path.join(os.path.dirname(args.gan) or ".",
                                       "schedule_out")
    os.makedirs(out_dir, exist_ok=True)

    png = os.path.join(out_dir, "gantt_chart.png")
    md = os.path.join(out_dir, "schedule_report.md")
    csvp = os.path.join(out_dir, "tasks.csv")
    jp = os.path.join(out_dir, "schedule_summary.json")

    if not args.no_chart:
        draw_gantt(tasks, today, png, title=meta.get("name") or "项目横道图")
        print("[OUT] 横道图  -> %s" % png)
    rep = render_report(meta, tasks, today, phases, task_res, load_by_res)
    with open(md, "w", encoding="utf-8") as f:
        f.write(rep)
    print("[OUT] 周报    -> %s" % md)

    cols = ["id", "uid", "name", "level", "parent_id", "start", "end",
            "duration", "complete", "status", "meeting", "overdue_days",
            "deps", "resources"]
    with open(csvp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for t in tasks:
            w.writerow([t["id"], t["uid"], t["name"], t["level"], t["parent_id"],
                        t["start_d"], t["end_d"], t["duration"], t["complete"],
                        t["status"], t["meeting"], t["overdue_days"],
                        ";".join(d["pred_id"] or "" for d in t["deps"]),
                        ";".join(task_res.get(t["id"], []))])
    print("[OUT] 任务表  -> %s" % csvp)

    summary = {"meta": meta, "tasks": tasks, "resources": resources,
               "allocations": allocations, "roles": roles}
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, default=str, indent=2)
    print("[OUT] 结构化  -> %s" % jp)
    print("\n完成。原 .gan 未改动；产物均在 %s" % out_dir)


if __name__ == "__main__":
    main()
