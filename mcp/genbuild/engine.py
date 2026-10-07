# -*- coding: utf-8 -*-
u"""genbuild.engine - 分阶段构建流水线。

旧 gen_build.execute() 260 行巨石按构建阶段拆分, 每阶段一个
_stage_* 函数, 阶段间只通过 pools dict 传递元素 id 池 (材质分组
按名引用池)。阶段顺序与旧版逐一对齐, 行为等价:

  ping -> (takeover 守卫/另存) -> 清场+标高 -> (dwg 骨架) -> 墙 ->
  墙型定型 -> (骨架置换) -> 楼板 -> 楼梯 -> 屋面 -> 栏杆 -> 族加载 ->
  门窗 -> 柱 -> 材质 -> 饰面 -> 保存 -> 验收

RunContext 持有桥客户端/logger/命令序号/材质缓存, 消除旧版全局
可变状态 (LOG / ID / cache={})。
"""
import json
import time

from genbuild import config, macros
from genbuild.bridge import default_client, esc, unwrap
from genbuild.errors import BuildError, EXIT_OK, EXIT_ACCEPT_FAIL

# 兼容导出: 沙箱命令自增序号 (历史探针脚本 `from gen_build import ID`
# 引用同一列表对象; RunContext 也用它, 同进程内序号不冲突)。
ID = [900000]


# ------------------------------------------------------------- 上下文
class RunContext(object):
    u"""一次构建运行的运行时上下文。"""

    def __init__(self, client=None, logger=None):
        self.client = client or default_client()
        self.log = logger
        self._ids = ID
        self.material_avail = None   # 材质探测缓存 (旧 cache={} 反模式修复)

    def next_id(self):
        self._ids[0] += 1
        return self._ids[0]

    # ---- 双命令通道: 沙箱代码 / 桥原生命令 (旧 run()/bridge() 合并)
    def run_code(self, code, label, timeout=config.RUN_CODE_TIMEOUT):
        u"""execute_code 通道: 注入失败预处理器后发沙箱代码。"""
        code = code.replace(u"__PRE__", macros.PRE_SRC)
        cmd = {u"type": u"execute_code", u"id": self.next_id(),
               u"code": esc(code), u"no_transaction": True}
        return self._dispatch(cmd, label, timeout)

    def call_cmd(self, cmd_type, payload, label,
                 timeout=config.RUN_CODE_TIMEOUT):
        u"""桥原生命令通道 (create_walls/save_document/ping 等)。"""
        cmd = {u"type": cmd_type, u"id": self.next_id()}
        cmd.update(payload)
        return self._dispatch(cmd, label, timeout)

    def _dispatch(self, cmd, label, timeout):
        t0 = time.time()
        r = self.client.call(cmd, timeout=timeout)
        res = unwrap(r.get(u"result", r))
        show = json.dumps(res, ensure_ascii=False)
        if self.log:
            # 完整响应进文件 (DEBUG), 截断预览进控制台 (INFO)
            self.log.debug(u"[%s] %s" % (label, show))
            self.log.info(u"[gen:%s] (%.1fs) %s"
                          % (label, time.time() - t0,
                             show[:400] + (u"..." if len(show) > 400
                                           else u"")))
        return res


# --------------------------------------------------------- 材质探测
def pick_material(ctx, pool_keywords, label):
    u"""按关键词从桥可用材质池匹配; 探测结果缓存于 ctx (只探一次)。"""
    if ctx.material_avail is None:
        probe = ctx.call_cmd(u"set_material", {u"name": u"___probe___"},
                             u"材质探测-" + label)
        err = probe.get(u"error", u"") or u""
        avail = []
        if u"available" in err:
            avail = [s.strip() for s in
                     err.split(u"available:")[-1].split(u",") if s.strip()]
        ctx.material_avail = avail
    for kw in pool_keywords:
        for a in ctx.material_avail:
            if kw in a:
                return a
    return None


# ------------------------------------------------------------- 阶段
def _stage_ping(ctx, cfg):
    u"""连通性探测: 返回活动文档名 (dry 模式到此为止)。

    2026-10-01（S12）：顺带核对桥的策略档。genbuild 的建模配方是 17 处
    `execute_code`（清场/墙型定型/面层/验收在桥里没有对应命令），因此**必须**
    桥处于 `dev` 档。在这里提前拦下，比建到一半才被拒要好得多 ——
    dry 模式就能报出来，而且模型一个字节都没动。
    """
    r = ctx.call_cmd(u"ping", {}, u"ping", timeout=config.PING_TIMEOUT)
    doc = (r or {}).get(u"document")
    ver = (r or {}).get(u"version")
    pol = (r or {}).get(u"policy")
    ctx.log.info(u"[ping] 桥=%s 文档=%s 策略档=%s" % (ver, doc, pol))
    if pol and pol != u"dev":
        raise BuildError(
            u"桥当前策略档是 '%s'，而 genbuild 的配方依赖 execute_code"
            u"（属 dangerous 档），在此档下会被拒绝。\n"
            u"修法：把 MCP Bridge.pushbutton\\mcp_bridge_config.json 的 "
            u"\"policy\" 改成 \"dev\"，然后重新点一次 Revit 里的 MCP Bridge 按钮。\n"
            u"（若你并不需要用 genbuild，保持 safe/build 即可 —— 那正是它该在的档位。）"
            % pol)
    return doc


def _stage_takeover(ctx, cfg, doc):
    u"""文档守卫 + takeover 另存。守卫不符抛 BuildError。"""
    exp_doc = cfg.get(u"expect_doc")
    if exp_doc and doc != exp_doc and not cfg.get(u"takeover"):
        raise BuildError(
            u"活动文档 '%s' != 期望 '%s'; 若确认要在当前文档上重建, "
            u"在 yaml 加 takeover: true" % (doc, exp_doc))
    if cfg.get(u"takeover") and cfg.get(u"save_path"):
        # 桥 save_document 对已有路径文档只做 Save(忽略 path/mode),
        # 另存必须走 execute_code 直呼 SaveAs(桥冻结 7.10.4 不改)。
        res = ctx.run_code(macros.save_as(cfg[u"save_path"]), u"接管-另存")
        if not res.get(u"saved"):
            raise BuildError(u"接管 SaveAs 失败: %s" % res)


def _stage_wipe(ctx, cfg):
    u"""清场 + 标高建立/校正。"""
    ctx.run_code(macros.wipe_and_levels(
        config.WIPE_CATS,
        [[l[u"name"], l.get(u"elev")] for l in
         (cfg.get(u"levels") or []) if l.get(u"elev") is not None]),
        u"清场+标高")


def _stage_grids(ctx, cfg):
    u"""轴网 (grids: [{name, from, to}], mm)。WIPE_CATS 含 OST_Grids,
    重跑安全; 无 grids 段时跳过 (旧配方零回归)。"""
    grids = cfg.get(u"grids") or []
    if not grids:
        return
    res = ctx.run_code(macros.grids_create(grids), u"轴网")
    ctx.log.info(u"[轴网] made=%d err=%s" % (
        len(res.get(u"made") or []),
        json.dumps(res.get(u"err") or [], ensure_ascii=False)))


def _stage_dwg(ctx, cfg):
    u"""dwg 骨架翻模 (可选): 桥原生 dwg_to_model。"""
    if not cfg.get(u"dwg"):
        return
    d = cfg[u"dwg"]
    res = ctx.call_cmd(
        u"dwg_to_model",
        {u"json_path": d[u"json_path"],
         u"safe_mode": bool(d.get(u"safe_mode", True))},
        u"dwg骨架", timeout=config.DWG_TIMEOUT)
    if res.get(u"error"):
        raise BuildError(u"dwg 翻模失败: %s" % res.get(u"error"))
    ctx.log.info(u"[dwg] planned=%s ok=%s fail=%s by_type=%s" % (
        res.get(u"planned"), res.get(u"built_ok"),
        res.get(u"built_fail"), res.get(u"by_type")))


def _stage_walls(ctx, cfg, pools):
    u"""建墙 (按 标高/厚/高 分组) -> 墙型定型 -> (dwg 骨架墙置换)。"""
    from genbuild.spec import wall_segs
    wall_groups = []
    groups = {}
    for s in wall_segs(cfg):
        groups.setdefault((s[u"level"], s[u"t"], s[u"h"]), []).append(s)
    for (lvl, t, h), items in groups.items():
        walls = [{u"start": [s[u"seg"][0], s[u"seg"][1]],
                  u"end": [s[u"seg"][2], s[u"seg"][3]],
                  u"height_mm": h, u"thickness_mm": t, u"level": lvl}
                 for s in items]
        res = ctx.call_cmd(u"create_walls", {u"walls": walls},
                           u"墙-%s-t%s" % (lvl, t))
        gids = res.get(u"ids") or []
        wall_groups.append((lvl, t, gids))
        pools.setdefault(u"walls_t%d" % int(t), []).extend(gids)
    wall_ids = [i for _, _, g in wall_groups for i in g]

    # 墙型定型(见 macros.SET_WALL_TYPES 注): 桥按厚度选型在本环境
    # 静默降级 -> 全部墙落到同一基础型; 按池修复 + 单层精确厚度
    wt_groups = [{u"t": int(k[7:]), u"ids": v} for k, v in pools.items()
                 if k.startswith(u"walls_t") and v]
    if wt_groups:
        res = ctx.run_code(macros.set_wall_types(wt_groups), u"墙型定型")
        ctx.log.info(u"[墙型] %s" % json.dumps(res, ensure_ascii=False))

    # 骨架墙置换: 精修墙已建 -> 骨架中中心线被精修墙覆盖>=min_cov 者
    # 删除 (rebuild_05 的 delete_ids 是一次性元素 id, 重建后必然漂移)
    if cfg.get(u"dwg") and (cfg[u"dwg"] or {}).get(u"supersede") \
            and wall_groups:
        sp = cfg[u"dwg"][u"supersede"] or {}
        lvl0 = sp.get(u"level") or wall_groups[0][0]
        segs_all = [list(map(float, s[u"seg"])) for s in wall_segs(cfg)]
        res = ctx.run_code(
            macros.supersede(lvl0, segs_all, wall_ids,
                             tol=sp.get(u"tol", 15),
                             min_cov=sp.get(u"min_cov", 0.95)),
            u"骨架置换")
        ctx.log.info(u"[骨架置换] deleted=%s kept=%s" % (
            res.get(u"n_deleted"), res.get(u"kept")))
    return wall_groups


def _stage_floors(ctx, cfg, pools):
    u"""楼板批量创建; tag 标记的板进独立池 (供后续引用)。"""
    fls = []
    ftags = []
    for f in cfg.get(u"floors") or []:
        fl = {u"points": f[u"points"], u"level": f[u"level"],
              u"structural": bool(f.get(u"structural"))}
        if f.get(u"type"):
            fl[u"type"] = f[u"type"]
        fls.append(fl)
        ftags.append(f.get(u"tag"))
    if not fls:
        return []
    res = ctx.call_cmd(u"create_floors", {u"floors": fls}, u"楼板")
    floor_ids = res.get(u"ids") or []
    for i, tag in enumerate(ftags):
        if tag and i < len(floor_ids):
            pools.setdefault(u"floor_%s" % tag, []).append(floor_ids[i])
    pools[u"floors"] = floor_ids
    return floor_ids


def _stage_stairs(ctx, cfg, pools):
    u"""楼梯逐条 (两跑 + 平台板)。"""
    from genbuild.spec import stair_geometry
    stair_ids = []
    landing_ids = []
    for i, st in enumerate(cfg.get(u"stairs") or []):
        res = ctx.run_code(macros.stairs_u(stair_geometry(st)),
                           u"楼梯%d" % (i + 1))
        if res.get(u"stairs"):
            stair_ids.append(res[u"stairs"])
        if res.get(u"landing"):
            landing_ids.append(res[u"landing"])
    pools[u"stairs"] = stair_ids
    return stair_ids


def _stage_roofs(ctx, cfg, pools):
    u"""多屋面: 兼容旧单 roof + roofs 列表, 逐条按 freeform/gable 发射。

    2026-10-02 改 —— 默认改用 freeform（这是本次缺陷修复的核心）:

      老路 gable_roof 依赖 NewExtrusionRoof/NewFootPrintRoof，已被
      「17 次历史实证 + 本次活体复现」证明在 Revit 2019 上必然失败
      （Revit 内部抛 "Value cannot be null"，参数已核验非空）。
      失败后它会把屋面建成立方楼板并返回一个**正常 id** —— 失败被伪装成成功。
      实测后果：四层别墅、三层社区服务中心的屋面其实都是楼板，一直没人发现。

      而这条路要不要走，原先取决于配方里有没有写一个 **spec 不校验、文档也没有**
      的 `macro: freeform` 键。默认值是一条死路，这是根因。

    现在:
        macro 缺省 / "freeform" -> gable_freeform（双坡体量，归 OST_Roofs）
        macro == "gable"        -> gable_roof（老路，仅在**显式**要求时）
        其它取值                 -> 已由 spec 阶段报错；这里仍按 freeform 兜底
    """
    roof_specs = ([cfg[u"roof"]] if cfg.get(u"roof") else []) \
        + (cfg.get(u"roofs") or [])
    roof_ids = []
    for i, rf in enumerate(roof_specs):
        g = {u"level": rf[u"level"],
             u"axis": rf.get(u"axis", u"x"),
             u"span": rf[u"span"], u"cross": rf[u"cross"],
             u"slope": rf.get(u"slope", 30),
             u"flat": rf.get(u"flat") or [rf[u"span"][0], rf[u"cross"][0],
                                          rf[u"span"][1], rf[u"cross"][1]]}
        tag = u"屋面%d" % (i + 1)
        # 注意：这里是 CPython 侧，没有桥的 to_text，用 %s 转换即可。
        try:
            macro = (u"%s" % (rf.get(u"macro") or u"freeform")).strip().lower()
        except Exception:
            macro = u"freeform"

        if macro == u"gable":
            res = ctx.run_code(macros.gable_roof(g), tag)
            made = res.get(u"made") or {}
            if made.get(u"degraded") == u"floor":
                # 关键：**不能**静默计入成功。这条日志就是本次修复要留下的
                # 证据 —— 将来谁再走老路，一眼能在日志里看到它退化成了楼板。
                ctx.log.info(
                    u"[警告] %s：真屋面 API 全部失败，已退化成【楼板】"
                    u"(id=%s)。它看着像屋面，但进不了屋面明细表、不被屋面"
                    u"过滤器找到，也不能编辑坡度。请改用 macro: freeform。"
                    % (tag, made.get(u"id")))
            elif made.get(u"id"):
                roof_ids.append(made[u"id"])
            else:
                ctx.log.info(u"[警告] %s：老路与兜底均失败：%s"
                             % (tag, made.get(u"err") or u"未知"))
        else:
            if macro != u"freeform":
                ctx.log.info(u"[警告] %s：未知 macro=%s，按 freeform 处理"
                             % (tag, macro))
            # 双坡棱柱 DirectShape，一次成体含双山墙；归 OST_Roofs 类别
            res = ctx.run_code(macros.gable_freeform(g),
                               u"%s-双坡体量" % tag)
            if res.get(u"id"):
                roof_ids.append(res[u"id"])
            else:
                ctx.log.info(u"[警告] %s：freeform 屋面失败：%s"
                             % (tag, res.get(u"err") or u"无返回 id"))

    roof_id = roof_ids[0] if roof_ids else None
    pools[u"roofs"] = roof_ids
    pools[u"roof"] = [roof_id] if roof_id else []
    return roof_id


def _stage_railings(ctx, cfg, pools):
    u"""栏杆逐条 (阳台/平台路径)。"""
    rail_ids = []
    for i, r in enumerate(cfg.get(u"railing") or []):
        res = ctx.run_code(macros.balcony_railing(r[u"level"], r[u"path"]),
                           u"栏杆%d" % (i + 1))
        if res.get(u"id"):
            rail_ids.append(res[u"id"])
    pools[u"railing"] = rail_ids
    return rail_ids


def _stage_families(ctx, cfg):
    u"""族批量加载 (须在开洞前完成)。"""
    if cfg.get(u"families"):
        ctx.run_code(macros.load_families(
            [f[u"path"] for f in cfg[u"families"]]), u"族加载")


def _stage_openings(ctx, cfg):
    u"""门窗逐类放置 + bbz 审计 (是否落在宿主墙 z 范围内)。"""
    audits = []
    ops = cfg.get(u"openings") or []
    for kind in (u"door", u"window"):
        kind_ops = [dict(o, sill=o.get(u"sill", 0)) for o in ops
                    if o[u"kind"] == kind]
        if not kind_ops:
            continue
        res = ctx.run_code(macros.doors_windows(kind, kind_ops),
                           u"门窗-" + kind)
        audits += res.get(u"audit") or []

    bad = [a for a in audits if (a[-1] not in (u"OK",)) or
           (len(a) >= 2 and a[1] in (u"DIED", u"NOBB")) or
           (len(a) >= 6 and a[5] != u"OK")]
    ctx.log.info(u"[审计] 门窗 bbz: n=%d 越界/异常=%d %s" % (
        len(audits), len(bad), json.dumps(bad[:6], ensure_ascii=False)))
    return audits, bad


def _stage_columns(ctx, cfg, pools):
    u"""建筑柱 (门亭柱等): 族须在 families 段加载过。"""
    pools[u"columns"] = []
    for c in (cfg.get(u"columns") or []):
        res = ctx.run_code(macros.place_columns(c),
                           u"柱-" + str(c.get(u"fam", u"?")))
        ctx.log.info(u"[柱] placed=%s audit=%s err=%s" % (
            res.get(u"placed"), res.get(u"audit"), res.get(u"err")))
        pools[u"columns"].extend(res.get(u"ids") or [])


def _stage_materials(ctx, cfg, pools):
    u"""按池批量刷材质 (兼容旧式 {group: [关键词]} dict 写法)。"""
    mats = cfg.get(u"materials") or []
    if isinstance(mats, dict):
        mats = [dict(group=k, pick=v) for k, v in mats.items()]
    for m in mats:
        grp = m.get(u"group")
        kws = m.get(u"pick") or []
        ids = pools.get(grp) or []
        if not ids or not kws:
            ctx.log.info(u"[材质] %s 无对象或无关键词, 跳过" % grp)
            continue
        mat = pick_material(ctx, kws, grp)
        if not mat:
            ctx.log.info(u"[材质] %s 无匹配可用材质, 跳过" % grp)
            continue
        res = ctx.call_cmd(u"set_material", {u"name": mat, u"ids": ids},
                           u"材质-" + grp)
        ctx.log.info(u"[材质] %s <- %s assigned=%s" % (
            grp, mat, res.get(u"assigned")))


def _stage_finishes(ctx, cfg, pools):
    u"""饰面: 墙无 ROOT_MATERIAL_PARAM(assigned=0 真因) -> 走类型
    compound 结构层材质; target:roof/columns 走元素/符号参数。"""
    for f in (cfg.get(u"finishes") or []):
        label = f.get(u"type_name") or f.get(u"target") or u"el"
        mr = ctx.run_code(macros.ensure_material(f.get(u"material") or {}),
                          u"材质确保-" + str(label))
        mid = mr.get(u"id")
        if not mid:
            ctx.log.info(u"[饰面] %s 材质不可用: %s" % (label, mr))
            continue
        if f.get(u"type_name"):
            # MCP-W-<N>mm: 由同厚度墙实例反查类型(绕开 exec 沙箱
            # SystemFamilyType.Name 陷阱), 找不到再退宏内名称匹配
            wid = None
            tn = f[u"type_name"]
            if f.get(u"kind", u"wall") == u"wall" and tn.startswith(
                    u"MCP-W-") and tn.endswith(u"mm"):
                try:
                    wid = (pools.get(u"walls_t%d" % int(tn[6:-2]))
                           or [None])[0]
                except Exception:
                    wid = None
            res = ctx.run_code(macros.finish_type(f.get(u"kind", u"wall"),
                                                  tn, mid, wall_id=wid),
                               u"饰面-" + str(label))
            ctx.log.info(u"[饰面] %s via=%s layer=%s ok=%s err=%s" % (
                label, u"wall#" + str(wid) if wid else u"name",
                res.get(u"layer"), res.get(u"ok"), res.get(u"err")))
        elif f.get(u"target") == u"roof":
            rids = pools.get(u"roofs") or pools.get(u"roof") or []
            if rids:
                res = ctx.run_code(macros.finish_elements(rids, mid),
                                   u"饰面-屋顶体量")
                ctx.log.info(u"[饰面] 屋顶体量 assigned=%s/%s" % (
                    res.get(u"assigned"), len(rids)))
        elif f.get(u"target") == u"columns":
            cids = pools.get(u"columns") or []
            if cids:
                res = ctx.run_code(macros.set_sym_material(cids, mid),
                                   u"饰面-柱")
                ctx.log.info(u"[饰面] 柱 assigned=%s" % res.get(u"assigned"))
        else:
            ctx.log.info(u"[饰面] %s 无 target/type_name, 跳过" % label)


def _stage_save(ctx, cfg):
    u"""保存文档 (save_path 存在时)。"""
    if cfg.get(u"save_path"):
        ctx.call_cmd(u"save_document", {u"path": cfg[u"save_path"]},
                     u"保存", timeout=config.SAVE_TIMEOUT)


def _stage_accept(ctx, cfg, audits, bad):
    u"""验收: VERIFY 计数与 expect 对比。返回退出码。"""
    res = ctx.run_code(macros.VERIFY, u"验收")
    expect = cfg.get(u"expect") or {}
    mapping = {u"walls": u"OST_Walls", u"doors": u"OST_Doors",
               u"windows": u"OST_Windows", u"floors": u"OST_Floors",
               u"roofs": u"OST_Roofs", u"stairs": u"OST_Stairs",
               u"railings": u"OST_StairsRailing",
               u"columns": u"OST_Columns",
               u"generic_models": u"OST_GenericModel"}
    ok = True
    for k, want in expect.items():
        got = res.get(mapping.get(k, u""), u"?")
        flag = u"[OK]" if got == want else u"[FAIL]"
        if got != want:
            ok = False
        ctx.log.info(u"[验收] %s: got=%s want=%s %s" % (k, got, want, flag))
        if k == u"floors" and got != want:
            # 2026-10-02：floors 的不匹配特别容易看错，补一条诊断提示。
            #   实测教训：三层社区服务中心 want=3 got=6，其中 2 个是**楼梯平台**
            #   （Revit 里平台本来就归 OST_Floors），1 个是退化成楼板的屋面。
            #   光看总数分不清"多出来的是合法的平台还是错件"，
            #   导致排查绕了远路 —— 所以把判断方法直接写进日志。
            n_stairs = len(cfg.get(u"stairs") or [])
            ctx.log.info(
                u"[验收]   提示：OST_Floors 含【楼梯平台】（每个双跑楼梯约 1 个）。"
                u"本配方楼梯数=%s；若 got-want 约为该数，多为平台而非错件。"
                u"否则请逐元素查 category/class 定位。" % n_stairs)
    verdict = u"全部达标" if ok and not bad else (
        u"数量达标但门窗审计有异常" if ok else u"与期望不符, 看日志")
    ctx.log.info(u"[结果] %s — %s" % (cfg.get(u"name", u"unnamed"), verdict))
    return EXIT_OK if (ok and not bad) else EXIT_ACCEPT_FAIL


# ------------------------------------------------------------- 主流程
def execute(ctx, cfg, dry=False):
    u"""执行一次构建。返回退出码 (errors.EXIT_*)。

    dry=True: 只 ping 验证桥可达, 不建模 (静态校验已由 cli 完成)。
    """
    t_start = time.time()
    doc = _stage_ping(ctx, cfg)
    if dry:
        ctx.log.info(u"[干跑] 桥可达, dry 模式结束")
        return EXIT_OK

    _stage_takeover(ctx, cfg, doc)
    _stage_wipe(ctx, cfg)
    _stage_dwg(ctx, cfg)
    _stage_grids(ctx, cfg)

    pools = {}
    _stage_walls(ctx, cfg, pools)
    _stage_floors(ctx, cfg, pools)
    _stage_stairs(ctx, cfg, pools)
    _stage_roofs(ctx, cfg, pools)
    _stage_railings(ctx, cfg, pools)
    _stage_families(ctx, cfg)
    audits, bad = _stage_openings(ctx, cfg)
    _stage_columns(ctx, cfg, pools)
    _stage_materials(ctx, cfg, pools)
    _stage_finishes(ctx, cfg, pools)
    _stage_save(ctx, cfg)
    code = _stage_accept(ctx, cfg, audits, bad)
    ctx.log.info(u"[总耗时] %.1fs" % (time.time() - t_start))
    return code
