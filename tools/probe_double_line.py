# -*- coding: utf-8 -*-
"""双线墙配对 / 吞墙探针（一次性测量工具，不进 CI）。

量三件事，都是"效果不好"的直接指标：
  1) 原始墙段里有多少条存在"平行 + 垂距在墙厚带内 + 轴向大比例重叠"的伙伴
     —— 这就是会被重复建造的量。
  2) 过一遍 consolidate_walls 之后这个量还剩多少。
  3) 墙线总长的保留率。**这条是防吞墙的**：合并本该把双线收成中线（≈50%），
     明显低于 50% 说明删掉的不只是重复线，而是真墙。

轴向重叠必须算：早先只看"平行 + 垂距"会把同轴相隔很远的两面墙也算成重复，
真值测试「同轴远隔不得吞墙」就是卡这个的。

用法：
    python tools/probe_double_line.py A.dxf B.dxf ...
    python tools/probe_double_line.py @list.txt      # 每行一个 DXF 路径
不带参数时跑仓库里的 cad_samples/（注意：那批是被处理过的样本，
真图结论要用 D:\\CAD\\... 转出的 DXF，别拿样本当真图）。
"""
from __future__ import division
import glob
import math
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIB = os.path.join(ROOT, "pyrevit-bim-panel", "BIMToolkit.extension", "lib")
sys.path.insert(0, LIB)

import dwg_to_json as dj
import bimconv_rules as br
import bimconv_walls as bw

RULES = os.path.join(LIB, "..", "BIMToolkit.tab", u"BIM 工具.panel",
                     "DWG翻模.pushbutton", "mapping_rules.csv")
LO, HI = 80.0, 420.0        # 墙厚合理区间(mm)
PARALLEL_TOL = 6.0          # 度
MIN_OVERLAP = 0.6           # 轴向重叠比例下限，低于此是两面不相干的墙
MIN_SEG = 200.0


def segs_of(plans):
    u"""从墙 plan 列表取世界坐标线段 (x1,y1,x2,y2,L)。"""
    out = []
    for p in plans:
        g = p.get("geom") or {}
        pts = g.get("points") or []
        if len(pts) < 2 and g.get("start") and g.get("end"):
            pts = [g["start"], g["end"]]
        n = len(pts)
        rng = range(n - 1)
        for i in rng:
            a, b = pts[i], pts[i + 1]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L >= MIN_SEG:
                out.append((a[0], a[1], b[0], b[1], L))
        if g.get("closed") and n > 2:
            a, b = pts[-1], pts[0]
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L >= MIN_SEG:
                out.append((a[0], a[1], b[0], b[1], L))
    return out


def ang(s):
    return math.degrees(math.atan2(s[3] - s[1], s[2] - s[0])) % 180.0


def para(a, b):
    d = abs(ang(a) - ang(b))
    return min(d, 180.0 - d) < PARALLEL_TOL


def _pt_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 0:
        return math.hypot(px - ax, py - ay)
    u = ((px - ax) * dx + (py - ay) * dy) / L2
    u = 0.0 if u < 0 else (1.0 if u > 1 else u)
    return math.hypot(px - (ax + u * dx), py - (ay + u * dy))


def pseg_dist(s, t):
    return min(_pt_seg(s[0], s[1], t[2], t[3], t[0], t[1]),
               _pt_seg(s[2], s[3], t[2], t[3], t[0], t[1]),
               _pt_seg(t[0], t[1], s[2], s[3], s[0], s[1]),
               _pt_seg(t[2], t[3], s[2], s[3], s[0], s[1]))


def axial_overlap(s, t):
    u"""s 在 t 轴向上的投影覆盖 t 自身长度的比例（0~1）。"""
    dx, dy = t[2] - t[0], t[3] - t[1]
    L2 = dx * dx + dy * dy
    if L2 <= 0:
        return 0.0
    u1 = ((s[0] - t[0]) * dx + (s[1] - t[1]) * dy) / L2
    u2 = ((s[2] - t[0]) * dx + (s[3] - t[1]) * dy) / L2
    lo, hi = min(u1, u2), max(u1, u2)
    return max(0.0, min(hi, 1.0) - max(lo, 0.0)) * math.sqrt(L2) / max(
        math.hypot(s[2] - s[0], s[3] - s[1]), 1e-9)


def count_dups(segs, sample_n=200, seed=13):
    u"""返回 (抽样数, 有真重复伙伴的段数)。判据含轴向重叠，避免误数不相干平行墙。"""
    if not segs:
        return 0, 0
    rnd = random.Random(seed)
    samp = segs if len(segs) <= sample_n else rnd.sample(segs, sample_n)
    n = 0
    for s in samp:
        hit = False
        for t in segs:
            if t is s or not para(s, t):
                continue
            if not (LO <= pseg_dist(s, t) <= HI):
                continue
            if max(axial_overlap(s, t), axial_overlap(t, s)) < MIN_OVERLAP:
                continue
            hit = True
            break
        if hit:
            n += 1
    return len(samp), n


def probe(path, levels=(u"标高 1",)):
    rules = br.load_rules(RULES)
    _, _, ents, _, _ = dj.read_dwg(path, explode_blocks=False)
    planned, _u = br.plan_from_json({"entities": ents}, rules, list(levels))
    raw = [p for p in planned if p["element_type"] == "wall"]
    cons = [p for p in bw.consolidate_walls(planned) if p["element_type"] == "wall"]
    sr, sc = segs_of(raw), segs_of(cons)
    nr, dr = count_dups(sr)
    nc, dc = count_dups(sc)
    lr = sum(s[4] for s in sr) / 1000.0
    lc = sum(s[4] for s in sc) / 1000.0
    thk = sum(1 for p in cons if p.get("thickness_mm"))
    return dict(name=os.path.basename(path), segs_raw=len(sr), segs_cons=len(sc),
                len_raw=lr, len_cons=lc,
                dup_raw=100.0 * dr / nr if nr else 0.0,
                dup_cons=100.0 * dc / nc if nc else 0.0,
                thk_pct=100.0 * thk / len(cons) if cons else 0.0)


def main():
    argv = sys.argv[1:]
    if argv and argv[0].startswith("@"):
        files = [l.strip() for l in open(argv[0][1:], "r") if l.strip()]
    elif argv:
        files = argv
    else:
        files = sorted(glob.glob(os.path.join(ROOT, "pyrevit-bim-panel",
                                              "cad_samples", "*.dxf")))
        print(u"（未给路径，跑的是 cad_samples —— 那批是被处理过的样本，"
              u"不能当真实图纸语料）")
    rows = []
    for f in files:
        try:
            r = probe(f)
        except Exception as e:
            print(u"%-18s 探针失败 %s: %s"
                  % (os.path.basename(f), type(e).__name__, str(e)[:70]))
            continue
        rows.append(r)
        print(u"%-18s 墙段 %6d -> %5d  真重复 %5.1f%% -> %5.1f%%  "
              u"墙线 %8.0fm -> %7.0fm (保留 %4.0f%%)  带实测厚度 %3.0f%%"
              % (r["name"], r["segs_raw"], r["segs_cons"],
                 r["dup_raw"], r["dup_cons"], r["len_raw"], r["len_cons"],
                 100.0 * r["len_cons"] / r["len_raw"] if r["len_raw"] else 0,
                 r["thk_pct"]))
    if not rows:
        return 1
    tr = sum(r["len_raw"] for r in rows)
    tc = sum(r["len_cons"] for r in rows)
    print(u"\n%d 张图：墙线 %0.0fm -> %0.0fm（保留 %.1f%%）"
          % (len(rows), tr, tc, 100.0 * tc / tr if tr else 0))
    print(u"双线取中的理想保留率≈50%；明显低于 50% 说明删掉的是真墙不是重复线。"
          u"残余真重复率就是「聚类后还会被重复建造」的量。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
