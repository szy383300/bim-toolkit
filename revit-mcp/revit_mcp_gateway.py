#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
revit-mcp-gateway: 标准 MCP 服务器(stdio, JSON-RPC 2.0) -> BIMToolkit MCP Bridge v7 (TCP 127.0.0.1:9877)。

让 Claude Desktop / Cursor / 任意 MCP 客户端直接操作 Revit 2019。

用法（Claude Desktop 配置示例, claude_desktop_config.json）:
    {
      "mcpServers": {
        "revit": {
          "command": "python",
          "args": ["E:/bim-toolkit/revit-mcp/revit_mcp_gateway.py"]
        }
      }
    }

前提:
  1. Revit 2019 已打开, pyRevit 已加载 BIMToolkit
  2. 点击「BIM 工具 -> MCP Bridge」按钮（启动 TCP 桥并生成 bridge.token）

可选参数:
  --token-file <path>   token 文件位置（默认自动定位到扩展按钮目录）
  --host 127.0.0.1      桥地址
  --port 9877           桥端口
  --timeout 60          单请求超时（秒）

零第三方依赖（纯标准库）。
"""

import json
import os
import socket
import sys
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9877
DEFAULT_TIMEOUT = 60.0

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "revit-mcp", "version": "1.2.0"}


# ---------------------------------------------------------------------------
# token / config 定位
# ---------------------------------------------------------------------------

def bridge_dir():
    """MCP Bridge 按钮所在目录（token/config 同目录）。"""
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(
        appdata, "pyRevit", "Extensions", "BIMToolkit.extension",
        "BIMToolkit.tab", "BIM 工具.panel", "MCP Bridge.pushbutton")


def token_path_from_args():
    if "--token-file" in sys.argv:
        return sys.argv[sys.argv.index("--token-file") + 1]
    return os.path.join(bridge_dir(), "bridge.token")


def bridge_config_path():
    return os.path.join(bridge_dir(), "mcp_bridge_config.json")


def read_token():
    path = token_path_from_args()
    with open(path, "r", encoding="utf-8") as f:
        t = f.read().strip()
    if not t:
        raise RuntimeError("token file is empty: {}".format(path))
    return t


def allow_execute_code():
    try:
        with open(bridge_config_path(), "r", encoding="utf-8-sig") as f:
            return bool(json.load(f).get("allow_execute_code", False))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Bridge TCP 客户端（换行分帧 + auth + id 匹配）
# ---------------------------------------------------------------------------

class BridgeError(Exception):
    pass


class Bridge:
    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self.buf = b""
        self.nid = 0

    def _read_line(self, deadline):
        while b"\n" not in self.buf:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise BridgeError("bridge response timeout ({}s)".format(
                    int(self.timeout)))
            self.sock.settimeout(remaining)
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                raise BridgeError("bridge response timeout ({}s)".format(
                    int(self.timeout)))
            if not data:
                raise BridgeError("bridge closed the connection")
            self.buf += data
        line, self.buf = self.buf.split(b"\n", 1)
        return line

    def connect(self):
        token = read_token()
        try:
            s = socket.create_connection((self.host, self.port), timeout=5)
        except OSError as e:
            raise BridgeError(
                "cannot connect to MCP Bridge at {}:{} ({}). "
                "请在 Revit 中点击「BIM 工具 -> MCP Bridge」按钮启动桥。".format(
                    self.host, self.port, e))
        self.sock = s
        self.buf = b""
        s.sendall((json.dumps({"type": "auth", "token": token}) + "\n")
                  .encode("utf-8"))
        deadline = time.time() + 5
        resp = json.loads(self._read_line(deadline).decode("utf-8"))
        if not resp.get("ok"):
            raise BridgeError("bridge auth failed: {}".format(resp.get("error")))
        return resp

    def call(self, cmd_type, args=None, timeout=None):
        """发送一条命令，等待同 id 应答。返回 result dict 或抛 BridgeError。"""
        timeout = timeout or self.timeout
        if self.sock is None:
            self.connect()
        self.nid += 1
        rid = self.nid
        msg = dict(args or {})
        msg["type"] = cmd_type
        msg["id"] = rid
        try:
            self.sock.sendall(
                (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
        except OSError:
            # 连接可能被桥侧重启，重连一次
            self.close()
            self.connect()
            self.sock.sendall(
                (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))

        deadline = time.time() + timeout
        while True:
            line = self._read_line(deadline)
            try:
                resp = json.loads(line.decode("utf-8"))
            except ValueError:
                continue
            if resp.get("id") != rid:
                continue  # 他人/陈旧应答，忽略
            if "error" in resp:
                raise BridgeError(str(resp["error"]))
            return resp.get("result", {})

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        self.buf = b""


# ---------------------------------------------------------------------------
# MCP 工具定义
# ---------------------------------------------------------------------------

def build_tools():
    tools = [
        {
            "name": "revit_get_info",
            "description": "获取当前 Revit 文档摘要：标题、路径、构件总数、"
                           "类别分布 Top30、标高列表、当前选择数。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "revit_list_levels",
            "description": "列出全部标高（id/名称/标高，ft 与 mm）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "revit_list_families",
            "description": "列出全部族及其类型（族名 -> 类型列表）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "revit_list_elements",
            "description": "按类别名列出构件（id/名称/所在标高）。类别名用"
                           "revit_get_info返回的中文名，如「墙」「门」。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string",
                                 "description": "类别名（中文，如 墙/门/窗）"},
                    "limit": {"type": "integer", "default": 100},
                },
                "required": ["category"],
            },
        },
        {
            "name": "revit_get_element",
            "description": "按 ElementId 读取单个构件的全部参数。",
            "inputSchema": {
                "type": "object",
                "properties": {"element_id": {"type": "integer"}},
                "required": ["element_id"],
            },
        },
        {
            "name": "revit_create_walls",
            "description": "批量创建墙（原子操作，单事务，失败回滚）。坐标单位"
                           "毫米；level 可选（标高名，缺省最低标高）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "walls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "start": {"type": "array",
                                          "items": {"type": "number"},
                                          "description": "[x, y, (z)] mm"},
                                "end": {"type": "array",
                                        "items": {"type": "number"},
                                        "description": "[x, y, (z)] mm"},
                                "height_mm": {"type": "number", "default": 3000},
                                "thickness_mm": {"type": "number", "default": 200},
                                "level": {"type": "string"},
                            },
                            "required": ["start", "end"],
                        },
                    },
                },
                "required": ["walls"],
            },
        },
        {
            "name": "revit_create_grids",
            "description": "批量创建轴网（原子操作，单事务，失败回滚，重名"
                           "自动跳过）。每个轴网两种给法之一：显式线段 "
                           "{name?, p0:[x,y]mm, p1:[x,y]mm}，或轴线 "
                           "{name?, axis:\"x\"|\"y\", coord_mm}。axis=\"y\" "
                           "表示平行 Y 轴的竖向轴网（自动编号 1,2,3…），"
                           "axis=\"x\" 表示平行 X 轴的水平轴网（自动编号 "
                           "A,B,C…）。不传 name 则自动分配。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "grids": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string",
                                         "description": "轴号，缺省自动分配"},
                                "axis": {"type": "string",
                                         "enum": ["x", "y"],
                                         "description": "轴线方向（给 "
                                                        "coord_mm 时必填）"},
                                "coord_mm": {"type": "number",
                                             "description": "轴线在垂直方向"
                                                            "的坐标 mm"},
                                "p0": {"type": "array",
                                       "items": {"type": "number"},
                                       "description": "线段起点 [x,y] mm"},
                                "p1": {"type": "array",
                                       "items": {"type": "number"},
                                       "description": "线段终点 [x,y] mm"},
                            },
                        },
                    },
                },
                "required": ["grids"],
            },
        },
        {
            "name": "revit_create_levels",
            "description": "批量创建标高（原子操作，单事务，失败回滚，重名"
                           "自动跳过）。elevation_mm 从 0.000 起算。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "levels": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string",
                                         "description": "标高名，缺省 Revit "
                                                        "默认名"},
                                "elevation_mm": {"type": "number",
                                                 "description": "标高 mm"},
                            },
                            "required": ["elevation_mm"],
                        },
                    },
                },
                "required": ["levels"],
            },
        },
        {
            "name": "revit_query_elements",
            "description": "按条件过滤构件（只读）。可组合：category（中文"
                           "类别名）、level（标高名）、name_contains（名称"
                           "子串）、ids（ElementId 列表）；with_params 指定"
                           "要带出的参数名列表（如 [\"体积\",\"长度\"]，"
                           "长度/体积为 Revit 内部单位：英尺/立方英尺）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string",
                                 "description": "类别名（中文，如 墙/门/窗）"},
                    "level": {"type": "string",
                              "description": "标高名过滤"},
                    "name_contains": {"type": "string",
                                      "description": "名称子串过滤"},
                    "ids": {"type": "array",
                            "items": {"type": "integer"},
                            "description": "直接按 ElementId 集合查询"},
                    "with_params": {"type": "array",
                                    "items": {"type": "string"},
                                    "description": "要带出的参数名列表"},
                    "limit": {"type": "integer", "default": 200,
                              "maximum": 1000},
                },
            },
        },
        {
            "name": "revit_get_quantities",
            "description": "分类别工程量统计（数量/面积 m2/体积 m3）+ 简易"
                           "造价估算（混凝土/模板/钢筋估）。可选 "
                           "detail_category 返回该类别逐构件明细。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "detail_category": {
                        "type": "string",
                        "description": "可选：类别名（中文），返回逐构件"
                                       "体积/面积明细",
                    },
                },
            },
        },
        {
            "name": "revit_set_parameters",
            "description": "按类别批量设置一个参数（原子操作）。注意 value 为 "
                           "Revit 内部单位（长度参数是英尺，mm/304.8）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "类别名（中文）"},
                    "parameter": {"type": "string", "description": "参数名"},
                    "value": {"description": "新值（数值或字符串）"},
                    "level": {"type": "string",
                              "description": "可选：仅限该标高上的构件"},
                },
                "required": ["category", "parameter", "value"],
            },
        },
        {
            "name": "revit_batch_count",
            "description": "全模型分类别工程量统计（数量/面积/体积）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "revit_run_report",
            "description": "规范审查（防火/抗震/结构/节能/面积五类）+ 评分 + "
                           "整改建议。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "revit_get_changes",
            "description": "增量获取模型变更（DocumentChanged 推送的序列流）。"
                           "传 since=<上次 current_seq> 只取新增；返回 "
                           "changes(added/deleted/modified 构件 id) 与 "
                           "current_seq（下次用它当 since）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "since": {"type": "integer", "default": 0,
                              "description": "序号水位线，只返回比它新的变更"},
                },
            },
        },
        {
            "name": "revit_get_selection",
            "description": "读取当前选择集（构件 id 列表 + 类别/名称明细）。",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "revit_set_selection",
            "description": "设置当前选择集（高亮指定构件，便于用户核对 AI "
                           "刚建/改的内容）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "integer"},
                            "description": "ElementId 列表"},
                },
                "required": ["ids"],
            },
        },
        {
            "name": "revit_dwg_to_model",
            "description": "DWG 翻模：从 model_plan.json（dwg_to_json.py 的"
                           "产物）批量建模。safe_mode 默认 true（关墙接合/"
                           "门窗开洞，只出骨架，最稳）。返回计划数/成败计数/"
                           "失败样例消息（fail_samples）。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "json_path": {"type": "string",
                                  "description": "model_plan.json 的绝对路径"},
                    "rules_path": {"type": "string",
                                   "description": "可选：mapping_rules.csv 路径"},
                    "safe_mode": {"type": "boolean", "default": True},
                },
                "required": ["json_path"],
            },
        },
    ]
    if allow_execute_code():
        tools.append({
            "name": "revit_execute_code",
            "description": "在 Revit 内执行任意 IronPython 2.7 代码（危险，"
                           "仅 mcp_bridge_config.json 显式开启时可用）。",
            "inputSchema": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        })
    return tools


# MCP 工具名 -> 桥命令名
TOOL_TO_CMD = {
    "revit_get_info": ("get_info", {}),
    "revit_list_levels": ("get_levels", {}),
    "revit_list_families": ("get_families", {}),
    "revit_list_elements": ("get_elements", None),
    "revit_get_element": ("get_element", None),
    "revit_create_walls": ("create_walls", None),
    "revit_create_grids": ("create_grids", None),
    "revit_create_levels": ("create_levels", None),
    "revit_query_elements": ("query_elements", None),
    "revit_get_quantities": ("get_quantities", None),
    "revit_set_parameters": ("set_parameters", None),
    "revit_batch_count": ("batch_count", {}),
    "revit_run_report": ("run_report", {}),
    "revit_get_changes": ("get_changes", None),
    "revit_get_selection": ("get_selection", {}),
    "revit_set_selection": ("set_selection", None),
    "revit_dwg_to_model": ("dwg_to_model", None),
    "revit_execute_code": ("execute_code", None),
}

# 个别工具需要更长桥应答超时（翻模大批量建模）
TOOL_TIMEOUTS = {
    "revit_dwg_to_model": 600.0,
}

RESOURCES = [
    {
        "uri": "revit://model/summary",
        "name": "Revit 模型摘要",
        "description": "当前文档标题、构件数、类别分布、标高列表",
        "mimeType": "application/json",
    },
]


# ---------------------------------------------------------------------------
# JSON-RPC 处理
# ---------------------------------------------------------------------------

def rpc_send(obj):
    sys.stdout.buffer.write(
        (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def text_content(payload):
    return [{"type": "text",
             "text": json.dumps(payload, ensure_ascii=False, indent=1)}]


def handle_request(req, bridge):
    method = req.get("method", "")
    params = req.get("params") or {}

    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": SERVER_INFO}

    if method == "ping":
        return {}

    if method == "tools/list":
        return {"tools": build_tools()}

    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        if name not in TOOL_TO_CMD:
            raise BridgeError("unknown tool: {}".format(name))
        cmd, fixed = TOOL_TO_CMD[name]
        merged = dict(fixed or {})
        merged.update(args)
        result = bridge.call(cmd, merged,
                             timeout=TOOL_TIMEOUTS.get(name))
        return {"content": text_content(result), "isError": False}

    if method == "resources/list":
        return {"resources": RESOURCES}

    if method == "resources/read":
        uri = params.get("uri", "")
        if uri == "revit://model/summary":
            result = bridge.call("get_info", {})
            return {"contents": [{
                "uri": uri,
                "mimeType": "application/json",
                "text": json.dumps(result, ensure_ascii=False, indent=1),
            }]}
        raise BridgeError("unknown resource: {}".format(uri))

    raise BridgeError("method not supported: {}".format(method))


def main():
    host = DEFAULT_HOST
    port = DEFAULT_PORT
    timeout = DEFAULT_TIMEOUT
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--timeout" in sys.argv:
        timeout = float(sys.argv[sys.argv.index("--timeout") + 1])

    bridge = Bridge(host, port, timeout)

    # stdio 按行读 JSON-RPC；notifications 无应答
    for raw in sys.stdin.buffer:
        line = raw.strip()
        if not line:
            continue
        try:
            req = json.loads(line.decode("utf-8"))
        except ValueError:
            continue
        method = req.get("method", "")
        rid = req.get("id")
        if method.startswith("notifications/"):
            continue
        try:
            result = handle_request(req, bridge)
        except BridgeError as e:
            if rid is not None:
                rpc_send({"jsonrpc": "2.0", "id": rid,
                          "error": {"code": -32000, "message": str(e)}})
            continue
        except Exception as e:  # noqa: BLE001
            if rid is not None:
                rpc_send({"jsonrpc": "2.0", "id": rid,
                          "error": {"code": -32603,
                                    "message": "internal error: {}".format(e)}})
            continue
        if rid is not None:
            rpc_send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    main()
