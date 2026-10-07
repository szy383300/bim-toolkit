# -*- coding: utf-8 -*-
u"""bridge_client.py - 兼容入口 (实现已移至 genbuild.bridge)。

历史脚本 `from bridge_client import call` 与命令行用法保持不变:
  python bridge_client.py send <cmd.json file> [out.json]
  python bridge_client.py call '<json string>'

改进: 连接/认证/协议错误统一包装为 BridgeError (不再裸抛
ConnectionRefusedError / SystemExit), socket 保证关闭。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genbuild.bridge import BridgeClient, call  # noqa: E402,F401
from genbuild.errors import BridgeError  # noqa: E402,F401

if __name__ == u"__main__":
    if len(sys.argv) < 3:
        print(u"用法: python bridge_client.py send <cmd.json> [out.json]"
              u" | call '<json>'")
        sys.exit(2)
    mode = sys.argv[1]
    if mode == u"send":
        import io
        cmd = json.load(io.open(sys.argv[2], encoding=u"utf-8-sig"))
    else:
        cmd = json.loads(sys.argv[2])
    try:
        out = call(cmd)
    except BridgeError as ex:
        print(u"[桥错误] %s" % ex)
        sys.exit(2)
    txt = json.dumps(out, ensure_ascii=False, indent=1)
    if len(sys.argv) > 3:
        import io
        io.open(sys.argv[3], u"w", encoding=u"utf-8").write(txt)
        print(u"saved to", sys.argv[3])
    print(txt[:4000])
