# -*- coding: utf-8 -*-
"""Mock of pyrevit.DB (Revit API subset used by BIMToolkit).

Only models the surface BIMToolkit scripts touch, so the scripts can be
imported and executed without Revit/pyRevit installed. Kept Py2/Py3 compatible
(no f-strings) so it can also run under a real IronPython 2.7.
"""


class ElementId(object):
    def __init__(self, value):
        self._v = int(value)

    @property
    def IntegerValue(self):
        return self._v

    def __eq__(self, other):
        return isinstance(other, ElementId) and other._v == self._v

    def __hash__(self):
        return hash(self._v)

    def __repr__(self):
        return "ElementId(%d)" % self._v


class StorageType(object):
    Double = "Double"
    Integer = "Integer"
    ElementId = "ElementId"
    String = "String"


class BuiltInParameter(object):
    SCHEDULE_LEVEL_PARAM = "SCHEDULE_LEVEL_PARAM"
    SYMBOL_FAMILY_AND_TYPE_NAMES_PARAM = "SYMBOL_FAMILY_AND_TYPE_NAMES_PARAM"
    ALL_MODEL_MARK = "ALL_MODEL_MARK"
    FAMILY_SHARED = "FAMILY_SHARED"

    def __getattr__(self, name):
        # 任意未列出的 BuiltInParameter 名 -> 返回该名字（供 lookup_param 用）
        return name


class FailureSeverity(object):
    Error = "Error"
    Warning = "Warning"


# 仅作为 OfClass 参数的占位类
class Level(object):
    pass


class View(object):
    pass


class ViewSheet(object):
    pass


class Family(object):
    pass


class FamilySymbol(object):
    pass


class ElementIsElementTypeFilter(object):
    def __init__(self, invert):
        self.invert = invert


class ElementLevelFilter(object):
    def __init__(self, level_id):
        self.level_id = level_id


class FakeParam(object):
    def __init__(self, storage, value, readonly=False, has_value=True):
        self._storage = storage
        self._value = value
        self.IsReadOnly = readonly
        self.HasValue = has_value

    def AsDouble(self):
        return self._value

    def AsInteger(self):
        return self._value

    def AsElementId(self):
        return self._value

    def AsString(self):
        return self._value


class FakeCategory(object):
    def __init__(self, name):
        self.Name = name


class FakeElement(object):
    _counter = 1

    def __init__(self, name="Elem", category="通用类别", mark=""):
        self.Id = ElementId(FakeElement._counter)
        FakeElement._counter += 1
        self.Name = name
        self.Category = FakeCategory(category)
        self._mark = mark

    def get_Parameter(self, bip):
        name = bip if isinstance(bip, str) else getattr(bip, "__name__", str(bip))
        if name == "ALL_MODEL_MARK":
            return FakeParam("String", self._mark)
        if name == "SYMBOL_FAMILY_AND_TYPE_NAMES_PARAM":
            return FakeParam("String", u"族A:类型1")
        return None

    def LookupParameter(self, name):
        # 真实 Revit 元素有 LookupParameter；这里委托给 get_Parameter
        return self.get_Parameter(name)


class FakeLevel(FakeElement):
    def __init__(self, i):
        super(FakeLevel, self).__init__(name=u"楼层%d" % i, category=u"楼层")
        self.Elevation = float(i * 3.0)  # 楼层高程（脚本按 Elevation 排序）


class FakeFamily(object):
    _counter = 1

    def __init__(self, i, n_symbols, shared=0, editable=True, user=True):
        self.Id = ElementId(FakeFamily._counter)
        FakeFamily._counter += 1
        self.Name = u"族%d" % i
        self._n_symbols = n_symbols
        self.IsUserCreated = user
        self.IsEditable = editable
        self._shared = shared

    def GetFamilySymbolIds(self):
        return [ElementId(9000 + j) for j in range(self._n_symbols)]

    def get_Parameter(self, bip):
        name = bip if isinstance(bip, str) else str(bip)
        if name == "FAMILY_SHARED":
            return FakeParam("Integer", self._shared)
        return None


class FakeSymbol(object):
    _counter = 1

    def __init__(self, i, n_params):
        self.Id = ElementId(FakeSymbol._counter)
        FakeSymbol._counter += 1
        self.Name = u"类型%d" % i
        self.Parameters = [FakeParam("Double", 1.0) for _ in range(n_params)]


class FakeFailureMessage(object):
    def __init__(self, desc, severity, eids):
        self._desc = desc
        self._sev = severity
        self._eids = eids

    def GetDescriptionText(self):
        return self._desc

    def GetSeverity(self):
        return self._sev

    def GetFailingElements(self):
        return self._eids


# 文档里"已有墙"的数量。默认 0 = 干净文档。
#
# 为什么要这个开关：DWG翻模 有一道硬闸门 —— 模型里已有墙就拒绝写入
# （script.py:359-378，三崩实录换来的：残墙叠新墙 -> 墙重叠风暴 -> 再崩）。
# 门禁测试要验证"确认后写入"，就必须在**干净文档**下跑；
# 而"脏文档必须被拦住"本身就是一条安全断言，值得单独测（见 harness 用例 5）。
# 2026-10-01：此前 DB.Wall 在 mock 里不存在，AttributeError 被按钮的
# except 吞掉，闸门等于没被测到 —— 属于"假通过"。
WALL_COUNT = 0


def make_elements(cls, doc):
    if cls is Level:
        return [FakeLevel(i) for i in range(3)]
    if cls is View:
        return [FakeElement(name=u"视图%d" % i, category=u"视图") for i in range(2)]
    if cls is ViewSheet:
        return [FakeElement(name=u"图纸%d" % i, category=u"图纸") for i in range(2)]
    if cls is Family:
        return [
            FakeFamily(1, 3, shared=1, editable=True, user=True),
            FakeFamily(2, 2, shared=0, editable=True, user=True),
            FakeFamily(3, 0, shared=0, editable=False, user=False),
            FakeFamily(4, 1, shared=0, editable=True, user=True),
        ]
    if cls is FamilySymbol:
        return [FakeSymbol(i, (i % 3) + 1) for i in range(6)]
    # Wall 在 pyrevit.db 里没有真实类（由测试台宽松补齐），只能按类名识别。
    if getattr(cls, "__name__", u"") == u"Wall":
        return [FakeElement(name=u"墙%d" % i, category=u"墙")
                for i in range(WALL_COUNT)]
    # 默认：通用模型构件
    return [FakeElement(name=u"构件%d" % i, category=u"墙") for i in range(10)]


class FilteredElementCollector(object):
    """模拟 Revit FilteredElementCollector。

    复刻 IronPython 2.7 + Revit 的两个坑：
      - 没有 .Count 属性（IronPython 下访问会 AttributeError）
      - 不能直接 list()（IronPython 下 FilteredElementCollector 不可迭代 -> TypeError）
    必须走 .ToElements()。
    """

    def __init__(self, doc):
        self._doc = doc
        self._cls = None

    def OfClass(self, cls):
        self._cls = cls
        return self

    def WherePasses(self, filt):
        return self

    # `WhereElementIsNotElementType()` 是**真实存在**的 Revit 2019 API
    # （2026-10-01 用 pythonnet 对真实 RevitAPI.dll 核实：hasattr=True，
    #  与 WhereElementIsElementType / WhereElementIsCurveDriven 同族）。
    # 施工方案 / 规范审查 两个按钮用它，此前 mock 缺这个方法 -> 假失败。
    def WhereElementIsNotElementType(self):
        return self

    def WhereElementIsElementType(self):
        return self

    def ToElements(self):
        return make_elements(self._cls, self._doc)

    # 故意不定义 .Count，也不定义 __iter__
