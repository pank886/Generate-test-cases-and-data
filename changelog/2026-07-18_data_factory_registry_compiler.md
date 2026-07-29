# 变更计划：数据工厂注册表编译器（B+A：半自动编译 + 哨兵防漂移）

| 项目 | 内容 |
|:---|:---|
| 变更日期 | 2026-07-18 |
| 变更类型 | 新增维护工具链（不改生成主链路） |
| 涉及文件 | `data_factory/compile.py`（新增）, `data_factory/registry.py`, `settings.py`, `config.py`, `chongelog/YAML_SPECIFICATION.md`, `tests/test_registry_sentinel.py`（新增） |
| 前置依赖 | 2026-07-18 质量治理计划落地（methods.yaml v2 分类结构 + `data_factory/registry.py`） |
| 状态 | ⏸ 待主计划实施完成后开工 |

## 0. 用户决策记录（2026-07-18）

1. 方案：**B（半自动编译器）+ A（哨兵测试）**组合
2. `YAML_SPECIFICATION.md` §5.2 函数章节改由注册表渲染：**要做** —— methods.yaml 成为唯一手写点
3. 触发方式：**手动命令**（框架方法更新低频，不做 git hook / 定时器 / 启动检测）
4. 铁则：**LLM 只产草稿，methods.yaml 永不被自动写入**（注册表是校验器的质量闸门，闸门必须人工把关）

## 1. 维护流程（目标态）

```
测试框架更新 common/debugtalk.py
  → python -m data_factory.compile
      ① AST 解析（确定性）：函数名/签名/默认值/docstring → min_args/max_args
      ② 与 methods.yaml diff：新增 / 签名变更 / 已删除
      ③ 仅对新增与变更项调 LLM：源码+docstring+现有大类清单 → 草稿条目
         （description / usage_tips / 归类建议 / arg0_enum 建议；
          min_args/max_args 以 AST 结果强制覆盖 LLM 输出）
      ④ 输出 data_factory/methods.draft.yaml + 控制台 diff 摘要
  → 人工审核草稿 → 手动合并进 methods.yaml
  → python -m data_factory.compile --render-spec
      从 methods.yaml 重新渲染 YAML_SPECIFICATION.md §5.2 + 附录A 对应区块
  → pytest tests/test_registry_sentinel.py   （绿 = 源码/注册表/规范文档三处一致）
```

## 2. 组件设计

### 2.1 配置（`settings.py` / `config.py`）

```python
# 测试框架 DebugTalk 源码路径（可选）。为空时：编译器报错提示、哨兵测试 skip
debugtalk_source_path: str = ""     # .env: DEBUGTALK_SOURCE_PATH
```

### 2.2 读取器（`data_factory/compile.py`，纯 AST 无 LLM）

- `extract_functions(path) -> list[dict]`：`ast.parse` 提取公开函数（排除 `_` 前缀）的
  name / 参数列表 / 默认值个数 / docstring → 推导 `min_args = 必填参数数`、`max_args = 全参数数`
- `diff_registry(ast_funcs, registry) -> {added, changed, removed}`：changed 判定 = arity 不一致

### 2.3 LLM 编译（仅草稿）

- 新增 prompt `compile_factory_method_prompt`：输入函数源码 + docstring + methods.yaml 条目 schema
  + 现有大类清单（含 description），输出单条目 JSON（Pydantic 校验）
- 结构字段（min/max_args）以 AST 覆盖；`arg0_enum` 为 LLM 建议，草稿中标注 `# TODO 人工确认`
- 归类：LLM 从现有大类中选或建议新大类，草稿中同样标注

### 2.4 草稿输出

- 写 `data_factory/methods.draft.yaml`（结构与 methods.yaml 一致，仅含新增/变更条目）
- 控制台打印三段摘要：`新增 N（清单）/ 变更 M（arity 对照）/ 框架已删除 K（提示从注册表移除）`
- `removed` 只提示不生成删除操作 —— 删除动作也归人工

### 2.5 规范文档渲染（决策 2）

- `python -m data_factory.compile --render-spec`
- `YAML_SPECIFICATION.md` §5.2 函数小节与附录 A 函数速查表用标记包裹：
  `<!-- AUTO-GEN:placeholder-funcs BEGIN -->` … `<!-- AUTO-GEN:placeholder-funcs END -->`
  渲染时只替换标记区内内容，区外手写内容不动
- 渲染内容来源 = methods.yaml 的 syntax/description/params/usage_tips（与 prompt 渲染同源）
- 幂等：同一 methods.yaml 渲染两次无 diff

### 2.6 哨兵测试（`tests/test_registry_sentinel.py`）

| 用例 | 断言 |
|:---|:---|
| 未配置 DEBUGTALK_SOURCE_PATH | 全部 skip（不影响 CI） |
| 函数集一致性 | AST 函数集 == methods.yaml 函数集（多/少均红） |
| arity 一致性 | 每个函数 AST 推导的 min/max_args == validation 块 |
| 规范文档一致性 | §5.2 AUTO-GEN 区块内函数名集合 == methods.yaml（防止忘跑 --render-spec） |
| draft 残留提醒 | methods.draft.yaml 存在 → 警告（提示有未合并草稿） |

## 3. 验收标准

1. 未配置路径：`compile` 报错并给出配置指引；哨兵 skip；对生成主链路零影响
2. 用 fixture 伪造 debugtalk.py：新增函数 → 草稿含该条目且 min/max_args 正确；
   改签名 → changed 检出；删函数 → 哨兵红
3. `--render-spec` 幂等，AUTO-GEN 区外内容不被触碰
4. 全流程无任何"LLM 直接写 methods.yaml / 规范文档正文"的路径

## 4. 明确不做的事

- 不做 git hook / 文件监听 / 定时触发（决策 3：手动够用）
- 不做 `--apply` 自动合并草稿（决策 4：闸门人工把关）
- 不在本工具内维护 DebugTalk 函数实现本身（那是测试框架仓库的事）
