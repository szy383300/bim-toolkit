# -*- coding: utf-8 -*-
"""mock_bridge + MCP 适配器端到端测试。"""
import os
import sys
import json
import socket
import threading
import subprocess
import tempfile

PY = sys.executable
ADAPTER = r"E:\bim-toolkit\mcp\bimtoolkit_mcp.py"
PORT = 9987
TOKEN = "t" * 64

# 1) 假桥
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT))
srv.listen(1)
got = {}

def bridge():
    conn, _ = srv.accept()
    buf = b""
    while True:
        while b"\n" not in buf:
            c = conn.recv(4096)
            if not c:
                return
            buf += c
        line, buf = buf.split(b"\n", 1)
        cmd = json.loads(line.decode("utf-8"))
        t = cmd.get("type")
        if t == "auth":
            assert cmd.get("token") == TOKEN
            conn.sendall(json.dumps({"ok": True}).encode() + b"\n")
        elif t == "ping":
            conn.sendall(json.dumps(
                {"id": cmd.get("id"),
                 "result": {"pong": True, "document": " MOCK.rvt",
                            "version": "7.7.9-mock"}}).encode() + b"\n")
        elif t == "get_levels":
            conn.sendall(json.dumps(
                {"id": cmd.get("id"),
                 "result": {"levels": [{"name": "1F"}, {"name": "2F"}]}}).encode() + b"\n")
        elif t == "create_walls":
            # 模拟 ExternalEvent 异步慢回
            conn.sendall(json.dumps(
                {"id": cmd.get("id"),
                 "result": {"created": len(cmd.get("walls", [])),
                            "commits": ["Committed"]}}).encode() + b"\n")
        else:
            conn.sendall(json.dumps(
                {"id": cmd.get("id"), "error": "Unknown command: " + str(t)}).encode() + b"\n")

th = threading.Thread(target=bridge, daemon=True)
th.start()

# 2) 起适配器（子进程），走 stdio MCP
env = dict(os.environ, BIMTOOLKIT_PORT=str(PORT), BIMTOOLKIT_TOKEN=TOKEN)
proc = subprocess.Popen([PY, ADAPTER], stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

def mcp(msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
    proc.stdin.flush()
    line = proc.stdout.readline().decode("utf-8")
    return json.loads(line)

# initialize
r = mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"}}})
assert r["result"]["serverInfo"]["name"] == "bimtoolkit-mcp", r
print("[1] initialize OK ->", r["result"]["serverInfo"])

# tools/list
r = mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = r["result"]["tools"]
print("[2] tools/list OK ->", len(tools), "个工具:", [t["name"] for t in tools][:6], "...")
assert any(t["name"] == "create_walls" for t in tools)

# tools/call ping（触发连接+认证）
r = mcp({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "ping", "arguments": {}}})
txt = r["result"]["content"][0]["text"]
print("[3] ping OK ->", txt[:120])
assert "MOCK.rvt" in txt

# tools/call get_levels
r = mcp({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "get_levels", "arguments": {}}})
txt = r["result"]["content"][0]["text"]
print("[4] get_levels OK ->", txt[:120])
assert "2F" in txt

# tools/call create_walls（写命令）
r = mcp({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "create_walls",
                    "arguments": {"walls": [{"start": [0, 0], "end": [5000, 0]}]}}})
txt = r["result"]["content"][0]["text"]
print("[5] create_walls OK ->", txt[:120])
assert "Committed" in txt

# tools/call 未知工具
r = mcp({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}})
assert r["result"]["isError"] is True
print("[6] 未知工具正确报错 OK")

proc.kill()
print("\n[PASS] MCP 适配器端到端全部通过")
