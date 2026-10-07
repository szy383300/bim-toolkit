# -*- coding: utf-8 -*-
"""make_icons.py —— 为缺少 icon.png 的 pushbutton 生成占位图标（headless，matplotlib Agg）。

用法：E:/AI-pyenvs/bim-dev/Scripts/python.exe make_icons.py

2026-10-01 改：
  原来只写死 7 个按钮，新增的 12 个按钮完全不在覆盖范围，
  结果「写入诊断」上线时没有图标 —— 直到 selftest 第 3 关改成自动发现才被抓到。

  现在改为：**遍历 tab 下全部 .pushbutton，只补缺失的**。
    - 已存在的图标一律不碰（Create Villa 等有正式美术图，覆盖会造成实际损失）
    - 新增按钮自动获得占位图标，不必再改本脚本
  图标只是占位视觉，pyRevit 缺图标也能用（显示默认 logo），可随时换成正式美术图。
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
TAB = os.path.join(BASE, "BIMToolkit.extension", "BIMToolkit.tab")

# 配色与字母。注意字母要用 DejaVu Sans 装得下的字符（别用中文，会渲染成方框）。
STYLE = {
    u"楼层清点.pushbutton": (u"#1565c0", u"L"),
    u"一键统计.pushbutton": (u"#2e7d32", u"S"),
    u"批量改参.pushbutton": (u"#ef6c00", u"P"),
    u"构件清单导出.pushbutton": (u"#6a1b9a", u"E"),
    u"BIM 体检.pushbutton": (u"#c62828", u"H"),
    u"进度关联.pushbutton": (u"#00838f", u"4"),
    u"DWG翻模.pushbutton": (u"#5e35b1", u"D"),
    u"写入诊断.pushbutton": (u"#455a64", u"W"),
    u"翻模体检.pushbutton": (u"#00897b", u"Q"),
    u"AI 指令.pushbutton": (u"#6d4c41", u"A"),
    u"MCP Bridge.pushbutton": (u"#0277bd", u"M"),
    u"Create Villa.pushbutton": (u"#7b1fa2", u"V"),
    u"配置建模.pushbutton": (u"#00695c", u"G"),
}
DEFAULT_COLOR = u"#37474f"


def _fallback_letter(folder):
    """占位字母：优先取按钮名里的 ASCII 字母/数字，取不到就用 B。"""
    name = os.path.basename(folder)
    if name.endswith(u".pushbutton"):
        name = name[:-len(u".pushbutton")]
    for ch in name:
        if (u"a" <= ch <= u"z") or (u"A" <= ch <= u"Z") or (u"0" <= ch <= u"9"):
            return ch.upper()
    return u"B"


def draw(path, color, letter):
    fig, ax = plt.subplots(figsize=(0.64, 0.64), dpi=100)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.add_patch(plt.Rectangle((0.08, 0.08), 0.84, 0.84,
                               facecolor=color, edgecolor="white", lw=0.04))
    ax.text(0.5, 0.52, letter, ha="center", va="center",
            fontsize=34, color="white", fontweight="bold",
            fontfamily="DejaVu Sans")
    ax.axis("off")
    fig.savefig(path, dpi=100, transparent=True)
    plt.close(fig)


def main():
    made = []
    skipped = 0
    for root, dirs, _files in os.walk(TAB):
        for d in sorted(dirs):
            if not d.endswith(u".pushbutton"):
                continue
            full = os.path.join(root, d)
            if not os.path.isfile(os.path.join(full, "script.py")):
                continue
            out = os.path.join(full, "icon.png")
            if os.path.exists(out):
                skipped += 1
                continue
            color, letter = STYLE.get(d, (DEFAULT_COLOR, _fallback_letter(d)))
            draw(out, color, letter)
            made.append(os.path.relpath(out, TAB))
            print("wrote " + out)
    print("icons done: wrote=%d skipped(existing)=%d" % (len(made), skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
