# -*- coding: utf-8 -*-
"""build_release.py —— 从唯一权威源重建 bimtoolkit-release 发布件。

为什么需要它：`bimtoolkit-release/BIMToolkit.extension/` 与 `revit-mcp/` 被
.gitignore 忽略，而仓库里原本没有任何脚本能生成它们（`git ls-files
bimtoolkit-release` 只有 3 个文件），所以那是一份**手工快照**。后果已经发生过：
快照比源码少 6 个 lib 模块和整个「配置建模」按钮，桥停在 7.10.4 —— 那个版本
没有 COMMAND_TIERS，等于没有策略档位 —— 而它自己的 README 还写着
「execute_code 默认关闭」，配置却写着 allow_execute_code=true。

用法：
    python tools/build_release.py              # 重建发布件 + 出 zip
    python tools/build_release.py --check-only # 只校验，不写盘（CI 闸门用）
    python tools/build_release.py --no-zip     # 只重建目录，不打包

退出码：0 成功 / 1 校验或产物不合格 / 2 源缺失
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SRC_EXT = os.path.join(REPO, "pyrevit-bim-panel", "BIMToolkit.extension")
SRC_MCP = os.path.join(REPO, "revit-mcp")
REL_DIR = os.path.join(REPO, "bimtoolkit-release")
DST_EXT = os.path.join(REL_DIR, "BIMToolkit.extension")
DST_MCP = os.path.join(REL_DIR, "revit-mcp")

# 目录名黑名单：整目录不进发布件
EXCLUDE_DIRS = {"__pycache__", "logs_archive", "last-good", ".build-tmp"}

# 文件名黑名单（含通配）：运行态、编译产物、备份、本机配置
EXCLUDE_FILES = [
    "*.pyc", "*.bak-*", "*.bak_*", "*.log", "*.jsonl",
    "bridge.token", "version.txt",
    "mcp_bridge_config.json",       # 由本脚本按安全默认值重新生成
    "deepseek_config.json",          # 真实密钥只在本机/环境变量
    "ai_advisor_config.json",
]

# 绝不该再出现在发布件里的东西：S8 已因"点击即崩"退役的按钮 + 本机路径写死者
FORBIDDEN_NAMES = [u"写入诊断"]

# 真实密钥形态（S1 已吊销的那把也是这个形状）
SECRET_RE = re.compile(r"sk-[0-9a-f]{28,}")

# 本脚本自己生成、允许出现在发布件里的文件（其余命中 EXCLUDE_FILES 的都算脏）
GENERATED_ALLOWED = {"mcp_bridge_config.json", "version.txt"}

BRIDGE_REL = os.path.join("BIMToolkit.tab", u"BIM 工具.panel",
                          "MCP Bridge.pushbutton")


def _matched(name, patterns):
    from fnmatch import fnmatch
    for p in patterns:
        if fnmatch(name, p):
            return True
    return False


def _walk(src_root, allow_generated=False):
    """列出 (相对路径, 绝对路径)，按排除表过滤。

    allow_generated=True 时放行本脚本自己生成的文件（发布件打包要用，
    否则刚写进去的安全档桥配置会被排除表再次滤掉）。
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(src_root):
        dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
        for fn in sorted(filenames):
            if _matched(fn, EXCLUDE_FILES):
                if not (allow_generated and fn in GENERATED_ALLOWED):
                    continue
            rel = os.path.relpath(os.path.join(dirpath, fn), src_root)
            out.append((rel.replace("\\", "/"), os.path.join(dirpath, fn)))
    return out


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_short():
    try:
        return subprocess.check_output(
            ["git", "-C", REPO, "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL).decode("utf-8").strip()
    except Exception:
        return "nogit"


def _ext_version():
    p = os.path.join(SRC_EXT, "extension.json")
    try:
        with open(p, "rb") as f:
            return json.load(f).get("version", "0.0.0")
    except Exception:
        return "0.0.0"


def _bridge_version(text):
    m = re.search(r'BRIDGE_VERSION\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else "?"


def _build_bridge_config():
    """发布件的桥配置：只给 build 档。

    刻意不带 _why_dev 那类说明，也不给 dev —— 这份文件会被部署到别人机器上。
    """
    return u"""{
  "_readme": "由 tools/build_release.py 生成，请勿手改；要改默认档请改生成脚本。",
  "_policy": "safe=只读 | build=只读+有语义的写 | dev=全开（含 execute_code）。",
  "_note": "发布件固定 build。execute_code 不开放：需要它的流程应改成类型化命令。",
  "policy": "build",
  "allow_execute_code": false
}
"""


def _stage_copy(src_root, dst_root, label):
    entries = _walk(src_root)
    if not entries:
        raise RuntimeError("%s 源目录里没有可发布文件：%s" % (label, src_root))
    for rel, abs_src in entries:
        dst = os.path.join(dst_root, *rel.split("/"))
        d = os.path.dirname(dst)
        if not os.path.isdir(d):
            os.makedirs(d)
        shutil.copy2(abs_src, dst)
    return entries


def build():
    """先建到 .build-tmp，成功后再替换正式目录，避免半成品发布件。"""
    tmp = os.path.join(REL_DIR, ".build-tmp")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    ext_tmp = os.path.join(tmp, "BIMToolkit.extension")
    os.makedirs(ext_tmp)

    entries = _stage_copy(SRC_EXT, ext_tmp, u"扩展")
    if os.path.isdir(SRC_MCP):
        _stage_copy(SRC_MCP, os.path.join(tmp, "revit-mcp"), "revit-mcp")

    # 强制安全档
    cfg_dir = os.path.join(ext_tmp, BRIDGE_REL)
    if not os.path.isdir(cfg_dir):
        raise RuntimeError("发布件里找不到 MCP Bridge 目录：%s" % cfg_dir)
    with open(os.path.join(cfg_dir, "mcp_bridge_config.json"), "wb") as f:
        f.write(_build_bridge_config().encode("utf-8"))

    ver = _ext_version()
    commit = _git_short()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    with open(os.path.join(ext_tmp, "version.txt"), "wb") as f:
        f.write(("%s  build  %s  v%s\n" % (stamp, commit, ver)).encode("utf-8"))

    # 正式目录替换
    if os.path.isdir(DST_EXT):
        shutil.rmtree(DST_EXT)
    shutil.move(ext_tmp, DST_EXT)
    mcp_tmp = os.path.join(tmp, "revit-mcp")
    if os.path.isdir(mcp_tmp):
        if os.path.isdir(DST_MCP):
            shutil.rmtree(DST_MCP)
        shutil.move(mcp_tmp, DST_MCP)
    shutil.rmtree(tmp, ignore_errors=True)
    return entries, stamp, commit, ver


def scan_artifact(root):
    """产物体检。返回问题列表，空列表 = 合格。"""
    problems = []
    tiers_seen = False
    cfg_seen = False
    bver = "?"
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            if _matched(fn, EXCLUDE_FILES) and fn not in GENERATED_ALLOWED:
                problems.append("不该进发布件的文件：%s" % rel)
            for bad in FORBIDDEN_NAMES:
                if bad in rel:
                    problems.append(u"已退役构件仍在发布件里：%s" % rel)
            if fn.endswith(".py") or fn.endswith(".json"):
                try:
                    with open(full, "rb") as f:
                        text = f.read().decode("utf-8", "replace")
                except Exception:
                    continue
                m = SECRET_RE.search(text)
                if m:
                    problems.append("疑似真实密钥：%s（%s…）"
                                    % (rel, text[m.start():m.start() + 10]))
                if fn == "bridge_core.py":
                    bver = _bridge_version(text)
                    tiers_seen = bool(re.search(r"COMMAND_TIERS\s*=", text))
                if fn == "mcp_bridge_config.json":
                    cfg_seen = True
                    m = re.search(r'"policy"\s*:\s*"([a-z]+)"', text)
                    pol = m.group(1) if m else "?"
                    if pol not in ("build", "safe"):
                        problems.append(u"发布件桥策略是 %r，必须是 build/safe：%s"
                                        % (pol, rel))
                    if re.search(r'"allow_execute_code"\s*:\s*true', text):
                        problems.append(u"发布件桥配置开了 execute_code：%s" % rel)
    if not tiers_seen:
        problems.append(u"发布件里的 bridge_core.py 没有 COMMAND_TIERS —— "
                        u"等于没有策略档位（7.10.4 那代的老问题）")
    if not cfg_seen:
        problems.append(u"发布件里没有 MCP Bridge 的策略配置，"
                        u"装了会落回代码默认档，无法确认出厂策略")
    return problems, bver


def verify_matches_source():
    """发布件目录必须与源逐文件一致（桥配置与 version.txt 由本脚本改写，除外）。"""
    diffs = []
    src = dict(_walk(SRC_EXT))
    dst = dict(_walk(DST_EXT))
    exempt = {
        (os.path.join(BRIDGE_REL, "mcp_bridge_config.json")).replace("\\", "/"),
        "version.txt",
    }
    for rel, path in src.items():
        if rel in exempt:
            continue
        if rel not in dst:
            diffs.append(u"发布件缺文件：%s" % rel)
        elif _md5(path) != _md5(dst[rel]):
            diffs.append(u"内容不一致：%s" % rel)
    for rel in dst:
        if rel not in src and rel not in exempt:
            diffs.append(u"发布件多出源里没有的文件：%s" % rel)
    return diffs


def make_zip(ver, commit):
    out = os.path.join(REL_DIR, "BIMToolkit-Installer-v%s-%s.zip" % (ver, commit))
    if os.path.isfile(out):
        os.remove(out)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, abs_path in _walk(DST_EXT, allow_generated=True):
            z.write(abs_path, "BIMToolkit.extension/" + rel)
            n += 1
        for extra in ("install.bat", "configure_mcp.py", "README.md"):
            p = os.path.join(REL_DIR, extra)
            if os.path.isfile(p):
                z.write(p, extra)
                n += 1
        if os.path.isdir(DST_MCP):
            for rel, abs_path in _walk(DST_MCP):
                z.write(abs_path, "revit-mcp/" + rel)
                n += 1
    return out, n


def main(argv=None):
    ap = argparse.ArgumentParser(description="重建 bimtoolkit-release 发布件")
    ap.add_argument("--check-only", action="store_true",
                    help="只校验现有发布件是否等于源，不写盘")
    ap.add_argument("--no-zip", action="store_true", help="不生成 zip")
    args = ap.parse_args(argv)

    if not os.path.isdir(SRC_EXT):
        print("[FAIL] 找不到权威源目录：%s" % SRC_EXT)
        return 2

    if args.check_only:
        if not os.path.isdir(DST_EXT):
            print("[FAIL] 发布件不存在，请先跑 python tools/build_release.py")
            return 1
        diffs = verify_matches_source()
        problems, bver = scan_artifact(DST_EXT)
        for d in diffs + problems:
            print("  [X] %s" % d)
        if diffs or problems:
            print("[FAIL] 发布件与源不一致或不合格（发布桥 %s）" % bver)
            return 1
        print("[OK] 发布件与源逐文件一致；桥 %s 含策略档位；产物扫描干净" % bver)
        return 0

    entries, stamp, commit, ver = build()
    print("=== BIMToolkit 发布件重建 ===")
    print("  源      : %s" % SRC_EXT)
    print("  发布件  : %s" % DST_EXT)

    diffs = verify_matches_source()
    for d in diffs:
        print("  [X] %s" % d)
    problems, bver = scan_artifact(DST_EXT)
    for p in problems:
        print("  [X] %s" % p)
    if diffs or problems:
        print("[FAIL] 发布件已生成但未通过校验，不得分发")
        return 1

    def _count_buttons(panel):
        p = os.path.join(DST_EXT, "BIMToolkit.tab", panel)
        return len([d for d in os.listdir(p) if d.endswith(".pushbutton")])

    n_btn = _count_buttons(u"BIM 工具.panel")
    n_ai = _count_buttons(u"AI 智建.panel")
    n_cmd = 0
    bc = os.path.join(DST_EXT, BRIDGE_REL)
    with open(os.path.join(bc, "bridge_core.py"), "rb") as f:
        text = f.read().decode("utf-8", "replace")
    m = re.search(r"COMMAND_HANDLERS\s*=\s*\{(.*?)\n\}", text, re.S)
    if m:
        n_cmd = len(re.findall(r'["\'][a-z_]+["\']\s*:', m.group(1)))
    with open(os.path.join(bc, "mcp_bridge_config.json"), "rb") as f:
        pol = json.load(f).get("policy")

    # 发布凭据：四行，缺一不可
    print("  [凭据] 按钮 %d 个（AI 智建 %d / BIM 工具 %d）"
          % (n_btn + n_ai, n_ai, n_btn))
    print("  桥版本   : %s（含 COMMAND_TIERS 策略档位）" % bver)
    print("  命令数   : %d" % n_cmd)
    print("  出厂策略 : %s" % pol)
    print("  文件数   : %d，version.txt = %s build %s v%s"
          % (len(entries), stamp, commit, ver))

    if not args.no_zip:
        zp, zn = make_zip(ver, commit)
        print("  zip      : %s（%d 条目，%d KB）"
              % (os.path.basename(zp), zn, os.path.getsize(zp) // 1024))
    print("[OK] 发布件已重建并通过校验")
    return 0


if __name__ == "__main__":
    sys.exit(main())
