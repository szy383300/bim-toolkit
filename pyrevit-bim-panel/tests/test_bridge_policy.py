# -*- coding: utf-8 -*-
"""MCP Bridge 策略闸门的行为测试。

做法：在 IronPython 测试台的桩环境里 **exec 真实的 bridge_core.py**，
然后直接断言 tier_of / policy_allows 的行为矩阵。

不是"看源码对不对"，是把真代码跑起来按真行为断言。
必须作为**子进程**运行：测试台会 monkeypatch `open()`，在主进程里 import 会污染其它测试。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PANEL = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import ironpython_harness  # noqa: E402  安装 clr/System/Autodesk 桩

BRIDGE = os.path.join(
    PANEL, "BIMToolkit.extension", "BIMToolkit.tab", "BIM 工具.panel",
    "MCP Bridge.pushbutton", "bridge_core.py")

with open(BRIDGE, "rb") as f:
    src = f.read().decode("utf-8-sig")

g = {"__name__": "bridge_core_policy_test", "__file__": BRIDGE}
try:
    exec(compile(src, BRIDGE, "exec"), g)
except Exception as e:
    print("FAIL exec bridge_core.py: [%s] %s" % (type(e).__name__, e))
    sys.exit(1)

tier_of = g["tier_of"]
policy_allows = g["policy_allows"]
unclassified = g["unclassified_commands"]

failures = []


def expect(cond, msg):
    if cond:
        print("PASS  %s" % msg)
    else:
        print("FAIL  %s" % msg)
        failures.append(msg)


# ---- 档位归属 ----
expect(tier_of("ping") == "read", "ping 属 read")
expect(tier_of("create_walls") == "write", "create_walls 属 write")
expect(tier_of("execute_code") == "dangerous", "execute_code 属 dangerous")
expect(tier_of("delete_elements") == "dangerous", "delete_elements 属 dangerous")
expect(tier_of("no_such_cmd") == "unknown", "未注册命令 -> unknown")

# ---- 行为矩阵（这是本节的核心断言）----
expect(policy_allows("ping", "safe")[0] is True, "safe 允许只读")
expect(policy_allows("create_walls", "safe")[0] is False, "safe 拒绝写")
expect(policy_allows("execute_code", "safe")[0] is False, "safe 拒绝 execute_code")

expect(policy_allows("ping", "build")[0] is True, "build 允许只读")
expect(policy_allows("create_walls", "build")[0] is True, "build 允许写")
expect(policy_allows("execute_code", "build")[0] is False, "build 拒绝 execute_code")
expect(policy_allows("delete_elements", "build")[0] is False,
       "build 拒绝 delete_elements")

expect(policy_allows("execute_code", "dev")[0] is True,
       "dev 允许 execute_code（genbuild 依赖它）")
expect(policy_allows("delete_elements", "dev")[0] is True, "dev 允许 delete_elements")

# ---- fail-closed：未分档的命令在任何档下都被拒 ----
for pol in ("safe", "build", "dev"):
    expect(policy_allows("no_such_cmd", pol)[0] is False,
           "%s 档下未分档命令也被拒（fail-closed）" % pol)

# ---- 启动时的自检函数应为空 ----
expect(unclassified() == [], "没有未分档的命令：%s" % unclassified())

# ---------------------------------------------------------------------------
# genbuild 端：策略档不足时必须 **fail-fast**（而不是建到一半才失败）
# 这条很重要 —— genbuild 的配方是 17 处 execute_code，被拒绝时不希望
# 已经清完场、建了一半才发现。
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(PANEL), "mcp"))
try:
    from genbuild import engine as gb_engine
except Exception as e:
    print("SKIP  genbuild 侧检查（导入失败）：[%s] %s" % (type(e).__name__, e))
    gb_engine = None

if gb_engine is not None:
    class _FakeLog(object):
        def info(self, *a):
            pass

        def error(self, *a):
            pass

    class _FakeCtx(object):
        def __init__(self, policy):
            self._policy = policy
            self.log = _FakeLog()

        def call_cmd(self, cmd, params, label, timeout=None):
            return {"document": u"test.rvt", "version": u"7.13.0",
                    "policy": self._policy}

    for pol, should_raise in ((u"dev", False), (u"build", True), (u"safe", True)):
        try:
            gb_engine._stage_ping(_FakeCtx(pol), {})
            raised = False
        except gb_engine.BuildError:
            raised = True
        expect(raised == should_raise,
               "genbuild: policy=%s -> %s"
               % (pol, "开建前拒绝" if should_raise else "放行"))

print("")
if failures:
    print("RESULT: FAIL —— %d 项" % len(failures))
    sys.exit(1)
print("RESULT: OK —— 策略闸门行为矩阵全部符合预期")
