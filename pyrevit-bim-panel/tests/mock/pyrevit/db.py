# -*- coding: utf-8 -*-
"""Mock of pyrevit.DB (Revit API subset used by BIMToolkit).

Only models the surface BIMToolkit scripts touch, so the scripts can be
imported and executed without Revit/pyRevit installed. Kept Py2/Py3 compatible
(no f-strings) so it can also run under a real IronPython 2.7.
"""


class XYZ(object):
    """Revit Autodesk.Revit.DB.XYZ 的最小可用替身。

    刻意做成**真对象**而不是宽松桩：桩的 .X 返回又一个桩，`float()` 一抛
    element_factory 的 point_ok 就把所有段判成失败 —— 于是"翻模建墙"这条
    主链路在测试里其实一条都没跑到，测试却仍然绿。几何守卫要有意义，
    坐标类型必须先是真的。

    行为对齐真 XYZ：有 .X/.Y/.Z、有索引器、有 DistanceTo；**没有 len()、
    不能切片**（工厂的 coords_of 就是按这个约束写的）。
    """

    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.X = float(x)
        self.Y = float(y)
        self.Z = float(z)

    def __getitem__(self, i):
        return (self.X, self.Y, self.Z)[int(i)]

    def DistanceTo(self, other):
        dx = self.X - other.X
        dy = self.Y - other.Y
        dz = self.Z - other.Z
        return (dx * dx + dy * dy + dz * dz) ** 0.5

    def __repr__(self):
        return "XYZ(%g, %g, %g)" % (self.X, self.Y, self.Z)


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
    """Revit 语义（2026-10-07 真机量过，别再凭参数名推断）：构造参数是 **invert**。

      True  = 反转 = **只放行实例**   （等价 WhereElementIsNotElementType()）
      False = 不反转 = 只放行族类型   （等价 WhereElementIsElementType()）

    实测：OfCategory(OST_Walls) 全量 53 -> Filter(True) 18（类名 Wall）、
    Filter(False) 35（类名 WallType）、WhereElementIsNotElementType() 18。
    """

    def __init__(self, invert):
        self.invert = bool(invert)


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

    def __init__(self, name="Elem", category="通用类别", mark="", is_type=False):
        self.Id = ElementId(FakeElement._counter)
        FakeElement._counter += 1
        self.Name = name
        self.Category = FakeCategory(category)
        self._mark = mark
        # 与真实 Revit 对齐：族类型（WallType/FloorType/FamilySymbol）也是元素，
        # 但统计"模型构件"时必须排除它们。
        self.is_type = is_type

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
    # 类别轮着给墙/门/窗/柱：bimconv_build 的 door_syms / window_syms /
    # _column_pool 三个池都按 Category.Name 筛，以前 FakeSymbol 没有 Category
    # 属性（访问即 AttributeError，被调用方 except 吞掉），于是三个池恒为空，
    # "项目中没有窗族"这条分支在测试里永远先命中，门窗寄宿/开洞那段
    # 根本没有测试能执行到。补上类别才谈得上测它。
    _CATS = (u"墙", u"门", u"窗", u"柱")

    def __init__(self, i, n_params):
        self.Id = ElementId(FakeSymbol._counter)
        FakeSymbol._counter += 1
        self.Name = u"类型%d" % i
        self.Parameters = [FakeParam("Double", 1.0) for _ in range(n_params)]
        self.Category = FakeCategory(self._CATS[i % len(self._CATS)])
        # FamilySymbol 在 Revit 里就是 ElementType
        self.is_type = True


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
    # 无 OfClass 的裸收集器 = 整个文档。真实文档里族类型也是元素，
    # 必须混进池子，否则"实例 vs 类型"的过滤器方向写反时测试看不出来
    # （bimlib.all_model_elements 就是这么把 7 个按钮的统计口径整体搞反的）。
    if cls is None:
        pool = [FakeElement(name=u"构件%d" % i, category=u"墙")
                for i in range(10)]
        pool += [FakeElement(name=u"墙类型%d" % i, category=u"墙", is_type=True)
                 for i in range(3)]
        return pool
    # 默认：通用模型构件
    return [FakeElement(name=u"构件%d" % i, category=u"墙") for i in range(10)]



class _CatKey(object):
    u"""BuiltInCategory 成员的模拟：真 Revit 是个枚举，这里只需要它能带出
    对应的中文类别名，供 FilteredElementCollector.OfCategory 过滤。"""
    def __init__(self, name, cats):
        self.name = name
        self.cats = cats

    def __repr__(self):
        return u"BuiltInCategory.%s" % self.name


class BuiltInCategory(object):
    u"""⚠️ 这个类一旦定义，就**遮蔽**了测试台对 Autodesk.Revit.DB.BuiltInCategory
    的宽松自动补齐——于是代码里用到的每个 OST_ 都必须在这里列出来，
    漏一个就是 AttributeError。（第一次改完就把「工程量统计」打挂了，
    它用 OST_Roofs。）改这里的同步判据：跑 harness，它会把缺的报出来。

    cats 用中文类别名，因为按钮代码全按 `Category.Name` 比对；
    Revit 中文版返回中文类别名，这也是真机上 OfCategory 与按名筛能对上的一致性来源。
    """
    OST_Walls = _CatKey("OST_Walls", (u"墙",))
    OST_Roofs = _CatKey("OST_Roofs", (u"屋顶",))
    OST_StructuralFraming = _CatKey("OST_StructuralFraming", (u"结构框架",))
    OST_Floors = _CatKey("OST_Floors", (u"楼板",))
    OST_Doors = _CatKey("OST_Doors", (u"门",))
    OST_Windows = _CatKey("OST_Windows", (u"窗",))
    OST_Columns = _CatKey("OST_Columns", (u"柱",))
    OST_StructuralColumns = _CatKey("OST_StructuralColumns", (u"结构柱",))
    OST_Rooms = _CatKey("OST_Rooms", (u"房间",))
    OST_Grids = _CatKey("OST_Grids", (u"轴网",))
    OST_Stairs = _CatKey("OST_Stairs", (u"楼梯",))


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
        self._cats = None
        self._skip_types = False
        self._only_types = False

    def OfClass(self, cls):
        self._cls = cls
        return self

    def OfCategory(self, bic):
        u"""按类别筛。以前 mock 没这个方法，调用方 `.OfCategory(...)` 直接
        AttributeError 被 except 吞掉 -> 池子恒空 -> 门窗柱三条路径在测试里
        永远走不到"有没有族"这一步之后。"""
        self._cats = tuple(getattr(bic, "cats", ()) or ())
        return self

    def OfCategoryId(self, eid):
        return self

    def _apply_type_filter(self, only_types=None, skip_types=None):
        if only_types is not None:
            self._only_types = only_types
        if skip_types is not None:
            self._skip_types = skip_types
        return self

    def WherePasses(self, filt):
        """按过滤器语义真筛，不再原样返回 self。

        此前它是 no-op —— 于是"19 个脚本全部执行成功"和"统计口径整体反了"
        可以同时为真，测试等于没有。
        """
        name = type(filt).__name__
        if name == "ElementIsElementTypeFilter":
            # invert=True -> 只要实例；invert=False -> 只要族类型
            if filt.invert:
                return self._apply_type_filter(skip_types=True)
            return self._apply_type_filter(only_types=True)
        if name == "ElementLevelFilter":
            # mock 未建模"构件 -> 楼层"的归属关系（FakeElement 没有 level_id），
            # 因此这里刻意放行，避免把与本次断言无关的按钮变成假失败。
            lvl = getattr(filt, "level_id", None)
            pool = make_elements(self._cls, self._doc)
            if any(hasattr(e, "level_id") for e in pool):
                self._level_id = lvl
            return self
        return self

    # `WhereElementIsNotElementType()` 是**真实存在**的 Revit 2019 API
    # （2026-10-01 用 pythonnet 对真实 RevitAPI.dll 核实：hasattr=True，
    #  与 WhereElementIsElementType / WhereElementIsCurveDriven 同族）。
    # 施工方案 / 规范审查 两个按钮用它，此前 mock 缺这个方法 -> 假失败。
    def WhereElementIsNotElementType(self):
        return self._apply_type_filter(skip_types=True)

    def WhereElementIsElementType(self):
        return self._apply_type_filter(only_types=True)

    def ToElements(self):
        els = make_elements(self._cls, self._doc)
        if self._cats:
            els = [e for e in els
                   if getattr(getattr(e, "Category", None), "Name", None)
                   in self._cats]
        if self._only_types or self._skip_types:
            els = [e for e in els
                   if bool(getattr(e, "is_type", False)) == self._only_types]
        lvl = getattr(self, "_level_id", None)
        if lvl is not None:
            els = [e for e in els if getattr(e, "level_id", None) == lvl]
        return els

    # 故意不定义 .Count，也不定义 __iter__
