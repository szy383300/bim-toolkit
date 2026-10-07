# -*- coding: utf-8 -*-
"""
阶段 0 环境自检 —— 建筑信息化工具链验证
验证目标：
  1) Python / bim-dev 虚拟环境
  2) pywin32（AutoCAD COM 自动化）
  3) AutoCAD.Application COM 真实连接（取版本后退出）
  4) Revit 安装目录 + RevitAPI.dll 存在性（API 面可用）
  5) pythonnet 可用性（Revit API 的 .NET 调用通道）
  6) pyRevit 是否安装（Revit 二次开发的标准宿主）

说明：Revit 的 .NET API 是进程内 API，只能在 Revit 进程里被调用，
因此“真正的 Revit API 脚本”要走 pyRevit（IronPython）或 Revit 加载项，
而不是 standalone pythonnet。此脚本只做“可达性”验证，不强行在外部加载。
"""
import os
import sys
import importlib.util

SEP = "=" * 64
ok, fail, warn = "PASS", "FAIL", "WARN"


def line(t):
    print(t)


def check(name, cond, detail=""):
    tag = ok if cond else fail
    print(f"[{tag}] {name}" + (f"  -> {detail}" if detail else ""))
    return cond


print(SEP)
print("阶段 0 环境自检  |  bim-dev 工具链")
print(SEP)

# ---- 1. Python / venv ----
print("\n--- 1. Python / 虚拟环境 ---")
print(f"Python: {sys.version.split()[0]}  ({sys.executable})")
in_venv = getattr(sys, "base_prefix", sys.prefix) != sys.prefix
check("运行于虚拟环境(bim-dev)", in_venv, sys.prefix if in_venv else "未在 venv 中")

# ---- 2. pywin32 ----
print("\n--- 2. pywin32 (AutoCAD COM) ---")
pywin32 = importlib.util.find_spec("win32com") is not None
check("pywin32 已安装", pywin32)
if pywin32:
    import win32com.client
    print("     导入 win32com.client 成功")

# ---- 3. AutoCAD COM 真实连接 ----
print("\n--- 3. AutoCAD.Application COM 连接 ---")
acad_ver = None
if pywin32:
    dispatched = False
    for prog_id in ("AutoCAD.Application", "AutoCAD.Application.18"):
        try:
            acad = win32com.client.Dispatch(prog_id)
            dispatched = True
            try:
                acad_ver = acad.Version
            except Exception:
                acad_ver = "(Version 属性不可读)"
            print(f"[{ok}] 已连接 COM: {prog_id}  (AutoCAD 版本: {acad_ver})")
            # 立即退出，避免留下进程
            try:
                acad.Quit()
                print(f"[{ok}] 已向 AutoCAD 发送 Quit，未残留进程")
            except Exception as e:
                print(f"[{warn}] 已连接但 Quit 失败: {e}（请手动关闭弹出的 AutoCAD）")
            break
        except Exception as e:
            print(f"[{warn}] Dispatch({prog_id}) 失败: {e}")
    if not dispatched:
        print(f"[{fail}] 无法创建/连接 AutoCAD COM 对象")
        print("       排查：AutoCAD 2010 是否为 64 位？本脚本用 64 位 Python，")
        print("       若 AutoCAD 是 32 位则需在 32 位 Python 中调用；")
        print("       或确认 AutoCAD 已激活且 COM 服务已注册。")
else:
    print(f"[{fail}] pywin32 缺失，跳过 AutoCAD COM 测试")

# ---- 4. Revit 安装 + RevitAPI.dll ----
print("\n--- 4. Revit 安装与 API 程序集 ---")
candidates = [
    r"D:\Autodesk\Revit 2019",
    r"D:\Autodesk\Revit 2019\Program",
    r"C:\Program Files\Autodesk\Revit 2019",
    r"C:\Program Files\Autodesk\Revit 2019\Program",
]
revit_dir = next((c for c in candidates if os.path.isfile(os.path.join(c, "RevitAPI.dll"))), None)
check("RevitAPI.dll 存在（API 面可用）", revit_dir is not None,
      revit_dir if revit_dir else "未在常见路径找到")
if revit_dir:
    ui_dll = os.path.isfile(os.path.join(revit_dir, "RevitAPIUI.dll"))
    check("RevitAPIUI.dll 存在（UI 扩展点）", ui_dll)
    exe = os.path.join(os.path.dirname(revit_dir) if revit_dir.endswith("Program") else revit_dir, "Revit.exe")
    check("Revit.exe 主程序存在", os.path.isfile(exe), exe if os.path.isfile(exe) else "未找到")

# ---- 5. pythonnet ----
print("\n--- 5. pythonnet (Revit API .NET 通道) ---")
has_net = importlib.util.find_spec("clr") is not None
check("pythonnet(clr) 已安装", has_net)
if has_net:
    try:
        import clr
        print("     导入 clr 成功（pythonnet 就绪）")
        # 尝试把 Revit API 加为引用——外部进程通常失败，属预期，仅探测
        if revit_dir:
            try:
                clr.AddReference(os.path.join(revit_dir, "RevitAPI.dll"))
                print(f"[{ok}] 外部加载 RevitAPI.dll 成功（罕见，说明允许外部调用）")
            except Exception as e:
                print(f"[{warn}] 外部加载 RevitAPI.dll 失败（预期内）: {type(e).__name__}")
                print("       这是正常现象：Revit API 是进程内 API，")
                print("       只能在 Revit 进程内通过 pyRevit / 加载项调用。")
    except Exception as e:
        print(f"[{fail}] 导入 clr 失败: {e}")

# ---- 6. pyRevit（Revit 二开标准宿主）----
print("\n--- 6. pyRevit（Revit 二次开发宿主）---")
appdata = os.environ.get("APPDATA", "")
pyrevit_paths = [
    os.path.join(appdata, "pyRevit"),
    r"C:\Program Files\pyRevit",
    r"C:\ProgramData\pyRevit",
]
pyrevit_found = next((p for p in pyrevit_paths if os.path.isdir(p)), None)
if pyrevit_found:
    check("pyRevit 已安装", True, pyrevit_found)
else:
    check("pyRevit 已安装", False)
    print("     未检测到 pyRevit —— 这是 Revit 二次开发最顺手的入口，")
    print("     建议安装：https://github.com/pyrevitlabs/pyRevit （支持 Revit 2017–2021）")
    print("     装好后即可在 Revit 内用 IronPython 写插件/面板，无需 standalone 加载。")

# ---- 总结 ----
print("\n" + SEP)
print("自检结论")
print(SEP)
if acad_ver:
    print(f"AutoCAD COM : 可用（版本 {acad_ver}），可用 Python 驱动批量出图/标注")
else:
    print("AutoCAD COM : 不可用 —— 见上方排查建议")
print(f"Revit API   : {'程序集就绪（需 pyRevit/加载项在 Revit 内调用）' if revit_dir else '未找到 RevitAPI.dll'}")
print(f"下一步建议  : AutoCAD 自动化现在就能练；Revit 二开请先装 pyRevit，")
print(f"              再在《规划》阶段 3 用 pyRevit 写第一个 Revit 面板。")
print(SEP)
