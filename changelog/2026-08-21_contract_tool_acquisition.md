# contract-tool：从零获取权威接口契约（单目录可整体移走）

> 日期：2026-08-21
> 状态：**已实现，测试通过（28 passed）**
> 决策：DB 接口定义不可信（源自 api.md：query/body 合并、缺 `initDetailList`、delete 契约错、主数据假值）
> → 从零获取权威契约替代。开发读后端代码产出契约 YAML → 导入器以 scope 校验覆盖度、**整体替换**写库。

## 背景

- 提取器 `ingest/api_parser.py` 是忠实镜像——错在源文档形态，不在解析（已确认）。
- 后端代码在开发侧；开发用 webcoding 读代码产出契约文件，我们导入（不写黑盒探测脚本）。
- 框架 YAML 已支持 `testCase.params`（query）/ `testCase.json`（body）；`api_parameters` 是 Text 列
  JSON 数组，`web/tasks.py` 原样进 `{api_definitions}` 喂 LLM，**加 `location` 键自动透传**。

## 交付物（contract-tool/，自包含）

```
contract-tool/
  README.md              格式说明 + CLI + 交接流程（用户→开发→导入）
  scope.example.yaml     检索范围 manifest 模板（用户先填要从零获取哪些接口）
  contract_template.yaml 通用空模板（结构占位符，不带具体业务参数）
  contract_parser.py     契约 YAML → 归一化 API def（纯函数，location 标记/children/master_data 填值）
  importer.py            CLI 导入器（--dry-run 默认 / --apply / --scope 覆盖度 / --allow-extra）
  SKILL.md               编排流程（定 scope→交接开发→dry-run 预览→审阅干预点→apply→重生成→归档）
  tests/                 28 测试（parser 纯函数 + importer 内存 SQLite 集成）
.claude/skills/contract-fix/SKILL.md   薄指针（触发词：/契约导入、/校准接口、/导入契约）
```

## 关键语义（从零获取，非补充/校正）

- **写入 = 整体替换**：scope 内命中 Document 的接口，api_* 全部以契约为准覆盖（不做并集 merge，
  污染字段直接移除）；DB 无此接口 → 默认新建（确定性 `contract_*` doc_id，重复导入稳定命中）。
- **location 标记**：契约 `query[]` → `location="query"`，`body[]` → `location="body"`（children 递归）；
  `response[]` → return（无 location）。生成层 prompt 已补映射规则：
  `location=query` → `testCase.params`，`location=body` → `testCase.json`；无 location 按 GET/DELETE→params、
  POST/PUT/PATCH→json（`prompts/extraction_prompts.py` 四处：analyze/repair/format/single）。
- **master_data 填真值**：字段无 `example` 时按字段名后缀匹配主数据键（`xxxCode` ↔ `code`，大小写不敏感），
  替代旧假值（MFR001/SCENE001 等）。
- **覆盖度闭环**：scope 内接口无契约 → 缺失清单；scope 外契约 → 超范围告警 + 跳过（`--allow-extra` 才导入）；
  全部列进 `import_report.md`，不静默。
- **匹配**：method + url 归一化 + 段级模板回退（**模板段只匹配模板段，字面段只匹配字面段**——
  修正了最初把 `/order/{id}` 通配到 `/order/export` 的过宽匹配）。
- **破坏性兜底**：强制 dry-run 预览 → 人工审阅替换预览/覆盖度 → 才 `--apply`；写库前备份
  `data/backup_contract_<ts>.json`，可回滚。

## 验证

```bash
python -m pytest contract-tool/tests -q          # 28 passed
python contract-tool/importer.py --contracts contract-tool/tests/fixtures/sample_contract.yaml \
    --scope contract-tool/tests/fixtures/sample_scope.yaml          # dry-run 预览（缺失/超范围检出）
# 回归（排除 TestResolveApiDefs 4 条已知 DB 状态预失败：DB 已补前缀，测试仍断言旧 url）
python -m pytest tests/test_phase_bc_unit.py tests/test_phase_c_api.py tests/test_phase_a_analysis.py \
    tests/test_yaml_db_export.py tests/test_py_export_fixture.py tests/test_quality_gate.py \
    --deselect "tests/test_phase_bc_unit.py::TestResolveApiDefs"    # 224 passed
```

## 未做 / 后续

- 未对真实 electricMeter 接口执行导入（需先定 scope 交给开发产出契约；本工具与 skill 已就绪）。
- 不做黑盒探测脚本（开发读代码产出契约替代；后续如需交叉验证可加 `contract-tool/probe.py`）。
- 不自动重灌 ChromaDB；contract 直接写 SQL（Phase C 读 SQL）。
- 新建的行无模块绑定，需绑定才能进模块作用域（报告会标注）。
