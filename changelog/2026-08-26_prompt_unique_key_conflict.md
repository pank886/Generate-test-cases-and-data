# 2026-08-26 唯一键冲突修复：铁律 10/13 改写（前缀+随机消解「用例值优先」冲突）

## 背景

智慧用电_37 v3 重生成后 setup 全挂（20 ERROR），根因：

- PRE-001 / PRE-001_isolated 用固定 `name="测试电表B"` → 同轮自撞「电表名称请勿重复」
- PRE-003/004 写死 `code=ELEC_BIND/ELEC_PARENT` → 撞 dev 历史残留「该设备已存在」

对照 v2（动态化通过）与 v3（写死失败），定位为 **prompt 内部规则冲突**：

| 规则 | 要求 | 冲突 |
|---|---|---|
| 铁律 10 | 唯一键必须动态生成，禁止固定假值 | 与「用例值优先」冲突 |
| 铁律 13 | 引用前置资源必须变量引用，禁止写死 | 与「用例值优先」冲突 |
| human 段取值规则 | 字段值按 value > desc > 用例值 优先 | 「用例值」优先级最高 → LLM 采用 excel 写死的字面值 |

LLM 面对「唯一化」与「用例值优先」两条冲突指令时，v2/v3 各选了不同边（随机性），无法稳定遵守。

## 修复方案：前缀+随机消解冲突

工厂方法支持「前缀 + 随机后缀」，让「用例值」与「唯一化」**同时成立**：

- 用例值「测试电表B」保留作前缀 `${random_code('测试电表B')}` → 满足用例值优先
- 随机后缀 → 满足唯一键动态化
- LLM 无需判断「该值该不该动」，只需给唯一键套工厂方法 → 冲突判断消失

额外收益：contains 断言可命中前缀（`ELEC_PARENT` ⊂ `ELEC_PARENT_ab12`）。

**边界**：只适用「创建动作」的唯一键；引用动作（delete/断言引用已创建资源）禁止随机，必须变量引用创建时提取的同一值。

## 改动内容

### 1. prompts/extraction_prompts.py 铁律 10 改写

原：
```
10. 数据唯一化：设备名/编码等唯一键必须动态生成（${{ }} 工厂方法或时间戳/随机后缀），禁止输出固定假值——防跨套件重名与套件内自碰撞；
   此规则覆盖 setup/前置创建步骤与用例内创建步骤——任何创建型步骤的唯一键都要动态生成
```
改：
```
10. 唯一键动态化：设备名/编码等唯一键一律用工厂方法生成，可保留用例给出的值作前缀并追加随机后缀，禁止输出纯字面值；覆盖 setup 与用例内所有创建步骤
```

### 2. prompts/extraction_prompts.py 铁律 13 末尾补半句

原：
```
13. 跨步骤/前置资源引用：引用前置（setup）或上游步骤创建的资源标识（code/name 等），必须从创建步骤
   input_extract 提取 key（如 $.json.code），本步骤用 ${{get_extract_data(key)}} 引用；禁止写死或重新构造
```
改：末尾追加：
```
   引用动作禁止随机生成——必须引用创建时提取的同一值
```

### 3. 校验器决策：不加入（方案外发现）

原计划加「setup 创建块唯一键字段字面值 → 拦截回炉」校验器。**发现不可行**：`YamlPostValidator` 为纯结构验证器，无接口 desc 上下文，无法判断「哪些字段是唯一键」；注入 desc 集增加耦合，违背最小改动。

降级：静态检查脚本作为**测试验证手段**（不进运行流程）——读取 DB desc（含「唯一」字段集）+ 遍历生成的 setup YAML，检查唯一键字段是否含 `${`。

## 预期验证（v4 重生成后比对）

1. setup PRE-001/isolated name、PRE-003/004 code 均含 `${` 且保留原值前缀
2. duplicate code、delete 引用为变量（get_extract_data / ${变量}），非字面值
3. 跑框架：setup 无「名称请勿重复」/「该设备已存在」，isolated 自撞消除
4. 回归：A/E 校验器、B 类 msg=fail 断言不被破坏

## 已知接受（用户决策）

- getList/getParentList 从模块绑定移除（查询接口不管了），其用例在无接口定义下生成、失败可接受
- 不为 status_code 等固定键值对增加 prompt
- 校验器不新增（无 desc 上下文，纯 prompt 主防线）

## v4 验证结果（2026-08-26）

**达成（铁律 10/13 预期核心项）**
- setup 唯一键全动态化：PRE-001/isolated/PRE-003/004 均含 `${` 且保留原值前缀（静态检查确认）
- isolated 自撞消除：PRE-001 + isolated 同轮都成功，无「名称请勿重复」
- PRE-004 成功（动态 ELEC_PARENT_xxx）

**新发现（v4 引入）**
- PRE-003 失败「计费方案类型与电表类型不匹配」：LLM 给 PRE-003 选了三相，但 zhyqP25529429 是单相方案。
  实验矩阵（A 三相+initDetailList空❌ / B 单相+initDetailList空✅ / C 三相无initDetailList❌ / D 单相无initDetailList✅）
  证实根因=单相→三相，initDetailList 无关。用户质疑「问题不出在单相→三相」经实验证伪——
  根因确为方案-类型不匹配，深层是数据源缺「方案-类型匹配」事实（desc 只给了编码没给类型）。

**数据源补事实（本次追加）**
- add.payConfigCode desc 补类型匹配（2026-08-26 实测矩阵）：
  - 固定计费 zhyqP25529429 仅匹配单相（meterDeviceType=单相、meterTypeCode=1）
  - 分时 zhyqP96644027 仅匹配三相（meterDeviceType=三相、meterTypeCode=2）
  - 实测：分时+单相❌/三相✅/双相❌；固定+单相✅/三相❌/双相❌

**待办记录（teardown 缺陷，本轮不修，聚焦验证）**
- 生成器把 teardown 生成为 add（添加）而非 delete（删除），cleanup 写死 ELEC_BIND/ELEC_PARENT——
  teardown 永不清理，是跨轮残留积累的机制。对照组 teardown 应为 delete 清理创建的电表。
  需排查 teardown 生成机制（待办，用户决策记录，本轮不处理）。

## v5 验证结果（2026-08-26）

**核心目标全部达成**（junit：20 用例 = 9 PASS / 11 FAIL / 0 ERROR）
- ✅ setup 4 块全过（0 ERROR）——PRE-003 单相 + zhyqP25529429 匹配，无「名称请勿重复」「该设备已存在」
- ✅ isolated 自撞消除：PRE-001 + isolated 同轮均成功
- ✅ PRE-003 修复生效：LLM 选单相/1，不再触发「计费方案类型与电表类型不匹配」
- ✅ 唯一键全动态化：setup 层静态检查 0 问题（name/code 均含 `${`）
- ✅ A/E 校验器零回炉：24/24 一次通过（生成 YAML 无显式字段键操作数、无 contains 非标量值）
- ✅ 铁律 13 引用变量化：delete 引用均用 get_extract_data

**11 个 FAIL 分类（均非本次铁律改写引入的回归）**

| 类别 | 数量 | 用例 | 根因 |
|---|---|---|---|
| 查询接口（用户决策可接受） | 4 | getList 分页/非法页/非法排序、getParentList | 查询接口不在模块绑定，失败可接受 |
| B 类负向用例「期望 fail 但后端成功」 | 3 | invalid_category、billing_not_selected、gateway_protocol_not_selected | LLM 假设了不存在的后端校验：meterTypeCode='0'、不传 payConfigCode、不传 accessType 后端均放行 |
| delete 接口问题 | 2 | delete_positive、delete_bound_billing | positive：delete 后 getList 非空（delete 未生效或断言目标错）；bound：**KeyError 'ELEC_BIND'**，setup 提取 key=pre003MeterCode，用例引用 ELEC_BIND，键名不匹配 |
| 正向用例 contains 断言写法 | 2 | gateway_protocol_required、tou_positive | LLM 写 JSON 格式子串 `"accessMethod":1` 匹配不到 dict repr `'accessMethod': '1'`；tou 断言 getList data 含 sharp，但 sharp 在 initDetailList 不在 getList 返回 |

**方案外发现（需决策）**
1. **引用键名不匹配（铁律 13 机制缺口）**：setup 块 input_extract 的 key 是 LLM 自由命名（pre003MeterCode），用例层引用写另一个名（ELEC_BIND）→ KeyError。生成式引用固有脆弱点，对照组用「case名_前缀」约定命名。是否修？需方案。
2. **B 类负向用例有效性**：3 个负向用例假设了后端不存在的校验（枚举/必填），期望 fail 实际 success。是「后端无此校验」的数据事实问题（可补 desc 或改用例），还是接受负向用例失败？需决策。
3. **delete 接口语义**：delete_positive 中 delete 后 getList 仍非空，可能 delete body 格式（传 code 字符串数组）或逻辑删除未被 getList 过滤。需查 delete 接口定义。

## 方案外发现处置（2026-08-26 用户决策）

**发现 1 → 静态检查拦截**（已实现）
- `tests/tools/_check_37_unique_keys.py`（2026-08-27 由根目录迁入 `tests/tools/`）新增第 4 步「引用键一致性」：收集 setup_data 全部 input_extract keys + 本文件内已提取 keys，校验用例 `get_extract_data('key')` 引用必须在其中。
- 实测抓到 delete_bound 引用 `ELEC_BIND` 未定义（setup 提取键全集：createdMeterCode/electricMeterCode/pre001MeterCode/pre001IsolatedMeterCode/pre003MeterCode/pre004MeterCode）。
- 佐证机制缺口：setup 提取 key 有 4 种命名风格并存，LLM 自由命名导致用例层引用难对齐。拦截器作为验证手段（不进运行流程）。

**发现 2 → 记录不修**（用户决策）
- 3 个 B 类负向用例（meterTypeCode='0' 非法枚举/不传 payConfigCode/不传 accessType）期望 fail 但后端放行。后端确实无此校验，用例断言 fail 不成立。记录数据事实，本轮不修。
- **实测证据（pytest_v5.log，dev 后端 2026-08-26 19:34 实测，均为 add 接口）**：
  - invalid_category → `test_AddElectricMeter_InvalidMeterTypeCode_001`：请求 `{accessMethod:'1', code:'METER_pi657d', meterDeviceType:'单相', meterTypeCode:'0', name:'METERN_bqclds', sceneCode:'zhyqE29999415', sceneName:'公司场所'}` → 返回 `{"retCode":1,"msg":"success","data":null}`（**meterTypeCode='0' 非法枚举后端放行**）
  - billing_not_selected → `test_ElectricMeter_Add_Charge_WithoutPayConfig_001`：请求 `{accessMethod:'1', code:'METER_oicw1j', meterDeviceType:'单相', meterTypeCode:'1', name:'METER_NAME_05zq7d', sceneCode:'zhyqE29999415', sceneName:'公司场所'}`（无 `payConfigCode`） → 返回 `{"retCode":1,"msg":"success","data":null}`（**收费电表不传计费方案后端放行**）
  - gateway_protocol_not_selected → `test_ElectricMeterAdd_GatewayWithoutAccessType_negative`：请求 `{accessMethod:'1', code:'METER_CODE_br90eh', meterDeviceType:'单相', meterTypeCode:'1', name:'METER_opm3zq', sceneCode:'zhyqE29999415', sceneName:'公司场所'}`（无 `accessType`） → 返回 `{"retCode":1,"msg":"success","data":null}`（**网关接入不传协议后端放行**）
  - 归因：3 个用例的「必填/枚举校验」假设均不存在于 dev 后端 add 接口。若要修：后端补校验（记 bug）或改用例为「后端真实存在的校验」（如 code 为空「电表编号不能为空」、初始电量负数「初始电量必须大于0.00」）。

**发现 3 → 查 delete 定义，已定位**（用户决策）
- 实测 add→delete→getList 链路：DEL retCode=1 success（delete 本身正常，body 传 code 数组格式正确）。
- **根因 = getList 的 code 过滤参数不生效**：GETL 传 `{code: 刚删除的code}` 仍返回分页第一页全量电表（含 ELEC_002_yal47h 等其他残留），非空。delete_positive 的 `$.data: []` 收尾断言因此必然失败。
- 归因：getList 属查询接口（用户已决策失败可接受）；code 过滤不生效是查询接口行为事实，若要修需在 getList desc 标注「code 参数不生效，查询返回分页全量」或用 searchKey 验证。

## 待办清单（2026-08-26 追加，任务 #33）

以下为 v5 验证后遗留待办，均经用户决策「记录不修 / 聚焦验证」，非本方案范围：

1. **teardown 生成缺陷**：生成器把 teardown 生成为 add（添加）而非 delete（删除），cleanup 写死 ELEC_BIND/ELEC_PARENT，永不清理 → 跨轮残留累积机制。对照组 teardown 应为 delete 清理创建的电表。需排查 teardown 生成机制（是 prompt 把 cleanup 步骤错建成添加，还是生成器对 teardown 语义无约束）。

2. **getList code 过滤不生效**：实测 GETL 传 `{code: 刚删除的code}` 仍返回分页全量（含 ELEC_002 等残留），code 过滤参数被忽略。delete_positive 的 `$.data:[]` 收尾断言因此必然失败。属查询接口行为（用户已决策失败可接受）；若要修需在 getList desc 标注「code 参数不生效，查询返回分页全量」或改用 searchKey 验证删除效果。

3. **duplicate/getList 用例写死 ELEC_001**：负向用例依赖「已存在的 code」，但 ELEC_001 未被前置创建则资源不存在，用例无效。静态检查已报（`test_add_meter_duplicate_number_negative`、`test_get_meter_list_pagination_positive`）。修法方向：duplicate 负向用例应先创建目标 code 再断言重复，或将 code 改为对已创建资源（setup）的引用。

4. **B 类负向用例记录**（同上节「发现 2」）：3 个负向用例后端无枚举/必填校验，期望 fail 实际 success，已记录数据事实，未修。
