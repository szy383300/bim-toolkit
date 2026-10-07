# -*- coding: utf-8 -*-
u"""
bimhealth.py —— BIM 体检共用函数（阶段 4）。

封装三类检查 + 健康分：
  1) scan_warnings   警告/错误扫描（doc.GetWarnings()）
  2) scan_families   族体检（族数/类型数/孤儿族/共享族/可编辑族）
  3) scan_parameters 参数化体检（族类型参数化深度、实例「标记」缺失率）
  4) health_score    综合健康分 0~100 + 等级

与 一键统计 天然衔接：scan_warnings/doc.GetWarnings() 复用了 一键统计里的
「当前警告数」口径，体检在此之上展开深度分析。

只在 Revit + pyRevit 宿主内真正运行；headless 下仅做语法/符号校验，
不会被执行。所有 Revit API 调用包裹在 try/except，避免单点异常拖垮整条命令。
"""
from collections import Counter

try:
    from pyrevit import revit, DB, UI
except Exception:
    # headless 校验环境（py_compile / API 符号检查）下 pyrevit 不存在，置空即可；
    # 桥/嵌入式上下文（pyRevit 命令清理后 pyrevit 包不可导入）回退到直接引用
    # RevitAPI（model_builder 同款路径，桥内实测可用）。
    revit = None
    UI = None
    try:
        import clr
        clr.AddReference("RevitAPI")
        clr.AddReference("RevitAPIUI")
        from Autodesk.Revit import DB, UI
    except Exception:
        DB = None
        UI = None

# IronPython 2.7 兼容：FilteredElementCollector 没有 .Count 属性，
# .ToElements() 返回 .NET IList，Python 3 的 list() 写法在 IronPython 下不稳。
# 复用 bimlib 的 to_element_list（lib 目录在 pyRevit 宿主内自动入 sys.path）。
try:
    from bimlib import to_element_list, type_name
except Exception:
    # headless 兜底：不会被真正执行，仅保证模块可导入
    def to_element_list(collector):
        try:
            return list(collector.ToElements())
        except Exception:
            return list(collector)


# --------------------------------------------------------------------------- #
# 1) 警告 / 错误扫描
# --------------------------------------------------------------------------- #
def scan_warnings(doc):
    u"""扫描文档警告与错误。

    返回 dict:
        total     : 警告+错误总数
        errors    : 错误数（FailureSeverity.Error）
        warnings  : 警告数
        top       : [(描述, 次数, 'Error'|'Warning'), ...] 按频次 top15
        affected  : set(受影响构件 ElementId.IntegerValue)
    """
    result = {"total": 0, "errors": 0, "warnings": 0, "top": [], "affected": set(), "error": None}
    try:
        msgs = doc.GetWarnings()  # FailureMessageArray，直接迭代即可
    except Exception as _e:
        result["error"] = u"读取文档警告失败：%s" % _e
        return result

    desc_counter = Counter()
    sev_map = {}
    affected = set()
    for fm in msgs:
        try:
            desc = fm.GetDescriptionText() or u"(无描述)"
        except Exception:
            desc = u"(无描述)"
        try:
            sev = fm.GetSeverity()  # FailureSeverity
            is_err = (sev == DB.FailureSeverity.Error)
        except Exception:
            sev, is_err = None, False
        sev_str = "Error" if is_err else "Warning"
        desc_counter[desc] += 1
        sev_map[desc] = sev_str
        result["errors" if is_err else "warnings"] += 1
        try:
            for eid in fm.GetFailingElements():
                affected.add(eid.IntegerValue)
        except Exception:
            pass

    result["total"] = len(msgs)
    result["affected"] = affected
    result["top"] = [(d, c, sev_map[d]) for d, c in desc_counter.most_common(15)]
    return result


# --------------------------------------------------------------------------- #
# 2) 族体检
# --------------------------------------------------------------------------- #
def scan_families(doc):
    u"""族体检。

    返回 dict:
        families         : 已载入族总数
        user_families    : 用户族（IsUserCreated）
        system_families  : 系统族
        symbols          : 族类型(FamilySymbol)总数
        orphan_families  : [族名, ...] 没有任何类型的孤儿族
        shared_families  : 共享族数（FAMILY_SHARED=1）
        editable_families: 可编辑族数（IsEditable）
    """
    result = {
        "families": 0, "user_families": 0, "system_families": 0,
        "symbols": 0, "orphan_families": [], "shared_families": 0,
        "editable_families": 0, "error": None,
    }
    try:
        fams = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.Family))
    except Exception as _e:
        result["error"] = u"收集族失败：%s" % _e
        return result

    result["families"] = len(fams)
    for fam in fams:
        try:
            symbol_ids = list(fam.GetFamilySymbolIds())
            n_sym = len(symbol_ids)
        except Exception:
            n_sym = 0
        if n_sym == 0:
            try:
                result["orphan_families"].append(fam.Name)
            except Exception:
                result["orphan_families"].append(u"(未命名)")
        try:
            if fam.IsUserCreated:
                result["user_families"] += 1
            else:
                result["system_families"] += 1
        except Exception:
            pass
        try:
            if fam.IsEditable:
                result["editable_families"] += 1
        except Exception:
            pass
        try:
            sp = fam.get_Parameter(DB.BuiltInParameter.FAMILY_SHARED)
            if sp is not None and sp.AsInteger() == 1:
                result["shared_families"] += 1
        except Exception:
            pass

    try:
        syms = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol))
        result["symbols"] = len(syms)
    except Exception:
        pass
    return result


# --------------------------------------------------------------------------- #
# 3) 参数化体检
# --------------------------------------------------------------------------- #
def scan_parameters(doc, all_model_elements):
    u"""参数化体检。

    all_model_elements: bimlib 提供的「全部非类型构件」收集器（注入以避免循环依赖）。

    返回 dict:
        symbols_checked : 参与统计的族类型数
        min_params_type : (类型名, 参数个数) 参数最少的族类型
        avg_type_params : 族类型平均参数个数
        missing_mark    : 「标记(Mark)」为空的实例数
        total_checked   : 参与标记检查的实例总数
    """
    result = {
        "symbols_checked": 0, "min_params_type": ("", 10 ** 9),
        "avg_type_params": 0.0, "missing_mark": 0, "total_checked": 0,
        "error": None,
    }
    try:
        # 族类型参数化深度
        try:
            syms = to_element_list(DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol))
        except Exception:
            syms = []
        counts = []
        min_name, min_c = "", 10 ** 9
        for sym in syms:
            try:
                c = len(list(sym.Parameters))
            except Exception:
                c = 0
            counts.append(c)
            if c < min_c:
                min_c = c
                try:
                    min_name = type_name(sym)
                except Exception:
                    min_name = "?"
        result["symbols_checked"] = len(syms)
        if counts:
            result["avg_type_params"] = round(sum(counts) / len(counts), 1)
        if min_c != 10 ** 9:
            result["min_params_type"] = (min_name, min_c)

        # 实例「标记」缺失检查
        miss = 0
        els = []
        try:
            els = to_element_list(all_model_elements(doc))
        except Exception:
            els = []
        for el in els:
            try:
                p = el.get_Parameter(DB.BuiltInParameter.ALL_MODEL_MARK)
                if p is None:
                    continue
                val = p.AsString()
                if val is None or val.strip() == "":
                    miss += 1
            except Exception:
                pass
        result["missing_mark"] = miss
        result["total_checked"] = len(els)
    except Exception as _e:
        result["error"] = u"参数化体检执行异常：%s" % _e
    return result


# --------------------------------------------------------------------------- #
# 4) 综合健康分
# --------------------------------------------------------------------------- #
# 健康分扣分权重与上限（集中在此便于调参，改这里即可不必动逻辑）
HEALTH_WEIGHTS = {
    "warn_density": 3.0,   # 每 1% 警告密度扣的分
    "error": 5.0,          # 每个硬性错误扣的分
    "orphan": 2.0,         # 每个孤儿族扣的分
    "missing_mark": 0.5,   # 每个漏标实例扣的分
}
HEALTH_CAPS = {
    "warn_density": 30.0,  # 警告密度扣分上限
    "error": 30.0,         # 错误扣分上限
    "orphan": 10.0,        # 孤儿族扣分上限
    "missing_mark": 10.0,  # 漏标扣分上限
}


def health_score(total_elements, warn, err, orphan_count, missing_mark):
    u"""综合健康分（0~100）。启发式扣分，封顶不倒扣。

    权重与上限见模块顶部 HEALTH_WEIGHTS / HEALTH_CAPS。
    """
    score = 100.0
    density = (warn / total_elements * 100.0) if total_elements > 0 else 0.0
    score -= min(HEALTH_CAPS["warn_density"], density * HEALTH_WEIGHTS["warn_density"])
    score -= min(HEALTH_CAPS["error"], err * HEALTH_WEIGHTS["error"])
    score -= min(HEALTH_CAPS["orphan"], orphan_count * HEALTH_WEIGHTS["orphan"])
    score -= min(HEALTH_CAPS["missing_mark"], missing_mark * HEALTH_WEIGHTS["missing_mark"])
    score = max(0, round(score))
    return score


def health_grade(score):
    u"""分数 → 等级文字。"""
    if score >= 90:
        return u"A（优秀）"
    if score >= 75:
        return u"B（良好）"
    if score >= 60:
        return u"C（一般）"
    return u"D（需整改）"
