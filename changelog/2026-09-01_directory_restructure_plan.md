# 2026-09-01 目录重构施工计划（终版·用户已评审定稿）

> 用户评审通过，3 项决策落定：
> 1. **决策 A**：`database/` **暂不移**，重构后看整体效果再定（246 处 import 不值得为分层而改，非本次靶点）
> 2. **决策 B**：prompts **不物理拆文件**，`extraction_prompts.py` 保留单文件、内部按 Phase 分层注释
> 3. **采纳微调**：`api_annotations.py` → `infrastructure/annotations/`；`prompt_builder.py` **合并**进 `graph/nodes.py` 私有方法；`infrastructure/__init__.py` **空、不 re-export**

## 一、目标目录树（最终形态）

```
infrastructure/                # 第一层：纯基础设施（无业务逻辑）
  __init__.py                  # 空（不 re-export，杜绝 from infrastructure import 双路径并存）
  config.py                    # ← 根 config.py（薄包装层，引用 settings）
  settings.py                  # ← 根 settings.py（配置中心单例）
  observability.py             # ← 根 observability.py（日志/可观测，引用 config）
  annotations/                 # ← agent_components/api_annotations.py（跨 A/B/C/web 标注注册表）
    __init__.py
    api_annotations.py
  llm/                         # ← agent_components/llm/ + llm_client.py
    __init__.py  base.py  deepseek.py  client.py
  vector_store/                # ← dual_chroma.py + fallback_embeddings.py（跨阶段向量库）
    __init__.py  dual_chroma.py  fallback_embeddings.py

database/                      # 暂不移（保留根目录，重构后复评）

prompts/                       # 单一包，extraction_prompts.py 文件内按 Phase 分层（决策 B）
  __init__.py  response_model.py  definitions.py  extraction_prompts.py

agent_components/
  __init__.py                  # re-export State/ChatTestAgentGraph/build_workflow（外部 6 处零改动）
  graph/                       # NEW：Phase B 工作流
    __init__.py
    nodes.py                   # ← nodes.py，并吸收 prompt_builder.prepare_plan_prompt_vars 为私有方法
    graph_builder.py  state.py  graph_logging.py
  retrievers.py                # 留原位（Mixin，被 graph/nodes 继承，防循环导入）
  axure_parser.py              # 留原位（被 ingest/pipelines 直接依赖，保持 ingest 纯净）
  generators/  validation/     # 不动（Phase C）

ingest/  web/  data_factory/  scripts/   → 原样不动
根：main.py  web_app.py  ingest_v2.py  apikey.py → 原样不动（入口）
```

## 二、移动映射表（源 → 目标 → 消费者改动）

| # | 源 | 目标 | 消费者改动 |
|---|---|---|---|
| 1 | config.py | infrastructure/config.py | 42 处 |
| 2 | settings.py | infrastructure/settings.py | 3 处 |
| 3 | observability.py | infrastructure/observability.py | 43 处 |
| 4 | agent_components/api_annotations.py | infrastructure/annotations/api_annotations.py | 26 处 |
| 5 | agent_components/llm_client.py | infrastructure/llm/client.py | 5 处 |
| 6 | agent_components/llm/ | infrastructure/llm/ | 内部一致 |
| 7 | agent_components/dual_chroma.py | infrastructure/vector_store/dual_chroma.py | 24 处 |
| 8 | agent_components/fallback_embeddings.py | infrastructure/vector_store/fallback_embeddings.py | 2 处 |
| 9 | agent_components/nodes.py | agent_components/graph/nodes.py | 20 处 |
| 10 | agent_components/graph_builder.py | agent_components/graph/graph_builder.py | 6 处 |
| 11 | agent_components/state.py | agent_components/graph/state.py | 5 处 |
| 12 | agent_components/graph_logging.py | agent_components/graph/graph_logging.py | 1 处 |
| 13 | agent_components/prompt_builder.py | **并入 graph/nodes.py 私有方法**，删除文件 | 0 处 |
| 14 | prompts/extraction_prompts.py | **文件内按 Phase 分层注释**（不拆文件） | 0 处 |

## 三、决策记录

| 决策 | 结论 | 理由 |
|---|---|---|
| A. database/ 归位 | **暂不移** | 本身规整、非混乱源；246 处 import 远超重构收益；重构后复评 |
| B. prompts 拆分 | **文件内分层** | 只拆 extraction_prompts 单文件价值有限；注释分段已满足"细分成三部分"意图 |
| api_annotations 归属 | **移入 infrastructure/annotations/** | 无业务逻辑、纯注册表+规则引擎、被 ingest/generators/web 三方横切 |
| prompt_builder 策略 | **合并进 nodes.py** | 39 行单函数仅 graph/nodes 内部使用，独立成文件过度碎片化 |
| infrastructure/__init__ | **空** | 全部调用方显式 `from infrastructure.xxx import yyy`，杜绝双写法并存 |

## 四、import 替换规则（全量更新、机械替换）

| 旧写法 | 新写法 |
|---|---|
| `from config import X` | `from infrastructure.config import X` |
| `import config` | `import infrastructure.config as config` |
| `import config as _config` | `import infrastructure.config as _config` |
| `import config as _cfg` | `import infrastructure.config as _cfg` |
| `from settings import settings` | `from infrastructure.settings import settings` |
| `from observability import X` | `from infrastructure.observability import X` |
| `import observability` | `import infrastructure.observability as observability` |

替换顺序：先 `from ... import`，再 `import X as _Y` 变体，最后裸 `import X`（防误匹配 `import config as _config`）。

## 五、执行顺序（每阶段全量 pytest + 单阶段 git commit 可 revert）

| 阶段 | 内容 | 验证 |
|---|---|---|
| P1 | infrastructure/ 基础：config + settings + observability 归位 + __init__ 空 | 全套测试 |
| P2 | infrastructure/llm/ + vector_store/ + annotations/（自 agent_components） | 全套测试 |
| P3 | ~~database → infrastructure/db~~（取消） | — |
| P4 | agent_components/graph/（nodes 合并 prompt_builder）+ __init__ 更新 | 全套测试 |
| P5 | extraction_prompts.py 文件内 Phase 分层注释 | 全套测试 |
| P6 | 全量清理（删旧文件、__pycache__、import 烟雾测试同步断言）、changelog 追加 | 全套测试 |

## 六、规模汇总

| 项 | 数量 |
|---|---|
| 物理移动文件 | ~15 个（不含 database） |
| import 改动 | ~230 处 |
| 删除旧文件 | ~12 个（prompt_builder.py 等被合并/迁移的源文件） |
| 新增 __init__.py | infrastructure、annotations、vector_store、graph（llm 已有） |

## 七、执行结果（2026-09-01 完成）

| 阶段 | 状态 | 结果 |
|---|---|---|
| P1 | ✅ | config/settings/observability → infrastructure/；**修复 BASE_DIR 隐式耦合**（`dirname(abspath(__file__))` 迁移后指向 infrastructure/，改为上溯两级到项目根）；全仓 42/3/43 处 import + 2 处 mock 字符串重写 |
| P2 | ✅ | llm/ + vector_store/ + annotations/ 归位；26+5+24+2 处 import + 3 处 mock 字符串（`agent_components.dual_chroma.get_chroma_db`）重写 |
| P3 | ➖ | 取消（database 暂不移，重构后复评） |
| P4 | ✅ | graph/ 子包提取（nodes/graph_builder/state/graph_logging）；prompt_builder.py 合并进 `nodes._prepare_plan_prompt_vars` 并删除；__init__ re-export 指向 graph.*，`from agent_components import ChatTestAgentGraph` 6 处零改动 |
| P5 | ✅ | extraction_prompts.py 文件内 Phase 分层（docstring 索引 + 14 处函数级标记），不拆文件 |
| P6 | ✅ | 全量清理 + 残留 mock 字符串/注释修正（1 处 nodes mock）+ 空目录清理 + 本记录 |

**每阶段全量 pytest 验证**：P1/P2/P4/P5 均稳定在 `682 passed / 9 failed`，9 个失败全部为前置环境性断言失败（dev 前缀 / prompt 文案），与基线一致，**无重构引入回归**。

**执行中发现的隐藏陷阱**（超出纯 import 路径替换）：
1. `config.BASE_DIR` 由 `dirname(abspath(__file__))` 推导 → 迁移后必须上溯两级，否则 LOG_DIR/TESTCASE_BASE/uploads 静默错位
2. `@patch("config.CHROMA_RETRY_DELAY")` 等 **mock 字符串目标不随 import 语句自动更新**，需单独重写（config/observability/dual_chroma/nodes 共 9 处）
3. `from agent_components import dual_chroma` **子模块导入形式**随模块迁出失效（6 处测试）
4. `logs/` 下调试脚本引用 `agent_components.nodes`，已同步更新避免静默损坏
5. `git rm` 对含本地改动的文件需 `-f`（prompt_builder.py 合并删除时）

**最终形态**：
```
infrastructure/  config/settings/observability + llm/{base,client,deepseek}
                 + vector_store/{dual_chroma,fallback_embeddings} + annotations/{api_annotations}
agent_components/ graph/{nodes,graph_builder,state,graph_logging} + retrievers/axure_parser/generators/validation
prompts/         extraction_prompts.py（文件内 Phase 分层）
database/        保留根目录（重构后复评）
```

## 八、后续清理（2026-09-01）

`scripts/`（migrate_chroma_to_sqlite.py）与 `backups/`（v9 数据快照）迁入 `tests/`：
- `scripts/migrate_chroma_to_sqlite.py` → `tests/tools/`（一次性迁移工具，同批次检验辅助工具类）
- `backups/` → `tests/backups/`（生成数据快照，唯一副本）
- 两者**不追踪**：已 `git rm --cached` 脚本 + 加入 `.gitignore`（`tests/tools/migrate_chroma_to_sqlite.py`、`tests/backups/`）
- 项目测试结束后统一清理
- **修复迁移引入的 `sys.path` 深度 bug**：脚本原 `dirname(dirname(__file__))` 在 scripts/ 指向项目根，迁到 tests/tools/ 后需上溯三级

## 九、死代码清理（先注释）+ Phase A 归拢（2026-09-01）

评审 `prompts/response_model.py` 与 `prompts/extraction_prompts.py` 后的处理：

**1. 死代码注释（保留可回滚，运行确认后删除）**
- `TestPointItem`（真死代码，全仓零引用，非字段类型）
- `DataPlanStep`（仅被 import 烟雾测试吊着，运行时零引用）
- `ExcelRow` + `ExcelPlan` v1（v1 休眠：运行时 `excel_plan` 键恒为 ExcelPlanV2，v1 从不构造；`ExcelRow` 唯一消费者是 v1 的 rows 字段，一并注释）
- 连带清理 5 个引用点：`state.py`（import + `excel_plan` 注解）、`nodes.py`（import）、`prompts/__init__.py`（import + `__all__`）、`test_regression_import_smoke.py`（DataPlanStep 断言）
- ⚠️ **意外发现**：`class ExcelPlan` 被误触为 `class 1ExcelPlan`（非法标识符，SyntaxError），导致 response_model.py 无法编译、基线 682/9 不可复现——随 v1 注释一并消除（HEAD 版本确认正确，为笔误）

**2. Phase A 归拢**
- `extraction_prompts.py` 尾 4 个 Phase A 函数（`batch_chunk_summary` + 3 个 `analyze_*`）搬至文件头 Phase A 区，形成 A(7)→B(1)→C(4) 连续布局
- 纯函数重排，零行为变化；docstring 按 Phase 列函数名不受物理顺序影响，无需改

**3. 验证**：全量 pytest `682 passed / 9 failed`，与基线完全一致（9 个失败全为 `test_phase_bc_unit.py` 环境性断言，与本次改动无关），零回归

## 十、definitions.py 收尾：PromptFactory 并入 extraction_prompts.py（2026-09-01）

用户决策：**只移后两个 live 方法，前两个 legacy 方法注释舍弃**。

**1. 迁移（live，均为纯 `ChatPromptTemplate.from_messages` 构造器，不使用 self，展平零成本）**
- `generate_excel_plan_thinking` → `extraction_prompts.generate_excel_plan_thinking_prompt()`（Phase B 区）
- `confirm_user_intent` → `extraction_prompts.confirm_user_intent_prompt()`（Phase C 区）
- 调用方更新：nodes.py:164、retrievers.py:376 改懒加载函数调用（对齐 repair 的懒加载模式）；`self.prompt_factory` 属性删除（nodes.py:77）

**2. 注释（legacy 旧分段式生成）**
- `generate_excel_plan_node`：纯处理节点中构建后**从未发 LLM**（死构造）→ nodes.py:212-219 死块一并注释
- `analyze_test_points_raw`：节点无连边（dormant）→ retrievers.py 方法整体注释 + graph_builder.py:71 节点注册移除
- definitions.py 变遗留注释容器（保留原文，运行确认后删除）

**3. 连带清理**
- nodes.py 死块：L212-219（prompt + api_summaries/all_apis_json）、L242-250（_sections + prompt_vars）——均只服务从未发出的 prompt
- ⚠️ `test_analysis`（L241）与 `module_tree_json`（L240）**保留**：校验 `ExcelPlanValidator.validate(plan, test_analysis)`（L309）与快照 `module_tree_json`（L269）仍用
- prompts/__init__.py：PromptFactory re-export 移除
- 测试 7 文件：2 个改导函数、import_smoke 断言改 2 新函数、quality_gate/workflow_init 删死 mock（含失效 `import types`）、thinking_log 改 patch 模块函数、phase_a_analysis 注释测废弃方法的 @skip 类

**4. 验证**：7 个受影响测试文件 `116 passed / 1 xfailed`；全量 pytest `682 passed / 9 failed` 与基线一致，零回归
