# -*- coding: utf-8 -*-
u"""生成 云弦塔.yaml —— 17层未来主义塔楼 (仅南侧收退).

17F = L1-L2 裙房(h3600) + L3-L12 标准层(h3300) + L13-L17 南退台(h3300);
平面 24000x16000, 筒 5400x5400 @x9300-14700/y10300-15700(北贴);
南侧每层收 1600: L13 y1600 ... L17 y8000; 总高 56700;
平屋顶+女儿墙+机房墙(塔冠); 落地玻璃 3200x2850 sill450。
"""
import io

LV = [(u"标高 1", 0), (u"标高 2", 3600)]
for i in range(3, 18):
    LV.append((u"标高 %d" % i, 7200 + (i - 3) * 3300))
LV.append((u"屋顶", 56700))
EL = dict(LV)

OUTER_FULL = [[0, 0, 24000, 0], [0, 16000, 24000, 16000],
              [0, 0, 0, 16000], [24000, 0, 24000, 16000]]
CORE = [[9300, 10300, 14700, 10300], [9300, 15700, 14700, 15700],
        [9300, 10300, 9300, 15700], [14700, 10300, 14700, 15700]]

L = []
W = L.append


def segs_yaml(segs, indent=u"  "):
    return u", ".join(u"[%d, %d, %d, %d]" % tuple(s) for s in segs)


W(u"schema: 1")
W(u"name: 云弦塔")
W(u"note: >-")
W(u"  17 层未来主义塔楼 (2026-09-13 设计, 用户确认仅南侧收退)。")
W(u"  L1-L2 裙房 h3600 通高玻璃大堂; L3-L12 标准层 h3300 完全重复")
W(u"  (450 银白腰线 + 2850 落地玻璃, 柱网 4800); L13-L17 仅南侧每层收 1600")
W(u"  形成南向阶梯露台 (北/东/西垂直); 核心 5400x5400 北贴贯通; 总高 56700;")
W(u"  平屋顶 + 女儿墙 + 机房塔冠。落地玻璃 3200x2850 sill450, 共 %d 樘。" % 260)
W(u"expect_doc: 项目1")
W(u"takeover: true")
W(u"save_path: E:\\bim-toolkit\\models\\云弦塔.rvt")
W(u"levels:")
for n, e in LV:
    W(u"- {name: %s, elev: %d}" % (n, e))
W(u"grids:")
for i, x in enumerate([0, 4800, 9600, 14400, 19200, 24000]):
    W(u'- {name: "%d", from: [%d, -1500], to: [%d, 17500]}' % (i + 1, x, x))
for j, y in enumerate([0, 4000, 8000, 12000, 16000]):
    W(u'- {name: "%s", from: [-1500, %d], to: [25500, %d]}'
      % (chr(ord(u"A") + j), y, y))
W(u"walls:")
for idx in (0, 1):
    n = LV[idx][0]
    W(u"- {level: %s, t: 240, h: 3600, segs: [%s]}" % (n, segs_yaml(OUTER_FULL + CORE)))
    W(u"- {level: %s, t: 120, h: 3600, segs: [[12000, 10300, 12000, 15700]]}" % n)
for idx in range(2, 17):
    n = LV[idx][0]
    if idx <= 11:
        south = 0
        outer = OUTER_FULL
    else:
        south = 1600 * (idx - 11)
        outer = [[0, south, 24000, south],
                 [0, 16000, 24000, 16000],
                 [0, south, 0, 16000], [24000, south, 24000, 16000]]
    W(u"- {level: %s, t: 240, h: 3300, segs: [%s]}" % (n, segs_yaml(outer + CORE)))
    W(u"- {level: %s, t: 120, h: 3300, segs: [[12000, 10300, 12000, 15700]]}" % n)
parapet = [[0, 8000, 24000, 8000], [0, 16000, 24000, 16000],
           [0, 8000, 0, 16000], [24000, 8000, 24000, 16000]]
W(u"- {level: 屋顶, t: 240, h: 900, segs: [%s]}" % segs_yaml(parapet))
W(u"floors:")
for idx in range(0, 17):
    n = LV[idx][0]
    if idx <= 11:
        south = 0
    else:
        south = 1600 * (idx - 12)
    pts = u"[[0, %d], [24000, %d], [24000, 16000], [0, 16000]]" % (south, south)
    if idx == 0:
        W(u"- {level: %s, points: %s, structural: true}" % (n, pts))
    else:
        W(u"- {level: %s, points: %s}" % (n, pts))
W(u"- {level: 屋顶, points: [[0, 8000], [24000, 8000], [24000, 16000], [0, 16000]]}")
W(u"stairs:")
for i in range(1, 17):
    W(u"- {from: 标高 %d, to: 标高 %d, at: [9400, 10400], dir: [0, 1]}"
      % (i, i + 1))
W(u"railing:")
for k in range(5):
    W(u"- {level: 标高 %d, path: [[60, %d], [23940, %d]]}"
      % (12 + k, 1600 * k, 1600 * k))
W(u"families:")
W(u"- {path: C:\\ProgramData\\Autodesk\\RVT 2019\\Libraries\\China\\建筑\\门\\普通门\\平开门\\单扇\\单嵌板木门 1.rfa}")
W(u"- {path: C:\\ProgramData\\Autodesk\\RVT 2019\\Libraries\\China\\建筑\\门\\普通门\\平开门\\双扇\\双面嵌板木门 1.rfa}")
W(u"- {path: C:\\ProgramData\\Autodesk\\RVT 2019\\Libraries\\China\\建筑\\窗\\普通窗\\固定窗\\固定窗.rfa}")
W(u"openings:")
W(u"# ===== 裙房 L1-L2: 南 4 + 北 2 + 东西各 2 = 10/层 (h3000 窗台450 顶3450<墙3600) =====")
for idx in (0, 1):
    n = LV[idx][0]
    for x in (3600, 8400, 15600, 20400):
        W(u"- {kind: window, at: [%d, 0], w: 3600, h: 3000, sill: 450, level: %s}"
          % (x, n))
    for x in (9600, 14400):
        W(u"- {kind: window, at: [%d, 16000], w: 3600, h: 3000, sill: 450, level: %s}"
          % (x, n))
    for y in (5300, 10700):
        W(u"- {kind: window, at: [0, %d], w: 3600, h: 3000, sill: 450, level: %s}"
          % (y, n))
        W(u"- {kind: window, at: [24000, %d], w: 3600, h: 3000, sill: 450, level: %s}"
          % (y, n))
W(u"# ===== 标准层+退台层 L3-L17: 南 5 + 北 5 + 东西各 2~3 = 14~16/层 =====")
for idx in range(2, 17):
    n = LV[idx][0]
    south = 0 if idx <= 11 else 1600 * (idx - 11)
    for x in (2400, 7200, 12000, 16800, 21600):
        W(u"- {kind: window, at: [%d, %d], w: 3200, h: 2850, sill: 450, level: %s}"
          % (x, south, n))
        W(u"- {kind: window, at: [%d, 16000], w: 3200, h: 2850, sill: 450, level: %s}"
          % (x, n))
    if south <= 0:
        ewys = (3000, 8000, 13000)
    elif south <= 1600:
        ewys = (3400, 8000, 12600)
    elif south <= 3200:
        ewys = (5000, 8000, 13000)
    elif south <= 4800:
        ewys = (7200, 12800)
    elif south <= 6400:
        ewys = (8800, 13600)
    else:
        ewys = (12000,)
    for y in ewys:
        W(u"- {kind: window, at: [0, %d], w: 3200, h: 2850, sill: 450, level: %s}"
          % (y, n))
        W(u"- {kind: window, at: [24000, %d], w: 3200, h: 2850, sill: 450, level: %s}"
          % (y, n))
W(u"# ===== 门: 主入口 + 筒门 17 =====")
W(u"- {kind: door, at: [12000, 0], w: 2400, h: 2700, level: 标高 1, fam: 双面}")
for idx in range(0, 17):
    W(u"- {kind: door, at: [13500, 10300], w: 1200, h: 2100, level: %s}"
      % LV[idx][0])
W(u"materials:")
W(u"- {group: floor_porch, pick: [混凝土]}")
W(u"finishes:")
W(u"- {type_name: MCP-W-240mm, material: {name: 银白铝板涂料, r: 235, g: 237,"
  u" b: 240, smoothness: 70}}")
W(u"- {type_name: MCP-W-120mm, material: {name: 浅灰内墙涂料, r: 196, g: 200,"
  u" b: 205}}")
W(u"expect: {walls: 157, doors: 18, windows: 252, floors: 34, stairs: 16,"
  u" columns: 0, generic_models: 0}")

io.open(r"E:\bim-toolkit\buildings\云弦塔.yaml", "w", encoding="utf-8").write(
    u"\n".join(L) + u"\n")
print(u"OK lines=%d" % len(L))
