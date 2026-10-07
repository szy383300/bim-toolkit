# -*- coding: utf-8 -*-
u"""schedule_to_steps.py —— 把进度计划（schedule_summary.csv）转成 MCP Bridge 的
「导览任务树」JSON，供 4D 播放使用。

这是 S15 的核心增量：**播放本身桥里已经有了**（`list_steps` / `apply_step` /
`export_view` 三件套），缺的只是"把施工进度翻译成步骤"这一步。

链路
----
    schedule_summary.csv
        │  本模块（进度关联 按钮 或 命令行）
        ▼
    <计划名>_4d_steps.json        ← 桥认识的导览任务树
        │  mcp/play_4d.py（或任意 MCP 客户端）
        ▼
    frames/step_01.jpg … step_NN.jpg   ← 4D 动画帧

步骤树格式（与 bridge_core.do_list_steps / do_apply_step 对齐）
--------------------------------------------------------------
    {"task": 标题, "model": ..., "note": ..., "steps": [
        {"n": 1, "title": ..., "show_all": true, "desc": ...},
        {"n": 2, "title": ..., "levels": ["1F"], "desc": ...},
    ]}

约定（沿用仓库里既有的 guide_tasktree53.json 惯例）：
  * **第 1 步固定是「全楼总览」**（show_all）
  * **最后一步固定是「竣工全貌」**（show_all）
  * 中间每步给 `levels`，桥会隔离这些标高的构件并取景

已知边界
--------
`apply_step` 只能按**标高**隔离。计划里 `楼层` 为空的任务（模板里就有
「全楼通用墙」这种）**无法按楼层隔离** —— 这类会退化为 show_all，
并在 `desc` 里注明、同时在返回值里给 warning。**这是能力边界，不是 bug。**
"""
import re

# 计划表列名（与 进度关联 按钮、schedule_summary.csv 模板一致）
C_UID = u"uid"
C_NAME = u"名称"
C_LEVEL = u"楼层"
C_CAT = u"类别"
C_TYPE = u"类型"
C_START = u"计划开始"
C_END = u"计划完成"
C_STATUS = u"状态"

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_MONTH_RE = re.compile(r"(\d{4})-(\d{1,2})")

DEFAULT_MAX_STEPS = 30


def _s(row, key):
    v = row.get(key)
    if v is None:
        return u""
    try:
        return u"%s" % v
    except Exception:
        return u""


def parse_date(s):
    u"""取 YYYY-MM-DD；取不到返回空串（排序时排最后）。"""
    m = _DATE_RE.search(s or u"")
    if not m:
        return u""
    return u"%s-%s-%s" % (m.group(1), m.group(2), m.group(3))


def month_of(s):
    u"""取 YYYY-MM；取不到返回 u"未排期"。"""
    d = parse_date(s)
    if d:
        return d[:7]
    m = _MONTH_RE.search(s or u"")
    if m:
        try:
            return u"%s-%02d" % (m.group(1), int(m.group(2)))
        except Exception:
            return u"%s-%s" % (m.group(1), m.group(2))
    return u"未排期"


def normalize_tasks(rows):
    u"""把 CSV 行规整成任务列表，并**按计划开始排序**（无日期的排最后，其次按 uid）。"""
    tasks = []
    for r in (rows or []):
        uid = _s(r, C_UID).strip()
        name = _s(r, C_NAME).strip()
        if not uid and not name:
            continue
        tasks.append({
            "uid": uid,
            "name": name or uid,
            "level": _s(r, C_LEVEL).strip(),
            "category": _s(r, C_CAT).strip(),
            "type": _s(r, C_TYPE).strip(),
            "start": parse_date(_s(r, C_START)),
            "end": parse_date(_s(r, C_END)),
            "status": _s(r, C_STATUS).strip(),
            "start_raw": _s(r, C_START).strip(),
        })

    def key(t):
        return (t["start"] == u"", t["start"], t["uid"])

    tasks.sort(key=key)
    return tasks


def _bucket_by_month(tasks):
    u"""按开始月份分组，保持时间顺序。"""
    buckets = []
    cur = None
    for t in tasks:
        m = month_of(t["start_raw"] or t["start"])
        if cur is None or cur["month"] != m:
            cur = {"month": m, "tasks": []}
            buckets.append(cur)
        cur["tasks"].append(t)
    return buckets


def _step_from_tasks(n, tasks, matched_counts=None, forced_show_all=False):
    u"""把一组任务合成一个步骤。"""
    levels = []
    for t in tasks:
        if t["level"] and t["level"] not in levels:
            levels.append(t["level"])

    no_level = [t for t in tasks if not t["level"]]
    show_all = forced_show_all or (not levels)

    if len(tasks) == 1:
        t = tasks[0]
        title = t["name"]
    else:
        title = u"%s（%d 个任务）" % (tasks[0]["uid"] or u"多项", len(tasks))

    # 日期范围
    starts = [t["start"] for t in tasks if t["start"]]
    ends = [t["end"] for t in tasks if t["end"]]
    span = u""
    if starts or ends:
        span = u"%s ~ %s" % (min(starts) if starts else u"?",
                             max(ends) if ends else u"?")

    lines = []
    if span:
        lines.append(u"计划：%s" % span)
    for t in tasks[:12]:
        cnt = u""
        if matched_counts is not None and t["uid"] in matched_counts:
            cnt = u"（模型匹配 %d 个构件）" % matched_counts[t["uid"]]
        cat = (u"·" + t["category"]) if t["category"] else u""
        lv = (u"·" + t["level"]) if t["level"] else u"·(全楼)"
        st = (u"·" + t["status"]) if t["status"] else u""
        lines.append(u"- %s %s%s%s%s" % (t["uid"], t["name"], cat, lv, st + cnt))
    if len(tasks) > 12:
        lines.append(u"…另有 %d 个任务" % (len(tasks) - 12))
    if show_all and not forced_show_all:
        lines.append(u"⚠️ 本步任务未指定楼层（或全为全楼通用），无法按楼层隔离，"
                     u"已退化为全楼显示。")
    if no_level and levels:
        lines.append(u"⚠️ 其中 %d 个任务未指定楼层，实际显示范围比其覆盖范围大。"
                     % len(no_level))

    step = {"n": n, "title": title, "desc": u"\n".join(lines)}
    if show_all:
        step["show_all"] = True
        if not forced_show_all:
            # 让步骤列表一眼看出"这步为什么是全楼" —— 否则 4D 动画里
            # 会突兀地跳回全貌，看的人只会以为程序坏了。
            step["title"] = u"（全楼）" + title
    else:
        step["levels"] = levels
    return step


def build_task_tree(rows, matched_counts=None, group=u"auto",
                    max_steps=DEFAULT_MAX_STEPS, task_name=u"4D 施工导览",
                    model_path=u"", status_filter=None):
    u"""把进度计划行转成导览任务树。

    rows          : read_csv_unicode() 读出的行（list of dict）
    matched_counts: {uid: 匹配构件数}，可选，仅用于 desc
    group         : "task" 每任务一步 / "month" 按开始月合步 / "auto" 超阈值则按月
    max_steps     : auto 模式下超过它（含首尾两步）就改用按月合步
    status_filter : 只保留这些状态的任务（如 [u"未开始", u"进行中"]）；None = 全部

    返回 (tree, warnings)。
    """
    warnings = []
    tasks = normalize_tasks(rows)

    if status_filter:
        want = set(status_filter)
        before = len(tasks)
        tasks = [t for t in tasks if t["status"] in want]
        if before != len(tasks):
            warnings.append(u"按状态过滤：%d -> %d 个任务" % (before, len(tasks)))

    if not tasks:
        return None, [u"没有可用的任务（检查 schedule_summary.csv 是否有内容/是否被状态过滤掉）"]

    no_level = [t for t in tasks if not t["level"]]
    if no_level:
        warnings.append(
            u"%d 个任务没写「楼层」，无法按楼层隔离，相关步骤会退化为全楼显示：%s"
            % (len(no_level), u"、".join([t["uid"] or t["name"] for t in no_level[:5]])))

    # 决定分组方式
    if group == u"auto":
        # 首尾各 1 步 + 中间若干；留一点余量
        group = u"task" if len(tasks) <= (max_steps - 2) else u"month"

    if group == u"task":
        groups = [{"month": month_of(t["start_raw"] or t["start"]),
                   "tasks": [t]} for t in tasks]
    elif group == u"month":
        groups = _bucket_by_month(tasks)
    else:
        return None, [u"未知的 group=%s（只认 task/month/auto）" % group]

    if len(groups) > (max_steps - 2):
        warnings.append(u"步骤数 %d 超过上限 %d，已合并为按月分步（每步首行仍列明细）"
                        % (len(groups), max_steps))
        groups = _bucket_by_month(tasks)
        if len(groups) > (max_steps - 2):
            warnings.append(u"按月仍然 %d 步，超过上限 %d —— 建议缩小时间范围 "
                            u"或用 status_filter 只保留在施任务"
                            % (len(groups), max_steps))

    steps = []
    first = [t for t in tasks]
    steps.append({
        "n": 1,
        "title": u"全楼总览",
        "show_all": True,
        "desc": u"%s：共 %d 个任务，时间跨度 %s ~ %s。"
                % (task_name, len(tasks),
                   (tasks[0]["start"] or u"?"),
                   (max([t["end"] for t in tasks if t["end"]] or [u"?"]))),
    })

    n = 2
    for g in groups:
        title_prefix = u""
        if group == u"month" and g["month"]:
            title_prefix = g["month"] + u" "
        st = _step_from_tasks(n, g["tasks"], matched_counts)
        if title_prefix and not st["title"].startswith(title_prefix):
            st["title"] = title_prefix + st["title"]
        st["n"] = n
        steps.append(st)
        n += 1

    steps.append({
        "n": n,
        "title": u"竣工全貌",
        "show_all": True,
        "desc": u"导览结束，恢复全楼显示。共 %d 个任务。" % len(tasks),
    })

    tree = {
        "task": task_name,
        "model": model_path or u"",
        "version": u"4d-v1",
        "note": (u"由 schedule_to_steps.py 从进度计划生成；"
                 u"步骤结构对齐 bridge_core.do_apply_step（levels 隔离 + 取景）"),
        "steps": steps,
    }
    return tree, warnings
