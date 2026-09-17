# 本轮追踪清单：Phase C 定向重跑 + 权威失败数据双落点（2026-09-09）

用户要求：①本轮在 `E:\Generate-test-cases-and-data` 新增/改动的文件统一登记便于管理；
②在 `C:\Users\damai\PyCharmMiscProject` 下**新增**的文件在此保存路径列表便于追踪。

> 说明：核心代码文件因 import 依赖 / pytest 发现 / 项目约定（changelog、tests/tools aux）必须留原位，
> 故以本清单统一登记；可独立挪动的 scratch/运行摘要尽量放本目录。外部树（PyCharmMiscProject）写入
> 每次追加记录。

## A. repo（E:\Generate-test-cases-and-data）新增文件
| 相对路径 | 用途 |
|:---|:---|
| `changelog/2026-09-09_phase_c_selective_rerun_sidecars.md` | 方案文档（决策归档，文档先行） |
| `agent_components/run_results.py` | 纯函数库：junit/日志解析 → 逐 YAML `.last_run.json` + 选点 + 失败摘要（被 web/tasks import，须留位） |
| `_phase_c_selective_2026-09-09/MANIFEST.md` | 本追踪清单 |
| `tests/test_run_results.py` | run_results 纯函数单测（2026-09-09 建成，20 passed） |
| `tests/test_selective_phase_c_rerun.py` | yaml_gen allow/ctx + bg 装配 + 路由测试（2026-09-09 建成，12 passed） |
| `tests/tools/_ingest_phase_c_run_sidecars.py` | 手动摄取驱动（tests/tools 惯例 aux，不入系统运行） |
| `logs/_phase_c_driver_t24.py` | 基线全量重跑驱动（上一批遗留，gitignored，供参照） |

## B. repo 改动文件（本轮编辑，逐项追加）
| 相对路径 | 改动 |
|:---|:---|
| `agent_components/generators/yaml_gen.py` | `_generate_all_yamls` 增 `allow_paths/ctx_overrides`；Append 期过滤 + `_run_yaml_rounds` 逐任务 ctx（2026-09-09 完成） |
| `web/tasks.py` | 新增 `_selective_phase_c_rerun_bg`（2026-09-09 完成） |
| `web/routes/chat.py` | 新增 `POST /phase-c/selective-rerun`（2026-09-09 完成） |

## C. PyCharmMiscProject 新增写入（追加记录，绝对路径）
> 本清单创建时尚未新增外部文件；基线全量重跑 / 摄取 `.last_run.json` / teardown 新建后逐一登记。
> 登记方式：属同批模式（如 `setup_data/teardown_*.yaml` 47 个）时给根路径 + 计数，重要单文件列全。

| 批次 | 根路径（绝对） | 说明 |
|:---|:---|:---|
| 2026-09-09 基线全量重跑 | `C:\Users\damai\PycharmMiscProject\testcase\园区基线\智慧停车\SmartParking\**\*.yaml` | 重生成 **160** YAML（80 test_data + ~48 setup + 部分 teardown；`_generation_errors.json` 无失败） |
| 2026-09-09 第一轮执行摄取 | `C:\Users\damai\PycharmMiscProject\testcase\园区基线\智慧停车\SmartParking\**\*.last_run.json` | 摄取驱动写入 **83** 个动态落点（passed 20 / failed 27 / broken 36），run_ts=20260909_2010 cohort |
| 2026-09-09 第二轮定向重跑（进行中） | 同上 `SmartParking\**\*.yaml` | 覆盖重生成 27 test_data + 36 setup（digest 修正），完成后再核计 |

## D. 其他（非 repo / 非 PyCharmMiscProject）
| 路径 | 用途 |
|:---|:---|
| `C:\Users\damai\.claude\plans\virtual-munching-rivest.md` | 本轮方案（决策持久载体） |

---

# 2026-09-10 批次：智慧停车 Phase C 两轮重跑（分析 → 重生成 → 框架验证）

> 方案文档：`changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md`（含 Round 0 实测结论 §九）。

## E. repo 新增文件（2026-09-10）
| 相对路径 | 用途 |
|:---|:---|
| `changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` | 方案 + Round 0 实测结论（§九 **更正版**：撤回 C2/C3 与 B1「接口缺陷」定性） |
| `tests/tools/_probe_parking_park_reality.py` | Round 0 探针：只读采集真实车场/设备字典/规则 + saveOrUpdate 回放变体（aux，不入系统运行） |
| `tests/tools/_probe_parking_golden_path.py` | Round 0 探针②：黄金链路实证（add→回读→create→回读→device→delete），`--envelope raw/json`、`--mode golden/deletes`（aux） |
| `_phase_c_rerun_2026-09-10/round0_probe_readonly.json` | 只读采集证据 |
| `_phase_c_rerun_2026-09-10/round0_probe_with_replay.json` | 含真车场回放证据（⚠️ 结论已作废，探针封装失真 + getDetail 不可用） |
| `_phase_c_rerun_2026-09-10/round0_probe_variants.json` | 回放变体证据（⚠️ 同上作废） |
| `_phase_c_rerun_2026-09-10/round0_golden_raw.json` | 黄金链路首次（raw 封装）实证：证 S1/S3/S4/S5 通过 |
| `_phase_c_rerun_2026-09-10/round0_golden_raw_full.json` | 黄金链路完整实证：S7/S7b 撤回 C2/C3、S9 证明 parkCode 必须空串、S10 子场通过 |
| `_phase_c_rerun_2026-09-10/round0_delete_matrix.json` | 删除通道 5 种**带壳**编码全失败（形状错，非端点缺陷）+ getDetail 复测失败（N4） |
| `tests/tools/_cleanup_parking_probe_residue.py` | 探针残留清理（`--dry-run` 默认 / `--apply` 真删）；只删 `PROBE_*`/`探针*` 前缀（aux） |
| `tests/tools/_apply_parking_api_defs_facts.py` | 把 §十 实证事实写入模块 `api_defs.json` 示例值 + `--sync-db` 同步 SQLite 存档（幂等，`--apply` 才落盘） |
| `tests/test_parking_data_layer_facts.py` | 数据层事实护栏（**27 passed**）：parkCode 空串 / 设备字典成对 / 删除形状 / 上下文无取值示例 / 存档一致 / **`getAllWithDevices.return` 嵌套 + DB `api_returns` 一致（Round 2 新增 4 项）** |
| `_phase_c_rerun_2026-09-10/round2_per_case_attribution.md` | Round 2 逐例归属报告（42 例三层判障，2026-09-10） |
| `_phase_c_rerun_2026-09-10/r2_fail_forensics.txt` | 42 例失败点实收发取证（从 `round2_pytest.log` 按 pytest 节点切分提取） |
| `_phase_c_rerun_2026-09-10/cleanup_dryrun.json` | 清理 dry-run 证据（⚠️ 早期版本 dry-run 未真正跳过，误删 4 条探针规则，已修正为真跳过） |
| `_phase_c_rerun_2026-09-10/cleanup_applied.json` | 清理执行证据（公园 4/7 成功、规则全清） |
| `tests/tools/_probe_parking_delete_shapes.py` | Round 3 探针：删除通道 `codes` 形状四态实证（对象/数组/裸数组/逗号串）× 3 端点，假编码非破坏性（aux） |
| `tests/tools/_apply_parking_round3_facts.py` | Round 3 数据层 D1–D8 应用器（`--apply`/`--backup`/`--sync-db`；缺省 dry-run，幂等） |
| `_phase_c_rerun_2026-09-10/round3_probe_delete_shapes.json` | 删除形状证据（裸数组 vs 对象壳） |
| `_phase_c_rerun_2026-09-10/round3_probe_device_list.json` | 只读设备列表/分页结构证据（`getList` 裸数组、`getPage` 对象含 `data.list`） |
| `_phase_c_rerun_2026-09-10/round3_task_result.json` | Round 3 重生成驱动结果（172/174） |
| `_phase_c_rerun_2026-09-10/round3_driver.log` | Round 3 重生成驱动日志（三阶段 + 修复轮） |
| `_phase_c_rerun_2026-09-10/round3_pytest.log` | Round 3 框架执行日志（GBK 编码） |
| `_phase_c_rerun_2026-09-10/round3_junit.xml` | Round 3 junit（82 节点：18 passed / 38 failure / 24 setup-error / 2 teardown-error） |
| `_phase_c_rerun_2026-09-10/r3_fail_forensics.txt` | Round 3 侧车取证（58 条 failed/broken，`run_ts=20260910163000`） |
| `_phase_c_rerun_2026-09-10/round3_per_case_attribution.md` | Round 3 逐例归属报告（生成侧 54 / 接口缺陷候选 10） |

## F. repo 改动文件（2026-09-10，逐项追加）
| 相对路径 | 改动 |
|:---|:---|
| `_phase_c_selective_2026-09-09/MANIFEST.md` | 补登 2026-09-10 批次（本节）+ Round 1 / Round 2 回填（H 节） |
| `changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` | 追加 **§十二 Round 1 执行结果**（重生成事实 / 数据层落地实测 / junit 对比 / 主因证据链 / 原则③待决策项 / Round 2 修正清单） |
| `changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` | 按用户指正重写 §二/§五/§七/§九/§十：撤回 C2/C3/B1/N5/N2 的「接口缺陷」定性，改为数据层根因 + 正确的删除形状 |
| `data/app.db` | `documents.api_parameters` 同步 12 行（api_defs 事实）；同步前备份 `data/app.db.pre_apidefs_sync.bak` |

## I. 模块目录（PyCharmMiscProject）改动文件（2026-09-10）
| 绝对路径 | 改动 |
|:---|:---|
| `…\智慧停车\api_defs.json` | 12 处示例值/描述修正（parkCode→空串、设备字典真实值、placeCode 语义、删除形状）；备份 `api_defs.json.20260910101131.bak` |
| `…\智慧停车\phase_c_user_context.md` | 规则重写 R1–R7（parkCode 空串+回读、规则 code 页序回读、设备绑定语义、删除形状、getDetail 缺陷排除）；仍只含规则不含示例 |
| `…\智慧停车\api_defs.json` | Round 2：`parkingPark/getAllWithDevices` 的 `return` 补嵌套层级（`parkingPark`/`parkCode`/`carInDeviceList`/`carOutDeviceList` 等 13 条 + `data` desc 覆盖）；备份 `api_defs.json.20260910114807.bak`，SQLite `api_returns` 同步 1 行 |

## G. PyCharmMiscProject 新增写入（2026-09-10）
| 批次 | 根路径（绝对） | 说明 |
|:---|:---|:---|
| Round 0 探针（**API 写 dev，非文件**） | dev `https://dev.damaiiot.com:40443` | 新建探针规则 3 条 `zhyqC53976127`/`zhyqC50339448`/`zhyqC50068657`（`parkingPaymentConfig/delete` 不可用，未能清理）；车场 58 `zhyq318740310078449` 已按原值复原；未新增/改动任何文件 |
| Round 0 黄金链路探针（**API 写 dev，非文件**） | dev `https://dev.damaiiot.com:40443` | 新建探针规则 `zhyqC42922408`/`zhyqC02507707` + 探针车场 `zhyq241332878436414`/`zhyq134584101911699` + 2 台探针设备。**大部分已清理**：探针规则全清、车场删 4/7、设备已删。**余 3 个车场无法删除**：`探针空码04853`(zhyq139015534506054) / `探针F04846`(zhyq401422958554880) / `探针D04845`(zhyq524912295639258)——恒「请求失败」，疑先删其绑定规则致 orphan；登记不再追 |
| 删除形状实证结论（**API 写 dev，非文件**） | 同上 | `parkingPark/deleteByCodes` 与 `parkingPaymentConfig/delete` = **裸数组**；`parkingDevice/deleteByCodes` = **`{"codes":"a,b"}` 字符串**。原「不可用」判断为形状错误，已撤回 N5/N2 |

## H. Round 1 / Round 2 回填
| 批次 | 根路径（绝对） | 说明 |
|:---|:---|:---|
| Round 1 重生成（2026-09-10 10:20–10:44） | `…\智慧停车\SmartParking\**\*.yaml` | **153/174**：setup 47/47、test 80/80、teardown **26/47**（21 个被 `_filter_teardown_missing_pres` 按设计剔除）；`yaml_failed=0`，自查修复 6。驱动 `tests/tools/_run_parking_phase_c_rerun.py`，task_id `8310fbd81fe0444e8a15eb5850a4dcaa` |
| Round 1 执行摄取（10:53–10:55） | `…\SmartParking\**\*.last_run.json` | 写 **94** 个动态落点（passed 28 / failed 40 / broken 26）；junit 88 节点：passed 4 / failure 40 / setup-error 33 / teardown-error 11 |
| Round 1 证据 | `E:\Generate-test-cases-and-data\_phase_c_rerun_2026-09-10\` | `round1_task_result.json`、`round1_driver.log`、`round1_pytest.log`、`round0_baseline_junit.xml`（09:08 基线 junit 备份，用于同口径对比） |
| Round 1 结论 | `changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` **§十二** | 单一主因：回读路径 `$.data[0].parkCode` 少一层 `parkingPark`（62 处）→ 83 次调用带占位串级联 |
| Round 2 重生成（12:00–12:17） | `…\SmartParking\**\*.yaml` | **172/174**：setup 47/47、test 80/80（自查修复 2）、teardown **45/45**；`yaml_failed=0`；**引用完整性全部存在**（Round 1 有 12 处 MISSING）；回读路径 `$.data[0].parkingPark.parkCode` 70 处 |
| Round 2 执行摄取（12:18–12:20） | `…\SmartParking\**\*.last_run.json` | 写 **102** 个动态落点（passed 39 / failed 42 / broken 21）；junit 83 节点：passed 12 / failure 42 / setup-error 25 / teardown-error 4 |
| Round 2 证据 | `E:\Generate-test-cases-and-data\_phase_c_rerun_2026-09-10\` | `round2_task_result.json`、`round2_driver.log`、`round2_pytest.log`、`round2_junit.xml`、`r2_fail_forensics.txt` |
| Round 2 逐例归属 | `…\_phase_c_rerun_2026-09-10\round2_per_case_attribution.md` | 42 例：连通性 0 / **生成侧 35** / 接口缺陷候选 6 / 待确认 2；R1 主因（占位串级联 83→2）已清除 |
| Round 2 结论 | `changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` **§十四** | 关键口径更正：业务失败返 **HTTP 400**（非链路故障）、错误文案在 `$.data`、`parkCode:'ALL'` 合法 |

> **Round 2 收口**：按用户「只重跑两轮」约束，Round 2 后不再触发第三轮重生成。生成侧第一梯队（R7 提取键语义化、R1 负向状态码预期）属纯规则补丁，留待下一批决策。

## H2. Round 3（2026-09-10，用户授权「按更正后重跑一轮」）

> 方案：`changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` **§十五**。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| 归因更正 | ✅ 已写入 §15.1 | 撤回"schema 缺位"定性：接口覆盖 **100%（39/39）**、`status_code: 200` 是生成器产出、`return[].desc` 可承载说明 |
| 数据层改动 D1–D8 | ✅ 已执行 | 见 §15.2；**D9 撤并进规则 R9**（跨接口通用口径，不写数据层）。备份 `api_defs.json.20260910155014.bak`，`db synced rows=9`，文件/SQL 双写已核 |
| 删除形状探针 | ✅ 已执行 | `carWhiteBlack/delete`、`parkingCouponIssue/deleteByCodes` = **裸数组**；对照组 `parkingDevice/deleteByCodes` = 对象壳（逗号串），判别方法自证可靠。证据 `round3_probe_delete_shapes.json` |
| 规则层 R8–R12 | ✅ 已执行 | `phase_c_user_context.md`，纯规则无示例；R6 同步扩到黑白名单/优惠券删除形状 |
| 测试护栏 | ✅ 已执行 | `tests/test_parking_data_layer_facts.py` **46 passed**（原 27）：D1–D8 逐字段 + 文件/DB 一致 + R8–R12 文本存在性与反例 |
| 重生成 | ✅ 已执行 | **172/174**：setup 47/47（修复 1）、test 80/80（修复 5）、teardown 45/45（修复 0）；**引用完整性全部存在**；`_setup_extract_keys.json` **全缺失 0/94**（Round 2 为 7/94）。task_id `f6cfd751b95445a997a0d567f1ac293c` |
| 框架复跑 | ✅ 已执行 | `python run.py -p testcase/园区基线/智慧停车`（同 Round 1/2 口径全量）。junit：passed **18** / failure **38** / setup-error **24** / teardown-error **2**（82 节点） |
| Round 3 逐例归属 | ✅ 已产出 | `_phase_c_rerun_2026-09-10/round3_per_case_attribution.md`：**生成侧 54**（failure 28 + setup 级联 24 + teardown 2）/ **接口缺陷候选 10** |
| Round 3 结论 | ✅ 已写入 §十六 | 关键发现：缺口在**生成器对已声明规则的遵循一致性**，不在接口说明（覆盖 100%）。`bindPayCode` 同一根因复发 43 次 |
| 本轮不做 | — | `parkingPark/getDetail` 排除出**判障基数**（端点自身缺陷，影响 4 例）；两套 `return` 命名约定不一致单列待决策 |

> ⚠️ 未改动但已登记：9 个 09-09 残留 teardown（被剔出重生成队列、仍是修复前内容，pytest 仍会执行）
> + 12 个 `.py` 引用的 teardown YAML 磁盘缺失（致 6 例 teardown TypeError）。见方案 §12.6，**待决策**。

## H3. V1–V5 产出侧校验器（2026-09-10，用户决策：V1–V5 实现 / V6 只登记）

> 方案：`changelog/2026-09-10_parking_phase_c_rerun_two_rounds.md` **§16.7（落点更正）/ §16.8 / §十七**。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| 规则标定 | ✅ 已执行 | 拿 Round 3 真实产物 47 个 setup 反证：初版 16 命中（**2 误报 / 6 漏报**）→ 定稿 **20 命中 = 20/20 失败 setup，0 误报 0 漏报**。两处根因见 §17.1（V4 误取「第一个分页步」；V1 闸门错加 needPay） |
| 校验器实现 | ✅ 已执行 | `agent_components/validation/yaml_validator.py` 新增 `_scan_extract_value_forms()` 等（V1–V5，约 200 行）；落点按 README 的**单独管理校验器包**，非 `yaml_gen.py` 内联。V6 泛化**只登记不实现** |
| 调用点注册 | ✅ 已执行 | `yaml_gen.py:474`（`_scan_missing_key_refs` 旁）+ V 规则汇总日志；回炉通道复用 P0/P1 注入，零新增机制 |
| `validate_all` 形状失配 | ✅ 已修（另立一项） | §16.8 涉及项：只认 `{data:[...]}` → 兼容顶层 list。修前先量误报面：对真实产物 **0 命中**，安全启用 |
| **后校验修复轮死代码** | ✅ 已修（顺带发现） | §17.3：注入闸门 `result["rounds"] < YAML_REPAIR_ROUNDS` **恒为假**（rounds 含全量轮 ≥1 而默认上限 1）→ **后校验问题从未真正回炉过**。改为独立预算。全链路（→`_format_post_issues_for_prompt`→prompt）已逐环核验 |
| 测试 | ✅ 已执行 | `tests/test_bind_value_form_validator.py` 新增 24 用例；`test_regression_post_validator.py` +1 形状回归。相关套件 **93 passed**；全量 881 passed / 9 failed（**全部既有失败**，`git stash` 复验同失败） |
| Round 4 重生成 | ✅ 已执行 | 174 个：setup 47/47（修复 3）、test 80/80（修复 8）、teardown 46/46；引用完整性全部存在。task_id `c9607c6a9c314e9fa5d3efc81887f2ed` |
| **V1–V5 首次生效** | ✅ 已执行 | 判 P0 **30 处 / 21 个 setup** → 后校验回炉轮**真的执行**（`21/21，修复 1，轮次 2`）—— 证实 §17.3 死代码闸门修复有效 |
| 回炉效果 | ⚠️ 只消 1/3 | 重生成后复检：**残留 20 处 / 13 个 setup**（literal 8 / undeclared 8 / empty 4）。prompt 已带 `当前/期望/修复指引`，LLM 仍改不干净 |
| Round 4 框架复跑 | ✅ 已执行 | 86 节点（R3 为 82，**用例集已变不可直比**）：passed **16** / failure **40** / setup-error **23** / teardown-error **7** |
| Round 4 决定性口径 | ⚠️ 未收敛 | 服务端拒绝文案同口径计数：目标根因 `绑定规则不存在/类型不正确` **38→26（−12）**，但 `此车场名称已存在` **2→10**、`主场必须存在` **1→10** → 合计 **62→69（+7）**。**问题换形态，未解决** |
| Round 4 结论 | ✅ 已写入 §十八 | 校验器有效但**单靠「校验+回炉」不够**；下一步二选一（A 确定性改写 YAML / B 收口）。另需单独排查两个新升类别 |

**新增文件（2026-09-10 第二批）**：`tests/test_bind_value_form_validator.py`、
`tests/tools/_probe_v1v5_calibration.py`（标定探针，aux 不入系统运行）。
**Round 4 产物**：`_phase_c_rerun_2026-09-10/round4_{task_result.json,driver.log,pytest.log,junit.xml}`。

## H4. 判定表驱动生成（2026-09-10，用户决策：全做 37 条 / 独立映射数据文件）

> 来源：`D:\0-导出文件\停车类型和费用计算-规则表.xlsx`（表1 入场车辆类型 24 条 + 表2 车场收费 13 条）。
> **用户指定顺序：先出业务用例 → 用户过目 → 再对接口。** 故当前仅产出用例草案，未做接口映射、未写代码。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| 接口可达性预研 | ✅ 已核 | 两张判定表的每个条件/动作在现有 39 端点中**均找到承载**：条件→`parkingPark/saveOrUpdate`（`carInConfig.*` 开关）/`carWhiteBlack/add`/`mock/monthlyCar/add`/`parkingCouponIssue/add`；动作→`mock/access/enter|exit`（`openGate`）/`mock/bill/latest`（`paid`/`payableAmount`）；teardown 专用 `mock/delAllForMock`。**11 个 `mock/*` 端点为该判定表原生测试面** |
| 用例草案 | ✅ 已产出 | `rule_table_cases_draft.md`：**37 条**（表1 24 + 表2 13），纯业务语言、无接口名；`\` 保留为「不适用」 |
| 待用户核对 | ✅ 已过目（2026-09-10） | 7 处全部答复并落文档：①两组重复行**保留为独立用例**；②表1-10/21 **动作列已说明**「A 是、B 否」（入场即拦截）；③表1-04/15 步骤序列**本身不含出子场**，记不适用；④**同一行程按场区分段计费**；⑤「提前缴费=是→否缴费」=**出场时无需再缴**；⑥表2-01「否」=**不产生费用**；⑦表2 数值**由生成侧拟定** |
| 表2 数值口径 | ✅ 已拟定 | 单价 **5 元/小时**、免费时长 0；纯主场停 4h→**20 元**；主+子场：主 2h(10)+子 3h(15)；金额券 5 元、时长券 1h。13 条全部改为**数值断言**（15/20/25 元等） |
| 接口映射草案 | ✅ 已产出 | `rule_table_interface_map_draft.md`：条件侧/操作侧/断言侧三类映射 + 37 条逐条落点 |
| 缺口决策（2026-09-11） | ✅ 已决 | ①**优惠券无核销端点** → **先不管，8 条用例暂留**；②**转临停** → **删该车白名单/包月车记录**（`carWhiteBlack/delete`、`mock/monthlyCar/delete`）；③**共用数据统一前置生成、统一删除** |
| 转临停回读链 | ⏳ 待确认 | `carWhiteBlack/add` 返回 `data:null`、`mock/monthlyCar/add` 只返回 `success` → 删除需 `codes`，**必须 `getPage`/`query` 回读编码**（同 `bindPayCode` 类坑） |
| 共享前置设计 | ✅ 已产出 | 映射草案 §六：车场按配置变体分 **10 组 / 13 实体**（P1–P6 主场 + S1 子场 + PS1–PS3 主+子），每场 1 设备；作用域靠 `carWhiteBlack/add.parkCode`（逗号分隔/ALL）表达 |
| `delAllForMock` 事实缺口 | ⏳ 待确认 | body 为空但必需 `parkCode` → 拟按 `_apply_*_facts.py` 脚本补 api_defs.json + DB（不动 prompt） |
| 本轮生成范围 | ✅ 已确认 | 暂留 8 条券用例后 → **29 条**（表1 24 + 表2 的 01/05/06/10/11） |
| 生成方式 | ✅ 已定 | **不用 Phase C prompt**，只复用 schema 结构 + 输出逻辑（`yaml_gen.py` 布局 + `py_export.py::_generate_py_file`）；只输出 YAML + `.py` 驱动 |
| .py 格式 | ✅ 已定 | 按 **VehicleAccess 格式**：一条用例 = 一个 class、一步 = 一个 `@pytest.mark.order(N)` 方法（Phase B 划分） |
| `delAllForMock` | ✅ 已定 | **系统前置**，每次执行前跑一次（`order(-1)`） |
| 翻译命名 | ⏳ 待对比 | 先按生成侧自定英文名；结束后与现有翻译对比择优 |
| **VehicleAccess 参考分析** | ✅ 已产出 | `vehicleaccess_reference_analysis.md`。**定位（用户澄清）**：VehicleAccess 是**旧版**，其 `mock/*` 端点全是旧的，**只参考相互关系（调用顺序）**，端点/约定值不照抄 |
| 沿用参考的顺序规律 | ✅ 有效 | ①前置清理最先、系统级跑一次；②入场先于出场且时间递增；③`openGate` 是放行断言；④计费需传 `currentTime`；⑤**先读后付**；⑥**转类型/删除须先 query 回读 code 再 delete by codes**；⑦一人多车用 `subCarNumbers`、辅车牌存数组 → `get_extract_data_list(x,0)`；⑧子场序=入主→入子→出子→出主 |
| **测试范围更正** | ✅ 已澄清 | 测**新旧接口** → **智慧停车绑定的全部 39 接口**在范围内，**含车场配置**（`parkingPark/saveOrUpdate`+`carInConfig.*`）与**收费规则配置**（`parkingPaymentConfig/*`）→ **§6 共享车场设计不作废** |
| 白名单接口 | ✅ 已定 | 用**新的（不带 mock）** → `carWhiteBlack/add|getPage|delete`，**不用** `mock/whitelist/*` |
| 车场分组原则 | ✅ 已定 | **表1、表2 不区分车场，可混用** → 按**配置变体**分组，不按表分 |
| 设备 | ✅ 已定（2026-09-11） | **`parkingDevice/add` 建设备**（用户确认「设备创建不就在这里？」）→ `parkingDevice/getPage` **回读** `data.list[].code` 作 `deviceCode`。设备端点在 39 之内，属测试范围 |
| `delAllForMock` | ✅ 已定，缺省无参 | **`body: []`，无参 `{}`，园区级全量清** → 作**系统前置**（`order(-1)`，每次执行前跑一次）；**只出现在统一 teardown，不得进用例级 teardown** |
| ~~接口事实缺口（原则③登记）~~ | ❌ 已撤回 | **我方此前判断有误**：`api_defs.json` 中 `delAllForMock` 的 body 本就是 `[]`；VehicleAccess `cleanAllData.yaml` 实证为 `json: {}` 无参全量清 → **无需补 `parkCode` 接口事实** |
| **接口清单（39 个按角色分类）** | ✅ 已产出 | `rule_table_interface_inventory.md`：A 车场配置(4) / B 收费规则(6) / C 设备(7) / D 黑白名单(5) / E 优惠券(6,暂留) / F Mock 测试面(11) → **四角色**（系统前置 / 建数据 / 回读 / 触发 / 断言 / 清理）+ **三条引用铁律** |
| 生成器/映射文件 | ⛔ 未开工 | 待用例确认后启动：`rule_tables/parking_rule_map.yaml` + `registry.py`（照 `data_factory/` 模式）、`agent_components/generators/rule_table_gen.py` |

## H6. 格式来源更正 + 落地方案（2026-09-11）

> 用户指出：`SmartParking/test_SmartParking.py` **就是 Phase C 的产物**，`.py` 格式的规范来源 = **Phase C 生成代码本身**。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| **格式来源更正** | ✅ 已更正 | `.py` 与 YAML 的规章 = `py_export.py::_generate_py_file` + `yaml_gen.py::_generate_all_yamls`；`test_SmartParking.py` 是其**实例**（6 行 import / fixture / class / `run_blocks` 逐字对上；`test_ZCGZXZHBZSTQJFLCZDZX` 拼音回退 = 机器生成指纹） |
| ~~「`.py` 无 schema」~~ | ❌ 已撤回 | Phase C 生成器即成文规范，且有实例 |
| ~~「`_generate_py_file` 不能复用」~~ | ❌ 已撤回 | **可原样复用**；VehicleAccess 只提供**调用顺序关系**参考 |
| **产物骨架** | ✅ 已定 | 一律照 **Phase C**；`test_data.yaml` 粒度 = **1 用例 = 1 目录 + 1 YAML**（用户 2026-09-11 确认） |
| **落地方案** | ✅ 已产出 | `rule_table_gen_plan.md`：事实基线 + 产物树 + 映射文件 schema + 生成算法 + 复用清单 + 测试设计 |
| **事实基线（照抄真实 setup）** | ✅ 已提炼 | ①两个 URL 前缀（`/park-access-parking-web/` + `/park-access-parking-rule-new/mock/`）②`carInConfig.*` 是 **bool**、`needPay` 是 int ③**三条回读链**（收费规则→bindPayCode / 车场→parkCode / 白名单→code）④**设备不需 getPage**（`input_extract` 取 `$.json.code`）⑤ `parkName`/`bindPayCode` 字面量 = Round 4「此车场名称已存在」根因 → 生成器强制唯一化 |
| 设备回读 | ✅ 更正 | ~~需 `parkingDevice/getPage` 回读~~ → **`input_extract: <k>DeviceCode: $.json.code`**（两条真实 setup 均此写法） |
| 待你定（4 项） | ✅ 已定（2026-09-11） | ①**新建 `ParkingRuleTable/`** ②**统一前置**（29 条 = 1 story/1 class/1 setup）③`mock/monthlyCar/query` **已实测** ④特殊车牌用 **`我A12345`** |
| **`mock/monthlyCar/*` 实测** | ✅ 已完成 | 探针 `tests/tools/_probe_monthlycar_query_shape.py`。**①`query` 返回裸数组** → JSONPath = `$.[0].code`（**api_defs 记的顶层 `$.code` 是错的**）；**②`delete` 传车牌 → 假成功**（返 `success:true` 但不删，必须传真实 code）；③`add` 允许重复（同车+同场生成多条）；④mock 面无 `retCode` 外壳，`validation` 用 `contains: {success: true}` |
| **接口缺陷登记（原则③）** | ✅ 已决 → **A** | 用户选 A（修正 api_defs+DB） |
| **`mock/monthlyCar` 事实修正** | ✅ 已落盘（2026-09-11） | 新脚本 `tests/tools/_apply_monthlycar_query_shape_fact.py`（照 `_apply_parking_api_defs_facts.py` 同款）。**9 处 desc 修正**：query 七个字段标注「响应根为数组 → `$.[0].<name>`」；delete 的 `codes` 标注「必须传真实 code，传车牌假成功」；add 的 `carNumber` 标注「可重复添加」。已 `--apply --backup --sync-db`（备份 `api_defs.json.20260911105431.bak`，DB 同步 9 行），**幂等复跑 0 变更** |

## H7. 映射数据层落地（2026-09-11，task#17）

> 方案 §三/§六 的映射文件与加载器已产出，并以 **39 项单测**锁定（全绿）。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| **`rule_tables/parking_rule_map.yaml`** | ✅ 新增 | 判定表映射**唯一事实源**（v1.2）。分节：`meta` / `constants` / `table_defaults` / `deferred_rule` / `park_flags` / `park_template` / `pay_rule_template` / `billing` / `park_groups` / `vehicle_kinds` / `vehicle_recipes` / `action_parse` / `action_tokens` / `assert_columns` / `assert_value_map` / `fee_expectation` |
| **`rule_tables/registry.py`** | ✅ 新增 | 加载器（照 `data_factory/registry.py`：模块级缓存 + 双检锁 + `reset_cache()`）+ `match_group` / `resolve_vehicle_records` / `get_park_spec` / `apply_table_defaults` |
| **`tests/test_rule_table_registry.py`** | ✅ 新增 | 39 项：映射结构 / xlsx 交叉校验 / 车辆记录推导 / 分组渲染 / 唯一化 / 表级默认 |
| **xlsx 交叉校验** | ✅ 全绿 | **37/37 条规则全部命中分组**，且落点与 `park_groups[].covers` 一致（covers 覆盖满 37） |
| **`assert_columns` 按实际表头更正** | ✅ 已更正 | 表1 实际只有 **5** 个动作列（col10–col14），表2 有 **2** 个（col8–col9）= 7 个；草稿里的「表1 含缴费列」是**推断错误**，已按 xlsx 表头逐字改 |
| **暂留 8 条改为可推导** | ✅ 已更正 | 不再硬编码条号：`deferred_rule` 声明「表2 命中『使用优惠券-金额/时长』条件列的行暂留」，由适配器在 xlsx 上求得 → 单测断言结果 == `[2,3,4,7,8,9,12,13]`（与草稿一致） |
| **新增 `PO` 上游车场** | ✅ 已登记（原则③） | 表1-11/22「其他车场白名单/包月车」需**第二个车场**持有记录 → 分组数由 **10 组/13 实体** 增至 **11 组/14 实体**（PO 标 `matchable: false`，仅作数据宿主，不参与匹配）。P4 经 `other_parks` 声明依赖，渲染时把 PO 的 parkCode 填入 `otherParkWhiteAccessParks` |
| **`table_defaults` 表级默认** | ✅ 已登记 | 表2 条件列 D–H 全为 `\`，但 R32 注「默认临停车」→ 需 `tempCarIn=是` 才能进场。此规则落数据（不写死进适配器），`apply_table_defaults('表2', cells)` 注入且不覆盖显式值 |
| **`vehicle_kinds` / `vehicle_recipes` 拆分** | ✅ 已定 | 前者 = 记录配方（add/readback/delete + `cleanup` 分类），后者 = xlsx 车辆类型 → 记录列表的有序推导。**一人多车 A/B 的 B 车**由 `when_flag: carWhiteOtherIsCasual`（即条件列「车场是否支持一人多车白名单」=是）门控 —— 依据 xlsx 表1-09/20「按临停计费 A否、B是」与表1-10/21「允许入场 A是、B否」 |
| 推导期修正 | ✅ 已修 | 混合行 xlsx 原文是「主场白名单、子场**包月**」（**无「车」字**），`all_of` 匹配词改用「包月」才命中 |

## H8. 适配器落地 + 复用路径确定（2026-09-11，task#18）

| 项 | 状态 | 说明 |
|:---|:---|:---|
| **`rule_tables/rule_rows.py`** | ✅ 新增 | 判定表 xlsx → Phase C 行结构。产出 `RuleTablePlan{rows, shared, naming, cases}`；`cases` 是喂 YAML 内核的富规格 `CaseSpec` |
| **`tests/test_rule_table_rows.py`** | ✅ 新增 | 24 项：37→29 口径 / 一致性（不静默兜底）/ 动作解析 / Phase C 行结构 / 翻译缓存全覆盖 |
| **37 → 29 已验证** | ✅ | `total=37 active=29 rows=29`，暂留集合 == `deferred_table2_rules` |
| **复用 `_generate_py_file` 的路径** | ✅ 已确定 | 该函数吃 **Excel**（`_read_excel_rows`，Sheet1 九列 + Sheet2「共享前置」）。故适配器反向产出这份 Excel，再**原样调用** `_generate_py_file` —— 生成器代码零改动 |
| **零 LLM 命名** | ✅ 已确定 | `_translate_to_en` 先读 `<excel目录>/translation_cache.json`，**缓存命中即不调 LLM**。适配器把 `naming`（feature/story/29 条 title 的中英对照）写成该缓存 → 命名完全确定 |
| **命名落数据** | ✅ 已登记 | `parking_rule_map.yaml` 新增 `naming` 分节（feature_cn/story_cn/epic_cn + feature_en/story_en + 37 条 `titles`，含 8 条暂留）。`feature_en=ParkingRuleTable` → `test_ParkingRuleTable.py`；`story_en=ParkingRuleTable` → `class TestParkingRuleTable`，class_slug=`parking_rule_table`（对齐方案 §二 产物树） |
| **`\` 哨兵** | ✅ 已登记 | xlsx 用 `\` 表示「不适用」。新增 `action_parse.not_applicable: ["\\"]`，解析时丢弃（否则被当作未知 token 报错） |
| **时长可控（表2 金额断言的前提）** | ✅ 事实已确认 | `mock/access/enter|exit` 收 `time`、`mock/bill/unpaid` 收 `currentTime` → 4h/2h/3h 由**入参显式构造**，不依赖真实等待 |
| **接口清单更正** | ✅ 已更正 | `rule_table_interface_inventory.md` 角色汇总里「设备回读 `parkingDevice/getPage`」已改为**设备不回读**（`input_extract: $.json.code`），与 §一.4 一致 |

## H9. 内核开工前的接口事实更正与待定项（2026-09-11）

### 已确认并已落映射（有实证）

| 项 | 结论 | 来源 |
|:---|:---|:---|
| `parkingPark/getAllWithDevices` 回读 | `json: {parkName: <名>}`（**不带 `mainPark` 过滤**）→ `extract: $.data[0].parkingPark.parkCode`；**主/子场通用** | `SmartParking/setup_data/setup_change_device_parking.yaml` 逐字 |
| 子场 `saveOrUpdate` | `mainPark: false` + `parentCode: ${...父场Code}` + `needCount: 1` | 同上 |
| 特殊车牌字段 | **顶层** `specialAccessConfigList[]`，元素 `{carNumber, enable, parkCode:"", parkName:""}`；`specialCarEnable=true` 时必填 | `api_defs.json` body 清单 |
| teardown 容错 | `_relax_teardown_validation` 把 `**/teardown_*.yaml` 的 `validation` 置为 `[]`（幂等清扫）→ **重复删除不会失败** | `agent_components/generators/_repair_helpers.py:189` |
| 车辆记录归属 | 定为**统一前置**（setup 建全部用例的车、按键回读；teardown 兜底删）。因 teardown 断言已放宽，「转临停车」用例中途删除后 teardown 再删不会报错 | 上两行推论 + 方案 §七#2 |

### 待定（未实证，不猜 —— 需实跑或探针定）

| 项 | 现状 | 影响 |
|:---|:---|:---|
| `mock/access/enter\|exit` 的 `time` **格式** | api_defs 只写「停车开始时间」（string），无格式说明；框架有 `${get_current_time(...)}` 族助手但具体格式码未见用于入场时间 | 表2 金额断言依赖 4h/2h/3h 时长构造 |
| `mock/delAllForMock` 的 `parkCode` 语义 | api_defs **无 body 字段**；现存两处调用都传**字面量 `PARK-FIXTURE`**（占位，本身即缺陷）→ 真实语义未验证 | setup 首步清场是否真清空 |
| `mock/bill/unpaid` 的 `currentTime` 格式 | 同 `time` | 同上 |
| `parkingPaymentConfig/delete` 的请求体形状 | 前序 Round 3 已证 = **裸数组** | 已可用，非阻塞 |

> 处置建议：写内核前先跑一次**只读+最小写入探针**（照 `_probe_monthlycar_query_shape.py` 同款：登录 → 建 1 个探针车场 → 试 `access/enter` 几种时间格式 → 试 `delAllForMock` 的 `ALL`/空参 → 清理），把这两条钉死再生成。

## H10. YAML 生成内核落地（2026-09-11，task#19 + #21）

> 方案 §四「② yaml 内核」已实现，**不走 LLM**：确定性构造 + 逐条引用闭合自检。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| **`rule_tables/yaml_kernel.py`** | ✅ 新增 | 判定表 plan → setup / teardown / 逐用例 `test_data.yaml`。纯函数式拼装（`_step`/`_tc`/`_g` 等），零 LLM、零随机字面量 |
| **`tests/test_rule_table_yaml.py`** | ✅ 新增 | **21 项**：产物清单 / 引用闭合 / 唯一化 / 车场拓扑 / 断言取自 xlsx / 时长口径 |
| **引用闭合** | ✅ 全绿 | 定义键 **113**，setup / teardown / 29 用例**未定义引用 0 处** |
| **唯一化** | ✅ 全绿 | 无字面量 `parkName` / `bindPayCode`；新建车场 `parkCode` 恒为 `''`（服务端生成） |
| **`groups_in_scope` 依赖闭包** | ✅ 已修（实跑发现） | `PO` 是 `matchable: false`（无用例匹配它）→ 原先被过滤掉，导致 P4 的 `otherParkWhiteAccessParks` 为空串、**「其他车场」用例静默失效**。现在并上「在范围内车场通过 `other_parks` 引用的所有上游场」，PO 先于 P4 建立 |
| **子场挂子场收费规则** | ✅ 已修 | 子场若挂 `mainPark: true` 的规则，服务端拒；改为 `_pay_rule_steps` 出两条规则（`prePayCode` 主场 / `preChildPayCode` 子场 `mainPark: false`），按角色绑定 |
| **复合提取键 bug** | ✅ 已修 | `both` 作用域曾渲染成 `get_extract_data('prePS1M,PS1SParkCode')`（无效键）→ 改为 `_refs()` 把多个 `${get_extract_data(...)}` 用逗号连接 |
| **特殊车牌取牌键** | ✅ 已修 | `preCarT1_24ANumber` 曾被引用但无定义（特殊车牌没有独立 add 步）→ 由车场的 `specialAccessConfigList` `input_extract: $.json.specialAccessConfigList[0].carNumber` 提供 |
| **临停车取牌键** | ✅ 已修 | `preTempCarNumber` 曾被引用但无定义 → 新增 `_temp_car_step`：一次 `mock/carTypeQuery` 顺带 `input_extract` 出随机车牌 |
| **`是否缴费` 断言** | ✅ 已修 | 曾硬编码 `payableAmount: 0`（表2-05/10/11 是「缴费=是」，断言必错）→ 改按列 `value_map` 出 `eq: {$.paid: <bool>}` |
| **金额断言单一来源** | ✅ 已定 | `fee_expectation` 是**唯一驱动**；`_assert_steps` 在列循环后无条件调 `_fee_step`（表1-24 无「缴费金额」列但必须断言 fee=0） |
| **表1-24 时长补全** | ✅ 已修（单测发现） | 无 timeline → 出入场同刻（零时长），`fee=0` 是**恒真空断言**。补 `表1-24: 4h`（10:00 入 → 14:00 出），0 才有判别力 |
| **`vehicle_recipes` 一人多车更正** | ✅ 已修（实证推翻原模型） | 原模型把 B 车当**独立记录**且由 `carWhiteOtherIsCasual` 门控 —— 错。实证 `setup_one_person_multi_car.yaml`：**一条记录 + `subCarNumbers`**，`input_extract` 同时取 `$.json.carNumber`(A) 与 `$.json.subCarNumbers[0]`(B)。该 flag 只决定**车场放不放行**副车牌，与记录产出无关。`resolve_vehicle_records` 改为产出 `sub_of`、去掉 flag 门控；`tests/test_rule_table_registry.py` 三处旧断言同步更正 |

## H11. 落盘接线（2026-09-11，task#20）

> 方案 §九。**生成器零改动**：接线层把 plan 转成 `_generate_py_file` 认得的输入。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| **`rule_tables/phase_c_export.py`** | ✅ 新增 | `write_plan_excel` / `write_translation_cache` / `export` / `export_yaml` |
| **`tests/test_rule_table_export.py`** | ✅ 新增 | **10 项**：Excel 被生成器自己的读函数读回 / 缓存合并不丢既有条目 / 落盘不调 LLM / `.py` 结构 / 两条产线同目录 |
| **Excel 独立命名** | ✅ 已定 | `test_plan_parking_rule_table.xlsx`（**不覆盖** SmartParking 的 `test_plan.xlsx`）。原因：`output_base = dirname(excel)`，Excel 必须在 `智慧停车/` 才能让 feature 目录落对位置 |
| **翻译缓存合并写** | ✅ 已定（安全项） | 直接覆写会抹掉 SmartParking 的 67 story / 81 title，令那 82 条用例下次重跑掉进降级。改为 load→update→save；实测 `title_en 81 → 118`，旧条目逐条仍在。另有保险：`_translate_to_en` 仅在**有未命中项**时回写，本批全命中 ⇒ 生成期根本不碰该文件 |
| **零 LLM 落地** | ✅ 已验证 | `object.__new__(ChatTestAgentGraph)` 绕过 `__init__`（不需要 key），补 `_run_timestamp`/`_run_data`；测试把 `_invoke_structured`/`_invoke_think` 打桩为抛异常 |
| **产出** | ✅ 已落盘 | `.py` 1 个（1 class / 29 methods / `order(1..29)`）；YAML 31 个；`__init__.py` 1 个。`run_blocks` 路径 = `./testcase/园区基线/智慧停车/ParkingRuleTable/...` |
| **单测总览** | ✅ 全绿 | `test_rule_table_{registry,rows,yaml,export}.py` 合计 **99 passed** |

**新增文件（2026-09-11）**：`rule_tables/yaml_kernel.py`、`rule_tables/phase_c_export.py`、
`tests/test_rule_table_yaml.py`、`tests/test_rule_table_export.py`。
**改动文件**：`rule_tables/parking_rule_map.yaml`（timeline 补 `表1-24`）、
`rule_tables/registry.py`（`sub_of` + 去 flag 门控 + `get_assert_endpoints`/`get_parking_type`）、
`tests/test_rule_table_registry.py`（一人多车三处旧断言更正）。
**PyCharmMiscProject 新增写入**：`智慧停车\test_plan_parking_rule_table.xlsx`、
`智慧停车\ParkingRuleTable\**`（33 个：1 `.py` + 1 `__init__.py` + 31 YAML）、
`智慧停车\translation_cache.json`（**合并写**，非新增）。

## H12. ParkingRuleTable 两层实跑（2026-09-11，task#22）

命令：`pytest -c pytest.ini "testcase/园区基线/智慧停车/ParkingRuleTable"`（框架 venv）。
证据：`report/temp/*-result.json` 的 allure **原始请求/响应附件**。
详见 `_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md`。

| 轮次 | 结果 | 直接原因 |
|:---|:---|:---|
| Round 1 | **29/29 setup ERROR** | `/mock/*` 无 `{retCode,msg}` 外壳却断外壳 → ✅ 已修（新增 `validation_mock_read`；`TestMockFaceAssertions` 又抓出第三个漏网 `mock/monthlyCar/delete`） |
| Round 2 | **6 passed / 23 failed** | 五簇，见下 |

### 正面结论（重要）
`mock/bill/latest` 返回的 `payableAmount` 与推导值**逐条一致**：
表2-05 **20.0** / 表2-10 **25.0** / 表2-11 **15.0** → **金额口径与 timeline 构造正确**。
（唯一错项：`paid` 是**实缴**标志，未缴费服务端给 `false`；不应断 `paid: true`。）

### 五簇归属
| 簇 | 性质 | 用例 | 证据 |
|:---|:---|:---|:---|
| **A1** 按临停计费查询打错车场 | 🆕 生成器 bug | 表1-02、表1-13 | 入子用 `DEV-S1-1`，查询却打 `PO` 的 parkCode → `inPark:false` |
| **A2** `bill/unpaid` 时机/参数错 | 🆕 生成器 bug | 表1-24、表2-05/06/10/11 | 参照 VehicleAccess 004：应**在场时查**且 `currentTime` **晚于入场**；我生成「出场后查 + currentTime=入场时刻」→ `请求失败` |
| **B** 包月车记录不生效 | ⚠️ setup 数据 | 表1-12/13/14/15/16/17/18/19/20/21/22 | 入主被拒 或 `parkingType:"2"`（期望 `1`）；但表1-17 的删除**回读到 code** ⇒ 记录写了、**未对该车场生效** |
| **C** 一人多车开关与结果疑似反 | ❓ 待定性 | 表1-08/09/10/19/20 | `=false` 时后车仍按白名单放行；`=true` 时两车**全拒** |
| **D** 次级待查 | ❓ | 表1-11（他场准入 parkCode 形态）、表1-23（白名单+包月双记录） | — |
| **E** 表2-01 无账单 | 🆕 生成器 bug | 表2-01 | 无进出场动作 → `bill/latest` 返 `{}` |

通过：表1-01/03/04/05/06/07（6 条）。

### 待用户决策（原则③，两轮预算已用完 → 未擅自动手）
1. A1/A2/E 修法明确，可改；
2. **B/C 需先判「数据没给全」vs「接口缺陷」** → 建议先做 1 次最小对照实验（单条 `monthlyCar/add` + `query` 回读）；
3. 判定表「是否缴费」列口径：**实缴** vs **应缴**。

**新增文件**：`_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md`。

## H13. B/C 簇最小对照实验（2026-09-11，用户批准）

工具：`tests/tools/_probe_monthly_car_recognized.py`（aux，不参与系统运行）。
方法：复用框架 `RequestsBase.replace_load` 原样跑完 setup（110 步），在**同一现场**探针，末尾跑 teardown 清场。
踩坑：`api.replace_load` 每调一次重掷 `random_code`/`random_plates` ⇒ **只能替换一次**，
`input_extract` 必须从**同一个 payload** 取（框架语义 `$.json.<field>`）；取错对象会让所有车号变 `None`、探针作废。

| 簇 | 定性 | 关键证据 |
|:---|:---|:---|
| **B 包月车** | ✅ **接口逻辑 Bug**（我方数据正确） | `monthlyCar/query` 返回记录且 `parkCode` 与 `preP2ParkCode` **逐字相同**；入场后 `carTypeQuery` 仍给 `"2"`(临停)。对照：**同 parkCode** 下白名单记录能正确返回 `4` ⇒ `carTypeQuery` 不识别 `monthlyCar/add` 的记录 |
| **C 一人多车** | ⚠️ **行为与判定表相反**；映射待核 | `carWhiteBlack/getPage` 证实记录在（`code: zhyqB54665556`，`multi`+`subCarNumbers` 写入成功），该车入场 P3 仍 `openGate:false`；同写法在 P2 放行。**未排除**：列「是否支持一人多车白名单」→ `carWhiteOtherIsCasual` 的**映射本身未验证** |

**新增文件**：`tests/tools/_probe_monthly_car_recognized.py`、
`_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md`（H12 已建，本次补 B/C 实验结论 + §F 缴费接口缺口）。

### §F 新发现（用户指出）：缴费接口未接入
判定表动作列**没有缴费动作映射** ⇒ `bill/latest.paid` 恒 `false`。api_defs 内有两条且名称正好对上：
`mock/payWhenLeave/simulate`（出场缴费模拟 → 表2-05）、`mock/pay/simulate`（提前出场缴费 → 表2-06）。

## H14. teardown 留存开关（2026-09-11，用户要求「跑完要能进去看数据」）

**问题**：fixture 收尾跑 teardown，把本轮建的 **14 车场 + 28 设备 + 12 白名单 + 12 包月车 + 2 收费规则**
全删，跑完现场什么都不剩 → 失败原因无从人工排查。

**改动**（**不动 `py_export.py`** —— 它是 Phase C 格式权威、生产管线共用）：

| 项 | 说明 |
|:---|:---|
| `rule_tables/phase_c_export.py` | 新增 `_comment_out_teardown(py_path)` + `export(..., keep_data=True)`；返回值加 `teardown_commented` |
| 行为 | `keep_data=True`（**当前默认**）→ 生成后把 fixture 里 teardown 调用**整段注释**（只注释，不删文件）；`False` → 恢复可执行 |
| 产物 | `test_ParkingRuleTable.py` 的 fixture 里 teardown 已注释，路径保留可一键恢复 |

**踩坑（被新单测抓到）**：`[^']*teardown_[^']*` 会**连 setup 那行一起注释** —— 只要路径任一段含 `teardown_` 就误命中
（pytest 的 tmp_path 目录名带用例名时必现，如 `…/test_teardown_commented_by_def0/…`）。
已收窄为**文件基名** `'[^']*/teardown_[\w\-]+\.yaml'`。

**新增测试**：`tests/test_rule_table_export.py::TestKeepData` 3 项（默认注释 / `keep_data=False` 恢复 / setup 不被误伤）。
规则表四套件合计 **104 passed**。

**注意**：setup 第 1 步的 `mock/delAllForMock` **仍在**，即**下次再跑会把本轮留存的数据清掉** ——
留存的有效期是「跑到下次跑之前」。

**改动文件**：`rule_tables/phase_c_export.py`、`tests/test_rule_table_export.py`。

## H15. 第 3 轮「看不到车辆记录/进出记录」根因：车场编码回读取错（2026-09-11）

**用户报告**：「这轮跑下来还是没有车辆记录和进出记录」。查证结论：**记录在**，是回读机制取错了车场编码。

### 事实（只读盘点 `tests/tools/_check_parking_env_state.py`）
- 本轮 14 车场 `createTime=14:53:46~52`；**白名单 10 条 `createTime=14:54:08~27`**（=车辆记录，在的）
- 报告附件统计 `access/enter` **71 次** / `access/exit` **63 次** —— 进出请求确实发出
- 数据挂在**测试车场**（`PK-P1_h8xft6` 等随机名）下，不是默认园区 → 查看时要在车场列表里选

### 根因（我方生成器 Bug）
`getAllWithDevices` 的 `parkName` / `parkCode` **服务端完全不过滤**（实测 107/107/107），只有 `mainPark` 生效。
所有 `preXXXParkCode` 靠 `$.data[0]` 回读 ⇒ 退化为撞排序运气。日志逐字：
`line 457` 请求 `parkName=PK-PS3M_hfx8hc` → `line 458` `data[0]=id2705 PS2S` → `line 465` `prePS3MParkCode: zhyq846724013278630`(**PS2S 的码**)。

接口文档却写着「按 parkName 查询时精确命中的车场位于 data[0]」→ **契约违背 ⇒ 接口缺陷**（三层判障 §2）。
同时我方生成器必须加固：不能把正确性押在未实现的过滤上。

### 波及面：4 个子场拓扑全坏
| 车场 | 设备 | 根因 |
|:---|:---|:---|
| PK-S1_mz6dca | **0 个** | `preS1ParkCode`→P6 的码 |
| PK-PS1S_ana9ia | **0 个** | `prePS1SParkCode`→PS1M 的码 |
| PK-PS3M_hfx8hc | **0 个** | `prePS3MParkCode`→PS2S 的码 |
| PK-PS3S_p4lvo1 | 有，但 `parentCode`→PS2S | 同源 |

连带表1-03 白名单 `parkCode` = `PS1M,PS1M`（应 `PS1M,PS1S`）。
⇒ 用 S1/PS1S/PS3M 设备"入场"实际进的是 P6/PS1M/PS2S，**子场一片用例结果不可信**。

### 修复约束（均已实测）
| 约束 | 结论 |
|:---|:---|
| 自定义 `parkCode` | ❌ 拒绝（`请求失败`），码服务端生成 |
| 车场重名 | ✅ 服务端强制唯一（`此车场名称已存在`） |
| `extract` 代变量 | ❌ `apiutil.py:156` 先 pop 再 replace_load |
| jsonpath 过滤器 | 基础 `parse` 不支持 `[?()]`；**`jsonpath_ng.ext`(1.7.0 已装) 支持**，实测取码成功 |
| 顺带 | `parkingPark/deleteByCodes` 要**裸数组**；我方 teardown 用的是 `"a,b"` 字符串，待核对 |

### 方案（待用户确认，原则①）
- **甲（推荐）**：车场名确定化 ＋ 框架 `extract_*` 改 `jsonpath_ng.ext.parse`（超集、向后兼容；
  当前这类表达式是抛异常被吞、**静默不写 key**）→ 按名精确取码，与排序无关。
  ⚠️ 动 `base/apiutil.py`（SmartParking 82 条共用）→ 原则③先报备。
- **乙（不动框架，不稳）**：确定化名字 ＋ `mainPark` 过滤 ＋ `data[0]`；同秒创建翻车（已实测），不推荐。

**本次新建文件**：`tests/tools/_check_parking_env_state.py`（只读盘点）。
**本次更新**：`_phase_c_selective_2026-09-09/parking_rule_table_round2_attribution.md` §五。
**探针残留已清理**：`zhyq918087852392965`(PK-PROBE_CODE2) 已删，车场总数回到 107。

## H16. 车场编码回读按名筛选：框架加固 + 生成器改写（2026-09-11，task#23/#24）

> 方案：`changelog/2026-09-11_parkcode_readback_by_name.md`（用户「做吧」批准 = 方案甲 + conftest 快照）。

| 项 | 状态 | 说明 |
|:---|:---|:---|
| 框架 `base/apiutil.py` ① | ✅ 已改 | 新增模块级 `jsonpath_parse()`：优先 `jsonpath_ng.ext.parse`（基础语法**超集**，支持 `[?()]` 过滤器），`ext` 缺失退回 `jsonpath_ng.parse`（行为与改前逐字一致） |
| 框架 `base/apiutil.py` ② | ✅ 已改 | 三处调用点（`extract_input_data`/`extract_data`/`extract_list_data`）改用它 —— 原来过滤器表达式抛 `JsonPathLexerError` 被 `try/except` **吞掉**，表现为静默「提取不到数据」 |
| 框架 `base/apiutil.py` ③ | ✅ 已改 | `extract` / `extract_list` 补 `self.replace_load(...)`，与请求参数同等对待 → `${}` 能注进 jsonpath |
| 框架 `conftest.py` | ✅ 已改 | `clear_extract_data` 清空前把 `extract.yaml` 复制为 `extract.last_run.yaml`（空文件不留）；路径取 `conf.setting.FILE_PATH['extract']`，不受 cwd 影响 |
| 生成器 `rule_tables/yaml_kernel.py` | ✅ 已改 | `_park_steps` 回读表达式 `$.data[0]…` → `$.data[?(@.parkingPark.parkName=='${get_extract_data('preXParkName')}')]…`（14 个车场全部按名）。车场名**仍走 random_code**，未固定化 |
| 兼容性实测 | ✅ 723/723 | 全框架 `testcase/**/*.yaml` 存量 jsonpath 表达式用 `ext.parse` 静态扫描：**723 通过 / 0 失败** |
| 测试 | ✅ 全绿 | 新增 `tests/test_framework_extract_by_filter.py`（8 项，**框架 venv**）+ `tests/test_rule_table_export.py::TestParkCodeReadbackByName`（3 项）；`test_rule_table_export.py` 合计 17 passed |
| 顺带核实 | ✅ 无需改 | H15 登记的「teardown 删除形状待核对」→ 生成器**本来就是对的**：`parkingPark/deleteByCodes` / `carWhiteBlack/delete` 裸数组、`parkingDevice/deleteByCodes` 逗号串 |

**新增文件**：`changelog/2026-09-11_parkcode_readback_by_name.md`、`tests/test_framework_extract_by_filter.py`。
**repo 改动**：`tests/test_rule_table_export.py`（+`yaml_kernel` import、+`TestParkCodeReadbackByName`）、`rule_tables/yaml_kernel.py`、`_phase_c_selective_2026-09-09/MANIFEST.md`（本节）。
**PyCharmMiscProject 改动**：`base/apiutil.py`、`conftest.py`（本轮唯一改动外部树的两个文件；均**原位编辑**、无新增文件）。

### 验证实跑（第 4 轮）

| 项 | 结果 | 证据 |
|:---|:---|:---|
| 产物重生成 | ✅ 33 个（1 `.py` + 1 `__init__.py` + 31 YAML）；setup `parkName==` **14 处**、`data[0]` **0 处** | `ParkingRuleTable/setup_data/setup_parking_rule_table.yaml` |
| 框架实跑 | **6 passed / 23 failed** —— 与第 2 轮**同分、无回归**（失败全属未批准的 A1/A2/B/C/E/F） | `prt_round4_junit.xml`、`prt_round4_pytest.log` |
| **车场拓扑** | ✅ **14/14 全对**（第 3 轮：S1→P6、PS1S→PS1M、PS3M→PS2S、PS3S 挂错父场，4 个全坏） | 只读复核 `getAllWithDevices` 逐场比对码+设备 |
| 连带修复 | ✅ 表1-03 `parkCode` = **PS1M,PS1S**（第 3 轮为 PS1M,PS1M） | `log/test.20260911153600.log:994` |
| 车辆记录 | ✅ 10 条白名单**逐条挂对车场**（`青Y09730 → PS1M,PS1S` 等） | `carWhiteBlack/getPage` 实查 |
| conftest 快照 | ✅ `extract.last_run.yaml` 已落盘（14 车场名↔码↔设备码↔车牌） | 本机 `C:\Users\damai\PyCharmMiscProject\extract.last_run.yaml` |

### 🆕 本轮新发现（❗定性已更正，**未改**）

**多场白名单记录的编码回读失效。**
~~初判「`getPage` 带 `parkCode` 即返 0 = 接口缺陷」~~ —— **错**。用户追问"parkCode 不能带空串么"后实测：
该过滤**是等值匹配、工作正常**（单场记录传自己的单码 → 命中 1 条）。坏的是**多场记录的 `parkCode` 存的是复合串**，
任何等值查询都命中不了 —— **含它自己那个串**。属三层判障 §3「输入错误 ⇒ 我方修」。

| 记录 | 自己的单码 | `自己,别的` | 别的单码 | `''` | 不传 |
|:---|:---|:---|:---|:---|:---|
| 单场（`parkCode=P1`） | ✅ 1 | ❌ 0 | ❌ 0 | ✅ 1 | ✅ 1 |
| 多场（`parkCode=PS1M,PS1S`） | ❌ 0 | ❌ 0 | — | ✅ 1 | ✅ 1 |

⇒ **空串 ≡ 不传**（都=不筛）；受影响用例 = both 作用域白名单（如**表1-03**）；
连带 `preCarT1_XXAWhiteCode` 拿占位串 → teardown 删不掉、记录残留。
**修法候选（待批准）**：回读体不传 `parkCode`、只留 `searchKey:<完整车牌>`
（`searchKey` 是唯一的可用筛选，模糊匹配；`carNumber` 字段被完全忽略）。
对照 **`mock/monthlyCar/query` 的 `parkCode` 正常**（对码 1 条 / 错码 0 条）⇒ **不要一起删**。

**未做（待决策）**：A1（子场查询 scope 取 `eids[0]`）、A2（`bill/unpaid` 时机 + `currentTime`）、E（表2-01 无进出场）、F（未接 `payWhenLeave/simulate`、`pay/simulate`）、C 的列→字段映射核对、「是否缴费」口径、上述白名单回读。

---

## H17. 第一部分修复（A1 + 多场白名单回读 + A2 顺序 + F 缴费）（2026-09-11，task#26/#27）

用户指令：**「先改第一部分」** + **「直接改yaml文件和代码，先不走生成」**。
第一部分 = 我先前列出的 5 项生成器侧问题（A1 / A2 / E / F / 多场白名单回读）。

**用户决策（2026-09-11）**：① 金额断言先不改、**挂起等接口方**；② 表2-01 **挂起**；
③ `paid` 严格按判定表，流程改为「**先查询缴费金额，再看能不能出场**」；④ 缴费动作**接入**。

### 已改（4 项，均已验证）

| # | 项 | 改法 | 验证 |
|:---|:---|:---|:---|
| A1 | 「按临停计费」查询打错车场 | `_parking_type_call(case, p, entry_token)`：`scope` 为 None 时按**入场 token** 定场（入主→主场 / 入子→子场），不再兜底 `eids[0]` | 表1-02/1-13 的 `parkCode`：`prePOParkCode` → **`preS1ParkCode`** ✅ |
| task#26 | 多场白名单编码回读失效 | 映射新增 `vehicle_kinds.白名单.readback_multi`（`omit_body: [parkCode]` + `extract` 用 `[?(@.carNumber==…)]` 过滤）；`_car_readback_step` 按 `len(park_eids) > 1` 分流。**单场路径逐字不动**；`包月车` 不加 `readback_multi`（其 `parkCode` 实测正常） | 表1-03 回读体**无 `parkCode`**、extract 带 filter；表1-04/1-14 逐字不变 ✅ |
| A2(顺序) | 查金额/缴费的**时机** | `_fee_steps` 插在 `build_case` 里 **第一次出场之前**（`token.startswith("出") and not fee_inserted`），`currentTime` = 该次出场时间。**金额口径不动**（决策①） | 表1-24/2-05/2-06/2-10/2-11 落盘顺序复核 ✅ |
| F | 缴费动作未接入 | 数据层新增 `pay_actions`（`mock/pay/simulate` 提前缴费 / `mock/payWhenLeave/simulate` 出场缴费）；`rule_rows.CaseSpec` 加 `prepay` 读表2 条件列；`_pay_step` 按条件二选一 | 表2-05/2-10/2-11 → `payWhenLeave/simulate`；表2-06 → `pay/simulate` ✅ |

**新增探针（实证，非推断）**：`tests/tools/_probe_whitelist_readback_shape.py` —— 多场记录
带复合串/带单码 **total=0**、**不传 `parkCode` total=1** 且 filter 取到的 code 与记录自己的 code 一致。

**测试**：`tests/test_rule_table_export.py` **31 passed**（+`TestParkingTypeQueryTargetsEntryPark` 2 项、
+`TestMultiParkWhitelistReadback` 5 项、+`TestFeeQueryAndPayActions` 7 项）。
框架侧 `tests/test_framework_extract_by_filter.py` 8 passed（框架 venv）。

### ⏸ A2 金额口径 / E 的**前提被实跑推翻**（决策后：挂起等接口方）

实证脚本：`tests/tools/_probe_pay_semantics.py`、`tests/tools/_probe_bill_latest_points.py`（P6/P2，自建自清）。

| # | 原方案 | 实证结果 | 决策后 |
|:---|:---|:---|:---|
| **A2** | 「`bill/unpaid` 移到出场**前** + `currentTime` = 出场时刻」即可修好金额断言 | ❌ **前提不成立**：`bill/unpaid` 在该环境**恒返** `{"retCode":0,"msg":"fail","data":"请求失败"}` —— 入场时刻/出场时刻/出场尝试后/带 `parkCode` **全都失败**。它不是时机问题，是**该端点不可用**。金额断言只能改走 **`bill/latest.payableAmount`**（第 2 轮已证实返 20/25/15，与推导逐条一致） | 顺序已改（见上表）；**金额口径不动**，端点恒失败 → **挂起等接口方**（决策①） |
| **E** | 「表2-01 补一次入+出」 | ⚠️ 判定表该行「条件（出入场动作）」**逐字就是 `\`**（原始 xlsx 第 34 行）——补动作是**我方发明**。另：实测 **P2（`needPay`=0）出场后仍算 20.0**，"不收费车场"在 mock 侧未生效 → 表2-01 的 `payableAmount: 0` **当前必然失败** | **挂起等接口方**（决策②） |
| **F** | 「接入 `payWhenLeave/simulate`（表2-05）、`pay/simulate`（表2-06）」 | ✅ 接口本身可用（均返 `success:true`、出场转 `openGate:true`、`latest.paid` 变 `true`）；顺序与选口规则已定；**表2-06 冲突**：判定表写「动作（是否缴费）=否」，而"提前缴费=是"执行后 `paid` 实测 **=true** | **已接入**（决策④）；表2-06 的 `paid` **严格按判定表断 `false`**（决策③），冲突如实登记 |

**新增实证事实（可复用）**：

| 事实 | 值 |
|:---|:---|
| `bill/latest` **出场前** | 返 `""`（空串）——**连查两次、间隔 1s 都是空**，不能作为断言点 |
| `bill/latest` **出场后** | 返完整账单：`{carNumber, code, paid, payableAmount, paidAmount, entryTime, exitTime, parkingType}` |
| 未缴费 | `exit` → `openGate:false`；`latest` → `paid:false, payableAmount:20.0` |
| `payWhenLeave/simulate` 后 | `latest`（再出场前）→ `paid:false, payableAmount:20.0`；**出场** → `openGate:true`；`latest` → `paid:true, payableAmount:0.0, paidAmount:0` |
| `pay/simulate`（提前缴）后 | `exit` → `openGate:true`；`latest` → `paid:true, payableAmount:0.0`（⚠️ 该单 `entryTime` 被改写成出场时刻，mock 怪癖） |
| 「是否缴费」口径候选 | `paid = (是否提前缴费=是) or (动作（是否缴费）=是)` —— 4 条表2 用例全部自洽（01 否/05 是/06 **是**/10、11 是） |

**挂起 / 待接口方（原则③汇总，不擅自动手）**：
① `bill/unpaid` 恒返 `请求失败` 的定性与修法（决策①：挂起）；
② 表2-01「不收费车场」在 mock 侧如何成立（决策②：挂起）；
③ 表2-06 的 `paid` 口径冲突（决策③：已定「严格按判定表」，冲突登记待接口方）；
④ 表2-10/2-11 缴费点是否应挪到「出主」（现按字面落在第一次出场前）；
⑤ C 的列→字段映射 `carWhiteOtherIsCasual`；⑥ 8 条券用例仍 deferred（无核销接口）。

**预期仍红**：表2 各条的缴费步骤 `fee` 依赖 `bill/unpaid` 回读（占位串）⇒ 与决策①同一根因，
**不做局部兜底**（避免掩盖接口问题）；表2-06 的 `paid: false` 与实测 `true` 冲突同理如实留红。

**新增文件**：`changelog/2026-09-11_part1_a1_and_multipark_readback.md`（本轮决策载体）、
`tests/tools/_probe_whitelist_readback_shape.py`、`tests/tools/_probe_pay_semantics.py`、
`tests/tools/_probe_bill_latest_points.py`。
**repo 改动**：`rule_tables/yaml_kernel.py`（A1 + 回读分流 + `_fee_steps`/`_pay_step`/`_no_fee_step`）、
`rule_tables/parking_rule_map.yaml`（`readback_multi` + `pay_actions`）、`rule_tables/registry.py`
（`get_pay_actions`）、`rule_tables/rule_rows.py`（`CaseSpec.prepay`）、`tests/test_rule_table_export.py`
（+14 项 → 31）、`_phase_c_selective_2026-09-09/MANIFEST.md`（本节）。
**产物已落盘**：`PyCharmMiscProject/testcase/园区基线/智慧停车/ParkingRuleTable/` 下 **31 个 YAML 已重生成**
（`yaml_kernel.generate`，**纯确定性、不经 LLM**），等效于「直接改 YAML」。`.py` 与 Excel/翻译缓存**未重写**
（未跑 `export()`），下一轮实跑前按需补齐。

---

## H18. 券用例解禁：`/mock/addCouponAndUse` 接入（2026-09-11，task#28/#29/#30）

**决策载体**：`changelog/2026-09-11_coupon_cases_unblock.md`（原理由、契约、逐例推导、顺序、未验证项）。
**用户指令（2026-09-11）**：「优惠券注意，填写优惠券信息是**直接创建并核销**」「`/mock/addCouponAndUse`」
「金额券默认 **5**」「时长券测三种情况：① 时间正好可抵消 ② 时间超出停车时间 ③ 必须主场下带子场、
时间少于总停车时间但多于任意一场」。「在子场提前核销亦可，只要出场时金额是正确的」。

H17 的 8 条券用例暂留理由为「无券→行程核销接口」—— 新接口发布后该前提消失，**`deferred_rule` 触发列清空**。

### 已改（数据层 + 内核，均已验证）

| # | 项 | 改法 |
|:---|:---|:---|
| 1 | `deferred_rule` | `columns: []`（机制保留、开关不动）；`meta.deferred_table2_rules: [2,3,4,7,8,9,12,13] → []` |
| 2 | `coupon_actions`（新） | 端点 `mock/addCouponAndUse`（= 8137 `park-access-parking-rule-new`）+ body 模板，占位符 `<K>CouponType` / `<D>Data` / `<C>CarNumber` / `<T>Time` 由内核展开 |
| 3 | `coupon_expectation`（新） | 逐例 `[{type, data}]` —— **券参数的唯一来源**；双券为两条（金额在前） |
| 4 | `fee_expectation` | 6 → **14** 条（补 8 个抵扣后金额） |
| 5 | `timeline` | 6 → **14** 条（补 8 条：主场 4 条 `入主0/出主4`；主+子 4 条 `入主0/入子2/出子5/出主5`） |
| 6 | `_coupon_steps`（新） | 插在 `build_case` 第一次出场之前的 **`_fee_steps` 之前**（抵扣要先落账单） |
| 7 | `registry.py` | `get_coupon_actions()` / `get_coupon_expectation()` |

### 逐例取值（判定表第 9 列公式，单价 5 元/h）

| 用例 | 券 | data | 期望金额 |
|:---|:---|:---|:---|
| 表2-02 | 金额 | 5 | 15 |
| 表2-03 | 时长 | 240 ← **场景①「正好抵消」** | 0 |
| 表2-04 | 金额+时长 | 5 + **60**（⚠️ 见未验证 ①） | 10 |
| 表2-07 | 金额（+提前缴费） | 5 | 15 |
| 表2-08 | 时长（+提前缴费） | 300 ← **场景②「超出停车时间」** | 0 |
| 表2-09 | 金额+时长（+提前缴费） | 5 + **60** | 10 |
| 表2-12 | 金额（主+子） | 5 | 20 |
| 表2-13 | 时长（主+子） | 240 ← **场景③「>子180 且 >主120 且 <总300」** | 5 |

**顺序（不可调换）**：`入主/入子 → [券核销] → [缴费金额查询] → [缴费模拟] → 出主/出子 → [是否缴费校验]`。
主+子两条（表2-12/13）落在**出子之前**、用子场出场时刻（用户口径：子场提前核销亦可）。

### 测试（原则④）

| 文件 | 结果 |
|:---|:---|
| `tests/test_rule_table_export.py` | **41 passed**（31 → 41；新增 `TestCouponRedemption` 10 项：8 条全生成/顺序/`couponType`/`data`/双券两步/车牌引用/区间断言/金额/`handleTime`/`timeline`/mock 断言外壳） |
| `tests/test_rule_table_rows.py` | 37 → **37**（`active_cases`=37、`rows`=37、新增 `test_no_case_is_deferred`、`include_deferred` 开关仍可用） |
| `tests/test_rule_table_yaml.py` | 23 passed —— 修 `test_case_refs_all_defined`：原断言要求用例引用**全部**来自 setup，而 H17 的 `unpaidFee` 是**用例内**「查询→回读→缴费消费」的局部链 ⇒ 改为「setup **或同文件更早步骤**定义」的闭包（**测试口径修正，非产物放宽**） |
| 规则表四文件合计 | **132 passed**（另一并跑：全仓 `1012 passed`；11 项失败均为环境类：框架 conftest 在仓库 venv 下 `No module named 'common'` 2 项、`test_phase_bc_unit` 9 项） |

**产物已落盘**：`PyCharmMiscProject/testcase/园区基线/智慧停车/ParkingRuleTable/`
—— **39 个 YAML**（37 用例 + setup + teardown，`17 → 29 → 37`）、**`test_ParkingRuleTable.py` 37 methods / 37 `order`**、
Excel + 翻译缓存合并重写；`keep_data=True` ⇒ fixture 收尾 teardown **已注释**（路径保留）。
本步跑了 `export()`（H17 未跑），故 `.py` 与 Excel 一并补齐。

### ⚠️ 未验证 / 待实跑校准（原则③登记）

① 双券的**时长券 60min** 为保留「两券都真实生效」而取的数（取 240 会把应缴抵成 0 再减 5 → 负值），
   **未获用户逐字确认**，改动只影响 `coupon_expectation` 两行；
② `handleTime` = 出场时刻是否被 mock 接受（文档口径是「在场时间内」，出场时刻是边界值）；
③ 一次行程连核销两张券是否支持（表2-04/09 各调两次）；
④ 「优先抵扣主场时间」是否真在 mock 侧生效（表2-13 的 5 元完全依赖它）；
⑤ 表2-08「超出部分作废」是否不报错；
⑥ 券后金额断言仍走 `bill/unpaid` ⇒ **8 条预期仍红**，与决策①同一根因，**不做局部兜底**。

### 挂起清单（承接 H17，⑥ 已解除）

① `bill/unpaid` 恒返 `请求失败`（决策①）；② 表2-01「不收费车场」；③ 表2-06 `paid` 口径冲突；
④ 表2-10/2-11 缴费点是否应挪到「出主」；⑤ C 的 `carWhiteOtherIsCasual` 映射。
**⑥「8 条券用例仍 deferred」→ 本轮解除。**

**新增文件**：`changelog/2026-09-11_coupon_cases_unblock.md`、`tests/test_rule_table_export.py::TestCouponRedemption`（类内）。
**repo 改动**：`rule_tables/parking_rule_map.yaml`（`coupon_actions` / `coupon_expectation` / `fee_expectation` /
`timeline` / `deferred_rule` / `meta.deferred_table2_rules`）、`rule_tables/yaml_kernel.py`（`_coupon_steps` + 插点）、
`rule_tables/registry.py`（2 个 getter）、`rule_tables/rule_rows.py`（docstring 口径）、
`tests/test_rule_table_export.py`、`tests/test_rule_table_rows.py`、`tests/test_rule_table_yaml.py`、本清单。

---

## H19. 接口修复后重跑：28 红的分组归因 + 三条根因（2026-09-11，task#31）

**触发**：用户 2026-09-11「接口已修改，再测试下」—— `mock/bill/unpaid` 由「恒返 400」改为正常返
`{"fee": N}`（H17 决策①解除）。重跑 ParkingRuleTable 全量。

### 实测

| 轮次 | 结果 |
|:---|:---|
| 全量 | **28 failed / 9 passed**（179s） |
| 隔离 `-k t2_05` | **1 passed**（该例 `bill/unpaid` 返 `fee:20.00` ✅） |
| 隔离 `-k "t2_03 or t2_05"` | 1 passed（表2-03）/ 1 **failed**（表2-05） |
| 隔离 `-k "t2_05 or t2_06"`（**都无券**） | 第二个**failed** |
| 隔离 `-k "t2_02 or t2_05"` | 第二个 **failed** |

**绿**：表1-01…07、**表2-02**（金额券 5 → 15）、**表2-03**（时长券 240 → 0）
⇒ 券核销链与抵扣算法本次**首次实跑通过**。

### 根因 A：表2 全批共用一个临停车牌 ⇒ 第二条起塌成 0（**数据/编排**）

* setup `PRE-004` 只生成**一块**临停车牌（`random_plates(1)` → `preTempCarNumber`），37 条用例里
  **每一条临停用例都引用它**（全量跑实测全为同一车牌）。
* 现象：全量跑中**只有第一条真查账单的用例（表2-02）拿到正确金额**，此后每条（**含无券的表2-05/2-06**）
  `bill/unpaid` 一律 `fee:0.00`。
* 隔离实验：任取「第二个」用例必红，且**与券无关**（`t2_05→t2_06` 都无券也红）
  ⇒ 载体是**按车牌的访问状态**，不是券残留。
* 表2-01 的 `bill/latest` 返 `{}`（免费车场）**不在本根因内**，仍属挂起决策②。

### 根因 B1：记录有效期不覆盖时间线（**生成侧**，`parking_rule_map.yaml` 两处）

* 白名单 `multi` 体与包月车体的有效期用 `${get_current_time(ydm)}` / `${get_offset_time(ydm, 365)}`
  ⇒ 实际 `2026-09-11 → 2027-09-11`；而访问时刻是字面量 **2029-12-31**（`time_format.base_date`）。
* 单变量探针（**同车场 P1、同设备、同时刻，仅有效期不同**）：

  | 记录 | 有效期 | 入场 | carTypeQuery |
  |:---|:---|:---|:---|
  | A 包月车 | 2026-09-11 → 2027-09-11 | **false** | `null`（不认账） |
  | B 包月车 | 2029-01-01 → 2030-12-31 | **true** | `"1"` 包月车 ✅ |

* 命中用例：表1-12/13/14/15/16/17/18/22/23（包月车族）。
* 白名单组**不被日期挡**（探针 D：白名单 `multi:true` + 2026→2027 日期 @P1 → 入场 **true**）——
  见 B2。

### 根因 B2：车场开关语义未实现（**接口侧**）

* 单变量探针（**同一 add 体、同一时刻，仅车场不同**）：

  | 记录 | 车场（分组开关） | 入场 |
  |:---|:---|:---|
  | 白名单 `multi:false` | P3（`carWhiteOtherIsCasual:true`） | **true** |
  | 白名单 `multi:true` | P3（同上） | **false**（主车、子车**都**拒） |
  | 白名单 `multi:true` | P1（无开关） | true |

* 我们的**分组定义逐字忠于 xlsx**（表1-08 只勾「一人多车白名单=是」；表1-11 只勾「其他车场白名单准入=是」；
  表1-24 只勾「特殊车牌放行=是」），**add 体逐字忠于接口文档**（`multi` / `subCarNumbers` 均在册）。
  ⇒ 差异只能出在 mock 侧，**不在生成侧**。
* 命中用例：表1-08/19（P3）、表1-09/10/20/21（子车应作临停/被拒，实际一律放行）、
  表1-11/22（P4 其他车场准入，记录在 PO、进场在 P4 → 拒）、
  表1-24（**放行成立但计费未免**：`mock/bill/unpaid` 返 20，判定表「按临停计费=否」期望 0）。

### 新增 aux（`tests/tools/`，不参与系统运行）

* `_probe_bill_unpaid_shape.py` —— `bill/unpaid` 形状 / 各 currentTime / header 变体 / 2029 字面量组合探测
  （H17 决策①的证据链）；
* `_probe_vehicle_record_validity.py` —— 有效期窗口 A–E + 车场开关 F–H 的**单变量对照**（B1/B2 的证据）。

### 待用户决策（原则①，未动代码）

1. **根因 A**：每条临停用例各自生成独立车牌（改 `yaml_kernel` 的 `_temp_car_step`），还是每例前清一次场？
   —— 倾向独立车牌（最小改动、贴近真实场景）。
2. **根因 B1**：记录有效期改为覆盖 `time_format.base_date` 的**字面量**（与 §9a「用显式字面量」同一理由，
   仅改 `vehicle_recipes` 两处），还是把时间线整体改为**相对当前时间**？
   （后者顺带满足用户口径「出场时间不要超过当前时间，入场时间可以提前」，但改动面大得多。）
3. **根因 B2**：P3/P4/子车语义 + 特殊车牌不计费 —— 一并作为**接口缺陷**提给接口方？

## H20. H19 重跑（2026-09-14）：27 红归因 —— 产物未重导出 + 同槽位车牌覆盖（2026-09-14，task#32）

**触发**：用户 2026-09-14 重跑全量，日志 `C:\Users\damai\PyCharmMiscProject\log\test.20260914092925.log`（+ `.log.1`）。
用户要求：只做**我方生成数据问题**的归因，分「确定 / 不确定」两类，不做其它动作。

### 实测

| 项 | 值 |
|:---|:---|
| 用例 | 37 例 → **27 failed / 10 passed** |
| 断言失败 | **59 处** |

### 根因 A（新，**生成侧 + 流程**）：部署产物未随内核重新导出

* H19 决策①②的修复已于 **2026-09-11 18:04** 落入源码（`yaml_kernel.py` / `parking_rule_map.yaml`），
  但模块目录 `C:\Users\damai\PyCharmMiscProject\testcase\园区基线\智慧停车\ParkingRuleTable`
  下 39 个 YAML 的 mtime **全为 09-11 17:13**，无任何产物晚于 18:00 ⇒ 本批跑的是**修复前的旧产物**。
* 逐字比对（部署目录 vs 按当前源码当场重生成）：

  | 项 | 数 |
  |:---|:---|
  | 相同 | 26 |
  | **不同** | **15** |
  | 仅部署目录有 | 2（`__pycache__\*.pyc`） |

* 15 个不同文件分三类，逐项对应 H19 的两条修复：

  | 文件 | 部署目录（09-11 17:13） | 当场重生成 |
  |:---|:---|:---|
  | `setup_data/setup_parking_rule_table.yaml` | `get_current_time(ydm)`×15、`get_offset_time(ydm,365)`×15、`PRE-004`×1、键 `preTempCarNumber`×1、`random_plates`×31 | 时间工厂表达式 **0**、`2029-01-01`×15、`2030-12-31`×15、`PRE-004`×13、`preTempCarNumber` **0**、`random_plates`×43 |
  | 13 个 `test_t2_01…t2_13/test_data.yaml` | 引用**共用**键 `preTempCarNumber` | 引用**各例自有**键 |
  | `test_ParkingRuleTable.py` | 119 行差异**全部**是输出路径前缀（`./testcase/园区基线/…` vs `./../AppData/Local/Temp/…`），**语义零变化** | — |

* 映射到失败：
  * **H19 根因 A 未生效** → 13 个表2 用例共用一块临停车牌 → 在场/账单状态互相污染，
    `mock/bill/unpaid`（**账单-未缴费查询**）塌成 `fee:0` → **9 例** `fee` 断言失败
    （表2-04/05/06/07/09/10/11/12/13）。
  * **H19 根因 B1 未生效** → 有效期仍为 `${get_current_time(ydm)}`→`${get_offset_time(ydm,365)}`
    = 2026-09-14→2027-09-14，**不覆盖** `time_format.base_date=2029-12-31` → 记录被 mock 判为过期
    → **15 处**（包月车族 / 白名单 `multi`）。
* 结论：**内核逻辑无需改动**，重跑导出即可。此前 4 个守卫测试（`TestRecordValidity`、
  `TestTempCarPlateIsolation` 等）都在 `artifacts` fixture 里**重生成到 tmp**，比对的是源码自己跟自己、
  **不看部署目录** ⇒ 产物与源码不一致时它们照样全绿，这是本次「改了内核忘重导出」未被发现的原因。

### 根因 B（新，**生成侧**）：同槽位车牌被覆盖 —— 仅表1-23（`yaml_kernel.py::_car_add_step`）

* `body = copy.deepcopy(spec["add"]["body"])` 中的 `carNumber: "${random_plates(1)}"` 是**每条记录各抽一次**；
  而提取键 `k_car_number(case, car)` 只按「用例 + 槽位」命名 ⇒ 同一槽位出现两次时，**第二次覆盖第一次的键**。
* 命中面：仅 `vehicle_recipes.混合_主场白名单_子场包月`（表1-23）。该 recipe 两条记录同取 `car: car_a`：

  ```
  '表1-23: 新建白名单'   carNumber=${random_plates(1)}   input_extract={'preCarT1_23ANumber': …}
  '表1-23: 新建包月车'   carNumber=${random_plates(1)}   input_extract={'preCarT1_23ANumber': …}   ← 覆盖
  ```

* 本批实测两牌：**湘BHFL9H**（白名单 @ PS1M）、**云UH73VG**（包月 @ PS1S）；
  键最终 = 云UH73VG ⇒ 该用例**全场次**都用云UH73VG ⇒ 主车场白名单记录查不到 ⇒
  `carTypeQuery`（**车牌类型查询**）在主车场取不到 `parkingType=4`。
* 语义确认（**双向印证，非拍定**）：① 数据层 recipe 声明两记录同槽位；② 表1-23 的用例 YAML 全程
  只用一个车牌键跑完 入主→类型查→入子→出子→出主 ⇒ **同一块牌**（主场白名单、子场包月）。
  故应改**内核**，而非数据层。
* **与 H19 根因 A 的关系**：H19 定案「每条临停用例各自生成独立车牌」只修了 `_temp_car_step`；
  `_car_add_step` 的**同一故障形态**（同槽位各抽一次牌、共用一个键）当时未一起识别 ⇒ 本次补修。

### 不属我方（用户已确认）

* `mock/bill/unpaid`（**账单-未缴费查询**）本轮实际返值异常 —— 用户确认属**接口问题，已修复**，不计入归因。

### 不确定类（**未动**，原则③）

| 项 | 现象 |
|:---|:---|
| `carWhiteOtherIsCasual` 列→字段语义 | 实测疑似**反向**：表1-08（=是）两车**都拒**、表1-09（=否）**都放行**；承接 H19 根因 B2 |
| 表1-11 其他车场白名单 | 记录在 PO、进场在 P4 → 拒 |
| 表1-24 特殊车牌 | 放行成立但 `fee`=20 vs 判定表期望 0；且 add 体写法自标「未单独验证（H10）」 |
| 表2-06/07/08/09 `paid` 口径 | 候选口径：`paid = (是否提前缴费=是) or (动作（是否缴费）=是)` |

### 本轮新增文件（repo）

* `changelog/2026-09-14_parking_redeploy_and_mixed_plate_reuse.md` —— 修复方案 + §8 执行结果
* `tests/tools/_redeploy_parking_rule_table.py` —— 「导出 + 部署 + 自检」一条命令（aux，不入系统运行）。
  背景：根因 A 是**流程疏漏**（改了内核忘重导出），本脚本把它收成唯一路径，部署后当场比对
  部署目录 vs 按当前源码重生成，`--check` 只比对不写。

### 本轮改动（repo）

* `rule_tables/yaml_kernel.py` —— `_car_setup_steps` + `_car_add_step`：同槽位车牌复用（根因 B）。
  改名前后实测：`input_extract` 键 85 个、重复 0（修复前部署侧 73 个键、`preCarT1_23ANumber` 重复 2）。
* `tests/test_rule_table_yaml.py` §9 —— 4 个测试。反向验证（退回修复前实现）**4/4 全红**，非空转。

### 本轮部署（2026-09-14，PyCharmMiscProject）

* 重导出并部署 `智慧停车/ParkingRuleTable/`（39 YAML + `test_ParkingRuleTable.py`）、
  `test_plan_parking_rule_table.xlsx`（独立文件名，未碰既有 `test_plan.xlsx`）、
  `translation_cache.json`（合并写）。
* 部署后自检：与按当前源码重生成**逐文件一致**（相同 41 / 不同 0）。
* 单测：`test_rule_table_{yaml,export,rows}.py` → **96 passed**；全仓 → 26 failed **均与本轮无关**
  （502 服务未起 + 更早改动的过期预期，三个失败文件均不 import `rule_tables`）。

## H21. H20 修复后重跑（2026-09-14 11:08）：22 红，三处修复全部生效（task#33）

**日志**：`C:\Users\damai\PyCharmMiscProject\log\test.20260914110842.log`（+ `.log.1`，11:08–11:10）

### 总账

| 项 | H20 前（09:29） | H21 后（11:08） |
|:---|:---|:---|
| 断言失败 | 59 | **22** |
| 有失败用例 | 27 | **13** |
| setup（`PRE-*`） | — | **26 步 0 失败** |

### 三处修复的生效确认（逐例对账）

| 修复 | 对账 |
|:---|:---|
| H19 根因 A（临停车牌逐例隔离） | 表2-02…表2-13 的 `fee` 断言**全部通过**（此前 9 例塌成 `fee:0`） |
| H19 根因 B1（有效期覆盖基准日） | 表1-12…表1-18（包月车族）**全绿**；表1-19 从 **6 红 → 1 红**（入主 + 类型查 car_a/car_b 全通过） |
| H20 根因 B（同槽位车牌复用） | **表1-23 全绿**（9 步 0 失败，此前主车场白名单查不到、`parkingType` 取不到 4） |

### 余下 22 处失败，按组归因（**均为 H20 已登记的「不确定类」，无一新增生成侧问题**）

| 组 | 用例（处数） | 现象 | 归属 |
|:---|:---|:---|:---|
| **A 一人多车 / 子车语义** | 表1-08(6)、表1-09(1)、表1-10(1)、表1-19(1)、表1-20(2)、表1-21(1) = **12 处** | P3（`carWhiteOtherIsCasual=true`）下**两车全拒**；P1 下**子车该拒却放行**；子车类型查取到**主车类型**（表1-09 期望 2 得 4；表1-20 期望 2 得 1） | 承接 H19 根因 B2（**接口侧**）；`carWhiteOtherIsCasual` 列→字段语义待核对 |
| **A′ 副车牌出场 400（新暴露）** | 表1-19、表1-20 各 1 处 | 同一用例内：主车牌 `mock/access/exit`（**出场模拟**）→ 200 `openGate:true`；副车牌 → **400 `请求失败`**。两次请求形状**逐字相同**，仅车牌不同 | **接口侧**（见下「新暴露项」） |
| **B 其他车场白名单（P4）** | 表1-11(3)、表1-22(2) = **5 处** | 记录写在 PO、进场在 P4 → `openGate=false`、`parkingType=null` | 承接 H19 根因 B2（**接口侧**） |
| **C 特殊车牌不计费** | 表1-24(1) = **1 处** | `mock/bill/unpaid` 返 `fee:20.00`，判定表期望 0（「按临停计费=否」） | 承接 H19 根因 B2（**接口侧**） |
| **D 提前缴费 `paid` 口径** | 表2-06/07/08/09 各 1 = **4 处** | `mock/bill/latest`（**账单-最新缴费状态查询**）返 `paid:true` + `payableAmount:0.0`，断言期望 `False` | **口径待定**（见下「口径疑点」） |

### 新暴露项：副车牌 `mock/access/exit` 400（**非本次引入**）

* 判定依据：① 主/副车牌两次出场请求形状**逐字相同**（仅 `carNumber` 不同），主车牌 200、副车牌 400；
  ② H20 的改动经反向验证，**唯一**行为差异是同槽位重复出现时（仅表1-23），表1-19/1-20 的子车槽位不命中该分支；
  ③ 上一轮表1-19/1-20 因有效期过期**入主即被拒**，从未走到出场步骤 ⇒ 该 400 此前**被掩盖**，非新引入。
* 倾向：mock 侧「在场记录按主车牌索引、出场按副车牌查找」的不对称 ⇒ **接口侧待确认**。

### 口径疑点：`paid` 在提前缴费用例上系统性相反（**4/4**）

* 4 个 `*_prepay` 用例（表2-06/07/08/09）**全部**在 `paid` 上失败，而非提前缴费用例（表2-10…2-13）全部通过。
* mock 的回答**自洽**：`paid:true` + `payableAmount:0.0` + `paidAmount:0`（已无应缴）。
* 两种可能：① 判定表「是否缴费」列的口径与 `bill/latest.paid` 语义不同（我方断言口径错）；
  ② mock 的 `paid` 未纳入「提前缴费」动作（接口侧）。
  ⇒ 需用户确认判定表该列口径后再定，**未动**。

> **2026-09-15 已结**：用户判定为**可能①，我方断言口径错**。见 **H22**。

### 未做

* 未改任何断言、映射、口径（原则③：以上 22 处全部登记待决策）
* 未碰 git

---

## H22. 用户两条口径落地（2026-09-15）：`paid` 口径修正 + 一人多车先后时刻（task#34）

### 输入

用户 2026-09-15 两条口径（原话）：

1. 一人多车：「**应该有时间先后关系吧？车场配置是否临停准入或白名单多车准入？
   包月一人多车，后入场标识为临停；车场不支持白名单多车准入，后入场车辆同包月车一人多车逻辑处理**」
2. 提前缴费：「提前缴费是否均产生费用？未产生费用应失败，且返回提示」
   → 随后修正我方理解：「**你这个提前缴费断言不对，提前缴费只要有费用产生是必成功的，
   后续你实际支付多少都成功，这只是一个动作**」

### 结论 1（`paid` 口径）：**我方断言错，4/4** —— 用户裁决为上面 H21 的「可能 ①」

* `yaml_kernel.py::_paid_step` 取期望值用的是判定表 **动作列（是否缴费）** 的原文
  （`bool(col["value_map"].get(raw))`）。而表2-06/07/08/09 这四行的两列**打架**：

  | 用例 | 条件（是否提前缴费） | 动作（是否缴费） | 我们断的 | 11:08 实测 |
  |:---|:---|:---|:---|:---|
  | 表2-06 | **是** | 否 | `paid: false` | `paid: true` |
  | 表2-07 | **是** | 否 | `paid: false` | `paid: true` |
  | 表2-08 | **是** | 否 | `paid: false` | `paid: true` |
  | 表2-09 | **是** | 否 | `paid: false` | `paid: true` |

  四例「提前缴费模拟」（`mock/pay/simulate`）实测**全部 `{"success":true}`**；
  `payableAmount:0.0` 是券抵扣后的余额，不影响「已缴」这一事实。
  按用户口径「提前缴费只是一个动作，只要有费用产生必成功，后续实付多少都成功」
  ⇒ 缴了费 `paid` 必为 `true`。

* **修法（同源，非新造规则）**：`_pay_step` 早就按「条件列优先」选出有效缴费动作，
  `_paid_step` 却另判一次而分叉。现抽出 `_pay_action_key(case)` 作为「缴不缴费」的
  **唯一判据**，缴费步骤与缴费状态断言都从它取。
* 效果：表2-06/07/08/09 → `paid: true`；表2-02…05 / 2-10…13 不变（本已 `true`）；
  表2-01（无缴费动作）不变 `false`。

### 结论 2（一人多车先后）：**判定表写了顺序，是我们生成时丢的**

> **2026-09-15 已改写法**：本节下面的 `(token, car)` 键 + `_time_of` 退回 + 偏移写法
> **已全部删除**，改为**逐例字面量、按 c9 动作顺序位置对应**。见 **H23**。
> 本节的结论（顺序是判定表明写的、是我们丢的、车场开关映射是有的）**仍然成立**。

* 判定表 c9「条件（出入场动作）」**写死了动作顺序**：
  表1-08/09/19/20 = `A入主-B入主-A出主-B出主`；表1-10/21 = `A入主-B入主`。
* 但 `_times_for` 的 key 只有 `token`，而 **A/B 两车共用同一个 token**（都是「入主」/「出主」）
  ⇒ 两车被赋成同一时刻；表1 又无 timeline，一律落基准时刻。
  09-14 那轮实测 6 例 A/B 全是 `2029-12-31 10:00:00` 同秒进、同秒出 ——
  **「后入场」在产物里不存在**。
* 车场开关**映射是有的、且逐字忠于 xlsx**（P1 临停关/一人多车关；P2 临停开；
  P3 一人多车=是），不是缺配置的问题。

### 变更清单

| # | 对象 | 动作 |
|:---|:---|:---|
| 1 | `rule_tables/yaml_kernel.py` | `_pay_action_key` 新增；`_pay_step` 改用之；`_paid_step` 期望值改同源（签名去掉未用的 `col`/`raw`） |
| 2 | `rule_tables/yaml_kernel.py` | `_times_for` 支持 `(token, car)` 键；新增 `_time_of`；`build_case` 取时刻改走 `_time_of`；`_parking_type_call` 的 `startTime` 改取**该车自己**的入场时刻；`_coupon_steps`/`_fee_steps` 传车槽位；新增 `_primary_car_slot` |
| 3 | `rule_tables/parking_rule_map.yaml` | 6 个一人多车用例各写**显式 timeline**（带 `car`）；`是否缴费` 列的 `value_map` 注明不再是期望值来源 |
| 4 | `tests/test_rule_table_export.py` | `TestOnePersonMultiCarOrder` 新增 3 项；`TestFeeQueryAndPayActions` 的 paid 锁定测试改口径 + 新增全表2 不变式 1 项 |
| 5 | 部署产物 | 经 `tests/tools/_redeploy_parking_rule_table.py` 重导出 |
| 6 | 文档 | 本节 + `changelog/2026-09-15_paid_semantics_and_multi_car_order.md` |

### 测试设计与反向验证（原则④：绿灯不算数）

反向验证：把两处改动**各自换回改动前的实现**，确认新测试会红（一次性脚本，不入库）：

| 场景 | 换回的旧实现 | 结果 |
|:---|:---|:---|
| A | `_times_for` 只按 token 取时刻 | `test_b_enters_after_a`、`test_exit_order_matches_the_table` **红**（`表1-08': 'B 必须后入场（同秒 = 顺序丢了）'`），其余不红 |
| B | `_paid_step` 照动作列原文 | `test_paid_follows_the_payment_action_not_the_column`、`test_paid_agrees_with_the_pay_step_for_every_case` **红**（`表2-06': '有缴费动作却断 paid=false'`），其余不红 |
| C | `_parking_type_call` 的 `startTime` 写死基准时刻 | `test_type_query_uses_that_car_own_entry_time` **红**，其余不红 |

**如实登记一处局限**：`test_type_query_uses_that_car_own_entry_time` 在场景 A 下**不会红** ——
它断的是「查询时刻 == 自身入场时刻」，而两车同秒时**两侧一起塌**，等式仍成立。
它守的是场景 C 那个 Bug（`startTime` 写死），顺序塌陷由另两项守。

### 部署结果

```
相同 41 | 不同 0 | 仅部署侧有 0 | 仅重生成侧有 0
```

重导出前 `--check` 的差异面**恰好 10 个文件**，无连带：

* 6 个 `test_t1_08/09/10/19/20/21_*/test_data.yaml`（先后时刻 + 查询时刻）
* 4 个 `test_t2_06/07/08/09_*_prepay/test_data.yaml`（`paid` 口径）
* `setup_data/setup_parking_rule_table.yaml` **逐字未变**（由测试反向锁定）

仓内单测：`pytest tests/test_rule_table_export.py tests/test_rule_table_yaml.py tests/test_rule_table_rows.py -q`
→ **100 passed**（原 96 + 新增 4）。

### 登记待决（原则③：方案外发现，未顺手改）

| # | 项 | 说明 |
|:---|:---|:---|
| E1 | 表1-08/19 在 P3 **连主车都被拒**（`openGate:false`、`parkingType:null`） | 承接 H19-B2「车场开关语义未实现」；`carWhiteOtherIsCasual` 的列→字段映射当初是**穷举挑的、未验证**，其字面语义是「白名单**其他车场**按**临停**」，与列名「是否支持一人多车白名单=是 ⇒ 应支持」**反向**。09-11 单变量探针已证：同一 add 体、仅车场不同 → 白名单 `multi:true` 在 P3 入场 **false**、在 P1 **true**。**我们补了先后时刻也不保证这 4 例翻绿** |
| E2 | 4 个「不支持」例 mock 不按判定表走 | 表1-09 子车类型查得 `4`、表1-20 得 `1`（期望都是 `2` 临停）；表1-10/21 子车该拒却放行。同属 H19-B2 接口侧 |
| E3 | 副车牌 `mock/access/exit`（出场）返 400 | H21 已登记，**非本次引入** |
| E4 | 用户口径另一半「**未产生费用应失败，且返回提示**」**无用例覆盖** | 表2-01（不产生费用）根本没有缴费动作。故本次**不写无测试覆盖的死代码**，等有用例时与用例一起加 |
| E5 | `carInConfig` 字段全集里**没有**「一人多车」对应字段 | 见 E1；接口方需澄清该开关的正式字段 |

### 未做

* 未改 E1–E5 任何一项（登记待决策）
* 未跑接口实跑（用户自己执行）
* 未碰 git（无提交、无暂存）

## H23. 时刻写法改「逐例字面量、按位置对应」+ 补全 17 例零时长（2026-09-15，task#35）

> 承接 **H22**：H22 用「偏移 + `(token, car)` 键」把一人多车的先后补上了，但用户随即质询
> 写法本身 ——「时间方法已有偏差值设计，每条用例直接设置合理进出时间不行么？设置时间表的优势在哪里？」
> 并由此牵出**更大的一面**：`有些用例我看了下，怎么入场出场都在同一个时间？`

### 输入

* 用户 2026-09-15 决策：写法取 **乙（逐例字面量、按 c9 动作顺序位置对应）**；表1 无时间线的 **17 例「补」**
* 用户原话：「有些用例我看了下，怎么入场出场都在同一个时间？」
* 用户原话：「表2-01增加一个临停车入场出场动作」（判定表部分**由用户改**，见下）

### 结论 1（写法）：位置即归属，`(token, car)` 键整个删掉

判定表 c9「条件（出入场动作）」本身就是**有序**的动作序列
（表1-08/09/19/20 = `A入主-B入主-A出主-B出主`），故时刻**按同一顺序排列、位置即归属**，
不需要任何键。H22 引入的 `(token, car)` 键与 `_time_of` 的退回逻辑**全部删除**：

| 项 | H22（已废） | H23（现行） |
|:---|:---|:---|
| 时间线形态 | `[{token, car, offset_h}]` | `['2029-12-31 10:00:00', …]` |
| 取时刻 | `_time_of(times, token, car)` 按键查 | `times[i]` 按动作下标直取 |
| 无时间线 | 兜底：所有动作落基准时刻 | **报错**（有动作就必须有时间线） |
| 个数不符 | 不检查 | **报错**（不补齐、不截断） |
| 日期 | 由 offset 推出，天然跟随 `base_date` | 字面量，故**强制**日期 == `base_date`（H19-B1） |

### 结论 2（17 例零时长）：兜底删除后才暴露的那一层

17 例（表1-01…1-07、1-11…1-18、1-22、1-23）**入场 == 出场**、停车时长 0 ——
根因就是上表最后那行「无时间线兜底」。已逐例补齐真实在场时长，
复用表2 已实证的两条时长剖面（主场 4h / 主 2h+子 3h），**不新造数值**：

| 动作形态 | 用例 | 时刻 |
|:---|:---|:---|
| 入主→出主 | 1-01 / 11 / 12 / 22 | 10:00, 14:00 |
| 入子→出子 | 1-02 / 13 | 10:00, 14:00 |
| 入主→入子→出子→出主 | 1-03 / 05 / 14 / 16 / 23 | 10:00, 12:00, 15:00, 15:00 |
| 入主→**入子(被拒)**→出主 | 1-04 / 15 | 10:00, 12:00, 14:00 |
| 入主→**变更临停**→出主 | 1-06 / 07 / 17 / 18 | 10:00, 12:00, 14:00 |

后两行的 `12:00` 是「中途」：入子被拒的**尝试**时刻 / 变更临停的时刻。
`变更临停` 不产生请求时刻，但仍占时间线一个位置（逐位对齐 c9 的动作序列）。

### 表2-01：判定表 F34 原文是 `\`（不适用）—— 已由**用户**补齐 `入主-出主`

* 事实：表2-01 的 F34「条件（出入场动作）」原文写 `\`，`parse_actions` 按 `not_applicable`
  解析成**空动作** ⇒ 该例产物里只有两个 `bill/latest` 查询步、**没有任何入场/出场**。
  而它断的正是 `payableAmount: 0` —— 查一个**从未进过场**的牌。
  （车牌 `preCarT2_01ANumber` 已在 setup 的 `PRE-004: 生成临停车牌（表2-01）` 里造好，
  P2 的进出设备键 `preP2InDeviceCode`/`preP2OutDeviceCode` 也齐备 —— **只差这一格**。）
* 决策与执行：用户把 F34 改为 `入主-出主`（源表 mtime **2026-09-15 15:25:38**、
  sha `0c2d46f9`，改前 `658e17dd`）；**生成侧不新造判定表没写的东西**。
* 落地：map 里那一行时间线**由注释启用为真条目**
  （`表2-01: ['2029-12-31 10:00:00', '2029-12-31 14:00:00']`，4h，同 表2-05）。
  此前只以注释记着、**不预置** —— 先加时间线再补动作会因「个数不等」报错（有意）。
* 反向验证（两道都拦得住）：① 源表换回仓内 09-03 旧快照（F34 仍是 `\`）而时间线已在
  → 内核**直接拒生成**：`timeline 有 2 个时刻、动作有 0 个 → ValueError`；
  ② 再摘掉时间线（= 改动前真实状态）→ **翻正后的锁红**（`mock/access/` 不在产物里）。
* 产物（已部署）：`mock/access/enter`（入场）@10:00 / `preP2InDeviceCode`
  → `mock/access/exit`（出场）@14:00 / `preP2OutDeviceCode`
  → `mock/bill/latest`（获取车牌最新账单）断 `$.paid: false` → 同接口断 `$.payableAmount: 0`。
  两条账单断言**落在出场之后**（位置才对）。进出场两步仍只断 `status_code: 200`（见 E6）。

### 变更清单

| # | 对象 | 动作 |
|:---|:---|:---|
| 1 | `rule_tables/yaml_kernel.py` | `_times_for` 改返回**位置对应的字面量列表**；删除 `_time_of` 与全部兜底；`build_case` 改 `enumerate` 取 `now`；`_coupon_steps`/`_fee_steps` 去掉 `exit_token`/`times` 参数改收 `now`；`_parking_type_call` 改收 `entry_time` |
| 2 | `rule_tables/parking_rule_map.yaml` | timeline 全部改字面量（15 条换算 + 17 条新增 + 表2-01 注释待启）；块头注释重写 |
| 3 | `rule_tables/registry.py` | `get_timeline()` docstring 改「位置对应」 |
| 4 | `tests/test_rule_table_export.py` | 新增 `TestActionTimes`（3 项）；`test_timelines_cover_all_eight` 改位置形态；`TestOnePersonMultiCarOrder` docstring 去 `car` 键说法 |
| 5 | 部署产物 | `tests/tools/_redeploy_parking_rule_table.py` 重导出（**两次**：写法改动一次、F34 补齐后又一次） |
| 6 | 文档 | 本节 + `changelog/2026-09-15_action_times_literals_and_17_cases.md`；H22 加前向指针 |
| 7 | `rule_tables/parking_rule_map.yaml` | 表2-01 时间线**由注释启用为真条目**（F34 已补，4h 同 表2-05） |
| 8 | `tests/test_rule_table_yaml.py` | 表2-01 那条**反向锁翻正**：原 `assert "mock/access/" not in text` 依据的正是缺陷本身，改为「必须有进出场 + 账单断言落在出场之后」 |
| 9 | `tests/test_rule_table_export.py` | 三处锁死计数随之上调：有动作用例 36→**37**、同槽位进出对 38→**39**、进出场步 100→**102** |

### 测试设计与反向验证（原则④：绿灯不算数）

| 场景 | 换回的旧行为 | 结果 |
|:---|:---|:---|
| A | 无时间线落基准时刻 + 只按 token 做键 | `test_b_enters_after_a` **红**（`表1-08': 'B 必须后入场（同秒 = 顺序丢了）'`） |
| B | 17 例未补时刻（全部落基准时刻，时长 0） | `test_every_car_exits_after_it_enters` **红**（`表1-01': 'car_a', '出场不晚于入场（停车时长 0）'`） |
| C | `build_case` 把进出场时刻**另算一份**（写死基准） | `test_every_entry_step_carries_its_own_time` **红** |
| D | 给**无动作**的表2-01 登记时间线（死数据） | `test_timeline_is_positional_and_matches_action_count` **红**；且内核直接 `ValueError` 拒生成 |
| E | 表2-01 回到「F34 未补」态（旧快照 0 动作 + 摘掉时间线 = 改动前真实状态） | 翻正后的锁 `test_temp_car_case_has_fee_assertion` **红**（`mock/access/` 不在产物里） |

**如实登记各测试的守备边界**：场景 A 下三个新测试**都不红** ——
结构测试读的是 map 而非内核输出（守数据层），另两项与 `_times_for` 自比（守「两处真相分叉」）。
三类 Bug 各由一项守；无一冗余，也无一项能独当全部。

### 部署结果

**第一次（写法改动）**

* 重导出前 `--check`：**差异面恰好 17 个文件**（= 新增时刻的 17 例）
  ⇒ 反向证明「偏移 → 字面量」换算是**等价**的（其余 24 个逐字未变，含 `setup_data` 与一人多车 6 例）
* 三件套：**103 passed**（原 100 + 新增 3）
* 重导出 + 自检：**相同 41 | 不同 0 | 仅部署侧有 0 | 仅重生成侧有 0**

**第二次（用户补齐 F34 后）**

* 三件套复跑：**103 passed**（三处锁死计数 36/38/100 → 37/39/102 后仍全绿）
* `--check`：差异面**恰好 1 个文件** —— `test_t2_01_free_park_no_payment/test_data.yaml`；
  `test_ParkingRuleTable.py` **逐字未变**（它只驱动目录、不含步骤内容）
* 重导出 + 自检：**相同 41 | 不同 0 | 仅部署侧有 0 | 仅重生成侧有 0**
* 两次的差异面 ↔ 部署后自检 41/0 两侧口径一致，无「改了没导出」

### 第三次实跑对比（用户执行）：断言失败 40 → 62 的归因

上次 `test.20260915140318.log` → 本次 `test.20260915153155.log`（**只读日志，未调接口**）：

| 项 | 上次 14:03 | 本次 15:31 |
|:---|:---|:---|
| 失败断言日志行 | **40** | **62** |
| 有用例失败 | 9 | 20 |

新增 22 行 = **11 例 × 2**（同一断言在 `assertions.py` 与 `apiutil.py` 各记一行），
11 例逐条都是 `$.openGate` 期望 `True` 实际 `False`，**全部落在出场步**：
表1-01 / 1-03 / 1-04 / 1-05 / 1-06 / 1-07 / 1-16 / 1-17 / 1-18 / 1-23 / 表2-01
= **17 例补时刻里出场的 10 例** + **表2-01**（本轮第一次有进出场步）。
另 7 例改了时刻仍放行；其余 9 例计数**持平**。

同一用例出场步对照：表1-01（白名单 P1）出 10:00 ✓ → 出 14:00 **✗**；表1-04 同形；
表1-03 / 1-05 出主 10:00 ✓ → 15:00 **✗**（出子仍 ✓）；表1-16（包月 PS2M）出主 ✓ → **✗**。

**不是「到 14:00 就被拦」**：同为 14:00 出场而放行的反例有 表1-12（包月 P1）、表1-02（白名单 S1）、
表1-14（包月 PS1M 出主 15:00）、表2-02…2-09（临停 P6）、表1-24。

实测事实（同一轮内成立）：① **子场出场一律放行**（1-02 / 1-03 / 1-05 出子 / 1-13 / 1-14 / 1-16 出子 / 2-10…2-13）；
② **P2 本轮五例四拦**（1-06 / 1-09 / 1-17 / 2-01 拦，仅 1-20 放行），而 1-09 上一轮就是 4h 出场、**也被拦**
（当时车牌完全不同，且两轮 37/37 例车牌全部重新随机，仅 表1-24 固定牌）⇒ 与车牌无关、**上一轮已存在**；
③ 分界在**车辆记录**：1-17 与 1-20 同车场 P2、同设备 `DEV-P2-2_nsj8j4`、同时刻 14:00，一个 ✗ 一个 ✓，
记录差别只有 `mainCount: 1` vs `mainCount: 2 + subCarNumbers`。

**未确定（不编）**：mock 的出场放行规则本身 —— `data/app.db` 里 mock 接口说明只有一行、无拒行规则，
被拒返回只有 `{"openGate":false}`、无原因码。最贴合的假说是「按应付金额是否结清」（子场免费 / 月卡减免），
但**解释不了** 表1-16 与 表1-17 —— 按假说这两例本应放行。证据不足，只登记。

**定性结论**：上一轮那 17 例的绿灯是**「进出场同一时刻（零时长）」人为场景下的绿灯**；
给上真实时长后出场才第一次被真正检验，故这 11 例是**本轮新暴露的既有问题**，不是本轮新引入的缺陷。
**本轮未因此改任何时刻、任何断言。**

### 登记待决（原则③：方案外发现，未顺手改）

| # | 项 | 说明 |
|:---|:---|:---|
| E6 | 表2-01 的入场/出场步**只断 `status_code: 200`，不断放行/拒行** | 表2 表头**没有**「动作（允许入场-/出场-主场）」列（表1 才有），故无期望值来源；按「不新造判定表没写的东西」不加 `openGate` 断言。F34 补齐后这两步已落地，仍只断 200 —— 若需也验放行，要判定表补列 |
| E7 | 仓内 `uploads/product/停车类型和费用计算-规则表.xlsx` 是 **2026-09-03 的旧快照** | registry 实际读的是 `D:\0-导出文件\` 那份；**D: 那份 F34 已由用户 09-15 补齐为 `入主-出主`，仓内这份仍是 `\`**（反向验证场景 E 正是拿它当「未补」态用）。该副本是否还有用途、要不要同步 —— 登记不动 |
| **E8** | 本仓也有一个 `data_factory` 包（`methods.yaml`/`registry.py`，**无 `plate_generator.py`**），与**框架**的 `data_factory`（`C:\Users\damai\PyCharmMiscProject\data_factory`，有 `plate_generator.py`）**同名** | 从仓库根跑 pytest 时仓库根在 `sys.path` 前 → 框架 `conftest.py:9` 的 `from data_factory.plate_generator import PlateGenerator` 命中**仓库那个包** → `ModuleNotFoundError`。全量里 `tests/test_framework_extract_by_filter.py::TestConftestSnapshot` 2 项因此**红**（单跑该文件却 2 passed —— 因为 `apiutil` fixture 先把 MISC 插到了 0 位，之后各测试模块按 pytest prepend 模式又把仓库根插回 0 位）。与 `rule_tables/` 无关，**不是本轮引入**；仓库的 `data_factory` 是生产代码在用（`infrastructure/llm/client.py`、`prompts/response_model.py`），**不能改名**。可选方向：`apiutil` fixture 的 `if str(MISC) not in sys.path` 守卫改为无条件 `insert(0, MISC)`（最小、只动 1 个测试文件的辅助函数）。**用户 2026-09-15 决定：先不改** |
| E9 | 全量里这 2 项**上一轮为何是 skip**，我没查出来 | 计数算术能定死它们上一轮是 skipped（总数 1061 → 1064，正好 +3 = 新增 3 项，无增删用例），但该模块除 `MISC.exists()` 这个已成立的守卫外**没有 skip 分支**。存疑登记，不编 |
| **E10** | **主场出场在 4~5h 后被拦** —— 第三轮实跑 11 例新增 `$.openGate` 失败的主体 | 见上「第三次实跑对比」。三条实测事实清楚，但 **mock 放行规则本身没查到**，且有 2 例反证；**本轮未改时刻、未改断言**。下一步三选一：① 先查 mock 侧规则（我拿不到 mock 源码/库）；② 调判定表侧的车辆记录或动作；③ 暂不管，等 mock 侧确认 |
| E1–E5 | 承接 H22，未动 | 见上 |

### 未做

* 未改判定表（**用户自己改 F34**，我只改生成侧）；用户改之前**未预置**表2-01 的时间线
* 未跑接口实跑（用户自己执行）
* **未因第三轮实跑的 11 例出场被拦改动任何时刻、任何断言** —— 只做归因与登记（E10）
* 未碰 git（无提交、无暂存）
* 未改 E8（用户 09-15 决定先不改 `data_factory` 同名遮蔽）；未改仓内那份 09-03 旧快照 xlsx

---

## H24. 前置按「全局 / 每组 / 每例」拆成 48 个 fixture（2026-09-15，Allure 展示粒度）

> 承接 H23 的第三次实跑。**与用例内容无关**：本轮只动**前置的组织方式与展示粒度**，
> 未改任何时刻、断言、动作、用例内容，判定表也未动。
> 前因：用户看 Allure 报告时问「所有的前置都放在一块了，不能一些测试用例，共有的前置，
> 展示时只展示自己的前置么？」→ 探针实测确认**没有 CLI 开关**（`allure_pytest/listener.py`
> 的 fixture 容器 children = `item._request.fixturenames` 传递闭包 = 所有引用它的用例），
> 故「展示粒度 = 引用粒度 = 执行粒度」，只能真的拆 fixture。用户拍板「按2改吧」，
> 并随后确认开关设计「Sheet1 按这个来」（`fixture等级 = 逐前置` + Sheet2 新增「依赖前置」列）。

### 结论

* 拆法、依赖图、代价、测试设计、留档路径：见 **`changelog/2026-09-15_per_precondition_fixtures.md`**（本文不重复）
* 块账：全局 5 + 每组 56 + 每例 61 = **122**（与拆前逐块相同，122 块哈希两两不同）
  → 48 个 fixture（1 + 11 + 36），落成 48 个 `setup_data/setup_parking_rule_table__<编号>.yaml`

### 开发中查出的真缺陷（本轮已修）

| 项 | 说明 |
|:---|:---|
| PO 组的 fixture 一度没生成 | `_per_pre_fixtures` 原先只按 **Sheet1 用到的编号**收集，而 **PO 组没有任何用例直接引用**（只有 P4 的「依赖前置」指向它）→ P4 的 fixture 参数指向不存在的名字，**跑到引用 P4 组的用例时 pytest 报 fixture not found（整例 ERROR）**；实测 `--collect-only` 收得下、`setup` 时才炸，受影响的是表1-11 / 表1-22。已改为先做**依赖闭包**再生成；是靠「fixture 数 == 48」这条锁发现数出来是 47 |

### 测试设计与反向验证（原则④：绿灯不算数）

| # | 锁 | 结果 |
|:--|:--|:--|
| ① | 块划分：三级并集逐块 == `build_setup`；硬锁 5/56/61、11 组/36 例；无重复块 | ✅ |
| ② | 旧顺序：建场块顺序 == 实体序（PO 在 P4 前）、设备块全在建场块之后、组内「建场+回读 → 出入口设备」 | ✅ |
| ③ | 组内自足：组前置只出现本组 `pre<EID>*` 键、无清场/收费规则/车辆记录；例前置只出现本用例自己的键 | ✅ |
| ④ | 依赖：Sheet2「依赖前置」⇔ fixture 参数；P4→PO、S1→P1 在列；全局是唯一无依赖的根；每条方法挂 全局+本组+本例；teardown 只在根上（全文件 1 处） | ✅ |
| ⑤ | 命名耦合：`py_export._pre_id_slug` ≡ `yaml_kernel.pre_id_slug`、等级字面量 ≡ `FIXTURE_LEVEL_PER_PRE`、class_slug ≡ `SLUG`；文件主干 == fixture 名 | ✅ |
| ⑤′ | **反向锁（共用代码）**：`tests/test_py_export_fixture.py::TestLegacyModeUnchanged` 固化「非逐前置模式产物形状不变」；另拿 `园区基线/智慧停车/test_plan.xlsx` 重新生成 SmartParking 的 `.py`，与改动前 golden **逐字相同**（67 class / 80 用例 / 67 fixture / 47 处类级 usefixtures / 无显示名助手） | ✅ |
| — | 全套受影响测试：`test_rule_table_yaml` + `test_rule_table_export` + `test_rule_table_rows` + `test_rule_table_fixture_split`（新增 25 项）+ `test_py_export_fixture` + `test_run_results` = **154 passed** | ✅ |
| ⑥ | **端到端（用户执行）**：拆前一轮基线 → 拆后一轮，逐用例比对请求序列与通过集合 | ⏳ 待用户跑 |

### 部署结果（2026-09-15，PyCharmMiscProject）

* `_redeploy_parking_rule_table.py`（不带 `--check`）：37 用例、`teardown 已注释=True`
* `--check`（**这次才是有效自检**）：**相同 88 | 不同 0 | 仅部署侧有 1 | 仅重生成侧有 0**
  —— 唯一那 1 个是拆分前的旧 `setup_data/setup_parking_rule_table.yaml` 残留（E12 ①）
* 部署目录 `setup_data/` = 48 个 `setup_parking_rule_table__<编号>.yaml` + `teardown_parking_rule_table.yaml`
  （+ 上面那个残留）；37 个用例目录的 `test_data.yaml` 与 `.py` 逐字一致

### 登记待决（原则③：方案外发现，未顺手改）

| # | 项 | 说明 |
|:---|:---|:---|
| **E11** | **`agent_components/run_results.py` 的「class → setup 文件」映射在逐前置模式下失效** | `_fixture_of_class`（`run_results.py:189`）只认**类级** `@pytest.mark.usefixtures("setup_…")` 装饰器，而逐前置模式把前置挂在**方法**上、类上没有 → `has_setup=False`、`class_setup_abs()` 返回 `None`（它退化去探测已不再生成的 `setup_data/setup_parking_rule_table.yaml`）。影响：① 写 setup sidecar 时**前置失败不再归到 setup 文件**；② 定向重跑不再带上前置。**前置本身仍由 fixture 正常执行**，只是「失败归因 / 重跑范围」失去粒度。两条路：改 `run_results` 收集**方法级** usefixtures（并与 fixture 依赖闭包对齐），或本轮不动。本轮**未改**，等用户决策 |
| **E12** | `tests/tools/_redeploy_parking_rule_table.py` 三处（本轮实测） | ① **不清残留**：拆分后旧的 `setup_data/setup_parking_rule_table.yaml` 仍在部署目录里 → `--check` 实测「相同 88 \| 不同 0 \| **仅部署侧有 1**」（非致命，该文件已无任何引用，可人工删）。② **部署后的自检恒真**：正常模式走 `compare(module_dir, out["feature_dir"])`，而 `feature_dir = dirname(module_dir)/ParkingRuleTable` **就是** `module_dir` 自己 → 「相同 89 \| 不同 0」证明不了任何事；有效的是 `--check`（临时目录重生成后比对）。③ dry-run 打印把文件数写死成「39 个 YAML」（现为 49 + 37）。④ `tests/tools/_probe_monthly_car_recognized.py:37` 这个 2026-09-11 的一次性探针仍指向已被替换的 `setup_data/setup_parking_rule_table.yaml`（现在会 FileNotFound；该探针所属那轮实验早已结案）。⑤ 本目录 `_dryrun/ParkingRuleTable/test_ParkingRuleTable.py`（2026-09-11 的 dry-run 产物）在**仓库根**跑 `pytest` 时会被收集 → `ModuleNotFoundError: No module named 'allure'`（本仓 venv 无 allure）→ **整个 collection 中断**。故全量要跑 `pytest tests/`（本轮实测：1044 passed / 28 failed，失败全在上面四类）。五处都不碰，登记 |
| E13 | `fixture等级` 这一列在两个产品里原本都是**死列** | SmartParking 的 `test_plan.xlsx` 每行本来就带 1–5 个前置编号（`PRE-xxx`），但没人读那列的等级值。本轮只让「逐前置」这一个取值有了行为，其余取值（class / danyuan / 空）走原分支 —— 故共用代码的反向锁是必须的（见 ⑤′） |

### 未做

* 未跑接口实跑（用户自己执行）；未碰 git（无提交、无暂存）
* 未改判定表、未改任何时刻/断言/动作/用例内容；`teardown_parking_rule_table.yaml` 未变
* 未改 `run_results.py`（E11）、未改 `_redeploy_parking_rule_table.py`（E12）—— 均等用户决策
* 未碰 E1–E10（E8 按用户 09-15 决定先不改）
