# -*- coding: utf-8 -*-
"""IronPython 2.7 兼容测试台（第 4 关）。

逐个把全部按钮脚本的源码 exec 出来（带 mock pyrevit），捕获任何
IronPython 2.7 运行期异常；并做逻辑校验（health_score / CSV 往返 / plan_from_json 往返）。

运行：python tests/ironpython_harness.py
（也可由 selftest.py 以子进程方式调用，作为第 4 关）

V2.4 起的两类新桩（见 _install_native_stubs）：
  - 假 Autodesk.Revit.UI：批量改参/构件清单导出/进度关联 顶层 import TaskDialog，
    旧 harness 没有 Autodesk 桩 -> ModuleNotFoundError（历史遗留 3 个 FAIL）；
  - 假 System.Windows.Forms：DWG翻模 V2.4 / 构件清单导出 改用原生 WinForms
    （OpenFileDialog/SaveFileDialog/MessageBox），CPython harness 下真 WinForms
    会真的弹窗挂起（进程被外部 SIGTERM）。
"""
import os
import sys
import shutil
import tempfile

# 控制台可能是 GBK。本文件用 sys.stderr.write 输出中文，若某些字符装不下，
# 会以 UnicodeEncodeError 中断整轮 harness —— 那是打印问题，不是测试结果。
try:
    sys.stderr.reconfigure(errors="replace")
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# 1) 应用 IronPython 2.7 兼容模拟器（必须在 exec 脚本前）
sys.path.insert(0, HERE)
import ironpython_compat  # noqa: F401  (monkeypatch open())

# 2) 注入 mock pyrevit（让 `import pyrevit` 解析到假包）
sys.path.insert(0, os.path.join(HERE, "mock"))

# 3) 真实 lib 加入 path（脚本自己也会加，这里保证 bimlib/bimhealth 可 import）
LIB = os.path.abspath(os.path.join(HERE, "..", "BIMToolkit.extension", "lib"))
sys.path.insert(0, LIB)

# 3b) 测试台绝不发真实网络请求。
#     「AI 指令」在模块级就会走 main() -> llm_client.chat()。若环境变量里有
#     DEEPSEEK_API_KEY，harness 会真的打 DeepSeek：花额度、且在 CI 里可能挂住等网络。
#     这里先摘掉，让它按「未配置密钥」抛错，由按钮自己的 except 捕获 ——
#     这也是更严格、更确定的断言（测试结果不依赖外部服务是否可达）。
os.environ.pop("DEEPSEEK_API_KEY", None)

# 4) 假 Autodesk.* / System.Windows.Forms 桩（在下方定义后立即安装）

# 被自动补齐的桩符号。宽松桩是必要的（Windows/.NET 是宿主管道，不是本次被测对象），
# 但"要什么给什么"如果不记账，就会退化成"mock 太宽 -> 静默放过"。
# 因此每个自动补齐的符号都登记在这里，测试结束统一打印，供人工对照真实 API。
AUTO_STUBBED = set()


def _stub_for(name):
    """共用的桩工厂（注意：模块级 __getattr__ 是 1 参，实例/类级是 2 参，故分开包装）。"""
    if name.startswith("__"):
        raise AttributeError(name)
    return _Stub()


def _stub_getattr(self, name):
    return _stub_for(name)


def _stub_init(self, *a, **k):
    # object.__init__ 在多参数时会抛 TypeError，必须自己吞掉
    pass


def _stub_call(self, *a, **k):
    return _Stub()


class _StubMeta(type):
    """让**类级**属性访问也返回桩。

    必需：`BuiltInCategory.OST_Doors`、`FormStartPosition.CenterScreen`
    这类枚举成员是在**类**上取属性，只给实例装 __getattr__ 会 AttributeError。
    """

    def __getattr__(cls, name):
        return _stub_for(name)


# 用"显式元类构造"而不是 class 语句：Py2/Py3 都能用同一种写法。
_Stub = _StubMeta(str("_Stub"), (object,), {
    "__doc__": u"万能桩：任何属性 -> 新桩；调用 -> 新桩；下标 -> 新桩；布尔为 False。",
    "__getattr__": _stub_getattr,
    "__init__": _stub_init,
    "__len__": lambda self: 0,
    "__call__": _stub_call,
    "__getitem__": lambda self, i: _Stub(),
    "__setitem__": lambda self, i, v: None,
    "__iter__": lambda self: iter(()),
    "__bool__": lambda self: False,
    "__nonzero__": lambda self: False,
    "__str__": lambda self: u"",
    "__repr__": lambda self: u"<stub>",
})


def _fill(m, name):
    """给（已存在的）模块装上"要什么给什么"，并登记补齐的符号。"""

    def _getattr(attr):
        if attr.startswith("__"):
            raise AttributeError(attr)
        AUTO_STUBBED.add(name + "." + attr)
        cls = _StubMeta(str(attr), (_Stub,), {})
        setattr(m, attr, cls)
        return cls

    m.__getattr__ = _getattr
    return m


# pyRevit 在按钮作用域里注入 __revit__（UIApplication）。
# 12 个新增按钮在**模块级**就写 `uidoc = __revit__.ActiveUIDocument`，
# 不注入的话全部 NameError —— 那是假失败，不是真缺陷。
REVIT_APP = _Stub()


def _exec_globals(path):
    return {"__name__": "__main__", "__file__": path, "__revit__": REVIT_APP}


def _permissive(name, extra=None):
    """建一个"要什么给什么"的桩模块。"""
    import types
    m = types.ModuleType(name)
    for k, v in (extra or {}).items():
        setattr(m, k, v)
    return _fill(m, name)


def _install_native_stubs():
    """注入假 Autodesk.* 与 System.Windows.Forms 模块，headless 化按钮的原生对话框。

    返回 (SWF_CTRL, DialogResult, MessageBoxButtons)：
      SWF_CTRL["open_file"]   -> OpenFileDialog/SaveFileDialog 返回的文件（None=取消）
      SWF_CTRL["msg_answer"]  -> MessageBox.Show 对 YesNoCancel 弹窗的答复（None=一律取消）
    供写入门禁测试（_test_dwg_mode_gate）编程驱动。
    """
    import types

    # ---- Autodesk 桩 ----
    # 说明：Autodesk.Revit.DB 用宽松补齐，是因为本测试台只负责
    #   「脚本在 IronPython 2.7 下能不能跑起来」，
    #   「用到的 Revit API 符号在 2019 里是否真的存在」由 selftest.py 第 2 关
    #   （pythonnet 加载真实 RevitAPI.dll）负责。两者分工，不重复也不互相顶替。
    autodesk = _permissive("Autodesk")
    revit_m = _permissive("Autodesk.Revit")
    ui_m = _permissive("Autodesk.Revit.UI")
    db_m = _permissive("Autodesk.Revit.DB")
    struct_m = _permissive("Autodesk.Revit.DB.Structure")
    events_m = _permissive("Autodesk.Revit.DB.Events")

    class StructuralType(object):
        NonStructural = 0
        Beam = 1
        Column = 2
        Footing = 3
        Brace = 4

    struct_m.StructuralType = StructuralType

    class TaskDialogCommonButtons(object):
        Ok = 1
        Cancel = 2
        Yes = 4
        No = 8
        Close = 16
        Retry = 32

    class TaskDialogResult(object):
        Ok = 1
        Cancel = 2
        Yes = 4
        No = 8
        Retry = 16
        Close = 32
        None_ = 0

    class PostableCommand(object):
        def __init__(self, *a, **k):
            pass

    class TaskDialog(object):
        def __init__(self, title=None):
            self.Title = title
            self.MainInstruction = None
            self.MainContent = None
            self.CommonButtons = None

        def Show(self):
            # headless 默认一律「取消」：绝不静默写模型。
            # 测试可用 SWF_CTRL["taskdlg_answer"] 编程驱动，
            # 从而真正走通「用户点确定 -> 写入」那条分支。
            ans = SWF_CTRL.get("taskdlg_answer")
            if ans is not None:
                return ans
            return TaskDialogResult.Cancel

    ui_m.TaskDialog = TaskDialog
    ui_m.TaskDialogCommonButtons = TaskDialogCommonButtons
    ui_m.TaskDialogResult = TaskDialogResult
    ui_m.PostableCommand = PostableCommand
    autodesk.Revit = revit_m
    revit_m.UI = ui_m
    revit_m.DB = db_m
    db_m.Structure = struct_m
    db_m.Events = events_m
    for name, mod in (("Autodesk", autodesk), ("Autodesk.Revit", revit_m),
                      ("Autodesk.Revit.UI", ui_m), ("Autodesk.Revit.DB", db_m),
                      ("Autodesk.Revit.DB.Structure", struct_m),
                      ("Autodesk.Revit.DB.Events", events_m)):
        sys.modules[name] = mod

    # ---- System.Windows.Forms 桩 ----
    sys_m = types.ModuleType("System")
    win_m = types.ModuleType("System.Windows")
    forms_m = types.ModuleType("System.Windows.Forms")

    class DialogResult(object):
        OK = "OK"
        Yes = "Yes"
        No = "No"
        Cancel = "Cancel"

    class MessageBoxButtons(object):
        OK = "OK"
        YesNo = "YesNo"
        YesNoCancel = "YesNoCancel"

    class MessageBox(object):
        @staticmethod
        def Show(msg, title=None, buttons=None):
            # 之前只在 YesNoCancel 时读 msg_answer，导致用 YesNo 确认的按钮
            # （如「翻模体检」的删除确认）永远走不进去。现在任何带按钮的弹窗
            # 都可由 SWF_CTRL["msg_answer"] 驱动。
            ans = SWF_CTRL.get("msg_answer")
            if ans is not None and buttons is not None:
                return ans
            return DialogResult.Cancel

    class _FileDialogBase(object):
        def __init__(self):
            self.Title = None
            self.Filter = None
            self.FileName = None
            # 真 WinForms 上 Multiselect=True 后 FileNames 才是全部选中项；
            # 桩必须一起提供，否则按钮改成多选后测试台只会返回空列表，
            # "选了一套图"这条主路径又变成测不到的死区。
            self.FileNames = ()
            self.Multiselect = False

        def ShowDialog(self):
            path = SWF_CTRL.get("open_file")
            if path:
                names = list(path) if isinstance(path, (list, tuple)) else [path]
                self.FileNames = tuple(names)
                self.FileName = names[0]
                return DialogResult.OK
            return DialogResult.Cancel

    class OpenFileDialog(_FileDialogBase):
        pass

    class SaveFileDialog(_FileDialogBase):
        pass

    forms_m.MessageBox = MessageBox
    forms_m.MessageBoxButtons = MessageBoxButtons
    forms_m.DialogResult = DialogResult
    forms_m.OpenFileDialog = OpenFileDialog
    forms_m.SaveFileDialog = SaveFileDialog
    # 其余 WinForms 表面（Form/TextBox/Button/Label/ListBox/Clipboard/Application…）
    # 由宽松补齐负责，AI 指令 等按钮会用到它们。
    _fill(forms_m, "System.Windows.Forms")

    sys_m.Windows = win_m
    win_m.Forms = forms_m
    # bridge_core 会 `from System import EventHandler`；
    # 只给 forms 装宽松补齐不够，System / System.Windows 也要装。
    _fill(win_m, "System.Windows")
    _fill(sys_m, "System")

    # ---- System.Drawing / Collections / Runtime.CompilerServices ----
    draw_m = _permissive("System.Drawing")                 # Point/Size/Color
    coll_m = _permissive("System.Collections")
    gen_m = _permissive("System.Collections.Generic")      # List[T]
    rt_m = _permissive("System.Runtime")
    rcs_m = _permissive("System.Runtime.CompilerServices")  # StrongBox
    coll_m.Generic = gen_m
    rt_m.CompilerServices = rcs_m
    sys_m.Drawing = draw_m
    sys_m.Collections = coll_m
    sys_m.Runtime = rt_m

    # ---- clr（AddReference 等）----
    clr_m = types.ModuleType("clr")
    clr_m.AddReference = lambda *a, **k: None
    clr_m.GetClrType = lambda *a, **k: None

    for name, mod in (("clr", clr_m),
                      ("System", sys_m), ("System.Windows", win_m),
                      ("System.Windows.Forms", forms_m),
                      ("System.Drawing", draw_m),
                      ("System.Collections", coll_m),
                      ("System.Collections.Generic", gen_m),
                      ("System.Runtime", rt_m),
                      ("System.Runtime.CompilerServices", rcs_m)):
        sys.modules[name] = mod

    return ({"open_file": None, "msg_answer": None, "taskdlg_answer": None},
            DialogResult, MessageBoxButtons)


# 安装原生对话框桩（必须在 exec 任何按钮脚本之前）
SWF_CTRL, DLG_RESULT, DLG_BUTTONS = _install_native_stubs()

# 5) 放宽 mock pyrevit 的表面。
#    tests/mock/pyrevit 当初只为旧的 7 个按钮写；AI 智建 面板等 12 个新按钮
#    用到的 revit.DB 符号（BuiltInCategory / Wall / Category / Grid …）它没有。
#    统一放宽，并把补齐的符号记进 AUTO_STUBBED，供人工对照。
import pyrevit  # noqa: E402

for _mod_name, _mod in (("pyrevit", pyrevit),
                        ("pyrevit.db", pyrevit.DB),
                        ("pyrevit.revit", pyrevit.revit),
                        ("pyrevit.ui", pyrevit.UI),
                        ("pyrevit.forms", pyrevit.forms),
                        ("pyrevit.script", pyrevit.script)):
    _fill(_mod, _mod_name)


EXT_ROOT = os.path.abspath(os.path.join(HERE, "..", "BIMToolkit.extension"))
TAB_ROOT = os.path.join(EXT_ROOT, "BIMToolkit.tab")
PANEL = os.path.join(TAB_ROOT, "BIM 工具.panel")


def _discover_button_dirs():
    """自动发现 `BIMToolkit.tab` 下所有带 script.py 的 .pushbutton（相对 TAB_ROOT）。

    2026-10-01：此前是手写 7 个，实际有 19 个 —— 补齐了 12 个从未被实跑过的按钮。
    改成自动发现后，新增按钮不需要再改测试，
    从根上消除"加了按钮但测试没跟上"的静默漏测。
    """
    found = []
    for root, dirs, _files in os.walk(TAB_ROOT):
        for d in dirs:
            if not d.endswith(".pushbutton"):
                continue
            full = os.path.join(root, d)
            if os.path.isfile(os.path.join(full, "script.py")):
                found.append(os.path.relpath(full, TAB_ROOT))
    return sorted(found)


BUTTON_DIRS = _discover_button_dirs()
SCRIPTS = [os.path.join(b, "script.py") for b in BUTTON_DIRS]

# ---------------------------------------------------------------------------
# 已登记的**真实缺陷**（不是"测试太严"，是按钮真的坏了）
#
# 这些条目不会让本轮"变绿"：harness 仍会打印它们、在统计里计数，
# 默认模式下不算回归，--strict 下一样判 FAIL。
# 这样既能让套件作为回归门禁使用，又不会把真实缺陷混进"测试环境问题"里被忽略。
#
# 每条必须写清 原因 + 发现日期。**修好后请删除对应条目**，留着会掩盖回归。
#
# 2026-10-01：目前为空。
#   曾有一条 `写入诊断.pushbutton`（引用了 bimconv_build 的 6 个不存在私有符号，
#   点击即 AttributeError）。经确认该按钮是一次性诊断工具、埋点已随库重构消失，
#   已按产品决策**从扩展中删除**，故条目一并移除。
#   源码可从 git 恢复：
#     git show df81a3e:"pyrevit-bim-panel/BIMToolkit.extension/BIMToolkit.tab/BIM 工具.panel/写入诊断.pushbutton/script.py"
# ---------------------------------------------------------------------------
KNOWN_BROKEN = {}


def _button_name(script_rel):
    """从 `面板\\按钮.pushbutton\\script.py` 取出 `按钮.pushbutton`。"""
    return os.path.basename(os.path.dirname(script_rel))


def _stage():
    """把每个按钮目录整体复制到临时目录（保持源码干净，并带上 bundle 的 CSV/图标）。"""
    tmp = tempfile.mkdtemp(prefix="bimtoolkit_test_")
    for b in BUTTON_DIRS:
        src = os.path.join(TAB_ROOT, b)
        if os.path.isdir(src):
            dst = os.path.join(tmp, b)
            if not os.path.isdir(os.path.dirname(dst)):
                os.makedirs(os.path.dirname(dst))
            shutil.copytree(src, dst)
    return tmp


def run_one(tmp, rel):
    path = os.path.join(tmp, os.path.dirname(rel), "script.py")
    with open(path, "rb") as f:
        src = f.read().decode("utf-8-sig")
    exec(compile(src, path, "exec"), _exec_globals(path))


def _test_dwg_mode_gate(tmp):
    """回归：DWG翻模 的「写入模式闸门」在 取消/常规/安全 三种答复下都对。

    事故背景一：曾用 forms.CommandSwitchWindow(title=, message=, options=)，
    在 Revit 2019 的 legacy IronPython loader 下抛 TypeError（WPF 窗口 + 错误的
    关键字参数）。
    事故背景二：V2.4 起按钮改用原生 WinForms（OpenFileDialog + MessageBox），
    旧版基于 mock forms.alert/pick_file 的门禁测试随之失效（且真 WinForms 在
    harness 里会挂起）。
    现通过 _install_native_stubs 的 SWF_CTRL 编程驱动两种弹窗：
      open_file=None            -> 选图取消，绝不写入
      msg_answer=Cancel         -> 选了图但在模式确认取消，不写入
      msg_answer=Yes            -> 安全模式（v2.5 起安全为首选，safe_mode=True）
      msg_answer=No             -> 常规模式（safe_mode=False）
    """
    import json
    import bimconvert

    plan = {
        "source": "gate_test.dwg", "format": "dwg",
        "layers": ["WALL"], "blocks": [],
        "entities": [
            {"type": "line", "layer": "WALL", "handle": "1",
             "start": [0, 0, 0], "end": [6000, 0, 0]},
        ],
    }
    json_path = os.path.join(tmp, "gate_model_plan.json")
    with open(json_path, "wb") as f:
        f.write(json.dumps(plan).encode("utf-8"))

    # 自动定位 DWG翻模 按钮（面板层级随自动发现的相对路径走，不再写死平铺路径）
    dwg_rel = [b for b in BUTTON_DIRS if b.endswith(u"DWG翻模.pushbutton")]
    if not dwg_rel:
        sys.stderr.write("SKIP  logic: 未发现 DWG翻模 按钮\n")
        return
    script_path = os.path.join(tmp, dwg_rel[0], "script.py")
    with open(script_path, "rb") as f:
        src = f.read().decode("utf-8-sig")

    calls = []
    real_create = bimconvert.create_elements

    def fake_create(doc, planned, out=None, **kw):
        calls.append(kw)
        return [(True, u"ok", None)] * len(planned)

    try:
        bimconvert.create_elements = fake_create
        # (open_file, msg_answer, 是否应写入, safe_mode 期望值)
        # v2.5 起 Yes=安全模式(safe=True)、No=常规模式(safe=False)
        cases = [
            (None, None, False, None),        # ① 选图取消
            (json_path, DLG_RESULT.Cancel, False, None),  # ② 模式确认取消
            (json_path, DLG_RESULT.Yes, True, True),      # ③ 安全模式
            (json_path, DLG_RESULT.No, True, False),      # ④ 常规模式
        ]
        for idx, (open_file, answer, should_write, safe_expect) in enumerate(cases):
            SWF_CTRL["open_file"] = open_file
            SWF_CTRL["msg_answer"] = answer
            del calls[:]
            exec(compile(src, script_path, "exec"), _exec_globals(script_path))
            if not should_write:
                assert calls == [], (
                    u"用例%d：取消时不应写入，却调用了 create_elements %s"
                    % (idx, calls))
            else:
                assert len(calls) == 1, (
                    u"用例%d：应写入 1 次，实际 %d（open_file=%r msg_answer=%r）"
                    % (idx, len(calls), open_file, answer))
                got = calls[0].get("safe_mode")
                assert got is safe_expect, (
                    u"用例%d：safe_mode=%r，期望 %r" % (idx, got, safe_expect))
                # 开洞开关**只能由 lib 决定**。按钮里以前另写了一道
                # `door_window_cut=(not big) and (not safe)`（big=计划数>200），
                # 与 lib 里那道同名的阈值重复；拆掉 lib 那道后，调用方的显式传值
                # 把它整个覆盖掉，真机重跑门窗仍 0 寄宿。所以这里断言必须是 None。
                _dwy = calls[0].get("door_window_cut", "__缺参__")
                assert _dwy is None, (
                    u"用例%d：door_window_cut 应由 lib 决定（传 None），"
                    u"实得 %r —— 按钮里不要再写第二道规模阈值" % (idx, _dwy))

        # ⑤ 脏文档闸门：模型里已有墙必须**硬阻断**（DWG翻模/script.py:359-378，
        #    三崩实录换来的规则：残墙叠新墙 -> 墙重叠风暴 -> Revit 崩）。
        #    这条以前是"假通过"：DB.Wall 在 mock 里不存在，AttributeError 被
        #    按钮的 except 吞掉，闸门根本没被执行到。
        from pyrevit import DB as _mock_db
        _mock_db.WALL_COUNT = 3
        try:
            SWF_CTRL["open_file"] = json_path
            SWF_CTRL["msg_answer"] = DLG_RESULT.Yes
            del calls[:]
            exec(compile(src, script_path, "exec"), _exec_globals(script_path))
            assert calls == [], (
                u"用例5：文档已有墙时必须硬阻断，却调用了 create_elements %s"
                % calls)
        finally:
            _mock_db.WALL_COUNT = 0
    finally:
        bimconvert.create_elements = real_create

    sys.stderr.write("PASS  logic: DWG翻模 写入模式闸门"
                     "（取消/常规/安全/脏文档硬阻断，原生 WinForms 桩）\n")


def _test_batch_params_gate(tmp):
    """回归：批量改参 的「确认才写入」安全闸门 —— 补上此前的测试死区。

    背景：这条写入路径（批量改参/script.py:87-96）**从未被执行过**。
    原因是 TaskDialog 桩恒返回 Cancel，`if result == TaskDialogResult.Ok`
    恒为假 —— 而"默认只预览、确认才写入"恰恰是该按钮最关键的安全语义。
    现在 TaskDialog 桩支持 SWF_CTRL["taskdlg_answer"]，两条分支都能真跑到。
    """
    import bimlib
    from Autodesk.Revit.UI import TaskDialogResult as _TDR

    rel = [b for b in BUTTON_DIRS if b.endswith(u"批量改参.pushbutton")]
    if not rel:
        sys.stderr.write("SKIP  logic: 未发现 批量改参 按钮\n")
        return
    script_path = os.path.join(tmp, rel[0], "script.py")
    with open(script_path, "rb") as f:
        src = f.read().decode("utf-8-sig")

    # 自己造 CSV，不再依赖随包扩展目录的 batch_params.csv ——
    # 那份文件此前是活数据（墙 / 标记 / TEST-001），点一次确认就改项目，
    # 已于 2026-10-07 从扩展里移除；扩展只带 batch_params.csv.example。
    from bimlib import write_csv_unicode
    csv_out = os.path.join(tmp, rel[0], "batch_params.csv")
    write_csv_unicode(csv_out,
                      [u"Category", u"Type", u"ParamName", u"Value"],
                      [[u"墙", u"", u"标记", u"BMT-GATE-01"],      # 合法，应写入
                       [u"墙", u"", u"标记", u""],                 # 空值，必须被跳过
                       [u"墙", u"", u"标记", u"<填写真实值>"]])      # 占位串，必须被跳过

    calls = []
    real_set = bimlib.set_param

    def fake_set(elem, pname, val):
        calls.append((pname, val))
        return (u"", u"", True, u"")

    try:
        bimlib.set_param = fake_set

        # ① 确认框取消 -> 只预览，绝不写入
        SWF_CTRL["taskdlg_answer"] = _TDR.Cancel
        del calls[:]
        exec(compile(src, script_path, "exec"), _exec_globals(script_path))
        assert calls == [], (
            u"取消时不应写入，却调用了 set_param %d 次：%s" % (len(calls), calls[:3]))

        # ② 确认框确定 -> 真正写入
        SWF_CTRL["taskdlg_answer"] = _TDR.Ok
        del calls[:]
        exec(compile(src, script_path, "exec"), _exec_globals(script_path))
        assert calls, (
            u"确认后应写入，实际 set_param 一次都没被调用 —— "
            u"多半是测试造的 CSV 与 mock 构件不匹配（plan 为空）")
        # ③ 空值/占位串一行都不许落盘（写空串等于清空该参数）
        bad = [c for c in calls if c[1] != u"BMT-GATE-01"]
        assert not bad, (
            u"空 Value / 模板占位串被当成真实值写入了：%s" % (bad[:3],))
    finally:
        bimlib.set_param = real_set
        SWF_CTRL["taskdlg_answer"] = None
        try:
            os.remove(csv_out)
        except OSError:
            pass

    sys.stderr.write("PASS  logic: 批量改参 写入闸门（取消不写 / 确认才写 / "
                     "空值与占位串跳过，%d 处）\n" % len(calls))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    strict = "--strict" in argv

    tmp = _stage()
    known_failed = []
    new_failed = []
    sys.stderr.write("发现按钮脚本 %d 个:\n" % len(SCRIPTS))
    for s in SCRIPTS:
        sys.stderr.write("    - %s\n" % s)
    sys.stderr.write("")
    for s in SCRIPTS:
        name = _button_name(s)
        try:
            run_one(tmp, s)
            sys.stderr.write("PASS  %s\n" % s)
        except Exception as e:
            if name in KNOWN_BROKEN:
                sys.stderr.write("KNOWN %s  [%s] %s\n" % (s, type(e).__name__, e))
                known_failed.append((s, e))
            else:
                sys.stderr.write("FAIL  %s  [%s] %s\n" % (s, type(e).__name__, e))
                new_failed.append((s, e))

    # ---- 逻辑校验（不依赖 Revit 表面）----
    from bimhealth import health_score, health_grade
    assert health_score(100, 0, 0, 0, 0) == 100, "clean model should be 100"
    assert health_score(100, 50, 10, 5, 20) < 100, "dirty model should drop"
    assert health_grade(95).startswith("A"), health_grade(95)
    assert health_grade(70).startswith(("C", "D")), health_grade(70)
    sys.stderr.write("PASS  logic: health_score / health_grade\n")

    # ---- 规范审查三态：缺数据必须显式 not_checked，不能"少一条记录" ----
    # 病根：少记录 -> 少扣分 -> 空模型也能拿 95 分"优秀"。
    from standards_checker import check_energy, check_fire, check_structure
    _en = check_energy({u"wall_thickness_mm": 250},
                       {u"energy": {u"min_wall_insulation_mm": 0}})
    assert len(_en) == 1 and _en[0][u"status"] == u"not_checked", (
        u"节能阈值缺失时应产出 1 条 not_checked，实得 %r —— "
        u"静默返回空列表就是 95 分假绿灯的来源" % (_en,))
    _fi = check_fire({u"building_height_mm": 0, u"floor_heights_mm": []},
                     {u"fire": {}})
    assert any(c[u"status"] == u"not_checked" for c in _fi), (
        u"取不到建筑高度时防火应报 not_checked，而不是按 0m 判 pass：%r" % (_fi,))
    _st = check_structure({u"wall_thickness_mm": 0, u"floor_heights_mm": []},
                          {u"structure": {u"min_wall_thickness_mm": 160}})
    assert any(c[u"status"] == u"not_checked" for c in _st), (
        u"取不到墙厚时结构类应报 not_checked，而不是静默跳过：%r" % (_st,))
    sys.stderr.write("PASS  logic: 规范审查三态（not_checked 不再隐身）\n")

    # ---- 构件工厂：退化/NaN 输入必须在 Wall.Create 之前就被挡住 ----
    # 依据 bimconv_elements.py:149 的实录：喂给 Wall.Create 退化或 NaN 曲线
    # 触发的是**不可捕获的 Revit 原生闪退**，所以守卫只能在上游。
    import element_factory as ef

    class _P(object):
        def __init__(self, *c):
            self.c = [float(x) for x in c]

        def DistanceTo(self, o):
            s = 0.0
            for i in range(len(self.c)):
                d = self.c[i] - o.c[i]
                s += d * d
            return s ** 0.5

        def __getitem__(self, i):
            return self.c[i]

    # Revit 的真 XYZ 有 .X/.Y/.Z 与索引器，但**没有 len() 也不能切片**。
    # 工厂早期版本用 len(p)/p[:3] 判点，在真 XYZ 上直接 TypeError ——
    # 守卫比它要防的坏输入更早崩。这个替身就是为锁死那次修复而存在。
    class _XYZLike(object):
        def __init__(self, x, y, z):
            self.X, self.Y, self.Z = x, y, z

    assert ef.point_ok(_XYZLike(1.0, 2.0, 0.0)), \
        u"coords_of 必须认得只有 .X/.Y/.Z 的 Revit XYZ"
    assert not ef.point_ok(_XYZLike(float("nan"), 2.0, 0.0)), \
        u"XYZ 上的 NaN 也必须被 point_ok 挡住"
    try:
        ef.segments_from_points([_XYZLike(0, 0, 0), _XYZLike(float("inf"), 0, 0)])
        raise AssertionError(u"inf 坐标应抛 BadInput")
    except ef.BadInput:
        pass

    _nan = _P(float("nan"), 0.0, 0.0)
    try:
        ef.segments_from_points([_nan, _P(1, 0, 0)])
        raise AssertionError(u"含 NaN 的点序列必须抛 BadInput")
    except ef.BadInput:
        pass

    # 重合点：跳过该段而不是整条消失
    _kept, _dropped = ef.segments_from_points(
        [_P(0, 0, 0), _P(0, 0, 0), _P(5, 0, 0), _P(10, 0, 0)], min_len=0.1)
    assert len(_kept) == 2 and len(_dropped) == 1, \
        u"零长段应被跳过且记账，实得 kept=%d dropped=%d" % (
            len(_kept), len(_dropped))

    # 闭合：n 个点应产出 n 段
    _sq, _ = ef.segments_from_points(
        [_P(0, 0, 0), _P(10, 0, 0), _P(10, 10, 0), _P(0, 10, 0)], closed=True)
    assert len(_sq) == 4, u"闭合四点应产出 4 段，实得 %d" % len(_sq)

    # 整条过短：抛，不产出半条垃圾墙
    try:
        ef.segments_from_points([_P(0, 0, 0), _P(0.001, 0, 0)], total_min=1.0)
        raise AssertionError(u"总长低于下限应抛 BadInput")
    except ef.BadInput:
        pass

    # create_walls：某段失败不影响其他段，且失败原因逐段可追
    class _FakeWall(object):
        pass

    class _FakeLine(object):
        def __init__(self, a, b):
            self.a, self.b = a, b

        @staticmethod
        def CreateBound(a, b):
            return _FakeLine(a, b)

    class _FakeWallApi(object):
        @staticmethod
        def Create(doc, curve, wt, lvl, h, off, flip, struct):
            if curve.a.c[0] == 9 or curve.b.c[0] == 9:
                raise RuntimeError(u"模拟该段建墙失败")
            return _FakeWall()

    class _FakeWallUtils(object):
        calls = []

        @staticmethod
        def DisallowWallJoinAtEnd(w, end):
            _FakeWallUtils.calls.append(end)

    class _FakeAPI(object):
        Line = _FakeLine
        Wall = _FakeWallApi
        WallUtils = _FakeWallUtils

    _p = [_P(0, 0, 0), _P(5, 0, 0), _P(9, 0, 0), _P(12, 0, 0)]
    _segs = [(_p[0], _p[1]), (_p[1], _p[2]), (_p[2], _p[3])]
    _made, _fail = ef.create_walls(object(), _segs, 1, 2, 10.0, api=_FakeAPI)
    # x=9 那个顶点被第 2、3 段共用，所以两段都失败、只有第 1 段建成 ——
    # 这正是"逐段容错"要的语义：坏点只污染与它相邻的段，不吃掉整条。
    assert [i for i, _r in _fail] == [1, 2], \
        u"失败段号应为 [1, 2]，实得 %r" % (_fail,)
    assert len(_made) == 1, u"3 段里应建成 1 段，实得 %d" % len(_made)
    assert len(_made) + len(_fail) == len(_segs), \
        u"成功数 + 失败数必须等于请求段数，否则就是静默丢件"

    # 接合抑制开关要真的生效（翻模侧靠它躲开墙重叠风暴）
    _FakeWallUtils.calls = []
    ef.create_walls(object(), [(_p[0], _p[1])], 1, 2, 10.0,
                    suppress_join=True, api=_FakeAPI)
    assert _FakeWallUtils.calls == [0, 1], \
        u"suppress_join=True 应两端各调一次，实得 %r" % (_FakeWallUtils.calls,)
    _FakeWallUtils.calls = []
    ef.create_walls(object(), [(_p[0], _p[1])], 1, 2, 10.0, api=_FakeAPI)
    assert _FakeWallUtils.calls == [], u"默认不应关接合"

    # 绕过 segments_from_points 直接拼的坏段，也要在 Create 之前被挡住
    _bad = [(_P(0, 0, 0), _P(float("nan"), 0, 0))]
    _made2, _fail2 = ef.create_walls(object(), _bad, 1, 2, 10.0, api=_FakeAPI)
    assert _made2 == [] and len(_fail2) == 1, \
        u"NaN 端点必须被拒且不调用 Create：%r %r" % (_made2, _fail2)

    # 非法墙高：0 高墙与 NaN 高都要挡在门口
    for _bad_h in (0, -1, float("nan")):
        try:
            ef.create_walls(object(), _segs, 1, 2, _bad_h, api=_FakeAPI)
            raise AssertionError(u"墙高 %r 应被拒绝" % _bad_h)
        except ef.BadInput:
            pass

    # 空输入不许"静默成功"
    try:
        ef.create_walls(object(), [], 1, 2, 10.0, api=_FakeAPI)
        raise AssertionError(u"segments 为空应抛 BadInput，而不是返回 0 成功")
    except ef.BadInput:
        pass
    sys.stderr.write("PASS  logic: element_factory 守卫（NaN/退化/零高/空输入）\n")

    # ---- 翻模建墙：直接打 _create_wall，证明工厂接管后行为没变 ----
    # 为什么必须单独测：DWG翻模 的写入闸门测试把 bimconvert.create_elements
    # 整个换成了 spy（只记参数、返回假结果），所以"墙到底建没建出来"这条
    # 主链路此前从未被任何测试执行过。
    import bimconv_elements as _be
    from pyrevit import DB as _mdb

    def _wall_plan(pts, closed=False, height=None):
        geom = {"type": "polyline", "points": pts, "closed": closed}
        return {"type": u"wall", "geom": geom, "height": height,
                "level": u"标高 1", "thickness_mm": 240}

    class _FakeWT(object):
        Id = 1

    _col, _crv = [], []
    _w1 = _be._create_wall(object(), _wall_plan([[0, 0, 0], [6000, 0, 0]]),
                           1, _FakeWT(), collect=_col, curves=_crv)
    assert _w1 is not None and len(_col) == 1, \
        u"两点直墙应建出 1 段并进 collect，实得 collect=%d" % len(_col)
    assert len(_crv) == 1, u"curves 应与墙一一对应（门窗寄宿要用）"

    _col2 = []
    _be._create_wall(object(), _wall_plan(
        [[0, 0, 0], [5000, 0, 0], [5000, 5000, 0], [0, 5000, 0]],
        closed=True, height=3000), 1, _FakeWT(), collect=_col2)
    assert len(_col2) == 4, \
        u"闭合四点应建 4 段墙，实得 %d —— 少建就是静默丢构件" % len(_col2)

    # 重合点：只丢那一段，其余照建
    _col3 = []
    _be._create_wall(object(), _wall_plan(
        [[0, 0, 0], [0, 0, 0], [4000, 0, 0]]), 1, _FakeWT(), collect=_col3)
    assert len(_col3) == 1, \
        u"重合点应只吃掉那一段，实得 %d 段" % len(_col3)

    # 全退化：必须抛（上层记"跳过"），不能返回 None 让调用方以为建好了
    try:
        _be._create_wall(object(), _wall_plan([[0, 0, 0], [0, 0, 0]]),
                         1, _FakeWT())
        raise AssertionError(u"两点重合应抛异常记为跳过，而不是静默返回")
    except AssertionError:
        raise
    except Exception:
        pass

    # NaN 坐标：必须在进 Wall.Create 之前就被挡掉（native crash 防线）
    try:
        _be._create_wall(object(), _wall_plan(
            [[float("nan"), 0, 0], [5000, 0, 0]]), 1, _FakeWT())
        raise AssertionError(u"NaN 坐标必须被拒绝")
    except AssertionError:
        raise
    except Exception:
        pass
    sys.stderr.write("PASS  logic: 翻模 _create_wall 经工厂后行为不变"
                     "（直墙/闭合/重合点/全退化/NaN）\n")


    # ---- 门窗开洞默认值：大规模计划不得再自动关掉开洞 ----
    # 为什么必须测：DWG翻模 的写入闸门把 create_elements 整个换成 spy，
    # 所以 create_elements 内部"门窗到底走哪条分支"此前从未被任何测试执行。
    # 旧默认是 `door_window_cut = len(planned) <= 200`——真图计划普遍上万，
    # 等于任何真图都拿不到洞口，而按钮标签写着"含门/窗开洞"。
    # 两条分支的结果文案不同，正好可以据此判定默认值：
    #   cut=True  且没有墙可寄宿 -> 失败「模型中无墙可寄宿」
    #   cut=False                -> 自由放置，会被记成「成功」
    import bimconv_build as _bb

    def _win_plan(i):
        return {"element_type": "window", "reliability": "experimental",
                "level": u"标高 1", "height": 1500.0, "type_hint": u"",
                "source": u"insert#W%d" % i, "note": u"",
                "geom": {"type": "insert", "layer": u"WINDOW",
                         "handle": u"W%d" % i, "block": u"WIN",
                         "point": [i * 1000.0, 0.0, 0.0]}}

    _big = [_win_plan(i) for i in range(300)]      # 300 > 旧阈值 200
    _res = _bb.create_elements(object(), _big, out=None,
                               door_window_cut=None, safe_mode=False)
    _msgs = [r[1] for r in _res]
    _hostel = sum(1 for m in _msgs if u"无墙可寄宿" in str(m))
    assert _hostel == len(_big), (
        u"300 个窗（>旧阈值200）应全部尝试寄宿开洞并因无墙而明确失败，"
        u"实得 %d/%d；其余消息样例=%s" % (_hostel, len(_big), _msgs[:2]))
    # 反向：显式 cut=False 时必须走自由放置（记成功），证明上面那条不是假通过
    _res2 = _bb.create_elements(object(), [_win_plan(i) for i in range(5)],
                                out=None, door_window_cut=False, safe_mode=False)
    assert not any(u"无墙可寄宿" in str(r[1]) for r in _res2), \
        u"cut=False 应走自由放置分支，不该报『无墙可寄宿』"
    sys.stderr.write("PASS  logic: 门窗开洞不再按规模一刀切关闭"
                     "（300 个窗全部尝试寄宿；cut=False 时确实走自由放置）\n")

    from bimlib import write_csv_unicode, read_csv_unicode
    tf = os.path.join(tempfile.gettempdir(), "bimtoolkit_test.csv")
    write_csv_unicode(tf, [u"名称", u"值"], [[u"墙", 1], [u"楼板", u"二"]])
    rows = read_csv_unicode(tf)
    assert rows and rows[0].get(u"名称") == u"墙", rows
    sys.stderr.write("PASS  logic: csv round-trip (utf-8 BOM)\n")

    # ---- 收集器口径：实例 ≠ 族类型 ----
    # 极性以 2026-10-07 真机测量为准：ElementIsElementTypeFilter 的参数是 invert，
    # True = 只放行实例（等价 WhereElementIsNotElementType()），False = 只放行族类型。
    # 这组断言同时校验 mock 与 bimlib 两侧：任何一侧把极性写反都会红。
    from pyrevit import DB as _db
    fec = _db.FilteredElementCollector
    inst = [e for e in fec(None).WherePasses(
        _db.ElementIsElementTypeFilter(True)).ToElements()]
    typ = [e for e in fec(None).WherePasses(
        _db.ElementIsElementTypeFilter(False)).ToElements()]
    assert inst and not any(getattr(e, "is_type", False) for e in inst), \
        u"mock 极性写反：Filter(True) 应当只给实例"
    assert typ and all(getattr(e, "is_type", False) for e in typ), \
        u"mock 极性写反：Filter(False) 应当只给族类型"
    assert len(inst) == len(fec(None).WhereElementIsNotElementType().ToElements()), \
        u"mock 失真：Filter(True) 应与 WhereElementIsNotElementType() 同结果"
    assert len(typ) == len(fec(None).WhereElementIsElementType().ToElements()), \
        u"mock 失真：Filter(False) 应与 WhereElementIsElementType() 同结果"

    from bimlib import all_model_elements
    got = all_model_elements(None).ToElements()
    assert got, u"mock 失效：all_model_elements 返回空"
    n_type = len([e for e in got if getattr(e, "is_type", False)])
    assert n_type == 0, (
        u"统计口径反了：all_model_elements 拿到的 %d 个元素里有 %d 个是族类型。"
        u"ElementIsElementTypeFilter 的参数是 invert，统计构件要传 True。"
        % (len(got), n_type))

    # Level 是实例：Filter(True) 应给全部楼层，Filter(False) 应给空
    lv_inst = fec(None).OfClass(_db.Level).WherePasses(
        _db.ElementIsElementTypeFilter(True)).ToElements()
    lv_typ = fec(None).OfClass(_db.Level).WherePasses(
        _db.ElementIsElementTypeFilter(False)).ToElements()
    assert len(lv_inst) == 3 and lv_typ == [], \
        u"mock 失真：Level 是实例，Filter(True) 应得 3 层、Filter(False) 应得 0"
    sys.stderr.write("PASS  logic: 收集器口径（极性按真机测量：True=实例）\n")

    # ---- AI 指令 的执行前静态护栏（S13）----
    from ai_code_guard import scan, blockers, warnings
    _ok_code = (u"wt = None\n"
                u"for t in FilteredElementCollector(doc).OfClass(WallType).ToElements():\n"
                u"    if t.Kind == WallKind.Basic:\n"
                u"        wt = t\n"
                u"        break\n")
    assert blockers(scan(_ok_code)) == [], u"正常改模型的代码不应被拦"
    assert warnings(scan(_ok_code)) == [], u"正常改模型的代码不应被告警"
    for bad, why in ((u"open(r'C:/x.txt','w')\n", u"open"),
                     (u"m = __import__('os')\n", u"__import__"),
                     (u"import clr\nclr.AddReference('System.IO')\n", u"clr.AddReference"),
                     (u"import subprocess\n", u"subprocess")):
        assert blockers(scan(bad)), u"%s 应被拦截" % why
    # 删构件/弹窗是「AI 指令」的本职，只能告警、不能拦
    assert blockers(scan(u"doc.Delete(ids)\n")) == [], u"删构件不应被硬拦"
    assert warnings(scan(u"doc.Delete(ids)\n")), u"删构件应告警"
    assert warnings(scan(u"TaskDialog.Show('x')\n")), u"弹窗应告警"
    sys.stderr.write("PASS  logic: ai_code_guard 静态护栏（拦越界 / 放行本职）\n")

    # ---- AI 建议层的提示词构造（S13）----
    import ai_advisor
    _rep = {"score": 62, "grade": u"C", "fail_count": 1, "warn_count": 1,
            "model_params": {"total_floors": 3, "building_height_mm": 9000,
                             "wall_thickness_mm": 200, "total_area_sqm": 500.0},
            "checks": [{"item": u"疏散距离", "status": u"fail",
                        "message": u"超出", "standard": u"GB50016",
                        "suggestion": u"调整"},
                       {"item": u"窗墙比", "status": u"warning",
                        "message": u"偏大", "standard": u"GB50176",
                        "suggestion": u"复核"},
                       {"item": u"层高", "status": u"pass",
                        "message": u"合格", "standard": u"-",
                        "suggestion": u"-"}]}
    _sys, _usr = ai_advisor.build_prompts(_rep, u"测试模型")
    assert u"疏散距离" in _usr and u"窗墙比" in _usr, u"不合格/警告项应进入提示词"
    assert u"层高" not in _usr, u"合格项不应进入提示词"
    assert u"不得编造条文号" in _sys, u"系统提示词必须禁止编造条文号"
    assert u"不得质疑" in _sys, u"系统提示词必须禁止改动判定结论"
    sys.stderr.write("PASS  logic: ai_advisor 提示词（规则判/AI 解释，只带不合格项）\n")

    # ---- 梁（S14 新增）：截面解析 + 坏输入必须被拒 ----
    from bimconv_elements import _beam_size, _create_beam
    assert _beam_size({"type_hint": u"300x600"}) == (300.0, 600.0), \
        "type_hint 300x600 应解析为 300 宽 600 高"
    assert _beam_size({"type_hint": u"250*500"}) == (250.0, 500.0), "* 分隔同样识别"
    assert _beam_size({"type_hint": u"200X400"}) == (200.0, 400.0), "大写 X 同样识别"
    assert _beam_size({}) == (200.0, 400.0), "无提示时用默认 200x400"
    assert _beam_size({"beam_width_mm": 150, "beam_height_mm": 350}) == (150.0, 350.0), \
        "显式字段优先于 type_hint"

    def _must_raise(plan, why):
        try:
            _create_beam(None, plan, None)
        except Exception:
            return
        raise AssertionError(why)

    _must_raise({"geom": {}}, u"缺端点应抛异常（不能建到一半才崩）")
    _must_raise({"geom": {"start": [0, 0], "end": [1, 0]}},
                u"长度 1mm 的退化梁应被拒绝")
    _must_raise({"geom": {"start": [float('nan'), 0], "end": [5000, 0]}},
                u"NaN 端点应被拒绝")
    sys.stderr.write("PASS  logic: 梁（截面解析 + 退化/NaN 拒绝）\n")

    # ---- 4D 步骤生成（S15）：进度计划 -> 桥的导览任务树 ----
    import schedule_to_steps as s2s
    _rows = [
        {"uid": "A", u"名称": u"一层墙", u"楼层": u"1F", u"类别": u"墙",
         u"计划开始": u"2026-03-10", u"计划完成": u"2026-03-20", u"状态": u"未开始"},
        {"uid": "B", u"名称": u"二层墙", u"楼层": u"2F", u"类别": u"墙",
         u"计划开始": u"2026-03-25", u"计划完成": u"2026-04-05", u"状态": u"未开始"},
        {"uid": "C", u"名称": u"全楼通用", u"楼层": u"", u"类别": u"墙",
         u"计划开始": u"2026-05-01", u"计划完成": u"2026-05-10", u"状态": u"未开始"},
    ]
    _tree, _warns = s2s.build_task_tree(_rows, group=u"task")
    _steps = _tree["steps"]
    assert _steps[0].get("show_all") and _steps[-1].get("show_all"), \
        u"首步与末步必须是全楼（总览 / 竣工）"
    assert any(s.get("levels") == ["1F"] for s in _steps), u"1F 任务应生成 levels=[1F]"
    assert any(s.get("show_all") and s["title"].startswith(u"（全楼）")
               for s in _steps[1:-1]), u"无楼层任务应退化为全楼并在标题里标注"
    assert _warns, u"存在无楼层任务时必须给 warning（不能静默退化）"
    _t2, _ = s2s.build_task_tree(_rows, group=u"month")
    _months = [s["title"][:7] for s in _t2["steps"][1:-1]]
    assert _months == sorted(_months) and len(_t2["steps"]) < len(_steps), \
        u"按月合步应减少步数且保持时间顺序"
    sys.stderr.write("PASS  logic: 4D 步骤生成（首末全楼 / levels / 无楼层退化 / 按月有序）\n")

    # ---- 图纸翻模：plan_from_json 规则匹配往返（IronPython 2.7 兼容）----
    from bimconvert import load_rules, plan_from_json
    rules_csv = os.path.join(PANEL, u"DWG翻模.pushbutton", "mapping_rules.csv")
    rules = load_rules(rules_csv)
    sample_plan = {
        "source": "sample.dwg", "format": "dwg",
        "layers": ["GRID", "WALL", "COL", "DOOR"],
        "blocks": ["COL-A", "DOOR-S"],
        "entities": [
            {"type": "line", "layer": "GRID", "handle": "1",
             "start": [0, 0, 0], "end": [10000, 0, 0]},
            {"type": "lwpolyline", "layer": "WALL", "handle": "2", "closed": True,
             "points": [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0]]},
            {"type": "insert", "layer": "COL", "handle": "3",
             "block": "COL-A", "point": [500, 500, 0]},
            {"type": "insert", "layer": "DOOR", "handle": "4",
             "block": "DOOR-S", "point": [200, 0, 0]},
        ],
    }
    planned, unmatched = plan_from_json(sample_plan, rules, ["1F", "2F"])
    assert unmatched == [], "unmatched=%s" % unmatched
    assert len(planned) == 4, "planned=%s" % planned
    assert planned[0]["element_type"] == "grid" and planned[0]["reliability"] == "mature"
    assert planned[1]["element_type"] == "wall" and planned[1]["reliability"] == "mature"
    assert planned[2]["element_type"] == "column" and planned[2]["reliability"] == "mature"
    assert planned[3]["element_type"] == "door" and planned[3]["reliability"] == "experimental"
    sys.stderr.write("PASS  logic: bimconvert.plan_from_json round-trip (mature+experimental)\n")

    # ---- DWG翻模 写入模式闸门（V2.4 原生 WinForms 桩驱动）----
    _test_dwg_mode_gate(tmp)

    # ---- 批量改参 写入闸门（此前是测试死区）----
    _test_batch_params_gate(tmp)

    # ---- 柱族类型选择器 ----
    # 真机实测 1733 根柱被静默统一成 UC305x305x97：旧路径
    # `col_syms if col_syms else all_syms` + `pool[0]`，既可能拿门当柱，
    # 结果又取决于收集器顺序。这条断言把三件事钉住。
    import bimconv_elements as _bce

    class _Sym(object):
        def __init__(self, n):
            self.Name = n

    _steel = _Sym(u"UC305x305x97")
    _steel2 = _Sym(u"UC406x140x39")
    _conc = _Sym(u"混凝土-矩形 400x400")
    _conc2 = _Sym(u"混凝土-圆形 800")
    # 契约：pool 已由 _column_pool / col_syms 按类别筛过，这里传的都是柱族。
    # 1) 有混凝土柱时只能落在混凝土集合内。不断言"哪一根"——按名排序选圆形
    #    还是矩形不是要紧的性质，要紧的是可复现。
    _got = _bce.pick_column_symbol([_steel, _conc, _conc2], u"")
    assert u"混凝土" in _got.Name, "应只在混凝土柱里挑，实得 %s" % _got.Name
    assert _bce.pick_column_symbol([_conc2, _steel, _conc], u"").Name == _got.Name, \
        "打乱入参顺序必须得到同一个类型"
    # 2) 没有混凝土柱时也要确定（旧实现返回 pool[0]，等于收集器顺序）
    assert _bce.pick_column_symbol([_steel2, _steel], u"").Name == _steel.Name, \
        "无混凝土柱时应按名排序取首枚"
    assert _bce.pick_column_symbol([_steel, _steel2], u"").Name == _steel.Name
    # 3) 空池必须返回 None（让调用方明确失败），不能兜底成别的族
    assert _bce.pick_column_symbol([], u"") is None, "空池必须返回 None"
    assert _bce.pick_column_symbol(None, u"") is None, "None 池必须返回 None"
    # 4) hint 命中优先于"偏好混凝土"
    assert _bce.pick_column_symbol([_conc, _steel], u"uc305") is _steel, \
        "type_hint 应优先于默认偏好"
    sys.stderr.write("PASS  logic: pick_column_symbol 不再乱兜底且可复现\n")

    shutil.rmtree(tmp, ignore_errors=True)

    # ---- 覆盖度与"宽松桩"记账 ----
    autodesk_stubs = sorted(s for s in AUTO_STUBBED if s.startswith("Autodesk."))
    pyrevit_stubs = sorted(s for s in AUTO_STUBBED if s.startswith("pyrevit."))
    other_stubs = sorted(s for s in AUTO_STUBBED
                         if not s.startswith("Autodesk.")
                         and not s.startswith("pyrevit."))
    sys.stderr.write("\n--- 覆盖统计 ---\n")
    sys.stderr.write("按钮脚本: %d 个（自动发现，非手写清单）\n" % len(SCRIPTS))
    sys.stderr.write("实跑通过: %d / %d\n"
                     % (len(SCRIPTS) - len(new_failed) - len(known_failed),
                        len(SCRIPTS)))
    sys.stderr.write("宿主管道桩(System.*/clr): %d 个（宽松补齐，非被测对象）\n"
                     % len(other_stubs))
    if autodesk_stubs:
        # Revit API 符号是"被测域"，被自动补齐的必须人工对照 —— 这不等于它真存在。
        sys.stderr.write("⚠️ Autodesk 符号被自动补齐 %d 个，"
                         "需与真实 RevitAPI 对照（第 2 关只校验了白名单）:\n"
                         % len(autodesk_stubs))
        for s in autodesk_stubs:
            sys.stderr.write("    ? %s\n" % s)
    else:
        sys.stderr.write("Autodesk 符号: 0 个被自动补齐\n")
    if pyrevit_stubs:
        sys.stderr.write("⚠️ mock pyrevit 符号被自动补齐 %d 个"
                         "（mock 未覆盖，非真实 Revit 行为，仅供跑通）:\n"
                         % len(pyrevit_stubs))
        for s in pyrevit_stubs:
            sys.stderr.write("    ? %s\n" % s)

    if known_failed:
        sys.stderr.write("\n--- 已登记的真实缺陷（不是测试环境问题）---\n")
        for s, _e in known_failed:
            sys.stderr.write("  [!] %s\n" % s)
            sys.stderr.write("      %s\n" % KNOWN_BROKEN[_button_name(s)])
        sys.stderr.write("  修好后请从 KNOWN_BROKEN 删除对应条目。\n")

    if new_failed:
        sys.stderr.write(
            "\n[HARNESS] FAIL: %d new + %d registered\n"
            % (len(new_failed), len(known_failed)))
        return 1

    if strict and known_failed:
        sys.stderr.write(
            "\n[HARNESS] FAIL (--strict): %d registered defect(s) not tolerated\n"
            % len(known_failed))
        return 1

    if known_failed:
        sys.stderr.write(
            "\n[HARNESS] PASS WITH REGISTERED DEFECTS (%d)\n" % len(known_failed))
        return 0
    sys.stderr.write("\n[HARNESS] ALL PASS\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
