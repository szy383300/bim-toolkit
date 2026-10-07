# -*- coding: utf-8 -*-
"""AutoCAD AI controller (CPython 3).

Uses pywin32 COM to drive a *running* AutoCAD 2010 instance:
    natural-language instruction -> DeepSeek -> AutoLISP -> SendCommand

Usage:
    python acad_ai.py "画一个半径 50 的圆"
    python acad_ai.py            # 交互式输入指令

Requirements:
    pip install pywin32
    AutoCAD 2010 已启动（本机 ProgID: AutoCAD.Application.18）
    DEEPSEEK_API_KEY 环境变量已设置（或在 lib/deepseek_config.json 填 api_key）
"""
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import llm_client

# AutoCAD 2010 对应版本号 18.0；先试精确 ProgID，再退化为通用 ProgID
AUTOCAD_PROGIDS = ["AutoCAD.Application.18", "AutoCAD.Application"]

SYSTEM_PROMPT = (
    "你是一名 AutoCAD 自动化专家，负责输出可在 AutoCAD 2010 命令行直接执行的 "
    "AutoLISP 代码。\n"
    "规则：\n"
    "1. 只输出一个 ```lisp 代码块，里面是完整、可直接执行的 AutoLISP 表达式。\n"
    "2. 用 (command \"_命令名\" 参数 ...) 形式调用 AutoCAD 命令；"
    "点坐标用 (list x y z)，如 (list 0 0 0)。\n"
    "3. 不要输出任何解释性文字，只输出代码块。\n"
    "4. 确保括号配对、表达式是完整可执行的。\n"
)


def connect():
    try:
        import win32com.client
    except ImportError:
        sys.stderr.write("缺少 pywin32，请先安装: pip install pywin32\n")
        sys.exit(1)
    for pid in AUTOCAD_PROGIDS:
        try:
            app = win32com.client.GetActiveObject(pid)
            return app
        except Exception:
            continue
    try:
        app = win32com.client.Dispatch("AutoCAD.Application")
        return app
    except Exception as e:
        sys.stderr.write("无法连接到 AutoCAD，请确认 AutoCAD 已启动: " + str(e) + "\n")
        sys.exit(1)


def main():
    instruction = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else ""
    if not instruction.strip():
        instruction = input("请输入对 AutoCAD 的自然语言指令: ").strip()
    if not instruction:
        sys.stderr.write("未提供指令。\n")
        sys.exit(1)

    print("正在调用 DeepSeek 生成 AutoLISP ...")
    try:
        reply = llm_client.chat(SYSTEM_PROMPT, instruction, model="deepseek-chat")
    except Exception as e:
        sys.stderr.write("调用 DeepSeek 失败: " + str(e) + "\n")
        sys.exit(1)

    code, _lang = llm_client.extract_code(reply, lang="lisp")
    if not code:
        code = reply.strip()
    print("\n===== 生成的 AutoLISP =====\n" + code + "\n===========================\n")

    ans = input("是否发送到 AutoCAD 执行? (y/N): ").strip().lower()
    if ans not in ("y", "yes"):
        print("已取消。")
        sys.exit(0)

    app = connect()
    doc = app.ActiveDocument
    try:
        doc.SendCommand(code + "\n")
        print("已发送命令到 AutoCAD。")
    except Exception as e:
        sys.stderr.write("发送命令失败: " + str(e) + "\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
