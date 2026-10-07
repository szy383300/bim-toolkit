#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""revit-mcp-gateway 端到端测试：mock 桥（模拟 Revit 内 Bridge v7）+ stdio 网关子进程。

跑法: python test_gateway_e2e.py
覆盖: initialize / tools/list / tools/call(读+写) / resources / 错误 token / 桥断连提示
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GATEWAY = os.path.join(HERE, "revit_mcp_gateway.py")
TEST_PORT = 19999
TOKEN = "t" * 64  # 64 hex-ish chars, >=32

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("PASS  {}".format(name))
    else:
        FAIL.append(name)
        print("FAIL  {}  {}".format(name, detail))


# ---------------------------------------------------------------------------
# Mock bridge: newline-framed v7 protocol
# ---------------------------------------------------------------------------

def handle_auth(state, cmd):
    if cmd.get("token") == TOKEN:
        state["authed"] = True
        return {"ok": True, "server": "mcp-bridge", "version": "7.0"}
    return {"ok": False, "error": "auth failed"}


def handle_cmd(cmd):
    ctype = cmd.get("type")
    cid = cmd.get("id")
    if ctype == "get_info":
        return {"id": cid, "result": {
            "title": "测试项目.rvt", "instance_count": 42,
            "categories": {"墙": 20, "门": 22},
            "levels": [{"id": 1, "name": "1F", "elevation": 0.0}]}}
    if ctype == "create_walls":
        n = len(cmd.get("walls", []))
        return {"id": cid, "result": {"created": n, "failed": 0, "errors": []}}
    if ctype == "ping":
        return {"id": cid, "result": {"pong": True}}
    return {"id": cid, "error": "unknown command: {}".format(ctype)}


def serve_client(conn, expect_token):
    state = {"authed": False}
    buf = b""
    conn.settimeout(10)
    try:
        while True:
            data = conn.recv(65536)
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                cmd = json.loads(line.decode("utf-8"))
                if cmd.get("type") == "auth":
                    if cmd.get("token") != expect_token:
                        conn.sendall((json.dumps(
                            {"ok": False, "error": "auth failed"}) + "\n")
                            .encode("utf-8"))
                        return
                    state["authed"] = True
                    conn.sendall((json.dumps(
                        {"ok": True, "server": "mcp-bridge",
                         "version": "7.0"}) + "\n").encode("utf-8"))
                    continue
                if not state["authed"]:
                    conn.sendall((json.dumps(
                        {"error": "auth required"}) + "\n").encode("utf-8"))
                    return
                conn.sendall((json.dumps(handle_cmd(cmd)) + "\n")
                             .encode("utf-8"))
    except (socket.timeout, ConnectionError, OSError):
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def start_mock_bridge(token, port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(5)
    t = threading.Thread(target=_accept_loop, args=(srv, token), daemon=True)
    t.start()
    return srv


def _accept_loop(srv, token):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        threading.Thread(target=serve_client,
                         args=(conn, token), daemon=True).start()


# ---------------------------------------------------------------------------
# Gateway driver
# ---------------------------------------------------------------------------

class GatewayProc:
    def __init__(self, token_file):
        self.proc = subprocess.Popen(
            [sys.executable, GATEWAY,
             "--token-file", token_file, "--port", str(TEST_PORT),
             "--timeout", "5"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)

    def send(self, obj):
        self.proc.stdin.write(
            (json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def recv(self, timeout=10):
        # 按行读（阻塞读带超时用线程兜底）
        result = {}

        def _read():
            result["line"] = self.proc.stdout.readline()
        t = threading.Thread(target=_read)
        t.start()
        t.join(timeout)
        if not t.is_alive() and result.get("line"):
            return json.loads(result["line"].decode("utf-8"))
        return None

    def call(self, method, params=None, rid=None):
        rid = rid if rid is not None else int(time.time() * 1000) % 100000
        self.send({"jsonrpc": "2.0", "id": rid,
                   "method": method, "params": params or {}})
        return self.recv()

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


def main():
    # token 文件
    tf = tempfile.NamedTemporaryFile("w", suffix=".token", delete=False,
                                     encoding="utf-8")
    tf.write(TOKEN)
    tf.close()

    badf = tempfile.NamedTemporaryFile("w", suffix=".token", delete=False,
                                       encoding="utf-8")
    badf.write("f" * 64)
    badf.close()

    mock = start_mock_bridge(TOKEN, TEST_PORT)
    time.sleep(0.3)

    # ---- 正常链路 ----
    gw = GatewayProc(tf.name)
    try:
        r = gw.call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        check("initialize 应答", r and r["result"]["serverInfo"]["name"] == "revit-mcp")

        gw.notify("notifications/initialized")
        r = gw.call("tools/list")
        names = [t["name"] for t in r["result"]["tools"]] if r else []
        check("tools/list 返回 17 个原子工具", len(names) == 17, str(names))
        for new_tool in ("revit_create_grids", "revit_create_levels",
                         "revit_query_elements", "revit_get_quantities"):
            check("v7.7 新工具已注册: " + new_tool, new_tool in names)
        check("execute_code 默认不在工具列表",
              "revit_execute_code" not in names, str(names))

        r = gw.call("tools/call", {"name": "revit_get_info", "arguments": {}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("tools/call get_info 到达 mock 桥", payload.get("title") == "测试项目.rvt")

        r = gw.call("tools/call", {"name": "revit_create_walls", "arguments": {
            "walls": [{"start": [0, 0], "end": [6000, 0]},
                      {"start": [6000, 0], "end": [6000, 9000]}]}})
        payload = json.loads(r["result"]["content"][0]["text"])
        check("tools/call create_walls 原子写工具", payload.get("created") == 2)

        r = gw.call("resources/list")
        uris = [x["uri"] for x in r["result"]["resources"]] if r else []
        check("resources/list", "revit://model/summary" in uris)

        r = gw.call("resources/read", {"uri": "revit://model/summary"})
        payload = json.loads(r["result"]["contents"][0]["text"])
        check("resources/read 摘要", payload.get("instance_count") == 42)

        r = gw.call("tools/call", {"name": "revit_get_element",
                                   "arguments": {"id": 999}})
        check("错误 id 应映射桥错误(err路径)", r is not None and (
            "error" in r or r["result"].get("isError")))
    finally:
        gw.close()

    # ---- 错误 token ----
    gw2 = GatewayProc(badf.name)
    try:
        gw2.call("initialize", {})
        r = gw2.call("tools/call", {"name": "revit_get_info", "arguments": {}})
        check("错误 token 被拒", r is not None and "error" in r, str(r))
    finally:
        gw2.close()

    # ---- 桥未启动（换个没人监听的端口） ----
    try:
        p = subprocess.Popen(
            [sys.executable, GATEWAY, "--token-file", tf.name,
             "--port", "29876", "--timeout", "3"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        # initialize 是懒连接，不应碰桥
        p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 1,
                                   "method": "initialize",
                                   "params": {}}) + "\n").encode("utf-8"))
        p.stdin.flush()
        line = p.stdout.readline()
        r = json.loads(line.decode("utf-8")) if line else None
        check("桥未启动时 initialize 正常（懒连接）",
              r is not None and "result" in r, str(r))
        # 第一次 tools/call 才连桥 -> 人话错误
        p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": 2,
                                   "method": "tools/call",
                                   "params": {"name": "revit_get_info",
                                              "arguments": {}}}) + "\n")
                      .encode("utf-8"))
        p.stdin.flush()
        line = p.stdout.readline()
        r = json.loads(line.decode("utf-8")) if line else None
        err = (r or {}).get("error", {}).get("message", "")
        check("桥未启动时 tools/call 给人话错误",
              r is not None and "error" in r and "MCP Bridge" in err, str(r))
        p.stdin.close()
        p.wait(timeout=3)
    finally:
        pass

    mock.close()
    os.unlink(tf.name)
    os.unlink(badf.name)

    print("\n结果: {} 通过, {} 失败".format(len(PASS), len(FAIL)))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
