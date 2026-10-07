# -*- coding: utf-8 -*-
u"""ai_advisor.py —— 把「规则引擎的判定结果」交给 LLM，生成解释与整改建议。

设计原则（**别改**，改了就不再是这个东西了）
------------------------------------------------
1. **规则负责判，AI 只负责解释。**
   通过 / 不通过 / 警告 全部由 `standards_checker` 决定。
   AI 说什么都不改变判定结论，也不参与打分。

2. **AI 输出永不回写模型。**
   本模块没有任何写 Revit 的能力，只返回一段文本。
   调用方拿到的是"给人看的建议"，不是"给机器执行的指令"。

3. **必须标注「AI 生成，需人复核」。**
   见 `DISCLAIMER`；调用方展示时必须带上。

4. **失败必须能降级。**
   无密钥 / 无网络 / 超时 / 返回格式异常 —— 一律返回 `(None, 原因)`，
   调用方继续展示规则报告。**绝不让 AI 的失败影响审查本身。**

为什么要拆成"规则判 + AI 解释"
------------------------------
规则引擎的判定是确定的、可复现的、可追责的；但它写死的 `suggestion` 是通用套话。
LLM 的价值在于把"你这条不达标"讲成人能看懂的整改路径与规范依据。
两者各干各擅长的事 —— 而不是让 LLM 去判合规（那既不可复现也无法追责）。
"""
import os

import llm_client

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "ai_advisor_config.json")

DISCLAIMER = (u"⚠️ **以下内容由 AI 生成，仅供参考，需人工复核。**"
              u"上方的判定结论来自规则引擎，不受本节影响；"
              u"AI 建议不会写入模型。")

DEFAULTS = {
    u"enabled": True,
    u"max_items": 12,      # 最多把多少条不合格/警告项喂给 AI
    u"max_tokens": 1200,
    u"timeout": 60,        # 秒。UI 线程同步阻塞，别设太大
}

SYSTEM_PROMPT = (
    u"你是建筑规范审查助手。给定「规则引擎已经判定过」的不合格项与警告项，"
    u"你的任务是给出**可执行的整改建议与规范依据**。\n"
    u"\n"
    u"硬性约束（违反即为错误输出）：\n"
    u"1. 不得质疑、不得修改判定结论。通过与否由规则引擎决定，你无权更改。\n"
    u"2. **不得编造条文号**。不确定具体条款时，写「需查证」并说明要查什么，"
    u"绝不允许凭印象编一个 GB 编号出来。\n"
    u"3. 只输出给人看的文字，**不要输出任何 Revit 操作代码或脚本**。\n"
    u"4. 按整改优先级排序，最多 8 条，每条 1~3 行，直接说「改什么、怎么改、依据什么」。\n"
    u"5. 用简体中文。不要复述输入，不要写客套话。\n"
)


def load_config():
    u"""读 ai_advisor_config.json；读不到就用默认值（enabled=True）。"""
    cfg = dict(DEFAULTS)
    try:
        import json
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "rb") as f:
                raw = f.read()
            if raw[:3] == b"\xef\xbb\xbf":
                raw = raw[3:]
            data = json.loads(raw.decode("utf-8"))
            for k in cfg.keys():
                if k in data and data[k] is not None:
                    cfg[k] = data[k]
    except Exception:
        pass
    return cfg


def is_enabled():
    return bool(load_config().get(u"enabled", True))


def _num(v, default, lo, hi):
    try:
        n = int(v)
    except Exception:
        return default
    if n < lo:
        return lo
    if n > hi:
        return hi
    return n


def build_prompts(report, doc_title=None, max_items=None):
    u"""把规则报告压成提示词。返回 (system, user)。"""
    cfg = load_config()
    cap = _num(max_items if max_items is not None
               else cfg.get(u"max_items"), 12, 1, 50)

    params = report.get(u"model_params") or {}
    checks = report.get(u"checks") or []

    want = [c for c in checks
            if c.get(u"status") in (u"fail", u"warning")][:cap]

    lines = []
    lines.append(u"## 模型概况")
    lines.append(u"- 文档: %s" % (doc_title or u"(未提供)"))
    lines.append(u"- 层数: %s" % params.get(u"total_floors", u"?"))
    lines.append(u"- 建筑高度(mm): %s" % params.get(u"building_height_mm", u"?"))
    lines.append(u"- 墙厚(mm): %s" % params.get(u"wall_thickness_mm", u"?"))
    lines.append(u"- 估算面积(m2): %s" % params.get(u"total_area_sqm", u"?"))
    lines.append(u"")
    lines.append(u"## 规则引擎的判定结果（不可更改）")
    lines.append(u"- 综合评分: %s (%s)" % (report.get(u"score"),
                                          report.get(u"grade")))
    lines.append(u"- 不合格: %s 项 | 警告: %s 项 | 总检查: %s 项"
                 % (report.get(u"fail_count"), report.get(u"warn_count"),
                    len(checks)))
    lines.append(u"")
    if not want:
        lines.append(u"（没有不合格项，也没有警告项。）")
    else:
        lines.append(u"## 需要你给整改建议的项（共 %d 条，已截取前 %d 条）"
                     % (len([c for c in checks
                             if c.get(u"status") in (u"fail", u"warning")]),
                        len(want)))
        for i, c in enumerate(want):
            lines.append(u"%d. [%s] %s" % (i + 1,
                                           (c.get(u"status") or u"").upper(),
                                           c.get(u"item")))
            lines.append(u"   - 现象: %s" % c.get(u"message"))
            lines.append(u"   - 规则给的依据: %s" % c.get(u"standard"))
            lines.append(u"   - 规则的通用建议: %s" % c.get(u"suggestion"))
    lines.append(u"")
    lines.append(u"请针对以上每一类问题，给出具体的整改做法与规范依据（不确定的写「需查证」）。")
    return SYSTEM_PROMPT, u"\n".join(lines)


def advise(report, doc_title=None):
    u"""请求 AI 建议。返回 (文本, None) 或 (None, 失败原因)。

    任何失败都只是"这一节没内容"，不影响规则报告本身。
    """
    cfg = load_config()
    if not cfg.get(u"enabled", True):
        return None, u"AI 建议已在 ai_advisor_config.json 里关闭"

    if not report:
        return None, u"没有可分析的审查结果"

    try:
        system_prompt, user_prompt = build_prompts(report, doc_title)
    except Exception as e:
        return None, u"构造提示词失败: %s" % e

    try:
        text = llm_client.chat(
            system_prompt, user_prompt,
            max_tokens=_num(cfg.get(u"max_tokens"), 1200, 200, 4000),
            temperature=0.2,
            timeout=_num(cfg.get(u"timeout"), 60, 10, 180))
    except Exception as e:
        return None, u"调用 AI 失败: %s" % e

    text = (text or u"").strip()
    if not text:
        return None, u"AI 返回了空内容"
    return text, None
