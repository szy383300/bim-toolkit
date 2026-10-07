# -*- coding: utf-8 -*-
u"""配置建模 —— 在 Revit 里跑 genbuild 的「配置驱动建模」。

用法
----
点按钮 → 选一个 `buildings\\*.yaml` 配方 → 先**干跑**（静态校验 + ping 桥）
→ 看校验结果 → 再**明确确认**（点名这次会清掉哪些类别）→ 才正式构建。

为什么要外部子进程（而不是在按钮里直接 import genbuild）
------------------------------------------------------
genbuild 引擎是 **CPython 3** 写的，依赖 **PyYAML**；
而 Revit 2019 的 pyRevit 跑的是 **IronPython 2.7，没有 yaml**。
所以本按钮只做「选配方 + 干跑 + 确认 + 调子进程 + 回显日志」，
与 `DWG翻模` 是同一套路（那边也是把 ezdxf 的活丢给外部 CPython）。

安全闸门
--------
正式构建会**先清场**：`genbuild.config.WIPE_CATS` 里的类别
（墙/门/窗/楼板/屋顶/楼梯/轴网/柱…）会被删除再重建。
所以流程刻意分成两段，且确认框会**列出本次实际会被清的类别**
（类别表从引擎现读，不在这里硬编码，避免两边漂移）。
"""
import json
import os
import sys
import time

from pyrevit import revit, script

out = script.get_output()

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "genbuild_config.json")
# 引擎与配方在开发仓库里、不在扩展目录内，所以按钮部署后无法从 __file__ 推出仓库根：
# 必须由 genbuild_config.json 的 repo_root 指路。留空时主流程给出明确的填法提示。
DEFAULT_REPO = u""

# genbuild 的退出码语义（与 genbuild/errors.py 对齐）
EXIT_MEANING = {
    0: u"成功",
    1: u"配方错误（YAML 静态校验没通过，未动模型）",
    2: u"桥错误（Revit 里没点开 MCP Bridge？）",
    3: u"构建错误（已开始建模但中途失败）",
    4: u"验收未达标",
}

# 与 DWG翻模 同款：外部 CPython 候选（需要装了 PyYAML）
# 顺序由 _find_python 保证：genbuild_config.json 里的 python > 本表 > 系统 PATH
_HOME = os.environ.get("USERPROFILE") or os.path.expanduser("~")
_PY_CANDIDATES = [
    os.environ.get("BIMTOOLKIT_PYTHON", ""),
    os.path.join(_HOME, ".workbuddy", "binaries", "python",
                 "envs", "default", "Scripts", "python.exe"),
    r"D:\Python\python.exe",
    r"C:\Python39\python.exe",
    "python",
]


# --------------------------------------------------------------------- 基础
def _msg_box(msg, title=u"配置建模"):
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons
        MessageBox.Show(msg, title, MessageBoxButtons.OK)
    except Exception:
        out.print_md(u"⚠️ " + msg)


def _ask_yes_no(msg, title=u"配置建模 · 确认"):
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import (MessageBox, MessageBoxButtons,
                                          DialogResult)
        res = MessageBox.Show(msg, title, MessageBoxButtons.YesNo)
        return res == DialogResult.Yes
    except Exception:
        return False


def _find_python(configured):
    u"""按 配置 > 候选表 顺序找可用的 CPython。"""
    cands = []
    if configured:
        cands.append(configured)
    cands.extend(_PY_CANDIDATES)
    for p in cands:
        if p == "python":
            continue
        try:
            if os.path.isfile(p):
                return p
        except Exception:
            pass
    return "python"


def _load_config():
    cfg = {u"repo_root": DEFAULT_REPO, u"python": u"", u"recipes_dir": u"buildings"}
    try:
        with open(CONFIG_PATH, "rb") as f:
            raw = f.read()
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        data = json.loads(raw.decode("utf-8"))
        for k in (u"repo_root", u"python", u"recipes_dir"):
            v = data.get(k)
            if v:
                cfg[k] = v
    except Exception as e:
        out.print_md(u"⚠️ 读 genbuild_config.json 失败（用默认值）：%s" % e)
    return cfg


def _pick_yaml(start_dir):
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import OpenFileDialog, DialogResult
        dlg = OpenFileDialog()
        dlg.Title = u"选择建模配方（buildings\\*.yaml）"
        dlg.Filter = u"配方 YAML (*.yaml;*.yml)|*.yaml;*.yml|所有文件 (*.*)|*.*"
        if os.path.isdir(start_dir):
            dlg.InitialDirectory = start_dir
        if dlg.ShowDialog() != DialogResult.OK:
            return None
        return dlg.FileName
    except Exception as e:
        _msg_box(u"打开文件选择框失败：%s" % e)
        return None


# --------------------------------------------------------------- 子进程执行
def _run(cmd, cwd, log_path, timeout_hint):
    u"""跑一个子进程，输出重定向到文件（避免管道缓冲塞死）。

    返回 (returncode, 日志文本尾部)。启动失败返回 (None, 原因)。
    """
    out.print_md(u"⏳ %s（后台运行中，请勿关闭 Revit）…" % timeout_hint)
    lf = None
    try:
        import subprocess
        lf = open(log_path, "wb")
        p = subprocess.Popen(cmd, stdout=lf, stderr=lf, cwd=cwd)
        p.wait()
        rc = p.returncode
    except Exception as e:
        return None, u"无法启动子进程：%s" % e
    finally:
        try:
            if lf is not None:
                lf.close()
        except Exception:
            pass
    return rc, _tail(log_path)


def _tail(path, n=40):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except Exception as e:
        return u"(读不到日志 %s：%s)" % (path, e)
    text = None
    for enc in ("utf-8", "mbcs", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except Exception:
            continue
    if text is None:
        text = u"(日志无法解码)"
    lines = text.splitlines()
    return u"\n".join(lines[-n:])


def _show_tail(tail):
    for line in (tail or u"").splitlines():
        out.print_md(u"    " + line)


def _wipe_categories(py, mcp_dir, log_path):
    u"""从引擎现读本次会被清场的类别（不在这里硬编码，避免两边漂移）。

    读不到就返回 None，调用方退化为一句概括性提示。
    """
    code = (u"import sys;sys.path.insert(0,%r);"
            u"from genbuild import config;"
            u"print(u','.join(config.WIPE_CATS))" % mcp_dir)
    rc, tail = _run([py, u"-c", code], mcp_dir, log_path,
                    u"读取清场类别")
    if rc != 0 or not tail:
        return None
    line = tail.strip().splitlines()[-1].strip() if tail.strip() else u""
    if not line or u"Traceback" in line:
        return None
    return [c.strip() for c in line.split(u",") if c.strip()]


# --------------------------------------------------------------------- 主流程
def main():
    cfg = _load_config()
    repo = cfg[u"repo_root"]
    if not repo:
        _msg_box(u"还没配置 genbuild 仓库根目录。\n\n"
                 u"编辑按钮目录下的 genbuild_config.json，把 repo_root 填成本机上含 "
                 u"mcp/gen_build.py 与 buildings/ 的那个目录，保存后重新点按钮。")
        return
    gen_build = os.path.join(repo, u"mcp", u"gen_build.py")
    recipes = os.path.join(repo, cfg[u"recipes_dir"])

    if not os.path.isfile(gen_build):
        _msg_box(u"找不到 genbuild 入口：\n%s\n\n"
                 u"请检查按钮目录下 genbuild_config.json 的 repo_root 是否正确。"
                 % gen_build)
        return

    yaml_path = _pick_yaml(recipes)
    if not yaml_path:
        return

    py = _find_python(cfg[u"python"])
    tmp = os.environ.get("TEMP") or HERE
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dry_log = os.path.join(tmp, u"genbuild_dry_%s.log" % stamp)
    run_log = os.path.join(tmp, u"genbuild_run_%s.log" % stamp)
    wipe_log = os.path.join(tmp, u"genbuild_wipe_%s.log" % stamp)
    mcp_dir = os.path.join(repo, u"mcp")

    out.print_md(u"# 配置建模")
    out.print_md(u"- 配方：`%s`" % yaml_path)
    out.print_md(u"- 解释器：`%s`" % py)
    out.print_md(u"- 引擎：`%s`" % gen_build)

    # ---- 第 1 段：干跑（静态校验 + ping 桥，不动模型）----
    out.print_md(u"\n## 第 1 段：干跑校验（不写模型）")
    rc, tail = _run([py, gen_build, yaml_path, u"--dry"], mcp_dir, dry_log,
                    u"校验配方")
    if rc is None:
        _msg_box(tail, u"配置建模 · 启动失败")
        return
    _show_tail(tail)
    if rc != 0:
        _msg_box(u"干跑未通过（exit=%s：%s）\n\n模型没有被改动。\n日志：%s"
                 % (rc, EXIT_MEANING.get(rc, u"未知"), dry_log),
                 u"配置建模 · 干跑未通过")
        return
    out.print_md(u"\n✅ 干跑通过：配方静态校验无问题，且桥可达。")

    # ---- 第 2 段：强确认（点名会清掉什么）----
    cats = _wipe_categories(py, mcp_dir, wipe_log)
    if cats:
        cat_line = u"、".join(cats)
    else:
        cat_line = u"（读取失败，引擎默认清单：墙/门/窗/楼板/屋顶/楼梯/轴网/柱 等）"
    try:
        doc_name = revit.doc.Title
    except Exception:
        doc_name = u"(未知)"

    ok = _ask_yes_no(
        u"即将在**当前文档**上正式构建：\n\n"
        u"文档：%s\n"
        u"配方：%s\n\n"
        u"⚠️ 构建会**先清场**，以下类别里的现有构件会被删除后重建：\n"
        u"%s\n\n"
        u"这是配置驱动建模的设计行为（不是 bug）。\n"
        u"建议先存盘或另存副本。\n\n"
        u"确认现在构建？" % (doc_name, os.path.basename(yaml_path), cat_line),
        u"配置建模 · 清场确认（不可逆）")
    if not ok:
        out.print_md(u"\n已取消，未构建。")
        return

    # ---- 第 3 段：正式构建 ----
    out.print_md(u"\n## 第 2 段：正式构建")
    rc, tail = _run([py, gen_build, yaml_path], mcp_dir, run_log,
                    u"正式构建")
    if rc is None:
        _msg_box(tail, u"配置建模 · 启动失败")
        return
    _show_tail(tail)
    if rc == 0:
        out.print_md(u"\n✅ 构建完成（exit=0）。日志：`%s`" % run_log)
        _msg_box(u"构建完成。\n\n日志：%s" % run_log, u"配置建模")
    else:
        out.print_md(u"\n❌ 构建未完成（exit=%s：%s）。日志：`%s`"
                     % (rc, EXIT_MEANING.get(rc, u"未知"), run_log))
        _msg_box(u"构建未完成（exit=%s：%s）\n\n日志：%s"
                 % (rc, EXIT_MEANING.get(rc, u"未知"), run_log),
                 u"配置建模 · 失败")


if __name__ == "__main__":
    main()
else:
    main()
