# -*- coding: utf-8 -*-
"""BIMToolkit MCP 配置写入器（Python 3，仅标准库）。

把 revit-mcp 网关注册进 WorkBuddy 的 mcp.json（合并，不覆盖其他 server）。
用法:
  python configure_mcp.py --python <python.exe路径> --gateway <revit_mcp_gateway.py路径>
"""
import io
import json
import os
import shutil
import sys


def main():
    py = None
    gw = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--python" and i + 1 < len(args):
            py = args[i + 1]
            i += 2
        elif args[i] == "--gateway" and i + 1 < len(args):
            gw = args[i + 1]
            i += 2
        else:
            i += 1
    if not py or not gw:
        print("usage: configure_mcp.py --python <exe> --gateway <py>")
        return 2

    cfg_dir = os.path.join(os.path.expanduser("~"), ".workbuddy")
    cfg_path = os.path.join(cfg_dir, "mcp.json")

    cfg = {}
    if os.path.exists(cfg_path):
        try:
            with io.open(cfg_path, "r", encoding="utf-8-sig") as f:
                cfg = json.loads(f.read())
        except Exception as e:
            print("[x] mcp.json 解析失败: %s" % e)
            print("    请手动检查: %s" % cfg_path)
            return 1
        try:
            shutil.copyfile(cfg_path, cfg_path + ".bak")
            print("[i] 已备份原配置 -> mcp.json.bak")
        except Exception:
            pass
    else:
        os.makedirs(cfg_dir, exist_ok=True)

    if not isinstance(cfg, dict):
        cfg = {}
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    cfg["mcpServers"] = servers
    servers["revit"] = {"command": py, "args": [gw]}

    with io.open(cfg_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2))

    print("[OK] 已写入: %s" % cfg_path)
    print("     revit -> %s %s" % (py, gw))
    print("")
    print("下一步:")
    print("  1. 打开 WorkBuddy 连接器管理页（右上角自定义连接器入口）")
    print("  2. 找到 revit，点击 Trust（信任）")
    print("  3. 在 Revit 里点一次「MCP Bridge」按钮启动桥")
    print("  4. 回到对话，说: 在 Revit 里建一面 3000x200x3000 的墙")
    return 0


if __name__ == "__main__":
    sys.exit(main())
