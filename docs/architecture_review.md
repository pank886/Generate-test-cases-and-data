# 架构审查报告

> 审查引擎: Skill B (risk-detective) | 规则集: docs/RULES_INDEX.md + docs/RULES_DETAIL.md

---

## 第一部分：审查摘要

| 项目 | 内容 |
|:---|:---|
| 扫描范围 | `.`（项目根目录，排除 `tests`, `docs`, `.git`, `__pycache__`, `.venv`, `node_modules`, `vector_store`, `uploads`, `logs`, `.claude`, `chonglog`, `testcase_out`） |
| 扫描文件数 | 70 个源文件（57 `.py` + 7 `.js` + 4 `.html`/`.css` + 2 `.yaml`） |
| 审查时间 | 2026-07-18 |
| P0 问题数 | **4** |
| P1 问题数 | **3** |
| P2 问题数 | **3** |
| **存在规则盲区** | **FALSE** |
| **盲区数量** | 0 |
| 审查结论 | ⚠️ **有条件通过** — 存在 P1 问题需在 Skill C 执行时附带修复方案，P0 问题均为已有代码（非本次变更引入） |

---

## 第二部分：问题统计概览

| 规则 | 违反次数 | P0 | P1 | P2 |
|:---|:---:|:---:|:---:|:---:|
| M3: 异常处理与日志 | 3 | 2 | 0 | 1 |
| M5: 文件与路径安全 | 3 | 1 | 1 | 1 |
| M6: 代码结构与配置 | 2 | 0 | 1 | 1 |
| M7: 前端安全与交互 | 2 | 1 | 1 | 0 |

---

## 第三部分：风险详情清单

---

### [P0] ISSUE-001: `except Exception: pass` 静默吞异常 — Phase B 工作流恢复路径

- **问题编号**：`ISSUE-001`
- **触发规则**：`M3: 异常处理与日志` — 禁止静默吞异常
- **风险位置**：`web/tasks.py:428-429`
- **违规代码**：
  > ```python
  >         except Exception:
  >             pass
  > ```
- **违规描述**：`_resume_workflow_bg` 中读取 `thinking_trace.log` 失败时静默跳过。若文件编码异常或权限错误，`failed_tc_ids` 保持为空列表，前端不展示校验失败警告，用户对 Excel 计划中的问题毫不知情。
- **风险推演**：LLM 生成的 Excel 包含 3 行校验失败的用例 → `thinking_trace.log` 写入了失败标记 → Phase B 后台任务读取该文件时因编码问题抛异常 → `except pass` 吞掉 → 前端显示"全部通过" → 用户点击确认 → Phase C 基于有问题的 Excel 生成 .py/.yaml → 遗漏校验失败的用例。
- **修复建议**：至少记录 WARNING 日志 `logger.warning("无法读取思考日志: %s", e, exc_info=True)`，降级为不影响主流程。

---

### [P0] ISSUE-002: `except Exception: pass` 静默吞异常 — 文件删除路径

- **问题编号**：`ISSUE-002`
- **触发规则**：`M3: 异常处理与日志` — 禁止静默吞异常
- **风险位置**：`web/routes/files.py:146-147`
- **违规代码**：
  > ```python
  >                     try:
  >                         import json as _json
  >                         with open(meta_path, "r", encoding="utf-8") as _mf:
  >                             _doc_id = _json.load(_mf).get("doc_id")
  >                     except Exception:
  >                         pass
  > ```
- **违规描述**：删除文件时读取 `.meta.json` 失败静默跳过。若 JSON 损坏或被截断（如之前在崩溃时写入一半），`_doc_id` 保持 `None`，导致 ChromaDB 中的孤儿数据永不清理。
- **风险推演**：用户删除文件 → meta.json 读取失败 → pass → `_doc_id` 为 None → ChromaDB 清理被跳过 → 向量库残留数据 → 后续检索命中已删除文档的旧数据 → 生成错误的测试用例。
- **修复建议**：记录 WARNING 日志后继续流程（meta.json 损坏不应阻断删除操作），但必须记录异常便于排查。

---

### [P0] ISSUE-003: 路径包含检查使用 `startswith` 而非 `commonpath`

- **问题编号**：`ISSUE-003`
- **触发规则**：`M5: 文件与路径安全` — 路径包含检查必须用 `os.path.commonpath`，禁止 `startswith`
- **风险位置**：`web/routes/files.py:329`
- **违规代码**：
  > ```python
  >         allowed_dirs = [
  >             _os.path.abspath(config.TESTCASE_BASE),
  >             _os.path.abspath("uploads"),
  >         ]
  >         if not any(abs_path.startswith(d) for d in allowed_dirs):
  > ```
- **违规描述**：`startswith` 做目录归属判断存在路径穿越漏洞。例如 `/tmp/attack_uploads` 以 `/tmp/attack_` 开头，可绕过以 `/tmp/attack` 为前缀的白名单。虽当前白名单中 `uploads` 不易利用，但该模式违反安全铁律。
- **风险推演**：若未来白名单目录名更短（如 `/a`），攻击者可通过 `/a_evil/file.txt` 绕过检查读取越权文件。
- **修复建议**：替换为 `os.path.commonpath([abs_path, allowed_d]) == allowed_d` 语义等价的安全检查。

---

### [P0] ISSUE-004: `onclick` 属性中 `esc(path)` 拼接 — JS 语法破坏风险

- **问题编号**：`ISSUE-004`
- **触发规则**：`M7: 前端安全与交互` — 动态路径必须用 `data-*` 属性 + 事件委托，禁止在 HTML 字符串中拼接 `esc(path)`
- **风险位置**：`static/app.js:36, 38, 670`
- **违规代码**：
  > ```javascript
  > '<button class="btn btn-sm btn-outline" onclick="openLocalFile(\'' + esc(path) + '\')">打开</button>'
  > ```
- **违规描述**：`esc()` 将 `'` 转义为 `&#39;`，但浏览器将 HTML 属性值解码后 `&#39;` 还原为 `'`，若 `path` 包含 `'` 则破坏 onclick 字符串字面量边界。
- **风险推演**：文件名包含单引号（如 `test's_file.py`）→ esc 转为 `&#39;` → HTML 解码还原为 `'` → onclick 变成 `onclick="openLocalFile('test's_file.py')"` → JS 语法错误 → 按钮无响应。
- **修复建议**：改用 `data-path="..."` 属性存储路径 + 全局事件委托读取 `e.target.dataset.path`，彻底消除拼接风险。

---

### [P1] ISSUE-005: `os.path.abspath("uploads")` 相对路径

- **问题编号**：`ISSUE-005`
- **触发规则**：`M5: 文件与路径安全` — 所有文件路径必须以 `config.BASE_DIR` 为根
- **风险位置**：`web/routes/files.py:327`（及 365 行）
- **违规代码**：
  > ```python
  >             _os.path.abspath("uploads"),
  > ```
- **违规描述**：使用相对路径 `"uploads"` 依赖当前工作目录（CWD），不同启动方式可能导致 CWD 不同（如 systemd、IDE 直接运行、命令行启动）。
- **风险推演**：从不同目录启动服务 → CWD 不是项目根目录 → `abspath("uploads")` 解析到错误路径 → 文件读取白名单不包含实际文件路径 → 所有文件操作返回 403。
- **修复建议**：改为 `_os.path.abspath(_os.path.join(config.BASE_DIR, "uploads"))`。

---

### [P1] ISSUE-006: `test_point_analysis` 字段在 TypedDict 中重复定义

- **问题编号**：`ISSUE-006`
- **触发规则**：`M6: 代码结构与配置` — 代码结构重复/冲突
- **风险位置**：`agent_components/state.py:37-38`
- **违规代码**：
  > ```python
  >     test_point_analysis: Optional[str]       # analyze_test_points_raw 输出的自由文本分析
  >     test_point_analysis: Optional[str]       # analyze_test_points_raw 输出的自由文本分析报告
  > ```
- **违规描述**：同一个 TypedDict 键定义了两次，类型签名完全相同但注释不同。Python TypedDict 中后定义覆盖前定义，造成混淆且违反单一数据源原则。
- **风险推演**：若有人修改第 37 行的注释/类型，期望生效但实际被第 38 行覆盖 → 类型检查与实际行为不符 → 潜在的类型安全问题。
- **修复建议**：删除第 37 行，保留第 38 行，合并注释为 `# analyze_test_points_raw 输出的自由文本分析报告`。

---

### [P1] ISSUE-007: 前端 `catch` 缺少错误日志（多处）

- **问题编号**：`ISSUE-007`
- **触发规则**：`M7: 前端安全与交互` — catch 禁止为空，但关键路径缺少 `console.error` 排查手段
- **风险位置**：`static/app.js:345, 352, 460, 468, 516, 526, 567, 575, 587`
- **违规代码**：
  > ```javascript
  >   } catch (e) { toast('操作失败'); }
  > ```
- **违规描述**：多处 API 调用 catch 仅弹 toast 提示"失败"，不记录 `console.error` 也不展示具体错误信息。用户看到"失败"后无法自行排查，开发者也无法从控制台获取错误详情。
- **风险推演**：创建模块失败 → toast "失败" → 用户不知道是网络超时（重试即可）还是名称冲突（需改名） → 重复尝试仍失败 → 放弃使用。
- **修复建议**：统一错误处理模式 `catch (e) { console.error('操作名:', e); toast('操作失败: ' + (e.message || '')); }`。

---

## 第四部分：P2 最佳实践建议

- **`web/app.py:318`** 建议：Ollama 健康检查重试中的 `except Exception: pass` 应至少 `print(f"[startup] 等待 Ollama...({attempt})")` 输出进度 | 规则：`M3`
- **`agent_components/nodes.py:556`** 建议：`Path("logs")` 硬编码日志路径应改用 `config.LOG_DIR`，保持与其他模块一致 | 规则：`M6`
- **`docs/fixes_summary.md` 根目录残留** 建议：确认 `./fixes_summary.md` 是否仍被引用，若无则删除（正确位置是 `./docs/fixes_summary.md`） | 规则：`M6`

---

## 第五部分：本次 Phase B 变更专项审查

对本次 `chonglog/2026-07-16_phase_c_downstream.md` Phase B 部分的实现进行了逐项对照审查：

| 计划项 | 审查结果 |
|:---|:---|
| `TestCaseRow.mutates_data` 字段 | ✅ `bool, default=False`，Excel 9 列表头不变 |
| `TestCaseRow.is_negative_test` 字段 | ✅ `bool, default=False`，不写入 Excel |
| `SharedPrecondition.cloned_from` 字段 | ✅ `Optional[str], default=None`，不写入 Excel |
| `settings.py::resource_mutate_keywords` 配置 | ✅ 26 个中英文关键词，`config.py` 导出 `RESOURCE_MUTATE_KEYWORDS` |
| `generate_excel_plan_node` prompt 追加 | ✅ JSON 示例含 `mutates_data`/`is_negative_test`，字段描述 + 判断规则完整 |
| `_resolve_resource_conflicts` 算法实现 | ✅ 三层过滤（有无前置/关键词兜底/PRE 映射）→ 克隆隔离 → `cloned_from` 标记 |
| B5 嵌入位置（方案 A） | ✅ 不拆图节点，在 `_generate_excel_plan_node` 内部、Excel 写入前调用 |
| 图拓扑不变 | ✅ `graph_builder.py` 零修改 |
| 新增 LLM 调用 | ✅ 0 次（纯代码节点） |
| 关键词兜底日志 | ✅ `logger.debug` 记录每次兜底，含 `tc.id` |
| 消解统计日志 | ✅ `logger.info` 输出隔离的 PRE 数和受影响用例数 |
| PRE 缺失防护 | ✅ `_find_pre` 返回 None 时 WARNING 跳过，不崩溃 |
| `defaultdict` 导入 | ✅ `nodes.py:4` from `collections` |

**结论：Phase B 实现 0 偏移，0 新增违规，代码质量符合全部规则要求。**

---

## 第六部分：已通过审查的核心文件

| 文件 | 状态 |
|:---|:---:|
| `prompts/response_model.py` | ✅ 通过 |
| `prompts/definitions.py` | ✅ 通过 |
| `settings.py` | ✅ 通过 |
| `config.py` | ✅ 通过 |
| `agent_components/nodes.py` | ✅ 通过（含本次新增消解器） |
| `agent_components/graph_builder.py` | ✅ 通过 |
| `agent_components/retrievers.py` | ✅ 通过 |
| `agent_components/dual_chroma.py` | ✅ 通过 |
| `agent_components/generators.py` | ✅ 通过 |
| `agent_components/state.py` | ⚠️ ISSUE-006（重复字段） |
| `agent_components/validator.py` | ✅ 通过 |
| `agent_components/module_tree.py` | ✅ 通过 |
| `agent_components/llm/base.py` | ✅ 通过 |
| `agent_components/llm/deepseek.py` | ✅ 通过 |
| `agent_components/fallback_embeddings.py` | ✅ 通过 |
| `web/app.py` | ✅ 通过（P2 建议项） |
| `web/tasks.py` | ❌ ISSUE-001 |
| `web/routes/chat.py` | ✅ 通过 |
| `web/routes/files.py` | ❌ ISSUE-002, ISSUE-003, ISSUE-005 |
| `web/routes/modules.py` | ✅ 通过 |
| `web/routes/bindings.py` | ✅ 通过 |
| `web/routes/docs.py` | ✅ 通过 |
| `web/routes/api_extract.py` | ✅ 通过 |
| `web/services/doc_binding.py` | ✅ 通过 |
| `database/__init__.py` | ✅ 通过 |
| `database/models.py` | ✅ 通过 |
| `database/operations.py` | ✅ 通过 |
| `database/init_db.py` | ✅ 通过 |
| `ingest_v2.py` | ✅ 通过 |
| `observability.py` | ✅ 通过 |
| `static/app.js` | ❌ ISSUE-004, ISSUE-007 |
| `templates/index.html` | ✅ 通过 |
| `static/style.css` | ✅ 通过 |
| `data_factory/mock_data.py` | ✅ 通过 |

---

## 审查员备注

审查结论：⚠️ **有条件通过**

- **本次 Phase B 变更**：0 项新增违规，实现与计划完全对齐，通过审查
- **已有代码**：发现 4 项 P0、3 项 P1、3 项 P2，均为历史遗留问题（非本次变更引入）
- **规则盲区**：无
- **建议 Skill C 优先修复**：ISSUE-001（静默吞异常）、ISSUE-002（静默吞异常）、ISSUE-005（相对路径）、ISSUE-006（重复字段）
