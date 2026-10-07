# -*- coding: utf-8 -*-
"""Revit "AI 指令" 按钮 (pyRevit / IronPython 2.7, Revit 2019)。

工作流：抓取模型上下文 -> 弹出原生 WinForms 输入框收集自然语言指令 ->
调用 DeepSeek 生成 Revit Python 代码 -> 打开 Notepad 供查看 + MessageBox 确认 ->
在 Transaction 中执行（出错自动回滚）。

安全约束（Revit 2019 旧版 IronPython 加载器）：
  - 不使用任何 WPF 窗体（forms.ask_for_string / forms.alert 均为 WPF，会崩）。
  - 仅用原生 WinForms 输入框 + Revit TaskDialog / WinForms MessageBox。
  - 不使用 f-strings（IronPython 2.7 不支持）。
  - AI 生成的代码执行前必须经过人工确认（安全门）。
"""
import os
import sys
import traceback

from Autodesk.Revit.DB import *
from Autodesk.Revit.UI import *

# pyRevit 注入的全局：__revit__, doc, uidoc
uidoc = __revit__.ActiveUIDocument
doc = uidoc.Document

# ---- 把扩展根目录的 lib 加入搜索路径，以便 import llm_client ----
# 目录链: script.py(in AI 指令.pushbutton) -> BIM 工具.panel -> BIMToolkit.tab
#         -> BIMToolkit.extension(扩展根)  => 向上 3 层
HERE = os.path.dirname(__file__)
EXT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
LIB = os.path.join(EXT, "lib")
if LIB not in sys.path:
    sys.path.insert(0, LIB)

try:
    import llm_client
except Exception as e:
    _show = None
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons
        MessageBox.Show("无法加载 llm_client: " + str(e), "AI 指令 - 错误",
                        MessageBoxButtons.OK)
    except Exception:
        pass
    raise

SYSTEM_PROMPT = (
    "你是一名 Autodesk Revit 2019 的 Python 自动化专家，"
    "输出可在 Revit IronPython 2.7 环境中直接执行的 Python 代码。\n"
    "可用上下文（由宿主注入全局变量）：\n"
    "  doc    = 当前 Document\n"
    "  uidoc  = 当前 UIDocument\n"
    "  __revit__ = 当前 RevitApplication\n"
    "已预先导入: from Autodesk.Revit.DB import *\n"
    "规则：\n"
    "1. 只输出一个 ```python 代码块，不要任何解释性文字。\n"
    "2. 不要自己开启 Transaction —— 宿主会把你的代码包在一个名为 'AI 指令' 的 "
    "Transaction 里执行。你只需直接对 doc 做增删改查。\n"
    "3. 不要调用 forms.alert / TaskDialog / 任何 pyRevit WPF 窗体。\n"
    "4. 不要使用 f-strings（IronPython 2.7 不支持），用 .format() 或 % 格式化。\n"
    "5. 优先使用 FilteredElementCollector(doc).OfClass(...) 等标准 API。\n"
    "6. 若需要遍历图元，用 collector.ToElements() 得到列表再迭代。\n"
    "7. **只读参数陷阱**：许多 BuiltInParameter 是只读的（例如 WALL_ATTR_WIDTH_PARAM、"
    "WALL_USER_HEIGHT_PARAM 在 WallType 上是只读的）。不要直接对只读参数调用 .Set()。\n"
    "8. **墙体厚度/高度**：创建墙时，高度在 Wall.Create 之后可设置 WALL_USER_HEIGHT_PARAM；"
    "厚度必须通过 WallType 的 CompoundStructure 修改，或寻找已有匹配的 WallType，"
    "禁止直接设置 WallType 的 WALL_ATTR_WIDTH_PARAM。如必须新建类型，用 "
    "wall_type.GetCompoundStructure() / SetLayerWidth() / SetCompoundStructure()。\n"
    "9. 若出现 'The parameter is read-only'，说明代码违反了上述规则，应改用只写可写参数或 "
    "通过 CompoundStructure/类型切换实现。\n"
    "10. **创建墙——务必照抄下面的模板（四个坑已全部规避）**：\n"
    "    坑A：IronPython 对 Wall.Create 的 **4 参重载解析不可靠**（Curve 版与 IList[Curve] 版"
    "签名几乎一样），会报 'expected IList[Curve], got Line' 或 'expected Curve, got List[Curve]'。\n"
    "    坑B：**所有 IList[Curve] 版本都是『轮廓墙』，要求闭合轮廓**，传单条直线会报 "
    "'Could not construct a proper face with the input curves to create a wall correctly'。\n"
    "    坑C：**幕墙 / 叠层墙类型的 GetCompoundStructure() 返回 None**，直接改厚度会报 "
    "'NoneType object has no attribute SetLayerWidth'。"
    "必须先用 WallKind.Basic 过滤出『基本墙』类型，不能随手取 ToElements()[0]。\n"
    "    => 因此：不用 4 参重载、不用任何 IList[Curve] 版本、墙类型必须按 Kind 过滤。\n"
    "    **正确做法：使用唯一的 8 参重载**（单条 Curve + 显式墙类型与高度，签名唯一无歧义）：\n"
    "    Wall.Create(doc, Curve curve, ElementId wallTypeId, ElementId levelId,\n"
    "                double height, double offset, bool flip, bool structural)\n"
    "    完整模板（以 2000mm 高、200mm 厚 为例）：\n"
    "    from Autodesk.Revit.DB import *\n"
    "    level = FilteredElementCollector(doc).OfClass(Level).ToElements()[0]\n"
    "    wt = None\n"
    "    for t in FilteredElementCollector(doc).OfClass(WallType).ToElements():\n"
    "        if t.Kind == WallKind.Basic:\n"
    "            wt = t\n"
    "            break\n"
    "    if wt is None:\n"
    "        raise Exception(u'未找到基本墙类型')\n"
    "    new_wt = wt.Duplicate(u'AI墙_200mm')\n"
    "    cs = new_wt.GetCompoundStructure()\n"
    "    if cs is None:\n"
    "        raise Exception(u'该墙类型没有复合结构')\n"
    "    idx = 0\n"
    "    try:\n"
    "        for i in range(cs.LayerCount):\n"
    "            # 2019 无 CompoundStructureLayerFunction 枚举，按名字比较：\n"
    "            if str(cs.GetLayerFunction(i)) == u'Structure':\n"
    "                idx = i\n"
    "                break\n"
    "    except Exception:\n"
    "        pass\n"
    "    cs.SetLayerWidth(idx, 200/304.8)\n"
    "    new_wt.SetCompoundStructure(cs)\n"
    "    line = Line.CreateBound(XYZ(0,0,0), XYZ(5000/304.8, 0, 0))\n"
    "    wall = Wall.Create(doc, line, new_wt.Id, level.Id, 2000/304.8, 0.0, False, False)\n"
    "    （高度由第 5 个参数直接给出，不必再设置 WALL_USER_HEIGHT_PARAM；"
    "墙厚由 CompoundStructure 设置，禁止直接 Set 墙厚参数，见规则 7-9。）\n"
    "11. 长度单位是英尺：mm 数值必须除以 304.8（例：2000mm -> 2000/304.8）。\n"
    "12. 若 8 参重载仍报重载相关错误，用显式重载选择兜底：\n"
    "    Wall.Create.Overloads[Document, Curve, ElementId, ElementId, float, float, bool, bool](\n"
    "        doc, line, new_wt.Id, level.Id, 2000/304.8, 0.0, False, False)\n"
)


def to_text(v):
    u"""把任意值安全转成 unicode 文本。

    这是本按钮最关键的一个防御：IronPython 2.7 下
        u"中文" + str(含中文的内容)
    会先按 **ascii** 隐式解码 str，直接抛 UnicodeDecodeError。
    而本脚本里到处是中文拼接（用户指令、类别名、标高名、Revit 异常信息都可能是中文），
    不统一转换就会"莫名其妙报错"。所有往 u"" 上拼的值都必须先过这个函数。
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


def get_model_context():
    """收集模型摘要，供 LLM 生成更贴合的代码。"""
    parts = []
    try:
        parts.append(u"文档标题: " + to_text(doc.Title))
        collector = FilteredElementCollector(doc).WhereElementIsNotElementType()
        elems = collector.ToElements()
        parts.append(u"非类型图元总数: " + str(len(elems)))

        cat_count = {}
        for e in elems:
            try:
                cat = e.Category
                if cat is not None and cat.Name:
                    name = to_text(cat.Name)
                    cat_count[name] = cat_count.get(name, 0) + 1
            except Exception:
                pass
        top = sorted(cat_count.items(), key=lambda kv: -kv[1])[:25]
        parts.append(u"主要类别 (Top25):")
        for name, c in top:
            parts.append(u"  - " + name + u": " + str(c))

        levels = FilteredElementCollector(doc).OfClass(Level).ToElements()
        lvl_names = []
        for l in levels:
            lvl_names.append(to_text(l.Name))
        parts.append(u"标高数: " + str(len(lvl_names)) +
                     u" -> " + u", ".join(lvl_names[:30]))
    except Exception as ex:
        parts.append(u"(收集上下文出错: " + to_text(ex) + u")")

    try:
        sel_ids = uidoc.Selection.GetElementIds()
        parts.append(u"当前选择图元数: " + str(len(list(sel_ids))))
    except Exception:
        parts.append(u"当前选择图元数: 0")

    return u"\n".join(parts)


def ask_instruction():
    """原生 WinForms 输入框，返回指令字符串或 None。"""
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Windows.Forms import (Form, TextBox, Button, Label,
                                          Panel, DockStyle, DialogResult,
                                          FormStartPosition, ScrollBars,
                                          BorderStyle)
        from System.Drawing import Point, Color
    except Exception:
        return _ask_instruction_clipboard()

    form = Form()
    form.Text = u"AI 指令 - 输入自然语言"
    form.StartPosition = FormStartPosition.CenterScreen
    form.Width = 620
    form.Height = 380
    form.MinimizeBox = False
    form.MaximizeBox = False

    # 布局坑：WinForms 按 Controls 索引倒序做 Dock 布局，DockStyle.Fill 的控件
    # 必须最先 Add（索引最小 => 最后布局），否则 TextBox 会占满整块客户区，
    # 其顶部被 Label 盖住 —— 表现就是"输入的指令看不到"。
    # 正确顺序：tb(Fill) -> lbl(Top) -> bottom(Bottom)
    tb = TextBox()
    tb.Multiline = True
    tb.ScrollBars = ScrollBars.Both
    tb.Dock = DockStyle.Fill
    tb.BackColor = Color.White
    tb.ForeColor = Color.Black
    tb.BorderStyle = BorderStyle.FixedSingle
    form.Controls.Add(tb)

    lbl = Label()
    lbl.Text = (u"用中文描述你想让 Revit 做的操作，例如：\n"
                u"  - 把所有标高 1 的墙厚度改成 200mm\n"
                u"  - 列出当前文档里所有门的数量和类型\n"
                u"  - 在 (0,0,0) 处创建一个 3000mm 高的标高")
    lbl.Dock = DockStyle.Top
    lbl.Height = 70
    lbl.BackColor = Color.White
    form.Controls.Add(lbl)

    bottom = Panel()
    bottom.Dock = DockStyle.Bottom
    bottom.Height = 46
    form.Controls.Add(bottom)

    btn_ok = Button()
    btn_ok.Text = u"生成并执行"
    btn_ok.DialogResult = DialogResult.OK
    btn_ok.Width = 130
    btn_ok.Height = 30
    btn_ok.Location = Point(470, 8)
    bottom.Controls.Add(btn_ok)

    btn_cancel = Button()
    btn_cancel.Text = u"取消"
    btn_cancel.DialogResult = DialogResult.Cancel
    btn_cancel.Width = 110
    btn_cancel.Height = 30
    btn_cancel.Location = Point(340, 8)
    bottom.Controls.Add(btn_cancel)

    form.AcceptButton = btn_ok
    form.CancelButton = btn_cancel

    result = form.ShowDialog()
    if result == DialogResult.OK:
        # 用户输入是中文，必须先转 unicode，否则后续 u"" 拼接会炸
        return to_text(tb.Text).strip()
    return None


def _ask_instruction_clipboard():
    """WinForms 不可用时的降级方案：从剪贴板读取指令。"""
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import (Clipboard, MessageBox,
                                          MessageBoxButtons, DialogResult)
        txt = to_text(Clipboard.GetText() or u"")
        res = MessageBox.Show(
            u"已用剪贴板内容作为指令。\n剪贴板内容:\n" + (txt[:600] or u"(空)") +
            u"\n\n是否使用？", u"AI 指令", MessageBoxButtons.YesNo)
        if res == DialogResult.Yes:
            return txt.strip()
    except Exception:
        pass
    return None


def show_error(msg, title=u"AI 指令 - 错误"):
    msg_t = to_text(msg)
    title_t = to_text(title)
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import MessageBox, MessageBoxButtons
        MessageBox.Show(msg_t, title_t, MessageBoxButtons.OK)
    except Exception:
        print(msg_t)


def _copy_to_clipboard(text):
    """把报错复制到剪贴板，便于粘贴求助。失败静默忽略。"""
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import Clipboard
        Clipboard.SetText(text)
        return True
    except Exception:
        return False


def _request_fix(bad_code, instruction, tb_text):
    """把失败代码 + 报错回喂 DeepSeek，返回修正后的代码；失败返回 None。"""
    bad_code = to_text(bad_code)
    tb_text = to_text(tb_text)
    instruction = to_text(instruction)
    fix_prompt = (
        u"下面这段在 Revit 2019 IronPython 2.7 中执行失败的 Python 代码：\n\n"
        u"```python\n" + bad_code + u"\n```\n\n"
        u"执行时抛出的错误：\n\n" + tb_text[:2000] + u"\n\n"
        u"原始用户指令是：" + instruction + u"\n\n"
        u"请修正这段代码的错误，只输出一个 ```python 代码块，不要任何解释文字。\n"
        u"务必遵守：Revit 只读参数陷阱（不要对只读 BuiltInParameter 调 .Set()）、"
        u"墙厚走 CompoundStructure、不要开 Transaction、不要用 f-string。"
    )
    try:
        reply = llm_client.chat(SYSTEM_PROMPT, fix_prompt, model="deepseek-chat")
        code, _l = llm_client.extract_code(reply, lang="python")
        return code
    except Exception as e:
        show_error(u"请求 AI 修正失败: " + to_text(e))
        return None


MAX_FIX_ATTEMPTS = 3


def confirm_and_run(code_text, instruction, attempt=1):
    """打开 Notepad 供查看，MessageBox 确认后于 Transaction 中执行（失败可自愈重试）。"""
    # 统一转 unicode：用户指令与 AI 代码都可能含中文，避免 u"" 拼接炸
    instruction = to_text(instruction)
    code_text = to_text(code_text)

    # ---- 执行前的静态护栏（2026-10-01, S13）----
    # 拦"越出操作模型"的代码（文件/进程/网络/动态导入/加载程序集），
    # 只对"删构件/弹窗"这类本职操作告警。见 lib/ai_code_guard.py 的说明。
    guard_hits = []
    try:
        import ai_code_guard
        guard_hits = ai_code_guard.scan(code_text)
    except Exception:
        # 护栏本身不可用时不阻断：它是加分项，不是正确性依赖
        pass

    hard_block = [h for h in guard_hits if h["severity"] == "block"]
    soft_warn = [h for h in guard_hits if h["severity"] == "warn"]

    if hard_block:
        try:
            import ai_code_guard as _g
            detail = _g.format_hits(hard_block)
        except Exception:
            detail = u"\n".join([h["reason"] for h in hard_block])
        show_error(
            u"生成的代码越出了「操作当前模型」的范围，已拒绝执行。\n\n"
            u"%s\n\n"
            u"这些能力（读写文件 / 启动进程 / 访问网络 / 动态导入 / 加载任意 .NET "
            u"程序集）不是改模型需要的。请把指令限定在模型操作上，例如"
            u"「把标高1的墙厚度改成200mm」。" % detail)
        return

    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), "revit_ai_cmd.py")
    try:
        with open(tmp, "wb") as f:
            # UTF-8 BOM，让 Notepad 正确显示中文。
            # 坑：IronPython 2.7 下 "\xef\xbb\xbf".encode("utf-8") 会先按 ascii
            # 解码，抛 UnicodeDecodeError；必须直接写字面量字节 b"\xef\xbb\xbf"。
            f.write(b"\xef\xbb\xbf")
            if isinstance(code_text, bytes):
                f.write(code_text)
            else:
                f.write(code_text.encode("utf-8"))
    except Exception as e:
        show_error(u"写入临时文件失败: " + to_text(e))
        return

    # 打开 Notepad 供人工查看（异步，不阻塞）
    try:
        import System
        System.Diagnostics.Process.Start("notepad.exe", tmp)
    except Exception:
        pass

    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        from System.Windows.Forms import (MessageBox, MessageBoxButtons,
                                          DialogResult)
        preview = code_text
        if len(preview) > 1500:
            preview = preview[:1500] + u"\n... (已截断，完整内容见已打开的 Notepad)"
        instr_preview = instruction if len(instruction) <= 200 else (instruction[:200] + u"...")
        warn_text = u""
        if soft_warn:
            try:
                import ai_code_guard as _g
                warn_text = u"\n\n⚠️ 这段代码会做以下敏感操作，请确认确有必要：\n" \
                            + _g.format_hits(soft_warn)
            except Exception:
                warn_text = u"\n\n⚠️ 这段代码含 %d 处敏感操作。" % len(soft_warn)
        res = MessageBox.Show(
            u"你的指令：" + instr_preview +
            u"\n\n已生成 Revit Python 代码并打开 Notepad 供查看。\n\n" + preview +
            warn_text +
            u"\n\n是否在事务中执行这段代码？（出错将自动回滚）",
            u"AI 指令 - 确认执行" +
            (u"（AI 第 %d 次修正）" % (attempt - 1) if attempt > 1 else u""),
            MessageBoxButtons.YesNo)
        if res != DialogResult.Yes:
            MessageBox.Show(u"已取消执行。", u"AI 指令", MessageBoxButtons.OK)
            return
    except Exception as e:
        show_error(u"确认对话框出错: " + to_text(e))
        return

    t = Transaction(doc, "AI 指令")
    t.Start()
    ns = dict(globals())
    try:
        exec(code_text, ns)
        status = t.Commit()
        try:
            import clr
            clr.AddReference("System.Windows.Forms")
            from System.Windows.Forms import MessageBox, MessageBoxButtons
            MessageBox.Show(u"AI 指令已执行完成（事务状态: " + to_text(status) + u"）。",
                            u"AI 指令", MessageBoxButtons.OK)
        except Exception:
            pass
    except Exception as ex:
        try:
            t.RollBack()
        except Exception:
            pass
        tb_text = to_text(traceback.format_exc())
        err_text = to_text(ex)
        # 自愈：自动把报错回喂 AI 让其修正，最多 MAX_FIX_ATTEMPTS 轮。
        # 不再弹窗询问"是否修正"—— 修正后的代码仍须经确认对话框才会执行，故安全。
        if attempt < MAX_FIX_ATTEMPTS:
            fixed = _request_fix(code_text, instruction, tb_text)
            if fixed:
                confirm_and_run(fixed, instruction, attempt + 1)
                return
        copied = _copy_to_clipboard(u"错误：\n" + err_text + u"\n\n" + tb_text)
        show_error(u"执行出错，已回滚事务:\n" + err_text + u"\n\n" + tb_text[:1800] +
                   (u"\n\n（完整报错已复制到剪贴板，可直接粘贴）" if copied else u""))


def main():
    instruction = ask_instruction()
    if not instruction:
        return
    instruction = to_text(instruction)
    ctx = get_model_context()
    user_msg = (u"模型上下文:\n" + ctx + u"\n\n用户指令:\n" + instruction)
    try:
        reply = llm_client.chat(SYSTEM_PROMPT, user_msg, model="deepseek-chat")
    except Exception as e:
        show_error(u"调用 DeepSeek 失败: " + to_text(e))
        return

    code, _lang = llm_client.extract_code(reply, lang="python")
    if not code:
        show_error(u"DeepSeek 未返回可识别的 ```python 代码块。\n原始回复前 800 字:\n" +
                   to_text(reply or u"")[:800])
        return

    confirm_and_run(code, instruction)


if __name__ == "__main__":
    main()
else:
    # pyRevit 直接执行模块，确保入口被调用
    main()
