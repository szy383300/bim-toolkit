# -*- coding: utf-8 -*-
"""
deploy.py —— 一键部署 BIMToolkit 扩展到 pyRevit 用户扩展目录。

默认做五件事：
  1) 部署前先备份当前已装版本到 last-good/<时间戳>/BIMToolkit.extension
  2) 把仓库 BIMToolkit.extension 镜像到 %APPDATA%\\pyRevit\\Extensions\\BIMToolkit.extension
     （镜像时排除运行时状态：bridge.token / mcp_audit.jsonl / mcp_crash.log /
       mcp_bridge.log / logs_archive\\ / *.bak-*，避免删掉审计流水）
  2b) 收紧 bridge.token 的 ACL 为“仅当前用户可读”
  3) 写版本留底：**仓库 + 安装目录都写** version.txt
     （时间 + 短 git commit + extension.json 版本，用于区分多份副本）
  4) 部署后自校验：逐文件哈希比对 SRC 与 DST，不一致则 exit=1 且不打标签
  5) 若本目录是 git 仓库，自动打 deploy-<时间戳> 标签留底

本脚本是**开发机上的唯一部署通道**。`bimtoolkit-release\\install.bat` 是给
全新机器用的安装器，两者的排除表必须保持一致（见 RUNTIME_EXCLUDE_*）。

用法：
  python deploy.py            # 常规部署（镜像 + 备份 + 留底）
  python deploy.py --dry      # 只打印将做什么，不写盘
  python deploy.py --no-tag   # 不打 git 标签

注意：
  - 扩展目录在用户 AppData，无需管理员权限即可写入。
  - 若 Revit 启动后面板不出现、报缺 Xceed.Wpf.AvalonDock，请另以管理员运行
    fix_pyrevit_dll.bat（DLL 需写 Program Files，本脚本不管）。
"""
import os
import sys
import shutil
import subprocess
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "BIMToolkit.extension")
APPDATA = os.environ.get("APPDATA") or os.path.join(
    os.path.expanduser("~"), "AppData", "Roaming")
DST = os.path.join(APPDATA, "pyRevit", "Extensions", "BIMToolkit.extension")
BACKUP_ROOT = os.path.join(HERE, "last-good")
VERSION_FILE = os.path.join(HERE, "version.txt")
ROBOCOPY = r"C:\Windows\System32\robocopy.exe"

# --- 运行时状态排除表 ------------------------------------------------------
# 这些文件只在“目标目录”里存在，由 MCP Bridge 运行时生成，仓库里没有。
# 不加排除时 robocopy /MIR 会把它们当成“源里没有的多余文件”删掉，
# 而它们是审计与排障的唯一凭据（mcp_audit.jsonl / logs_archive\ / mcp_crash.log）。
#
# 2026-10-01 修复：此前 /MIR 没有任何 /XD /XF，每次部署都会清空审计流水。
# 本排除表必须与 bimtoolkit-release\install.bat 第 2 步保持一致——
# 两处不一致就会出现“用不同的工具部署得到不同结果”的漂移。
RUNTIME_EXCLUDE_DIRS = ["__pycache__", "logs_archive"]
RUNTIME_EXCLUDE_FILES = ["bridge.token", "mcp_audit.jsonl",
                         "mcp_bridge.log", "mcp_crash.log",
                         "*.bak-*", "*.bak_*"]

# 部署时才产生的文件（只存在于目标目录）：自校验必须忽略，否则会误报"多余"。
DEPLOY_LOCAL_FILES = ["version.txt"]

# S8：版本留底同时写进**安装目录**。此前只写仓库内的 version.txt，
# 光看安装目录根本不知道装的是哪一版 —— 而桌面四个 .bat 全部指向安装目录。
DST_VERSION_FILE = os.path.join(DST, "version.txt")
EXT_JSON = os.path.join(SRC, "extension.json")


def _now():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def _git_short_hash():
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=HERE, stderr=subprocess.DEVNULL)
        return out.decode("utf-8", "replace").strip()
    except Exception:
        return ""


def _is_git():
    try:
        subprocess.check_output(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=HERE, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _robocopy_argv(src, dst):
    """构造镜像命令行（含运行时状态排除）。"""
    argv = [ROBOCOPY, src, dst, "/MIR", "/NP", "/R:1", "/W:1"]
    if RUNTIME_EXCLUDE_DIRS:
        argv += ["/XD"] + RUNTIME_EXCLUDE_DIRS
    if RUNTIME_EXCLUDE_FILES:
        argv += ["/XF"] + RUNTIME_EXCLUDE_FILES
    return argv


def _robocopy_mirror(src, dst, dry):
    """用 robocopy 做真·镜像（含删除 DST 中 SRC 没有的文件）。

    运行时状态文件（token / 审计流水 / 日志 / 备份）由 RUNTIME_EXCLUDE_*
    排除，既不复制也不删除。返回是否成功。
    """
    argv = _robocopy_argv(src, dst)
    if dry:
        print("    [DRY] " + " ".join('"%s"' % a if " " in a else a
                                      for a in argv))
        return True
    os.makedirs(dst, exist_ok=True)
    rc = 1
    try:
        proc = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        rc = proc.returncode
    except Exception as e:
        print("    [ERR] 调用 robocopy 失败: %s" % e)
        return False
    # robocopy 退出码 0~7 均视为成功（8+ 才是真的失败）
    if rc > 7:
        tail = proc.stdout.decode("utf-8", "replace").strip().splitlines()[-8:]
        print("    [ERR] robocopy 返回 %d：" % rc)
        for ln in tail:
            print("      " + ln)
        return False
    return True


def _backup(dst, backup_root, dry):
    if not os.path.isdir(dst):
        print("  [跳过] 目标未安装，无需备份：%s" % dst)
        return True
    ts = _now()
    backup_dir = os.path.join(backup_root, "BIMToolkit.extension_%s" % ts)
    if dry:
        print("    [DRY] 备份 %s -> %s" % (dst, backup_dir))
        return True
    os.makedirs(backup_root, exist_ok=True)
    try:
        shutil.copytree(dst, backup_dir, dirs_exist_ok=True,
                        copy_function=shutil.copy2)
        print("  [OK] 已备份上一版到 %s" % backup_dir)
        return True
    except Exception as e:
        print("  [ERR] 备份失败: %s" % e)
        return False


TOKEN_REL = os.path.join("BIMToolkit.tab", "BIM 工具.panel",
                         "MCP Bridge.pushbutton", "bridge.token")


def _harden_token_acl(dst, dry):
    """把 bridge.token 的 ACL 收紧为“仅当前用户可读”。

    桥把 token 明文写在脚本同目录。默认 ACL 下同机其他用户可以读到它，
    而 bridge_core.py 的认证只是 `cmd["token"] == TOKEN`——读到即等于通过认证。
    这里做“清除继承 + 只授权当前用户”，作为纵深防御的第一层。

    更彻底的做法是在 bridge_core.py 生成 token 时（load_or_create_token）
    就地设 ACL；因项目有“桥冻结、不再改 bridge_core.py”的约定，此处未改。
    """
    path = os.path.join(dst, TOKEN_REL)
    if not os.path.isfile(path):
        print("  [跳过] 目标无 bridge.token（桥尚未启动过，属正常）")
        return
    user = os.environ.get("USERNAME") or ""
    if not user:
        print("  [跳过] 取不到 USERNAME，无法设 ACL")
        return
    if dry:
        print('    [DRY] icacls "%s" /inheritance:r /grant:r "%s:(R)"'
              % (path, user))
        return
    try:
        proc = subprocess.run(
            ["icacls", path, "/inheritance:r", "/grant:r", "%s:(R)" % user],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if proc.returncode == 0:
            print("  [OK] 已收紧 bridge.token ACL（仅 %s 可读）" % user)
        else:
            print("  [WARN] icacls 返回 %d：%s"
                  % (proc.returncode,
                     proc.stdout.decode("utf-8", "replace").strip()[:200]))
    except Exception as e:
        print("  [WARN] 设置 ACL 失败: %s" % e)


def _ext_version():
    """读 extension.json 的 version 字段（读不到返回空串）。"""
    import json
    try:
        with open(EXT_JSON, "rb") as f:
            raw = f.read()
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        return json.loads(raw.decode("utf-8")).get("version", "") or ""
    except Exception:
        return ""


def _excluded(rel):
    """rel 是否落在排除表内（与 robocopy 的 /XD /XF 同口径）。"""
    import fnmatch
    parts = rel.replace("/", "\\").split("\\")
    for p in parts:
        if p in RUNTIME_EXCLUDE_DIRS:
            return True
    name = parts[-1]
    for pat in RUNTIME_EXCLUDE_FILES + DEPLOY_LOCAL_FILES:
        if fnmatch.fnmatch(name, pat):
            return True
    return False


def _walk_files(root):
    """列出 root 下所有文件的 {相对路径: md5}，套用与 robocopy 相同的排除表。"""
    import hashlib
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in RUNTIME_EXCLUDE_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if _excluded(rel):
                continue
            try:
                with open(full, "rb") as f:
                    out[rel] = hashlib.md5(f.read()).hexdigest()
            except Exception as e:
                out[rel] = "<不可读: %s>" % e
    return out


def _verify_mirror(src, dst):
    """部署后自校验：逐文件比对 SRC 与 DST（同排除口径）。

    返回 (ok, problems)。这是"部署成功"的**唯一凭据** ——
    不看 robocopy 的退出码（0~7 都算成功），只看实际落地结果。
    """
    a = _walk_files(src)
    b = _walk_files(dst)
    problems = []
    for rel in sorted(a):
        if rel not in b:
            problems.append("缺失: %s" % rel)
        elif a[rel] != b[rel]:
            problems.append("内容不同: %s" % rel)
    for rel in sorted(b):
        if rel not in a:
            problems.append("多余（已装独有）: %s" % rel)
    return (not problems), problems


def _write_version(dry):
    """写版本留底：**仓库 + 安装目录都要写**。

    内容形如 `20261001-180450  deploy  38d2c06  v2.0.0`：
    时间 + 短 git commit + extension.json 版本 —— 三者合起来才能区分
    "四份副本都自称 v2.0.0"的那种混乱。
    """
    ts = _now()
    ghash = _git_short_hash()
    ev = _ext_version()
    parts = [ts, "deploy"]
    if ghash:
        parts.append(ghash)
    if ev:
        parts.append("v%s" % ev)
    line = "  ".join(parts)
    if dry:
        print("    [DRY] 写 version.txt（仓库 + 安装目录）: %s" % line)
        return
    for path, label in ((VERSION_FILE, "仓库"),
                        (DST_VERSION_FILE, "安装目录")):
        try:
            with open(path, "w") as f:
                f.write(line + "\n")
            print("  [OK] 已写%s version.txt: %s" % (label, line))
        except Exception as e:
            print("  [WARN] 写%s version.txt 失败: %s" % (label, e))


def _git_tag(dry):
    if not _is_git():
        print("  [跳过] 非 git 仓库，不打标签")
        return
    # 仓库尚无任何提交时无法打标签（git tag 需要有效对象），优雅跳过
    try:
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=HERE, stderr=subprocess.DEVNULL)
    except Exception:
        print("  [跳过] 仓库尚无提交，跳过打标签（先 git commit 后再部署即可留底）")
        return
    ts = _now()
    tag = "deploy-%s" % ts
    if dry:
        print("    [DRY] git tag %s" % tag)
        return
    try:
        subprocess.check_call(["git", "tag", tag], cwd=HERE)
        print("  [OK] 已打 git 标签 %s" % tag)
    except Exception as e:
        print("  [WARN] 打 git 标签失败（不影响部署）: %s" % e)


def main():
    dry = "--dry" in sys.argv
    no_tag = "--no-tag" in sys.argv
    print("=== BIMToolkit 部署 ===")
    print("  源      : %s" % SRC)
    print("  目标    : %s" % DST)
    print("  模式    : %s" % ("干跑(不写盘)" if dry else "正式部署"))

    if not os.path.isdir(SRC):
        print("[FAIL] 找不到源目录：%s" % SRC)
        sys.exit(1)

    print("\n[1] 备份当前已装版本")
    if not _backup(DST, BACKUP_ROOT, dry):
        print("[FAIL] 备份失败，已中止以免丢失现有版本")
        sys.exit(1)

    print("\n[2] 镜像部署扩展")
    if not _robocopy_mirror(SRC, DST, dry):
        print("[FAIL] 部署失败")
        sys.exit(1)
    print("  [OK] 已镜像 BIMToolkit.extension -> %s" % DST)

    print("\n[2b] 收紧 bridge.token 权限")
    _harden_token_acl(DST, dry)

    print("\n[3] 写版本留底（仓库 + 安装目录）")
    _write_version(dry)

    print("\n[4] 部署后自校验（逐文件哈希）")
    if dry:
        print("    [DRY] 跳过（干跑未写盘）")
        verify_ok = True
    else:
        verify_ok, problems = _verify_mirror(SRC, DST)
        if verify_ok:
            print("  [OK] 已装副本与仓库逐文件一致（%d 个文件）"
                  % len(_walk_files(SRC)))
        else:
            print("  [FAIL] 自校验不通过，%d 处不一致：" % len(problems))
            for p in problems[:40]:
                print("      " + p)
            if len(problems) > 40:
                print("      ...（还有 %d 处）" % (len(problems) - 40))

    if not verify_ok:
        # 自校验失败**不打标签**：不能让一个没落地的版本留下"已部署"的痕迹。
        print("\n[FAIL] 部署未通过自校验，已跳过 git 标签。")
        sys.exit(1)

    if not no_tag:
        print("\n[5] git 标签")
        _git_tag(dry)

    print("\n[完成] 重启 Revit（或 pyRevit Reload）后生效。")
    if not dry:
        print("  若面板不出现、报缺 Xceed.Wpf.AvalonDock，请管理员运行 fix_pyrevit_dll.bat")
    sys.exit(0)


if __name__ == "__main__":
    main()
