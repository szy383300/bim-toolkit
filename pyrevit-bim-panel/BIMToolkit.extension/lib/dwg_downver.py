# -*- coding: utf-8 -*-
u"""
dwg_downver.py —— 图纸版本转换：高版本 DWG 另存为低版本（默认 2010/AC1024），
让 AutoCAD 2010 等老 CAD 能直接打开。

实测背景：AutoCAD 2010 打开 AC1032（2018~2025 格式）报「不是有效的图形文件」。
本工具用本机"能打开该图纸的高版本引擎"（中望CAD 2026 / 更高版本 AutoCAD）
打开后 SaveAs 成目标版本，逐格式枚举 + 文件头魔数校验，绝不覆盖原图。

用法：
    python dwg_downver.py <input.dwg> [output.dwg]
                          [--target 2007|2010|2013|2018] [--no-start]
输出：
    默认同目录 <原名>_<目标年份>.dwg。
依赖：pywin32；本机需有能打开该图纸的 CAD。只在 CPython 3 下运行。
"""
import os
import sys

from dwg_to_dxf import (_log, _head, _dwg_tag, _DWG_TAGS, _app_max_tag,
                        _attach, _start, _find_open_doc, _libredwg_dxf)

# 目标年份 -> (DWG 魔数, SaveAs 格式枚举候选：先精确目标再逐步降级兜底)
# AcSaveAsType：12=2000_dwg, 24=2004_dwg, 36=2007_dwg, 48=2010_dwg,
#               60=2013_dwg, 64=2018_dwg（中望沿用同一套枚举；魔数校验兜底）
_TARGETS = {
    u"2007": (u"AC1021", (36, 24, 12)),
    u"2010": (u"AC1024", (48, 36, 24, 12)),
    u"2013": (u"AC1027", (60, 48, 36)),
    u"2018": (u"AC1032", (64, 60)),
}


def _saveas_dwg(doc, out_path, target_tag, fmts):
    u"""SaveAs 为指定版本 DWG：逐格式枚举 + 魔数校验。

    精确命中目标版本直接成功；存出「更低版本」也接受（目标老 CAD 同样能
    打开，留作磁盘上的兜底结果）；存出「更高版本」删掉换下一个格式。
    返回 (是否成功, 实际版本魔数或 None)。
    """
    fallback_tag = None
    for fmt in fmts:
        try:
            doc.SaveAs(out_path, fmt)
        except Exception as e:
            _log(u"  格式 %s 保存失败：%s" % (fmt, e))
            continue
        tag = _dwg_tag(out_path)
        if not tag:
            _log(u"  格式 %s 存出的文件头不是 DWG（%r），换下一个格式"
                 % (fmt, _head(out_path, 8)))
            try:
                os.remove(out_path)
            except Exception:
                pass
            continue
        if tag == target_tag:
            _log(u"  格式 %s -> %s（%s），校验通过"
                 % (fmt, tag, _DWG_TAGS.get(tag, u"?")))
            return True, tag
        if tag < target_tag:
            _log(u"  格式 %s 存出 %s（比目标更低，留作兜底）" % (fmt, tag))
            fallback_tag = tag       # 磁盘上当前这份就是它
            continue
        _log(u"  格式 %s 存出 %s（仍高于目标），删掉换下一个格式" % (fmt, tag))
        try:
            os.remove(out_path)
        except Exception:
            pass
    # 兜底结果必须"文件还在且魔数对得上"才算数（防止后续格式删除后误报）
    if fallback_tag and os.path.isfile(out_path) and _dwg_tag(out_path) == fallback_tag:
        _log(u"  未精确存出 %s，但已存出 %s（更低版本，目标 CAD 同样能打开）"
             % (target_tag, fallback_tag))
        return True, fallback_tag
    return False, None


def _open_in_best_cad(dwg, file_tag, no_start):
    u"""按版本感知顺序找到能打开该图纸的 CAD，返回 (doc, pid) 或 (None, None)。"""
    if file_tag and file_tag > u"AC1024":
        order = ("ZWCAD.Application", "AutoCAD.Application")
        _log(u"版本高于 2010 格式：优先用高版本引擎（中望CAD 2026 可直接打开）。")
    else:
        order = ("AutoCAD.Application", "ZWCAD.Application")
    for pid in order:
        app = _attach(pid)
        if app is None and not no_start:
            app = _start(pid)
        if app is None:
            continue
        cap = _app_max_tag(app)
        if file_tag and cap and file_tag > cap:
            _log(u"  %s 最高支持 %s（%s），打不开 %s（%s），换下一款 CAD。"
                 % (pid, cap, _DWG_TAGS.get(cap, u"?"),
                    file_tag, _DWG_TAGS.get(file_tag, u"?")))
            continue
        doc = _find_open_doc(app, dwg)
        if doc is None:
            _log(u"正在 CAD 中打开：%s" % dwg)
            try:
                doc = app.Documents.Open(dwg)
            except Exception as e:
                _log(u"  打开 DWG 失败：%s" % e)
                continue
        else:
            _log(u"DWG 已在该 CAD 中打开。")
        return doc, pid
    return None, None


def main(argv):
    raw = argv[1:]
    no_start = "--no-start" in raw
    target = u"2010"
    if "--target" in raw:
        i = raw.index("--target")
        if i + 1 < len(raw):
            target = raw[i + 1]
            raw = raw[:i] + raw[i + 2:]
    args = [a for a in raw if not a.startswith("--")]
    if not args:
        _log(u"用法：python dwg_downver.py <input.dwg> [output.dwg] "
             u"[--target 2007|2010|2013|2018] [--no-start]")
        return 2
    if target not in _TARGETS:
        _log(u"不支持的目标版本：%s（可选 2007/2010/2013/2018）" % target)
        return 2
    target_tag, fmts = _TARGETS[target]

    dwg = os.path.abspath(args[0])
    if not os.path.isfile(dwg):
        _log(u"找不到文件：%s" % dwg)
        return 2
    if not dwg.lower().endswith(u".dwg"):
        _log(u"请传入 .dwg 文件（当前：%s）" % os.path.basename(dwg))
        return 2
    out = os.path.abspath(args[1]) if len(args) > 1 else dwg[:-4] + u"_" + target + u".dwg"
    if out.lower() == dwg.lower():
        _log(u"输出路径不能与原图相同（绝不覆盖原图）。")
        return 2

    file_tag = _dwg_tag(dwg)
    _log(u"原图版本：%s（%s）" % (file_tag or u"未识别",
                                 _DWG_TAGS.get(file_tag, u"未知")))
    if file_tag and file_tag <= target_tag:
        _log(u"图纸版本不高于目标 %s（%s），老 CAD 本来就能打开，无需转换。"
             % (target, target_tag))
        return 0

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    doc, pid = _open_in_best_cad(dwg, file_tag, no_start)
    if doc is None:
        _log(u"失败：本机没有能打开该图纸的 CAD（需中望2026/更高版本 AutoCAD）。")
        _log(u"补救：①装 ODA File Converter（免费，命令行可批量转版本）；"
             u"②手动用高版本 CAD 另存。")
        return 1

    _log(u"另存为 %s（%s）：%s" % (target, target_tag, out))
    ok, real = _saveas_dwg(doc, out, target_tag, fmts)
    if ok:
        _log(u"完成：%s（实际版本 %s/%s）" % (out, real, _DWG_TAGS.get(real, u"?")))
        _log(u"现在可以用 AutoCAD 2010 直接打开它了。")
        return 0
    _log(u"  该 CAD 未能存出目标版本 DWG。")

    # ---- 无 CAD 兜底：LibreDWG 直接转低版本 DXF（AutoCAD 2010 打开 DXF
    #      与 DWG 等效；LibreDWG 写 DWG 质量不稳，所以兜底输出 DXF）----
    as_r = {u"2007": u"r2007", u"2010": u"r2010",
            u"2013": u"r2013", u"2018": u"r2018"}.get(target, u"r2010")
    dxf_out = os.path.splitext(out)[0] + u".dxf"
    if _libredwg_dxf(dwg, dxf_out, as_r=as_r):
        _log(u"完成（LibreDWG 无 CAD 转换）：%s" % dxf_out)
        _log(u"说明：兜底输出的是 %s 版 DXF 而非 DWG——AutoCAD 打开 DXF 与 DWG 等效。"
             % target)
        return 0

    _log(u"失败：所有途径（CAD 另存 / LibreDWG 兜底）都未能完成版本转换。")
    _log(u"补救：①确认 dwg2dxf.exe 在「你的用户目录\\cad-tools」下、"
         u"或已进 PATH、或已用环境变量 BIMTOOLKIT_DWG2DXF 指到它；"
         u"②装 ODA File Converter。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
