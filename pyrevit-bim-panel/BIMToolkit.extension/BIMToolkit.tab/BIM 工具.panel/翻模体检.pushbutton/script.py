# -*- coding: utf-8 -*-
# 翻模体检（V1）：读 DWG翻模 运行落盘的 *_report.json，把可疑构件在三维里
# 分类列表、点选定位、一键删除。数据由写入链路自动取证（V2.5）。
from pyrevit import revit, DB, script
import os
import json

out = script.get_output()


def to_text_err(v):
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
        return u"<无法转换>"


def _msg_box(msg, title=u"翻模体检"):
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons
        MessageBox.Show(msg, title, MessageBoxButtons.OK)
    except Exception:
        print(msg)


def _read_json_unicode(path):
    with open(path, "rb") as f:
        return json.loads(f.read().decode("utf-8-sig"))


def _quality_score(rep):
    u"""V2.6 质量自评分：报告自带 quality 字段直接用；V2.5 旧报告就地补算。"""
    q = rep.get("quality")
    if isinstance(q, dict) and "score" in q:
        return q
    try:
        plan = rep.get("plan_count")
        if not plan:
            return None
        failed = rep.get("failed") or []
        fails = rep.get("failures") or []
        overlap = sum(1 for f in fails
                      if u"重叠" in (f.get("desc") or u"") and f.get("ids"))
        walls = [c for c in (rep.get("created") or []) if c.get("type") == u"wall"]
        frag = 0
        for c in walls:
            try:
                if 0 < float(c.get("extra") or u"0") < 600:
                    frag += 1
            except Exception:
                pass
        if rep.get("mismatch"):
            score = 0
        else:
            score = 100
            score -= min(40.0, 100.0 * len(failed) / plan)
            score -= min(30.0, 50.0 * len(fails) / plan)
            score -= min(20.0, 2.0 * overlap)
            score -= min(15.0, (30.0 * frag / len(walls)) if walls else 0.0)
            score = int(round(max(0, min(100, score))))
        grade = (u"优秀" if score >= 90 else u"良好" if score >= 75
                 else u"及格" if score >= 60 else u"需人工复核")
        return {"score": score, "grade": grade, "breakdown": {
            "plan_count": plan, "failed": len(failed), "failures": len(fails),
            "overlap_pairs": overlap, "walls": len(walls),
            "fragments": frag, "mismatch": bool(rep.get("mismatch"))}}
    except Exception:
        return None


def _find_latest_report():
    u"""扫描常用目录（桌面/文档/下载，两层深）找最新的 *_report.json。
    返回 (路径, 修改时间) 或 (None, None)。"""
    import time
    roots = []
    for env in ("USERPROFILE",):
        home = os.environ.get(env)
        if home:
            roots.append(os.path.join(home, u"Desktop"))
            roots.append(os.path.join(home, u"Documents"))
            roots.append(os.path.join(home, u"Downloads"))
    best = (None, 0.0)
    now = time.time()
    for root in roots:
        if not os.path.isdir(root):
            continue
        # 手动两层遍历，避免 os.walk 在大目录上拖慢
        scan = [root]
        try:
            for name in os.listdir(root):
                p = os.path.join(root, name)
                if os.path.isdir(p):
                    scan.append(p)
        except Exception:
            pass
        for d in scan:
            try:
                for name in os.listdir(d):
                    if name.lower().endswith(u"_report.json"):
                        p = os.path.join(d, name)
                        try:
                            m = os.path.getmtime(p)
                        except Exception:
                            continue
                        # 只要 30 天内的报告
                        if m > best[1] and (now - m) < 30 * 86400:
                            best = (p, m)
            except Exception:
                pass
    if best[0]:
        return best[0], best[1]
    return None, None


def _fmt_time(ts):
    import time as _t
    try:
        return _t.strftime(u"%Y-%m-%d %H:%M").decode("utf-8")
    except Exception:
        try:
            return unicode(_t.strftime("%Y-%m-%d %H:%M"))
        except Exception:
            return u""


def _pick_report():
    u"""优先自动发现最新报告（30 天内），直接询问是否载入；
    否则/拒绝时打开文件选择框（初始目录定位到报告所在文件夹）。"""
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import (OpenFileDialog, DialogResult,
                                      MessageBox, MessageBoxButtons)

    latest, mtime = _find_latest_report()
    if latest and mtime:
        res = MessageBox.Show(
            u"发现最近的体检报告：\n%s\n（生成于 %s）\n\n直接载入吗？"
            % (to_text_err(latest), _fmt_time(mtime)),
            u"翻模体检", MessageBoxButtons.YesNo)
        if res == DialogResult.Yes:
            return latest
        start_dir = os.path.dirname(latest)
    else:
        start_dir = None

    try:
        dlg = OpenFileDialog()
        dlg.Title = u"选择 DWG翻模 运行生成的 *_report.json"
        dlg.Filter = u"体检报告 (*_report.json)|*_report.json|所有文件 (*.*)|*.*"
        if start_dir and os.path.isdir(start_dir):
            dlg.InitialDirectory = start_dir
        if dlg.ShowDialog() == DialogResult.OK:
            return dlg.FileName
    except Exception as e:
        _msg_box(u"打开文件选择框失败：%s" % to_text_err(e))
    return None


def _classify(rep):
    u"""把报告归类成可复核条目：[(类别, 描述, [元素id_int], 默认处置建议)]。"""
    items = []
    fails = rep.get("failures") or []

    # A 重叠墙（墙对）
    for f in fails:
        d = f.get("desc") or u""
        ids = f.get("ids") or []
        if u"重叠" in d and ids:
            items.append((u"重叠墙", d, ids, u"建议二选一删除"))

    # B 离轴墙/梁
    for f in fails:
        d = f.get("desc") or u""
        ids = f.get("ids") or []
        if u"偏离" in d and ids:
            items.append((u"偏离轴线的构件", d, ids, u"核对位置，酌情保留"))

    # C 被失败处理自动删除的构件
    for d in rep.get("deleted_by_handler") or []:
        items.append((u"已被自动删除", u"Revit 判定为问题构件（Id=%s）" % d.get("id"),
                      [d.get("id")], u"仅需知悉，构件已不在模型中"))

    # D 自动生成的楼板
    for c in rep.get("created") or []:
        src = c.get("source") or u""
        if src.startswith(u"wall-loop") or src.startswith(u"wall-cluster"):
            items.append((u"自动楼板", c.get("note") or u"由建筑轮廓自动生成",
                          [c.get("id")], u"L 形楼凹口被外包覆盖，酌情处理"))

    # E 碎墙段（墙总长 < 600mm）
    for c in rep.get("created") or []:
        if c.get("type") != u"wall":
            continue
        try:
            L = float(c.get("extra") or u"0")
        except Exception:
            continue
        if 0 < L < 600:
            items.append((u"碎墙段", u"墙总长仅 %.0f mm（疑似图纸噪声）" % L,
                          [c.get("id")], u"建议删除"))

    return items


def _focus(doc, ids):
    u"""选中构件并切到三维缩放。"""
    try:
        from System.Collections.Generic import List
        idcol = List[DB.ElementId]()
        for i in ids:
            idcol.Add(DB.ElementId(i))
        revit.uidoc.Selection.SetElementIds(idcol)
        uidoc = revit.uidoc
        try:
            if uidoc.ActiveView.ViewType != DB.ViewType.ThreeDimensional:
                vfts = [t for t in DB.FilteredElementCollector(doc)
                        .OfClass(DB.ViewFamilyType).ToElements()
                        if t.ViewFamily == DB.ViewFamily.ThreeDimensional]
                if vfts:
                    v3 = DB.View3D.CreateIsometric(doc, vfts[0].Id)
                    try:
                        v3.Name = u"翻模体检"
                    except Exception:
                        pass
                    uidoc.ActiveView = v3
            from Autodesk.Revit.UI import PostableCommand
            uidoc.PostCommand(PostableCommand.ZoomToFit)
        except Exception:
            pass
        return True
    except Exception as e:
        _msg_box(u"定位失败：%s" % to_text_err(e))
        return False


def _delete(doc, ids):
    u"""事务内删除构件（带确认）。返回是否删除。"""
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons, DialogResult
        res = MessageBox.Show(u"确认删除选中的 %d 个构件？\n（可 Ctrl+Z 撤销）" % len(ids),
                              u"翻模体检 · 删除确认", MessageBoxButtons.YesNo)
        if res != DialogResult.Yes:
            return False
        from System.Collections.Generic import List
        idcol = List[DB.ElementId]()
        for i in ids:
            idcol.Add(DB.ElementId(i))
        t = DB.Transaction(doc, u"翻模体检-删除可疑构件")
        t.Start()
        doc.Delete(idcol)
        t.Commit()
        out.print_md(u"🗑 已删除 %d 个构件" % len(ids))
        return True
    except Exception as e:
        _msg_box(u"删除失败：%s" % to_text_err(e))
        return False


def main():
    doc = revit.doc
    rep_path = _pick_report()
    if not rep_path:
        return
    try:
        rep = _read_json_unicode(rep_path)
    except Exception as e:
        _msg_box(u"报告读取失败：%s" % to_text_err(e))
        return

    items = _classify(rep)
    counts = {}
    for cat, _d, _i, _s in items:
        counts[cat] = counts.get(cat, 0) + 1

    out.print_md(u"# 翻模体检报告")
    out.print_md(u"来源：`%s`" % to_text_err(rep_path))
    out.print_md(u"构件计数：写前 %s → 写后 %s（对账%s）" % (
        rep.get("counts_before"), rep.get("counts_after"),
        u"一致" if not rep.get("mismatch") else u"**不一致！**"))
    q = _quality_score(rep)
    if q:
        out.print_html(
            u'<div style="padding:10px 14px;border:2px solid #d48806;'
            u'background:#fffbe6;border-radius:4px;margin:8px 0;">'
            u'<span style="font-size:22px;font-weight:bold;">质量评分 %d / 100</span>'
            u'<span style="font-size:16px;">　%s</span><br/>'
            u'<span style="color:#666;">扣分明细：写入失败 %d ｜ 失败证据 %d ｜ '
            u'重叠墙 %d 对 ｜ 碎墙段 %d / %d 堵</span></div>'
            % (q.get("score", 0), q.get("grade", u""),
               (q.get("breakdown") or {}).get("failed", 0),
               (q.get("breakdown") or {}).get("failures", 0),
               (q.get("breakdown") or {}).get("overlap_pairs", 0),
               (q.get("breakdown") or {}).get("fragments", 0),
               (q.get("breakdown") or {}).get("walls", 0)))
    if rep.get("failed"):
        out.print_md(u"写入失败 %d 项（明细见日志）" % len(rep.get("failed")))
    if counts:
        rows = [[k, v] for k, v in counts.items()]
        from bimlib import html_table
        out.print_md(u"## 可疑项 %d 条" % len(items))
        out.print_html(html_table([u"类别", u"数量"], rows))
    else:
        out.print_md(u"✅ 未发现可疑构件。")
        _msg_box(u"未发现可疑构件，模型很干净。")
        return

    # ---- WinForms 复核窗口 ----
    import clr
    clr.AddReference("System.Windows.Forms")
    from System.Windows.Forms import (Form, ListBox, Button, Label,
                                      DockStyle, AnchorStyles,
                                      MessageBoxButtons, DialogResult,
                                      FormBorderStyle)
    from System.Drawing import Size, Point

    sel = {"ids": None}

    q_title = _quality_score(rep)
    frm = Form()
    frm.Text = u"翻模体检 · 可疑构件复核（%s）%s" % (
        to_text_err(os.path.basename(rep_path)),
        (u" ｜ 质量 %d 分 %s" % (q_title["score"], q_title.get("grade", u"")))
        if q_title else u"")
    frm.Size = Size(760, 520)
    frm.FormBorderStyle = FormBorderStyle.Sizable
    frm.TopMost = True

    lbl = Label()
    lbl.Text = u"共 %d 条可疑项。选中一条 → 定位（三维选中+缩放）或 删除。" % len(items)
    lbl.SetBounds(12, 10, 700, 22)
    frm.Controls.Add(lbl)

    lb = ListBox()
    lb.SetBounds(12, 38, 720, 380)
    lb.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
    for cat, d, ids, adv in items:
        lb.Items.Add(u"【%s】%s（%d 个元素）→ %s" % (cat, d[:60], len(ids), adv))
    lb.SelectedIndex = 0
    frm.Controls.Add(lb)

    def _cur():
        k = lb.SelectedIndex
        if 0 <= k < len(items):
            return items[k]
        return None

    def _on_focus(sender, args):
        it = _cur()
        if not it:
            return
        ids = [i for i in it[2] if i]
        if _focus(doc, ids):
            sel["ids"] = ids

    def _on_delete(sender, args):
        it = _cur()
        if not it:
            return
        ids = [i for i in it[2] if i]
        if _delete(doc, ids):
            k = lb.SelectedIndex
            lb.Items.RemoveAt(k)
            items.pop(k)
            if lb.Items.Count:
                lb.SelectedIndex = min(k, lb.Items.Count - 1)

    btn_focus = Button()
    btn_focus.Text = u"定位选中"
    btn_focus.SetBounds(12, 432, 120, 30)
    btn_focus.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
    btn_focus.Click += _on_focus
    frm.Controls.Add(btn_focus)

    btn_del = Button()
    btn_del.Text = u"删除选中构件"
    btn_del.SetBounds(142, 432, 120, 30)
    btn_del.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
    btn_del.Click += _on_delete
    frm.Controls.Add(btn_del)

    btn_close = Button()
    btn_close.Text = u"关闭"
    btn_close.SetBounds(612, 432, 120, 30)
    btn_close.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
    btn_close.Click += lambda s, a: frm.Close()
    frm.Controls.Add(btn_close)

    frm.ShowDialog()
    out.print_md(u"体检复核结束：剩余可疑项 %d 条（处置记录见输出上方删除日志）。"
                 % len(items))


if __name__ == "__main__":
    main()
