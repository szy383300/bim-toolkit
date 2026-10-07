# -*- coding: utf-8 -*-
"""
door_schedule.py —— CAD 门窗表解析（M3）。

输入：门窗表所在 DXF（ezdxf 可读）
输出：标准 JSON
  {
    "title": "1#楼门窗表",
    "items": [
      {"kind": "door", "code": "M1-1021", "width_mm": 1000, "height_mm": 2100,
       "type_name": "镶板门", "counts": {"1层": "24", ...}, "total": "30",
       "std_ref": ["参闽", "85J602", "M1-0921"], "raw_row": [...]}
    ]
  }

原理：表格没有线框约束时也能读——
  1) TEXT/MTEXT 按 y 聚类成行（自适应行距，按文本高度的中位数定桶宽）
  2) 行内按 x 排序成单元格序列
  3) 找含"门窗表"的标题行；表头行(门窗名称/洞口尺寸)锚定列语义
  4) "门"/"窗"分隔行切换 kind；洞口尺寸"WxH"解析宽高
运行环境：本机 bim-dev venv（ezdxf），不进 Revit。
  python door_schedule.py <图纸.dxf> [--out 门窗表.json]
"""
import sys
import os
import re
import json

_KIND_MARK = ("门", "窗")
_SIZE_RE = re.compile(r"(\d{3,4})\s*[xX×\*]\s*(\d{3,4})")
_CODE_RE = re.compile(r"^[MT] ?[C]?\s*[-‐–—]?\s*\d", re.IGNORECASE)
_KW_TITLE = ("门窗表", "门表", "窗表")
_KW_HEADER = ("洞口尺寸", "门窗名称", "编号")


def _iter_texts(msp):
    for e in msp:
        t = e.dxftype()
        if t == "TEXT":
            yield e.dxf.insert, e.dxf.text
        elif t == "MTEXT":
            yield e.dxf.insert, e.plain_text() if hasattr(e, "plain_text") else e.text


def _cluster_rows(cells, tol):
    """cells: [(x, y, text)] -> [[(x, text)...]] 按 y 降序的行。"""
    if not cells:
        return []
    cells = sorted(cells, key=lambda c: -c[1])
    rows = []
    cur, cur_y = [], cells[0][1]
    for c in cells:
        if abs(c[1] - cur_y) <= tol:
            cur.append(c)
        else:
            rows.append(cur)
            cur, cur_y = [c], c[1]
    if cur:
        rows.append(cur)
    out = []
    for r in rows:
        r.sort(key=lambda c: c[0])
        out.append([(c[0], c[2]) for c in r])
    return out


def _row_text(row):
    return " ".join(t for _, t in row)


def parse_dxf(path, verbose=False):
    import ezdxf
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()

    cells = []
    heights = []
    for pos, text in _iter_texts(msp):
        s = (text or "").strip()
        if not s:
            continue
        cells.append((float(pos.x), float(pos.y), s))
        h = getattr(pos, "z", 0.0) or 0.0  # 不可靠，仅占位
    # 自适应行距：用相邻文字 y 差的中位数兜底 → 退化为固定 600mm
    ys = sorted(set(round(c[1]) for c in cells))
    diffs = [b - a for a, b in zip(ys, ys[1:]) if b - a > 50]
    tol = 300.0
    if diffs:
        diffs.sort()
        med = diffs[len(diffs) // 2]
        tol = max(120.0, min(900.0, med * 0.6))
    rows = _cluster_rows(cells, tol)

    # 1) 标题行
    title = ""
    for r in rows:
        txt = _row_text(r)
        for kw in _KW_TITLE:
            if kw in txt:
                title = txt.strip()
                break
        if title:
            break

    # 2) 逐行解析，"门"/"窗"行切换类别
    items = []
    kind = None
    for r in rows:
        txt = _row_text(r)
        stripped = [t for _, t in r]
        # 类别切换行：整行只有"门"或"窗"（或带数量类字样）
        if len(stripped) <= 3 and stripped and stripped[0] in _KIND_MARK:
            kind = u"door" if stripped[0] == u"门" else u"window"
            continue
        # 表头行跳过
        if any(kw in txt for kw in _KW_HEADER):
            continue
        # 数据行：必须含编号样式的单元格
        code_cell = None
        for _, t in r:
            if _CODE_RE.match(t) and len(t) <= 16:
                code_cell = t
                break
        if not code_cell:
            continue
        # 洞口尺寸
        size = None
        for _, t in r:
            m = _SIZE_RE.search(t)
            if m:
                size = (int(m.group(1)), int(m.group(2)))
                break
        # 数量列：纯数字(可带 x4) 或整数；合计取最后一个纯整数
        nums = [t for _, t in r if re.fullmatch(r"\d{1,4}([xX]\d{1,2})?", t)]
        total = nums[-1] if nums else ""
        # 图集引用：编号列右侧的短文本
        idx = [t for _, t in r].index(code_cell)
        tail = [t for _, t in r][idx + 1:]
        std = [t for t in tail if not _SIZE_RE.search(t)
               and not re.fullmatch(r"\d{1,4}([xX]\d{1,2})?", t)]
        # 类型名 = tail 里最后一个中文项（如"镶板门"）
        type_name = ""
        for t in reversed(tail):
            if re.search(u"[\u4e00-\u9fff]", t):
                type_name = t
                break
        if size is None:
            continue
        items.append({
            "kind": kind or "door",
            "code": code_cell,
            "width_mm": size[0],
            "height_mm": size[1],
            "type_name": type_name,
            "counts": nums[:-1] if len(nums) > 1 else [],
            "total": total,
            "std_ref": std[:4],
            "raw_row": [t for _, t in r],
        })
    if verbose:
        sys.stderr.write("[door_schedule] rows=%d items=%d title=%s\n"
                         % (len(rows), len(items), title))
    return {"title": title, "source": os.path.basename(path), "items": items}


def main():
    if len(sys.argv) < 2:
        print(u"用法: door_schedule.py <门窗表.dxf> [--out xx.json]")
        sys.exit(1)
    src = sys.argv[1]
    out = None
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]
    data = parse_dxf(src, verbose=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out:
        with open(out, "wb") as f:
            f.write(b"\xef\xbb\xbf")
            f.write(text.encode("utf-8"))
        print(u"已生成: %s (%d 项)" % (out, len(data["items"])))
    else:
        print(text)


if __name__ == "__main__":
    main()
