# -*- coding: utf-8 -*-
import sys, json
from collections import Counter
sys.path.insert(0, r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/lib")
import bimconv_rules as rules

csv = rules.load_rules(r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/BIMToolkit.tab/BIM 工具.panel/DWG翻模.pushbutton/mapping_rules.csv")
print("rules:", len(csv))
for r in csv:
    print(" ", r["match_type"], r["pattern"], "->", r["element_type"], "lvl", r["level"])

plan = json.load(open(r"E:/bim-toolkit/pyrevit-bim-panel/cad_samples/05_plan.json", encoding="utf-8-sig"))
print("entities:", len(plan["entities"]))
print(Counter(e["type"] for e in plan["entities"]))
print("insert layers:", Counter(e.get("layer") for e in plan["entities"] if e["type"] == "insert").most_common(12))

planned, unmatched = rules.plan_from_json(plan, csv, ["1F"])
print("planned:", len(planned), "unmatched:", len(unmatched))
print(Counter(p["element_type"] for p in planned))
