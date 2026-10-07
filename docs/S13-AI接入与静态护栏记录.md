# S13 执行记录 —— AI 真接入（规则判、AI 解释）+ AI 指令静态护栏

- 执行时间：2026-10-01 18:28 ~ 18:31
- 队列位置：[增强方案 §5](BIM工具箱-现状调查与增强方案.md) 的 **S13**（原 C2 + C6）
- 结果：C2 完成；**C6 经核实不成立，已换成真正降低风险的方案**（见 §一）

---

## 一、又一处方案纠正：C6「AI 指令改产 YAML」不成立

原计划：

> **C6**：把 `AI 指令` 从「生成代码」升级为「生成配置」……既然已有了 genbuild 的
> YAML schema，更稳的做法是让 LLM **只产出 YAML**，再由校验器把关后执行。

动手前核了一遍 `AI 指令` 的 `SYSTEM_PROMPT`（`script.py:50-89`），它整个是**任意模型编辑**：

```
"把所有标高1的墙厚度改成200mm"
坑A：IronPython 对 Wall.Create 的 4 参重载解析不可靠…
坑B：所有 IList[Curve] 版本都是『轮廓墙』，要求闭合轮廓…
坑C：幕墙/叠层墙类型的 GetCompoundStructure() 返回 None…
只读参数陷阱：WALL_ATTR_WIDTH_PARAM 在 WallType 上是只读的…
```

而 genbuild 的 YAML schema 是**整栋楼配方**（`levels` / `walls` / `floors` / `stairs` /
`roofs` / `railings` / `materials`）。它**表达不了"改现有构件的厚度"**。

**结论**：把 `AI 指令` 改成只产 YAML，等于把一个"操作模型"的通用工具
换成一个"按配方盖楼"的窄工具 —— 这是**能力倒退**，不是升级。

所以 C6 换成真正对症的做法：**收窄失控面，而不是砍掉能力**（见 §三）。

---

## 二、S13a：规则判、AI 解释（C2）

### 分工是刻意的

| | 谁负责 | 为什么 |
|---|---|---|
| **判定**（通过/不合格/警告/评分） | `standards_checker` 规则引擎 | 必须确定、可复现、可追责 |
| **解释**（整改路径、规范依据） | LLM | 规则写的 `suggestion` 是通用套话，AI 能讲成人看得懂的 |

**绝不让 LLM 去判合规** —— 那既不可复现，也无法追责。

### 三条硬约束（写进了 `ai_advisor.py` 的模块注释）

1. **规则负责判，AI 只负责解释。** AI 说什么都不改判定结论，也不参与打分。
2. **AI 输出永不回写模型。** 该模块没有任何写 Revit 的能力，只返回文本。
3. **必须标注「AI 生成，需人复核」。** 见 `DISCLAIMER`，展示时强制带上。

外加第 4 条工程要求：**失败必须能降级** —— 无密钥/无网络/超时/空回复，
一律返回 `(None, 原因)`，调用方继续展示规则报告。

### 系统提示词里的两条关键禁令

```
1. 不得质疑、不得修改判定结论。通过与否由规则引擎决定，你无权更改。
2. 不得编造条文号。不确定具体条款时，写「需查证」…
   ……绝不允许凭印象编一个 GB 编号出来。
```

> 第 2 条尤其重要：LLM 编造规范条文号是这类应用最典型的"看起来专业、其实有害"的失败模式。

### 新增/改动文件

| 文件 | 说明 |
|---|---|
| `lib/ai_advisor.py` | 新增。提示词构造 + 调用 + 降级 |
| `lib/ai_advisor_config.json` | 新增。`enabled` / `max_items` / `max_tokens` / `timeout` |
| `lib/llm_client.py` | `chat()` / `_post()` 支持可选 `timeout` |
| `AI 智建.panel/规范审查.pushbutton/script.py` | 规则报告之后追加 AI 节 |

**为什么给 `llm_client` 加 timeout**：原来硬编码 120 秒，而这调用是在
**Revit UI 线程上同步阻塞**的 —— 120 秒足以让 Revit 看起来像卡死。
现在 `ai_advisor` 传 60 秒（可配，10~180）。

### 展示顺序（很重要）

```
1. 先出规则报告（判定权威：评分 / 逐项状态 / 规则建议）
2. 再出「AI 整改建议（仅供参考）」节：
     有内容 -> 先打 DISCLAIMER，再打正文
     失败   -> 打"未生成：<原因>"，并说明"这不影响上面的判定结论"
3. TaskDialog 里注明 "AI 建议: 已生成 / 未生成（原因）"
```

### 降级验证（实测）

```
无密钥时：文本=None
         原因=调用 AI 失败: 未配置 DeepSeek API Key。请设置环境变量
              DEEPSEEK_API_KEY，或在 lib/deepseek_config.json 中填写 api_key 字段。
         enabled=True
         降级正确 = True
```

---

## 三、S13b：`AI 指令` 的执行前静态护栏（替代 C6）

新增 `lib/ai_code_guard.py`。生成代码在**执行前**扫一遍：

| 级别 | 命中 | 处理 |
|---|---|---|
| **block** | `open()`、`__import__`、`import os/sys/subprocess/shutil/socket/urllib/ctypes/…`、`clr.AddReference`、`System.IO/.Diagnostics/.Net/.Reflection/.Management`、`Process.Start`、`Environment.Exit`、`eval/exec/compile` | **拒绝执行** + 说明越界在哪 |
| **warn** | `doc.Delete()`、`SaveAs`/`Save`、`doc.Close`、自己开 `Transaction`、`TaskDialog`/`MessageBox`、`PickObject(s)` | 在确认框里显著列出，**仍可执行** |

**分界线**：只拦"越出操作当前模型"的能力（文件/进程/网络/动态导入/加载程序集）；
正常的 Revit API 调用一律放行 —— 删构件、改参数、建墙正是这个按钮的本职，只告警。

### 实测

```
正常改参（WallKind.Basic + 8 参重载模板） : block=0 warn=0   ← 不误伤
文件读写 open(...)                        : block=1
动态导入 __import__("os")                 : block=1
加载程序集 clr.AddReference("System.IO")  : block=2
删除构件 doc.Delete(ids)                  : block=0 warn=1   ← 本职，只告警
弹窗 TaskDialog.Show(...)                 : block=0 warn=1
```

### 局限（写进了模块注释，不藏着）

> 这是**文本模式匹配，不是沙箱**。它能挡住明显的越界意图，
> 挡不住刻意绕过的写法。真正的隔离要靠进程/权限边界，不在本模块职责内。

---

## 四、测试

在 `ironpython_harness.py` 的逻辑断言区新增两条：

```
PASS  logic: ai_code_guard 静态护栏（拦越界 / 放行本职）
PASS  logic: ai_advisor 提示词（规则判/AI 解释，只带不合格项）
```

第二条同时钉住三件事：
- 不合格项**进**提示词（`疏散距离`、`窗墙比`）
- 合格项**不进**提示词（`层高`）—— 省 token，也让 AI 聚焦
- 系统提示词里必须有「不得编造条文号」与「不得质疑（判定结论）」

---

## 五、部署与验证

| 项 | 结果 |
|---|---|
| 部署后自校验 | ✅ **78 个文件全等**（75 → 78，新增 3 个 lib 文件） |
| `lib/ai_advisor.py` / `ai_advisor_config.json` / `ai_code_guard.py` | ✅ 均已部署 |
| selftest 八关，默认 / `--strict` | ✅ 双 PASS，exit=0 |
| harness | ✅ ALL PASS（19 个按钮 + 8 条逻辑断言） |
| 安装目录 version.txt | `20261001-183022  deploy  38d2c06  v2.0.0` |

---

## 六、遗留

| 项 | 说明 |
|---|---|
| 「AI 智建」面板命名 | 7 个按钮里只有「规范审查」调 LLM（且只用于解释）。面板名仍会让人以为 7 个都"智能判断"。**改名是产品决策**，未擅自改 |
| AI 建议的端到端 | 只验证了"提示词构造正确"与"失败降级正确"。**真正调通 DeepSeek 拿到建议，需要一次真实请求**（本次未做，避免消耗额度；用户可在 Revit 里点一次验证） |
| 护栏挡不住刻意绕过 | 见 §三末 |
| 规范审查仍是 UI 线程同步阻塞 | 超时已从 120s 降到 60s，但仍是阻塞。彻底解决要改成异步 + 进度提示 |

---

## 七、队列进度

| 序 | 项 | 状态 |
|---|---|---|
| S1~S12 | 见各自记录 | ✅（S2 暂缓） |
| **S13** | **AI 真接入（规则判/AI 解释）+ 静态护栏** | ✅（原 C6 经核实**不成立**，已换方案） |
| S14 | 翻模补齐（梁 + PDF/YOLO 入口） | ⬜ 下一站 |
| S15 | 4D 播放 | ⬜ |
| S16 | 解释器收敛 / 归档 / CI | ⬜ |
