# -*- coding: utf-8 -*-
"""临时回放：验证 v2.9 门窗改判（含开启弧的块 窗→门）。"""
import json
import sys
sys.path.insert(0, r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/lib")
import bimconv_rules as rules

plan_json = json.load(open(r"E:/bim-toolkit/pyrevit-bim-panel/cad_samples/2平面_t3_plan.json", encoding="utf-8-sig"))
csv_rules = rules.load_rules(r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/BIMToolkit.tab/BIM 工具.panel/DWG翻模.pushbutton/mapping_rules.csv")
levels = ["1F"]
planned, unmatched = rules.plan_from_json(plan_json, csv_rules, levels)

from collections import Counter
cnt = Counter(p["element_type"] for p in planned)
print("planned by type:", dict(cnt))
print("unmatched:", len(unmatched))

doors = [p for p in planned if p["element_type"] == "door"]
wins = [p for p in planned if p["element_type"] == "window"]
flip = [p for p in doors if u"改判" in (p.get("note") or "")]
print("doors=%d (其中改判=%d), windows=%d" % (len(doors), len(flip), len(wins)))
for p in doors[:3]:
    print("  door sample:", p["source"], p["geom"].get("block"), p["geom"].get("layer"), "h=", p["height"])
for p in wins[:3]:
    print("  window sample:", p["source"], p["geom"].get("block"), p["geom"].get("layer"), "h=", p["height"])
# 断言：任何残留 window 不得带 has_arc
bad = [p for p in wins if (p.get("geom") or {}).get("has_arc")]
assert not bad, "仍有带开启弧的块被判为窗: %d" % len(bad)
# 断言：2平面_t3 实测 $DorLib2D$ 门块 300+，改判+块名规则命中的门必须可观
assert len(doors) >= 300, "门数过少: %d" % len(doors)
print("[replay] v2.9 door/window reclassification OK")
