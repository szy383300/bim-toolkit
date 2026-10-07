# -*- coding: utf-8 -*-
"""AI 智建 > 一键建模 — 读取 building_params.json 自动创建完整建筑模型。

工作流：
  1. 参数来源：lib/building_params.json（当前为示例/手写参数；
     规划中的三视图 AI 识别器尚未实现，参数文件需自行准备或编写）
  2. 用户将 JSON 放入扩展 lib/ 目录或当前项目同目录
  3. 点击此按钮 → 自动创建标高、墙体、楼板、屋顶、窗户、门、阳台

技术要点（Revit 2019 IronPython 2.7 兼容）：
  - Wall.Create 使用 8 参重载（唯一无歧义）
  - 墙类型按 WallKind.Basic 过滤（避开幕墙/叠层墙）
  - 坡屋顶使用 NewFootPrintRoof + set_DefinesSlope
  - 2019 无墙顶附着屋顶 API，墙高按层高+100mm 预留
  - 单位：mm / 304.8 = ft
"""
import os
import sys
import json

uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

HERE = os.path.dirname(__file__)
EXT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB = os.path.join(EXT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

from bimlib import to_text
from model_builder import build_model

try:
    from pyrevit import script
    output = script.get_output()
except Exception:
    output = None


def _print(msg):
    if output:
        output.print_md(msg)
    else:
        print(msg)


def find_params_json():
    candidates = [
        os.path.join(LIB, "building_params.json"),
        os.path.join(os.path.dirname(HERE), "building_params.json"),
    ]
    try:
        doc_path = doc.PathName
        if doc_path:
            doc_dir = os.path.dirname(doc_path)
            candidates.insert(0, os.path.join(doc_dir, "building_params.json"))
    except Exception:
        pass
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def load_params():
    path = find_params_json()
    if not path:
        return None, None
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:3] == b"\xef\xbb\xbf":
        raw = raw[3:]
    data = json.loads(raw.decode("utf-8"))
    return data, path


def main():
    _print(u"## AI 一键建模")

    params, path = load_params()
    if params is None:
        try:
            from Autodesk.Revit.UI import TaskDialog, TaskDialogCommonButtons
            dlg = TaskDialog(u"AI 一键建模")
            dlg.MainInstruction = u"未找到 building_params.json"
            dlg.MainContent = (
                u"请将 building_params.json 放入以下任一位置：\n"
                u"  1. 当前项目文件同目录\n"
                u"  2. 扩展 lib 目录:\n" + LIB)
            dlg.CommonButtons = TaskDialogCommonButtons.Ok
            dlg.Show()
        except Exception:
            pass
        return

    _print(u"参数文件: %s" % path)

    binfo = params.get("building_info", {})
    structural = params.get("structural_system", {})
    total_floors = binfo.get("total_floors", 1)
    length = binfo.get("footprint_length_m", 12.0)
    width = binfo.get("footprint_width_m", 9.0)
    wall_t = structural.get("wall_thickness_mm", 200)

    _print(u"- 层数: **%d**" % total_floors)
    _print(u"- 尺寸: **%.1f x %.1f m**" % (length, width))
    _print(u"- 墙厚: **%d mm**" % wall_t)
    _print(u"")

    result = build_model(doc, params, _print)

    _print(u"")
    _print(u"---")
    _print(u"**模型生成完成**")
    _print(u"- 标高: %d 个" % result["levels"])
    _print(u"- 外墙: %d 堵" % result["ext_walls"])
    _print(u"- 内墙: %d 堵" % result["int_walls"])
    _print(u"- 楼板: %d 块" % result["floors"])
    _print(u"- 屋顶: %s" % (u"已创建" if result["roof"] else u"未创建"))
    _print(u"- 窗户: %d 个" % result["windows"])
    _print(u"- 门: %d 个" % result["doors"])
    _print(u"- 阳台: %d 个" % result["balconies"])
    _print(u"")
    _print(u"_AI 三视图识别 → Revit 自动建模_")


if __name__ == "__main__":
    main()
else:
    main()
