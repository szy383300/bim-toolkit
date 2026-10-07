# -*- coding: utf-8 -*-
"""
bimtoolkit_mcp.py —— BIMToolkit MCP 适配器（M2 闭环的 AI 侧入口）。

把 Revit 里 MCP Bridge 暴露的 21 个命令原样转成 MCP 工具，
不新增能力、不改动扩展目录——工具箱保持精炼。

链路：
  MCP 客户端(Claude/Cline/WorkBuddy...)
    --stdio JSON-RPC--> 本适配器
    --TCP 127.0.0.1:9877 换行JSON--> Revit 内 MCP Bridge(bridge_core.py)

协议要点（对齐 bridge_core v7.7.9）：
  1) 连上先发 {"type":"auth","token":"..."}，回 {"ok":true}
  2) 命令 {"type":"<cmd>","id":<n>,...参数} -> {"id":<n>,"result"|"error":...}
  3) 写命令(create_*/set_*/execute_code/dwg_to_model)经 ExternalEvent 异步回包，
     id 匹配等待即可
  4) 线路 UTF-8（v7.7.9 起不再是 ASCII 转义）

零第三方依赖，Python 3.8+ 标准库即可跑。

用法（客户端配置示例）：
  claude mcp add bimtoolkit -- python <仓库根>/mcp/bimtoolkit_mcp.py
环境变量：
  BIMTOOLKIT_PORT   默认 9877
  BIMTOOLKIT_TOKEN  默认自动读扩展目录 bridge.token
"""
import sys
import os
import json
import socket

PROTOCOL_VERSION = "2024-11-05"
HOST = "127.0.0.1"
PORT = int(os.environ.get("BIMTOOLKIT_PORT", "9877"))

# 与 bridge_core.py COMMAND_HANDLERS 一一对应（只读/只写分组只为写描述）
BRIDGE_TOOLS = [
    ("ping", "连通性测试，返回 Revit 文档名与 Bridge 版本", {
        "type": "object", "properties": {}, "required": []}),
    ("get_info", "项目概况：文档、层级、构件统计", {
        "type": "object", "properties": {}, "required": []}),
    ("get_categories", "列出项目中所有类别", {
        "type": "object", "properties": {}, "required": []}),
    ("get_levels", "列出所有标高", {
        "type": "object", "properties": {}, "required": []}),
    ("get_families", "列出已载入的族", {
        "type": "object", "properties": {}, "required": []}),
    ("get_elements", "按类别收集构件（返回 id/名称）", {
        "type": "object",
        "properties": {"category": {"type": "string", "description": "类别名，如 Walls / Doors"}},
        "required": []}),
    ("get_element", "按 ElementId 读单个构件详情（含参数）", {
        "type": "object",
        "properties": {"element_id": {"type": "integer", "description": "Revit ElementId"}},
        "required": ["element_id"]}),
    ("get_selected", "读取当前选中构件", {
        "type": "object", "properties": {}, "required": []}),
    ("get_changes", "读取增量变更流（DocumentChanged 缓存）", {
        "type": "object", "properties": {}, "required": []}),
    ("get_selection", "读取选择集", {
        "type": "object", "properties": {}, "required": []}),
    ("set_selection", "设置选择集（会高亮构件；桥实际参数 ids）", {
        "type": "object",
        "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
        "required": ["ids"]}),
    ("dwg_to_model", "DWG 翻模：按 model_plan.json 建模（桥实际参数 json_path）", {
        "type": "object",
        "properties": {"json_path": {"type": "string", "description": "model_plan.json 绝对路径"},
                       "safe_mode": {"type": "boolean", "description": "默认 true"}},
        "required": ["json_path"]}),
    ("create_walls", "按线段批量建墙（毫米坐标）", {
        "type": "object",
        "properties": {"walls": {"type": "array", "items": {"type": "object"},
                                 "description": "每项含 start/end/level/height/thickness"}},
        "required": ["walls"]}),
    ("create_grids", "建轴网（自动避让重名）", {
        "type": "object",
        "properties": {"grids": {"type": "array", "items": {"type": "object"}}},
        "required": ["grids"]}),
    ("create_levels", "建标高", {
        "type": "object",
        "properties": {"levels": {"type": "array", "items": {"type": "object"}}},
        "required": ["levels"]}),
    ("query_elements", "按条件查询构件（类别/标高/名称/ids 过滤）", {
        "type": "object",
        "properties": {"category": {"type": "string"},
                       "level": {"type": "string"},
                       "name_contains": {"type": "string"},
                       "ids": {"type": "array", "items": {"type": "integer"}},
                       "with_params": {"type": "array", "items": {"type": "string"}},
                       "limit": {"type": "integer"}},
        "required": []}),
    ("get_quantities", "工程量统计", {
        "type": "object", "properties": {}, "required": []}),
    ("set_parameters", "按类别批量设置一个参数（value 为 Revit 内部单位，"
                       "长度参数是英尺）", {
        "type": "object",
        "properties": {"category": {"type": "string"},
                       "parameter": {"type": "string"},
                       "value": {},
                       "level": {"type": "string", "description": "可选：仅限该标高"}},
        "required": ["category", "parameter", "value"]}),
    ("batch_count", "按类别批量计数", {
        "type": "object", "properties": {}, "required": []}),
    ("run_report", "跑项目报告", {
        "type": "object", "properties": {}, "required": []}),
    ("execute_code", "执行代码（高级；bridge 配置 allow_execute_code 才开）", {
        "type": "object",
        "properties": {"code": {"type": "string"},
                       "no_transaction": {"type": "boolean",
                                          "description": "true=不代管事务（载荷自管）"}},
        "required": ["code"]}),
    # ---- v7.12.2 补齐（2026-09-28 端到端评测：原适配器 21 项已滞后于
    #      bridge_core v7.11.0 的 35 项，与其自述「与 COMMAND_HANDLERS
    #      一一对应」不符）----
    ("debug_walls", "只读探针：墙收集三路统计", {
        "type": "object", "properties": {}, "required": []}),
    ("get_wall_geo", "按 ids 返回墙定位曲线端点与长度（mm）", {
        "type": "object",
        "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
        "required": ["ids"]}),
    ("create_floors", "建楼板：floors[{points(mm,逆时针闭合),level,structural?,type?}]", {
        "type": "object",
        "properties": {"floors": {"type": "array", "items": {"type": "object"}}},
        "required": ["floors"]}),
    ("create_roof_gable", "双坡屋面：x1,y1,x2,y2(mm),level,slope_deg,ridge_axis('x'|'y')", {
        "type": "object",
        "properties": {"x1": {"type": "number"}, "y1": {"type": "number"},
                       "x2": {"type": "number"}, "y2": {"type": "number"},
                       "level": {"type": "string"}, "slope_deg": {"type": "number"},
                       "ridge_axis": {"type": "string", "enum": ["x", "y"]}},
        "required": ["x1", "y1", "x2", "y2"]}),
    ("create_stairs", "楼梯：base_level,top_level,start[x,y](mm),dir[dx,dy],width_mm,run_mm", {
        "type": "object",
        "properties": {"base_level": {"type": "string"}, "top_level": {"type": "string"},
                       "start": {"type": "array", "items": {"type": "number"}},
                       "dir": {"type": "array", "items": {"type": "number"}},
                       "width_mm": {"type": "number"}, "run_mm": {"type": "number"},
                       "total_rise_mm": {"type": "number"}},
        "required": ["base_level", "top_level"]}),
    ("create_railing", "栏杆：path[[x,y]..](mm),level,height_mm?,stairs_or_ramp_id?", {
        "type": "object",
        "properties": {"path": {"type": "array", "items": {"type": "array"}},
                       "level": {"type": "string"},
                       "height_mm": {"type": "number"},
                       "stairs_or_ramp_id": {"type": "integer"}},
        "required": ["path", "level"]}),
    ("create_door_window", "门窗开洞：openings[{kind:'door'|'window',point,width_mm,height_mm?,sill_mm?,level?}]", {
        "type": "object",
        "properties": {"openings": {"type": "array", "items": {"type": "object"}}},
        "required": ["openings"]}),
    ("set_material", "给实例赋材质：name(材质名包含字) + ids 或 category", {
        "type": "object",
        "properties": {"name": {"type": "string"},
                       "ids": {"type": "array", "items": {"type": "integer"}},
                       "category": {"type": "string"}},
        "required": ["name"]}),
    ("delete_elements", "按 ElementId 删除构件：ids[]", {
        "type": "object",
        "properties": {"ids": {"type": "array", "items": {"type": "integer"}}},
        "required": ["ids"]}),
    ("save_document", "保存/另存：path（未保存文档必填）", {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": []}),
    ("apply_step", "导览步骤：step{levels[]|show_all},pad_mm?", {
        "type": "object",
        "properties": {"step": {"type": "object"}, "pad_mm": {"type": "number"}},
        "required": ["step"]}),
    ("reset_view", "导览复位：清除临时隐藏/隔离", {
        "type": "object", "properties": {}, "required": []}),
    ("export_view", "导出「导览3D」视图为图片：path", {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]}),
    ("list_steps", "读导览任务树 JSON：path -> {task, steps[]}", {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]}),
]


class BridgeClient(object):
    """到 Revit MCP Bridge 的单连接客户端（阻塞、按 id 等回包）。"""

    def __init__(self, host, port, token):
        self.host, self.port, self.token = host, port, token
        self.sock = None
        self.buf = b""
        self._id = 0

    # -- 连接 ------------------------------------------------------------
    def _discover_token(self):
        t = os.environ.get("BIMTOOLKIT_TOKEN")
        if t:
            return t.strip()
        # 部署目录 + 源码目录各找一次
        appdata = os.environ.get("APPDATA", "")
        cands = [
            os.path.join(appdata, "pyRevit", "Extensions", "BIMToolkit.extension",
                         "BIMToolkit.tab", "BIM 工具.panel", "MCP Bridge.pushbutton",
                         "bridge.token"),
            # 源码目录：本文件在 <仓库根>/mcp/ 下，回推两级即仓库根，不依赖某台机器的路径
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "pyrevit-bim-panel", "BIMToolkit.extension",
                         "BIMToolkit.tab", u"BIM 工具.panel",
                         "MCP Bridge.pushbutton", "bridge.token"),
        ]
        for p in cands:
            try:
                if os.path.exists(p):
                    return open(p, "rb").read().decode("utf-8").strip()
            except Exception:
                pass
        return None

    def connect(self):
        if self.sock is not None:
            return
        token = self.token or self._discover_token()
        if not token:
            raise RuntimeError(
                "找不到 bridge.token。请先在 Revit 里点 MCP Bridge 启动桥，"
                "或设置环境变量 BIMTOOLKIT_TOKEN。")
        s = socket.create_connection((self.host, self.port), timeout=5)
        self.sock = s
        self.buf = b""
        self._send_line({"type": "auth", "token": token})
        resp = self._read_line()
        if not resp.get("ok"):
            self.sock = None
            raise RuntimeError("Bridge 认证失败：%s" % json.dumps(resp, ensure_ascii=False))

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

    # -- 收发 ------------------------------------------------------------
    def _send_line(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n"
        self.sock.sendall(data)

    def _read_line(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("Bridge 连接已断开（Revit 里桥可能停了）")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line.decode("utf-8"))

    # -- 对外 ------------------------------------------------------------
    def call(self, cmd_type, params):
        self.connect()
        self._id += 1
        cid = self._id
        cmd = {"type": cmd_type, "id": cid}
        if isinstance(params, dict):
            for k, v in params.items():
                if k not in ("type", "id"):
                    cmd[k] = v
        self._send_line(cmd)
        # 写命令回包由 ExternalEvent 线程发出，按 id 等待；顺带丢弃陈旧推送
        while True:
            resp = self._read_line()
            if resp.get("id") == cid:
                if "error" in resp and "result" not in resp:
                    raise RuntimeError("Bridge 错误: %s" % json.dumps(
                        resp.get("error"), ensure_ascii=False))
                return resp.get("result", resp)


# ---------------------------------------------------------------------------
# MCP stdio 骨架（JSON-RPC 2.0，换行分帧）
# ---------------------------------------------------------------------------
def _reply(msg_id, result):
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": msg_id, "result": result},
        ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply_error(msg_id, code, message):
    sys.stdout.write(json.dumps(
        {"jsonrpc": "2.0", "id": msg_id,
         "error": {"code": code, "message": message}},
        ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    client = BridgeClient(HOST, PORT, None)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        method = msg.get("method", "")
        msg_id = msg.get("id")
        if method == "initialize":
            _reply(msg_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "bimtoolkit-mcp", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            pass  # 通知，无需回包
        elif method == "tools/list":
            _reply(msg_id, {"tools": [
                {"name": n, "description": d, "inputSchema": s}
                for (n, d, s) in BRIDGE_TOOLS]})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            args = params.get("arguments") or {}
            schema = next((s for (n, _, s) in BRIDGE_TOOLS if n == name), None)
            if schema is None:
                _reply(msg_id, {"content": [{"type": "text", "text": "未知工具: " + name}],
                                "isError": True})
                continue
            try:
                result = client.call(name, args)
                text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
                if len(text) > 60000:
                    text = text[:60000] + "\n...(截断)"
                _reply(msg_id, {"content": [{"type": "text", "text": text}]})
            except Exception as e:
                hint = ""
                if isinstance(e, (socket.error, RuntimeError)) and \
                        ("10061" in str(e) or "拒绝" in str(e) or "refused" in str(e).lower()):
                    hint = (u"\n[提示] 连不上 Revit 桥：请先打开 Revit → "
                            u"BIM 工具 → MCP Bridge 启动桥，再重试。")
                _reply(msg_id, {"content": [{"type": "text",
                                             "text": str(e) + hint}],
                                "isError": True})
        elif method == "ping":
            _reply(msg_id, {})
        elif msg_id is not None:
            _reply_error(msg_id, -32601, "method not found: " + method)


if __name__ == "__main__":
    main()
