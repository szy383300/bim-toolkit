# -*- coding: utf-8 -*-
u"""genbuild.errors - 特化异常与语义化退出码。

退出码约定 (供 CI / 外层脚本判断):
  0  成功 (dry 通过 / 构建完成且验收达标)
  1  配方错误: YAML 载入失败或静态校验发现致命问题
  2  桥错误: 连接失败 / 认证失败 / 响应协议异常
  3  构建错误: 执行期失败 (守卫拒绝 / SaveAs 失败 / 未预期异常)
  4  验收未达标: 构建完成但 expect 数量不符或门窗审计有异常
"""


class GenBuildError(Exception):
    u"""genbuild 异常基类。"""


class SpecError(GenBuildError):
    u"""YAML 载入失败或结构不可用 (文件不存在 / 解析错误 / 顶层非映射)。"""


class BridgeError(GenBuildError):
    u"""TCP 桥连接 / 认证 / 响应协议错误。"""


class BuildError(GenBuildError):
    u"""构建执行期致命失败 (文档守卫拒绝 / SaveAs 失败等)。"""


EXIT_OK = 0
EXIT_SPEC_ERROR = 1
EXIT_BRIDGE_ERROR = 2
EXIT_BUILD_ERROR = 3
EXIT_ACCEPT_FAIL = 4
