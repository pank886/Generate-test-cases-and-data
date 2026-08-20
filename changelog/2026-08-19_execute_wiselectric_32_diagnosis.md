# 智慧用电_32/33 生成用例执行 + 失败根因诊断（5 层）

> 日期：2026-08-19
> 状态：**执行管道 ✅ 已通；URL 前缀 ✅ 已修(_32)；payConfig ✅ 已修(_32)；其余缺陷按用户决策「先记录，后续处理」**
> 背景：执行 Phase C 刚生成的测试套件 `testcase/园区基线/智慧用电_32`，报告落 `logs/`，`tests/` 建专用 pytest 执行器；失败逐层定位并修复到可跑通、可出报告的层面。
> 复测：**智慧用电_33**（2026-08-20 同法处理前缀+payConfig 后执行）复现 §1 层 3/4，并新发现**固定假主数据碰撞**（§1 层 5）——已按"能合并则合并"并入 §1/§3/§7，不再单列章节。

## 0. 交付物（原任务：执行用例 + 报告落 logs + tests 建执行器）

| 交付 | 位置 |
|------|------|
| pytest 执行器 | `tests/execution/test_run_generated_case.py`（+ `__init__.py`） |
| 执行方法 | `run_generated_case()`：在框架根复刻 run.py 的 pytest 参数（`-c pytest.ini -v -s --alluredir=./report/temp <path>`），子进程执行，捕获控制台输出 + 解析 junit.xml → 写报告到 `logs/test_report_<场景>_<时间戳>.log` |
| 默认跳过 | `RUN_GENERATED_CASE=1` 环境变量门控，不开则整体 skip，不拖慢仓库回归套件 |
| 执行方式 | **不修改框架**（run.py/pytest.ini/common/base 一律不动） |
| 三次执行报告 | `logs/test_report_智慧用电_32_*.log` ×3（143811=405 层 / 145922=payConfig 层 / 154014=retCode 断言层） |
| _33 执行报告 | `logs/test_report_智慧用电_33_20260820_144659.log`（24 收集 / 0 执行，setup 全 ERROR，复现层 3/4 + 新层 5） |

## 1. 失败根因分层（已实测验证）

| 层 | 现象 | 根因 | 结论 |
|----|------|------|------|
| 1 | POST 裸路径 → **405 nginx** | DB `api_url` 与所有生成用例缺服务前缀 `park-energy-electric-web/`（72 条全裸，前缀记录=0）；框架 `base_url(config host)+url` 拼接打到 nginx 不路由路径 | **✅ 已修**：_32 全部 yaml 40 行 url 补前缀（23 文件），实测带前缀 401（业务响应）→ 路由正确 |
| 2 | POST 带前缀 → **400** `计费方案 P001 不存在` | 测试数据引用 dev 不存在的主数据 `payConfigCode: P001`；add 只校验 payConfig（实测换成真实码后其余假主数据全通过） | **✅ 已修**：setup/teardown 12 行 payConfig → 真实码 `zhyqP25529429`，add 实测 `retCode:1 success` |
| 3 | setup 第一个块 add **已成功** 却断言失败 | 生成层硬编码 `eq: {retCode: 0}` 为成功断言（`prompts/response_model.py:319,517` / `extraction_prompts.py:266,301,343`），但 dev 后端 **成功=retCode 1、业务失败=0** | ⏸ **用户决策：先记录，后续处理**（生成层根因，未改 prompt）；**_33 复测同现**：PRE-002/PRE-003 真实 add `retCode:1` 成功、HTTP 200，被 `eq retCode 0` 标记失败 |
| 4 | teardown 全是 add 块、delete 全部 400 | ① teardown 生成成 add 而非 delete（"清理"实为重新添加）② delete 接口按生成 body `{code}` 返回 400 `请求失败`（真实 delete 契约待确认） | ⏸ **先记录，后续处理**；**_33 复测同现**：teardown 4 块仍全为 `post /electricMeter/add` |
| 5 | setup 块 add → **400** `电表名称请勿重复` / `该设备...已存在` | **固定假主数据碰撞（_33 新发现）**：设备名字/编码写死（`测试电表A/B/分时电表`、`METER_TEST_00x`），dev DB 持久化 → ① 跨套件：与 _32 执行遗留同名（即使编码不同，后端按名称查重）② 套件内：`PRE-00x` 与其 `PRE-00x_isolated_TC-0xx` 复用同一电表 → 自碰撞 | ⏸ **先记录，后续处理**（生成层唯一化/执行前清理，并入 §3-7 / §7 优化方向） |

## 2. 现场真实上传值对比（用户提供，2026-08-19）

真实成功 payload + 返回 `{"retCode":1,"msg":"success"}`（再次确认 retCode 1=成功）。

| 项 | 结论 |
|----|------|
| required 口径 | DB required(22) == 真实非空(22)，**双向零差异** —— 上次必填修正被证实完全正确 |
| 字段集 | 真实有 DB 无：`initDetailList`；DB 有真实无：35 个（查询参数污染 `pageNum/pageSize/searchKey/startTime...` + 少量可选项 `sharp/peak/flat/valleyElectricity`、`breakerList`、`payConfigName`）；setup body 多 `yqAppCode`（真实在 header `yq-app-code: test`，不在 body） |
| 主数据值差异（13 项） | `meterDeviceManufacturerCode/Name`: MFR001/测试厂家 → **华立/华立**；`meterDeviceModelCode/Name`: MODEL001/DTZ-MODEL → **DDS28/DDS28**；`sceneCode/Name`: SCENE001/测试场景 → **zhyqE29999415/公司场所**；`accessType`: '1'→**'4'**；`useType`: '2'→**'1'**；`billingFactor`: 1→**'1'(string)**；`electricity`: '100.00'→'0'；`purpose`: 自动化测试数据→照明 |

> 值差异**不阻塞执行**（add 只校验 payConfig），但语义全是假主数据。用户决策：**先记录，后续处理**（未落 _32、未改 DB 字段集）。

## 3. 待处理清单（后续处理，本次未做）

1. **生成层 retCode 约定硬编码 0=成功**（实际后端 1=成功）→ 改 `prompts/response_model.py` + `extraction_prompts.py` 后重新生成；_32 断言约定错乱（还有一处 `retCode: 200` 把业务码当 HTTP 码）；**已 _33 复测证实**（成功 add 被误判失败）
2. **teardown 生成成 add** 而非 delete（生成层缺陷）；**已 _33 复测证实**（teardown 4 块仍全 add）
3. **delete 接口 body `{code}` 返回 400**（真实 delete 契约未确认，delete 相关生成用例跑不通）
4. **DB `/electricMeter/add` 字段集**：查询参数污染收敛 + 补 `initDetailList`（用户前次决策「只改 required 不动字段集」，本轮对比后仍维持，后续可一并处理）
5. **_32 主数据真实化**：华立/DDS28/zhyqE29999415/accessType 4/useType 1（用户决策待定）
6. **dev 遗留测试电表**：`E-METER-001/003/004` + `PROBE-0002`（本轮 setup/探针创建，delete 已知格式清不掉）；**已在 _33 复测命中**——遗留同名导致后续套件 setup 撞 `电表名称请勿重复`
7. **固定假主数据碰撞（_33 新发现）**：设备名/编码写死 + dev DB 持久化 → 跨套件同名（`测试电表A` 撞 _32 遗留）+ 套件内自碰撞（`PRE-00x` 与其 `_isolated_TC-0xx` 复用同一电表）→ 生成层需唯一化（时间戳/随机后缀）或执行前清理遗留

## 4. 改动文件

| 文件 | 改动 |
|------|------|
| `tests/execution/__init__.py` + `tests/execution/test_run_generated_case.py` | 新增执行器（原任务交付） |
| `C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_32/**/*.yaml`（23 文件 40 行） | url 补 `park-energy-electric-web/` 前缀 |
| 同上 setup/teardown（12 行） | `payConfigCode` 假码 → `zhyqP25529429` |

## 5. 明确不改

- **不改** 测试框架（run.py / pytest.ini / common / base / conftest）
- **不改** 生成 prompt / DB / 其他场景（按用户决策，缺陷记录待后续统一处理）
- **不改** 已生成的其余场景 yaml（_13/_30/_31 同样缺前缀，未在本次范围）

## 6. 运行环境备忘（后续复用）

- dev 后端：`https://dev.damaiiot.com:40443`；智慧用电业务接口统一前缀 `park-energy-electric-web/`；登录走 `park-base-auth/login`（独立服务）
- 业务信封：成功 `retCode:1`、业务失败 `retCode:0`（HTTP 200 或 400 不定）
- 真实主数据：payConfig `zhyqP25529429`(固定) / `zhyqP96644027`(分时)；scene `zhyqE29999415`(公司场所)；厂家 `华立` 型号 `DDS28`
- 执行入口：`RUN_GENERATED_CASE=1 python -m pytest tests/execution/test_run_generated_case.py -v -s`（报告落 `logs/`）

## 7. 总结：问题归因与修复方向

### 一、数据不全（总归因，一条）

**根因**：DB 存的接口定义（`api_parameters`/`api_returns`/`value`）不全、不准 → 生成层拿不到真实字段集、真实返回语义、真实主数据 → 只能硬编码假设 + 编造。以下表象**全部归因于此一条**：
- 字段集污染（add 混入查询参数 35 个）+ 缺 `initDetailList`
- delete 契约错（按 body `{code}` 返回 400）
- 主数据假值（MFR001/SCENE001/accessType 1/useType 2）+ **固定名/编码写死 → 跨套件与套件内重名碰撞**（_33 实测 `电表名称请勿重复`/`该设备已存在` 400，§1 层 5）
- retCode 断言约定错（返回结构缺「成功=1」语义 → 生成层只能硬编码猜 0）

**改哪里**：`data/app.db` → `documents` → `/electricMeter/add`（及 delete 等）：
- 收敛字段集（剔查询参数、补 `initDetailList`）、修正 delete 参数
- `value` 列存真实主数据（payConfig `zhyqP25529429`/scene `zhyqE29999415`/厂家 `华立`/型号 `DDS28`）
- `api_returns` 标注成功值语义（`retCode=1` 成功）
- **根治在 Phase A 提取器**：提取 YApi 时存真实字段集/返回语义/主数据示例，而非共享参数表

### 二、生成流程优化方向（流程本身可改，不依赖数据补全）

1. **teardown 生成规则**：按业务语义生成逆操作（add→delete），不镜像复制 add 块
2. **断言先探后断**：生成用例前对接口发一次真实请求，取真实 retCode/msg 再定成功/失败断言，不硬编码 0=成功
3. **生成后自检（冒烟收集）**：生成完跑 collect + setup 冒烟，提前拦截 url 404/405、主数据不存在、必填缺失，失败即回灌生成层，不等到人工执行
4. **主数据引用策略**：主数据字段优先复用同库已存真实值；无真值时打标「待填」而非编造
5. **生成产物验证入口**：`tests/execution/` 执行器即生成后冒烟入口（`RUN_GENERATED_CASE=1`），可挂到生成完成节点自动触发
6. **用例数据唯一化**（_33 层 5 触发）：生成时对关键唯一键（设备名/编码）加时间戳/随机后缀，且 `PRE-00x` 与其 `_isolated_TC-0xx` 不得复用同一实体；或执行前先清理 dev 遗留同名数据

### 三、环境 / 遗留（不属于前两类）

- dev 遗留测试电表 `E-METER-001/003/004` + `PROBE-0002`：待 delete 契约确认后清理；**_33 复测证实**遗留同名已阻塞后续套件 setup（`电表名称请勿重复`）
- 其余场景（_13/_30/_31）同缺前缀 + 同有 retCode 约定问题：随生成层修复后统一重生成；后续套件另需应对 §3-7 固定主数据碰撞
