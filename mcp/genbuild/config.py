# -*- coding: utf-8 -*-
u"""genbuild.config - 集中配置: 常量默认值 + 环境变量覆盖。

环境变量 (全部可选, 换端口/换机无需改源码):
  BIMTOOLKIT_HOST        桥地址, 默认 127.0.0.1
  BIMTOOLKIT_PORT        桥端口, 默认 9877
  BIMTOOLKIT_DEP         bridge.token 搜索目录 1 (Revit pushbutton 部署目录)
  BIMTOOLKIT_EXT         bridge.token 搜索目录 2 (扩展源码目录)
  BIMTOOLKIT_CALL_TIMEOUT 单命令默认超时秒, 默认 240
"""
import os

# 默认值一律运行时推导，不写死某台机器的绝对路径：
#   _REPO_ROOT 由本文件位置回推三级（mcp/genbuild/config.py -> 仓库根）
#   _APPDATA 取环境变量，取不到再按用户目录拼（非 Windows 下也能 import）
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_APPDATA = os.environ.get(u"APPDATA") or os.path.join(os.path.expanduser(u"~"), u"AppData", u"Roaming")

# ------------------------------------------------------------- 桥连接
HOST = os.environ.get(u"BIMTOOLKIT_HOST", u"127.0.0.1")
PORT = int(os.environ.get(u"BIMTOOLKIT_PORT", u"9877"))

DEP = os.environ.get(
    u"BIMTOOLKIT_DEP",
    os.path.join(_APPDATA, u"pyRevit", u"Extensions", u"BIMToolkit.extension",
                 u"BIMToolkit.tab", u"BIM 工具.panel", u"MCP Bridge.pushbutton"))
EXT = os.environ.get(
    u"BIMTOOLKIT_EXT",
    os.path.join(_REPO_ROOT, u"pyrevit-bim-panel", u"BIMToolkit.extension"))


def _int_env(name, default):
    try:
        return int(os.environ.get(name, u"") or default)
    except ValueError:
        return default


CALL_TIMEOUT_DEFAULT = _int_env(u"BIMTOOLKIT_CALL_TIMEOUT", 240)

# 引擎各类命令的超时 (秒): 与旧版逐一对齐
PING_TIMEOUT = 15
RUN_CODE_TIMEOUT = 280
DWG_TIMEOUT = 560
SAVE_TIMEOUT = 120

# ------------------------------------------------------------- 建模默认
WALL_H_DEFAULT = 3000          # 墙缺省高度 mm

# 清场类别: 每次构建前删除的活动类别 (验证宏 VERIFY 的类别清单与之对应)
WIPE_CATS = [u"OST_Walls", u"OST_Doors", u"OST_Windows", u"OST_Floors",
             u"OST_Roofs", u"OST_Stairs", u"OST_StairsRuns",
             u"OST_StairsLandings", u"OST_StairsRailing", u"OST_Lines",
             u"OST_Grids", u"OST_GenericModel", u"OST_StructuralColumns",
             u"OST_Columns"]

# 开洞校验/找型阈值 (mm)
OPENING_MAX_WALL_DIST = 300.0    # 开洞中心到最近墙的最大距离
OPENING_JAMB_MARGIN = 50.0       # 端距小于 w/2+该值 时警告"零窗垛贴边"
TYPE_MATCH_TOL = 1.5             # 族型宽/高精确匹配容差 (±mm)

# ------------------------------------------------------- 楼梯布局常量
# stairs_u 宏的局部布局 (实证: 别墅楼梯块, dir=+y); 其余方向经 STAIR_ROT
# 旋转。单位 mm。
STAIR_RUN1 = (600, 0, 600, 1800)
STAIR_RUN2 = (1800, 3000, 1800, 1200)
STAIR_LANDING = [(0, 1800), (1200, 1800), (1200, 3000), (0, 3000)]
STAIR_LANDING_OFFSET = 1500
STAIR_ROT = {(0, 1): lambda x, y: (x, y),
             (0, -1): lambda x, y: (-x, -y),
             (1, 0): lambda x, y: (y, -x),
             (-1, 0): lambda x, y: (-y, x)}
