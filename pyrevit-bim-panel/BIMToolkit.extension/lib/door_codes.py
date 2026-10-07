# -*- coding: utf-8 -*-
u"""door_codes.py —— 门窗编号解析器 v1.0。

从 DWG/DXF 文本层提取门窗口编号（M1022 / C2115 / FM乙1521 …），
按行业编号惯例解码洞口尺寸，并与 openings（融合检出）按就近匹配绑定。

编号惯例（宽高单位 dm，1dm=100mm）：
  M1022  → 1000 x 2200   门（M + 宽2位 + 高2位）
  M0622  →  600 x 2200
  C2115  → 2100 x 1500   窗（C + 宽2位 + 高2位）
  M822   →  800 x 2200   3位缩写（宽1位）
  M0522B →  500 x 2200   字母后缀=变体，不影响尺寸
  FM乙1521 → 防火门 1500 x 2100（FM + 甲/乙/丙 + 宽2位 + 高2位）

纯文本+几何，无 Revit 依赖；兼容 IronPython 2.7（u""、无 f-string）。
"""
import math
import re

# 宽容口径：M/C/F 开头 + 2~4 位数字 + 可选字母后缀
_CODE_LOOSE = re.compile(u"^([MFC]{1,2}[甲乙丙]?)(\\d{2,4})([A-Za-z])?$")


def decode_code(code):
    u"""编号 → (width_mm, height_mm) 或 None。

    M1022→(1000,2200); M822→(800,2200); M0522B→(500,2200)。
    高度 2 位（dm）；宽取剩余前 2 位（或 3 位缩写的 1 位）。
    """
    if not code:
        return None
    m = _CODE_LOOSE.match(code.strip())
    if not m:
        return None
    digits = m.group(2)
    if len(digits) == 4:
        w, h = digits[:2], digits[2:]
    elif len(digits) == 3:
        w, h = digits[0], digits[1:]
    else:
        return None
    try:
        wmm, hmm = int(w) * 100, int(h) * 100
    except ValueError:
        return None
    if wmm < 300 or wmm > 4000 or hmm < 300 or hmm > 4000:
        return None
    return wmm, hmm


def is_door_code(code):
    return bool(code) and code.strip().upper().startswith(u"M")


def harvest_texts(dxf_path):
    u"""DXF → [(text, x, y)] 世界坐标（msp TEXT/MTEXT）。

    需 ezdxf；调用方负责把 DWG 先转 DXF（dwg_to_json._convert_dwg_to_dxf）。
    """
    import ezdxf
    doc = ezdxf.readfile(dxf_path)
    out = []
    for e in doc.modelspace():
        t = e.dxftype()
        try:
            if t == "TEXT":
                s = e.dxf.text
                p = e.dxf.insert
            elif t == "MTEXT":
                s = e.text
                p = e.dxf.insert
            else:
                continue
        except Exception:
            continue
        if s:
            out.append((s, float(p[0]), float(p[1])))
    return out


def extract_codes(texts, dedupe_tol=100.0):
    u"""[(text,x,y)] → [dict(code,w,h,x,y)]。同位同码去重。"""
    codes = []
    for s, x, y in texts:
        sc = s.strip()
        if not sc or len(sc) > 12:
            continue
        if not _CODE_LOOSE.match(sc):
            continue
        wh = decode_code(sc)
        if not wh:
            continue
        codes.append({"code": sc, "w": wh[0], "h": wh[1],
                      "x": x, "y": y})
    # 同位置同码去重（表格区/多重标注）
    kept = []
    for c in codes:
        dup = False
        for k in kept:
            if (k["code"] == c["code"]
                    and abs(k["x"] - c["x"]) < dedupe_tol
                    and abs(k["y"] - c["y"]) < dedupe_tol):
                dup = True
                break
        if not dup:
            kept.append(c)
    return kept


def attach_codes(openings, codes, max_dist=1500.0):
    u"""把就近编码绑到 openings 上（就地覆盖 width_mm/height_mm）。

    openings: [{"kind":"door","point":[x,y],"width_mm":..,"height_mm":..}, ...]
    匹配规则：
      - 门检出优先匹配 M/F 开头编号，窗检出优先 C 开头；
        优先级不中时放宽到任意编号（图纸门编号写成 C 的情况兜底）。
    返回 stats dict：n_total/n_matched/n_prio/n_fallback/n_nomatch,
    以及 width_diff 直方图（编码宽 vs 原 bbox 宽）。
    """
    stats = {"n_total": len(openings), "n_matched": 0, "n_prio": 0,
             "n_fallback": 0, "n_nomatch": 0, "diff_bins": {},
             "unmatched_examples": []}
    if not codes:
        stats["n_nomatch"] = stats["n_total"]
        return stats
    for op in openings:
        px, py = op["point"][0], op["point"][1]
        kind = op.get("kind", "door")
        want = u"M" if kind == "door" else u"C"
        best_prio = None
        best_fb = None
        for c in codes:
            d = math.hypot(c["x"] - px, c["y"] - py)
            if d > max_dist:
                continue
            prio = c["code"].upper().startswith(want)
            cand = (d, c)
            if prio:
                if best_prio is None or d < best_prio[0]:
                    best_prio = cand
            else:
                if best_fb is None or d < best_fb[0]:
                    best_fb = cand
        pick = best_prio or best_fb
        if pick is None:
            stats["n_nomatch"] += 1
            if len(stats["unmatched_examples"]) < 8:
                stats["unmatched_examples"].append(
                    u"%s @(%d,%d)" % (kind, int(px), int(py)))
            continue
        c = pick[1]
        old_w = op.get("width_mm")
        op["width_mm"] = float(c["w"])
        op["height_mm"] = float(c["h"])
        op["code"] = c["code"]
        op["code_src"] = "prio" if best_prio else "fallback"
        stats["n_matched"] += 1
        if best_prio:
            stats["n_prio"] += 1
        else:
            stats["n_fallback"] += 1
        if old_w:
            b = int(abs(old_w - c["w"]) // 200) * 200
            key = u"%d-%d" % (b, b + 200)
            stats["diff_bins"][key] = stats["diff_bins"].get(key, 0) + 1
    return stats
