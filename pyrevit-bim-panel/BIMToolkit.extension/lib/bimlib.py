# -*- coding: utf-8 -*-
u"""
bimlib.py —— BIMToolkit 扩展共用函数。

pyRevit 会把扩展根目录下的 lib 文件夹自动加入 sys.path，
所以各 pushbutton 的 script.py 里直接 `from bimlib import ...` 即可。

注意：本文件只在 Revit + pyRevit 宿主内真正运行；headless 下仅做语法/符号校验，
不会被执行。所有 Revit API 调用都包裹在 try/except，避免单点异常拖垮整条命令。
"""
try:
    from pyrevit import revit, DB, UI
except Exception:
    # headless 校验环境（py_compile / API 符号检查）下 pyrevit 不存在，置空即可；
    # 桥/嵌入式上下文（pyRevit 命令清理后 pyrevit 包不可导入）回退到直接引用
    # RevitAPI（model_builder 同款路径，桥内实测可用）。
    revit = None
    UI = None
    try:
        import clr
        clr.AddReference("RevitAPI")
        clr.AddReference("RevitAPIUI")
        from Autodesk.Revit import DB, UI
    except Exception:
        DB = None
        UI = None


def all_model_elements(doc):
    u"""全部非类型的模型/注释构件收集器（便于统一统计）。

    注意：WhereElementIsNotElementType 是 IronPython 扩展方法，在 pyRevit 的
    CPython 引擎下不一定可用；改用引擎无关的 ElementIsElementTypeFilter(True)。
    """
    return DB.FilteredElementCollector(doc).WherePasses(
        DB.ElementIsElementTypeFilter(True))


def element_level_name(el):
    u"""读取构件所属楼层名（无则返回 ''）。优先 SCHEDULE_LEVEL_PARAM。"""
    try:
        lp = el.get_Parameter(DB.BuiltInParameter.SCHEDULE_LEVEL_PARAM)
        if lp and lp.HasValue:
            lvl = el.Document.GetElement(lp.AsElementId())
            if lvl:
                return lvl.Name
    except Exception:
        pass
    return ""


def lookup_param(el, name):
    u"""按 用户参数名 / BuiltInParameter 枚举名 找到参数对象。"""
    p = el.LookupParameter(name)
    if p is not None:
        return p
    bip = getattr(DB.BuiltInParameter, name, None)
    if bip is not None:
        try:
            return el.get_Parameter(bip)
        except Exception:
            return None
    return None


def read_param(p):
    u"""通用读取参数当前值（按存储类型返回 Python 原生值）。"""
    if p is None:
        return None
    try:
        if p.StorageType == DB.StorageType.Double:
            return round(p.AsDouble(), 3)
        if p.StorageType == DB.StorageType.Integer:
            return p.AsInteger()
        if p.StorageType == DB.StorageType.ElementId:
            return p.AsElementId().IntegerValue
        return p.AsString()
    except Exception:
        return "?"


def set_param(el, name, value):
    u"""给构件写参数。返回 (old, new, ok, note)。按存储类型做类型转换。"""
    p = lookup_param(el, name)
    if p is None:
        return (None, None, False, u"无此参数: %s" % name)
    if p.IsReadOnly:
        return (None, None, False, u"参数只读: %s" % name)
    old = read_param(p)
    try:
        if p.StorageType == DB.StorageType.Double:
            p.Set(float(value))
        elif p.StorageType == DB.StorageType.Integer:
            p.Set(int(value))
        elif p.StorageType == DB.StorageType.ElementId:
            p.Set(DB.ElementId(int(value)))
        else:
            p.Set(str(value))
    except Exception as e:
        return (old, None, False, u"写入失败: %s" % e)
    return (old, read_param(p), True, "")


def family_type_name(el):
    u"""族:类型 组合名（用于按类型子串匹配）。"""
    try:
        ft = el.get_Parameter(DB.BuiltInParameter.SYMBOL_FAMILY_AND_TYPE_NAMES_PARAM)
        if ft:
            return ft.AsString() or ""
    except Exception:
        pass
    return ""


def type_name(et):
    u"""ElementType 派生类的安全名字读取（IPY2.7 的 .Name 属性是雷）。

    WallType / FamilySymbol / FloorType 等 ElementType 子类在 IronPython 2.7
    下访问 .Name 会抛 AttributeError: Name（new 隐藏属性绑定失败；
    Level.Name 正常）。回退读 ALL_MODEL_TYPE_NAME 内置参数
    （model_builder._type_name 同款，真机验证可用）。取不到返回 u""。
    """
    try:
        n = et.Name
        try:
            return unicode(n)
        except NameError:
            return n
    except Exception:
        pass
    try:
        p = et.get_Parameter(DB.BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p is not None:
            return p.AsString() or u""
    except Exception:
        pass
    return u""


def html_table(headers, rows):
    u"""生成简易 HTML 表格，供 output.print_html 使用。"""
    th = "".join("<th>%s</th>" % h for h in headers)
    body = ""
    for r in rows:
        tds = "".join("<td>%s</td>" % ("" if c is None else c) for c in r)
        body += "<tr>%s</tr>" % tds
    return ("<table border='1' cellspacing='0' cellpadding='4' "
            "style='border-collapse:collapse'>%s%s</table>" %
            ("<tr>%s</tr>" % th, body))


# --------------------------------------------------------------------------- #
# IronPython 2.7 / Python 2 兼容工具
# --------------------------------------------------------------------------- #
# pyRevit on Revit 2019 使用 IronPython 2.7，不支持 Python 3 的 open(encoding=...)。
# 下面工具集中处理：计数（.Count 属性在 IronPython 下不可用）、CSV 读写。
try:
    text_type = unicode  # IronPython / Python 2
except NameError:
    text_type = str      # Python 3（headless 校验不执行到此处）


def to_text(v):
    u"""把任意值安全转成 unicode 文本。

    IronPython 2.7 下这两个操作都会对非 unicode 的 str 按 **ascii** 隐式解码，
    一旦内容含中文就抛 UnicodeDecodeError：
        u"中文 %s" % str_value        （% 格式化）
        u"中文 " + str_value          （拼接）
        c in u"abc"  where c 是中文字符 （成员判断）
    本项目的模型名 / 类别名 / Revit 异常信息都可能是中文，凡是往 u"" 上拼或 %s
    填入的值，都必须先过 to_text()。
    """
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8")
        except Exception:
            try:
                return v.decode("mbcs")
            except Exception:
                try:
                    return v.decode("utf-8", "replace")
                except Exception:
                    return u"<无法解码的文本>"
    try:
        return unicode(v)
    except NameError:
        return str(v)
    except Exception:
        try:
            return unicode(v, "utf-8", "replace")
        except Exception:
            try:
                return str(v)
            except Exception:
                return u"<无法转换的文本>"


def to_element_list(collector):
    u"""把 FilteredElementCollector 转成 Python list（IronPython 兼容）。"""
    try:
        return list(collector.ToElements())
    except Exception:
        try:
            return list(collector)
        except Exception:
            return []


def count_collector(collector):
    u"""IronPython 兼容计数（.FilteredElementCollector.Count 属性不可用）。"""
    return len(to_element_list(collector))


def read_csv_unicode(path):
    u"""UTF-8 BOM CSV 读取，IronPython 2.7 与 CPython 3 行为一致。

    读二进制 -> 解码 -> io.StringIO 喂 csv，避免两个引擎下 csv 对二进制/
    文本文件语义不一致的问题。返回 dict 列表，key/value 均为 unicode/str。
    """
    import csv
    import io
    rows = []
    with open(path, 'rb') as f:
        text = f.read().decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    for r in reader:
        u = {}
        for k, v in r.items():
            uk = k.decode('utf-8-sig') if isinstance(k, bytes) else k
            if isinstance(v, bytes):
                uv = v.decode('utf-8-sig')
            else:
                uv = v
            u[uk] = uv
        rows.append(u)
    return rows


def write_csv_unicode(path, headers, rows):
    u"""UTF-8 BOM CSV 写入，IronPython 2.7 与 CPython 3 行为一致。

    rows: 二维列表；单元格支持 unicode/str/int/None。
    用 io.StringIO 攒文本（csv.writer 在 Py3 下只接受 str），最后整体编码为
    utf-8 字节 + BOM 落盘，规避 csv 在二进制/文本文件下的引擎差异。
    """
    import csv
    import io
    # Python 2 / IronPython 的 csv 模块只接受字节串（喂 unicode 会 ASCII 编码失败），
    # 而 Python 3 的 StringIO 只接受 str。故按引擎选择缓冲类型并按需编码单元格。
    is_py2 = (str is bytes)
    sio = io.BytesIO() if is_py2 else io.StringIO()
    w = csv.writer(sio)

    def _cell(c):
        if c is None:
            return ""
        s = text_type(c)
        if is_py2:
            s = s.encode('utf-8')  # Python 2：csv 需要字节
        return s

    w.writerow([_cell(h) for h in headers])
    for r in rows:
        w.writerow([_cell(c) for c in r])
    data = sio.getvalue()
    if not isinstance(data, bytes):
        data = data.encode('utf-8')
    with open(path, 'wb') as f:
        # UTF-8 BOM，确保 Excel 直接打开不乱码
        f.write(b'\xef\xbb\xbf')
        f.write(data)
