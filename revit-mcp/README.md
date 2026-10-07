# revit-mcp — 让 AI Agent 直接操作 Revit 2019

标准 MCP（Model Context Protocol）服务器 + Revit 内 TCP 桥。
Claude Desktop / Cursor / 任意 MCP 客户端配置一次，即可用自然语言驱动 Revit：
查模型、批量建墙、批量改参、工程量统计、规范审查。

## 架构

```
MCP 客户端(Claude 等) ── stdio JSON-RPC 2.0 ── revit_mcp_gateway.py（本目录）
                                        │ token 鉴权 + 换行分帧 JSON
                                        └── TCP 127.0.0.1:9877
                                            MCP Bridge v7（pyRevit 按钮内）
                                                │ Idling 派发·UI 线程安全
                                                └── Revit 2019
```

## 使用步骤

1. Revit 2019 打开模型，pyRevit 加载 BIMToolkit
2. 点击 **BIM 工具 → MCP Bridge**（首次点击自动生成 `bridge.token` 并启动监听）
3. MCP 客户端配置（Claude Desktop 的 `claude_desktop_config.json`）：

```json
{
  "mcpServers": {
    "revit": {
      "command": "python",
      "args": ["E:/bim-toolkit/revit-mcp/revit_mcp_gateway.py"]
    }
  }
}
```

4. 客户端里直接对话，例如：
   - "看下当前 Revit 模型概况" → `revit_get_info`
   - "建一圈 200 厚 3000 高的外墙，12 米 × 9 米" → `revit_create_walls`
   - "统计一下全模型的工程量" → `revit_batch_count`
   - "做一次规范审查" → `revit_run_report`

## 工具清单（17 个原子工具）

| 工具 | 读/写 | 说明 |
|---|---|---|
| revit_get_info | 读 | 文档摘要/类别分布/标高/选择数 |
| revit_list_levels | 读 | 全部标高 |
| revit_list_families | 读 | 全部族与类型 |
| revit_list_elements | 读 | 按类别列构件 |
| revit_get_element | 读 | 单构件全部参数 |
| revit_create_walls | 写 | 批量建墙（mm 坐标，单事务，失败回滚） |
| revit_create_grids | 写 | 批量建轴网（显式线段或轴线坐标，轴号自动分配/重名跳过，v7.7） |
| revit_create_levels | 写 | 批量建标高（重名跳过，v7.7） |
| revit_query_elements | 读 | 多条件过滤构件（类别/标高/名称/ids，可带参数投影，v7.7） |
| revit_get_quantities | 读 | 分类别工程量 + 简易造价估算，可选逐构件明细（v7.7） |
| revit_set_parameters | 写 | 按类别批量改参（值用 Revit 内部单位，长度=英尺） |
| revit_batch_count | 读 | 分类别工程量统计 |
| revit_run_report | 读 | 规范审查五类检查 + 评分 |
| revit_get_changes | 读 | 文档增量变更订阅（seq 水位轮询，v7.6） |
| revit_get_selection | 读 | 当前选择集（ids + 类别/名称明细，v7.6） |
| revit_set_selection | 写 | 设置当前选择集（v7.6） |
| revit_dwg_to_model | 写 | DWG 识图成果 JSON 一键建模（v7.6，网关超时 600s） |

另有 `revit_execute_code`（任意代码执行）**默认禁用**，需在按钮目录
`mcp_bridge_config.json` 中设 `"allow_execute_code": true` 才会出现——仅在
受信任环境使用。

## 安全机制

- **token 鉴权**：桥首启生成 64 位随机 token（按钮目录 `bridge.token`），
  网关持 token 才能执行命令；仅监听 127.0.0.1
- **原子工具**：写操作限定为 create_walls / create_grids / create_levels /
  set_parameters，单事务 + 提交状态核对，失败自动回滚，Agent 无法执行任意删除
- **审计日志**：每条命令（类型/成败/耗时）落 `mcp_audit.jsonl`，
  auth 成败同样记录

## 测试

```
python test_gateway_e2e.py
```

mock 桥端到端：initialize / tools/list / tools/call / resources / 错误 token
被拒 / 桥未启动人话提示，11 用例。

## 已知边界

- **v7.7.1 修复（2026-09-11 真机实测）**：写命令 Raise 在 recv 循环**中途**发生时，同 tick 后续 recv 会撞 WSAEINVAL(10022) 并把**活着的客户端**误杀（live_test_v77 的 T5 错 token 秒连秒关 + create_levels 同 tick 竞态，主连接被强拆 10054）。修复三件套：Raise 发射后立即中止本 tick 剩余 socket I/O；recv 10022 视为瞬态不再杀客户端（只记日志延后处理）；raise_pending 超 30s 自动自愈恢复泵（防 Execute 丢失导致 I/O 永久冻结）
- Revit 弹模态框时命令在队列等待（网关默认 60s 超时），不是丢失
- set_parameters 的 value 是 Revit 内部单位（长度参数为英尺，mm÷304.8）
- 仅支持本机单 Revit 实例（2019）；第二个实例点按钮会明确报 bind 失败（v7.3 起 SO_EXCLUSIVEADDRUSE）
- **pyRevit Reload 会停止桥**——Reload 后必须重新点击 MCP Bridge 按钮（v7.1 起日志会提示）
- **v7.1 修复**：不调 `args.SetRaiseWithoutDelay()` 时 Revit 只在用户输入（鼠标/键盘）时才触发 Idling，Revit 挂后台没人碰就一次都不触发，桥表现为"连得上、无应答"。v7.1 每次 Idling 调用 SetRaiseWithoutDelay 并把 socket 泵限流到 ~20Hz（CPU 近零）
- **v7.2 修复**：Idling 整体弃用（用户不碰 Revit 它就不抛），改用 WinForms Timer（100ms，WM_TIMER 骑 UI 线程消息泵，与焦点/输入无关，模态框期间也跳）
- **v7.3 修复**：pyRevit 在命令结束后**清空命令脚本的全局变量**（journal 铁证：每跳 UnboundNameException "name 'log' is not defined"），回调全部失效甚至炸出 .NET 弹窗杀会话。修复：全部逻辑迁入 `bridge_core.py` 导入模块（pyRevit 不清导入模块全局，pyrevitlib 自身即此机制），script.py 只做薄启动器；tick 体全包裹 try/except（异常只写 mcp_crash.log，永不进消息循环）
- **v7.4 修复**：Revit 禁止在 API 上下文之外开事务（实测报错 "Starting a transaction from an external application running outside of API context is not allowed"——Timer tick 在 UI 线程但**不是** API 上下文，所以只读 get_info 能跑、create_walls 被拒）。修复：写入类命令（create_walls / set_parameters / execute_code）改为入队 + `ExternalEvent.Raise()`，由 `BridgeEventHandler.Execute()`（IExternalEventHandler，pyRevit 官方同款机制）在合法 API 上下文内执行并回包；ExternalEvent 在按钮点击时创建（点击本身就是 API 上下文）。另：script.py 每次点击先 `sys.modules.pop("bridge_core")` 强制重读磁盘，**改完代码直接重点按钮即可生效，不再需要 pyRevit Reload**
- **v7.5 增强**：execute_code 新增 `no_transaction` 模式——lib 函数（如 model_builder.build_model）自开事务，Revit 禁止事务嵌套，桥不能再包外层事务；返回执行命名空间里的 `_result` 结构化数据；ns 注入 `to_text` 与 `BRIDGE_DIR`；payload 以 UTF-8 字节串 exec（IPY2.7 对含非 ASCII 的 unicode 源码报 SyntaxError，字节串 + 文件内 coding cookie 才是正解）
- **v7.5 实机验证补充（2026-09-10）**：
  - IPY2.7 的 exec 字节串**不认 coding cookie**（按 cp1252 解码），payload 源码必须纯 ASCII，中文一律 `\uXXXX` 转义
  - 自动标注：`Reference(墙)` 在 NewDimension 中解析为墙**起点平面**（探针实测：3000/6000 双墙链段 6000=起点差），N 面墙只得 N-1 段（末段墙长不在链内）；`Location.Curve.GetEndPointReference` 返回 None；几何端面 Reference 与元素 Reference **混排会污染整条标注**（所有段变 -304.8mm）；设 `Options.View` 后端面引用大面积缺失。结论：端面补链三条路全堵死，保持元素引用链，N-1 语义已写入按钮 docstring
  - 墙自动 joining：首尾相接的共线墙会被 Revit 自动 join，端面从几何里消失——做标注/几何测试时墙间留 ≥2mm 缝
  - lib 四文件（standards_checker/bimhealth/cost_estimator/bimlib）`from pyrevit import DB` 在桥上下文静默失败（DB=None），已加 RevitAPI 直引回退
- **P0 已闭环（2026-09-10 晚，逐步二分定位）**：T1 "build_model 静默死锁"真凶不是 NewFootPrintRoof 计算挂起，而是 **Transaction.Commit 弹出「0 错误 N 警告」模态框**（Win32 实锤：主窗 disabled + #32770 对话框），ExternalEvent Execute 被永久阻塞——与 T1 全部症状吻合（心跳继续/无崩溃日志/写队列瘫痪）。逐项二分发现并已修复 4 个真 bug（model_builder.py，C: E: 已同步）：
  1. 全部 9 处事务装 `_SwallowWarnings`(IFailuresPreprocessor)，commit 自动删警告永不弹框；
  2. `create_gable_roof` 的 `SketchPlane.Create` 原在事务外（报 "document has no open transaction"），已移入事务内；
  3. `NewFootPrintRoof` 的 out 参数在 IPY2.7 必须显式传**带初值**的 `StrongBox[ModelCurveArray](ModelCurveArray())`（无初值报 "Value cannot be null"；直接传 ModelCurveArray 报 TypeError）；
  4. 窗/门族符号必须 `symbol.Activate()` 后才能 `NewFamilyInstance`（报 "The symbol is not active"），已加 `_pick_active()`；
  5. **IPY2.7 无法绑定 `WallType.Name`**（ElementType new 隐藏属性，AttributeError: Name；Level.Name 正常）→ `ensure_wall_type` 复用搜索全盲、二次运行必撞名回退基础墙类型丢厚度。已加 `_type_name()`（回退 `ALL_MODEL_TYPE_NAME` 内置参数）。
  修复后终验：build_model 连跑两次计数完全一致（标高3/外墙8/内墙4/楼板3/屋顶✓/窗20/门1/阳台1/零失败），核心层厚度 200/120 精确保留。另：排查期间造了 `mcp_dialog_eater.py`（Temp）——无人值守时自动点掉 Revit 纯警告模态框的后台守护。
- **v7.6 增强（2026-09-10）**：新增 DocumentChanged 增量订阅（模块级 `_CHANGES`/`_CHANGE_SEQ` + seq 水位 `get_changes` 轮询，重复订阅防护：每次 start() 先 `-=` 旧委托再 `+=`）；`get_selection`/`set_selection` 选择集双向同步；`dwg_to_model` 把 bimconvert 链路（load_rules→plan_from_json→create_elements）工具化（网关侧超时 600s）；配套 gateway v1.1.0（13 原子工具）。bimconv 五件（bimconvert/bimconv_build/bimconv_elements/bimconv_geom/bimconv_walls）补齐桥上下文 `from pyrevit import DB` 静默失败回退（直引 RevitAPI）。
- **v7.6.1 遥测（2026-09-11）**：心跳清理循环的 dead_fds 改为 `(fd, reason)` 元组（fin/sockerr/oserror/dead-flag），掉线时记日志 `Client dropped (fd=N, reason=X), M left`——此前心跳只报 "0 client(s)" 不记原因，无法区分客户端正常关闭与异常掉线。纯日志增强，协议不变。
- **v7.6.2 修复（2026-09-11，真机取证）**：`get_selection` 在裸 Timer tick（非 API 上下文）读 `uidoc.Selection`——处理器正常返回（审计 ok、9ms）但响应永远到不了客户端、下一跳 recv 报错断连（sockerr）；而 batch_count 在新连接上带中文载荷完全正常，排除编码嫌疑。修复三连：① `get_selection` 移入 ext-event 路由（API 上下文执行，与 set_selection 同路径）；② `_send` 改 ensure_ascii=True + 短暂阻塞发送（≤2s）——旧的非阻塞 sendall 会把 wouldblock 静默吞掉 = 响应丢失 = 客户端干等整个超时；发送失败现在记日志；③ sockerr/oserror 掉线原因带上真实 errno。另：实测抓出 `dwg_to_model` 调 `create_elements` 多传了 `log_file` 关键字（lib 真实签名 6 参无此），每次必 TypeError——已删。
- **v7.6.3 修复（2026-09-11，遥测立功）**：v7.6.2 的 `_send` settimeout/setblocking 杂耍在这套 IPY/.NET 栈上把 socket 留在非法组合态，毒化后续 recv——排队写命令的下一跳 recv 报 **WSAEINVAL 10022** 掉线，ext-event 回包撞上 "Bad file descriptor"。回退为纯非阻塞 sendall（7.6.1 整会话无事故的形态），保留 ASCII 线 + 发送失败日志（wouldblock 现在可见而非静默）；清理循环埋点 `state["dead"]`，**死客户端的排队写命令不再执行**——模型状态与（已断开的）客户端认知保持一致。教训：**socket 模式切换在此栈不可碰，发送路径保持最简**。
- **v7.6.4 修复（2026-09-11，三轮取证收敛出真不变量）**：v7.6.3 无杂耍仍 10022，杂耍理论证伪。三轮日志对齐后真规律：**只要 `ExternalEvent.Raise()` 待执行（Execute 未跑），下一次 recv 必死 WSAEINVAL；Execute 完成后 recv 即恢复**（R3 两个完整 Raise→Execute 循环正常，第三个待执行 Raise 杀死下一跳 recv）。修复：Raise 待执行期间 tick **暂停一切 socket I/O**（`raise_pending` 标志，Execute 的 finally 里清除）——客户端不断、字节在 OS 缓冲等待，代价仅 ~100ms 级延迟。另：Raise() 自身抛错时清标志并回错误包。**R4 实测：全链路一条连接跑通（ping→增量→建墙→选择集→dwg），10022 绝迹。**
- **v7.6.5 修复（2026-09-11）**：`dwg_to_model` 第二处幻影签名——`create_elements` 实际只返回 `results` 列表（lib 里根本没有 dbcheck，与 log_file 同出一处的过期摘要），`results, dbcheck = ...` 把 >2 项的列表拆成 2 个名字报 "too many values to unpack"。改为单值接收；返回新增 `fail_samples`（前 5 条失败消息，built_fail 不再瞎猜）；`process_command` 错误响应保留 handler 的 traceback（此前被剥掉，客户端只剩一行裸错误）。网关 revit_dwg_to_model 描述同步更正（e2e 11/11）。**教训闭环：跨文件调用一律先 Grep 真实签名，摘要不可信。**
- **双引擎策略结论（2026-09-11，决策关闭）**：**维持 IronPython 2.7 单引擎，不引入 `#! python3` CPython 双引擎**。理由：① 桥与全部 lib 已在 IPY2.7 上完成 P0 级实战验证（事务/警告吞咽/StrongBox/out 参数/族符号激活/ElementType.Name 回退五大雷区全部趟平并有测试背书）；② pyRevit 6.x 的 CPython 引擎需另维护一套运行时，且上述每个 IPY 坑都要在 CPython 侧重新验证，收益为零（两引擎调的都是同一套 Revit API）；③ 单引擎减少维护面。若未来 pyRevit 官方弃 IPY，再整体迁移而非双轨并行。
- **v7.6.6 修复（2026-09-11，双事故同源）**：**旧桥"干净退休"后端口实际要 10–20+ 秒才真正释放**（IPY2.7/.NET 的 socket 句柄释放滞后，v7.5-hotfix 的"几百毫秒"假设错误）。实锤两例：① 07:41 旧桥日志已出 "Bridge shut down cleanly"，新实例 bind 仍连败 8 次跨 8 秒（10048→10013），用户被迫 23 秒后再点一次才成功；② 16:09 同秒双击——实例 B 先请求 A 退休、自己 6 秒窗口内 bind 全败 FATAL，A 的 Timer 迟到 7 秒才起、第一跳就执行了退休指令 → **两实例全灭、端口空悬**，且哨兵被误导。修复：bind 重试 10×0.6s → **45×1.0s**（覆盖最慢释放窗口，双击竞速下后点实例会等到端口腾出后自然接管；真双实例时 EXCLUSIVE 报错语义不变）。教训：对"释放完成"的判定不能信日志时序，要给足重试窗口。
