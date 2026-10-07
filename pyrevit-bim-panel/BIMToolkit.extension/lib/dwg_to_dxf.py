# -*- coding: utf-8 -*-
u"""
dwg_to_dxf.py (V2) —— DWG 翻模【第 0 步】：让 CAD 自己把 DWG 另存为 DXF（免手动）。

V2（2026-09-08）：图纸版本感知路由。
    实测：AutoCAD 2010 打开 AC1032（2018~2025 格式）报「不是有效的图形文件」
    —— 不是图纸坏了，是版本高于老 CAD 的支持上限。
    因此 V2 读取 DWG 头版本魔数，按「哪款 CAD 能打开这个版本」排序候选：
      - 图纸版本 > 2010 格式 → 中望CAD（高版本引擎）优先，AutoCAD 兜底；
      - 图纸版本 <= 2010 格式 → AutoCAD 优先（原顺序）；
    某款 CAD 打开失败/能力不足时自动换下一款；两款都失败才报错。

原理（不截图、不模拟按键，走 COM 接口直连，比"AI 按按键"可靠一个量级）：
    1) 优先附着正在运行的 AutoCAD / 中望CAD（COM: GetActiveObject）；
    2) 没有运行的就按候选顺序启动（首次启动约 30~60 秒，属正常）；
    3) 若目标 DWG 未打开则打开（打不开自动换下一款 CAD）；
    4) 命令文档 SaveAs 为 DXF（逐个尝试格式枚举，保存后校验文件头，
       防止"存出来其实是 DWG 只是改了扩展名"的假 DXF）；
    5) 成功后可直接接 DWG转JSON.bat（第 1 步）。

用法：
    python dwg_to_dxf.py <input.dwg> [output.dxf] [--no-start]
    （--no-start：只附着已运行的 CAD，不启动）

依赖：pywin32（已装 managed venv）。本文件只在 CPython 3 下运行。
"""
import os
import sys

# SaveAs 格式枚举候选（AcSaveAsType）：13=ac2000_dxf, 1=acR12_dxf, 12=ac2000_dwg…
# 不同 CAD 版本枚举值可能不同 -> 逐个尝试 + 文件头校验兜底，谁存出真 DXF 用谁。
_SAVEAS_FMTS = (13, 1, 12, 64, 60)

# CAD COM ProgID（默认顺序；实际顺序会按图纸版本动态调整）
_APP_IDS = ("AutoCAD.Application", "ZWCAD.Application")

# DWG 版本魔数 -> 人类可读年份
_DWG_TAGS = {
    u"AC1012": u"R13",
    u"AC1014": u"R14",
    u"AC1015": u"2000",
    u"AC1018": u"2004",
    u"AC1021": u"2007",
    u"AC1024": u"2010~2012",
    u"AC1027": u"2013~2017",
    u"AC1032": u"2018~2025",
}


def _log(msg):
    try:
        print(msg)
    except Exception:
        print(msg.encode("utf-8", "replace").decode("utf-8", "replace"))
    sys.stdout.flush()


def _head(path, n=64):
    u"""读文件头 n 字节（失败返回 b""）。"""
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except Exception:
        return b""


def _is_dwg(head):
    u"""DWG 文件头以 AC1x 版本串开头（AC1015/AC1018/AC1024/AC1027/AC1032…）。"""
    return head[:3] == b"AC1" and head[3:4].isdigit()


def _is_dxf(head):
    u"""真 DXF 判定：二进制 DXF 有固定签名；ASCII DXF 前几行是组码 '  0' + SECTION。"""
    if head.startswith(b"AutoCAD Binary DXF"):
        return True
    if head[:3] == b"AC1":
        return False  # 这是 DWG（假 DXF）
    return b"SECTION" in head or head.lstrip()[:1] == b"0"


def _dwg_tag(path):
    u"""读 DWG 头 6 字节版本魔数（如 AC1032）；非 DWG 返回 None。
    魔数字典序即版本序（AC1012 < AC1014 < … < AC1032），可直接字符串比较。"""
    head = _head(path, 8)
    if _is_dwg(head):
        try:
            return head[:6].decode("ascii")
        except Exception:
            return None
    return None


def _app_max_tag(app):
    u"""探测一款 CAD 能打开的最高 DWG 版本魔数（探测不到返回 None=未知）。
    AutoCAD/中望 的 Version 主版本号与格式上限大致对应：
      >=22（中望 202x 的版本号是大数，同样落在这里）-> AC1032；
      19~21 -> AC1027；18 -> AC1024（2010~2012）；17 -> AC1021；更老 -> AC1015。
    """
    try:
        v = u"%s" % app.Version
    except Exception:
        return None
    try:
        major = int(float(str(v).strip().split(".")[0].split(" ")[0]))
    except Exception:
        return None
    if major >= 22:
        return u"AC1032"
    if major >= 19:
        return u"AC1027"
    if major == 18:
        return u"AC1024"
    if major == 17:
        return u"AC1021"
    return u"AC1015"


def _attach(pid):
    u"""附着正在运行的指定 CAD；失败返回 None。"""
    import win32com.client
    try:
        app = win32com.client.GetActiveObject(pid)
        _log(u"已附着运行中的 CAD：%s" % pid)
        return app
    except Exception:
        return None


def _start(pid):
    u"""启动指定 CAD；失败返回 None。"""
    import win32com.client
    try:
        _log(u"正在启动 %s（首次约 30~60 秒，请稍候）…" % pid)
        app = win32com.client.Dispatch(pid)
        try:
            app.Visible = True
        except Exception:
            pass
        _log(u"已启动：%s" % pid)
        return app
    except Exception as e:
        _log(u"  启动 %s 失败：%s" % (pid, e))
        return None


def _find_open_doc(app, dwg_path):
    u"""在已打开文档里找目标 DWG（按全路径/文件名匹配）。"""
    try:
        docs = app.Documents
        n = docs.Count
    except Exception:
        return None
    want = os.path.basename(dwg_path).lower()
    want_full = os.path.abspath(dwg_path).lower()
    for i in range(n):
        try:
            d = docs.Item(i)
            full = (d.FullName or u"").lower()
            if full == want_full or os.path.basename(full) == want:
                return d
        except Exception:
            continue
    return None


def _saveas_dxf(doc, dxf_path):
    u"""SaveAs 为 DXF：逐个格式枚举尝试，保存后校验文件头。
    返回 True=成功；False=全部失败。"""
    for fmt in _SAVEAS_FMTS:
        try:
            doc.SaveAs(dxf_path, fmt)
        except Exception as e:
            _log(u"  格式 %s 保存失败：%s" % (fmt, e))
            continue
        head = _head(dxf_path)
        if _is_dxf(head):
            _log(u"  格式 %s -> 真 DXF，校验通过" % fmt)
            return True
        if _is_dwg(head):
            _log(u"  格式 %s 存出来仍是 DWG（假 DXF），换下一个格式重试" % fmt)
            try:
                os.remove(dxf_path)
            except Exception:
                pass
            continue
        _log(u"  格式 %s 存出的文件头无法识别（%r），换下一个格式重试"
             % (fmt, head[:8]))
        try:
            os.remove(dxf_path)
        except Exception:
            pass
    return False


# --------------------------------------------------------------------------- #
# 无 CAD 兜底：LibreDWG（GNU 开源）直接把 DWG 转 DXF，不需要任何 CAD。
# 2026-09-08 实测：AC1032（8.3MB 结构图）--as r2010 -> 64MB DXF，ezdxf 解析
# 出 18013 实体，全管线走通。
# 探测顺序：BIMTOOLKIT_DWG2DXF 环境变量 > %USERPROFILE%\cad-tools\dwg2dxf.exe
#           > PATH 里的 dwg2dxf.exe。换机器不用改源码。
# --------------------------------------------------------------------------- #
_LIBREDWG_ENV = u"BIMTOOLKIT_DWG2DXF"


def _libredwg_paths():
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return (
        os.environ.get(_LIBREDWG_ENV, u""),
        os.path.join(home, u"cad-tools", u"dwg2dxf.exe"),
    )


def _libredwg_exe():
    for p in _libredwg_paths():
        if p and os.path.isfile(p):
            return p
    for p in os.environ.get("PATH", "").split(os.pathsep):
        cand = os.path.join(p, "dwg2dxf.exe")
        if p and os.path.isfile(cand):
            return cand
    return None


def _libredwg_dxf(dwg, dxf, as_r=u"r2010"):
    u"""用 LibreDWG 把 DWG 转成 DXF（无需 CAD）。成功返回 True。

    原生 exe 的 argv 对中文路径不稳：先复制到 %TEMP% 的 ASCII 路径再转，
    完成后把产物复制回目标位置。--no-start 模式同样可用（它不是交互式 CAD）。
    """
    exe = _libredwg_exe()
    if not exe:
        _log(u"  （未安装 LibreDWG 无 CAD 转换器，跳过兜底。"
             u"可在 https://github.com/LibreDWG/libredwg/releases 获取）")
        return False
    import shutil
    import subprocess
    import tempfile
    tmpd = tempfile.mkdtemp(prefix="dwg2dxf_")
    try:
        tin = os.path.join(tmpd, "in.dwg")
        tout = os.path.join(tmpd, "out.dxf")
        shutil.copyfile(dwg, tin)
        _log(u"LibreDWG 无 CAD 转换（%s，大图约 1~3 分钟）…" % as_r)
        r = subprocess.run([exe, "-y", "-v0", "--as", as_r, "-o", tout, tin],
                           capture_output=True, timeout=3600)
        if r.returncode != 0 or not os.path.isfile(tout):
            err = (r.stderr or b"")[-300:].decode("mbcs", "replace")
            _log(u"  LibreDWG 转换失败（exit=%s）%s" % (r.returncode, err))
            return False
        if not _is_dxf(_head(tout)):
            _log(u"  LibreDWG 输出的不是可识别 DXF，放弃。")
            return False
        shutil.copyfile(tout, dxf)
        _log(u"  LibreDWG 转换成功，DXF 头校验通过。")
        return True
    except Exception as e:
        _log(u"  LibreDWG 异常：%s" % e)
        return False
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def main(argv):
    args = [a for a in argv[1:] if a != "--no-start"]
    no_start = "--no-start" in argv[1:]
    if not args:
        _log(u"用法：python dwg_to_dxf.py <input.dwg> [output.dxf] [--no-start]")
        return 2
    dwg = os.path.abspath(args[0])
    if not os.path.isfile(dwg):
        _log(u"找不到文件：%s" % dwg)
        return 2
    if dwg.lower().endswith(u".dxf"):
        _log(u"输入已是 DXF，无需转换：%s" % dwg)
        return 0
    if not dwg.lower().endswith(u".dwg"):
        _log(u"请传入 .dwg 文件（当前：%s）" % os.path.basename(dwg))
        return 2
    dxf = os.path.abspath(args[1]) if len(args) > 1 else dwg[:-4] + u".dxf"

    # DXF 已存在且比 DWG 新：直接复用（增量省时）
    if os.path.isfile(dxf) and os.path.getmtime(dxf) >= os.path.getmtime(dwg):
        head = _head(dxf)
        if _is_dxf(head):
            _log(u"已有更新的同名 DXF，直接复用：%s" % dxf)
            return 0
        _log(u"已有同名 DXF 但不是真 DXF（可能是假 DXF），将重新生成。")

    # ---- 版本感知路由：新图纸优先高版本引擎，老图纸 AutoCAD 优先 ----
    file_tag = _dwg_tag(dwg)
    _log(u"DWG 版本：%s（%s）" % (file_tag or u"未识别",
                                 _DWG_TAGS.get(file_tag, u"未知")))
    if file_tag and file_tag > u"AC1024":
        order = ("ZWCAD.Application", "AutoCAD.Application")
        _log(u"版本高于 2010 格式：优先用高版本引擎（中望CAD 2026 可直接打开）。")
    else:
        order = _APP_IDS

    try:
        import pythoncom
        pythoncom.CoInitialize()
    except Exception:
        pass

    last_err = None
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
                last_err = e
                _log(u"  打开 DWG 失败：%s" % e)
                continue
        else:
            _log(u"DWG 已在该 CAD 中打开，直接另存。")

        _log(u"另存为 DXF：%s" % dxf)
        ok = False
        try:
            ok = _saveas_dxf(doc, dxf)
        except Exception as e:
            _log(u"SaveAs 异常：%s" % e)
        if ok:
            _log(u"完成：%s" % dxf)
            _log(u"下一步：把它拖到桌面 DWG转JSON.bat（本脚本成功后由 bat 自动接续）。")
            return 0
        _log(u"  该 CAD 未能存出真 DXF，换下一款 CAD…")

    # ---- 无 CAD 兜底：LibreDWG 直接转 DXF（2010 格式；--no-start 下同样可用，
    #      它不是交互式 CAD，只是个轻量命令行转换器）----
    if _libredwg_dxf(dwg, dxf, as_r=u"r2010"):
        _log(u"完成（LibreDWG 无 CAD 转换）：%s" % dxf)
        _log(u"下一步：把它拖到桌面 DWG转JSON.bat（本脚本成功后由 bat 自动接续）。")
        return 0

    if no_start:
        _log(u"失败：没有能处理该图纸的 CAD 在运行（--no-start 不启动）。")
    else:
        _log(u"失败：所有候选 CAD 均未能完成转换。%s"
             % ((u"最后一次错误：%s" % last_err) if last_err else u""))
        _log(u"补救：①把 LibreDWG 的 dwg2dxf.exe 放到你的用户目录\\cad-tools 下，"
             u"或设环境变量 BIMTOOLKIT_DWG2DXF 直接指到 exe，或让它进 PATH；"
             u"②装 ODA File Converter；③用更高版本 CAD 手动另存为 DXF。")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
