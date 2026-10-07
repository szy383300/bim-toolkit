# -*- coding: utf-8 -*-
u"""genbuild.bridge - TCP 桥客户端 (对应 Revit 内 MCP Bridge 7.10.4)。

协议: 每命令一条 TCP 连接; 先发 {"type":"auth","token":...} 一行 JSON,
认证通过后发命令 JSON 一行, 收一行 JSON 响应。所有写入经 esc() 转为
纯 ASCII (非 ASCII -> \\uXXXX, 沙箱侧 json.loads 还原)。

BridgeClient 每命令建连/关连 (try/finally 保证不泄漏 socket);
连接失败/认证失败/协议异常统一包装为 BridgeError, 消息含恢复指引。
模块级 call() 保留旧签名, 历史脚本 `from bridge_client import call`
无需改动。
"""
import io
import json
import os
import socket

from genbuild import config
from genbuild.errors import BridgeError


def esc(s):
    u"""消息编码: 非 ASCII 字符转 \\uXXXX, 保证模板注入后纯 ASCII。"""
    return u"".join(c if ord(c) < 128 else u"\\u%04x" % ord(c)
                    for c in s)


def unwrap(res):
    u"""桥响应双重嵌套解包: {"result": {...}, "status": "ok"} 逐层还原。

    防御: result 非 dict (None/标量) 时立即停止, 避免死循环
    (旧实现的 `res.get("result") or res` 在 falsy result 下原地打转)。
    """
    while (isinstance(res, dict) and u"result" in res
           and u"status" in res and set(res.keys()) <= {u"result", u"status"}):
        inner = res.get(u"result")
        if not isinstance(inner, dict):
            break
        res = inner
    return res


class BridgeClient(object):
    u"""桥 TCP 客户端。host/port 可由 BIMTOOLKIT_HOST/PORT 覆盖。"""

    def __init__(self, host=None, port=None):
        self.host = host or config.HOST
        self.port = port or config.PORT
        self._token = None

    # ---------------------------------------------------------- token
    def _load_token(self):
        u"""读 bridge.token (部署目录优先, 扩展目录兜底), 结果缓存。"""
        if self._token:
            return self._token
        for d in (config.DEP, config.EXT):
            p = os.path.join(d, u"bridge.token")
            if os.path.isfile(p):
                self._token = io.open(p, encoding=u"utf-8").read().strip()
                return self._token
        raise BridgeError(
            u"找不到 bridge.token (搜索: %s | %s) — 确认 BIMToolkit "
            u"扩展已同步到 pyRevit Extensions 目录" % (config.DEP, config.EXT))

    # ----------------------------------------------------------- call
    def call(self, cmd, timeout=None):
        u"""发一条命令并返回响应 dict。timeout=None 用默认配置。"""
        if timeout is None:
            timeout = config.CALL_TIMEOUT_DEFAULT
        token = self._load_token()
        try:
            s = socket.create_connection((self.host, self.port),
                                         timeout=timeout)
        except OSError as ex:
            raise BridgeError(
                u"无法连接桥 %s:%s (%s) — 确认 Revit 已打开且 "
                u"MCP Bridge 面板按钮已启动" % (self.host, self.port, ex))
        try:
            f = s.makefile(u"rwb")
            try:
                f.write((json.dumps({u"type": u"auth",
                                     u"token": token}) + u"\n")
                        .encode(u"utf-8"))
                f.flush()
                line = f.readline()
                if not line:
                    raise BridgeError(u"认证无响应 (连接被关闭)")
                auth = json.loads(line.decode(u"utf-8"))
                if not auth.get(u"ok"):
                    raise BridgeError(u"认证失败: %s" % auth)
                f.write((json.dumps(cmd) + u"\n").encode(u"utf-8"))
                f.flush()
                line = f.readline()
                if not line:
                    raise BridgeError(
                        u"命令 %s 无响应 (超时 %ss 或桥崩溃)"
                        % (cmd.get(u"type"), timeout))
                resp = json.loads(line.decode(u"utf-8"))
            except ValueError as ex:
                raise BridgeError(u"桥响应不是合法 JSON: %s" % ex)
        finally:
            try:
                s.close()
            except OSError:
                pass
        return resp


# 模块级默认客户端 (惰性单例): 兼容旧 call() 入口
_DEFAULT = None


def default_client():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = BridgeClient()
    return _DEFAULT


def call(cmd, timeout=240):
    u"""旧签名兼容入口: call(cmd, timeout=240) -> 响应 dict。"""
    return default_client().call(cmd, timeout)
