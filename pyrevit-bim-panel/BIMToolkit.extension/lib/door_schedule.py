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
# 编号首字母必须含 C：窗编号普遍是 C1/C0921，而旧正则只允许 M/T 开头
# （[C] 被放在 M/T 之后当可选位），于是 "C1" 根本匹配不上 ->
# 整张表的窗行一条都读不出来。真图实测：r6 门窗表 5 个门行全中、4 个窗行全丢。
_CODE_RE = re.compile(r"^[MTLC][\s\-‐–—]*[C]?[\s\-‐–—]*\d", re.IGNORECASE)
# 标题词收紧：旧值含 "门表"/"窗表"，会命中设计说明里的句子
# （r4 实测标题被认成 "400x400 3.门窗尺寸，划分和要求均详…"，然后从说明文字里
#  造出 26 条假数据）。只认完整的"门窗表"，且要求它像标题而不像句子。
_KW_TITLE = ("门窗表", "门窗明细表")
_TITLE_MAX_LEN = 16
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


def _kind_of(code, fallback=None):
    u"""门窗类别**以编号前字母为准**：M=门、C=窗（中文图通用约定）。

    旧实现只靠"门"/"窗"分隔行切换 kind，而 `_cluster_rows` 按 y 降序处理行。
    真图实测（r6 门窗表）：窗块在 y 上排在"窗"标记**之上**，于是所有 C 行
    处理完才翻到 window —— 13 条里 9 条被标成 door，包括 C1 3000x2700 这种
    一眼是窗的行。表格版式千变万化，编号前缀却是行内自带、不会错的证据。
    """
    c = (code or u"").strip().upper()
    if c.startswith(u"C"):
        return u"window"
    if c.startswith(u"M") or c.startswith(u"T"):
        return u"door"
    return fallback or u"door"


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

    # 1) 标题行：必须像标题（短），不能是说明句
    title = ""
    for r in rows:
        txt = _row_text(r).strip()
        for kw in _KW_TITLE:
            if kw in txt and len(txt) <= _TITLE_MAX_LEN:
                title = txt
                break
        if title:
            break

    # 2) 逐行解析，"门"/"窗"行切换类别
    items = []
    kind = None
    for r in rows:
        txt = _row_text(r)
        stripped = [t for _, t in r]
        # 类别切换行：短行里出现单独的"门"/"窗"即切换。
        # 旧写法要求 stripped[0] 就是它——真图表体常把"门"和序号/表头挤在同一行，
        # 于是 kind 永远停在默认 door，窗被当成门。
        if stripped and len(stripped) <= 4:
            _sw = None
            for _t0 in stripped:
                if _t0.strip() in _KIND_MARK:
                    _sw = u"door" if _t0.strip() == u"门" else u"window"
                    break
            if _sw:
                kind = _sw
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
        # 洞口尺寸：两种版式都要认
        #  (a) 一格内写 "1000x2100"
        #  (b) 宽、高是**两个独立单元格** —— 真图实测这才是主流。
        #      2026-10-11 拿 6 张真图跑这个解析器：5 张出 0 条，1 张出 8 条垃圾。
        #      查下来 r6 的门窗表结构完好（序号|编号|宽|高|数量|类型），
        #      编号列匹配得上，但旧代码只认 (a)，于是 `if size is None: continue`
        #      把每一行都跳过了 —— 整张表一条都读不出来。
        #      列位置实测：序号 x≈35902、编号 37393、宽 38692、高 40196、
        #      数量 42048、类型 43550。序号在编号**左侧**，所以只取编号右侧的数字
        #      就自动排除；宽高是 3~4 位且落在洞口尺寸合理区间，数量是 1~2 位整数。
        size = None
        dims = []
        counts = []
        code_x = None
        for x, t in r:
            if t == code_cell:
                code_x = x
                break
        for x, t in r:
            if code_x is not None and x <= code_x:
                continue
            m = _SIZE_RE.search(t)
            if m and size is None:
                size = (int(m.group(1)), int(m.group(2)))
                continue
            # re.fullmatch 是 Py3-only（IPY 2.7 运行时 AttributeError、编译期
            # 干净）——接进按钮前必须换掉。单元格文本无换行，match+$ 锚尾等价。
            ts = t.strip()
            if re.match(r"\d{3,4}$", ts):
                v = int(ts)
                if 200 <= v <= 9000:
                    dims.append(v)
            elif re.match(r"\d{1,4}([xX]\d{1,2})?$", ts):
                counts.append(ts)
        if size is None and len(dims) >= 2:
            size = (dims[0], dims[1])
            dims = dims[2:]
        # 数量列：编号右侧剩下的 1~2 位整数；合计取最后一个
        nums = counts
        total = nums[-1] if nums else ""
        # 图集引用：编号列右侧的短文本
        idx = [t for _, t in r].index(code_cell)
        tail = [t for _, t in r][idx + 1:]
        std = [t for t in tail if not _SIZE_RE.search(t)
               and not re.match(r"\d{1,4}([xX]\d{1,2})?$", t.strip())]
        # 类型名 = tail 里最后一个中文项（如"镶板门"）
        type_name = ""
        for t in reversed(tail):
            if re.search(u"[\u4e00-\u9fff]", t):
                type_name = t
                break
        if size is None:
            continue
        items.append({
            "kind": _kind_of(code_cell, kind),
            "code": code_cell,
            "width_mm": size[0],
            "height_mm": size[1],
            "type_name": type_name,
            "counts": nums[:-1] if len(nums) > 1 else [],
            "total": total,
            "std_ref": std[:4],
            "raw_row": [t for _, t in r],
        })
    # 没找到标题就**不出数据**。宁可空手，不可造假：
    # r4 实测没有门窗表、只有设计说明，旧代码从说明文字里造出 26 条自相矛盾的
    # 行（M0921 400x400、kind=door 而类型名"铝合金窗"、C5824 5800x2400）。
    if not title:
        items = []
    if verbose:
        sys.stderr.write("[door_schedule] rows=%d items=%d title=%s\n"
                         % (len(rows), len(items), title))
    return {"title": title, "source": os.path.basename(path), "items": items,
            "rejected_reason": (u"" if title else u"未找到门窗表标题")}


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
