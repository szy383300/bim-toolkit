# -*- coding: utf-8 -*-
# DWG翻模 (V2)：读 model_plan.json + mapping_rules.csv → 预览 → 确认 → 可验证写入。
# V2 核心变化：事务提交状态核对 + 数据库对账 + 全程日志落盘，杜绝「显示成功但没模型」。
from pyrevit import revit, DB, script
import os
import time
import json

from bimlib import html_table
from bimconvert import (load_rules, plan_from_json, create_elements,
                        RELIABILITY, consolidate_walls, slab_plans_from_wall_loops)


# --------------------------------------------------------------------------- #
# 原生 WinForms 对话框（不使用 pyRevit forms —— WPF 在 2019 legacy loader 下会崩）
# --------------------------------------------------------------------------- #
def _msg_box(msg, title=u"DWG 翻模"):
    u"""原生 MessageBox 弹信息。失败时退回 print，绝不因弹窗崩掉按钮。"""
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons
        MessageBox.Show(msg, title, MessageBoxButtons.OK)
    except Exception:
        print(msg)


def _pick_drawing():
    u"""原生 OpenFileDialog 选 .dwg/.dxf/.json；取消返回 None。

    V2.4：直接选图纸——dwg/dxf 会在后台自动转换成 model_plan.json。
    """
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import OpenFileDialog, DialogResult
        dlg = OpenFileDialog()
        dlg.Title = (u"选择图纸或 JSON（.dwg/.dxf 自动后台转换 → 翻模；"
                     u".json 直接翻模）")
        dlg.Filter = (u"翻模输入 (*.dwg;*.dxf;*.json)|*.dwg;*.dxf;*.json|"
                      u"所有文件 (*.*)|*.*")
        if dlg.ShowDialog() == DialogResult.OK:
            return dlg.FileName
    except Exception as e:
        _msg_box(u"打开文件选择框失败：%s" % to_text_err(e))
    return None


# 后台转换用的外部 Python（ezdxf 纯 Python 库，IronPython 跑不了，
# 依次回退：BIMTOOLKIT_PYTHON 环境变量 → 本机的几个常见安装位置 → 系统 PATH。
# 不写死某台机器的用户名，换机器改环境变量即可，不用动源码。
_HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
_PY_CANDIDATES = [
    os.environ.get("BIMTOOLKIT_PYTHON", ""),
    os.path.join(_HOME, ".workbuddy", "binaries", "python",
                 "envs", "default", "Scripts", "python.exe"),
    os.path.join(_HOME, ".workbuddy", "binaries", "python",
                 "envs", "default", "python.exe"),
    r"D:\Python\python.exe",
    r"C:\Python39\python.exe",
    "python",
]


def _find_python():
    for p in _PY_CANDIDATES:
        if p == "python":
            return p  # fallback to system PATH
        try:
            if p and os.path.isfile(p):
                return p
        except Exception:
            pass
    return "python"


def _run_step(cmd, out, title, log_path):
    u"""跑一个后台转换步骤：输出重定向到文件（避免管道缓冲塞死），
    结束后核对 exit code，失败时把日志尾部打出来。返回是否成功。"""
    out.print_md(u"⏳ %s （后台运行中，图纸大时需几分钟，请勿关闭 Revit）…"
                 % title)
    lf = open(log_path, "wb")
    try:
        import subprocess
        p = subprocess.Popen(cmd, stdout=lf, stderr=lf,
                             cwd=os.path.dirname(cmd[1]))
        p.wait()
    except Exception as e:
        out.print_md(u"❌ 无法启动后台转换：%s" % to_text_err(e))
        return False
    finally:
        try:
            lf.close()
        except Exception:
            pass
    if p.returncode != 0:
        out.print_md(u"❌ %s 失败（exit=%s），日志尾部：" % (title, p.returncode))
        try:
            with open(log_path, "rb") as f:
                data = f.read()
            try:
                text = data.decode("utf-8")
            except Exception:
                try:
                    text = data.decode("mbcs")
                except Exception:
                    text = to_text_err(data[-2000:])
            for line in text.splitlines()[-30:]:
                out.print_md(u"    %s" % line)
        except Exception:
            pass
        return False
    out.print_md(u"✅ %s 完成" % title)
    return True


def _convert_to_json(src, out):
    u"""V2.4 一站式入口：.dwg/.dxf 自动后台转成 model_plan.json。

    转换链与桌面 DWG转JSON.bat 完全同款：
      .dwg → dwg_to_dxf.py（CAD COM / LibreDWG 兜底）→ .dxf
           → dwg_to_json.py → <名>_model_plan.json
      .dxf → dwg_to_json.py
      .json → 原样返回
    成功返回 json 路径，失败返回 None（原因已打印）。
    """
    ext = os.path.splitext(src)[1].lower()
    if ext == ".json":
        return src
    base = os.path.splitext(src)[0]
    here = os.path.dirname(__file__)
    lib = os.path.abspath(os.path.join(here, "..", "..", "..", "lib"))
    tool_json = os.path.join(lib, "dwg_to_json.py")
    tool_dxf = os.path.join(lib, "dwg_to_dxf.py")
    for t in (tool_json, tool_dxf):
        if not os.path.isfile(t):
            _msg_box(u"未找到转换工具：\n%s" % to_text_err(t))
            return None
    py = _find_python()
    json_path = base + u"_model_plan.json"
    log1 = os.path.join(os.environ.get("TEMP", base), u"bw_conv_dxf.log")
    log2 = os.path.join(os.environ.get("TEMP", base), u"bw_conv_json.log")

    if ext == ".dxf":
        # v2.4b: 显式 --out —— dwg_to_json 默认名是 <名>_plan.json，
        # 与按钮约定的 <名>_model_plan.json 错位（2026-09-11 真图首跑实锤）
        ok = _run_step([py, tool_json, src, "--out", json_path],
                       out, u"解析 DXF → JSON", log2)
        if not ok:
            return None
        if not os.path.isfile(json_path):
            _msg_box(u"转换完成但找不到输出：\n%s" % to_text_err(json_path))
            return None
        return json_path

    if ext != ".dwg":
        _msg_box(u"不支持的文件类型：%s（仅 .dwg / .dxf / .json）" % to_text_err(ext))
        return None

    # .dwg：先转 DXF（内部自动走 CAD COM，失败回退 LibreDWG）
    dxf = base + u".dxf"
    if not _run_step([py, tool_dxf, src], out,
                     u"DWG → DXF（自动 SaveAs，含 LibreDWG 兜底）", log1):
        return None
    if not os.path.isfile(dxf):
        _msg_box(u"DWG→DXF 完成但找不到 DXF：\n%s" % to_text_err(dxf))
        return None
    ok = _run_step([py, tool_json, dxf, "--out", json_path],
                   out, u"解析 DXF → JSON", log2)
    if not ok:
        return None
    if not os.path.isfile(json_path):
        _msg_box(u"转换完成但找不到输出：\n%s" % to_text_err(json_path))
        return None
    return json_path


def to_text_err(v):
    u"""异常/对象安全转文本（IronPython 2.7 下 u""+str(中文) 会按 ascii 解码而炸）。"""
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except Exception:
            try:
                return v.decode("mbcs")
            except Exception:
                return u"<无法解码>"
    try:
        return unicode(v)
    except NameError:
        return str(v)
    except Exception:
        try:
            return str(v)
        except Exception:
            return u"<无法转换>"


def _ask_mode(msg, sub):
    u"""三选一：安全 / 常规 / 取消（Yes/No/Cancel）。失败返回 None（不写入）。

    v2.5：安全模式升为【是】首选——常规模式的寄宿开洞+墙接合在真图上
    （05.dwg，2026-09-11）触发过提交期原生崩溃，合成样图不代表真图。
    """
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import (MessageBox, MessageBoxButtons,
                                          DialogResult)
        full = msg + u"\n\n" + sub + (
            u"\n\n【是】= 安全模式（真图首选）\n【否】= 常规模式（真图有崩溃案例，选前请存盘）\n"
            u"【取消】= 不写入")
        res = MessageBox.Show(full, u"DWG 翻模 · 写入模式",
                              MessageBoxButtons.YesNoCancel)
        if res == DialogResult.Yes:
            return u"safe"
        if res == DialogResult.No:
            return u"normal"
        return None
    except Exception:
        return None


def _read_json_unicode(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8-sig"))


def _level_names(doc):
    return [lv.Name for lv in
            DB.FilteredElementCollector(doc).OfClass(DB.Level).ToElements()]


def _focus_result(doc, out):
    u"""写完后新建「干净」三维视图切过去并缩放全图（绕开旧视图的隐藏/截面框）。"""
    try:
        from Autodesk.Revit.UI import PostableCommand
        uidoc = revit.uidoc
        switched = False
        try:
            vfts = [t for t in DB.FilteredElementCollector(doc)
                    .OfClass(DB.ViewFamilyType).ToElements()
                    if t.ViewFamily == DB.ViewFamily.ThreeDimensional]
            if vfts:
                v3 = DB.View3D.CreateIsometric(doc, vfts[0].Id)
                try:
                    v3.Name = u"DWG翻模结果"
                except Exception:
                    pass
                uidoc.ActiveView = v3
                switched = True
        except Exception:
            pass
        if not switched:
            try:
                v3id = doc.GetDefaultElement3DViewId()
                v3 = doc.GetElement(v3id) if v3id else None
                if v3 is not None:
                    uidoc.ActiveView = v3
                    switched = True
            except Exception:
                pass
        try:
            uidoc.PostCommand(PostableCommand.ZoomToFit)
        except Exception:
            pass
        out.print_md(u"👀 %s" % (u"已新建干净三维视图「DWG翻模结果」并缩放至全图"
                                 u"（仍看不到就按 **VV** 查可见性）" if switched
                                 else u"请手动切到三维视图按 **ZF** 缩放全部。"))
    except Exception:
        pass


def main():
    doc = revit.doc
    here = os.path.dirname(__file__)
    out = script.get_output()

    # 1) 选图纸（.dwg/.dxf 后台自动转 JSON；.json 直接用）
    src_path = _pick_drawing()
    if not src_path:
        return
    ext = os.path.splitext(src_path)[1].lower()
    if ext == ".json":
        json_path = src_path
    else:
        out.print_md(u"# 🚀 一站式翻模：后台转换图纸 → 自动建模")
        out.print_md(u"来源图纸：`%s`" % to_text_err(src_path))
        json_path = _convert_to_json(src_path, out)
        if not json_path:
            _msg_box(u"图纸转换失败，详情见输出窗口。")
            return
        out.print_md(u"📄 JSON 就绪：`%s`" % to_text_err(json_path))
    try:
        plan_json = _read_json_unicode(json_path)
    except Exception as e:
        _msg_box(u"JSON 读取失败：%s" % to_text_err(e))
        return

    # 2) 映射规则
    rules_path = os.path.join(here, "mapping_rules.csv")
    if not os.path.isfile(rules_path):
        _msg_box(u"未找到映射规则：\n%s" % to_text_err(rules_path))
        return
    rules = load_rules(rules_path)

    levels = _level_names(doc)
    if not levels:
        _msg_box(u"当前模型没有任何楼层（Level），无法定位构件。请先建好楼层。")
        return

    # 3) 计划 + 墙段合并（含重叠去重）
    planned, unmatched = plan_from_json(plan_json, rules, levels)
    n_wall_before = sum(1 for p in planned if p["element_type"] == "wall")
    planned = consolidate_walls(planned)
    n_wall_after = sum(1 for p in planned if p["element_type"] == "wall")

    # 3.5) V2.4a：无楼板边线的图纸自动出板（墙链环路/墙簇外包两级）
    auto_slabs = slab_plans_from_wall_loops(planned)
    if auto_slabs:
        planned.extend(auto_slabs)
        n_auto = len(auto_slabs)
        tot_m2 = 0.0
        for s in auto_slabs:
            try:
                import re
                m = re.search(r"([\d.]+) 平米", s.get("note", ""))
                if m:
                    tot_m2 += float(m.group(1))
            except Exception:
                pass
        _auto_slab_info = (n_auto, tot_m2)
    else:
        _auto_slab_info = None

    # 4) 预览
    out.print_md(u"# DWG 翻模预览（不写入）")
    out.print_md(u"来源：`%s` ｜ 楼层候选：%s" % (plan_json.get("source", ""), u"、".join(levels)))
    counts = {}
    for p in planned:
        counts[p["element_type"]] = counts.get(p["element_type"], 0) + 1
    if counts:
        rows = [[k, counts[k], RELIABILITY.get(k, "experimental")] for k in counts]
        out.print_md(u"## 计划建模：%d 个" % len(planned))
        out.print_html(html_table([u"类型", u"数量", u"可靠性"], rows))
        if n_wall_after < n_wall_before:
            out.print_md(u"🧱 墙段已合并：**%d → %d** 条连续墙线" % (n_wall_before, n_wall_after))
    if _auto_slab_info:
        out.print_md(u"🏢 图纸无楼板边线，已按建筑轮廓自动生成 **%d** 块楼板（合计 %.0f 平米）——"
                     u"L 形楼的凹口会被外包矩形覆盖，属已知近似。"
                     % (_auto_slab_info[0], _auto_slab_info[1]))
    else:
        out.print_md(u"⚠️ 没有匹配到任何规则——检查 mapping_rules.csv 的层名/块名。")
    if unmatched:
        um = [[u.get("type"), u.get("layer") or u.get("block"), u.get("handle")]
              for u in unmatched[:200]]
        out.print_md(u"## ⚠️ 未匹配（不建）：%d 个" % len(unmatched))
        out.print_html(html_table([u"图元", u"层/块", "handle"], um))

    if not planned:
        _msg_box(u"没有匹配到任何规则，无构件可建。")
        return

    # 4.5) 重复运行闸门：模型里已有墙时，原地重跑会叠墙（爆栈闪退事故源）
    try:
        n_existing = len(list(DB.FilteredElementCollector(doc).OfClass(DB.Wall).ToElements()))
        if n_existing > 0:
            # v2.8d: 硬阻断（原为可点掉的警告）。三崩实录：崩溃后 Revit 文档恢复
            # 会把残墙带回来，新墙叠旧墙 → 「墙重叠」风暴 → 666 循环 → 再崩。
            # 转换图翻模必须落在干净文档里，这条没有商量余地。
            _msg_box(
                u"当前模型里已有 %d 堵墙，拒绝写入。\n\n"
                u"崩溃后 Revit 文档恢复会把上次的残墙带回来，\n"
                u"新墙叠旧墙必然触发「墙重叠」失败风暴并再次崩溃。\n\n"
                u"正确操作：\n"
                u"  1. 关闭当前文档（不要保存）\n"
                u"  2. 新建空白项目（或打开确认无墙的文档）\n"
                u"  3. 重新运行 DWG翻模" % n_existing,
                u"DWG 翻模 · 需要干净文档")
            out.print_md(u"已阻断：模型中已有 %d 堵墙（v2.8d 硬规则：翻模只进干净文档）。" % n_existing)
            return
    except Exception:
        pass

    # 5) 模式选择（v2.5：安全模式为真图首选）
    msg = (u"将建模 %d 个构件。\n可 Ctrl+Z 撤销。" % len(planned))
    sub = (u"● 安全模式（真图首选）：只建 轴网+墙+柱+板（门窗/楼梯全关）\n"
           u"● 常规模式：含门/窗开洞/楼梯；墙接合已全局默认关闭\n"
           u"  （v2.8c：接合在转换图上两次触发 Revit 几何核崩溃，骨架优先）")
    choice = _ask_mode(msg, sub)
    if not choice:
        out.print_md(u"已取消，未写入。")
        return
    safe = (choice == u"safe")
    big = len(planned) > 200

    # 6) 可验证写入（V2）：事务状态核对 + 数据库对账 + 日志落盘
    log_file = os.path.join(os.path.dirname(json_path),
                            u"DWG翻模日志_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
    out.print_md(u"📝 本次运行日志：`%s`（Revit 若崩，看这个文件）" % to_text_err(log_file))
    try:
        dbinfo = {}
        results = create_elements(
            doc, planned, out,
            batch_size=25 if len(planned) > 500 else 50,
            join_walls=False,  # v2.8c: 接合全局关闭（两次崩溃实录，骨架优先）
            door_window_cut=(not big) and (not safe),
            safe_mode=safe, dbinfo=dbinfo)
    except Exception as ex:
        out.print_md(u"# ❌ 写入失败：过程出错，本次未落盘")
        out.print_md(u"错误：%s" % to_text_err(ex))
        out.print_md(u"（V2 不静默回滚，失败必现形。）完整证据在日志：`%s`" % to_text_err(log_file))
        return

    # 7) 结果汇总（口径 = 结果列表 + 落盘对账，不再轻信结果列表）
    n_ok = sum(1 for r in results if r[0])
    n_fail = len(results) - n_ok
    out.print_md(u"## 写入结果：成功 %d / 失败 %d" % (n_ok, n_fail))
    _delta = dbinfo.get("delta")
    if _delta is not None and _delta != n_ok:
        out.print_md(u"# ⚠️ 数据库对账不一致——「成功」不可信！")
        out.print_md(u"落盘增量：%s ｜ 声称成功：%s" % (_delta, n_ok))
        out.print_md(u"**结论：部分构件没有真正落盘。** 把日志发给维护者：`%s`" % to_text_err(log_file))
    elif _delta is not None:
        out.print_md(u"✅ 数据库对账一致：落盘增量 %d = 声称成功 %d" % (_delta, n_ok))
    if n_fail:
        fr = [[i + 1, results[i][1]] for i in range(len(results)) if not results[i][0]]
        out.print_html(html_table(["#", u"失败原因"], fr))

    # 7.2) 运行日志落盘（新契约下 lib 不再写日志，按钮自己补上最小日志）
    try:
        with open(log_file, "wb") as _lf:
            _cs = None
            _fl = None
            try:
                _cs = dbinfo.get("commit_status")
                _fl = dbinfo.get("failures")
            except Exception:
                pass
            _lf.write(to_text_err(
                u"[DWG翻模 %s] planned=%d ok=%d fail=%d delta=%s commits=%s\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S"),
                   len(planned), n_ok, n_fail, _delta, _cs)).encode("utf-8"))
            if _fl:
                _lf.write(to_text_err(
                    u"[失败原文（前%d条，severity|描述）]\n" % len(_fl)).encode("utf-8"))
                for _f in _fl:
                    _lf.write(to_text_err(u"  %s\n" % _f).encode("utf-8"))
            for i, r in enumerate(results):
                if not r[0]:
                    _lf.write(to_text_err(u"  #%d %s\n" % (i + 1, r[1])).encode("utf-8"))
    except Exception:
        pass

    # 7.5) V2.6 质量自评分（读刚落盘的体检报告；读取失败静默跳过，不挡主流程）
    try:
        rp = os.path.splitext(log_file)[0] + u"_report.json"
        if os.path.isfile(rp):
            rep = _read_json_unicode(rp)
            q = rep.get("quality")
            if isinstance(q, dict) and "score" in q:
                b = q.get("breakdown") or {}
                out.print_md(u"## 🎯 翻模质量评分：**%d / 100　%s**" % (
                    q.get("score", 0), q.get("grade", u"")))
                out.print_md(u"扣分明细：写入失败 %d ｜ 失败证据 %d ｜ 重叠墙 %d 对 ｜ "
                             u"碎墙段 %d/%d 堵 ｜ 对账%s" % (
                                 b.get("failed", 0), b.get("failures", 0),
                                 b.get("overlap_pairs", 0), b.get("fragments", 0),
                                 b.get("walls", 0),
                                 u"一致" if not b.get("mismatch") else u"**不一致！**"))
                out.print_md(u"（≥90 优秀 ｜ 75-89 良好 ｜ 60-74 及格 ｜ <60 需人工复核。"
                             u"用「翻模体检」按钮逐条处置可疑项后分数会改善。）")
    except Exception:
        pass

    # 8) 高亮 + 对中
    try:
        from System.Collections.Generic import List
        ids = List[DB.ElementId]()
        for r in results:
            if r[0] and r[2] is not None:
                try:
                    ids.Add(r[2].Id)
                except Exception:
                    pass
        if ids.Count:
            revit.uidoc.Selection.SetElementIds(ids)
            out.print_md(u"🔵 已高亮本次新建的 %d 个构件（点选可看类型）" % ids.Count)
    except Exception:
        pass
    _focus_result(doc, out)


if __name__ == "__main__":
    main()
