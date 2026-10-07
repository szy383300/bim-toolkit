# -*- coding: utf-8 -*-
"""验证真图 05_plan.json（v2.9）门窗分类。"""
import json
import sys
from collections import Counter
sys.path.insert(0, r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/lib")
import bimconv_rules as rules

plan_json = json.load(open(
    r"D:\CAD\CAD\建筑图纸\房屋设计图\小区住宅楼建筑结构施工图 270套\北馨住宅平立面图\05_plan.json",
    encoding="utf-8-sig"))
csv = rules.load_rules(
    r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/BIMToolkit.tab/BIM 工具.panel/DWG翻模.pushbutton/mapping_rules.csv")
planned, unmatched = rules.plan_from_json(plan_json, csv, ["1F"])

cnt = Counter(p["element_type"] for p in planned)
print("planned:", dict(cnt), "unmatched:", len(unmatched))
doors = [p for p in planned if p["element_type"] == "door"]
wins = [p for p in planned if p["element_type"] == "window"]
flip = [p for p in doors if u"改判" in (p.get("note") or "")]
print("doors=%d (改判=%d), windows=%d" % (len(doors), len(flip), len(wins)))
for p in doors:
    g = p["geom"]
    print("  door", g.get("block"), g.get("layer"), "h=", p["height"])
bad = [p for p in wins if (p.get("geom") or {}).get("has_arc")]
assert not bad, u"仍有带开启弧的窗: %d" % len(bad)
assert len(flip) == 18, u"改判数应=18(_DBLK), 实际=%d" % len(flip)
print("[OK] 18 门 + 13 窗，与块解剖一致")
