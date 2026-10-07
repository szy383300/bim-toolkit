# -*- coding: utf-8 -*-
"""AutoCAD AI 控制器 - WinForms 对话框版 (CPython 3 + pythonnet + pywin32)。

用法：
    python acad_ai_gui.py

运行后会弹出一个原生 Windows 对话框：输入自然语言指令 -> 生成 AutoLISP ->
检查代码 -> 点击"执行到 AutoCAD"。

要求：
    - AutoCAD 2010 已启动（本机 ProgID: AutoCAD.Application.18）
    - 已安装 pywin32 与 pythonnet
    - DEEPSEEK_API_KEY 环境变量（或 lib/deepseek_config.json 的 api_key）已配置
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")
from System.Windows.Forms import (Form, TextBox, Button, Label,
                                  Application, MessageBox, MessageBoxButtons,
                                  ScrollBars, BorderStyle, FormStartPosition)
from System.Drawing import Point, Color, Size

import llm_client

AUTOCAD_PROGIDS = ["AutoCAD.Application.18", "AutoCAD.Application"]

SYSTEM_PROMPT = (
    "你是一名 AutoCAD 自动化专家，负责输出可在 AutoCAD 2010 命令行直接执行的 "
    "AutoLISP 代码。\n"
    "规则：\n"
    "1. 只输出一个 ```lisp 代码块，里面是完整、可直接执行的 AutoLISP 表达式。\n"
    "2. 用 (command \"_命令名\" 参数 ...) 形式调用 AutoCAD 命令；"
    "点坐标用 (list x y z)，例如 (list 0 0 0)。\n"
    "3. 不要输出任何解释性文字，只输出代码块。\n"
    "4. 确保括号配对、表达式是完整可执行的。\n"
)


def connect_acad():
    try:
        import win32com.client
    except ImportError:
        MessageBox.Show("缺少 pywin32，请先安装: pip install pywin32", "错误",
                        MessageBoxButtons.OK)
        sys.exit(1)
    for pid in AUTOCAD_PROGIDS:
        try:
            return win32com.client.GetActiveObject(pid)
        except Exception:
            continue
    try:
        return win32com.client.Dispatch("AutoCAD.Application")
    except Exception as e:
        MessageBox.Show("无法连接到 AutoCAD，请确认 AutoCAD 已启动: " + str(e),
                        "错误", MessageBoxButtons.OK)
        sys.exit(1)


class MainForm(Form):
    def __init__(self):
        super(MainForm, self).__init__()
        self.Text = "AI 指令 - AutoCAD"
        self.Width = 720
        self.Height = 540
        self.StartPosition = FormStartPosition.CenterScreen
        self.MinimizeBox = True
        self.MaximizeBox = True

        # ---- 指令输入区 ----
        lbl_inst = Label()
        lbl_inst.Text = "输入自然语言指令："
        lbl_inst.Location = Point(12, 12)
        lbl_inst.Size = Size(200, 20)
        self.Controls.Add(lbl_inst)

        self.tb_inst = TextBox()
        self.tb_inst.Multiline = True
        self.tb_inst.ScrollBars = ScrollBars.Both
        self.tb_inst.Location = Point(12, 34)
        self.tb_inst.Size = Size(680, 90)
        self.tb_inst.BackColor = Color.White
        self.tb_inst.ForeColor = Color.Black
        self.tb_inst.BorderStyle = BorderStyle.FixedSingle
        self.Controls.Add(self.tb_inst)

        # ---- 生成按钮 ----
        self.btn_gen = Button()
        self.btn_gen.Text = "生成 AutoLISP"
        self.btn_gen.Location = Point(12, 134)
        self.btn_gen.Size = Size(120, 30)
        self.btn_gen.Click += self.on_generate
        self.Controls.Add(self.btn_gen)

        # ---- 代码显示区 ----
        lbl_code = Label()
        lbl_code.Text = "生成的 AutoLISP（可手动编辑）："
        lbl_code.Location = Point(12, 172)
        lbl_code.Size = Size(260, 20)
        self.Controls.Add(lbl_code)

        self.tb_code = TextBox()
        self.tb_code.Multiline = True
        self.tb_code.ScrollBars = ScrollBars.Both
        self.tb_code.Location = Point(12, 194)
        self.tb_code.Size = Size(680, 220)
        self.tb_code.BackColor = Color.White
        self.tb_code.ForeColor = Color.Black
        self.tb_code.BorderStyle = BorderStyle.FixedSingle
        self.Controls.Add(self.tb_code)

        # ---- 执行按钮 ----
        self.btn_run = Button()
        self.btn_run.Text = "执行到 AutoCAD"
        self.btn_run.Location = Point(12, 424)
        self.btn_run.Size = Size(140, 30)
        self.btn_run.Enabled = False
        self.btn_run.Click += self.on_execute
        self.Controls.Add(self.btn_run)

        # ---- 状态栏 ----
        self.lbl_status = Label()
        self.lbl_status.Text = ""
        self.lbl_status.Location = Point(164, 430)
        self.lbl_status.Size = Size(520, 20)
        self.Controls.Add(self.lbl_status)

        # 预填示例
        self.tb_inst.Text = ("画一个半径 50 的圆，圆心在 0,0")

    def on_generate(self, sender, args):
        instruction = self.tb_inst.Text.strip()
        if not instruction:
            MessageBox.Show("请先输入指令。", "提示", MessageBoxButtons.OK)
            return
        self.lbl_status.Text = "调用 DeepSeek 中..."
        self.btn_gen.Enabled = False
        try:
            reply = llm_client.chat(SYSTEM_PROMPT, instruction,
                                    model="deepseek-chat", max_tokens=2000)
            code, _lang = llm_client.extract_code(reply, lang="lisp")
            if not code:
                code = reply.strip()
            self.tb_code.Text = code
            self.btn_run.Enabled = True
            self.lbl_status.Text = "生成完成，可编辑后点击执行"
        except Exception as e:
            MessageBox.Show(str(e), "调用 DeepSeek 失败", MessageBoxButtons.OK)
            self.lbl_status.Text = "生成失败"
        finally:
            self.btn_gen.Enabled = True

    def on_execute(self, sender, args):
        code = self.tb_code.Text.strip()
        if not code:
            return
        try:
            app = connect_acad()
            doc = app.ActiveDocument
            doc.SendCommand(code + "\n")
            self.lbl_status.Text = "已发送到 AutoCAD"
        except Exception as e:
            MessageBox.Show(str(e), "执行失败", MessageBoxButtons.OK)
            self.lbl_status.Text = "执行失败"


def main():
    Application.EnableVisualStyles()
    Application.SetCompatibleTextRenderingDefault(False)
    form = MainForm()
    form.ShowDialog()


if __name__ == "__main__":
    main()
