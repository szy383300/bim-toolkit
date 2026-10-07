# -*- coding: utf-8 -*-
"""
analyze_plan.py —— 分析 model_plan.json：实体统计 + 与 mapping_rules.csv 命中率。

  E:/AI-pyenvs/bim-dev/Scripts/python.exe analyze_plan.py model_plan.json [mapping_rules.csv]

输出：
  - 实体总数、按类型、按图层分布
  - 块(INSERT)引用的块名分布，并与 block 规则命中
  - 每个图层是否命中翻模规则、命中的构件类型、层高/楼层
  - 未命中（不会建构件）的图层 / 块名清单

mapping_rules.csv 列：match_type,pattern,element_type,level,height,type_hint,note
  match_type: layer | block（分别匹配图层名 / 块参照名）
  pattern    : 支持 * 通配（fnmatch，大小写不敏感）
  element_type: grid/wall/column/door/window/room/slab/beam ...
"""
import os
import sys
import csv
import json


def load_rules(csv_path):
    rules = []
    if not csv_path or not os.path.exists(csv_path):
        return rules
    with open(csv_path, "rb") as f:
        raw = f.read().decode("utf-8-sig")
    for row in csv.DictReader(raw.splitlines()):
        # 兼容不同列名（老版用 layer_pattern/category）
        pat = (row.get("pattern") or row.get("layer_pattern") or "").strip()
        et = (row.get("element_type") or row.get("category") or "").strip()
        mt = (row.get("match_type") or "layer").strip().lower()
        if mt not in ("layer", "block"):
            mt = "layer"
        rules.append({
            "match_type": mt,
            "pattern": pat,
            "element_type": et,
            "level": (row.get("level") or "").strip(),
            "height": (row.get("height") or "").strip(),
            "type_hint": (row.get("type_hint") or "").strip(),
            "note": (row.get("note") or "").strip(),
        })
    return rules


def match(rules, name):
    """fnmatch 命中（大小写不敏感），返回命中规则列表。"""
    import fnmatch
    hits = []
    for r in rules:
        pat = r.get("pattern", "")
        if not pat:
            continue
        if fnmatch.fnmatch(name.upper(), pat.upper()) or fnmatch.fnmatch(name, pat):
            hits.append(r)
    return hits


def main():
    if len(sys.argv) < 2:
        print("用法: analyze_plan.py model_plan.json [mapping_rules.csv]")
        sys.exit(1)
    plan_path = sys.argv[1]
    rules_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(plan_path), "BIMToolkit.tab", "BIM 工具.panel",
        "DWG翻模.pushbutton", "mapping_rules.csv")
    # 也尝试同目录 / 常见位置
    if not os.path.exists(rules_path):
        for cand in (
            os.path.join(os.path.dirname(os.path.abspath(plan_path)), "mapping_rules.csv"),
            r"E:/bim-toolkit/pyrevit-bim-panel/BIMToolkit.extension/BIMToolkit.tab/BIM 工具.panel/DWG翻模.pushbutton/mapping_rules.csv",
        ):
            if os.path.exists(cand):
                rules_path = cand
                break

    plan = json.load(open(plan_path, "r", encoding="utf-8-sig"))
    rules = load_rules(rules_path)
    layer_rules = [r for r in rules if r["match_type"] == "layer"]
    block_rules = [r for r in rules if r["match_type"] == "block"]

    ents = plan.get("entities", [])
    by_type, by_layer, inserts = {}, {}, {}
    for e in ents:
        by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        L = e.get("layer", "")
        by_layer[L] = by_layer.get(L, 0) + 1
        if e["type"] == "insert":
            inserts[e.get("block", "")] = inserts.get(e.get("block", ""), 0) + 1

    print("=" * 64)
    print("源文件 : %s  (format=%s)" % (plan.get("source"), plan.get("format")))
    print("图层数 : %d   块数: %d   实体数: %d" % (
        len(plan.get("layers", [])), len(plan.get("blocks", [])), len(ents)))
    print("-" * 64)
    print("按类型: %s" % by_type)
    print("块引用: %s" % (inserts if inserts else "无"))

    print("-" * 64)
    print("图层命中分析（规则: %s）" % os.path.basename(rules_path))
    unmatched = []
    for L in sorted(by_layer.keys()):
        hits = match(layer_rules, L)
        if hits:
            desc = ", ".join("[%s→%s/%s h=%s]" % (
                h["pattern"], h["element_type"], h["level"], h["height"]) for h in hits)
            print("  %-16s x%-4d 命中 %s" % (L, by_layer[L], desc))
        else:
            unmatched.append(L)
            print("  %-16s x%-4d 未命中（不建构件）" % (L, by_layer[L]))

    if inserts:
        print("-" * 64)
        print("块参照命中分析（block 规则）")
        ub = []
        for B in sorted(inserts.keys()):
            hits = match(block_rules, B)
            if hits:
                desc = ", ".join("[%s→%s/%s]" % (
                    h["pattern"], h["element_type"], h["level"]) for h in hits)
                print("  %-16s x%-4d 命中 %s" % (B, inserts[B], desc))
            else:
                ub.append(B)
                print("  %-16s x%-4d 未命中（不建构件）" % (B, inserts[B]))

    print("-" * 64)
    if unmatched:
        print("未命中图层(%d): %s" % (len(unmatched), unmatched))
    else:
        print("全部图层均已命中翻模规则 ✓")
    if inserts and ub:
        print("未命中块(%d): %s" % (len(ub), ub))
    print("=" * 64)


if __name__ == "__main__":
    main()
