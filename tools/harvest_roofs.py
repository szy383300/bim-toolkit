# -*- coding: utf-8 -*-
u"""收割本机所有可用的**真屋面**，汇总成一个屋面库 roof_library.rvt。

来源（实测有真屋面的）：
  Autodesk 样例：rac_basic(2) / rac_advanced(6) / Technical_school(6) /
                 Arch Link(6) / Golden_Nugget建筑(20)
  自有工程      ：桌面\\自有工程\\项目1.rvt(2)
合计 42 个 RoofBase。

安全：
  * 每个来源先复制到临时目录再打开，原件绝不触碰
  * CopyPasteOptions 配 IDuplicateTypeNamesHandler —— 否则 Revit 弹「重复类型」
    模态框会把桥静默卡死（上一轮踩过，堵了 15 分钟）
"""
import io
import json
import os
import shutil
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Desktop")
sys.path.insert(0, os.path.join(_REPO, "mcp"))
from genbuild.bridge import BridgeClient  # noqa: E402

TMPDIR = os.path.join(_REPO, "_survey", "_lib_tmp")
LIBOUT = os.path.join(_REPO, "_models", "roof_library.rvt")
OUT = os.path.join(_REPO, "_survey", "roof_library_log.txt")

SOURCES = [
    (r"D:\Autodesk\Revit 2019\Samples\rac_basic_sample_project.rvt", u"rac_basic"),
    (r"D:\Autodesk\Revit 2019\Samples\rac_advanced_sample_project.rvt", u"rac_advanced"),
    (r"D:\Autodesk\Revit 2019\Samples\Technical_school-current_m.rvt", u"Technical_school"),
    (r"D:\Autodesk\Revit 2019\Samples\Arch Link Model.rvt", u"ArchLink"),
    (r"D:\Autodesk\Revit 2019\Samples\BIM_Projekt_Golden_Nugget-Architektur_und_Ingenieurbau.rvt", u"GoldenNugget"),
    (os.path.join(_DESKTOP, u"自有工程", u"项目1.rvt"), u"自有工程-项目1"),
]

# 第 1 步：用样板新建库文档
NEW_LIB = u'''# -*- coding: utf-8 -*-
out = {}
app = __revit__.Application
try:
    t = app.DefaultProjectTemplate
    d2 = app.NewProjectDocument(t)
    out["created"] = to_text(d2.Title)
    out["path"] = to_text(d2.PathName) if d2.PathName else u""
except Exception as ex:
    out["err"] = to_text(ex)[:250]
_result = out
'''

# 第 2 步：从一个来源复制其全部屋面进"当前文档"
HARVEST = u'''# -*- coding: utf-8 -*-
import json
from Autodesk.Revit.DB import (RoofBase, FilteredElementCollector, ElementId,
                               ElementTransformUtils, CopyPasteOptions,
                               IDuplicateTypeNamesHandler, DuplicateTypeAction,
                               Transaction)
from System.Collections.Generic import List
out = {}
app = __revit__.Application

class UseDest(IDuplicateTypeNamesHandler):
    def OnDuplicateTypeNamesFound(self, args):
        return DuplicateTypeAction.UseDestinationTypes

src = None
try:
    src = app.OpenDocumentFile(r\'__P__\')
except Exception as ex:
    out["open_err"] = to_text(ex)[:250]

if src is not None:
    try:
        roofs = list(FilteredElementCollector(src).OfClass(RoofBase).ToElements())
        out["src_count"] = len(roofs)
        ids = List[ElementId]()
        for r in roofs:
            ids.Add(r.Id)
        if ids.Count:
            t = Transaction(doc, "harvest roofs")
            t.Start()
            try:
                opts = CopyPasteOptions()
                opts.SetDuplicateTypeNamesHandler(UseDest())
                from Autodesk.Revit.DB import Transform
                got = ElementTransformUtils.CopyElements(
                    src, ids, doc, Transform.Identity, opts)
                t.Commit()
                out["copied"] = len(list(got))
            except Exception as ex:
                try:
                    t.RollBack()
                except Exception:
                    pass
                out["copy_err"] = to_text(ex)[:250]
    except Exception as ex:
        out["scan_err"] = to_text(ex)[:250]
    finally:
        try:
            src.Close(False)
            out["src_closed"] = True
        except Exception as ex:
            out["close_err"] = to_text(ex)[:150]

out["lib_roofs_now"] = len(list(
    FilteredElementCollector(doc).OfClass(RoofBase).ToElements()))
_result = out
'''

# 第 3 步：保存库
SAVE_LIB = u'''# -*- coding: utf-8 -*-
out = {}
try:
    if __import__("os").path.exists(r\'__P__\'):
        __import__("os").remove(r\'__P__\')
    doc.SaveAs(r\'__P__\')
    out["saved"] = r\'__P__\'
except Exception as ex:
    out["err"] = to_text(ex)[:250]
_result = out
'''


def log(s):
    with io.open(OUT, "a", encoding="utf-8") as f:
        f.write(u"%s\n" % s)
    print(s)


def call(c, code, tmo=300, no_tx=True):
    cmd = {"type": "execute_code", "code": code}
    if no_tx:
        cmd["no_transaction"] = True
    r = c.call(cmd, timeout=tmo)
    res = r.get("result", r)
    if isinstance(res, dict) and isinstance(res.get("result"), dict):
        res = res["result"]
    return res


def main():
    if not os.path.isdir(TMPDIR):
        os.makedirs(TMPDIR)
    with io.open(OUT, "w", encoding="utf-8") as f:
        f.write(u"=== 屋面库收割日志 ===\n")

    c = BridgeClient()
    log(u"[1] 新建库文档…")
    r = call(c, NEW_LIB, tmo=300)
    log(u"    -> %s" % json.dumps(r, ensure_ascii=False)[:200])
    if r.get("err"):
        log(u"    新建失败，终止")
        return

    total = 0
    for path, label in SOURCES:
        if not os.path.isfile(path):
            log(u"[跳过] %s 不存在" % label)
            continue
        tmp = os.path.join(TMPDIR, u"src.rvt")
        try:
            shutil.copyfile(path, tmp)
        except Exception as e:
            log(u"[失败] %s 复制失败: %s" % (label, e))
            continue
        t0 = time.time()
        try:
            r = call(c, HARVEST.replace("__P__", tmp), tmo=900)
        except Exception as e:
            log(u"[超时] %s %s（%.0fs）—— 可能有模态框，终止" %
                (label, type(e).__name__, time.time() - t0))
            return
        log(u"[%s] 源屋面=%s 复制=%s 库内累计=%s（%.0fs）%s"
            % (label, r.get("src_count"), r.get("copied"),
               r.get("lib_roofs_now"), time.time() - t0,
               (" 错误:" + str(r.get("copy_err") or r.get("open_err"))) if
               (r.get("copy_err") or r.get("open_err")) else u""))
        total = r.get("lib_roofs_now") or total
        try:
            os.remove(tmp)
        except Exception:
            pass

    log(u"[3] 保存库 -> %s" % LIBOUT)
    r = call(c, SAVE_LIB.replace("__P__", LIBOUT), tmo=600)
    log(u"    -> %s" % json.dumps(r, ensure_ascii=False)[:200])
    log(u"=== 完成：库内真屋面 %s 个 ===" % total)
    log(u"DONE")


if __name__ == "__main__":
    main()
