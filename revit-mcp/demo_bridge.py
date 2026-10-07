#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
demo_bridge.py — 用 BIMToolkit MCP 原子工具在 Revit 里建一座梁桥。

结构（桥轴线沿 +X，mm）：
  标高   地面 ±0.000 / 桥面 +8.000
  轴网   竖向 1-5（桥墩轴线 x=0/12.5/25/37.5/50m）+ 横向 A（中线）/B（桥面边缘）
  桥面板 6 条 2m 厚扁墙并排：50m 长 × 12m 宽 × 0.5m 厚，底 +7.5m
  桥墩   3 个：横桥向墙 10m 长 × 1.5m 厚 × 7.5m 高（x=12.5/25/37.5m）
  桥台   2 个：12m 长 × 1.5m 厚 × 8m 高（x=0.75/49.25m）
  栏杆   2 道：50m 长 × 0.2m 厚 × 1.2m 高，桥面上

只读用例之外全部走 v7.7 原子工具：create_levels / create_grids /
create_walls / query_elements / get_quantities。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from live_test_v77 import Client, read_token  # noqa: E402


def dump(tag, r, keys=None):
    if keys:
        r = {k: r.get(k) for k in keys if k in r}
    print("[{}] {}".format(tag, json.dumps(r, ensure_ascii=False)))


def main():
    c = Client(read_token())

    # ---- 标高 ----
    r = c.call("create_levels", {"levels": [
        {"name": "地面 ±0.000", "elevation_mm": 0},
        {"name": "桥面 +8.000", "elevation_mm": 8000},
    ]})
    dump("标高", r, ("created", "skipped", "errors"))

    # ---- 轴网 ----
    grids = [{"axis": "y", "coord_mm": x} for x in
             (0, 12500, 25000, 37500, 50000)]
    grids += [{"name": "A", "axis": "x", "coord_mm": 0},
              {"name": "B", "axis": "x", "coord_mm": 6000}]
    r = c.call("create_grids", {"grids": grids})
    dump("轴网", r, ("created", "skipped", "errors"))

    # ---- 墙（桥面板/桥墩/桥台/栏杆）----
    walls = []

    # 桥面板：6 条 2m 厚扁墙（y=-5000..5000，各 2m 厚 → 总宽 12m）
    for y in (-5000, -3000, -1000, 1000, 3000, 5000):
        walls.append({"start": [0, y, 7500], "end": [50000, y, 7500],
                      "height_mm": 500, "thickness_mm": 2000})

    # 桥墩 3 个（横桥向）
    for x in (12500, 25000, 37500):
        walls.append({"start": [x, -5000], "end": [x, 5000],
                      "height_mm": 7500, "thickness_mm": 1500})

    # 桥台 2 个
    for x in (750, 49250):
        walls.append({"start": [x, -6000], "end": [x, 6000],
                      "height_mm": 8000, "thickness_mm": 1500})

    # 栏杆 2 道
    for y in (-5900, 5900):
        walls.append({"start": [200, y, 8000], "end": [49800, y, 8000],
                      "height_mm": 1200, "thickness_mm": 200})

    r = c.call("create_walls", {"walls": walls})
    dump("墙", r, ("created", "failed", "errors"))

    # ---- 复核：查询墙 ----
    r = c.call("query_elements", {"category": u"墙", "limit": 50})
    print("[复核] 模型中墙总数 matched={}".format(r.get("total_matched")))

    # ---- 工程量 ----
    r = c.call("get_quantities", {"detail_category": u"墙"})
    counts = [x for x in (r.get("counts") or []) if x.get("category") == u"墙"]
    detail = r.get("detail") or []
    total_v = sum(d.get("volume_m3") or 0 for d in detail)
    print("[工程量] 墙: {} 根 / 总体积 {:.2f} m3".format(
        counts[0]["count"] if counts else 0, total_v))
    print("[造价] {}".format(json.dumps(
        r.get("cost_estimate"), ensure_ascii=False)))

    c.close()
    print("[DONE] 桥建好了——轴网/标高/墙已入模，Ctrl+Z 可整桥撤销。")


if __name__ == "__main__":
    main()
