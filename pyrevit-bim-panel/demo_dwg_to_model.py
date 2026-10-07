# -*- coding: utf-8 -*-
"""端到端演示：图纸(DWG) -> 中性 JSON -> 按规则映射成建模计划 -> 预览 HTML。
不含 Revit 写入（那一步需开 Revit 点按钮）；本脚本证明"读图+映射"链路可用。
环境：E:/AI-pyenvs/bim-dev/Scripts/python.exe（有 ezdxf）
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, "BIMToolkit.extension", "lib")
RULES = os.path.join(HERE, "BIMToolkit.extension", "BIMToolkit.tab",
                     "BIM 工具.panel", "DWG翻模.pushbutton", "mapping_rules.csv")
OUTDIR = os.path.join(HERE, "demo_output")
sys.path.insert(0, LIB)

from dwg_to_json import convert, _selftest  # noqa 复用转换器需 ezdxf
from bimconvert import load_rules, plan_from_json, RELIABILITY

import ezdxf


def build_sample_dwg(path):
    """一张示意小平面：轴网线 + 两段外墙(闭合) + 几根柱 + 一道门。"""
    d = ezdxf.new("R2010")
    for nm in ("GRID", "WALL", "COL", "DOOR"):
        d.layers.add(nm)
    msp = d.modelspace()
    # 轴网
    msp.add_line((0, 0, 0), (12000, 0, 0), dxfattribs={"layer": "GRID"})
    msp.add_line((0, 0, 0), (0, 9000, 0), dxfattribs={"layer": "GRID"})
    # 外墙（闭合）
    msp.add_lwpolyline([(0, 0), (12000, 0), (12000, 9000), (0, 9000)],
                       close=True, dxfattribs={"layer": "WALL"})
    # 内墙
    msp.add_lwpolyline([(6000, 0), (6000, 9000)], dxfattribs={"layer": "WALL"})
    # 柱
    blk = d.blocks.new("COL-A")
    blk.add_point((0, 0))
    for (x, y) in [(0, 0), (12000, 0), (0, 9000), (12000, 9000), (6000, 4500)]:
        msp.add_blockref("COL-A", (x, y, 0), dxfattribs={"layer": "COL"})
    # 门
    dblk = d.blocks.new("DOOR-S")
    dblk.add_point((0, 0))
    msp.add_blockref("DOOR-S", (3000, 0, 0), dxfattribs={"layer": "DOOR"})
    d.saveas(path)
    return path


def html_table(headers, rows):
    th = "".join("<th>%s</th>" % h for h in headers)
    body = ""
    for r in rows:
        body += "<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>"
    return "<table border='1' cellspacing='0' cellpadding='6'>" \
           "<thead>%s</thead><tbody>%s</tbody></table>" % (th, body)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="dwgdemo_")
    dwg = os.path.join(tmp, "sample_floor.dwg")
    build_sample_dwg(dwg)

    json_path, plan = convert(dwg, os.path.join(OUTDIR, "sample_floor_plan.json"))
    rules = load_rules(RULES)
    levels = ["1F", "2F"]
    planned, unmatched = plan_from_json(plan, rules, levels)

    counts = {}
    for p in planned:
        counts[p["element_type"]] = counts.get(p["element_type"], 0) + 1

    cr = [[k, counts[k], RELIABILITY.get(k, "experimental")] for k in counts]
    um = [[u.get("type"), u.get("layer") or u.get("block"), u.get("handle")]
          for u in unmatched[:200]]

    html = u"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>DWG 翻模预览（演示）</title>
<style>body{font-family:system-ui,'Microsoft YaHei',sans-serif;margin:24px;color:#222}
h1{font-size:20px}table{border-collapse:collapse;margin:10px 0;font-size:14px}
th{background:#2b6cb0;color:#fff}td,th{border:1px solid #ccc;padding:6px 10px}
.mature{color:#2f855a;font-weight:600}.exp{color:#c05621}
.note{color:#666;font-size:13px}</style></head><body>
<h1>📐 DWG 翻模预览（演示，未写入 Revit）</h1>
<p class="note">来源：<code>%s</code> ｜ 图纸实体 %d 个 ｜ 楼层候选：%s</p>
<h2>计划建模：%d 个</h2>
%s
%s
<p class="note">说明：mature=接口稳可直建（轴网/墙/柱）；experimental=试建、失败仅上报不中断
（门/窗/房间/板）。真实写入需在 Revit 内点「DWG翻模」按钮并确认。</p>
</body></html>""" % (
        plan.get("source"), len(plan.get("entities", [])), "、".join(levels),
        len(planned),
        html_table(["类型", "数量", "可靠性"], cr),
        (("<h2>⚠️ 未匹配（不建）：%d 个</h2>" % len(unmatched) +
         html_table(["图元", "层/块", "handle"], um)) if um else ""),
    )
    out_html = os.path.join(OUTDIR, "sample_floor_preview.html")
    with open(out_html, "wb") as f:
        f.write(html.encode("utf-8"))
    print("DWG:", dwg)
    print("JSON:", json_path, "(%d entities)" % len(plan.get("entities", [])))
    print("PLANNED:", len(planned), "UNMATCHED:", len(unmatched))
    print("PREVIEW:", out_html)
    return out_html


if __name__ == "__main__":
    main()
