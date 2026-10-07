# -*- coding: utf-8 -*-
u"""桥部署一键同步（版本冻结工作流的配套工具）。

用法:  python sync_bridge.py [dev_file 相对名]
默认同步整个 pushbutton 目录（bridge_core.py / script.py / 配置 / 图标）。

规则（2026-09-12 起生效）:
  - dev 目录 = 本仓库的 pyrevit-bim-panel/...（唯一编辑点）
  - 同步目标 = bimtoolkit-release + AppData pyRevit 实际加载路径
  - 桥冻结在 7.10.4：实验性建模逻辑一律放本仓库 mcp/ 下的脚本，
    走 execute_code 通道执行，不再改 bridge_core.py / 不升版本 / 不用点按钮
  - 本脚本跑完打印三副本哈希，必须全一致才算部署成功
"""
import hashlib
import json
import os
import shutil
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APPDATA = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
_PANEL = os.path.join("BIMToolkit.extension", "BIMToolkit.tab",
                      "BIM 工具.panel", "MCP Bridge.pushbutton")

DEV = os.path.join(_REPO, "pyrevit-bim-panel", _PANEL)
DSTS = [
    os.path.join(_REPO, "bimtoolkit-release", _PANEL),
    os.path.join(_APPDATA, "pyRevit", "Extensions", _PANEL),
]
SKIP_DIRS = ("__pycache__",)


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()[:10]


def main():
    names = sys.argv[1:] or [f for f in sorted(os.listdir(DEV))
                             if f not in SKIP_DIRS]
    ok = True
    for name in names:
        src = os.path.join(DEV, name)
        if not os.path.isfile(src):
            print(u"[跳过] %s（dev 中不存在）" % name)
            continue
        h0 = md5(src)
        line = [u"%s %s" % (name, h0)]
        for dst_root in DSTS:
            dst = os.path.join(dst_root, name)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            h = md5(dst)
            same = (h == h0)
            ok = ok and same
            tag = os.path.splitdrive(dst_root)[0] + \
                (u"…Roaming" if "Roaming" in dst_root else u"…release")
            line.append(u"%s=%s%s" % (tag, h, u"" if same else u" MISMATCH"))
        print(u" | ".join(line))
    # 顺带汇报桥版本与 execute_code 开关
    core = os.path.join(DEV, "bridge_core.py")
    for ln in open(core, encoding="utf-8", errors="replace"):
        if "BRIDGE_VERSION" in ln:
            print(ln.strip())
            break
    cfg = json.load(open(os.path.join(DEV, "mcp_bridge_config.json"),
                         encoding="utf-8-sig"))
    print(u"allow_execute_code =", cfg.get("allow_execute_code"))
    print(u"部署%s" % (u"成功：三副本一致" if ok else u"失败：存在不一致！"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
