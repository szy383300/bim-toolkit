#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_test_v77.py — v7.7.0 四个新 MCP 工具的真机验证脚本（P0 验收用）。

前提:
  1. Revit 2019 已打开（建议开一个**空白/草稿**文档——本脚本会创建
     测试轴网/标高，删除工具还没有，建完只能手动删）
  2. 已点击「BIM 工具 -> MCP Bridge」按钮（桥监听 127.0.0.1:9877）

用法:
  python live_test_v77.py

用例:
  T1 revit_create_levels   建 2 个测试标高（重名第二个自动跳过）
  T2 revit_create_grids    1 条轴线 + 1 条显式线段（轴号自动分配）
  T3 revit_query_elements  查询刚建的标高/轴网，回读参数
  T4 revit_get_quantities  分类别工程量 + 造价估算
  T5 鉴权负例: 错 token 被拒
"""

import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HOST, PORT = "127.0.0.1", 9877
TIMEOUT = 60.0


def bridge_dir():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(
        appdata, "pyRevit", "Extensions", "BIMToolkit.extension",
        "BIMToolkit.tab", "BIM 工具.panel", "MCP Bridge.pushbutton")


def read_token():
    with open(os.path.join(bridge_dir(), "bridge.token"), "r",
              encoding="utf-8") as f:
        return f.read().strip()


class Client(object):
    """与网关同款协议客户端（换行分帧 + id 匹配）。"""

    def __init__(self, token):
        self.sock = socket.create_connection((HOST, PORT), timeout=5)
        self.buf = b""
        self.nid = 0
        self._send({"type": "auth", "token": token})
        resp = self._read(5)
        if not resp.get("ok"):
            raise RuntimeError("auth failed: {}".format(resp.get("error")))
        print("  auth OK, bridge version:", resp.get("version"))

    def _send(self, obj):
        self.sock.sendall(
            (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read(self, timeout):
        deadline = time.time() + timeout
        while b"\n" not in self.buf:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise RuntimeError("timeout waiting for bridge")
            self.sock.settimeout(remaining)
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError("bridge closed connection")
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    def call(self, cmd_type, args=None, timeout=TIMEOUT):
        self.nid += 1
        rid = self.nid
        msg = dict(args or {})
        msg["type"] = cmd_type
        msg["id"] = rid
        self._send(msg)
        while True:
            resp = self._read(timeout)
            if resp.get("id") != rid:
                continue
            if "error" in resp:
                return {"__error__": resp["error"]}
            return resp.get("result", {})

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def main():
    token = read_token()
    # v7.7.2: unique per-run names + coord jitter - earlier runs leave
    # MCP-TEST-* elements behind (no delete tool yet), and duplicate
    # names are skipped BY DESIGN which would fake a T1/T2 failure.
    stamp = time.strftime("%H%M%S")
    jitter = int(time.time() * 1000) % 100000
    print("[run stamp] {} (coord jitter {})".format(stamp, jitter))
    print("[connect] 127.0.0.1:9877 ...")
    c = Client(token)
    results = []

    def check(name, ok, detail=""):
        results.append((name, ok, detail))
        print("  {} {} {}".format("PASS" if ok else "FAIL", name, detail))

    # T5 first: wrong token must be rejected (fresh connection)
    print("[T5] 错误 token 负例")
    try:
        bad = Client("0" * 64)
        check("T5 错 token 被拒", False, "意外通过鉴权")
        bad.close()
    except RuntimeError:
        check("T5 错 token 被拒", True)

    # T1 levels
    print("[T1] revit_create_levels")
    l1 = "MCP-TEST-L1-" + stamp
    l2 = "MCP-TEST-L2-" + stamp
    r = c.call("create_levels", {"levels": [
        {"name": l1, "elevation_mm": 15000 + jitter},
        {"name": l1, "elevation_mm": 16000 + jitter},  # 重名 -> skip
        {"name": l2, "elevation_mm": 16500 + jitter},
    ]})
    ok = "created" in r and r.get("created") == 2 and len(
        r.get("skipped") or []) == 1
    check("T1 建 2 标高 + 重名跳 1", ok, json.dumps(r, ensure_ascii=False))

    # T2 grids
    print("[T2] revit_create_grids")
    ga = "MCP-TEST-A-" + stamp
    gb = "MCP-TEST-B-" + stamp
    yb = 18000 + jitter
    r = c.call("create_grids", {"grids": [
        {"axis": "y", "coord_mm": 30000 + jitter},       # 自动数字编号
        {"name": ga, "axis": "x", "coord_mm": jitter},
        {"name": gb, "p0": [0, yb],
         "p1": [60000, yb]},                             # 显式线段
        {"name": ga, "axis": "x",
         "coord_mm": 6000 + jitter},                     # 重名 -> skip
    ]})
    ok = "created" in r and r.get("created") == 3 and len(
        r.get("skipped") or []) == 1
    check("T2 建 3 轴网 + 重名跳 1", ok, json.dumps(r, ensure_ascii=False))

    # T3 query
    print("[T3] revit_query_elements")
    r = c.call("query_elements", {
        "category": u"标高", "name_contains": "MCP-TEST",
        "with_params": [u"标高"], "limit": 10})
    lv_ok = r.get("total_matched", 0) >= 2 and len(
        r.get("elements") or []) >= 2
    check("T3a 查测试标高 >=2", lv_ok,
          json.dumps(r, ensure_ascii=False)[:300])
    r = c.call("query_elements", {
        "category": u"轴网", "name_contains": "MCP-TEST"})
    gd_ok = r.get("total_matched", 0) >= 2
    check("T3b 查测试轴网 >=2", gd_ok,
          json.dumps(r, ensure_ascii=False)[:300])

    # T4 quantities
    print("[T4] revit_get_quantities")
    r = c.call("get_quantities", {"detail_category": u"标高"})
    ok = ("counts" in r and isinstance(r.get("counts"), list)
          and "cost_estimate" in r)
    check("T4 工程量 + 造价", ok,
          json.dumps(r, ensure_ascii=False)[:300])

    c.close()

    fails = [x for x in results if not x[1]]
    print("\n[live_test_v77] {} / {} PASS".format(
        len(results) - len(fails), len(results)))
    if fails:
        print("FAILURES:", [f[0] for f in fails])
        sys.exit(1)
    print("提醒: 测试标高/轴网不会自动删除，请在 Revit 里手动清理 "
          "（名称含 MCP-TEST）。")


if __name__ == "__main__":
    main()
