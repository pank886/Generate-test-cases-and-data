# 数据工厂新增 random_code：带固定前缀的唯一随机编码

> 日期：2026-08-24
> 状态：**已实现**
> 决策：`data_factory/methods.yaml` 的「数据生成类」此前唯一随机函数是 `random_plates`，LLM 给
> code/name/payConfigCode/sceneCode 等字段需要"唯一/随机"值时只能拿车牌生成器顶用（单节点 36 全程
> 未碰两段式 prompt，属结构性缺口而非"老师"）。新增 `random_code(prefix, length)` 填补通用随机码缺口，
> 语义明确，模型有合适选项。

## 背景

- 单节点 prompt 会把整份 `methods.yaml` 注入 system 段（`prompts/extraction_prompts.py:357-358`），
  「数据生成类」是唯一能产生随机输出的来源，但其中只有 `random_plates`。
- 根因 = 数据工厂缺口：缺通用随机码/随机串生成器，导致模型被迫拿车牌生成器当万能随机函数。

## 改动

**E 盘（LLM 引用清单）**
- `data_factory/methods.yaml`：「数据生成类」新增 `random_code(prefix, length)` 条目
  （syntax/description/params/usage_tips/validation），随 `registry.render_for_prompt()` 自动注入 prompt，
  `prompts/response_model.py` 校验器自动消费 validation 规则（`min_args:1, max_args:2`）。
- `tests/test_phase_bc_unit.py`：方法集合断言 6→7（`test_load_methods_covers_all_six` → `..._seven`）。

**C 盘（执行节点，PyCharmMiscProject）**
- `data_factory/code_generator.py`（新增）：`CodeGenerator` 类，镜像 `PlateGenerator` 的文件持久化
  全局去重（`data/random_code_{build_id}.txt`，本次运行全文件不重复）。
- `data_factory/__init__.py`：导出 `CodeGenerator`。
- `common/debugtilk.py`：新增 `random_code(prefix, length=6)` 方法（`base/apiutil.py replace_load`
  经 `getattr(DebugTalk(), fn)(*parse_dollar_args(...))` 调用）。

## 语义

- 语法：`${random_code('METER', 6)}` → `METER_x8f2k9`（前缀 + `_` + 6 位小写字母数字）。
- `prefix` 必填（业务标识，如 METER/PAY/SCENE），`length` 可选默认 6。
- 唯一性：按完整编码全局去重并持久化；`length<=0` 抛 `ValueError`，空间耗尽有兜底上限。
- 措辞不出现"替代某方法"，避免 LLM 误判为某方法的替代品。

## 验证

- 注册表：`pytest tests/test_phase_bc_unit.py::TestFactoryRegistry` → 5 passed；
  整文件 `test_phase_bc_unit.py`（排除已知 DB 状态预失败的 TestResolveApiDefs）→ 103 passed。
- 运行时（C 盘一次性脚本，WORKSPACE 隔离持久化文件，跑完即删）11 项全过：格式
  （`METER_` + N 位小写字母数字）、默认长度 6、自定义长度、连续 50 个不重复、跨实例持久化去重、
  `length=0/-1` 抛 `ValueError`、缺 prefix 抛 `TypeError`、DebugTalk 端到端、
  `replace_load` 带引号/不带引号参数均正常。
