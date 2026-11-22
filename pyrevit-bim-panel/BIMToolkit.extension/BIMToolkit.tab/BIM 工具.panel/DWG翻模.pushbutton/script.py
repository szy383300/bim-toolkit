# -*- coding: utf-8 -*-
# DWG翻模 (V2)：读 model_plan.json + mapping_rules.csv → 预览 → 确认 → 可验证写入。
# V2 核心变化：事务提交状态核对 + 数据库对账 + 全程日志落盘，杜绝「显示成功但没模型」。
from pyrevit import revit, DB, script
import os
import sys
import time
import json

# v2.15.1 每次点击强制从磁盘重新加载翻模 lib：pyRevit 的 Reload 清不干净
# 扩展 lib 的 sys.modules 缓存，旧模块在内存里会让所有新功能「看起来无变化」
# （图框路由/图层映射连续三轮真机实锤——磁盘已部署、按钮行为纹丝不动）。
# 清掉后下面的 import 必然拿到磁盘最新版；MCP Bridge 等其它持有旧模块引用
# 的组件互不影响（各自独立实例，模块内无跨模块可变状态）。
for _m in ("bimlib", "bimconv_geom", "bimconv_rules", "bimconv_walls",
           "bimconv_elements", "bimconv_build", "bimconvert",
           "element_factory", "door_schedule"):
    sys.modules.pop(_m, None)

from bimlib import html_table
from bimconvert import (load_rules, plan_from_json, create_elements,
                        RELIABILITY, consolidate_walls, slab_plans_from_wall_loops,
                        column_symbol_name, consolidate_grids,
                        apply_opening_codes, is_by_design_skip,
                        subdrawing_summary)


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
    u"""原生 OpenFileDialog 选 .dwg/.dxf/.json，**可多选**；取消返回 []。

    V2.4：直接选图纸——dwg/dxf 会在后台自动转换成 model_plan.json。
    V2.13：支持多选。一张平面定不出墙高、也定不出"这是几层"，得一次喂一套图，
    每张按文件名归到各自楼层。单选时返回长度为 1 的列表，调用方按列表处理。
    """
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import OpenFileDialog, DialogResult
        dlg = OpenFileDialog()
        dlg.Title = (u"选择图纸或 JSON（可 Ctrl/Shift 多选，一套图一起翻）\n"
                     u".dwg/.dxf 自动后台转换 → 翻模；.json 直接翻模")
        dlg.Filter = (u"翻模输入 (*.dwg;*.dxf;*.json)|*.dwg;*.dxf;*.json|"
                      u"所有文件 (*.*)|*.*")
        dlg.Multiselect = True
        if dlg.ShowDialog() == DialogResult.OK:
            return [f for f in (dlg.FileNames or []) if f]
    except Exception as e:
        _msg_box(u"打开文件选择框失败：%s" % to_text_err(e))
    return []


# 后台转换用的外部 Python（ezdxf 纯 Python 库，IronPython 跑不了，
# 沿用桌面 bat 同款解释器；依次回退）
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
            if os.path.isfile(p):
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


# --------------------------------------------------------------------------- #
# v2.15 图层映射会话：把「猜图层」变成「用户配一次」（红瓦/广联达同款模式）。
# 真机实锤：13152 个图元落在 LAYER1/LAYER2/GROUND/0 这类层名上全 miss。
# 用户映射存在图纸旁 <json>.layermap.json（sidecar），同图下次自动复用。
# --------------------------------------------------------------------------- #
_MAP_CHOICES = [
    (u"忽略（不追加规则）", u""),
    (u"墙 wall", u"wall"),
    (u"柱 column", u"column"),
    (u"门 door", u"door"),
    (u"窗 window", u"window"),
    (u"轴网 grid", u"grid"),
    (u"楼板 slab", u"slab"),
    (u"楼梯 stair", u"stair"),
    (u"梁 beam", u"beam"),
    (u"房间 room", u"room"),
    (u"不建（明确排除）", u"exclude"),
]


def _sidecar_path(json_path):
    return json_path + u".layermap.json"


def _load_sidecar(json_path):
    u"""读图纸旁的映射 sidecar → rules 列表（格式不合法/不存在返回 []）。"""
    try:
        p = _sidecar_path(json_path)
        if not os.path.isfile(p):
            return []
        data = _read_json_unicode(p)
        out = []
        for r in (data.get("rules") or []):
            if (r.get("element_type") or "").strip():
                out.append({
                    "match_type": (r.get("match_type") or "layer").strip().lower(),
                    "pattern": (r.get("pattern") or "*").strip(),
                    "element_type": r["element_type"].strip().lower(),
                    "level": (r.get("level") or "*").strip(),
                    "height": float(r.get("height") or 0),
                    "type_hint": (r.get("type_hint") or "").strip(),
                    "note": (r.get("note") or u"").strip(),
                })
        return out
    except Exception:
        return []


def _save_sidecar(json_path, rules):
    try:
        with open(_sidecar_path(json_path), "wb") as f:
            f.write(json.dumps({"rules": rules, "note":
                                u"DWG翻模图层映射会话产物；删除此文件可重新配置"},
                               ensure_ascii=False).encode("utf-8"))
        return True
    except Exception:
        return False


def _layer_map_dialog(layer_rows, n_small, e_small):
    u"""未识别图层映射对话框（原生 WinForms，IPY2.7）。

    layer_rows: [(layer, 类型集合文字, count)] 按 count 降序。
    返回 {图层名: element_type}（用户勾了至少一项且点确定）或 None（取消/
    对话框异常——异常时静默走原路径，绝不让配置界面挡住翻模）。
    """
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Windows.Forms import (Form, Label, ComboBox, Button,
                                          FlowLayoutPanel, AnchorStyles,
                                          DialogResult, ScrollBars,
                                          CheckedListBox)
        from System.Drawing import Point, Size, ContentAlignment

        frm = Form()
        frm.Text = u"DWG翻模 · 未识别图层映射"
        frm.Size = Size(560, 420)
        frm.StartPosition = 1  # CenterScreen

        head = Label()
        head.Text = (u"这张图有 %d 个图层没命中任何内置规则（下面的图层合计 "
                     u"%d 个图元）。\n为每个图层选一个构件类型，选择会保存在"
                     u"图纸旁（layermap.json），同图下次自动复用。\n"
                     u"拿不准就选「忽略」——按原逻辑处理，不会更糟。"
                     % (len(layer_rows), sum(r[2] for r in layer_rows)))
        head.Size = Size(540, 58)
        head.Location = Point(8, 8)
        frm.Controls.Add(head)

        panel = FlowLayoutPanel()
        panel.Location = Point(8, 70)
        panel.Size = Size(532, 260)
        panel.AutoScroll = True
        panel.FlowDirection = 1  # TopDown
        panel.WrapContents = False

        combos = []
        for lay, etypes, cnt in layer_rows:
            hint = u""
            try:
                from bimconv_rules import layer_hint
                hint = layer_hint(lay) or u""
            except Exception:
                pass
            row = FlowLayoutPanel()
            row.Size = Size(520, 30)
            row.WrapContents = False
            lb = Label()
            lb.Text = u"%s  [%s] ×%d%s" % (lay[:26], etypes, cnt,
                                           (u"  → 建议:%s" % hint) if hint else u"")
            lb.Size = Size(330, 24)
            cb = ComboBox()
            cb.DropDownStyle = 2  # DropDownList
            for name, _v in _MAP_CHOICES:
                cb.Items.Add(name)
            # 有启发式建议的层预选建议项，否则默认「忽略」
            sel = 0
            if hint:
                for i, (name, v) in enumerate(_MAP_CHOICES):
                    if v == hint:
                        sel = i
                        break
            cb.SelectedIndex = sel
            cb.Size = Size(170, 24)
            row.Controls.Add(lb)
            row.Controls.Add(cb)
            panel.Controls.Add(row)
            combos.append((lay, cb))
        frm.Controls.Add(panel)

        if n_small:
            foot = Label()
            foot.Text = u"另有 %d 个小图层（各 <%d 个图元，合计 %d 个）未列出。" % (
                n_small, 20, e_small)
            foot.Size = Size(540, 20)
            foot.Location = Point(8, 334)
            frm.Controls.Add(foot)

        ok = Button()
        ok.Text = u"确定（按选择追加规则）"
        ok.Size = Size(180, 30)
        ok.Location = Point(240, 356)
        cancel = Button()
        cancel.Text = u"取消（全部忽略）"
        cancel.Size = Size(120, 30)
        cancel.Location = Point(430, 356)
        frm.Controls.Add(ok)
        frm.Controls.Add(cancel)
        frm.AcceptButton = ok
        frm.CancelButton = cancel

        result = {}

        def _on_ok(*_a):
            result["ok"] = True
            frm.Close()

        def _on_cancel(*_a):
            frm.Close()

        ok.Click += _on_ok
        cancel.Click += _on_cancel
        frm.ShowDialog()
        if not result.get("ok"):
            return None
        chosen = {}
        for lay, cb in combos:
            idx = cb.SelectedIndex
            if idx is None or idx < 0:
                idx = 0
            val = _MAP_CHOICES[idx][1]
            if val:
                chosen[lay] = val
        return chosen or None
    except Exception as _dlg_ex:
        # v2.15.2 不再静默：对话框炸了要弹窗示警（WinForms 某 API 在 IPY 下
        # 不可用时，用户至少知道为什么没弹映射框）
        try:
            import traceback as _tb
            _msg_box(u"图层映射对话框未能打开（已跳过，翻模继续走原逻辑）：\n%s\n\n%s"
                     % (to_text_err(_dlg_ex),
                        to_text_err(_tb.format_exc()[-600:])))
        except Exception:
            pass
        return None


def _aggregate_unmatched(entities, rules):
    u"""未识别层按「层」聚合（同层多实体类型并成一行）→ 对话框行。"""
    try:
        from bimconv_rules import unmatched_layer_stats
        rows, n_small, e_small = unmatched_layer_stats(entities, rules)
    except Exception as _agg_ex:
        try:
            _msg_box(u"未识别图层统计失败（映射会话已跳过）：\n%s" % to_text_err(_agg_ex))
        except Exception:
            pass
        return [], 0, 0
    by_layer = {}
    order = []
    for lay, et, n in rows:
        d = by_layer.get(lay)
        if d is None:
            d = {"count": 0, "types": []}
            by_layer[lay] = d
            order.append(lay)
        d["count"] += n
        if et not in d["types"]:
            d["types"].append(et)
    out = [(lay, u"/".join(by_layer[lay]["types"]), by_layer[lay]["count"])
           for lay in order]
    out.sort(key=lambda r: -r[2])
    return out, n_small, e_small


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

    # v2.15.2 引擎指纹：一轮真机实验分清「按钮被缓存 / lib 被缓存 / 对话框炸」
    # 三种假设。按钮版本=20261015-A；lib 探针=模块里有没有图层映射新函数。
    try:
        import bimconv_rules as _probe
        _lib_ok = hasattr(_probe, "unmatched_layer_stats")
        _lib_file = getattr(_probe, "__file__", u"?")
    except Exception as _pe:
        _lib_ok = False
        _lib_file = u"import失败:%s" % _pe
    out.print_md(u"🔧 引擎指纹：按钮 **20261015-A** ｜ 图层映射lib=%s ｜ lib来自 `%s`"
                 % (u"✓新" if _lib_ok else u"✗旧", to_text_err(_lib_file)))

    # 1) 选图纸（可多选；.dwg/.dxf 后台自动转 JSON；.json 直接用）
    #    一次选一套：一张平面只能定出平面位置，定不出墙高、也定不出"这是几层"
    #    ——那些信息在文件名、在别的图、在门窗表里。分开喂进来才能各归其层。
    src_paths = _pick_drawing()
    if not src_paths:
        return
    rules_path = os.path.join(here, "mapping_rules.csv")
    if not os.path.isfile(rules_path):
        _msg_box(u"未找到映射规则：\n%s" % to_text_err(rules_path))
        return
    rules = load_rules(rules_path)

    levels = _level_names(doc)
    if not levels:
        _msg_box(u"当前模型没有任何楼层（Level），无法定位构件。请先建好楼层。")
        return

    planned = []
    unmatched = []
    plan_texts = []
    level_report = []
    plan_jsons = []      # v2.14: (文件名, plan_json) —— 图框路由摘要用
    for src_path in src_paths:
        ext = os.path.splitext(src_path)[1].lower()
        if ext == ".json":
            json_path = src_path
        else:
            if len(src_paths) == 1:
                out.print_md(u"# 🚀 一站式翻模：后台转换图纸 → 自动建模")
                out.print_md(u"来源图纸：`%s`" % to_text_err(src_path))
            json_path = _convert_to_json(src_path, out)
            if not json_path:
                _msg_box(u"图纸转换失败：%s\n详情见输出窗口。"
                         % os.path.basename(src_path))
                return
            out.print_md(u"📄 JSON 就绪：`%s`" % to_text_err(json_path))
        try:
            plan_json = _read_json_unicode(json_path)
        except Exception as e:
            _msg_box(u"JSON 读取失败：%s" % to_text_err(e))
            return
        fname = os.path.basename(src_path)
        plan_jsons.append((fname, plan_json))
        plan_texts.extend(plan_json.get("texts") or [])

        # v2.15 图层映射会话：这张图有内置规则不认识的大图层时，
        # 让用户配一次（sidecar 复用）。取消/无未识别/异常 → 零影响。
        extra_rules = _load_sidecar(json_path)
        if extra_rules:
            out.print_md(u"📌 已加载图纸旁的图层映射：`%s`（%d 条用户规则）"
                         % (os.path.basename(_sidecar_path(json_path)),
                            len(extra_rules)))
        elif plan_json.get("entities"):
            _rows, _ns, _es = _aggregate_unmatched(
                plan_json.get("entities") or [], rules)
            if _rows:
                # v2.15.3 预告 + 事后回显：弹窗不再「凭空出现」，取消也有后果说明
                out.print_md(u"🧩 这张图有 **%d** 个内置规则不认识的大图层"
                             u"（合计 **%d** 个图元）——**即将弹出映射窗口**，"
                             u"请为每个图层选构件类型（可选「墙/柱/门窗/轴网/"
                             u"不建/忽略」）："
                             % (len(_rows), sum(r[2] for r in _rows)))
                for _r in _rows:
                    out.print_md(u"　`%s` [%s] ×%d" % (_r[0], _r[1], _r[2]))
                _chosen = _layer_map_dialog(_rows, _ns, _es)
                if _chosen:
                    from bimconv_rules import rules_from_layer_map
                    extra_rules = rules_from_layer_map(_chosen)
                    if _save_sidecar(json_path, extra_rules):
                        out.print_md(u"📌 图层映射已保存到图纸旁：`%s`"
                                     u"（删除它可重新配置）"
                                     % os.path.basename(
                                         _sidecar_path(json_path)))
                    for _lay, _et in _chosen.items():
                        out.print_md(u"　`%s` → **%s**" % (_lay, _et))
                else:
                    out.print_md(u"⏭️ 图层映射窗口被关闭/取消——本次按内置规则"
                                 u"跑，上面这些图层（共 %d 个图元）**不会建**。"
                                 u"想配置就重点一次按钮，窗口会再弹。"
                                 % sum(r[2] for r in _rows))
        rules_eff = (extra_rules or []) + rules

        p_part, u_part = plan_from_json(plan_json, rules_eff, levels,
                                        file_hint=fname)
        planned.extend(p_part)
        unmatched.extend(u_part)
        _b = {}
        for _p in p_part:
            _b[_p.get("level_basis") or u"?"] = _b.get(_p.get("level_basis") or u"?", 0) + 1
        _lv = {}
        for _p in p_part:
            _lv[_p.get("level") or u"?"] = _lv.get(_p.get("level") or u"?", 0) + 1
        level_report.append((fname, _b, _lv))

    # 3) 计划 + 墙段合并（含重叠去重）
    #    门窗编号就近配对：能按分米命名解码的直接拿到真实洞口宽高。
    all_texts = []
    for _t in (plan_texts or []):
        all_texts.append(_t)
    planned, code_stats = apply_opening_codes(planned, all_texts)
    n_wall_before = sum(1 for p in planned if p["element_type"] == "wall")
    planned = consolidate_walls(planned)
    n_wall_after = sum(1 for p in planned if p["element_type"] == "wall")
    # 轴网同样要归并：真机实测 3231 条轴网只有 1172 条不同定位线（64% 精确重复），
    # 且施工图按开间逐段画轴线，一条真轴线被拆成几段 → 气泡糊成一片。
    n_grid_before = sum(1 for p in planned if p["element_type"] == "grid")
    planned = consolidate_grids(planned)
    n_grid_after = sum(1 for p in planned if p["element_type"] == "grid")

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
    out.print_md(u"本次输入 **%d** 张图纸 ｜ 项目楼层候选：%s"
                 % (len(src_paths), u"、".join(levels)))
    # 每张图落到哪一层、是"怎么定的"必须摊开说：平面图本身不含层号信息，
    # 猜错一整层的墙就全叠到别的层上，比不建还难查。
    _lr = []
    for _fn, _basis, _lv in level_report:
        _lr.append([_fn,
                    u"、".join(u"%s×%d" % (k, v) for k, v in sorted(_lv.items())),
                    u"、".join(u"%s×%d" % (k, v) for k, v in sorted(_basis.items()))])
    if _lr:
        out.print_html(html_table([u"图纸", u"落到楼层", u"定层依据"], _lr))
    _fallback = sum(sum(v for k, v in _b.items() if u"兜底" in k)
                    for _f, _b, _l in level_report)
    if _fallback:
        out.print_md(u"⚠️ **%d** 个构件的楼层是**兜底取首个楼层**（文件名和图层名都读不出层号）。"
                     u"多层图这样会整层叠在一起——请把文件名改成带层号的（如「三层平面图.dwg」）"
                     u"或在 mapping_rules.csv 的 level 列写死。" % _fallback)

    # v2.14 图框路由摘要（Stage 4）：一张 DWG 多张子图按「图框+框内图名」归层，
    # 立面/剖面/详图框整框排除，层号读不出的框拒绝——拒绝是按用户拍板的
    # 「宁可不建也不放错层」，数字必须如实摊开。
    try:
        from bimconv_rules import frame_routes as _frame_routes
        for fname, _pj in plan_jsons:
            _frs = _frame_routes(_pj.get("frames") or [],
                                 _pj.get("texts") or [], levels)
            if not _frs:
                continue
            _n_plan = sum(1 for r in _frs if r["status"] == "plan")
            _n_exc = sum(1 for r in _frs if r["status"] == "exclude")
            _n_rej = sum(1 for r in _frs if r["status"] == "reject")
            _n_noev = sum(1 for r in _frs if r["status"] == "no_evidence")
            out.print_md(u"🗺 **图框路由** `%s`：%d 框 → 可建平面 **%d** ｜ "
                         u"排除非平面(立面/剖面/详图/说明) %d ｜ 拒绝(层号不可辨) %d ｜ "
                         u"无图名证据 %d" % (fname, len(_frs), _n_plan, _n_exc,
                                             _n_rej, _n_noev))
        _b_all = {}
        for _p in planned:
            _k = (_p.get("level_basis") or u"?")[:6]
            _b_all[_k] = _b_all.get(_k, 0) + 1
        _routed = sum(v for k, v in _b_all.items() if k.startswith(u"按图框"))
        _near = sum(v for k, v in _b_all.items() if k.startswith(u"图名就近"))
        _fb = sum(v for k, v in _b_all.items() if k.startswith(u"兜底"))
        if _routed or _near:
            out.print_md(u"　构件归层：**按图框图名 %d** ｜ 图名就近 %d ｜ "
                         u"其它依据 %d ｜ 兜底 %d（兜底＝文件名与图名都读不出层号，"
                         u"多层图会叠层）"
                         % (_routed, _near,
                            len(planned) - _routed - _near - _fb, _fb))
    except Exception:
        pass

    # v2.15 楼层表预检：图纸图名声称画了哪些层 vs 项目楼层表有没有。
    # 真机实锤：项目楼层表从 V3-L4 起，图纸有 1~3 层平面——缺层的图元
    # 对不上层号而全数拒建。BIMMake「识别楼层表」的轻量版。
    try:
        from bimconv_rules import missing_levels_report
        for fname, _pj in plan_jsons:
            _miss, _samples = missing_levels_report(
                _pj.get("texts") or [], levels)
            if _miss:
                _ex = u"、".join(u"%d层(「%s」)" % (n, _samples[n])
                                for n in _miss[:6])
                out.print_md(u"⚠️ **楼层表预检**：`%s` 的图名里出现这些层号，"
                             u"但项目楼层表里**没有**对应楼层：%s。"
                             u"缺层的构件会因对不上层号被拒建——请先在项目里"
                             u"补齐楼层再重跑（或确认这些层确实不需要）。"
                             % (fname, _ex))
    except Exception:
        pass

    # 图面连通簇：一张 DWG 里常常并排画着十几张图（各层平面+立面+剖面）。
    # 现在的归位单位是「文件」，所以这些图会被整体当成一张平面图建进同一层。
    # 真机实测（2026-10-11 高层住宅「平立面图.dwg」）：19789 条墙线分成 30 个
    # 连通簇，计划却只有一个楼层 → 全叠一层 →「墙重叠」风暴。这条必须说在前面，
    # 否则用户看到的只是"一坨墙 + 满屏失败"，不知道是叠的。
    _n_sub, _n_in, _n_all = subdrawing_summary(planned)
    _n_lv = len(set(p.get("level") or u"" for p in planned))
    if _n_sub > 1:
        out.print_md(u"🗺 **图面按位置连通分出 %d 张独立子图**（%d / %d 个图元），"
                     u"而计划只用到 **%d** 个楼层。当前版本按「一个文件 = 一层」归位："
                     u"同一张 DWG 里并排的平面/立面/剖面会被**当成同一张平面图叠建在同一层**，"
                     u"模型必然出现「墙重叠」。要出可用模型，请一次只喂**一张平面图**"
                     u"并把文件名改成带层号的（如「三层平面图.dwg」）；"
                     u"自动按子图拆分归层还在做。"
                     % (_n_sub, _n_in, _n_all, _n_lv))
    counts = {}
    for p in planned:
        counts[p["element_type"]] = counts.get(p["element_type"], 0) + 1
    if counts:
        rows = [[k, counts[k], RELIABILITY.get(k, "experimental")] for k in counts]
        out.print_md(u"## 计划建模：%d 个" % len(planned))
        out.print_html(html_table([u"类型", u"数量", u"可靠性"], rows))
        if n_wall_after < n_wall_before:
            out.print_md(u"🧱 墙段已合并：**%d → %d** 条连续墙线" % (n_wall_before, n_wall_after))
        _op = code_stats.get("openings", 0)
        if _op:
            out.print_md(u"🔤 门窗编号：**%d / %d** 个洞口在图面上找到了编号"
                         u"（其中 %d 个按分米命名解出真实洞口宽高，%d 个编号太短"
                         u"需查门窗表），**%d 个没有任何编号**——这些仍按规则默认尺寸建，"
                         u"是猜的。" % (code_stats.get("coded", 0), _op,
                                       code_stats.get("decoded", 0),
                                       code_stats.get("coded", 0)
                                       - code_stats.get("decoded", 0),
                                       code_stats.get("uncoded", 0)))
        if n_grid_after < n_grid_before:
            out.print_md(u"📐 轴网已归并：**%d → %d** 条（去重复 + 把按开间画的共线轴段"
                         u"接成连续轴线）" % (n_grid_before, n_grid_after))
        if counts.get("column"):
            # 柱类型是全局挑一个套到所有柱上，不写出来就等于静默决定：
            # 真机实测 1733 根柱被统一成 UC305x305x97 一根型钢而预览没提。
            _csy = column_symbol_name(revit.doc)
            out.print_md(u"🏗 柱将**统一**使用族类型 **%s**（规则未指定类型，"
                         u"施工图柱多为混凝土；需要别的类型请在 mapping_rules.csv "
                         u"的 type_hint 列填写后重跑）"
                         % (_csy or u"⚠️ 项目里没有柱族，柱会逐个明确失败"))
            _sz = {}
            for _p in planned:
                if _p.get("element_type") == "column" and _p.get("width_mm"):
                    _k = u"%dx%d" % (_p["width_mm"], _p["depth_mm"])
                    _sz[_k] = _sz.get(_k, 0) + 1
            if _sz:
                _top = sorted(_sz.items(), key=lambda kv: -kv[1])[:6]
                out.print_md(u"📏 其中 **%d** 根柱的截面已从图中实测（天正柱符号是 "
                             u"1×1mm 方块按插入缩放放大，所以尺寸取得到）：%s"
                             % (sum(_sz.values()),
                                u"、".join(u"%s mm ×%d 根" % (k, v) for k, v in _top)))
            else:
                out.print_md(u"⚠️ 本图柱**量不到截面**（块内没有可炸开的实心几何），"
                             u"全部 %d 根将用同一个族类型——这是如实报告，不是已正确分型"
                             % counts.get("column", 0))
    if _auto_slab_info:
        out.print_md(u"🏢 图纸无楼板边线，已按建筑轮廓自动生成 **%d** 块楼板（合计 %.0f 平米）——"
                     u"L 形楼的凹口会被外包矩形覆盖，属已知近似。"
                     % (_auto_slab_info[0], _auto_slab_info[1]))
    else:
        out.print_md(u"⚠️ 没有匹配到任何规则——检查 mapping_rules.csv 的层名/块名。")
    if unmatched:
        # 分两栏：「明确不建」是设计决定（如屋面），「规则没覆盖」才是要修的缺陷。
        # 以前合成一句"未匹配（不建）"，一个像漏洞、一个像决定，读的人分不出来。
        _excl = [u for u in unmatched if u.get("excluded")]
        _miss = [u for u in unmatched if not u.get("excluded")]
        if _excl:
            out.print_md(u"## ℹ️ 明确不建：%d 个（设计决定，非规则遗漏）" % len(_excl))
            _ex_lay = {}
            for u in _excl:
                k = u.get("type") or u.get("layer") or u"?"
                _ex_lay[k] = _ex_lay.get(k, 0) + 1
            out.print_html(html_table([u"类别（图层）", u"图元数"],
                                       [[k, v] for k, v in
                                        sorted(_ex_lay.items(),
                                               key=lambda kv: -kv[1])[:15]]))
        if _miss:
            um = [[u.get("type"), u.get("layer") or u.get("block"), u.get("handle")]
                  for u in _miss[:200]]
            out.print_md(u"## ⚠️ 规则未覆盖：%d 个" % len(_miss))
            out.print_md(u"_这些图层名没命中任何规则。若其中有该建的构件，"
                         u"请往 `mapping_rules.csv` 补层名模式。_")
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
    if _n_sub > 1:
        # 弹窗是写入前最后一道闸：预览里那条"多张子图会叠在同一层"很可能已经滚出窗口。
        msg += (u"\n\n⚠️ 图面里有 %d 张并排的独立子图，但只归到 %d 个楼层——"
                u"它们会叠建在同一层（墙重叠）。" % (_n_sub, _n_lv))
    sub = (u"● 安全模式（真图首选）：只建 轴网+墙+柱+板（门窗/楼梯全关）\n"
           u"● 常规模式：含门/窗开洞/楼梯；墙接合已全局默认关闭\n"
           u"  （v2.8c：接合在转换图上两次触发 Revit 几何核崩溃，骨架优先）")
    choice = _ask_mode(msg, sub)
    if not choice:
        out.print_md(u"已取消，未写入。")
        return
    safe = (choice == u"safe")

    # 6) 可验证写入（V2）：事务状态核对 + 数据库对账 + 日志落盘
    #    日志放第一张图纸旁边（多选时 json_path 只是循环里最后一个，语义不清）
    log_file = os.path.join(os.path.dirname(src_paths[0]),
                            u"DWG翻模日志_%s.txt" % time.strftime("%Y%m%d_%H%M%S"))
    out.print_md(u"📝 本次运行日志：`%s`（Revit 若崩，看这个文件）" % to_text_err(log_file))
    try:
        dbinfo = {}
        results = create_elements(
            doc, planned, out,
            batch_size=25 if len(planned) > 500 else 50,
            join_walls=False,  # v2.8c: 接合全局关闭（两次崩溃实录，骨架优先）
            # 这里原先写 `door_window_cut=(not big) and (not safe)`，
            # 而 big = len(planned) > 200 —— 同一个 200 阈值在按钮和 lib 各写一遍。
            # 上一轮只拆了 lib 那道，调用方这道显式传值把它整个覆盖掉，
            # 所以真机重跑门窗仍然 0 寄宿。安全模式由 lib 内部处理，这里交回默认。
            door_window_cut=None,
            safe_mode=safe, dbinfo=dbinfo)
    except Exception as ex:
        out.print_md(u"# ❌ 写入失败：过程出错，本次未落盘")
        out.print_md(u"错误：%s" % to_text_err(ex))
        out.print_md(u"（V2 不静默回滚，失败必现形。）完整证据在日志：`%s`" % to_text_err(log_file))
        return

    # 7) 结果汇总（口径 = 结果列表 + 落盘对账，不再轻信结果列表）
    #    「安全模式已跳过」是用户在模式弹窗里选的口径，不是建不出来，必须单独算。
    #    2026-10-11 高层住宅实锤：1128 行红字里 1106 行是安全模式跳过，
    #    把「9823 个已落盘」整个盖掉，读出来就成了"全失败"。
    n_ok = sum(1 for r in results if r[0])
    n_skip = sum(1 for i in range(len(results))
                 if not results[i][0] and is_by_design_skip(results[i][1]))
    n_fail = len(results) - n_ok - n_skip
    out.print_md(u"## 写入结果：成功 %d ／ 按所选模式跳过 %d ／ 失败 %d"
                 % (n_ok, n_skip, n_fail))
    if n_skip:
        out.print_md(u"ℹ️ 这 %d 个是**安全模式按设计不建**的门/窗/楼梯/梁，不是失败。"
                     u"要出门窗：撤销本次结果后重跑，模式选【否】常规模式。" % n_skip)
    _delta = dbinfo.get("delta")
    # 口径：delta 是**落盘构件实例增量**，n_ok 是**计划条数**。一条多点折线
    # 会化成多段墙（bimconv_elements._create_wall 按段建），所以 delta >= n_ok
    # 属正常，只有 delta < n_ok 才说明「声称成功、实际没落盘」。
    if _delta is not None and _delta < n_ok:
        out.print_md(u"# ⚠️ 数据库对账不一致——「成功」不可信！")
        out.print_md(u"落盘增量：%s ｜ 声称成功：%s" % (_delta, n_ok))
        out.print_md(u"**结论：部分构件没有真正落盘。** 把日志发给维护者：`%s`" % to_text_err(log_file))
    elif _delta is not None:
        out.print_md(u"✅ 数据库对账通过：落盘构件增量 %d ≥ 计划成功 %d"
                     u"（差值 %d 为折线链化的额外分段）"
                     % (_delta, n_ok, _delta - n_ok))
    if n_fail:
        fr = [[i + 1, results[i][1]] for i in range(len(results))
              if not results[i][0] and not is_by_design_skip(results[i][1])]
        out.print_md(u"## ❌ 真失败 %d 个（建不出来，不是被跳过）" % len(fr))
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
                u"[DWG翻模 %s] planned=%d ok=%d skipped=%d fail=%d delta=%s commits=%s\n"
                % (time.strftime("%Y-%m-%d %H:%M:%S"),
                   len(planned), n_ok, n_skip, n_fail, _delta, _cs)).encode("utf-8"))
            if _fl:
                _lf.write(to_text_err(
                    u"[失败原文（前%d条，severity|描述）]\n" % len(_fl)).encode("utf-8"))
                for _f in _fl:
                    _lf.write(to_text_err(u"  %s\n" % _f).encode("utf-8"))
            # 逐行只写真失败：安全模式跳过动辄上千行，写进去会把真故障信号埋掉
            for i, r in enumerate(results):
                if not r[0] and not is_by_design_skip(r[1]):
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
                             u"可疑项需人工确认后处置 —— 原「翻模体检」按钮因数据源"
                             u"从未落地已于 2026-10-07 移除，处置入口待写侧补齐后"
                             u"作为本按钮的第二步重建。）")
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
