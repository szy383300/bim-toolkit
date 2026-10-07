# -*- coding: utf-8 -*-
u"""北馨05 一键重建流水线（Revit 开着 + MCP Bridge 已点击后运行）。

用法:  python rebuild_05.py

步骤:
  1. 体检: 桥版本 / 活动文档指纹（拒绝在已保存有内容的文档里重复翻模前先提示）
  2. dwg_to_model  (safe_mode 默认 True -> 只出骨架: 墙/轴网/板)
  3. create_walls  23 面填缝墙（含 4 面加长窗垛版，来自 refine_payload.json）
  4. create_door_window 23 个门窗（14 门 + 9 窗）
  5. 验收: 门=14 窗=9 墙=104，并大声提醒保存
"""
import io
import json
import sys

sys.path.insert(0, r"E:\bim-toolkit\mcp")
from bridge_client import call  # noqa: E402

PLAN = (r"D:\CAD\CAD\建筑图纸\房屋设计图"
        r"\小区住宅楼建筑结构施工图 270套"
        r"\北馨住宅平立面图\05_plan.json")
PAYLOAD = r"E:\bim-toolkit\mcp\refine_payload.json"


def main():
    # 1. 体检
    r = call({"type": "ping", "id": 1}, timeout=15)
    ver = r.get("result", {}).get("version")
    print(u"[1/5] 桥版本: %s" % ver)

    # 2. 翻模骨架
    r = call({"type": "dwg_to_model", "id": 2, "json_path": PLAN},
             timeout=300)
    res = r.get("result", r)
    if "error" in res:
        raise SystemExit(u"翻模失败: %s" % res["error"])
    print(u"[2/5] 翻模: planned=%s ok=%s fail=%s by_type=%s" % (
        res.get("planned"), res.get("built_ok"), res.get("built_fail"),
        res.get("by_type")))

    # 3. 填缝墙
    pay = json.load(io.open(PAYLOAD, encoding="utf-8"))
    r = call({"type": "create_walls", "id": 3,
              "walls": pay["create_walls"]}, timeout=280)
    res = r.get("result", r)
    print(u"[3/5] 填缝墙: created=%s failed=%s" % (
        res.get("created"), res.get("failed")))

    # 4. 门窗
    r = call({"type": "create_door_window", "id": 4,
              "openings": pay["create_door_window"]}, timeout=280)
    res = r.get("result", r)
    print(u"[4/5] 门窗: created=%s failed=%s" % (
        res.get("created"), res.get("failed")))
    for e in (res.get("errors") or []):
        print(u"   - %s" % e[:130])

    # 5. 验收
    counts = {}
    for cat in (u"门", u"窗", u"墙"):
        r = call({"type": "get_elements", "id": 5, "category": cat,
                  "limit": 300}, timeout=60)
        els = r.get("result", {}).get("elements", [])
        counts[cat] = len(els)
    print(u"[5/5] 验收: 门=%(门)s 窗=%(窗)s 墙=%(墙)s (目标 14/9/104)"
          % counts)
    ok = (counts[u"门"] == 14 and counts[u"窗"] == 9
          and counts[u"墙"] == 104)
    print(u"" == "" and (u"结果: 全部达标 ✓" if ok else u"结果: 与目标不符，看上面 errors"))
    print(u"")
    print(u"!! 立即 Ctrl+S 保存文档 —— 未保存的模型等于没有模型 !!")


if __name__ == "__main__":
    main()
