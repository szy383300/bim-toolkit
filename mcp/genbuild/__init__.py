# -*- coding: utf-8 -*-
u"""genbuild - 配置驱动 Revit 建模引擎 (宿主侧, CPython 3)。

模块划分:
  config    常量默认值 + BIMTOOLKIT_* 环境变量覆盖
  errors    特化异常 + 语义化退出码
  logsetup  logging 配置 (文件 DEBUG 带时间戳 / 控制台 INFO)
  spec      YAML 载入 + 全量静态校验 + 纯几何 (楼梯布局/墙段展开)
  bridge    TCP 桥客户端 (BridgeClient) + 消息编码/响应解包
  engine    分阶段构建流水线 (RunContext + stage 函数)
  cli       argparse 命令行入口
  macros    Revit 2019 execute_code 配方模板 (IronPython 2.7 沙箱)

硬约束: 桥冻结 7.10.4 不改; macros.py 内模板字符串必须保持
IronPython 2.7 兼容语法; 配方协议 (模板 + __G__ 注入) 不变。
"""
__version__ = u"2.0.0"
