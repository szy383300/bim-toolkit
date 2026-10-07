# 通用建筑建模方案探讨（BIMToolkit）

日期：2026-09-12 ｜ 状态：方案征集确认，未实施

## 1. 问题定位

北馨05、四层别墅各自一个 `rebuild_05.py` / `build_villa.py`（及 `build_villa2.py`），
重复的其实是**流程**（清场→标高→墙→楼板→楼梯→屋顶→栏杆→门窗→材质→保存→验收），
每栋楼不同的只是**描述数据**。13 轮探针踩出的 Revit 配方
（StairsEditScope 双参新建、梯段反算、挤出屋顶、栏杆 Create(doc,loop,TypeId,lvlId)、
门窗自建宿主、exec 纯 ASCII 等）是**与具体建筑无关**的，应沉淀一次、处处复用。

## 2. 候选方案对比

| 方案 | 思路 | 优点 | 缺点 | 适用场景 |
|---|---|---|---|---|
| A 配置驱动 | 一份 YAML 描述建筑（标高/墙段/开洞/板/引用宏），通用引擎 `gen_build.py` 校验后发射桥命令与 execute_code | 新增建筑≈零代码；描述可 diff/复用/校验；引擎只写一次 | schema 表达力有限，特殊构件要留扩展点 | 常规民用建筑（推荐核心） |
| B 原型模板库 | 更高层抽象：住宅/内廊办公等原型 + 参数（开间/进深/层高/户型），展开成 A 的配置 | 语义级复用，配置量更小 | 每类原型仍要写一次展开逻辑 | 系列化标准设计 |
| C LLM 生成配置 | 用自然语言让 AI 直接产出 A 的 YAML | 门槛最低 | 尺度/校验风险高，需人工核对 | 概念草案、快速起稿 |
| D 图纸导入 | dwg_to_model 翻模（已有） | 有图即可用 | 依赖图纸质量，门窗仍需补 | 有 CAD 底图的翻模 |
| E 库式 DSL | Python fluent API（`B.level(3000).wall(...)`） | 表达力最强，可写计算逻辑 | 仍是代码，维护成本高于数据 | 高度定制、异形建筑 |

## 3. 推荐：A 为骨架 + 宏库沉淀 + 扩展点

- **配置层**：`buildings/<名称>.yaml`，只放数据；
- **宏库** `macros/`：把已验证配方封装成带参宏——`stairs_u`(双跑梯)、
  `gable_roof`(挤出双坡)、`balcony_railing`(阳台栏杆)、`door_grid`(门窗排布)、
  `wipe_and_levels`(清场+标高修正)；
- **引擎** `gen_build.py`：读 YAML → 干跑校验（开洞必须落墙、梯段反算、
  层高一致性）→ 顺序发射；失败路径一律 Cancel/RollBack，杜绝模态弹窗挂桥；
- **扩展点**：YAML 里允许 `exec:` 直插 execute_code 片段（对应方案 E 的逃生门）。

新增建筑工作流 = 复制最接近的 yaml → 改数据 → `python gen_build.py buildings/xxx.yaml`。

## 4. YAML 示例（四层别墅骨架）

```yaml
name: 四层别墅
levels: [ {name: 标高1, elev: 0}, {name: 标高2, elev: 3000}, ... ]
walls:
  - {level: 标高1, t: 240, segs: [[0,0,12000,0], ...]}
  - {level: 标高1, t: 100, segs: [[2400,0,2400,9000], ...]}
floors: [ {level: 标高1, points: [[0,0],[12000,0],[12000,9000],[0,9000]], structural: true} ]
openings:
  - {kind: door, at: [1200, 0], w: 1200, level: 标高1}
stairs: [ {macro: stairs_u, from: 标高1, to: 标高2, at: [1800, 3300], dir: [0,1]} ]
roof:   {macro: gable_roof, level: 屋顶, x1: -600, y1: -600, x2: 12600, y2: 9600, slope: 30}
railing:[ {macro: balcony_railing, level: 标高2, path: [[4200,0],[4200,-1500],[9000,-1500],[9000,0]]} ]
materials: {ext: 砖, int: 涂料, roof: 瓦}
```

## 5. 迁移路径（确认后执行）

1. 抽引擎：把 build_villa2.py 的执行片段泛化成引擎 + 5 个宏（~1 天当量）；
2. 双向验证：四层别墅与北馨05 各写一份 yaml，产物与现有脚本一致；
3. 沉淀：后续每栋新楼只进 yaml，不再新增 build_*.py。

## 6. 风险与说明

- 楼梯/屋顶配方尚未 100% 打通（桥两次被模态弹窗挂死，待用户点掉对话框后
  先跑只读侦察再提交）；宏库的 `stairs_u`/`gable_roof` 需在侦察结论出来后定稿；
- schema 版本化：yaml 头部加 `schema: 1`，后续演进不破坏旧文件；
- 桥冻结原则不变：配方全部走 execute_code，7.10.4 不再升版。
