# -*- coding: utf-8 -*-
u"""
bimconv_build.py —— 翻模「构建编排」层：分批事务 + 失败抑制 + 逐构件建设计划。

三件事：
  1) _WarnSuppressor  把 Revit 的几何失败对话框吃掉，避免大批量时「一秒一个弹窗」卡死。
  2) _Batch           真正分批提交事务（每 N 个提交一次并落盘，防大事务原生崩溃）。
  3) create_elements()按计划建构件：先墙（供门/窗寄宿），再其余；逐构件 try/except，
                      失败记为「跳过」并给出中文原因，绝不中断整体。

依赖：bimconv_elements（各构件创建）、bimconv_walls（墙接合与寄宿索引）。
"""

try:
    from pyrevit import DB
except Exception:
    DB = None
# MCP bridge / embedded context: pyrevit import may fail silently -> DB=None.
# Fall back to the direct RevitAPI reference (same fix as patch_lib_db.py).
if DB is None:
    try:
        import clr
        clr.AddReference("RevitAPI")
        from Autodesk.Revit import DB
    except Exception:
        DB = None

from bimconv_elements import (_level_id, _create_grid, _create_wall, _create_column,
                              _create_slab, _create_stair, _create_door_window,
                              _create_room, _create_beam)
from bimconv_walls import _build_wall_index, _join_walls
# v2.8: 实测厚度 → 复用/复制对应厚度墙类型（DWG-240厚 等）。
# model_builder 顶部直连 Autodesk.Revit.DB，headless 导入会炸——必须带守卫，
# 炸了就回退基础墙类型（厚度按类型默认，不劣化旧逻辑）。
try:
    from model_builder import ensure_wall_type
except Exception:
    ensure_wall_type = None
try:
    from bimlib import type_name
except Exception:
    def type_name(et):
        try:
            return et.Name
        except Exception:
            return u""


# FailureHandlingOptions 失败抑制（思路参考开源 manicotti 的 FailureSwallower.cs）：
# 大批量翻模时，个别墙/门/窗会触发「墙重叠」「不能生成墙」「墙稍微偏离了轴」等失败，
# Revit 会弹失败对话框阻塞脚本。这里在 PreprocessFailures 里把**所有**失败消息
# （Warning 与 Error 都删掉）并返回 ProceedWithCommit 强制提交，不再阻塞写入。
# 被删的失败对应的图元 Revit 会自动跳过/回退，属良性噪声。
# 任何环境不支持都安全降级为普通事务（try/except 包裹）。
try:
    from Autodesk.Revit.DB import (IFailuresPreprocessor, FailureProcessingResult,
                                   FailureSeverity)
    _HAVE_FAILURES = True
except Exception:
    _HAVE_FAILURES = False

# v2.5c: 本次 create_elements 期间捕捉到的失败原文（severity|description），
# 供 dbinfo/运行日志诊断「为什么整批回滚」——不再瞎猜。
_LAST_FAILURES = []


if _HAVE_FAILURES:
    try:
        from System.Collections.Generic import List as _NetList
    except Exception:
        _NetList = None

    class _WarnSuppressor(IFailuresPreprocessor):
        u"""批量翻模时，墙重叠/无法连接/图元被反转等几何失败会被 Revit 标记为 Error/Warning
        并反复弹失败对话框，导致脚本卡死、错误报告一秒一个。

        v2.5 修复（真图 05.dwg 实锤：55 全「成功」但落盘增量 0）：
        FailuresAccessor 只有 DeleteWarning（且只对 Warning 生效），没有 DeleteError——
        旧代码对 Error 两连 try 全部抛异常被吞，Error 原样残留，ProceedWithCommit
        无法消化未解析的 Error → Commit 静默回滚（返回 RolledBack 而非抛异常），
        整批构件消失但结果列表仍声称成功。现在：
          - Warning → DeleteWarning 删掉；
          - Error   → SetDefaultResolution() 用默认解法消化（通常是删掉出问题的
            那个图元，其余构件保住），保证事务真正提交。

        v2.5c：每条失败原文记入 _LAST_FAILURES（截断 50 条），落盘日志可见根因。

        v2.6 修复（2026-09-11 反射 RevitAPI.dll 实锤 + 桥端 live test 交叉验证）：
        FailureMessage/FailureMessageAccessor 上根本没有 SetDefaultResolution——
        v2.5 的 Error 消化调用每次都抛 AttributeError 被吞，Error 从未真正消化，
        这正是 05.dwg 批次回滚的根因。正确姿势是 FailuresAccessor.ResolveFailures
        (失败元素 id 集合)（须 IsElementsDeletionPermitted()）：
          - Warning → DeleteWarning 删掉；
          - Error   → ResolveFailures(fm.GetFailingElementIds())，默认解法即
            删除出问题的图元，其余构件保住，事务真正提交。
        另：0 消息的 pass 必须返回 Continue 而非 ProceedWithCommit——
        ProceedWithCommit 声称"已解决全部失败"，0 消息时返回会触发
        Revit 的 6 轮重试后整体回滚（桥端 v7.7.6 真机实测）。

        任何异常都被吞掉，绝不因预处理本身而导致事务失败。

        v2.8b 收敛语义（真图 05.dwg 原生崩溃实录驱动）：
        崩溃链 = 「墙重叠」Warning → DeleteWarning + ProceedWithCommit → Revit
        重生成 → 几何条件未消除 → 警告重挂 → 再删再提交 → 无限循环 → Revit
        666 轮强制终止（ADocumentUndo.cpp）→ 原生崩溃。根因：DeleteWarning 只
        删消息不解决几何。修复：Warning 的 GetFailingElementIds 同样收进 doomed，
        统一 ResolveFailures 应用 Revit 默认解法（墙重叠的默认解法就是删掉
        其中一道墙）——几何条件真正消除，循环收敛。首遍之后若仍只剩无 id 的
        消息，返回 Continue 放弃干预（绝不无限声称"已解决"）。

        v2.8d 终结版（journal.0093 三崩实录：666 循环再现）：
        「墙重叠」这类 Warning 的默认解法是【什么都不做】（UI 点确定后墙保持
        重叠照常提交），ResolveFailures 对它不消解任何几何 → 条件永在 →
        ProceedWithCommit 重生成 → 重挂 → 无限循环。修法：
          - 纯 Warning pass：DeleteWarning（等价 UI 点确定）→ 返回 Continue，
            **绝不返回 ProceedWithCommit**——不声称"已解决"就没有重试循环；
          - Error：ResolveFailures（错误默认解法=删图元，真消解）→
            ProceedWithCommit（诚实声明）；
          - 混合：Error 走 ResolveFailures + Warning 走 DeleteWarning →
            ProceedWithCommit；
          - 有消息但无 id 且非首遍：Continue 放弃干预。
        ProceedWithCommit 只允许配「真消解过的 Error」，这条是三崩换来的铁律。
        """
        def PreprocessFailures(self, failuresAccessor):
            self._pass = getattr(self, '_pass', 0) + 1
            doomed = None
            n_msgs = 0
            n_err = 0
            try:
                doomed = _NetList[DB.ElementId]() if _NetList else None
                for fm in list(failuresAccessor.GetFailureMessages()):
                    n_msgs += 1
                    try:
                        if len(_LAST_FAILURES) < 50:
                            try:
                                _desc = fm.GetDescriptionText()
                            except Exception:
                                _desc = u"?"
                            _LAST_FAILURES.append(u"%s|%s"
                                                  % (fm.GetSeverity(), _desc))
                    except Exception:
                        pass
                    try:
                        if fm.GetSeverity() == FailureSeverity.Warning:
                            # 等价 UI 点确定：删消息、不动几何、不触发重试
                            failuresAccessor.DeleteWarning(fm)
                        elif doomed is not None and \
                                failuresAccessor.IsElementsDeletionPermitted():
                            n_err += 1
                            for eid in fm.GetFailingElementIds():
                                doomed.Add(eid)
                    except Exception:
                        pass
                if doomed is not None and doomed.Count > 0:
                    try:
                        failuresAccessor.ResolveFailures(doomed)
                    except Exception:
                        pass
            except Exception:
                pass
            if n_err > 0 and doomed is not None and doomed.Count > 0:
                # Error 已用默认解法真消解（几何条件消除），诚实声明提交
                return FailureProcessingResult.ProceedWithCommit
            # 纯 Warning（已 DeleteWarning）或无可消解 Error：Continue，
            # 让提交按 UI 语义走完——绝不喂 Revit 重试循环
            return FailureProcessingResult.Continue
else:
    # 无 Failure API（headless 测试台）：占位为 None，保证模块属性始终存在，
    # 使 `from bimconv_build import _WarnSuppressor` 在任何环境都不报错。
    _WarnSuppressor = None


class _Batch(object):
    u"""真正分批提交外层事务（每 size 个提交一次，落盘释放内存，防 Revit 大事务崩溃）。

    IronPython 2.7 兼容（用类属性管理计数，不用 nonlocal）。
    """
    def __init__(self, doc, out, n, size=250):
        self.doc = doc
        self.out = out
        self.n = n
        self.size = size
        self.count = 0
        self.t = None
        self.statuses = []   # v2.5: 每批 Commit 状态留痕（RolledBack 不再静默）
        self._start()

    def _start(self):
        self.t = DB.Transaction(self.doc, u"DWG翻模-批次")
        # 挂失败抑制器：删掉本批次里的几何 Warning/Error，避免预警噪声升级为 fatal abort。
        # 任何环境不支持都安全降级（try/except 包裹），不影响正常写入。
        try:
            if _HAVE_FAILURES:
                opts = self.t.GetFailureHandlingOptions()
                opts.SetFailuresPreprocessor(_WarnSuppressor())
                self.t.SetFailureHandlingOptions(opts)
        except Exception:
            pass
        self.t.Start()

    def _commit(self):
        if self.t is not None and self.t.HasStarted():
            st = None
            try:
                st = self.t.Commit()
            except Exception:
                try:
                    self.t.RollBack()
                except Exception:
                    pass
            # v2.5: Commit 失败时 Revit 返回 RolledBack/Failed 而不抛异常——
            # 旧代码不看返回值，整批构件消失仍报「成功」（真图 05.dwg 实锤）。
            committed = True
            try:
                committed = (st == DB.TransactionStatus.Committed)
            except Exception:
                committed = True
            self.statuses.append(str(st))
            if not committed and self.out is not None:
                self.out.print_md(
                    u"  ⚠️ 批次提交未成功（status=%s）——本批构件可能全部未落盘，"
                    u"结果列表里的「成功」不可信" % st)

    def tick(self):
        u"""计一个构件；满 size 个就提交并开新事务。"""
        self.count += 1
        if self.count % self.size == 0:
            self._commit()
            self._start()
            if self.out is not None:
                self.out.print_md(u"  …已提交 %d / %d（分批落盘，防崩溃）"
                                  % (self.count, self.n))

    def finish(self):
        self._commit()
        if self.out is not None:
            self.out.print_md(u"  …全部写入完成，共 %d 个" % self.count)


def create_elements(doc, planned, out=None, batch_size=None, join_walls=None,
                    door_window_cut=None, safe_mode=False, dbinfo=None):
    u"""按计划建构件，内部自带分批事务（每 batch_size 个提交一次，真正落盘释放内存）。

    逐个 try/except，返回每项的 (是否成功, 说明, 构件对象)。

    性能/稳定性：
      - 类型/族只预取一次并缓存（避免 O(n^2) 全库扫描拖垮 Revit）；
      - 每 batch_size 个提交一个真实事务（SubTransaction 不落盘，大事务是崩溃主因）；
        batch_size 默认随规模自动（>500 用 25，否则 50），可显式传入覆盖；
      - join_walls / door_window_cut 默认随规模自动：计划数 > 200 时自动关闭
        （墙接合 / 门洞布尔是最重的几何运算，容易把 Revit 拖崩），先保骨架；
      - safe_mode=True 时一键全关「墙接合 + 门/窗开洞 + 楼梯」（只出骨架），最不容易崩，
        适合先验证图纸能跑通、再逐步放开重运算；
      - 可选 out（pyRevit output）用于打印进度，防止「无响应」误判。
    """
    if safe_mode:
        # 安全模式：一键全关最重几何运算（接合/门洞/楼梯），只出骨架，最稳
        join_walls = False
        door_window_cut = False
    else:
        if join_walls is None:
            # v2.8c: 接合默认关闭（骨架优先）。真图 05.dwg 两次原生崩溃实录：
            # 转换墙密度高、贴面端点多，JoinGeometry/墙清理在短粗墙垛上触发
            # BlendSection/FaultyAtoms 几何核崩溃。接合只是外观清理，收益
            # 远小于风险；调用方明确传 True 才开启。
            join_walls = False
        if door_window_cut is None:
            door_window_cut = len(planned) <= 200
    if batch_size is None:
        # 事务上限调小：单事务构件数越少，越不容易触发 Revit 大事务原生崩溃，
        # 且一旦某个坏图元漏网，最多只损失一个小批次而非整批回滚。
        batch_size = 25 if len(planned) > 500 else 50
    if safe_mode and out is not None:
        out.print_md(u"  >> 安全模式：只建 轴网+墙+柱+板（门窗/楼梯/接合/开洞全关，最稳）")
    elif (not join_walls) and out is not None:
        out.print_md(u"  >> 骨架优先：墙接合已关闭（v2.8c 默认，防几何核崩溃）")
    if (not door_window_cut) and out is not None and (not safe_mode):
        out.print_md(u"  >> 大规模模式：门/窗改为按点自由放置（不切墙洞），防崩溃")

    if DB is None:
        raise Exception(u"create_elements 只能在 Revit + pyRevit 宿主内运行")

    # ---- 预取类型/族（每个只扫一次，全程缓存）----
    wall_types = list(DB.FilteredElementCollector(doc).OfClass(DB.WallType).ToElements())
    # v2.7: 默认只用基本墙（Basic）。真图 05.dwg 实锤：收集器首项可能是
    # 玻璃幕墙/幕墙类型，_pick 空提示时 pool[0] 直接把整栋楼建成透明幕墙。
    # 优先名含 常规/基本/砖/混凝土 的基本墙，其余基本墙兜底；幕墙彻底出局。
    try:
        _basic_walls = [w for w in wall_types
                        if getattr(w, "Kind", None) == DB.WallKind.Basic]
    except Exception:
        _basic_walls = []
    if _basic_walls:
        _preferred = []
        for w in _basic_walls:
            try:
                _nm = type_name(w) or u""
            except Exception:
                _nm = u""
            if any(k in _nm for k in (u"常规", u"基本", u"砖", u"混凝土",
                                      u"普通", u"普通砖墙")):
                _preferred.append(w)
        wall_types = _preferred + [w for w in _basic_walls
                                   if w not in _preferred]
    floor_types = list(DB.FilteredElementCollector(doc).OfClass(DB.FloorType).ToElements())
    all_syms = list(DB.FilteredElementCollector(doc).OfClass(DB.FamilySymbol).ToElements())
    col_syms = [s for s in all_syms
                if s.Category and (u"\u67f1" in (s.Category.Name or "")
                                   or "column" in (s.Category.Name or "").lower())]
    # 门/窗要用对应族（早期 get_sym 只认柱，会把窗建成立柱——这里拆出专用池）
    door_syms = [s for s in all_syms
                 if s.Category and (u"\u95e8" in (s.Category.Name or "")
                                    or "door" in (s.Category.Name or "").lower())]
    window_syms = [s for s in all_syms
                   if s.Category and (u"\u7a97" in (s.Category.Name or "")
                                      or "window" in (s.Category.Name or "").lower())]

    def _pick(pool, hint):
        if not pool:
            return None
        if hint:
            hl = hint.lower()
            for s in pool:
                if hl in type_name(s).lower():
                    return s
        return pool[0]

    wt_cache, sym_cache, ft_cache = {}, {}, {}

    def get_wt(hint):
        if hint not in wt_cache:
            wt_cache[hint] = _pick(wall_types, hint)
        return wt_cache[hint]

    def get_sym(hint):
        if hint not in sym_cache:
            base = col_syms if col_syms else all_syms
            sym_cache[hint] = _pick(base, hint)
        return sym_cache[hint]

    def get_ft(hint):
        if hint not in ft_cache:
            ft_cache[hint] = _pick(floor_types, hint)
        return ft_cache[hint]

    # v2.8: 厚度→墙类型缓存（同一厚度只 Duplicate 一次；二次运行复用同名类型）
    thk_wt_cache = {}

    def _warn_silent(msg):
        try:
            if out is not None:
                out.print_md(msg)
        except Exception:
            pass

    def wt_for_thickness(base, thk_mm):
        u"""轮廓取中实测厚度 → DWG-{n}厚 墙类型（复用同名 / 首次复制改厚）。"""
        if not thk_mm or ensure_wall_type is None or base is None:
            return base
        key = int(thk_mm)
        if key in thk_wt_cache:
            return thk_wt_cache[key]
        try:
            wt, ok = ensure_wall_type(doc, base, u"DWG-%d厚" % key, key,
                                      _warn_silent)
        except Exception:
            wt, ok = base, False
        result = wt if wt is not None else base
        thk_wt_cache[key] = result
        return result

    def _count_model_elems():
        u"""全模型非类型构件计数（落盘对账基准；失败返回 -1）。"""
        try:
            col = DB.FilteredElementCollector(doc).WherePasses(
                DB.ElementIsElementTypeFilter(True))
            return len(list(col.ToElements()))
        except Exception:
            return -1

    n_before = _count_model_elems()
    del _LAST_FAILURES[:]  # v2.5c: 每轮清空失败记录（只反映本次 create_elements）

    # v2.8b: 厚度墙类型预建（独立小事务，先于一切构件事务）。
    # 崩溃教训：批量构件事务中途 Duplicate/改复合结构会放大失败重试环；
    # 类型先备好，构件事务只引用不修改。
    try:
        _thk_need = sorted(set(int(p["thickness_mm"]) for p in planned
                               if p.get("thickness_mm")))
    except Exception:
        _thk_need = []
    if _thk_need and ensure_wall_type is not None:
        _base_wt = get_wt(u"")
        if _base_wt is not None:
            _setup_t = DB.Transaction(doc, u"DWG翻模-预建厚度墙类型")
            try:
                _setup_t.Start()
                for _k in _thk_need:
                    wt_for_thickness(_base_wt, _k)
                _setup_t.Commit()
            except Exception:
                try:
                    _setup_t.RollBack()
                except Exception:
                    pass

    results = []
    created_walls = []
    walls_with_curves = []  # (墙, 墙曲线) 供门/窗寄宿并自动开洞
    bat = _Batch(doc, out, len(planned), size=batch_size)

    # ---- 预缓存楼层 + 平面视图（避免逐构件全库扫描）----
    _level_cache = {}
    _room_view_cache = {}
    try:
        for lv in DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements():
            _level_cache[lv.Name] = lv.Id
        for v in DB.FilteredElementCollector(doc).OfClass(DB.ViewPlan).ToElements():
            try:
                if v.GenLevel:
                    _room_view_cache[v.GenLevel.Id.IntegerValue] = v
            except Exception:
                pass
    except Exception:
        pass

    # ---- 阶段 A：先建所有墙，并收集墙线曲线（门/窗需寄宿其上）----
    wall_plans = [p for p in planned if p["element_type"] == "wall"]
    other_plans = [p for p in planned if p["element_type"] != "wall"]
    ordered_plans = list(wall_plans) + list(other_plans)  # results 同序，供逐件补救定位

    # v2.5c: 单计划→构件的公共派发（主流程与逐件补救共用）
    def _make_one(plan, win_wall_index=None):
        et = plan["element_type"]
        # v2.8d: 安全模式连门/窗也跳过——宿主窗放置时会自动切墙洞（动墙几何），
        # 与「只出骨架」承诺冲突；三崩教训：任何额外几何处理都可能引爆核失败。
        if safe_mode and et in ("stair", "door", "window", "beam"):
            raise Exception(u"安全模式已跳过：%s" % et)
        if safe_mode and et == "stair":
            raise Exception(u"安全模式已跳过：楼梯")
        level_id = _level_id(doc, plan["level"], _level_cache)
        if level_id is None:
            raise Exception(u"找不到目标楼层「%s」，已跳过" % plan["level"])
        if et == "grid":
            return _create_grid(doc, plan)
        if et == "wall":
            wt = get_wt(plan["type_hint"])
            if not wt:
                raise Exception(u"项目中没有可用的墙类型（WallType），已跳过")
            # v2.8: 双线轮廓取中的实测厚度 → 选/建对应厚度墙类型
            wt = wt_for_thickness(wt, plan.get("thickness_mm"))
            return _create_wall(doc, plan, level_id, wt,
                                collect=created_walls, curves=walls_with_curves)
        if et == "column":
            sym = get_sym(plan["type_hint"])
            if not sym:
                raise Exception(u"项目中没有可用的柱族类型（FamilySymbol），已跳过")
            return _create_column(doc, plan, level_id, sym)
        if et == "stair":
            return _create_stair(doc, plan, level_id)
        if et == "slab":
            ft = get_ft(plan["type_hint"])
            if not ft:
                raise Exception(u"项目中没有可用的楼板类型（FloorType），已跳过")
            return _create_slab(doc, plan, level_id, ft)
        if et in ("door", "window"):
            # v2.9: 不再用 all_syms 兜底——回退会把门建成窗/立柱。
            # 缺族就明确失败，逐件补救记录里能看清原因。
            pool = door_syms if et == "door" else window_syms
            if not pool:
                raise Exception(u"项目中没有%s族（FamilySymbol），已跳过；"
                                u"请载入门/窗族后重试"
                                % (u"门" if et == "door" else u"窗"))
            sym = _pick(pool, plan["type_hint"])
            if not sym:
                raise Exception(u"项目中没有「%s」对应的族类型，已跳过" % et)
            return _create_door_window(doc, plan, level_id, sym, win_wall_index,
                                       cut=door_window_cut)
        if et == "room":
            return _create_room(doc, plan, level_id, _room_view_cache)
        if et == "beam":
            # 2026-10-01：原来直接跳过（"需结构梁系统/线型族"）。
            # 现在与楼梯同法 —— DirectShape 沿梁线挤出长方体，不需要结构梁族。
            # 安全模式仍跳过：它是新几何，不该进"只出骨架"那个承诺里。
            return _create_beam(doc, plan, level_id)
        raise Exception(u"暂不支持的构件类型：%s" % et)

    for plan in wall_plans:
        bat.tick()
        try:
            el = _make_one(plan)
            results.append((True, u"成功", el))
        except Exception as ex:
            results.append((False, u"%s" % ex, None))

    # 墙线空间索引：门/窗就近寄宿快速查找
    wall_index = _build_wall_index(walls_with_curves) if walls_with_curves else None

    # ---- 阶段 B：建其余构件；门/窗寄宿最近墙并自动开洞 ----
    for plan in other_plans:
        bat.tick()
        try:
            el = _make_one(plan, wall_index)
            results.append((True, u"成功", el))
        except Exception as ex:
            results.append((False, u"%s" % ex, None))

    bat.finish()

    # 落盘对账：结果列表声称的成功数 vs 数据库实际增量
    # （V2 对账精神的轻量版；不一致说明有图元没真正落盘）
    n_after = _count_model_elems()
    if dbinfo is not None:
        dbinfo["before"] = n_before
        dbinfo["after"] = n_after
        dbinfo["delta"] = ((n_after - n_before)
                           if (n_before >= 0 and n_after >= 0) else None)
        # v2.5: 每批提交状态（含 RolledBack），与 delta 互为印证
        try:
            dbinfo["commit_status"] = list(bat.statuses)
        except Exception:
            pass
        try:
            dbinfo["failures"] = list(_LAST_FAILURES[:10])
        except Exception:
            pass

    # ---- v2.5c 逐件补救：某些批次的提交被 Revit 整批回滚时（status=RolledBack，
    # 真图 05.dwg 两连 rollback 实锤），只对该批的「声称成功」构件逐件重试——
    # 每件独立事务 + 独立失败抑制，坏一个弃一个，绝不再全军覆没。
    bad_batches = set()
    for _bi, _st in enumerate(bat.statuses):
        if "Committed" not in _st:
            bad_batches.add(_bi)
    if bad_batches and out is not None:
        _size = bat.size or batch_size
        redo = [i for i in range(min(len(results), len(ordered_plans)))
                if results[i][0] and (i // _size) in bad_batches]
        out.print_md(u"  🛟 %d 个批次提交被回滚——启动逐件补救，重试 %d 个"
                     u"（每件独立事务，坏一个弃一个）" % (len(bad_batches), len(redo)))
        _fixed = 0
        for _i in redo:
            _t = DB.Transaction(doc, u"DWG翻模-逐件补救")
            try:
                if _HAVE_FAILURES:
                    _fopts = _t.GetFailureHandlingOptions()
                    _fopts.SetFailuresPreprocessor(_WarnSuppressor())
                    _t.SetFailureHandlingOptions(_fopts)
            except Exception:
                pass
            _t.Start()
            try:
                _el = _make_one(ordered_plans[_i], wall_index)
                _st2 = _t.Commit()
                _ok = True
                try:
                    _ok = (_st2 == DB.TransactionStatus.Committed)
                except Exception:
                    _ok = True
                if _ok:
                    _fixed += 1
                    results[_i] = (True, u"成功", _el)
                else:
                    results[_i] = (False, u"逐件提交仍回滚（%s）" % _st2, None)
            except Exception as _ex:
                try:
                    _t.RollBack()
                except Exception:
                    pass
                results[_i] = (False, u"%s" % _ex, None)
        out.print_md(u"  🛟 逐件补救完成：%d / %d 落盘" % (_fixed, len(redo)))
        # 重算对账（补救后的真实落盘口径）
        _na2 = _count_model_elems()
        n_after = _na2
        if dbinfo is not None:
            dbinfo["after"] = _na2
            dbinfo["delta"] = ((_na2 - n_before)
                               if (n_before >= 0 and _na2 >= 0) else None)

    # 墙段接合：共享端点的墙用 JoinGeometryUtils 接合（独立事务，墙已落盘；大规模自动跳过）
    if join_walls and created_walls:
        try:
            jt = DB.Transaction(doc, u"DWG翻模-墙接合")
            jt.Start()
            n_join = _join_walls(doc, created_walls)
            jt.Commit()
            if out is not None:
                out.print_md(u"  …墙段接合完成（%d 堵墙，接合 %d 处）"
                             % (len(created_walls), n_join))
        except Exception as ex:
            if out is not None:
                out.print_md(u"  ⚠️ 墙段接合部分失败：%s" % ex)
    elif created_walls and out is not None:
        out.print_md(u"  …墙段接合已跳过（大规模/安全模式），墙体未接合"
                     u"（角落可能重叠，可手动或后续细化工具补）")
    return results
