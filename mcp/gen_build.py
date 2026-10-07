# -*- coding: utf-8 -*-
u"""gen_build.py - 兼容入口 (实现已拆分至 genbuild 包)。

历史探针/验证脚本的导入面保持不变:
  from gen_build import esc, unwrap, ID
  from gen_build import macros
命令行用法与退出码见 genbuild.cli。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from genbuild.bridge import esc, unwrap  # noqa: E402,F401
from genbuild.engine import ID, execute  # noqa: E402,F401
from genbuild import macros  # noqa: E402,F401
from genbuild.cli import main  # noqa: E402,F401

if __name__ == u"__main__":
    sys.exit(main())
