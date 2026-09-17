"""A/B 对比：generate_yaml_data_single_prompt 现有版 vs 新版，各完整生成一轮到独立目录。

用法:
  python logs/prompt_ab_run.py old   # 用当前磁盘 prompt
  python logs/prompt_ab_run.py new   # 用用户提供的新版 prompt（本文件内嵌）

输出:
  logs/prompt_ab/<tag>/            ← 独立输出目录（含 test_plan.xlsx 副本）
  logs/prompt_ab/<tag>/_summary.json
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import json
import shutil
import time

EXCEL = r"C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_36/test_plan.xlsx"
USER_CTX = "智慧用电（电表管理）模块：电表新增、查询、删除接口测试数据生成。"
SCRATCH = os.path.join(ROOT, "logs", "prompt_ab")


def _new_prompt():
    """用户提供的新版 prompt（原文，MSG1041）。"""
    from langchain_core.prompts import ChatPromptTemplate
    system_template = """
# 角色定义
你是数据格式化专家，输出严格遵循 Pydantic 校验的 JSON。

# 输出格式（唯一来源）
{json_schema}

# 动态数据占位符（仅限以下工厂方法）
{data_factory_methods}

# 铁律（共 12 条）
## 结构层
1. 顶层只有 `data` 数组，每项含 `baseInfo` 和 `testCase`
2. 请求体三选一（json/params/data）：按 location 选（query→params，body→json）；无 location 时按方法（GET/DELETE→params，POST/PUT/PATCH→json）
3. url：仅路径部分，禁域名/query/占位符表达式，路径参数不替换具体值
4. header：JSON 请求体写 `Content-Type: application/json`，其余为空对象

## 断言层
5. validation 非空，每步至少一条断言；运算符仅限 `eq/contains/ne/db`
6. 断言格式：单键块 `{{"运算符": 值}}`，禁多键写法
7. status_code 仅用 `contains` 断言，值为字典对象 `{{"字段名": 期望值}}`
8. JSONPath 一律以 `$.` 开头
9. 期望值必须取自接口返回定义，正向用成功值、反向用失败值；未明确时退化用 contains/status_code，禁臆造

## 数据层
10. 唯一键（设备名/编码等）必须动态生成（${{}} 工厂方法/时间戳/随机后缀），禁固定假值
11. DELETE 请求参数严格按接口定义，禁多填
12. extract/input_extract 用不到就整字段省略，禁空对象或 null

# 输出格式
只输出纯净 JSON，禁 Markdown 代码块。
"""
    human_template = """
# B 用例内容（执行步骤 + 预期结果）
{test_case_logic}

# A 数据分析（生成前思考引导）
{data_analysis}

# 接口详情文档（请求/返回语义，取值唯一来源）
{api_definitions}

# 用户意图
{user_context}

# 数据库表结构（为空时禁 db 断言）
{db_schema}

---
请按 system 段的 schema 输出 TestData JSON。值优先级：value > desc > 用例值（异常除外），禁示例或占位数据。
"""
    return ChatPromptTemplate.from_messages([
        ("system", system_template),
        ("human", human_template),
    ])


def _v3_prompt():
    """用户提供的 v3 版 prompt：新版分节结构 + 找回的完整规则措辞（含运算符白名单）。"""
    from langchain_core.prompts import ChatPromptTemplate
    system_template = """
# 角色
你是数据格式化专家。输出严格遵循下方 yaml 格式 schema 的 TestData JSON（Pydantic 校验，字段与类型一个都不能错）。
本 prompt 不含任何具体业务示例，禁止编造与输入无关的固定数据。

# 可用数据工厂方法（动态占位符只能从此清单选择，严格按 syntax 填写）
{data_factory_methods}

# yaml 格式 schema（输出结构唯一来源，禁止自创结构或推断字段）
{json_schema}

# 铁律（共 12 条，内容唯一来源 = 上方 schema + human 段的 B/A/接口文档）
1. 顶层只能有一个 data 数组，每元素含 baseInfo 与 testCase 两个键
2. 请求体三选一（json/params/data 只出现一个）：优先按接口定义 body 字段的 `location` 选——
   `location=query` 的字段 → params，`location=body` 的字段 → json；接口定义未标 location 时
   按 HTTP 方法——GET/DELETE 用 params，POST/PUT/PATCH 用 json
3. url 与接口定义中的路径完全一致：只写路径、禁 query/域名/占位符表达式；路径参数不得替换成具体值
4. 每个 baseInfo 必有 header 键：JSON 请求体方法写 Content-Type=application/json，其余写空对象
5. validation 数组不得为空，每步至少一条断言；运算符只允许 eq/contains/ne/db；
   每条断言必须且只能是一个单键块，即一个运算符键对应一个断言对象，
   禁止 check/expected/operator、jsonpath/operator/value 等多键写法
6. status_code 只能被 contains 断言，且 contains 的值必须是字典对象（键=字段名、值=期望值）；禁止用 eq/ne 断言 status_code
7. JSONPath 一律以 $. 开头
8. extract/input_extract 用不到就整字段省略，禁止输出空对象或 null
9. 成功/失败断言的期望值取自接口返回定义：断言的字段与期望值必须取自「接口详情文档」返回定义中真实给出的字段/取值/语义；
   正向用例断言业务成功对应的返回取值，反向用例断言失败返回取值；返回定义未给出明确成功/失败取值时，退化为
   contains 字段存在性或 status_code 断言，禁止臆造返回定义之外的固定取值
10. 数据唯一化：设备名/编码等唯一键必须动态生成（${{ }} 工厂方法或时间戳/随机后缀），禁止输出固定假值——防跨套件重名与套件内自碰撞
11. delete 参数按接口定义：delete 请求参数严格按接口定义填写，禁止输出接口定义之外的任何字段
12. 禁止 Markdown；只输出纯净 JSON（Pydantic 校验，字段/类型一个都不能错）
"""
    human_template = """
# B 用例内容（本用例执行步骤 + 预期结果）
{test_case_logic}

# A 数据分析（生成前思考要点引导）
{data_analysis}

# 接口详情文档（路径/请求字段/返回语义，取值唯一来源）
{api_definitions}

# 用户意图
{user_context}

# 数据库表结构信息（为空时禁止 db 断言）
{db_schema}

请严格按照 system 段的 yaml 格式 schema 输出 TestData JSON：字段名与结构以 schema 为准，
字段值按 value > desc > 用例值 的优先级决定（异常用例除外），禁止使用示例或占位数据。
"""
    return ChatPromptTemplate.from_messages([
        ("system", system_template),
        ("human", human_template),
    ])


def _preflight(tag, prompt_fn):
    """渲染 pre-flight：各版都须能 format_messages 且含关键要素。prompt_fn=None 用磁盘现有版。"""
    import prompts.extraction_prompts as ep
    if prompt_fn is not None:
        ep.generate_yaml_data_single_prompt = prompt_fn
    fmt = ep.generate_yaml_data_single_prompt()
    dummy = dict(
        data_factory_methods="<FACTORY>",
        api_definitions="<API_DEF>",
        data_analysis="<ANALYSIS>",
        test_case_logic="<CASE_LOGIC>",
        user_context="<USER_CTX>",
        db_schema="<DB_SCHEMA>",
        json_schema='{"title": "TestData"}',
    )
    msgs = fmt.format_messages(**dummy)
    sys_txt = msgs[0].content
    hum_txt = msgs[1].content
    checks = {
        "json_schema_in_system": '{"title": "TestData"}' in sys_txt,
        "precedence_in_human": "value > desc" in hum_txt,
        "no_placeholder_leak": all("{json_schema}" not in m.content and "{test_case_logic}" not in m.content for m in msgs),
    }
    print(f"[{tag}] preflight checks: {checks}", flush=True)
    if not all(checks.values()):
        raise SystemExit(f"[{tag}] preflight FAILED")
    return msgs


def run(tag, prompt_fn):
    from web.tasks import _resolve_api_defs
    api_defs_json = _resolve_api_defs(EXCEL, "", "")
    if not api_defs_json:
        raise SystemExit("FATAL: api_defs resolve failed")

    out = os.path.join(SCRATCH, tag)
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    excel_copy = os.path.join(out, "test_plan.xlsx")
    shutil.copy2(EXCEL, excel_copy)

    if prompt_fn is not None:
        import prompts.extraction_prompts as ep
        ep.generate_yaml_data_single_prompt = prompt_fn

    from agent_components.graph.nodes import reload_llm, ChatTestAgentGraph
    reload_llm()
    agent = ChatTestAgentGraph()

    t0 = time.time()
    result = agent._generate_all_yamls(excel_copy, api_defs_json, USER_CTX)
    elapsed = time.time() - t0
    summary = {"tag": tag, "elapsed_s": round(elapsed, 1), "result": result,
               "output_dir": out}
    with open(os.path.join(out, "_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "old"
    if which == "old":
        tag, prompt_fn = "OLD", None
    elif which == "old2":
        tag, prompt_fn = "OLD2", None
    elif which == "new":
        tag, prompt_fn = "NEW", _new_prompt
    elif which == "v3":
        tag, prompt_fn = "V3", _v3_prompt
    elif which == "v4":
        # 存量前置修复后（PRE steps 去掉 meterDeviceType=1 字面量）复验：
        # 用磁盘 prompt（= 已替换的 v3 版），对已修复的 test_plan.xlsx 生成一轮。
        tag, prompt_fn = "V4", None
    else:
        raise SystemExit("unknown variant: " + which)
    print(f"=== prompt A/B: {tag} (prompt_fn={prompt_fn is not None}) ===", flush=True)
    _preflight(tag, prompt_fn)
    run(tag, prompt_fn)
    print(f"=== {tag} DONE ===", flush=True)


if __name__ == "__main__":
    main()
