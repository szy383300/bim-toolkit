# 缺陷修复方案设计（2026-10-02）

针对实建三层社区服务中心时撞出的缺陷，先设计再动手。
所有结论都基于**在活着的 Revit 2019 上实测**，不是从注释推断。

---

## 缺陷清单

| # | 缺陷 | 后果 | 状态 |
|---|---|---|---|
| 1 | `model_builder.py` 用 `from System.Runtime.CompilerServices import StrongBox` | 「一键建模」「一键演示」一点就炸 | 已修，方案待加固 |
| 2 | 屋面静默退化成**楼板** | 屋面语义丢失；验收 `OST_Roofs: 0` | 待修 |
| 3 | 验收 `expect.floors` 按类别计数太粗 | 分不清"多出来的是平台还是错件" | 待修 |

---

## 一、实测得到的三条硬事实

设计前先取证，三条都推翻了或修正了原有假设：

### 事实 A：真屋面 API 这条路**确实走不通**

在活着的桥上调 `create_roof_gable`（内置 `NewFootPrintRoof`）：

```
"error": "NewFootPrintRoof failed: Value cannot be null. | Value cannot be null.",
"diag":  "CurveArray/Level/RoofType 均已核验非空；2019 唯一重载 =
         NewFootPrintRoof(CurveArray, Level, RoofType, out ModelCurveArray)。
         异常来自 Revit 内部，非参数缺失"
```

→ 项目注释里"17 次实证全死"**是真的**。**不要试图去修 `NewFootPrintRoof`**，
那是 Revit 2019 内部行为，不在这轮可控范围内。

### 事实 B：`DirectShape` **可以**建到 `OST_Roofs` 类别

```
OST_Roofs        → {ok: true, id: 338527, category: "屋顶",     class: "DirectShape"}
OST_GenericModel → {ok: true, id: 338528, category: "常规模型", class: "DirectShape"}
```

→ 这是**整个方案的天花板所在**：屋面做不成参数化 Roof，但**可以归到正确类别**。

### 事实 C：`macro` 是个**未校验、未文档化的魔法键**

`engine.py:262` 是唯一的分叉点：

```python
if rf.get(u"macro") == u"freeform":
    res = ctx.run_code(macros.gable_freeform(g))   # 唯一能用的路
else:
    res = ctx.run_code(macros.gable_roof(g))       # 8 变体全死 → 静默退化成 Floor
```

而 `spec.py` 里**搜不到 `macro` 这个词** —— 不校验、不透传、不报错。
各配方的实际情况：

| 配方 | 写了 `macro` 吗 | 屋面实际是什么 |
|---|---|---|
| 仙湖别墅 | ✅ `freeform` | 正确（但归 `常规模型`） |
| 四层白色建筑 | ✅ `freeform` | 正确（但归 `常规模型`） |
| 白色别墅 | ✅ `freeform` | 正确（但归 `常规模型`） |
| **四层别墅** | ❌ 无 | **楼板**（一直没人发现） |
| **三层社区服务中心** | ❌ 无 | **楼板** |

**"能用的路要靠一个没文档的键去开"，而默认值是那条已证明走不通的路 —— 这是根因。**

---

## 二、修复方案

### 缺陷 1：StrongBox 导入（加固）

**现方案**：

```python
try:
    from clr import StrongBox
except Exception:
    try:
        from System.Runtime.CompilerServices import StrongBox
    except Exception:
        StrongBox = None          # ← 问题在这
```

**问题**：兜底成 `None` 是把失败**推迟**到使用处，届时只会看到
`TypeError: 'NoneType' object is not subscriptable` —— 又一个难查的错。
这个模块没有 StrongBox 就**根本无法工作**，所以应该**立刻、明确地失败**。

**改**：两级导入都失败时抛 `ImportError`，报文里写清楚试过哪两种写法、为什么、
以及去哪看正确写法（`bridge_core.py` 的用法）。

### 缺陷 2：屋面退化成楼板（核心）

分四步，每步独立可验：

#### 2a. `gable_freeform` 把屋面建到 `OST_Roofs`

`macros.py:816` 现在写死 `OST_GenericModel` → 改成 `OST_Roofs`（事实 B 已证可行）。

**收益**：屋面进正确的类别，屋面视图/过滤器/明细表都能找到它，
`OST_Roofs` 计数正常。

**代价**：DirectShape 仍是"体量替身"，不是参数化 Roof —— 不能编辑坡度、
不参与屋顶连接。**这一点必须写进注释，不能让人误以为它是真屋面。**

#### 2b. 默认改走 `freeform`

`engine.py:262` 的判据反转：

```python
macro = (rf.get(u"macro") or u"freeform").lower()   # 默认 freeform
if macro == u"freeform":
    → gable_freeform
elif macro == u"gable":
    → gable_roof（老路，仅显式要求时）
else:
    → 报错并指出合法取值
```

**理由**：老路已被"17 次实证 + 本次活体复现"证明走不通，
**默认值不该是一条死路**。三条既有配方写的 `freeform` 行为不变；
两条没写的（四层别墅、三层社区服务中心）**自动被修正**。

**兼容性**：这是行为变更，按仓库约定要 bump `BRIDGE_VERSION`？
—— 不涉及桥，是 genbuild 侧，改 `genbuild` 版本号/记录即可。

#### 2c. 老路退化成 Floor 时必须**喊出来**

`gable_roof` 的 8 变体全失败后建的 `Floor` 是把失败**伪装成成功**
（返回了 id，引擎就把它当屋面记进 `pools["roofs"]`）。

**改**：
- `gable_roof` 的返回值里加 `"degraded": "floor"` 标记
- `engine._stage_roofs` 看到该标记就**打显著警告**并把它记进
  `ctx.warnings`（而非静默计入成功）
- 验收阶段不再把它算作屋面的达成

#### 2d. `spec.py` 校验 `macro`

- 加入 `VALID_ROOF_MACROS = (u"freeform", u"gable")`
- `_check_roof_spec` 校验取值，非法值**报错**而不是静默走 else
- `stats` 里带上实际用到的 macro，干跑时可见

### 缺陷 3：`expect.floors` 判据太粗

`OST_Floors` 天然含**楼梯平台**（Revit 语义如此），所以"3 期望 vs 6 实际"
里 2 个是合法的、1 个是错的 —— 光看计数分不清。

**改（轻量）**：验收阶段对 floors 不只看总数，**输出构成明细**
（有几块是楼板、几块是平台、几块是别的），让不匹配**可诊断**。
不做成硬性"必须等于 N"，因为那需要区分元素子类，成本高、
且真实 Revit 里平台本来就该算楼板。

---

## 三、验收标准

修完必须满足：

| # | 验收项 | 方法 |
|---|---|---|
| 1 | 「一键建模」不再 ImportError | 真 Revit 无头跑 `build_from_params.py`，`fail_count=0` |
| 2 | 重建三层社区服务中心，`OST_Roofs ≥ 1` | `gen_build.py` 后查类别计数 |
| 3 | 屋面元素 `category=屋顶` | `get_element` 逐元素查 |
| 4 | 老路退化成 Floor 时**有警告** | 造一个 `macro: gable` 的配方试 |
| 5 | 非法 `macro` 值被拒绝 | 干跑 `macro: nonsense` |
| 6 | 既有 3 个 freeform 配方行为不变 | 干跑校验通过 |
| 7 | 八关测试 + CI 仍全绿 | `tools/ci.ps1` |

**注意**：`expect.roofs` 不能拿 `OST_Roofs` 做等同判断 —— 2a 之后是成立的，
但若将来有人走 `macro: gable` 仍会退化。所以 2c 的标记比计数更可靠。

---

## 四、不做的事（以及为什么）

| 不做 | 原因 |
|---|---|
| 修 `NewFootPrintRoof` | 事实 A：异常来自 Revit 内部，非参数问题。改代码解决不了 |
| 把 DirectShape 说成"真屋面" | 它不是。注释与文档必须写明是**体量替身** |
| 给 6 个解释器做合并 | 与本缺陷无关（S16 已说明那是仓库外的运维决定） |
| 重构 `gable_roof` 的 8 变体 | 它已是死路，保留只为兼容；重点是让它**别再假装成功** |

---

# 五、实施与验证结果（全部通过）

改动落在 5 个文件：

| 文件 | 改了什么 |
|---|---|
| `lib/model_builder.py` | StrongBox 两级导入都失败时**抛 ImportError**（原来兜底成 `None`，把失败推迟到使用处） |
| `mcp/genbuild/macros.py` | ① `gable_freeform` 类别 `OST_GenericModel` → **`OST_Roofs`**；② 老路兜底加 `degraded: "floor"` 标记 |
| `mcp/genbuild/engine.py` | ① `_stage_roofs` **默认改走 freeform**，只有 `macro: gable` 才走老路；② 见到 `degraded` 打显著告警；③ 验收 floors 不匹配时补诊断提示 |
| `mcp/genbuild/spec.py` | 新增 `VALID_ROOF_MACROS` 校验 `macro`；stats 增加 `roof_macros`（干跑可见） |
| `buildings/三层社区服务中心.yaml` | `expect` 按修复后口径修正（floors 5、roofs 1） |

## 验收 2/3：屋面归入 OST_Roofs

**修复前**（实测）：
```
id=333915  category=楼板  class=Floor  name=常规 - 150mm
[验收] OST_Roofs: 0     [验收] OST_Floors: 6
```

**修复后**（实测）：
```
屋顶类别里的元素: 1
  id=339643  category=屋顶  class=DirectShape
[验收] OST_Roofs: 1     [验收] OST_Floors: 5
```

最终验收**六项全过，exit=0**：
```
walls:   got=21 want=21 [OK]
doors:   got=13 want=13 [OK]
windows: got=29 want=29 [OK]
floors:  got=5  want=5  [OK]
stairs:  got=2  want=2  [OK]
roofs:   got=1  want=1  [OK]
[总耗时] 9.1s
```

## 验收 2b：默认就走 freeform —— 配方里**根本没写** macro

发射标签是 `屋面1-双坡体量`，不是老路的 `屋面1`：

```
[gen:屋面1-双坡体量] {"rise_mm": 3031.0, "id": 338808}
```

## 验收 4：老路退化**必须喊出来**

`macro: gable` 实跑，告警如期出现：

```
[gen:屋面1] {"variants": [...8 个 "Invalid profile."], "made": {"degraded": "flo...
[警告] 屋面1：真屋面 API 全部失败，已退化成【楼板】(id=339256)。
       它看着像屋面，但进不了屋面明细表、不被屋面过滤器找到，也不能编辑坡度。
       请改用 macro: freeform。
[验收] roofs: got=0 want=1 [FAIL]
```

**这条日志就是本次修复最想留下的东西** —— 同样的情况以前是**静默发生**的。

## 验收 5：非法 macro 被拒绝

```
[校验] ... "roof_macros": ["nonsense"]
[问题] roof.macro 须为 freeform/gable 之一, got nonsense
[中止] 干跑发现 1 个问题, 不执行
```

## 验收 6：既有配方不受影响

7 个配方干跑**全部 exit=0**。仙湖别墅的 3 处 freeform 屋面照常：

```
[校验] ... "roofs": 3, "roof_macros": ["freeform", "freeform", "freeform"]
```

**顺带修正**：四层别墅此前也没写 macro（屋面同样是楼板），
默认值改对后它**自动被修复**，无需改配方。

## 验收 1：StrongBox 修复

真 Revit 无头跑 `build_from_params.py`：
```
### 1/7 创建标高...  标高: 4 个
...
统计: {"fail_count": 0, ...}
=== 完成 ===
```

## 验收 7：门禁

| 项 | 结果 |
|---|---|
| `tools/ci.ps1` | ✅ 三关全绿 |
| genbuild 单元测试 | ✅ 31 passed |
| 部署一致性 | ✅ 79 文件全等（`20261002-074300 deploy 76258b0`） |

---

# 六、这次修复真正的教训

**根因不是"某个 API 用错了"，而是三个层层叠加的设计问题：**

1. **默认值是一条死路** —— `macro` 缺省走 `gable`，而 `gable` 已被证明必死
2. **失败被伪装成成功** —— 兜底建了块 Floor 却返回正常 id，引擎就把它当屋面计入成功
3. **决定生死的键没有契约** —— `macro` 不校验、不文档化、干跑时也看不见

第 3 点最隐蔽：**一个"不写也能跑、写错也不报错"的键，等于没有这个键。**

所以除了改默认值，还专门把 `roof_macros` 放进干跑的 stats ——
**让"会走哪条路"在动手之前就可见**，而不是等建完去查元素类别。

**测试全绿 ≠ 东西是对的。** 八关门禁覆盖 19 个按钮、31 个单元测试，
但"屋面其实是块楼板"这件事，只有真建一遍、再逐个元素查类别才看得出来。
