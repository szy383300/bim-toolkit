# -*- coding: utf-8 -*-
u"""ai_code_guard.py —— 对「AI 指令」生成的代码做执行前的静态扫描。

它拦什么、不拦什么
------------------
**拦**：越出"操作当前模型"这件事的代码 —— 文件读写、进程启动、网络、
        动态导入、加载任意 .NET 程序集。这些不是"AI 帮你改模型"需要的，
        一旦出现，几乎一定不是用户想要的结果。

**不拦**：正常的 Revit API 调用。删构件、改参数、建墙、开事务……
        这些是「AI 指令」的本职，只做**告警**，不阻止。

为什么不直接禁掉这个功能
------------------------
`AI 指令` 的价值就是"对模型做任意编辑"（例如"把所有标高1的墙厚度改成 200mm"）。
把它改成只能产出 genbuild 的 YAML，是**能力倒退**而不是升级 ——
YAML 配方表达的是"整栋楼怎么建"，表达不了"改现有构件"。
所以这里采取的是**收窄失控面**，而不是砍掉能力。

局限（必须知道）
----------------
这是**文本模式匹配**，不是沙箱。它能挡住明显的越界意图，
挡不住刻意绕过的写法。真正的隔离要靠进程/权限边界，不在本模块职责内。
"""
import re

# severity: "block" 直接拒绝执行 / "warn" 显著告警但仍允许
_BLOCK_RULES = [
    (r"__import__\s*\(", u"动态导入 __import__"),
    (r"\bclr\s*\.\s*AddReference\b", u"加载任意 .NET 程序集 (clr.AddReference)"),
    (r"\bSystem\s*\.\s*(IO|Diagnostics|Net|Reflection|Management)\b",
     u"直接使用 .NET 的文件/进程/网络/反射 API"),
    (r"\bProcess\s*\.\s*Start\b", u"启动外部进程"),
    (r"\bEnvironment\s*\.\s*Exit\b", u"退出进程"),
    (r"\bopen\s*\(", u"文件读写 open()"),
    (r"\b(eval|exec|compile)\s*\(", u"动态执行 eval/exec/compile"),
    (r"^\s*(import|from)\s+(os|sys|subprocess|shutil|socket|urllib|httplib|"
     r"requests|ctypes|pickle|glob|tempfile)\b",
     u"导入与「操作模型」无关的模块 (os/sys/subprocess/socket/…)"),
]

# 这些是「AI 指令」的本职，只告警
_WARN_RULES = [
    (r"\.\s*Delete\s*\(", u"调用 .Delete() 删除构件（不可逆，但可 Ctrl+Z）"),
    (r"\bSaveAs\b|\bdoc\s*\.\s*Save\s*\(", u"保存/另存文档"),
    (r"\bdoc\s*\.\s*Close\b", u"关闭文档"),
    (r"\bTransaction\s*\(", u"自己开了 Transaction（宿主已包一层，嵌套易出错）"),
    (r"\bTaskDialog\b|\bMessageBox\b|forms\s*\.\s*alert",
     u"弹窗（会阻塞 UI 线程）"),
    (r"\bPickObject\b|\bPickObjects\b", u"交互式选择（无人在旁时会挂住）"),
]

_BLOCK_RE = [(re.compile(p, re.M), why) for p, why in _BLOCK_RULES]
_WARN_RE = [(re.compile(p, re.M), why) for p, why in _WARN_RULES]


def _sample(code, m):
    u"""截取命中处的一小段，便于人核对。"""
    start = max(0, m.start() - 20)
    end = min(len(code), m.end() + 40)
    return code[start:end].replace(u"\n", u" ").strip()


def scan(code):
    u"""扫描代码，返回命中列表：[{severity, reason, sample}, ...]。

    同一规则只报一次（避免同一模式刷屏）。
    """
    hits = []
    if not code:
        return hits
    text = code
    try:
        if isinstance(text, bytes):
            text = text.decode(u"utf-8", u"replace")
    except Exception:
        pass

    for rx, why in _BLOCK_RE:
        m = rx.search(text)
        if m:
            hits.append({"severity": "block", "reason": why,
                         "sample": _sample(text, m)})
    for rx, why in _WARN_RE:
        m = rx.search(text)
        if m:
            hits.append({"severity": "warn", "reason": why,
                         "sample": _sample(text, m)})
    return hits


def blockers(hits):
    return [h for h in hits if h.get("severity") == "block"]


def warnings(hits):
    return [h for h in hits if h.get("severity") == "warn"]


def format_hits(hits, limit=6):
    u"""把命中列表排成人看的文本。"""
    lines = []
    for h in hits[:limit]:
        tag = u"⛔ 禁止" if h["severity"] == "block" else u"⚠️ 注意"
        lines.append(u"%s · %s\n     出现处：`%s`" % (tag, h["reason"], h["sample"]))
    if len(hits) > limit:
        lines.append(u"…还有 %d 条" % (len(hits) - limit))
    return u"\n".join(lines)
