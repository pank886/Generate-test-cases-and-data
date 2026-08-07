# Phase C 流程 — 输入/输出来源全图

| 项目 | 内容 |
|:---|:---|
| 日期 | 2026-08-07 |
| 阶段定位 | 确认计划 → 生成 `.py` + `.yaml`（含 dependency_map、翻译缓存、修复循环、后校验） |
| 入口 | `web/routes/chat.py:/confirm-plan` → `web/tasks.py:_confirm_plan_bg` |
| 核心文件 | `agent_components/generators/__init__.py`（GenerationMixin）、`_helpers.py`、`agent_components/post_validator.py`、`prompts/extraction_prompts.py`、`prompts/response_model.py`（TestData） |
| 数据落点 | 输出目录（`<feature_en>/` 树下）+ `logs/thinking_trace.log` + `logs/VALIDATION_INTERCEPT.md` |

---

## 〇、总览图

```
  /confirm-plan（excel_path + api_defs_json + user_ctx）
       │
       ▼
┌───────────────────────────────────┐
│ 0. 接口定义解析 _resolve_api_defs │  输入: 显式 api_defs_json / excel 同级 api_defs.json
│    （规则 M8：缺失 → 显式阻断）    │  输出: api_defs_json（快照）
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ Step0: 生成 dependency_map.json   │  输入: Excel 行 + api_defs + 模块树
│   LLM thinking → DependencyMap    │        + 产品文档(ChromaDB 检索) + 数据工厂
│   失败 → 非致命（日志告警继续）     │  输出: dependency_map.json
└──────────────┬────────────────────┘      ⚠ 注：当前生成后未被下游消费（见 2026-08-07 检查清单 1.4）
               ▼
┌───────────────────────────────────┐
│ C4: 英文翻译 _translate_to_en     │  输入: Excel feature/story/title 中文
│   缓存优先 → LLM → 拼音兜底       │  输出: translation_cache.json
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ C: 生成 .py 文件 _generate_py_file │  纯代码组装（不经 LLM）
│   → test_<feature_en>.py          │  输入: Excel 行 + 翻译 + 共享前置
│   fixture + run_blocks 结构        │  输出: .py（fixture 引用 setup/teardown YAML）
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ C: 生成 YAML _generate_all_yamls  │  yaml_tasks:
│   ① test_data.yaml（每用例）       │    test_data.yaml / setup_*.yaml / teardown_*.yaml
│   ② setup_data/setup_*.yaml       │  → _run_yaml_rounds（并发）
│   ③ setup_data/teardown_*.yaml    │
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ YAML 单文件生成 _generate_one_yaml│  （两段式，无 inline 重试）
│  ① thinking 分析（free_text）     │  输入: api_defs + 用例逻辑 + 数据工厂
│    analyze_yaml_data_prompt       │        + db_schema + user_ctx
│  ② json_mode 格式化（thinking off）│  输出: TestData → YAML 文件
│    format_yaml_data_prompt        │
│    pre_validate: 注入 _annotations│
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ 规整层（确定性，静默）             │  method 小写 / url 去域名 / header 按 CT 补全
│   normalize_base_info             │  / 断言合并 / 空 {} 剔除（response_model）
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ 校验层（Pydantic，回炉类）         │  {{}} 占位符幻觉 / 非注册表函数 / 实参不合规
│   TestData model_validator        │  / json·params·data 三选一 / 空列表
└──────────────┬────────────────────┘   / 提取值非 str / db 断言无 schema / 导出 eq 断言
               │
        ┌──────▼──────┐
        │ 通过 → 原子写盘 │（tmp + os.replace；路径参数/导出断言写盘前注入）
        │ 失败 → 登记占位 │ GEN-FAIL-R{轮}-{序}（不写盘）
        └──────┬──────┘
               ▼
┌───────────────────────────────────┐
│ 修复循环 _run_yaml_rounds          │  ≤ YAML_REPAIR_ROUNDS(1)
│  轮末全批次错误模式汇总            │  → repair_yaml_data_prompt（thinking 自查）
│  → 修复轮重生成                    │  → 注入 {prior_output + error_detail +
│                                   │      error_pattern_summary + post_check_issues}
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ 终态                             │  失败 → _generation_errors.json + 计 failed
│                                   │  （无占位假文件）
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ 后校验 YamlPostValidator          │  delete_body_wrapper(P0) / 断言动态 key(P1)
│   → _post_validation_issues.json  │  / 引号不配对(P2)
│   P0/P1 → 追加修复轮（默认配置下  │  ⚠ 死逻辑：见 2026-08-07 检查清单 1.1
│   该条件恒 False，不触发）         │
└──────────────┬────────────────────┘
               ▼
┌───────────────────────────────────┐
│ ValidationInterceptor.write_report│  logs/VALIDATION_INTERCEPT.md（拦截统计）
└──────────────┬────────────────────┘
               ▼
          完成 → 前端消息：.py + YAML 成功/失败 + _generation_errors.json 指引
```

---

## 一、阶段输入/输出对照表

| 步骤 | 输入 | 输入来源 | 输出 | 输出去向 |
|:--|:---|:---|:---|:---|
| 0 接口定义解析 | `excel_path` + `api_defs_json` | 前端 `/confirm-plan` + excel 同级 `api_defs.json` 快照（Phase B 落盘） | `api_defs_json` | 后续 YAML 生成 prompt |
| Step0 dep_map | Excel 行 + `api_defs_json` + 模块树 + 产品文档 + 数据工厂方法 | `_read_excel_rows` + 快照 + `ModuleOps.get_tree` + ChromaDB `search_product_docs` + `registry.render_for_prompt` | `dependency_map.json` | 输出目录（当前**不被消费**） |
| C4 翻译 | feature/story/title 中文 | Excel 行 | `feature_en/story_en/title_en` | `translation_cache.json`（Excel 同级） |
| C .py 生成 | Excel 行 + 翻译 + 共享前置 | `_read_excel_rows` + `_read_shared_preconditions` | `test_<feature_en>.py`（`import_header` + class + fixture + `run_blocks`） | 输出目录 `<feature_en>/` |
| C YAML 生成 | Excel 行 + `api_defs_json` + `user_ctx` + 翻译 | 前端 + 快照 + `_translate_to_en` | YAML 三件套 | 输出目录 |

### YAML 任务清单（`_generate_all_yamls` 构造）

| 任务 | 行内容（row） | 输出路径 | 说明 |
|:--|:---|:---|:---|
| test_data.yaml | 完整用例行（steps/expected/case_id） | `<feature_en>/<func_en>/test_data.yaml` | 每 TC 一个目录，含全部步骤 |
| setup_*.yaml | `# PRE-xxx: 名称` + 前置步骤文本 | `<feature_en>/setup_data/setup_<class_slug>.yaml` | 共享前置 → LLM 转 API 调用 |
| teardown_*.yaml | `根据 PRE-xxx 的创建步骤逆向操作：` + 步骤前 200 字符 | `<feature_en>/setup_data/teardown_<class_slug>.yaml` | 逆向清理（弱设计，见检查清单 2.3） |

---

## 二、`_generate_one_yaml` 单文件 I/O 明细

| 项 | 输入 | 来源 | 输出 | 去向 |
|:--|:---|:---|:---|:---|
| 阶段1 thinking 分析 | `api_definitions` + `test_case_logic`(步骤/预期) + `user_context` + `data_factory_methods` + `db_schema` | 快照 + Excel 行 + 前端 + `methods.yaml` + `config.DB_SCHEMA` | 自由文本分析 | 全文写 `thinking_trace.log`（`analyze_yaml_data`） |
| 阶段2 json_mode 格式化 | 分析文本 + 接口定义 + 用例逻辑 + 数据工厂 + db_schema | 阶段1 输出 + 快照 | `TestData` 模型 | Pydantic 校验 → 序列化 |
| pre_validate 钩子 | LLM 原始 dict | `_invoke_structured` 内部 | 注入 `baseInfo._annotations`（is_export 补占位断言） | 校验前 |
| 写盘前注入 | `result.data` | 阶段2 | 路径参数 `{xxx}` → `${get_extract_data(xxx)}`；is_export 断言接管为 `contains:{status_code:200}` | 写盘前 |
| 序列化 | `step.model_dump(exclude_none, by_alias)` | 模型 | 清理 `_annotations` + `yaml.dump` | `tmp` → `os.replace` 原子写盘 |

### 校验层规则（TestData / StepData / TestCase 的 model_validator）

| 规则 | 类别 | 拦截内容 |
|:--|:--|:---|
| `validate_placeholders` | B1-B4 | `{{}}` 双花括号 / 运算拼接 / 非注册表函数 / 实参越界 / 首参非枚举 / 断言 key 含 `${` |
| `validate_body_exclusivity` | B9 | json/params/data 三选一并存 |
| `validate_no_db_when_no_schema` | — | db_schema 为空时出现 `db` 断言 |
| `validate_export_assertion` | — | is_export 接口用 eq/ne 检查状态码 |
| `validate_url_no_placeholder` | — | url 含 `${` 或未标注的 `{xxx}` 字面量路径参数 |
| `validate_header_exists` | — | baseInfo 缺 `header` 键 |
| `validate_method_body_match` | — | GET/DELETE 用 json；POST/PUT/PATCH 声明 JSON 却用 params |
| 提取值类型 | B5/B10 | `extract` 系字段非 str（`Dict[str, str]` 严格校验） |
| 空列表 | B6/B7 | `testCase` / `data` 空、`validation` 空 |

---

## 三、修复循环与后校验 I/O

| 环节 | 输入 | 来源 | 输出 | 去向 |
|:--|:---|:---|:---|:---|
| 轮次循环 `_run_yaml_rounds` | `yaml_tasks` + `api_defs_json` + `user_ctx` | `_generate_all_yamls` 构造 | `{total, success, failed, repaired, rounds, errors_file}` | 汇总 + 前端消息 |
| 失败登记 | 异常 `err_text` | `_generate_one_yaml` 抛错 | `GEN-FAIL-R{轮}-{序}` + `raw_output_snippet` + `case_id` | `_generation_error_details.log`（原文+错误点）+ `thinking_trace.log` |
| 错误模式汇总 | `failures[]` | `_summarize_error_patterns`（B 类聚合） | `pattern` 文本 | 注入修复轮 prompt |
| 修复轮 | `{prior_output, error_detail, error_pattern_summary, round_no, post_check_issues}` | 上轮失败 + 全批次模式 | `repair_yaml_data_prompt` 思考 → 重生成 | 同单文件流程 |
| 终态失败 | 修复轮耗尽仍失败 | `registry` | `_generation_errors.json`（含 placeholder_id/case_id/yaml_path/rounds/error/snippet） | 输出目录（前端指引查看） |
| 后校验 | 全部 YAML 产物 | `YamlPostValidator.validate_all`（glob 扫描） | `_post_validation_issues.json` | 输出目录；P0/P1 设计上追加修复轮（默认配置死逻辑，见检查清单 1.1） |
| 拦截统计 | 校验拦截记录 | `ValidationInterceptor` | `logs/VALIDATION_INTERCEPT.md` | `logs/`（提示词优化依据） |

---

## 四、与相邻阶段的数据交接

| 交接 | 载体 | 说明 |
|:--|:---|:---|
| Phase B → Phase C | `test_plan.xlsx` + `api_defs.json` | `/confirm-plan` 入参；快照缺失/为空 → M8 显式阻断 |
| Phase A → Phase C | ChromaDB 产品文档（dep_map 检索） | `search_product_docs(query=user_ctx)` |
| Phase C → 运行时 | 输出目录 `.py` + `.yaml` | `RequestsBase().run_blocks(get_testcase_yaml(...))` 消费 |

---

## 五、关键设计约束

1. **两段式生成，无 inline 重试**：thinking 自由分析 → json_mode 单次输出；校验失败不原地重打（json_mode 无思考无法纠正信念型错误），登记占位进轮末自查修复循环。
2. **规整/重生成两分法**：确定性格式错误（method 大小写/url 域名/header 缺失）由代码静默修正；语义性错误（占位符幻觉/三选一/空输出）登记后送思考节点重生成。
3. **无占位假文件**：终态失败只写 `_generation_errors.json`，绝不写占位 YAML。
4. **数据工厂单一事实源**：`data_factory/methods.yaml` 一处维护，prompt 渲染 / `validate_placeholders` / 单测三处同源。
5. **已知缺口**（详见 `2026-08-07_phase_c_flow_checklist.md`）：dependency_map 未消费（1.4）、后校验修复轮默认死逻辑（1.1）、失败计数覆盖（1.2）、`.py` 与 YAML 失败脱节（1.3）、retCode:0 prompt 未修（3.1）。
