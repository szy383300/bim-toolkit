# -*- coding: utf-8 -*-
# pylint: skip-file
"""
MCP Bridge v7: Singleton non-blocking TCP server inside Revit
(pyRevit / IronPython 2.7, Revit 2019).

P0 upgrade (commercialization):
  - token auth (bridge.token, auto-generated, required on connect)
  - newline-delimited JSON framing with request id echo (persistent conn)
  - atomic tools (create_walls / set_parameters / batch_count / run_report)
  - execute_code disabled unless mcp_bridge_config.json allows it
  - audit log (mcp_audit.jsonl)

Protocol (TCP 127.0.0.1:9877, one JSON object per line, UTF-8):
  C: {"type":"auth","token":"<hex>"}\n
  S: {"ok":true,"server":"mcp-bridge","version":"7.0"}\n
  C: {"id":1,"type":"get_info"}\n
  S: {"id":1,"result":{...}}\n     or {"id":1,"error":"..."}\n

Architecture:
  - v7.7: P0 tools borrowed from open-source revit-mcp (458-star) analysis:
    create_grids / create_levels (batch write, ExternalEvent-routed,
    commit-status checked), query_elements (read-only filter with
    optional parameter projection), get_quantities (per-category
    count/area/volume + cost estimate, reuses lib/cost_estimator).
  - v7.6: P1 commands - get_changes (DocumentChanged incremental
    feed with seq watermark), get_selection / set_selection (selection
    sync), dwg_to_model (model_plan.json -> elements via the DWG翻模
    lib chain; caller-managed transactions like execute_code).
  - v7.5: execute_code supports no_transaction mode (caller-managed
    Transaction, for lib functions like model_builder.build_model that
    open their own - Revit forbids nested Transactions) and returns
    the executed namespace's _result; ns gains to_text and BRIDGE_DIR.
  - v7.4: WRITE commands (create_walls / set_parameters / execute_code)
    are queued and executed via ExternalEvent + IExternalEventHandler.
    Revit forbids starting a Transaction outside API context - the timer
    tick is on the UI thread but is NOT an API context ("Starting a
    transaction from an external application running outside of API
    context is not allowed", verified 2026-09-10). Read-only commands
    still run directly in the tick. ExternalEvent.Create() is called in
    start() because the button click IS a valid API context.
  - v7.3: ALL logic lives in this imported MODULE (bridge_core.py). pyRevit
    clears the command script's module globals after each run (verified
    2026-09-10: journal showed UnboundNameException "name 'log' is not
    defined" from every tick), so a thin script.py launcher imports this
    module and calls start(). Imported-module globals are NOT cleared
    (pyrevitlib itself survives this way), so the tick handler keeps
    working after the command exits.
  - v7.2: a WinForms Timer (100 ms, WM_TIMER on the Revit UI thread) drives
    the socket pump. UIApplication.Idling was ABANDONED: Revit only raises
    Idling in response to real user input on a focused Revit, so an
    unattended bridge would never tick (verified 3x on 2026-09-10).
    The timer fires regardless of focus/input, even during modal dialogs.
  - EXCEPTION ARMOR: the tick body is fully wrapped; no exception may ever
    escape into the WinForms message loop (an escaped UnboundNameException
    killed a Revit session on 2026-09-10 via the .NET unhandled dialog).
  - Singleton via .NET AppDomain; superseded pumps auto-retire
  - all I/O and Revit API on the UI thread (Revit requirement)

IronPython 2.7 notes:
  - json raises ValueError (no json.JSONDecodeError)
  - non-blocking recv raises socket.error WSAEWOULDBLOCK(10035) when no
    data yet -> must NOT be treated as a dead client
"""

import os
import sys
import json
import math
import socket
import traceback
import time

import clr
from System import EventHandler, AppDomain, Guid
clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import Timer
from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, Level, View, ViewType,
    Family, FamilySymbol, ElementId, Transaction, Category,
    CategoryType, BuiltInParameter, Grid,
    Wall, WallType, WallKind, XYZ, Line,
    FloorType, RoofType, CurveLoop, Curve, CurveArray,
    TemporaryViewMode, ImageExportOptions, ExportRange
)
# NOTE: CompoundStructureLayerFunction does NOT exist in Revit 2019
# (2019 uses MaterialFunctionAssignment). Compare layer function by NAME.
from Autodesk.Revit.DB.Structure import StructuralType
from Autodesk.Revit.UI import TaskDialog, IExternalEventHandler, ExternalEvent
from Autodesk.Revit.DB.Events import DocumentChangedEventArgs
from System.Collections.Generic import List as DotNetList

# v7.7.3: failure suppression for MCP write tools. Forensics
# 2026-09-11 18:17: do_create_walls had NO preprocessor; overlapping
# walls triggered commit warnings -> Revit MODAL DIALOG -> Execute
# blocked on the UI thread forever ("ext-event executed" never
# logged, client timed out). Same class of bug as the v7.5
# model_builder saga. Pattern copied from lib/bimconv_build.py.
try:
    from Autodesk.Revit.DB import (
        IFailuresPreprocessor, FailureProcessingResult, FailureSeverity,
        FailureMessageAccessor)
    _HAVE_FAILURES = True
except Exception:
    _HAVE_FAILURES = False

_MCP_LAST_FAILURES = []
# v7.9.1: 预处理器重试上限。教训（2026-09-12 07:39）：ResolveFailures
# 传错类型 -> 错误永远无法解决 -> Revit 逐轮重试 6 分钟才强制回滚，
# 整批实例丢失且桥端超时。上限 3 轮，超限 ProceedWithRollBack 快速失败。
_SUPPRESSOR_PASSES = [0]


class _MCPFailSuppressor(IFailuresPreprocessor):
    u"""Delete warnings, resolve errors by deleting the failing
    elements, record failure texts. 2019 fact (reflected on
    RevitAPI.dll 2026-09-11): FailureMessageAccessor has NO
    SetDefaultResolution - that call always threw AttributeError and
    errors stayed unresolved -> silent Commit rollback (this exact
    bug also explains the bimconv 05.dwg batch-rollback mystery).
    The real API is FailuresAccessor.ResolveFailures(IList[ElementId])."""

    def PreprocessFailures(self, failuresAccessor):
        # v7.7.5: failure texts are LOGGED immediately (the retry loop
        # invokes this pass after pass; _MCP_LAST_FAILURES is cleared
        # per-command instead of per-pass so telemetry survives).
        log("preprocessor: enter (pass %d)" % (_SUPPRESSOR_PASSES[0] + 1))
        _SUPPRESSOR_PASSES[0] += 1
        try:
            msgs = list(failuresAccessor.GetFailureMessages())
        except Exception:
            msgs = []
        # v7.9.1 修复：ResolveFailures 的 2019 真实签名是
        # IList[FailureMessageAccessor]（运行时实锤：
        # "expected IList[FailureMessageAccessor], got List[ElementId]"）。
        # 旧代码传 ElementId 列表必抛 -> 错误永不解决 -> 无限重试。
        doomed_msgs = DotNetList[FailureMessageAccessor]()
        for fm in msgs:
            try:
                sev = to_text(fm.GetSeverity())
                txt = to_text(fm.GetDescriptionText())
                _MCP_LAST_FAILURES.append(u"%s|%s" % (sev, txt))
                log("failure[%d posted]: %s | %s" % (
                    len(_MCP_LAST_FAILURES), sev, txt))
            except Exception:
                pass
            try:
                if fm.GetSeverity() == FailureSeverity.Error:
                    doomed_msgs.Add(fm)
            except Exception:
                pass
        try:
            failuresAccessor.DeleteAllWarnings()
        except Exception:
            pass
        if doomed_msgs.Count > 0:
            log("preprocessor: resolving {} error message(s)".format(
                doomed_msgs.Count))
            try:
                # 应用每条错误的默认解决方式（"不能从墙外剪切"的默认
                # 解决即删除该实例），错误的实例被剔除、其余照常提交
                failuresAccessor.ResolveFailures(doomed_msgs)
            except Exception as ex:
                log("ResolveFailures failed: {}".format(to_text(ex)))
        log("preprocessor: exit ({} message(s))".format(len(msgs)))
        if _SUPPRESSOR_PASSES[0] >= 3:
            # 有界退出：连续 3 轮仍有未解决错误 -> 整体回滚，绝不恋战
            log("preprocessor: pass cap reached -> ProceedWithRollBack")
            return FailureProcessingResult.ProceedWithRollBack
        if len(msgs) == 0:
            # v7.7.7: ProceedWithCommit CLAIMS "all failures resolved" -
            # returning it on a zero-message pass contradicts Revit's
            # own bookkeeping and triggers the retry-then-rollback loop
            # (observed 6x on 2026-09-11). Continue = no opinion.
            return FailureProcessingResult.Continue
        return FailureProcessingResult.ProceedWithCommit


_MCP_SUPPRESSOR = None  # v7.7.6: module-level singleton - keep ALIVE
# v7.12.3: EditScope 提交用的「不抑制」预处理器实例（同上，须保活）
_COMMIT_PROC = [None]


def _install_failure_preprocessor(t):
    u"""Attach the suppressor to a Transaction (2019-safe, degrades).
    v7.7.6: the instance is a module-level SINGLETON - an inline
    temporary can be GC'd while only held on the .NET side, and a
    dead IPY preprocessor makes Revit fail the commit silently
    (no PreprocessFailures dispatch, immediate RolledBack)."""
    global _MCP_SUPPRESSOR
    if not _HAVE_FAILURES:
        return
    try:
        _SUPPRESSOR_PASSES[0] = 0  # v7.9.1: 每个新事务重置重试计数
        if _MCP_SUPPRESSOR is None:
            _MCP_SUPPRESSOR = _MCPFailSuppressor()
        opts = t.GetFailureHandlingOptions()
        opts.SetFailuresPreprocessor(_MCP_SUPPRESSOR)
        t.SetFailureHandlingOptions(opts)
    except Exception as ex:
        log("install preprocessor failed: {}".format(to_text(ex)))

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "mcp_bridge.log")
TOKEN_PATH = os.path.join(HERE, "bridge.token")
AUDIT_PATH = os.path.join(HERE, "mcp_audit.jsonl")
CRASH_PATH = os.path.join(HERE, "mcp_crash.log")
CONFIG_PATH = os.path.join(HERE, "mcp_bridge_config.json")

# 版本号只在**行为发生变化**时递增。它的唯一用途是：`ping` 会回报它，
# 用来判断"Revit 里正跑着的这个桥，是不是仓库里最新的那份"。
#
# 2026-10-01 修正：此前它写 "7.11.0"，而代码里已经积累了 v7.12.0 / 7.12.1 /
# 7.12.2 / 7.12.3 / 7.12.4 五轮修复（见下方各处 v7.12.x 注释，多数是
# 2026-09-28 端到端评测实测出来的 2019 API 适配）。也就是说 `ping` 报的版本
# **低报了 5 个补丁** —— 排查"装的桥是不是旧的"时，这个号恰恰最不能骗人。
BRIDGE_VERSION = "7.13.0"

# lib/ of the extension (reuses cost_estimator / standards_checker)
EXT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB_DIR = os.path.join(EXT_ROOT, "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

_MM = 1.0 / 304.8  # mm -> ft

_WOULDBLOCK_ERRNOS = (10035, 11, 35)  # WSAEWOULDBLOCK / EAGAIN / EWOULDBLOCK

TOKEN = None
CONFIG = {"allow_execute_code": False, "policy": "build"}
# v7.13.0：当前生效的策略档（safe / build / dev），由 start() 从 CONFIG 注入。
# 之所以做成模块全局而不是每次读 CONFIG：闸门在每个命令上都要用，别反复取。
POLICY = "build"


def _is_wouldblock(exc):
    err = getattr(exc, "errno", None)
    if err in _WOULDBLOCK_ERRNOS:
        return True
    text = str(exc)
    return "10035" in text or "WOULDBLOCK" in text.upper()


def log(msg):
    try:
        with open(LOG_PATH, "ab") as f:
            line = "[{}] {}\n".format(time.strftime("%H:%M:%S"), msg)
            try:
                if isinstance(line, unicode):
                    line = line.encode("utf-8")
            except NameError:
                pass
            f.write(line)
    except Exception:
        pass
    print(msg)


def audit(entry):
    """Append one JSONL audit record (ASCII-safe)."""
    try:
        entry.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        line = json.dumps(entry, ensure_ascii=True, default=str)
        with open(AUDIT_PATH, "ab") as f:
            f.write((line + "\n").encode("utf-8"))
    except Exception:
        pass


def load_or_create_token():
    try:
        if os.path.exists(TOKEN_PATH):
            t = open(TOKEN_PATH, "rb").read().decode("utf-8").strip()
            if len(t) >= 32:
                return t
        t = Guid.NewGuid().ToString("N") + Guid.NewGuid().ToString("N")
        with open(TOKEN_PATH, "wb") as f:
            f.write(t.encode("utf-8"))
        return t
    except Exception as e:
        log("WARN: token setup failed: {}".format(e))
        return None


def load_config():
    cfg = {"allow_execute_code": False, "policy": None}
    try:
        if os.path.exists(CONFIG_PATH):
            raw = open(CONFIG_PATH, "rb").read()
            if raw[:3] == b"\xef\xbb\xbf":
                raw = raw[3:]
            data = json.loads(raw.decode("utf-8"))
            if "allow_execute_code" in data:
                cfg["allow_execute_code"] = bool(data["allow_execute_code"])
            pol = data.get("policy")
            if pol in ("safe", "build", "dev"):
                cfg["policy"] = pol
            elif pol is not None:
                log("WARN: 未知 policy %r，已忽略（只认 safe/build/dev）" % (pol,))
    except Exception as e:
        log("WARN: config load failed: {}".format(e))
    # v7.13.0 向后兼容：配置里没写 policy 时，按老的 allow_execute_code 推导，
    # 保证已有部署行为不变（true -> dev，false -> build）。
    if not cfg["policy"]:
        cfg["policy"] = "dev" if cfg["allow_execute_code"] else "build"
        log("NOTE: 配置未写 policy，按 allow_execute_code=%s 推导为 %s"
            % (cfg["allow_execute_code"], cfg["policy"]))
    return cfg


# v7.3: injected by start() from the launcher script. Do NOT reference
# __revit__ here - that global only exists inside the pyRevit command
# script scope (which pyRevit clears after each run).
uiapp = None
uidoc = None
doc = None

HOST = "127.0.0.1"
PORT = 9877

_appdomain = AppDomain.CurrentDomain
BRIDGE_DATA_KEY = "MCPBridgeSharedData_v7"


def to_text(v):
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except Exception:
            return v.decode("utf-8", "replace")
    try:
        return str(v)
    except Exception:
        return "<conversion error>"


def to_element_list(collector):
    try:
        return list(collector.ToElements())
    except Exception:
        return list(collector)


def _doc_fingerprint():
    u"""v7.9.0: 文档指纹，附在每个写命令响应里。
    教训（2026-09-12 凌晨）：Revit 重启后活动文档变成新建空白"项目1"，
    标题一模一样，23 个门窗全部静默打进空文档，排查花了 3 小时。
    title 可能重复、path 可以为空——两者一起报，空 path 即未保存。"""
    fp = {"title": "", "saved": False, "n_instances": 0}
    try:
        if doc is not None:
            fp["title"] = to_text(doc.Title)
            fp["saved"] = bool(doc.PathName)
            fp["n_instances"] = len(to_element_list(
                FilteredElementCollector(doc)
                .WhereElementIsNotElementType()))
    except Exception:
        pass
    return fp


# v7.6: DocumentChanged incremental feed state. Module globals survive
# pyRevit's command-script cleanup (same lesson as v7.3).
_CHANGES = []
_CHANGE_SEQ = [0]
_doc_changed_delegate = None


def _on_doc_changed(sender, args):
    u"""Record added/deleted/modified element ids. Runs in a valid API
    context (reading event args there is legal)."""
    try:
        _CHANGE_SEQ[0] += 1
        _CHANGES.append({
            "seq": _CHANGE_SEQ[0],
            "doc": to_text(args.GetDocument().Title),
            "added": [i.IntegerValue for i in args.GetAddedElementIds()],
            "deleted": [i.IntegerValue for i in args.GetDeletedElementIds()],
            "modified": [i.IntegerValue for i in args.GetModifiedElementIds()],
        })
        if len(_CHANGES) > 2000:
            del _CHANGES[:len(_CHANGES) - 2000]
    except Exception:
        pass


class _OutShim(object):
    u"""create_elements out-param shim for windowless (bridge) runs."""

    def __init__(self):
        self.msgs = []

    def print_md(self, m):
        try:
            self.msgs.append(to_text(m))
        except Exception:
            pass

    def print_html(self, m):
        pass

    def __getattr__(self, name):
        def _noop(*a, **k):
            return None
        return _noop


# ---------------------------------------------------------------------------
# Atomic command handlers (run directly on Revit UI thread)
# ---------------------------------------------------------------------------

def do_ping(cmd):
    return {"pong": True, "document": to_text(doc.Title),
            "version": BRIDGE_VERSION,
            # v7.13.0：把策略档回给客户端。像 genbuild 这种依赖 dangerous 档的
            # 调用方，可以**在开建之前**就知道自己会被拒 —— 而不是建到一半才失败。
            "policy": to_text(POLICY),
            "allow_execute_code": bool(CONFIG.get("allow_execute_code", False))}


def do_get_info(cmd):
    info = {
        "title": to_text(doc.Title),
        "path": to_text(doc.PathName),
        "worksharing": doc.IsWorkshared,
    }
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    elems = list(collector.ToElements())
    info["instance_count"] = len(elems)

    cat_counts = {}
    for e in elems:
        try:
            cat = e.Category
            if cat and cat.Name:
                name = to_text(cat.Name)
                cat_counts[name] = cat_counts.get(name, 0) + 1
        except Exception:
            pass
    info["categories"] = dict(sorted(cat_counts.items(), key=lambda x: -x[1])[:60])

    type_collector = FilteredElementCollector(doc).WhereElementIsElementType()
    info["type_count"] = len(list(type_collector.ToElements()))

    levels = []
    for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
        levels.append({
            "id": l.Id.IntegerValue,
            "name": to_text(l.Name),
            "elevation": l.Elevation,
        })
    info["levels"] = sorted(levels, key=lambda x: x["elevation"])

    try:
        sel_ids = uidoc.Selection.GetElementIds()
        info["selected_count"] = len(list(sel_ids))
    except Exception:
        info["selected_count"] = 0

    return info


def do_get_categories(cmd):
    cats = {}
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    for e in collector.ToElements():
        try:
            cat = e.Category
            if cat and cat.Name:
                name = to_text(cat.Name)
                cats[name] = cats.get(name, 0) + 1
        except Exception:
            pass
    return {"categories": dict(sorted(cats.items(), key=lambda x: -x[1]))}


def do_get_levels(cmd):
    levels = []
    for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
        levels.append({
            "id": l.Id.IntegerValue,
            "name": to_text(l.Name),
            "elevation_ft": round(l.Elevation, 4),
            "elevation_mm": round(l.Elevation * 304.8, 1),
        })
    return {"levels": sorted(levels, key=lambda x: x["elevation_ft"])}


def do_get_families(cmd):
    families = {}
    for fs in FilteredElementCollector(doc).OfClass(FamilySymbol).ToElements():
        try:
            fam_name = to_text(fs.Family.Name)
            type_name = to_text(fs.Name)
            if fam_name not in families:
                families[fam_name] = []
            families[fam_name].append({
                "type_name": type_name,
                "id": fs.Id.IntegerValue,
                "category": to_text(fs.Category.Name) if fs.Category else "",
            })
        except Exception:
            pass
    return {"families": families}


def do_get_elements(cmd):
    category_name = cmd.get("category", "")
    limit = int(cmd.get("limit", 100) or 100)
    results = []
    collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
    for e in collector.ToElements():
        try:
            cat = e.Category
            if cat and to_text(cat.Name) == category_name:
                elem_info = {
                    "id": e.Id.IntegerValue,
                    "name": to_text(e.Name) if hasattr(e, "Name") else "",
                    "category": to_text(cat.Name),
                    "level": "",
                }
                try:
                    level_param = e.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
                    if level_param is None:
                        level_param = e.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)
                    if level_param and level_param.AsElementId().IntegerValue > 0:
                        elem_info["level"] = to_text(doc.GetElement(level_param.AsElementId()).Name)
                except Exception:
                    pass
                results.append(elem_info)
                if len(results) >= limit:
                    break
        except Exception:
            pass
    return {"elements": results}


def do_get_element(cmd):
    element_id = cmd.get("id", 0)
    eid = ElementId(int(element_id))
    elem = doc.GetElement(eid)
    if elem is None:
        return {"error": "Element not found"}

    info = {
        "id": elem.Id.IntegerValue,
        "class": to_text(type(elem).__name__),
        "name": to_text(elem.Name) if hasattr(elem, "Name") else "",
        "category": to_text(elem.Category.Name) if elem.Category else "",
    }

    params = {}
    for p in elem.Parameters:
        try:
            pname = to_text(p.Definition.Name)
            if p.HasValue:
                st_name = to_text(p.StorageType)
                if st_name == "String":
                    params[pname] = to_text(p.AsString())
                elif st_name == "Integer":
                    params[pname] = p.AsInteger()
                elif st_name == "Double":
                    params[pname] = round(p.AsDouble(), 4)
                elif st_name == "ElementId":
                    eid_val = p.AsElementId().IntegerValue
                    params[pname] = eid_val if eid_val > 0 else None
        except Exception:
            pass
    info["parameters"] = params
    return info


def do_get_selected(cmd):
    try:
        ids = [eid.IntegerValue for eid in uidoc.Selection.GetElementIds()]
        return {"selected_ids": ids}
    except Exception as e:
        return {"error": to_text(e)}


# ---- atomic write tools ----------------------------------------------------

def _wall_type_for(thickness_mm):
    """Find-or-create basic wall type of given total thickness (named reuse)."""
    name = u"MCP-W-%dmm" % int(round(thickness_mm))
    for wt in to_element_list(FilteredElementCollector(doc).OfClass(WallType)):
        try:
            if to_text(wt.Name) == name:
                return wt
        except Exception:
            pass
    base = None
    for wt in to_element_list(FilteredElementCollector(doc).OfClass(WallType)):
        try:
            if wt.Kind == WallKind.Basic:
                base = wt
                break
        except Exception:
            pass
    if base is None:
        return None
    try:
        new_wt = base.Duplicate(name)
    except Exception:
        return base
    try:
        cs = new_wt.GetCompoundStructure()
        if cs and cs.LayerCount > 0:
            idx = 0
            try:
                for i in range(cs.LayerCount):
                    # 2019: MaterialFunctionAssignment; 2020+: CompoundStructureLayerFunction.
                    # Both stringify to "Structure" for the core layer.
                    if to_text(cs.GetLayerFunction(i)) == "Structure":
                        idx = i
                        break
            except Exception:
                pass
            cs.SetLayerWidth(idx, thickness_mm * _MM)
            new_wt.SetCompoundStructure(cs)
    except Exception:
        pass
    return new_wt


def do_create_walls(cmd):
    """walls: [{start:[x,y,(z)] mm, end:[x,y,(z)] mm, height_mm,
               thickness_mm, level(optional name)}]"""
    walls = cmd.get("walls") or []
    if not walls:
        return {"error": "walls[] required (coordinates in millimeters)"}
    levels = to_element_list(FilteredElementCollector(doc).OfClass(Level))
    if not levels:
        return {"error": "no levels in document"}

    t = Transaction(doc, "MCP create_walls")
    _install_failure_preprocessor(t)  # no modal-dialog blocking
    del _MCP_LAST_FAILURES[:]  # v7.7.5: per-command telemetry window
    t.Start()
    created = 0
    errors = []
    made_ids = []  # v7.10.0: 返回新建墙 id（材质赋值/后续操作需要）
    for i, w in enumerate(walls):
        try:
            s = w.get("start") or []
            e = w.get("end") or []
            if len(s) < 2 or len(e) < 2:
                errors.append("wall {}: start/end need [x, y] in mm".format(i))
                continue
            lvl = None
            lname = w.get("level")
            if lname:
                for l in levels:
                    if to_text(l.Name) == lname:
                        lvl = l
                        break
            if lvl is None:
                lvl = sorted(levels, key=lambda l: l.Elevation)[0]
            z_off = (float(s[2]) if len(s) > 2 else 0.0) * _MM
            p0 = XYZ(float(s[0]) * _MM, float(s[1]) * _MM, lvl.Elevation + z_off)
            p1 = XYZ(float(e[0]) * _MM, float(e[1]) * _MM, lvl.Elevation + z_off)
            h = float(w.get("height_mm", 3000)) * _MM
            thickness = float(w.get("thickness_mm", 200))
            wt = _wall_type_for(thickness)
            if wt is None:
                errors.append("wall {}: no basic wall type available".format(i))
                continue
            line = Line.CreateBound(p0, p1)
            wall_el = Wall.Create(doc, line, wt.Id, lvl.Id, h, 0.0,
                                  False, False)
            made_ids.append(wall_el.Id.IntegerValue)
            created += 1
        except Exception as ex:
            errors.append("wall {}: {}".format(i, to_text(ex)))

    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({}); batch rolled back".format(
            to_text(status)), "created": 0,
            "failures": _MCP_LAST_FAILURES[:10]}
    return {"created": created, "failed": len(errors), "ids": made_ids,
            "errors": errors[:10], "failures": _MCP_LAST_FAILURES[:10]}


def _find_category_by_name(name):
    try:
        for cat in doc.Settings.Categories:
            if to_text(cat.Name) == name:
                return cat
    except Exception:
        pass
    return None


def do_set_parameters(cmd):
    """Batch set one parameter on all elements of a category.
    value: raw Revit internal-unit value (length params are in FEET)."""
    cat_name = cmd.get("category", "")
    pname = cmd.get("parameter", "")
    value = cmd.get("value")
    level_name = cmd.get("level")
    if not cat_name or not pname:
        return {"error": "category & parameter required"}
    cat = _find_category_by_name(cat_name)
    if cat is None:
        return {"error": "category not found: {}".format(cat_name)}

    t = Transaction(doc, "MCP set_parameters")
    t.Start()
    try:
        els = to_element_list(
            FilteredElementCollector(doc).OfCategoryId(cat.Id)
            .WhereElementIsNotElementType())
        if level_name:
            keep = []
            for el in els:
                matched = False
                try:
                    lp = el.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
                    if lp is None:
                        lp = el.get_Parameter(BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)
                    if lp is not None:
                        lvl = doc.GetElement(lp.AsElementId())
                        if lvl is not None and to_text(lvl.Name) == level_name:
                            matched = True
                except Exception:
                    matched = True  # cannot tell -> keep
                if matched:
                    keep.append(el)
            els = keep

        changed = 0
        failed = 0
        missing = 0
        for el in els:
            p = None
            try:
                for pp in el.Parameters:
                    if to_text(pp.Definition.Name) == pname:
                        p = pp
                        break
            except Exception:
                pass
            if p is None:
                missing += 1
                continue
            try:
                if p.IsReadOnly:
                    failed += 1
                    continue
                p.Set(value)
                changed += 1
            except Exception:
                failed += 1
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"error": to_text(ex)}

    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({})".format(to_text(status))}
    return {"changed": changed, "failed": failed,
            "missing_parameter": missing, "scanned": len(els)}


def do_batch_count(cmd):
    """Element quantities per category (reuses lib/cost_estimator)."""
    try:
        import cost_estimator
        counts = cost_estimator.count_elements(doc)
        return {"counts": counts}
    except Exception as ex:
        return {"error": "count failed: {} / {}".format(
            to_text(ex), to_text(traceback.format_exc())[:500])}


def do_run_report(cmd):
    """Standards review + model params (reuses lib/standards_checker)."""
    try:
        import standards_checker
        rep = standards_checker.run_review(doc)
        return {
            "score": rep["score"],
            "grade": rep["grade"],
            "fail_count": rep["fail_count"],
            "warn_count": rep["warn_count"],
            "model_params": rep["model_params"],
            "checks": [
                {"item": c["item"], "status": c["status"],
                 "message": c["message"], "standard": c["standard"]}
                for c in rep["checks"]
            ],
        }
    except Exception as ex:
        return {"error": "report failed: {} / {}".format(
            to_text(ex), to_text(traceback.format_exc())[:500])}


def do_execute_code(cmd):
    if not CONFIG.get("allow_execute_code", False):
        return {"error": "execute_code disabled by config "
                         "(mcp_bridge_config.json: allow_execute_code)"}
    code_text = cmd.get("code", "")
    # v7.12.3 新增：`code_b64` —— base64(UTF-8) 载荷通道。
    #   动机：`code` 字段承载非 ASCII 时，本解释器链路无法可靠还原（见下方
    #   v7.12.2 结论）。base64 全程纯 ASCII，无字符集歧义，且每一步都可
    #   确定还原：b64decode -> utf-8 decode -> 非 ASCII 转 \uXXXX -> compile。
    #   调用方：base64.b64encode(src.encode("utf-8"))。
    _b64 = cmd.get("code_b64")
    if _b64:
        try:
            import base64 as _b64mod
            _rawb = _b64mod.b64decode(_b64)
            # v7.12.4：显式 UTF-8 解码得到 unicode（全程不使用 isinstance 判据
            #   ——实测 IronPython 下 isinstance(x, unicode) 对字节串亦为真，
            #   会把逐字符转义退化成逐【字节】转义，产出 \u00e6 一类乱码）。
            _u = _rawb.decode("utf-8")
            code_text = u"".join(
                [c if ord(c) < 128 else u"\\u%04x" % ord(c)
                 for c in _u]
            ).encode("ascii")
        except Exception as ex:
            return {"status": "error",
                    "error": "code_b64 decode failed: %s" % to_text(ex)}
    ns = {
        "doc": doc,
        "uidoc": uidoc,
        "__revit__": uiapp,
        "to_text": to_text,
        "BRIDGE_DIR": HERE,
    }
    exec("from Autodesk.Revit.DB import *", ns)
    exec("from Autodesk.Revit.UI import *", ns)

    # v7.5: exec payloads as UTF-8 BYTES so their own coding cookie
    # (# -*- coding: utf-8 -*-) governs - IronPython 2.7 raises
    # SyntaxError for non-ASCII inside a unicode source.
    try:
        if isinstance(code_text, unicode):
            code_text = code_text.encode("utf-8")
    except NameError:
        pass

    # v7.5: caller-managed transaction mode. Lib functions such as
    # model_builder.build_model open their OWN Transaction(s) and Revit
    # forbids nesting, so the bridge must not wrap them in an outer one.
    # v7.12.0 修复（2026-09-28 实测）：exec() 对 bytes 源码按【字节】
    #   解码，coding cookie 不生效 ⇒ 载荷内中文裸字面量变 mojibake
    #   （repr u"'\xe6\xa0\x87\xe9\xab\x98 2'"）。
    #   实测 compile+exec(unicode) 在该解释器可用且非 ASCII 正确。
    # v7.12.4：移除通用转义块（在 IronPython 下会按字节转义产出乱码）。
    #   非 ASCII 载荷请走 code_b64（上方已确定性转换）；裸 code 字段承载非
    #   ASCII 时本解释器链路无法可靠还原，请改用 u"..." + \uXXXX 写法。
    try:
        code_text = compile(code_text, "<mcp_payload>", "exec")
    except Exception:
        pass
    if cmd.get("no_transaction"):
        try:
            exec(code_text, ns)
            out = {"status": "ok"}
            if "_result" in ns:
                out["result"] = ns["_result"]
            return out
        except Exception as ex:
            return {
                "status": "error",
                "error": to_text(ex),
                "traceback": to_text(traceback.format_exc())[:2000],
            }

    t = Transaction(doc, "MCP Bridge Execute")
    t.Start()
    try:
        exec(code_text, ns)
        status = t.Commit()
        out = {"status": "committed", "transaction_status": to_text(status)}
        if "_result" in ns:
            out["result"] = ns["_result"]
        return out
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {
            "status": "rolled_back",
            "error": to_text(ex),
            "traceback": to_text(traceback.format_exc()),
        }


def do_get_changes(cmd):
    u"""v7.6: incremental model changes since a seq watermark."""
    try:
        since = int(cmd.get("since", 0) or 0)
    except Exception:
        since = 0
    out = [c for c in _CHANGES if c.get("seq", 0) > since]
    return {"changes": out[-50:], "current_seq": _CHANGE_SEQ[0],
            "truncated": len(out) > 50}


def do_get_selection(cmd):
    u"""v7.6: current selection with element details."""
    ids = []
    try:
        for eid in uidoc.Selection.GetElementIds():
            ids.append(eid.IntegerValue)
    except Exception as e:
        return {"error": to_text(e)}
    elems = []
    for i in ids[:200]:
        try:
            el = doc.GetElement(ElementId(i))
            if el is None:
                continue
            nm = ""
            try:
                nm = to_text(el.Name)
            except Exception:
                pass
            elems.append({
                "id": i,
                "category": to_text(el.Category.Name) if el.Category else "",
                "name": nm,
            })
        except Exception:
            pass
    return {"selected_ids": ids, "elements": elems}


def do_set_selection(cmd):
    u"""v7.6: replace the current selection (ids: [int]). Routed through
    ExternalEvent (UI state change; no Transaction required)."""
    raw_ids = cmd.get("ids") or []
    col = DotNetList[ElementId]()
    skipped = 0
    for i in raw_ids:
        try:
            col.Add(ElementId(int(i)))
        except Exception:
            skipped += 1
    uidoc.Selection.SetElementIds(col)
    return {"selected": col.Count, "skipped": skipped}


# ---- v7.7: P0 tools from open-source revit-mcp analysis -------------------

def _existing_grid_names():
    names = set()
    for g in to_element_list(FilteredElementCollector(doc).OfClass(Grid)):
        try:
            names.add(to_text(g.Name))
        except Exception:
            pass
    return names


def _auto_grid_label(taken, prefer_number):
    u"""Next free label: digits for vertical grids, letters (A, B, ..,
    Z, AA..) for horizontal ones. Returns None when exhausted."""
    if prefer_number:
        for i in range(1, 1000):
            nm = str(i)
            if nm not in taken:
                return nm
    for i in range(0, 700):
        nm = u""
        n = i
        while True:
            nm = chr(65 + (n % 26)) + nm
            n = n // 26 - 1
            if n < 0:
                break
        if nm not in taken:
            return nm
    return None


def do_create_grids(cmd):
    u"""v7.7: batch-create grids. Each item is either an explicit line
    ({name?, p0:[x,y] mm, p1:[x,y] mm}) or an axis line
    ({name?, axis: "x"|"y", coord_mm}).
    axis="y" -> grid parallel to Y at x=coord (vertical, auto-numbered);
    axis="x" -> grid parallel to X at y=coord (horizontal, auto-lettered).
    Existing labels are skipped, not duplicated. Single transaction,
    commit status checked."""
    grids = cmd.get("grids") or []
    if not grids:
        return {"error": "grids[] required (coordinates in millimeters)"}
    taken = _existing_grid_names()
    # v7.7.4: all-duplicate batch -> no transaction at all
    def _gd_todo(g):
        name = to_text(g.get("name") or "")
        return (not name) or (name not in taken)
    if not any(_gd_todo(g) for g in grids):
        return {"created": 0,
                "skipped": [u"all {} grid(s) already exist".format(len(grids))],
                "errors": []}
    t = Transaction(doc, u"MCP create_grids")
    _install_failure_preprocessor(t)
    del _MCP_LAST_FAILURES[:]  # v7.7.5: per-command telemetry window
    t.Start()
    created_ids = []
    skipped = []
    errors = []
    for i, g in enumerate(grids):
        try:
            name = to_text(g.get("name") or "")
            if name and name in taken:
                skipped.append(u"grid {}: name '{}' exists".format(i, name))
                continue
            p0 = g.get("p0")
            p1 = g.get("p1")
            axis = to_text(g.get("axis", "y"))
            if p0 and p1:
                if len(p0) < 2 or len(p1) < 2:
                    errors.append(u"grid {}: p0/p1 need [x, y] in mm".format(i))
                    continue
                ax = float(p0[0])
                ay = float(p0[1])
                bx = float(p1[0])
                by = float(p1[1])
                # v7.7.2: XYZ.Distance is missing from the IPY2.7
                # binding (live test 2026-09-11) - compute length in mm.
                if ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5 < 1.0:
                    errors.append(u"grid {}: line too short".format(i))
                    continue
                a = XYZ(ax * _MM, ay * _MM, 0.0)
                b = XYZ(bx * _MM, by * _MM, 0.0)
            else:
                coord = float(g.get("coord_mm", 0)) * _MM
                ext = 30000.0 * _MM  # 30 m default extent each way
                if axis == "x":
                    a = XYZ(-ext, coord, 0.0)
                    b = XYZ(ext, coord, 0.0)
                else:
                    a = XYZ(coord, -ext, 0.0)
                    b = XYZ(coord, ext, 0.0)
            grid = Grid.Create(doc, Line.CreateBound(a, b))
            if not name:
                name = _auto_grid_label(taken, axis == "y")
            if name:
                try:
                    grid.Name = name
                except Exception as ex:
                    errors.append(u"grid {}: created but rename failed: "
                                  u"{}".format(i, to_text(ex)))
            final_name = to_text(grid.Name)
            taken.add(final_name)
            created_ids.append(grid.Id.IntegerValue)
        except Exception as ex:
            errors.append(u"grid {}: {}".format(i, to_text(ex)))
    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({}); batch rolled back".format(
            to_text(status)), "created": 0,
            "failures": _MCP_LAST_FAILURES[:10]}
    return {"created": len(created_ids), "ids": created_ids,
            "skipped": skipped[:10], "errors": errors[:10],
            "failures": _MCP_LAST_FAILURES[:10]}


def do_create_levels(cmd):
    u"""v7.7: batch-create levels ({name?, elevation_mm}). Duplicate
    names are skipped. Single transaction, commit status checked."""
    levels = cmd.get("levels") or []
    if not levels:
        return {"error": "levels[] required (elevation_mm from 0.000 datum)"}
    existing = set()
    for l in to_element_list(FilteredElementCollector(doc).OfClass(Level)):
        try:
            existing.add(to_text(l.Name))
        except Exception:
            pass
    # v7.7.4: all-duplicate batch -> no transaction at all (an empty
    # commit must never be the thing that decides success here)
    def _lv_todo(lv):
        name = to_text(lv.get("name") or "")
        return (not name) or (name not in existing)
    if not any(_lv_todo(lv) for lv in levels):
        return {"created": 0,
                "skipped": [u"all {} level(s) already exist".format(len(levels))],
                "errors": []}
    t = Transaction(doc, u"MCP create_levels")
    _install_failure_preprocessor(t)
    del _MCP_LAST_FAILURES[:]  # v7.7.5: per-command telemetry window
    t.Start()
    created_ids = []
    skipped = []
    errors = []
    for i, lv in enumerate(levels):
        try:
            name = to_text(lv.get("name") or "")
            elev = float(lv.get("elevation_mm", 0)) * _MM
            if name and name in existing:
                skipped.append(u"level {}: name '{}' exists".format(i, name))
                continue
            lvl = Level.Create(doc, elev)
            if name:
                try:
                    lvl.Name = name
                except Exception as ex:
                    errors.append(u"level {}: created but rename failed: "
                                  u"{}".format(i, to_text(ex)))
            existing.add(to_text(lvl.Name))
            created_ids.append(lvl.Id.IntegerValue)
        except Exception as ex:
            errors.append(u"level {}: {}".format(i, to_text(ex)))
    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({}); batch rolled back".format(
            to_text(status)), "created": 0,
            "failures": _MCP_LAST_FAILURES[:10]}
    return {"created": len(created_ids), "ids": created_ids,
            "skipped": skipped[:10], "errors": errors[:10],
            "failures": _MCP_LAST_FAILURES[:10]}


def do_query_elements(cmd):
    u"""v7.7: read-only element filter. Optional: category (Chinese
    name), level (name), name_contains (substring), ids ([int]),
    with_params (list of parameter names to project), limit."""
    category = cmd.get("category") or ""
    level_name = cmd.get("level") or ""
    name_contains = cmd.get("name_contains") or ""
    want_params = cmd.get("with_params") or []
    ids_in = cmd.get("ids") or []
    try:
        limit = int(cmd.get("limit", 200) or 200)
    except Exception:
        limit = 200
    limit = max(1, min(limit, 1000))
    cap = 2000

    elems = []
    if ids_in:
        for i in ids_in:
            try:
                el = doc.GetElement(ElementId(int(i)))
                if el is not None:
                    elems.append(el)
            except Exception:
                pass
    else:
        cat = _find_category_by_name(category) if category else None
        if category and cat is None:
            return {"error": "category not found: {}".format(category)}
        col = FilteredElementCollector(doc)
        if cat is not None:
            # v7.7.2: OfCategory() wants BuiltInCategory; cat.Id is an
            # ElementId -> "expected BuiltInCategory, got ElementId"
            # (live test 2026-09-11). OfCategoryId is the right overload.
            col = col.OfCategoryId(cat.Id)
        elems = to_element_list(col.WhereElementIsNotElementType())
        if len(elems) > cap:
            elems = elems[:cap]

    results = []
    for e in elems:
        try:
            info = {"id": e.Id.IntegerValue, "category": "",
                    "name": "", "level": ""}
            try:
                if e.Category and e.Category.Name:
                    info["category"] = to_text(e.Category.Name)
            except Exception:
                pass
            try:
                if hasattr(e, "Name"):
                    info["name"] = to_text(e.Name)
            except Exception:
                pass
            try:
                lp = e.get_Parameter(BuiltInParameter.FAMILY_LEVEL_PARAM)
                if lp is None:
                    lp = e.get_Parameter(
                        BuiltInParameter.FAMILY_BASE_LEVEL_PARAM)
                if lp and lp.AsElementId().IntegerValue > 0:
                    info["level"] = to_text(
                        doc.GetElement(lp.AsElementId()).Name)
            except Exception:
                pass
            if level_name and info["level"] != level_name:
                continue
            if name_contains and name_contains not in info["name"]:
                continue
            if want_params:
                params = {}
                for want in want_params:
                    wname = to_text(want)
                    try:
                        for pp in e.Parameters:
                            if to_text(pp.Definition.Name) == wname:
                                if pp.HasValue:
                                    stn = to_text(pp.StorageType)
                                    if stn == "String":
                                        params[wname] = to_text(pp.AsString())
                                    elif stn == "Double":
                                        params[wname] = round(pp.AsDouble(), 4)
                                    elif stn == "Integer":
                                        params[wname] = pp.AsInteger()
                                    elif stn == "ElementId":
                                        ev = pp.AsElementId().IntegerValue
                                        params[wname] = ev if ev > 0 else None
                                break
                    except Exception:
                        pass
                info["params"] = params
            results.append(info)
        except Exception:
            pass

    total = len(results)
    return {"total_matched": total, "returned": min(total, limit),
            "elements": results[:limit], "truncated": total > limit}


_FT3_PER_M3 = 35.3146667
_FT2_PER_M2 = 10.7639104


def do_get_quantities(cmd):
    u"""v7.7: per-category quantity takeoff (count / area_sqm /
    volume_cum) + simple cost estimate, reusing lib/cost_estimator.
    Optional detail_category returns per-element volume/area."""
    detail_cat = cmd.get("detail_category") or ""
    try:
        import cost_estimator
        counts = cost_estimator.count_elements(doc)
    except Exception as ex:
        return {"error": "count failed: {}".format(to_text(ex))}
    out = {"counts": counts}
    try:
        cost_ref = cost_estimator.load_cost_ref()
        items, total = cost_estimator.estimate_cost(counts, cost_ref)
        out["cost_estimate"] = {"items": items, "total_yuan": total}
    except Exception:
        pass
    if detail_cat:
        cat = _find_category_by_name(detail_cat)
        if cat is None:
            out["detail_error"] = "category not found: {}".format(detail_cat)
        else:
            detail = []
            for e in to_element_list(
                    FilteredElementCollector(doc).OfCategoryId(cat.Id)
                    .WhereElementIsNotElementType()):
                try:
                    d = {"id": e.Id.IntegerValue,
                         "name": to_text(e.Name) if hasattr(e, "Name") else "",
                         "volume_m3": None, "area_m2": None}
                    vp = e.get_Parameter(
                        BuiltInParameter.HOST_VOLUME_COMPUTED)
                    if vp and vp.HasValue:
                        d["volume_m3"] = round(
                            vp.AsDouble() / _FT3_PER_M3, 4)
                    ap = e.get_Parameter(BuiltInParameter.HOST_AREA_COMPUTED)
                    if ap and ap.HasValue:
                        d["area_m2"] = round(ap.AsDouble() / _FT2_PER_M2, 2)
                    detail.append(d)
                except Exception:
                    pass
            out["detail"] = detail[:500]
    return out


def do_dwg_to_model(cmd):
    u"""v7.6: build elements from a model_plan.json (dwg_to_json output)
    via the DWG翻模 lib chain. create_elements opens its OWN
    transactions (caller-managed, like execute_code no_transaction)."""
    json_path = cmd.get("json_path", "")
    if not json_path or not os.path.isfile(json_path):
        return {"error": "json_path required (a model_plan.json file "
                         "produced by dwg_to_json.py)"}
    safe = bool(cmd.get("safe_mode", True))
    try:
        import bimconvert
        raw = open(json_path, "rb").read()
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        plan_json = json.loads(raw.decode("utf-8"))
        rules_path = cmd.get("rules_path") or os.path.join(
            EXT_ROOT, u"BIMToolkit.tab", u"BIM 工具.panel",
            u"DWG翻模.pushbutton", u"mapping_rules.csv")
        rules = bimconvert.load_rules(rules_path)
        level_names = []
        for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
            try:
                level_names.append(to_text(l.Name))
            except Exception:
                pass
        if not level_names:
            return {"error": "no levels in document - create levels first"}
        planned, unmatched = bimconvert.plan_from_json(
            plan_json, rules, level_names)
        planned = bimconvert.consolidate_walls(planned)
        try:
            slabs = bimconvert.slab_plans_from_wall_loops(planned)
            if slabs:
                planned.extend(slabs)
        except Exception:
            pass
        if not planned:
            return {"error": "nothing matched the mapping rules",
                    "unmatched": len(unmatched)}
        out = _OutShim()
        big = len(planned) > 200
        results = bimconvert.create_elements(
            doc, planned, out,
            batch_size=25 if len(planned) > 500 else 50,
            # v7.8.0: join_walls 默认关（v2.8c 铁律：墙接合是原生崩溃源之一，
            # 按钮侧早已强制关，桥侧漏改）。需要接合的场合由命令显式传入。
            join_walls=bool(cmd.get("join_walls", False)),
            door_window_cut=(not big) and (not safe),
            safe_mode=safe)
        n_ok = 0
        fail_samples = []
        for r in results:
            try:
                if r[0]:
                    n_ok += 1
                elif len(fail_samples) < 5:
                    fail_samples.append(to_text(r[1])[:200])
            except Exception:
                pass
        by_type = {}
        for p in planned:
            t_ = p.get("element_type", "?")
            by_type[t_] = by_type.get(t_, 0) + 1
        return {
            "planned": len(planned), "by_type": by_type,
            "unmatched": len(unmatched),
            "built_ok": n_ok, "built_fail": len(results) - n_ok,
            "fail_samples": fail_samples,
            "safe_mode": safe,
        }
    except Exception as ex:
        return {"error": to_text(ex),
                "traceback": to_text(traceback.format_exc())[:1500]}


def do_delete_elements(cmd):
    """ids: [ElementId int...] —— 精修用：删除建歪的门/窗等构件。"""
    ids = cmd.get("ids") or []
    if not ids:
        return {"error": "ids[] required"}
    t = Transaction(doc, "MCP delete_elements")
    _install_failure_preprocessor(t)
    t.Start()
    deleted, errors = 0, []
    for i in ids[:500]:
        try:
            doc.Delete(ElementId(int(i)))
            deleted += 1
        except Exception as ex:
            errors.append("id {}: {}".format(i, to_text(ex)))
    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({})".format(to_text(status)),
                "deleted": 0}
    return {"deleted": deleted, "failed": len(errors), "errors": errors[:10]}


def do_create_door_window(cmd):
    """openings: [{kind: 'door'|'window', point:[x,y] mm, width_mm,
                   height_mm(optional), sill_mm(optional, window),
                   level(optional name)}]
    精修核心命令：选宽度最贴近的族类型 → 就近寄宿墙自动开洞 →
    回写实例 宽度/高度/底高度 为请求值。"""
    openings = cmd.get("openings") or []
    if not openings:
        return {"error": "openings[] required"}
    levels = to_element_list(FilteredElementCollector(doc).OfClass(Level))
    if not levels:
        return {"error": "no levels in document"}

    import bimconv_walls as bw
    import bimconv_elements as be

    # 墙收集：v7.8.3 用与 do_get_elements 完全一致的「无过滤迭代 + 类别名匹配」
    # v7.10.2: 每面墙带 LevelId —— 四层建筑各层墙 XY 完全重叠，
    # 不分标高选宿主 = 门窗随机寄宿到别的楼层（别墅首建 28/49 丢失的根因）
    walls_all = []
    n_scanned = 0
    for e in to_element_list(
            FilteredElementCollector(doc).WhereElementIsNotElementType()):
        n_scanned += 1
        try:
            cat = e.Category
            if not cat:
                continue
            cn = to_text(cat.Name)
            if cn not in (u"墙", "Walls"):
                continue
            loc = e.Location
            lc = loc.Curve if loc is not None else None
            if lc is None:
                continue
            lid = None
            try:
                lp = e.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
                if lp is not None and lp.AsElementId():
                    lid = lp.AsElementId().IntegerValue
            except Exception:
                pass
            if lid is None:
                try:
                    lid = e.LevelId.IntegerValue
                except Exception:
                    lid = 0
            walls_all.append((e, lc, lid))
        except Exception:
            continue

    idx_cache = {}

    def index_for(lvl):
        u"""按标高取墙索引（懒建 + 缓存）。"""
        key = lvl.Id.IntegerValue
        if key in idx_cache:
            return idx_cache[key]
        sub = [(w, c) for (w, c, lid) in walls_all if lid == key]
        idx = None
        if sub:
            try:
                idx = bw._build_wall_index(sub)
            except Exception:
                idx = None
            if not idx or not idx[0]:
                idx = ({(0, 0): sub}, lambda xyz: (0, 0))
        idx_cache[key] = idx
        return idx

    # 族类型池：按类别缓存，宽度参数预解析
    def sym_width(sym):
        for nm in (u"宽度", "Width"):
            try:
                p = sym.LookupParameter(nm)
                if p is not None and p.HasValue and p.AsDouble() > 0:
                    return p.AsDouble()
            except Exception:
                pass
        return None

    sym_cache = {}

    def pick_sym(kind, width_mm):
        if kind not in sym_cache:
            cat = _find_category_by_name(u"门" if kind == "door" else u"窗")
            if cat is None:
                return []
            syms = [s for s in to_element_list(
                FilteredElementCollector(doc).OfCategoryId(cat.Id)
                .WhereElementIsElementType())
                if isinstance(s, FamilySymbol)]
            pool = []
            for s in syms:
                wft = sym_width(s)
                pool.append((s, wft))
            sym_cache[kind] = pool
        pool = sym_cache[kind]
        if not pool:
            return None
        target = width_mm * _MM
        fitting = [(s, w) for (s, w) in pool if w is not None and w <= target + 1e-9]
        if fitting:
            return max(fitting, key=lambda sw: sw[1])[0]
        return min(pool, key=lambda sw: (sw[1] if sw[1] is not None else 1e9))[0]

    def set_inst(inst, names, value):
        for nm in names:
            try:
                p = inst.LookupParameter(nm)
                if p is not None and not p.IsReadOnly:
                    p.Set(value)
                    return True
            except Exception:
                pass
        return False

    if not walls_all:
        # v7.9.0: 无墙文档直接整体失败，且必须在开事务之前。
        fp = _doc_fingerprint()
        return {"error": (u"活动文档中没有墙（title=%s, 已保存=%s, "
                          u"元素数=%d）——很可能在空白/错误文档里操作。"
                          u"请先对目标文档执行 dwg_to_model 翻模，"
                          u"或切换到目标文档后再点一次 MCP Bridge 按钮"
                          % (fp["title"], u"是" if fp["saved"] else u"否",
                             fp["n_instances"])),
                "created": 0, "failed": len(openings), "doc": fp}
    t = Transaction(doc, "MCP create_door_window")
    _install_failure_preprocessor(t)
    t.Start()
    created, errors = 0, []
    for i, op in enumerate(openings[:200]):
        try:
            kind = op.get("kind", "door")
            pt = op.get("point") or []
            if len(pt) < 2:
                errors.append("opening {}: point [x,y] required".format(i))
                continue
            width_mm = float(op.get("width_mm", 900 if kind == "door" else 1200))
            height_mm = float(op.get("height_mm", 2100 if kind == "door" else 1500))
            sill_mm = float(op.get("sill_mm", 900 if kind == "window" else 0))
            sym = pick_sym(kind, width_mm)
            if sym is None:
                # v2.11f: {} 与 %s 混用会抛 "not all arguments converted"，
                # 掩盖"模板没有门/窗族"的真实错误
                errors.append("opening {}: 项目中没有{}族".format(
                    i, u"门" if kind == "door" else u"窗"))
                continue
            lname = op.get("level")
            lvl = None
            if lname:
                for l in levels:
                    if to_text(l.Name) == lname:
                        lvl = l
                        break
            if lvl is None:
                lvl = sorted(levels, key=lambda l: l.Elevation)[0]
            # v7.10.2: 按标高取墙索引，杜绝跨层寄宿
            wall_index = index_for(lvl)
            if wall_index is None:
                errors.append("opening {}: 标高 %s 上没有墙"
                              % (i, to_text(lvl.Name)))
                continue
            plan = {
                "element_type": kind,
                "geom": {"type": "insert", "point": [pt[0], pt[1], 0.0]},
                "level": to_text(lvl.Name),
                "height": height_mm,
            }
            inst = be._create_door_window(doc, plan, lvl.Id, sym,
                                          wall_index, cut=True)
            if inst is None:
                errors.append("opening {}: placed None".format(i))
                continue
            set_inst(inst, (u"宽度", "Width"), width_mm * _MM)
            set_inst(inst, (u"高度", "Height"), height_mm * _MM)
            if sill_mm:
                set_inst(inst, (u"底高度", "SILL_HEIGHT"), sill_mm * _MM)
            created += 1
        except Exception as ex:
            errors.append("opening {}: {}".format(i, to_text(ex)[:160]))
    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({}); batch rolled back".format(
            to_text(status)), "created": 0,
            "failures": _MCP_LAST_FAILURES[:10]}
    return {"created": created, "failed": len(errors), "errors": errors[:20],
            "n_walls_indexed": len(walls_all)}


def do_debug_walls(cmd):
    """v7.8.5 只读探针：墙收集三路统计，定位 write 上下文收集为 0 的真因。"""
    hist = {}
    n_scanned = 0
    n_nocat = 0
    n_exc = 0
    last_exc = ""
    wall_hits = 0
    loc_none = 0
    loc_other = 0
    loc_curve_ok = 0
    ofclass_count = 0
    try:
        ofclass_count = len(list(FilteredElementCollector(doc)
                                 .OfClass(Wall).ToElements()))
    except Exception as ex:
        last_exc = "OfClass: " + to_text(ex)
    for e in to_element_list(
            FilteredElementCollector(doc).WhereElementIsNotElementType()):
        n_scanned += 1
        try:
            cat = e.Category
            if not cat:
                n_nocat += 1
                continue
            cn = to_text(cat.Name)
            hist[cn] = hist.get(cn, 0) + 1
            if cn in (u"墙", "Walls"):
                wall_hits += 1
                loc = e.Location
                if loc is None:
                    loc_none += 1
                else:
                    lt = to_text(type(loc).__name__)
                    if lt == "LocationCurve":
                        loc_curve_ok += 1
                    else:
                        loc_other += 1
        except Exception as ex:
            n_exc += 1
            last_exc = to_text(ex)
    top = sorted(hist.items(), key=lambda kv: -kv[1])[:15]
    return {"n_scanned": n_scanned, "n_nocat": n_nocat, "n_exc": n_exc,
            "last_exc": last_exc, "wall_hits": wall_hits,
            "loc_none": loc_none, "loc_other": loc_other,
            "loc_curve_ok": loc_curve_ok, "ofclass_count": ofclass_count,
            "top_categories": top}


def do_get_wall_geo(cmd):
    u"""v7.9.2 只读：按 ids 返回墙的定位曲线端点与长度（mm）。
    2026-09-12 门窗精修事故的后续工具：payload 声称的墙位置与模型
    实际位置对不上时，用它直接读模型几何，一炮定位。"""
    ids = cmd.get("ids") or []
    out = []
    for i in ids[:500]:
        try:
            e = doc.GetElement(ElementId(int(i)))
            loc = e.Location if e is not None else None
            curve = loc.Curve if loc is not None else None
            if curve is None:
                out.append({"id": int(i), "error": "no curve"})
                continue
            s = curve.GetEndPoint(0)
            t = curve.GetEndPoint(1)
            out.append({"id": int(i),
                        "start": [round(s.X / _MM), round(s.Y / _MM)],
                        "end": [round(t.X / _MM), round(t.Y / _MM)],
                        "length_mm": round(curve.Length / _MM)})
        except Exception as ex:
            out.append({"id": int(i), "error": to_text(ex)[:80]})
    return {"walls": out}


def _level_by_name(lname):
    levels = to_element_list(FilteredElementCollector(doc).OfClass(Level))
    if lname:
        for l in levels:
            if to_text(l.Name) == lname:
                return l
    if levels:
        return sorted(levels, key=lambda l: l.Elevation)[0]
    return None


def _curvearray_rect(x1, y1, x2, y2, z=0.0):
    u"""v7.10.3: 必须用 Revit 的 CurveArray —— NewFloor/NewFootPrintRoof
    不接受 .NET List[Curve]（实测: "expected CurveArray, got List[Curve]"）。"""
    arr = CurveArray()
    arr.Append(Line.CreateBound(XYZ(x1, y1, z), XYZ(x2, y1, z)))
    arr.Append(Line.CreateBound(XYZ(x2, y1, z), XYZ(x2, y2, z)))
    arr.Append(Line.CreateBound(XYZ(x2, y2, z), XYZ(x1, y2, z)))
    arr.Append(Line.CreateBound(XYZ(x1, y2, z), XYZ(x1, y1, z)))
    return arr


def do_create_floors(cmd):
    u"""v7.10.0: 建楼板。floors: [{points:[[x,y]..](mm, 逆时针闭合), level,
    structural(bool, 默认 False), type(可选楼板类型名包含字)}]"""
    floors = cmd.get("floors") or []
    if not floors:
        return {"error": "floors[] required"}
    ftype = None
    want = to_text(cmd.get("type", ""))
    for ft in to_element_list(FilteredElementCollector(doc).OfClass(FloorType)):
        try:
            nm = to_text(ft.get_Name())
        except Exception:
            try:
                nm = to_text(ft.Name)
            except Exception:
                continue
        if not want or want in nm:
            ftype = ft
            break
    t = Transaction(doc, "MCP create_floors")
    _install_failure_preprocessor(t)
    del _MCP_LAST_FAILURES[:]
    t.Start()
    created, errors = 0, []
    made_ids = []
    for i, fl in enumerate(floors[:200]):
        try:
            pts = fl.get("points") or []
            if len(pts) < 3:
                errors.append("floor {}: >=3 points required".format(i))
                continue
            lvl = _level_by_name(fl.get("level"))
            if lvl is None:
                errors.append("floor {}: no level".format(i))
                continue
            z = lvl.Elevation
            arr = CurveArray()
            n = len(pts)
            for k in range(n):
                a = pts[k]
                b = pts[(k + 1) % n]
                arr.Append(Line.CreateBound(
                    XYZ(a[0] * _MM, a[1] * _MM, z),
                    XYZ(b[0] * _MM, b[1] * _MM, z)))
            ft = ftype
            if ft is None:
                ft = list(FilteredElementCollector(doc)
                          .OfClass(FloorType).ToElements())[0]
            fl_el = doc.Create.NewFloor(arr, ft, lvl,
                                        bool(fl.get("structural", False)))
            created += 1
            made_ids.append(fl_el.Id.IntegerValue)
        except Exception as ex:
            errors.append("floor {}: {}".format(i, to_text(ex)[:150]))
    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({}); rolled back".format(
            to_text(status)), "created": 0,
            "failures": _MCP_LAST_FAILURES[:10]}
    return {"created": created, "failed": len(errors), "ids": made_ids,
            "errors": errors[:20]}


def _type_name(elem, fallback=u"?"):
    u"""v7.12.0：2019 安全取「类型/族」名。2019 的 Type.Name 恒抛
    AttributeError('Name')，必须走 SYMBOL_NAME_PARAM。
    ★ 反向陷阱：Level 反过来 —— 对 Level 用 SYMBOL_NAME_PARAM 返回 None，
    必须用 .Name。本函数只用于类型族元素，勿用于 Level。"""
    try:
        p = elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p is not None:
            v = p.AsString()
            if v:
                return to_text(v)
    except Exception:
        pass
    try:
        return to_text(elem.Name)
    except Exception:
        return fallback


def do_create_roof_gable(cmd):
    u"""v7.10.0: 双坡屋面（FootPrintRoof）。参数(mm):
    x1,y1,x2,y2 = 屋面外轮廓（含挑檐）, level, slope_deg, ridge_axis('x'/'y')
    ridge_axis='x': 屋脊沿 X，南北两条长边起坡（默认）。"""
    try:
        x1 = float(cmd["x1"]) * _MM
        y1 = float(cmd["y1"]) * _MM
        x2 = float(cmd["x2"]) * _MM
        y2 = float(cmd["y2"]) * _MM
    except Exception:
        return {"error": "x1,y1,x2,y2 (mm) required"}
    lvl = _level_by_name(cmd.get("level"))
    if lvl is None:
        return {"error": "no level"}
    slope = math.radians(float(cmd.get("slope_deg", 30)))
    ridge_axis = to_text(cmd.get("ridge_axis", "x"))
    rtypes = list(FilteredElementCollector(doc).OfClass(RoofType)
                  .ToElements())
    if not rtypes:
        return {"error": u"项目中没有屋顶类型(RoofType)"}
    rtype = rtypes[0]
    # 轮廓：ridge_axis='x' -> 脊沿 X，南北边起坡
    # _curvearray_rect 边序: 0=南(x1,y1)->(x2,y1), 1=东, 2=北, 3=西
    profile = _curvearray_rect(x1, y1, x2, y2, lvl.Elevation)
    sloped_edges = set((0, 2)) if ridge_axis == "x" else set((1, 3))
    t = Transaction(doc, "MCP create_roof_gable")
    _install_failure_preprocessor(t)
    del _MCP_LAST_FAILURES[:]
    t.Start()
    roof = None
    slope_notes = []
    try:
        # v7.10.4: 2019 实测签名是 4 参
        # (CurveArray, Level, RoofType, out StrongBox[ModelCurveArray])
        # —— 第 4 参是出参映射（"expected StrongBox[ModelCurveArray],
        # got List[bool]"），返回各轮廓边对应 ModelCurve，据此逐边设坡。
        from Autodesk.Revit.DB import ModelCurveArray as _MCA
        from clr import StrongBox as _StrongBox
        box = _StrongBox[_MCA]()
        roof = doc.Create.NewFootPrintRoof(profile, lvl, rtype, box)
        try:
            mc_arr = box.Value
            for k in range(mc_arr.Count):
                edge = mc_arr.get_Item(k)
                if k not in sloped_edges:
                    continue
                pd = edge.LookupParameter(u"定义坡度")
                if pd is None:
                    pd = edge.get_Parameter(BuiltInParameter.DEFINES_SLOPE)
                if pd is not None and not pd.IsReadOnly:
                    pd.Set(True)
                ps = edge.LookupParameter(u"坡度")
                if ps is None:
                    ps = edge.get_Parameter(BuiltInParameter.ROOF_SLOPE)
                if ps is not None and not ps.IsReadOnly:
                    ps.Set(slope)
        except Exception as exs:
            slope_notes.append(to_text(exs)[:120])
    except Exception as ex1:
        try:
            roof = doc.Create.NewFootPrintRoof(profile, lvl, rtype)
            # 无出参签名：整个屋顶用实例坡度参数兜底
            try:
                p = roof.LookupParameter(u"坡度")
                if p is None:
                    p = roof.get_Parameter(BuiltInParameter.ROOF_SLOPE)
                if p is not None and not p.IsReadOnly:
                    p.Set(slope)
            except Exception:
                pass
        except Exception as ex2:
            t.RollBack()
            return {"error": "NewFootPrintRoof failed: %s | %s" % (
                to_text(ex1)[:120], to_text(ex2)[:120]),
                    "diag": "CurveArray/Level/RoofType 均已核验非空；"
                            "2019 唯一重载 = NewFootPrintRoof(CurveArray, "
                            "Level, RoofType, out ModelCurveArray)。异常来自 "
                            "Revit 内部，非参数缺失 —— 需 Revit 侧单步调试。"}
    if roof is None:
        t.RollBack()
        return {"error": "roof is None"}
    status = t.Commit()
    if to_text(status) != "Committed":
        return {"error": "commit failed ({}); rolled back".format(
            to_text(status)), "failures": _MCP_LAST_FAILURES[:10]}
    return {"created": 1, "id": roof.Id.IntegerValue,
            "type": _type_name(rtype), "slope_notes": slope_notes}


def do_create_stairs(cmd):
    u"""v7.12.0 修复（2026-09-28 端到端评测实测）：2019 真实 API 适配 ——
    * StairsEditScope 在 Autodesk.Revit.DB，不在 .Architecture
      （原 from-import 必抛 ImportError，主路整条死）。
    * Start 必须用 2 参 (baseLevelId, topLevelId)；1 参重载语义是
      「编辑既有楼梯」，误用报 "It is not a Stair's id"。
    * StairsRun.CreateStraightRun(doc, stairsId, line, justification)
      共 4 参（原 CreateStraightStair 在 2019 **不存在**）。
    * scope.Commit(IFailuresPreprocessor) 必须传实例；传 None 报
      "input argument failurePreprocessor ... is null"。
    * 回退斜楼板用 CurveArray（2019 NewFloor 不收 List[Curve]）。
    base_level -> top_level, start:[x,y](mm), dir:[dx,dy], width_mm, run_mm。
    注：2019 直跑梯的踏步高由两标高差决定，total_rise_mm 仅作校验提示。"""
    bl = _level_by_name(cmd.get("base_level"))
    tl = _level_by_name(cmd.get("top_level"))
    if bl is None or tl is None:
        return {"error": "base_level/top_level required"}
    st = cmd.get("start") or [0, 0]
    d = cmd.get("dir") or [0, 1]
    width = float(cmd.get("width_mm", 1200)) * _MM
    run = float(cmd.get("run_mm", 2100)) * _MM
    sx, sy = float(st[0]) * _MM, float(st[1]) * _MM
    dx, dy = float(d[0]), float(d[1])
    errs = []
    rise_req = float(cmd.get("total_rise_mm", 0) or 0)
    rise_actual = (tl.Elevation - bl.Elevation) / _MM
    if rise_req and abs(rise_req - rise_actual) > 50:
        errs.append("total_rise_mm %.0f 与标高差 %.0f 不符：2019 直跑梯由标高差决定"
                    % (rise_req, rise_actual))
    stair_id = None
    scope = None
    base_p = XYZ(sx, sy, bl.Elevation)
    end_p = XYZ(sx + dx * run, sy + dy * run, bl.Elevation)
    try:
        from Autodesk.Revit.DB import StairsEditScope
        from Autodesk.Revit.DB.Architecture import StairsRun
        from Autodesk.Revit.DB.Architecture import StairsRunJustification
        scope = StairsEditScope(doc, "MCP stairs")
        stair_id = scope.Start(bl.Id, tl.Id)

        def _mk_run(p0, p1, tag):
            u"""v7.12.1（2026-09-28 实测）：2019 的 StairsRun.CreateStraightRun
            必须在【自开事务】内调用 —— StairsEditScope 不代管事务，缺事务报
            "Modifying is forbidden because the document has no open transaction"。"""
            tr = Transaction(doc, "MCP stairs %s" % tag)
            _install_failure_preprocessor(tr)
            del _MCP_LAST_FAILURES[:]
            tr.Start()
            try:
                StairsRun.CreateStraightRun(doc, stair_id,
                                            Line.CreateBound(p0, p1),
                                            StairsRunJustification.Center)
                st = tr.Commit()
                if to_text(st) != "Committed":
                    raise Exception("commit %s" % to_text(st))
            except Exception:
                try:
                    tr.RollBack()
                except Exception:
                    pass
                raise

        _mk_run(base_p, end_p, "run1")
        try:
            land_len = width * 1.1
            px, py = (-dy, dx)
            start2 = XYZ(end_p.X + dx * land_len + px * width,
                         end_p.Y + dy * land_len + py * width, bl.Elevation)
            end2 = XYZ(start2.X - dx * run, start2.Y - dy * run, bl.Elevation)
            _mk_run(start2, end2, "run2")
        except Exception as e3:
            errs.append("run2 failed: %s" % to_text(e3)[:120])
    except Exception as ex:
        errs.append(to_text(ex)[:220])
        stair_id = None
    if scope is not None:
        # v7.12.3：EditScope 的 Commit 改用**不抑制**的预处理器。
        #   动机：模块级 _MCPFailSuppressor 会在「3 轮仍有余留错误」时返回
        #   ProceedWithRollBack，实测导致 create_stairs 提交后构件消失
        #   （延迟回滚）。此处改为 Continue（不表态），让 EditScope 走自己的
        #   默认裁决，并把失败消息全量带回，便于定位。
        try:
            class _NoSuppress(IFailuresPreprocessor):
                def PreprocessFailures(self, failuresAccessor):
                    try:
                        for fm in failuresAccessor.GetFailureMessages():
                            _MCP_LAST_FAILURES.append(u"%s|%s" % (
                                to_text(fm.GetSeverity()),
                                to_text(fm.GetDescriptionText())))
                    except Exception:
                        pass
                    return FailureProcessingResult.Continue

            _COMMIT_PROC[0] = _NoSuppress()
            scope.Commit(_COMMIT_PROC[0])
        except Exception as ec:
            errs.append("scope.Commit failed: %s" % to_text(ec)[:200])
            stair_id = None
    if stair_id is not None:
        # v7.12.2：Commit 后必须**实证构件存在** —— 只回 "created: 1" 而模型里
        #   并无该元素 = 假成功（2026-09-28 评测实测踩到：id 回读为 None、
        #   OST_Stairs 计数 0）。不存在则明确报失败并落回退。
        _exists = False
        try:
            _exists = (doc.GetElement(stair_id) is not None)
        except Exception as ev:
            errs.append("verify failed: %s" % to_text(ev)[:120])
        try:
            sid = stair_id.IntegerValue
        except Exception:
            sid = stair_id
        if _exists:
            return {"created": 1, "id": sid, "mode": "real stairs",
                    "verified": True, "errors": errs[:5]}
        errs.append("stairs id %s Commit 后不存在（EditScope 未落盘）" % sid)
        stair_id = None
    t = Transaction(doc, "MCP stairs fallback")
    _install_failure_preprocessor(t)
    del _MCP_LAST_FAILURES[:]
    t.Start()
    try:
        ftypes = list(FilteredElementCollector(doc).OfClass(FloorType).ToElements())
        if not ftypes:
            t.RollBack()
            return {"error": "stairs failed; no FloorType for fallback",
                    "errors": errs[:4]}
        ca = CurveArray()
        wv = width / 2.0
        px, py = (-dy, dx)
        p1 = XYZ(sx - px * wv, sy - py * wv, bl.Elevation)
        p2 = XYZ(sx + px * wv, sy + py * wv, bl.Elevation)
        p3 = XYZ(end_p.X + px * wv, end_p.Y + py * wv, bl.Elevation)
        p4 = XYZ(end_p.X - px * wv, end_p.Y - py * wv, bl.Elevation)
        ca.Append(Line.CreateBound(p1, p2))
        ca.Append(Line.CreateBound(p2, p3))
        ca.Append(Line.CreateBound(p3, p4))
        ca.Append(Line.CreateBound(p4, p1))
        fl = doc.Create.NewFloor(ca, ftypes[0], bl, False)
        status = t.Commit()
        if to_text(status) != "Committed":
            return {"error": "stairs fallback commit failed (%s)"
                             % to_text(status), "errors": errs[:4]}
        return {"created": 1, "id": fl.Id.IntegerValue,
                "fallback": "sloped floor (real stairs failed)",
                "errors": errs[:5]}
    except Exception as ex2:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"error": "stairs + fallback both failed",
                "errors": errs[:4] + [to_text(ex2)[:160]],
                "failures": _MCP_LAST_FAILURES[:8]}


def do_create_railing(cmd):
    u"""v7.12.0 修复（2026-09-28 端到端评测实测）：2019 的 Railing.Create
    只有 Create(Document, ElementId railingTypeId, ElementId stairsOrRampId,
    RailingPlacementPosition) —— 语义是「在楼梯/坡道上生成栏杆」，
    **不接受曲线路径**：传 CurveLoop 必抛
    "expected RailingPlacementPosition, got CurveLoop"，传普通标高 id 必抛
    "The stairsOrRampId is not a stairs or ramp element"。
    ⇒ 路径式栏杆走薄墙实现（原回退思路），并把 2019 不存在的
    doc.Create.NewWall 改为 Wall.Create(...) 静态方法。
    path: [[x,y]..](mm), level, height_mm(默认 1100),
    stairs_or_ramp_id(可选；给了才走真实 Railing.Create)。"""
    lvl = _level_by_name(cmd.get("level"))
    if lvl is None:
        return {"error": "level required"}
    pts = cmd.get("path") or []
    if len(pts) < 2:
        return {"error": "path >= 2 points required"}
    height = float(cmd.get("height_mm", 1100)) * _MM
    host_id = cmd.get("stairs_or_ramp_id")
    errs = []
    if host_id:
        t = Transaction(doc, "MCP create_railing(host)")
        _install_failure_preprocessor(t)
        del _MCP_LAST_FAILURES[:]
        t.Start()
        try:
            from Autodesk.Revit.DB.Architecture import Railing
            from Autodesk.Revit.DB.Architecture import RailingType
            from Autodesk.Revit.DB.Architecture import RailingPlacementPosition
            rtypes = list(FilteredElementCollector(doc)
                          .OfClass(RailingType).ToElements())
            if not rtypes:
                raise Exception("no RailingType in doc")
            made = None
            for rt in rtypes[:4]:
                try:
                    made = Railing.Create(doc, rt.Id, ElementId(int(host_id)),
                                          RailingPlacementPosition.Treads)
                    break
                except Exception as ex:
                    errs.append("Railing.Create(%s): %s"
                                % (_type_name(rt), to_text(ex)[:120]))
            if made is None:
                raise Exception("all RailingType failed on host")
            status = t.Commit()
            if to_text(status) == "Committed":
                return {"created": 1, "id": made.Id.IntegerValue,
                        "mode": "railing-on-host", "errors": errs[:4]}
            raise Exception("commit failed (%s)" % to_text(status))
        except Exception as ex:
            errs.append("host path: %s" % to_text(ex)[:180])
        try:
            t.RollBack()
        except Exception:
            pass
    t = Transaction(doc, "MCP create_railing(wall)")
    _install_failure_preprocessor(t)
    del _MCP_LAST_FAILURES[:]
    t.Start()
    try:
        wtype = _wall_type_for(100)
        if wtype is None:
            wts = list(FilteredElementCollector(doc).OfClass(WallType).ToElements())
            if not wts:
                raise Exception("no WallType in doc")
            wtype = wts[0]
        ids = []
        for k in range(len(pts) - 1):
            a = pts[k]
            b = pts[k + 1]
            pa = XYZ(float(a[0]) * _MM, float(a[1]) * _MM, lvl.Elevation)
            pb = XYZ(float(b[0]) * _MM, float(b[1]) * _MM, lvl.Elevation)
            w = Wall.Create(doc, Line.CreateBound(pa, pb), wtype.Id, lvl.Id,
                            height, 0.0, False, False)
            ids.append(w.Id.IntegerValue)
        status = t.Commit()
        if to_text(status) != "Committed":
            return {"error": "railing(wall) commit failed (%s)" % to_text(status),
                    "failures": _MCP_LAST_FAILURES[:8], "errors": errs[:4]}
        return {"created": len(ids), "mode": "thin-wall railing",
                "height_mm": height / _MM, "ids": ids, "errors": errs[:4]}
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"error": "railing + fallback failed",
                "errors": errs[:4] + [to_text(ex)[:180]]}


def do_save_document(cmd):
    u"""v2.11f: 保存/另存。path: 目标 .rvt 路径（未保存文档必填）。
    doc.PathName 与目标不一致（如从模板打开）时走 SaveAs，禁止 Save 回只读模板。"""
    path = to_text(cmd.get("path", "") or "")
    cur = to_text(doc.PathName) if doc.PathName else ""
    if path:
        pl = path.lower().replace("\\", "/")
        cl = cur.lower().replace("\\", "/")
        if not pl.endswith(".rvt"):
            path += ".rvt"
            pl += ".rvt"
        if cl != pl:
            doc.SaveAs(path)
            return {"saved": True, "path": to_text(doc.PathName),
                    "mode": "SaveAs"}
        doc.Save()
        return {"saved": True, "path": to_text(doc.PathName), "mode": "Save"}
    if not doc.PathName:
        return {"error": "document never saved; path required "
                         "(target .rvt)"}
    doc.Save()
    return {"saved": True, "path": to_text(doc.PathName), "mode": "Save"}


def do_set_material(cmd):
    u"""v7.10.0: 给实例赋材质。name(材质名包含字), ids 或 category。
    走 ROOT_MATERIAL_PARAM 实例材质覆盖。"""
    from Autodesk.Revit.DB import BuiltInParameter
    want = to_text(cmd.get("name", ""))
    if not want:
        return {"error": "name required"}
    mat = None
    names = set()
    # Material 收集（OfClass 在写上下文有静默空的老坑 -> 无过滤迭代按类名匹配）
    for e in to_element_list(FilteredElementCollector(doc)
                             .WhereElementIsNotElementType()):
        try:
            if to_text(type(e).__name__) == "Material":
                names.add(to_text(e.Name))
                if mat is None and want in to_text(e.Name):
                    mat = e
        except Exception:
            continue
    if mat is None:
        return {"error": "material not found: %s; available: %s" % (
            want, u", ".join(sorted(names))[:300])}
    ids = cmd.get("ids") or []
    if not ids and cmd.get("category"):
        r = do_get_elements({"category": cmd.get("category"), "limit": 500})
        ids = [e["id"] for e in r.get("elements", [])]
    n = 0
    for i in ids[:500]:
        try:
            e = doc.GetElement(ElementId(int(i)))
            p = e.get_Parameter(BuiltInParameter.ROOT_MATERIAL_PARAM)
            if p is not None and not p.IsReadOnly:
                p.Set(mat.Id)
                n += 1
        except Exception:
            continue
    return {"assigned": n, "material": to_text(mat.Name),
            "id": mat.Id.IntegerValue}


# ---------------------------------------------------------------------------
# v7.11: 施工导览步骤机（借鉴广联达 StepBase：隔离 + 取景 + 导图）
# 实测铁律（2026-09-15）：DisableTemporaryViewMode 与
# IsolateElementsTemporary 都需要事务，且必须在同一个事务里先 Disable
# 再 Isolate——分开调用残留隔离清不掉，ExportImage 出全白图。
# ExportImage 默认输出 .jpg（无视 FilePath 扩展名）。
# ---------------------------------------------------------------------------

GUIDE_VIEW_NAME = u"导览3D"


def _guide_get_view():
    for x in FilteredElementCollector(doc).OfClass(View).ToElements():
        try:
            if x.ViewType == ViewType.ThreeD and to_text(x.Name) == GUIDE_VIEW_NAME:
                return x
        except Exception:
            pass
    return None


def _guide_ensure_view():
    v = _guide_get_view()
    if v is not None:
        return v, None
    from Autodesk.Revit.DB import (
        View3D as _V3D, ViewFamilyType as _VFT, ViewFamily as _VF)
    vft = None
    for t_ in FilteredElementCollector(doc).OfClass(_VFT).ToElements():
        try:
            if t_.ViewFamily == _VF.ThreeDimensional:
                vft = t_
                break
        except Exception:
            pass
    if vft is None:
        return None, "no ViewFamilyType 3D"
    t = Transaction(doc, u"MCP create guide view")
    t.Start()
    try:
        v = _V3D.CreateIsometric(doc, vft.Id)
        v.Name = GUIDE_VIEW_NAME
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return None, to_text(ex)
    return v, None


def _guide_collect(ids, xs, ys, zs, tids):
    """按标高 id 集合收集 墙/板/结构柱/门/窗，附带包围盒（英尺）。"""
    for bc in (BuiltInCategory.OST_Walls, BuiltInCategory.OST_Floors,
               BuiltInCategory.OST_StructuralColumns,
               BuiltInCategory.OST_Doors, BuiltInCategory.OST_Windows):
        for e in FilteredElementCollector(doc).OfCategory(bc).ToElements():
            try:
                lp = e.get_Parameter(BuiltInParameter.WALL_BASE_CONSTRAINT)
                lid = lp.AsElementId() if (
                    lp is not None and lp.AsElementId()) else e.LevelId
                if lid is not None and lid.IntegerValue in tids:
                    ids.Add(e.Id)
                    b = e.get_BoundingBox(None)
                    if b is not None:
                        xs.append(b.Min.X)
                        xs.append(b.Max.X)
                        ys.append(b.Min.Y)
                        ys.append(b.Max.Y)
                        zs.append(b.Min.Z)
                        zs.append(b.Max.Z)
            except Exception:
                continue


def do_apply_step(cmd):
    u"""导览步骤：{step:{levels:["1F",...]} 或 {show_all:true}, pad_mm?}。
    隔离目标标高构件（show_all 恢复全部）+ ZoomAndCenterRectangle 取景。
    实测：Disable+Isolate 必须同一事务，先 Disable 后 Isolate。"""
    step = cmd.get("step") or {}
    levels = step.get("levels") or []
    show_all = bool(step.get("show_all"))
    pad = float(cmd.get("pad_mm", 15000.0)) / 304.8
    v, err = _guide_ensure_view()
    if err:
        return {"error": err}
    uidoc.ActiveView = v
    lmap = {}
    all_lids = set()
    for l in FilteredElementCollector(doc).OfClass(Level).ToElements():
        try:
            lmap[to_text(l.Name)] = l.Id
            all_lids.add(l.Id.IntegerValue)
        except Exception:
            pass
    tids = all_lids if show_all else set()
    if not show_all:
        for nm in levels:
            lid = lmap.get(to_text(nm))
            if lid is not None:
                tids.add(lid.IntegerValue)
    ids = DotNetList[ElementId]()
    xs, ys, zs = [], [], []
    _guide_collect(ids, xs, ys, zs, tids)
    if not show_all and ids.Count == 0:
        return {"error": "no elements for levels: " + ",".join(levels)}
    t = Transaction(doc, u"MCP guide isolate")
    _install_failure_preprocessor(t)
    t.Start()
    try:
        v.DisableTemporaryViewMode(TemporaryViewMode.TemporaryHideIsolate)
        if not show_all:
            v.IsolateElementsTemporary(ids)
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"error": to_text(ex)}
    zoomed = False
    if xs:
        c1 = XYZ(min(xs) - pad, min(ys) - pad, min(zs) - pad)
        c2 = XYZ(max(xs) + pad, max(ys) + pad, max(zs) + pad)
        for uv in uidoc.GetOpenUIViews():
            try:
                if uv.ViewId == v.Id:
                    uv.ZoomAndCenterRectangle(c1, c2)
                    zoomed = True
                    break
            except Exception:
                pass
    return {"isolated": 0 if show_all else ids.Count, "zoomed": zoomed,
            "view": GUIDE_VIEW_NAME}


def do_reset_view(cmd):
    u"""导览复位：清除「导览3D」的临时隐藏/隔离，恢复全部显示。"""
    v = _guide_get_view()
    if v is None:
        return {"error": "guide view missing"}
    uidoc.ActiveView = v
    t = Transaction(doc, u"MCP guide reset")
    t.Start()
    try:
        v.DisableTemporaryViewMode(TemporaryViewMode.TemporaryHideIsolate)
        t.Commit()
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        return {"error": to_text(ex)}
    return {"reset": True, "view": GUIDE_VIEW_NAME}


def do_export_view(cmd):
    u"""导出「导览3D」当前视图为图片。注意：Revit 按内容实际输出
    .jpg（FilePath 扩展名会被忽略），调用方按返回的 saved 路径取文件。"""
    path = cmd.get("path") or ""
    if not path:
        return {"error": "path required"}
    v = _guide_get_view()
    if v is None:
        return {"error": "guide view missing"}
    try:
        if uidoc.ActiveView is None or uidoc.ActiveView.Id != v.Id:
            uidoc.ActiveView = v
    except Exception:
        pass
    from Autodesk.Revit.DB import ImageExportOptions as _IEO, \
        ExportRange as _ER
    io2 = _IEO()
    io2.ExportRange = _ER.CurrentView
    io2.FilePath = path.replace("\\", "/")
    io2.ShouldCreateWebSite = False
    doc.ExportImage(io2)
    saved = path.replace("\\", "/")
    if not saved.lower().endswith(".jpg"):
        saved = saved[:saved.rfind(".")] + ".jpg" if "." in saved \
            else saved + ".jpg"
    return {"saved": saved}


def do_list_steps(cmd):
    u"""读导览任务树 JSON：{path}。返回 {task, steps:[{n,title,desc,
    levels,show_all}]}。纯文件读取，不触 Revit API。"""
    path = cmd.get("path") or ""
    if not path or not os.path.isfile(path):
        return {"error": "path required (guide task tree json)"}
    raw = open(path, "rb").read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    tree = json.loads(raw.decode("utf-8"))
    steps = []
    for s in tree.get("steps", []):
        steps.append({"n": s.get("n"), "title": s.get("title"),
                      "desc": s.get("desc"),
                      "levels": s.get("levels") or [],
                      "show_all": bool(s.get("show_all"))})
    return {"task": tree.get("task"), "steps": steps}


COMMAND_HANDLERS = {
    "ping": do_ping,
    "get_info": do_get_info,
    "get_categories": do_get_categories,
    "get_levels": do_get_levels,
    "get_families": do_get_families,
    "get_elements": do_get_elements,
    "get_element": do_get_element,
    "get_selected": do_get_selected,
    "get_changes": do_get_changes,
    "get_selection": do_get_selection,
    "set_selection": do_set_selection,
    "debug_walls": do_debug_walls,
    "get_wall_geo": do_get_wall_geo,
    "create_floors": do_create_floors,
    "create_roof_gable": do_create_roof_gable,
    "create_stairs": do_create_stairs,
    "create_railing": do_create_railing,
    "save_document": do_save_document,
    "set_material": do_set_material,
    "dwg_to_model": do_dwg_to_model,
    "create_walls": do_create_walls,
    "create_grids": do_create_grids,
    "create_levels": do_create_levels,
    "query_elements": do_query_elements,
    "get_quantities": do_get_quantities,
    "set_parameters": do_set_parameters,
    "batch_count": do_batch_count,
    "run_report": do_run_report,
    "execute_code": do_execute_code,
    "delete_elements": do_delete_elements,
    "create_door_window": do_create_door_window,
    "apply_step": do_apply_step,
    "reset_view": do_reset_view,
    "export_view": do_export_view,
    "list_steps": do_list_steps,
}


# ---------------------------------------------------------------------------
# v7.4: ExternalEvent bridge for WRITE commands
# ---------------------------------------------------------------------------

# Commands that modify the model need a Transaction, and Revit only allows
# transactions inside a valid API context. The WinForms timer tick is on
# the UI thread but NOT an API context (verified 2026-09-10), so these
# commands are queued and executed by BridgeEventHandler.Execute(), which
# Revit calls in a proper API context after ExternalEvent.Raise().
# v7.6.2: get_selection joins - reading uidoc.Selection from the bare
# tick corrupted the client socket (response lost + next recv errored).
# v7.7: create_grids / create_levels join (Transaction writes).
WRITE_COMMANDS = ("create_walls", "create_grids", "create_levels",
                  "set_parameters", "execute_code",
                  "set_selection", "get_selection", "dwg_to_model",
                  "delete_elements", "create_door_window",
                  "create_floors", "create_roof_gable", "create_stairs",
                  "create_railing", "save_document", "set_material",
                  # debug_walls 虽只读，但必须走 ExternalEvent 通道，
                  # 才能与 create_door_window 同上下文复现收集为 0 的问题
                  "debug_walls",
                  # 导览步骤机三原语（v7.11）：视图修改必须走
                  # ExternalEvent API 上下文（事务/临时隐藏/取景均为写操作）
                  "apply_step", "reset_view", "export_view")


# ---------------------------------------------------------------------------
# v7.13.0（2026-10-01，S12）：命令分档 + 策略闸门
#
# 动机：在此之前，整座桥的安全开关**只有 `allow_execute_code` 一个布尔量**，
# 而它管的只是 `execute_code` 一条命令。其余 34 条命令（含 `delete_elements`
# 这种不可逆删除、`save_document` 这种可覆盖任意路径的写）**没有任何策略约束**。
#
# 分三档：
#   read      只读；不改模型（写磁盘的清单导出类不在此桥内）
#   write     有明确语义的写操作
#   dangerous 任意代码执行 / 不可逆删除 —— 能力上等价于"信任调用方"
#
# 三档策略：
#   safe   只允许 read
#   build  允许 read + write（genbuild 之外的常规自动化）
#   dev    全开（**genbuild 需要这一档**，见下）
#
# ⚠️ 关于 genbuild：它的建模配方是 17 处 `execute_code`（vs 7 处专用命令），
#    其中清场/墙型定型/面层/验收在桥里**没有对应命令**。所以"撤掉
#    execute_code 依赖"等于把 17 个宏搬成桥命令并解冻 bridge_core ——
#    那是另一个量级的重构，且与"配方走 execute_code、桥不再改"的既定架构冲突。
#    这里改为**把它变成显式、可见、可审计的选择**，而不是隐性事实。
# ---------------------------------------------------------------------------
COMMAND_TIERS = {
    "read": frozenset([
        "ping", "get_info", "get_categories", "get_levels", "get_families",
        "get_elements", "get_element", "get_selected", "get_changes",
        "get_selection", "debug_walls", "get_wall_geo", "query_elements",
        "batch_count", "run_report", "get_quantities", "list_steps",
    ]),
    "write": frozenset([
        "set_selection", "create_walls", "create_grids", "create_levels",
        "create_floors", "create_roof_gable", "create_stairs",
        "create_railing", "create_door_window", "set_parameters",
        "set_material", "dwg_to_model", "save_document",
        "apply_step", "reset_view", "export_view",
    ]),
    "dangerous": frozenset([
        "execute_code", "delete_elements",
    ]),
}

POLICY_TIERS = {
    "safe": frozenset(["read"]),
    "build": frozenset(["read", "write"]),
    "dev": frozenset(["read", "write", "dangerous"]),
}

# 命令 -> 档 的反查表（启动时构建一次）
_TIER_OF = {}
for _tier_name, _cmds in COMMAND_TIERS.items():
    for _c in _cmds:
        _TIER_OF[_c] = _tier_name


def tier_of(cmd_type):
    u"""返回命令所属档；未分类返回 u"unknown"。

    故意不默认成 read/write —— 未分类命令在 safe/build 下**一律拒绝**（fail-closed），
    只有 dev 放行。新增命令忘了进分档表时，宁可拒绝也不要静默放行。
    """
    return _TIER_OF.get(cmd_type, u"unknown")


def policy_allows(cmd_type, policy=None):
    u"""当前策略是否允许该命令。返回 (allowed, tier, policy)。"""
    pol = policy or POLICY
    allowed_tiers = POLICY_TIERS.get(pol, POLICY_TIERS["build"])
    tier = tier_of(cmd_type)
    return (tier in allowed_tiers, tier, pol)


def unclassified_commands():
    u"""返回 COMMAND_HANDLERS 里没进分档表的命令（启动时自检用）。"""
    return sorted(set(COMMAND_HANDLERS.keys()) - set(_TIER_OF.keys()))


class BridgeEventHandler(IExternalEventHandler):
    """Drains the pending queue inside a valid Revit API context."""

    def Execute(self, uiapp_arg):
        # EXCEPTION ARMOR (same rule as the tick handler): nothing may
        # ever escape into Revit's external-event dispatcher.
        try:
            self._execute(uiapp_arg)
        except Exception as e:
            try:
                with open(CRASH_PATH, "ab") as f:
                    msg = "[{}] ext-event error: {}\n{}\n".format(
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        e, traceback.format_exc())
                    f.write(msg.encode("utf-8", "replace"))
            except Exception:
                pass
        finally:
            # v7.6.4: Execute ran -> no Raise outstanding anymore.
            try:
                sh = _appdomain.GetData(BRIDGE_DATA_KEY)
                if sh is not None:
                    sh["raise_pending"] = False
            except Exception:
                pass

    def _execute(self, uiapp_arg):
        shared = _appdomain.GetData(BRIDGE_DATA_KEY)
        pending = shared.get("pending") if shared else None
        if not pending:
            return

        # Refresh document refs - the active doc may differ from the one
        # captured at button-click time.
        global uiapp, uidoc, doc
        uiapp = uiapp_arg
        uidoc = uiapp_arg.ActiveUIDocument
        if uidoc is None:
            log("ext-event: no active document, rejecting {} "
                "command(s)".format(len(pending)))
            for state, cmd in pending:
                _send(state, {"id": cmd.get("id"),
                              "error": "no active document"})
            del pending[:]
            return
        doc = uidoc.Document

        count = 0
        while pending:
            state, cmd = pending.pop(0)
            if state.get("dead"):
                log("ext-event: drop queued cmd for dead client "
                    "(id={})".format(cmd.get("id")))
                continue
            try:
                result = process_command(cmd, shared)
            except Exception as e:
                log("ext-event command error: {}".format(e))
                result = {"id": cmd.get("id"), "error": to_text(e)}
            # v7.9.0: 每个写命令响应都带文档指纹，AI 端可即时发现
            # "文档被换/未保存"。已在结果里带 doc 的不覆盖。
            try:
                if isinstance(result, dict) and "doc" not in result:
                    result["doc"] = _doc_fingerprint()
                if (cmd.get("type") == "dwg_to_model"
                        and isinstance(result, dict)
                        and "error" not in result
                        and not result.get("doc", {}).get("saved", True)):
                    result["warnings"] = [
                        u"当前文档从未保存过（path 为空）——Revit 关闭/崩溃"
                        u"会丢失全部建模成果，请立即在 Revit 里保存！"]
            except Exception:
                pass
            _send(state, result)
            count += 1
        if count:
            log("ext-event executed {} queued command(s)".format(count))

    def GetName(self):
        return "MCPBridgeEventHandler"


# ---------------------------------------------------------------------------
# Command dispatch (auth enforced by caller)
# ---------------------------------------------------------------------------

def process_command(cmd, shared):
    cmd_type = cmd.get("type", "")
    cid = cmd.get("id")
    log("Processing: {}".format(cmd_type))

    if cmd_type == "shutdown":
        shared["running"] = False
        return {"id": cid, "result": {"status": "shutting_down"}}

    # 先判"命令是否存在"，再判策略 —— 否则打错命令名会被报成"未分类档"，
    # 把"你写错了"和"策略不允许"两种完全不同的情况混为一谈。
    handler = COMMAND_HANDLERS.get(cmd_type)
    if handler is None:
        return {"id": cid, "error": "Unknown command: " + to_text(cmd_type)}

    # v7.13.0 策略闸门。放在这里是因为 process_command 是**唯一**的命令分发点
    # （只读命令直接进来，写命令经 ExternalEvent 后也汇到这里）。
    # 已注册但**未分档**的命令（tier == "unknown"）一律拒绝 —— fail-closed：
    # 新增命令忘了进 COMMAND_TIERS 时，宁可拒绝也不要静默放行。
    allowed, tier, pol = policy_allows(cmd_type)
    if not allowed:
        audit({"event": "denied", "cmd": to_text(cmd_type),
               "tier": to_text(tier), "policy": to_text(pol)})
        log("DENIED cmd={} tier={} policy={}".format(cmd_type, tier, pol))
        return {"id": cid, "error": to_text(
            u"命令 '%s' 属 '%s' 档，当前策略 '%s' 只允许 %s。"
            u"如需放行：改 MCP Bridge.pushbutton\\mcp_bridge_config.json 的 "
            u"policy 字段（safe / build / dev），然后重新点一次 MCP Bridge 按钮。"
            % (cmd_type, tier, pol,
               u"/".join(sorted(POLICY_TIERS.get(pol, ())))))}

    t0 = time.time()
    try:
        result = handler(cmd)
    except Exception as e:
        result = {"error": to_text(e),
                  "traceback": to_text(traceback.format_exc())[:2000]}

    ok = not (isinstance(result, dict) and "error" in result)
    audit({
        "event": "command",
        "cmd": to_text(cmd_type),
        "ok": ok,
        "dur_ms": int((time.time() - t0) * 1000),
        "err": (result.get("error") if not ok else None),
    })

    if ok:
        return {"id": cid, "result": result}
    out = {"id": cid, "error": to_text(result.get("error"))}
    # v7.12.1 修复（2026-09-28 评测实测）：错误信封原只转发 traceback /
    #   failures，把处理函数精心构造的 errors / diag / slope_notes 等诊断
    #   字段**全部丢弃** —— 恰在失败时最有用的信息被吞掉。改为除 error 外
    #   整体转发（列表截 12 项、字符串截 600 字）。
    if isinstance(result, dict):
        for k in result.keys():
            if k == "error":
                continue
            v = result[k]
            try:
                if k == "traceback":
                    out["traceback"] = to_text(v)[:1500]
                elif k == "failures":
                    out["failures"] = v[:10]
                elif isinstance(v, list):
                    out[k] = v[:12]
                elif isinstance(v, (unicode, str)):
                    out[k] = v[:600]
                else:
                    out[k] = v
            except Exception:
                pass
    return out


def _json_safe(o):
    u"""v7.7.8: recursively decode py2 byte-strings (assumed UTF-8) to
    unicode. A mixed str/unicode result dict made json.dumps emit raw
    high bytes that then exploded in the ascii wire encoder
    ("'ascii' codec can't encode character '\\u5730'", 2026-09-11)."""
    if isinstance(o, dict):
        return dict((_json_safe(k), _json_safe(v)) for k, v in o.items())
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    try:
        if isinstance(o, str) and not isinstance(o, unicode):
            try:
                return o.decode("utf-8")
            except Exception:
                return o.decode("utf-8", "replace")
    except NameError:
        pass
    return o


def _send(state, obj):
    u"""Send one JSON line (ASCII wire, plain non-blocking sendall).

    v7.6.3: NO settimeout/setblocking juggling - on this IPY/.NET stack
    toggling socket modes poisoned later recv() (WSAEINVAL 10022 drop,
    2026-09-11). Send failures are logged (wouldblock included; the old
    silent swallow = lost response = client hung for its full timeout).
    Loopback payloads are tiny, so a real wouldblock should never
    occur - and if it does, the log shows it.
    v7.7.8: obj sanitized via _json_safe so the wire is always pure
    ASCII (\\uXXXX escapes); decode errors can no longer kill the
    client - the error is logged and an ASCII-safe error reply is
    sent instead."""
    try:
        # v7.7.9: WIRE IS UTF-8. IronPython's json ignores
        # ensure_ascii=True for some string inputs (raw CJK reached the
        # wire and the ascii encode exploded - 2026-09-11, two probes).
        # utf-8 encoding of a unicode NEVER fails, so the wire is now
        # dumps(ensure_ascii=False) -> encode("utf-8"). Clients already
        # decode UTF-8.
        data = json.dumps(_json_safe(obj), ensure_ascii=False, default=str)
        if isinstance(data, unicode):
            data = data.encode("utf-8")
        data = data + "\n"
        state["conn"].sendall(data)
    except socket.error as se:
        log("send failed: {}".format(se))
        if not _is_wouldblock(se):
            state["dead"] = True
    except Exception as ex:
        log("send failed: {}".format(ex))
        state["dead"] = True


def _handle_line(state, line, shared):
    """One complete newline-terminated JSON line from a client."""
    try:
        cmd = json.loads(line.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        _send(state, {"error": "bad json line"})
        return

    cmd_type = cmd.get("type", "")

    if not state.get("authed"):
        if cmd_type == "auth":
            if TOKEN and cmd.get("token") == TOKEN:
                state["authed"] = True
                audit({"event": "auth", "ok": True})
                _send(state, {"ok": True, "server": "mcp-bridge",
                              "version": BRIDGE_VERSION})
            else:
                audit({"event": "auth", "ok": False})
                _send(state, {"ok": False, "error": "auth failed"})
                state["dead"] = True
        else:
            _send(state, {"error": "auth required "
                                   '(send {"type":"auth","token":"..."} first)'})
            state["dead"] = True
        return

    if cmd_type == "auth":
        _send(state, {"ok": True, "already": True})
        return

    # v7.4: write commands need API context -> queue for ExternalEvent.
    # The response is sent from BridgeEventHandler.Execute().
    if cmd_type in WRITE_COMMANDS:
        ext_event = shared.get("ext_event")
        pending = shared.get("pending")
        if ext_event is None or pending is None:
            _send(state, {"id": cmd.get("id"),
                          "error": "write commands unavailable "
                                   "(external event failed to init)"})
        else:
            pending.append((state, cmd))
            try:
                shared["raise_pending"] = True
                shared["raise_ts"] = time.time()  # v7.7.1 stale self-heal
                ext_event.Raise()
            except Exception as ex:
                shared["raise_pending"] = False
                shared["raise_ts"] = 0
                try:
                    pending.remove((state, cmd))
                except Exception:
                    pass
                _send(state, {"id": cmd.get("id"),
                              "error": "raise failed: {}".format(ex)})
        return

    try:
        result = process_command(cmd, shared)
    except Exception as e:
        log("handle_line error: {}".format(e))
        log(traceback.format_exc())
        result = {"id": cmd.get("id"), "error": to_text(e)}
    _send(state, result)


# ---------------------------------------------------------------------------
# Socket I/O on UI thread
# ---------------------------------------------------------------------------

def make_tick_handler(shared, my_id, timer_ref):
    """Create a timer tick handler that auto-retires when superseded."""

    def _tick_body(sender, event_args):
        # Auto-retire if a newer bridge instance took over
        if shared.get("owner_id", 0) != my_id:
            if not shared.get("retired_logged", False):
                log("Pump {} retiring (superseded by {})".format(my_id, shared.get("owner_id")))
                shared["retired_logged"] = True
            try:
                timer_ref.Stop()
            except Exception:
                pass
            return

        if not shared.get("running", True):
            log("Pump {} shutting down".format(my_id))
            try:
                srv = shared.get("server_sock")
                if srv:
                    srv.close()
            except Exception:
                pass
            try:
                for state in list(shared.get("clients", {}).values()):
                    try:
                        state["conn"].close()
                    except Exception:
                        pass
                shared["clients"].clear()
            except Exception:
                pass
            try:
                timer_ref.Stop()
            except Exception:
                pass
            log("Bridge shut down cleanly")
            return

        # v7.2: the timer interval (100 ms) is the throttle; no extra
        # time-based throttling needed here.
        pumps = shared.get("pumps", 0) + 1
        shared["pumps"] = pumps
        if pumps == 1:
            log("First timer pump ok (owner_id={})".format(my_id))
        elif pumps % 100 == 0:
            log("heartbeat: {} pumps, {} client(s)".format(
                pumps, len(shared.get("clients", {}))))

        server_sock = shared.get("server_sock")
        if not server_sock:
            return

        clients = shared["clients"]

        if shared.get("raise_pending"):
            # v7.6.4: no socket I/O while an ExternalEvent Raise is
            # outstanding - recv() with a pending Raise dies with
            # WSAEINVAL (10022) on this IPY/.NET stack (2026-09-11,
            # three rounds of live forensics). Execute clears the flag.
            # v7.7.1: stale-Raise self-heal - if Execute never ran
            # (event lost), socket I/O would freeze forever; resume
            # after 30s and log loudly.
            ts = shared.get("raise_ts") or 0
            if time.time() - ts > 30:
                shared["raise_pending"] = False
                log("WARN: raise_pending stale >30s - resume socket I/O")
            else:
                return

        try:
            # Accept new connections (non-blocking)
            while True:
                try:
                    conn, addr = server_sock.accept()
                    conn.setblocking(False)
                    fd = conn.fileno()
                    clients[fd] = {"conn": conn, "buf": b"", "authed": False}
                    log("Client connected: {} (fd={})".format(addr, fd))
                except (socket.error, OSError):
                    break

            # Read from existing clients (newline-framed protocol)
            dead_fds = []
            for fd, state in list(clients.items()):
                conn = state["conn"]
                try:
                    data = conn.recv(65536)
                    if not data:
                        dead_fds.append((fd, "fin"))
                        continue
                    state["buf"] += data

                    # process every complete line in the buffer
                    while True:
                        nl = state["buf"].find("\n")
                        if nl < 0:
                            break
                        line = state["buf"][:nl]
                        state["buf"] = state["buf"][nl + 1:]
                        if line.strip():
                            _handle_line(state, line, shared)
                        # v7.7.1: a Raise fired mid-tick (write command
                        # queued). ANY further recv this tick hits
                        # WSAEINVAL 10022 and - worse - gets live
                        # clients killed (live_test_v77 2026-09-11:
                        # T5 bad-token churn + create_levels Raise in
                        # the same tick dropped the MAIN connection).
                        # Stop socket I/O for the rest of the tick;
                        # unread buffers are picked up next tick.
                        if state.get("dead") or shared.get("raise_pending"):
                            break
                    if shared.get("raise_pending"):
                        break
                except socket.error as se:
                    se_errno = getattr(se, "errno", None)
                    if _is_wouldblock(se):
                        continue  # no data yet, keep the client
                    if se_errno == 10022:
                        # v7.7.1: transient WSAEINVAL - a Raise is/was
                        # pending; NOT a dead socket. Keep the client
                        # and let the guard skip the next tick(s).
                        log("recv WSAEINVAL on fd={} - deferred "
                            "(raise_pending={})".format(
                                fd, shared.get("raise_pending")))
                        continue
                    dead_fds.append((fd, "sockerr:{}".format(
                        se_errno or str(se))[:60]))
                except OSError as oe:
                    dead_fds.append((fd, "oserror:{}".format(
                        getattr(oe, "errno", None) or str(oe))[:60]))

                if state.get("dead"):
                    dead_fds.append((fd, "dead-flag"))

            for fd, reason in dead_fds:
                if fd in clients:
                    clients[fd]["dead"] = True
                    try:
                        clients[fd]["conn"].close()
                    except Exception:
                        pass
                    del clients[fd]
                    log("Client dropped (fd={}, reason={}), {} left"
                        .format(fd, reason, len(clients)))

        except Exception as e:
            log("pump error: {}".format(e))
            log(traceback.format_exc())

    def on_tick(sender, event_args):
        # v7.3 EXCEPTION ARMOR: nothing may EVER escape into the WinForms
        # message loop. An escaped UnboundNameException killed a Revit
        # session via the endless .NET unhandled-exception dialog on
        # 2026-09-10. Swallow everything; write to mcp_crash.log instead.
        try:
            _tick_body(sender, event_args)
        except Exception as e:
            try:
                with open(CRASH_PATH, "ab") as f:
                    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                    msg = "[{}] tick error: {}\n{}\n".format(
                        stamp, e, traceback.format_exc())
                    f.write(msg.encode("utf-8", "replace"))
            except Exception:
                pass

    return on_tick


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def start(uiapp_ref):
    """Called by the thin launcher script.py on each button click."""
    global TOKEN, CONFIG, POLICY, uiapp, uidoc, doc, _doc_changed_delegate

    log("=== MCP Bridge v{} starting ===".format(BRIDGE_VERSION))
    log("Python: {}".format(sys.version))

    if uiapp_ref is None:
        log("FATAL: no UIApplication passed from launcher")
        return
    uiapp = uiapp_ref
    uidoc = uiapp.ActiveUIDocument
    if uidoc is None:
        log("FATAL: no active document - open a project, then click again")
        return
    doc = uidoc.Document
    log("Active document: {}".format(doc.Title))

    TOKEN = load_or_create_token()
    CONFIG = load_config()
    if TOKEN is None:
        log("FATAL: cannot setup token file {}".format(TOKEN_PATH))
        return
    log("Token file: {}".format(TOKEN_PATH))
    log("execute_code allowed: {}".format(CONFIG.get("allow_execute_code")))
    # v7.13.0：注入策略档 + 自检分档表。
    # 自检放在启动处，是因为分档表与 COMMAND_HANDLERS 是两份清单，
    # 本项目已经吃过三次"两份清单不一致"的亏（部署排除表/按钮清单/测试清单）。
    POLICY = CONFIG.get("policy", "build")
    _tiers = sorted(POLICY_TIERS.get(POLICY, ()))
    log("policy: {} (允许档: {})".format(POLICY, "/".join(_tiers)))
    _unclassified = unclassified_commands()
    if _unclassified:
        log("WARN: {} 条命令未进分档表，safe/build 下会被拒绝: {}".format(
            len(_unclassified), ", ".join(_unclassified)))
    if POLICY != "dev":
        log("NOTE: 当前非 dev 档 -> execute_code/delete_elements 会被拒绝，"
            "genbuild 配方无法运行")
    log("Audit log: {}".format(AUDIT_PATH))

    # Get or create shared state in .NET AppDomain
    shared = _appdomain.GetData(BRIDGE_DATA_KEY)
    if shared is None:
        shared = {
            "owner_id": 0,
            "running": False,
            "server_sock": None,
            "clients": {},
            "timer": None,
        }
        _appdomain.SetData(BRIDGE_DATA_KEY, shared)

    # Retire any existing bridge
    if shared.get("running"):
        log("Stopping previous bridge instance...")
        shared["running"] = False
        time.sleep(0.2)

    # Clean up old server socket
    old_sock = shared.get("server_sock")
    if old_sock:
        try:
            old_sock.close()
        except Exception:
            pass
        shared["server_sock"] = None
        time.sleep(0.2)

    # Clean up any lingering client connections
    for state in list(shared.get("clients", {}).values()):
        try:
            state["conn"].close()
        except Exception:
            pass
    shared["clients"].clear()

    # Create new server socket with retry
    # v7.6.6: the port stays unusable 10-20+ s after the previous bridge
    # retires "cleanly" (IPY2.7/.NET releases the bound socket late).
    # Real-world evidence (2026-09-11): 07:41 retire -> bind failed through
    # +23 s; 16:09 double-click -> both instances died because the old 6 s
    # window expired before the port freed. 45 x 1 s covers the worst case.
    # v7.9.2: 主动探测代替盲等 —— 每轮先试连 9877，connect 被拒绝说明
    # 旧监听真没了，立刻重试 bind（0.25s 步进）；connect 还通说明旧桥
    # 残留，再等。并把 REUSEADDR 回退从第 4 次提前到第 2 次。
    server_sock = None
    last_err = None
    for attempt in range(60):
        used_excl = False
        try:
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # v7.3: prefer SO_EXCLUSIVEADDRUSE. Windows SO_REUSEADDR lets
            # two Revit PROCESSES both bind 9877 (connections land randomly).
            # EXCLUSIVE makes the second bind fail loudly with a clear error.
            # v7.5-hotfix: a just-closed EXCLUSIVE listener can hold the
            # port for a few hundred ms after same-process retirement, so
            # after early failed EXCLUSIVE attempts fall back to REUSEADDR
            # (same-process supersession is already guarded by the
            # AppDomain owner_id handshake).
            _excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if _excl is not None and attempt < 1:
                server_sock.setsockopt(socket.SOL_SOCKET, _excl, 1)
                used_excl = True
            else:
                server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_sock.bind((HOST, PORT))
            server_sock.listen(5)
            server_sock.setblocking(False)
            log("Server listening on {}:{} (non-blocking, {})".format(
                HOST, PORT, "EXCLUSIVE" if used_excl else "REUSEADDR"))
            break
        # v7.5-hotfix: IPY2.7 raises socket.error (an IOError subclass),
        # which "except OSError" alone does NOT catch - the retry loop
        # never worked and one transient 10048 killed startup (2026-09-10).
        except (socket.error, OSError, IOError) as e:
            last_err = e
            log("Bind attempt {} failed: {}".format(attempt + 1, e))
            try:
                server_sock.close()
            except Exception:
                pass
            server_sock = None
            # v7.9.2: 探测旧监听是否真的消失（refused=端口已释放）
            try:
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.settimeout(0.4)
                probe.connect((HOST, PORT))
                # connect 成功 = 还有活着的监听方，关掉探针再等
                probe.close()
                time.sleep(0.3)
            except Exception:
                # refused/超时都说明旧监听大概率已退场，尽快重试
                time.sleep(0.15)

    if server_sock is None:
        log("FATAL: Could not bind to {}:{} ({})".format(HOST, PORT, last_err))
        return

    # Register new owner
    shared["owner_id"] = shared.get("owner_id", 0) + 1
    my_id = shared["owner_id"]
    shared["server_sock"] = server_sock
    shared["running"] = True
    shared["clients"] = {}
    shared["pumps"] = 0        # v7.2: reset heartbeat state on restart
    shared["retired_logged"] = False

    # v7.4: create the ExternalEvent NOW - we are inside the button click,
    # which IS a valid API context (ExternalEvent.Create requires one).
    shared["pending"] = []
    try:
        handler = BridgeEventHandler()
        ext_event = ExternalEvent.Create(handler)
        shared["ext_event"] = ext_event
        log("ExternalEvent ready - write commands enabled")
    except Exception as e:
        shared["ext_event"] = None
        log("ERROR creating ExternalEvent ({}): write commands "
            "disabled".format(e))
        log(traceback.format_exc())

    # v7.6: DocumentChanged -> incremental change feed (get_changes).
    # Unsubscribe the previous delegate first: every button re-click calls
    # start() again, and each += would add one more duplicate subscription.
    try:
        if _doc_changed_delegate is not None:
            try:
                uiapp.Application.DocumentChanged -= _doc_changed_delegate
            except Exception:
                pass
        _doc_changed_delegate = EventHandler[DocumentChangedEventArgs](
            _on_doc_changed)
        uiapp.Application.DocumentChanged += _doc_changed_delegate
        log("DocumentChanged subscription ready (get_changes enabled)")
    except Exception as e:
        _doc_changed_delegate = None
        log("WARN: DocumentChanged subscribe failed: {}".format(e))

    # v7.2: WinForms Timer drives the pump (100 ms ticks on the UI thread,
    # independent of user input / window focus, fires even during modals)
    timer = Timer()
    timer.Interval = 100
    on_tick = make_tick_handler(shared, my_id, timer)
    try:
        timer.Tick += EventHandler(on_tick)
        timer.Start()
        shared["timer"] = timer
        log("WinForms timer started, 100ms ticks (owner_id={})".format(my_id))
    except Exception as e:
        log("ERROR starting timer: {}".format(e))
        log(traceback.format_exc())

    log("MCP Bridge v{} ready on {}:{} (owner_id={})".format(
        BRIDGE_VERSION, HOST, PORT, my_id))
    log("Connect: auth with token from {}".format(TOKEN_PATH))
    log("NOTE: pyRevit Reload stops the bridge - re-click this button "
        "after every Reload")


# Imported module: never auto-run. The launcher script.py calls start().
