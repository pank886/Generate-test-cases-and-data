"""新节点评估：thinking+json_mode 一步生成 ×5 次

运行方式: python -m tests.test_new_node_evaluation

统计维度：
  1. 数量        —— 用例数 / 共享前置 / story 数
  2. 接口包含情况 —— 定义接口 → 用例步骤覆盖的接口数（覆盖率、未覆盖接口清单）
  3. 异常用例数量 —— is_negative_test 数量与占比
  4. 接口质量     —— 步骤 URL 命中真实接口的比例（幻觉检测）、断言类型分布
  5. 合理性       —— 步骤/预期对齐、前置引用有效性、空步骤/空预期、story 分布
"""

import json, os, re, sys, time
from datetime import datetime

# ── 准备工作 ──
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import get_session_ctx, init_db
from database.operations import ModuleOps, BindingOps
from agent_components.nodes import ChatTestAgentGraph

init_db()

MODULE_NAME = "智慧用电"
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "tests", "eval_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── 地面真值：模块绑定的接口定义（用于覆盖率 / 幻觉检测）──
DEFINED_APIS = []          # [{url, method, name}]
DEFINED_URLS = []          # url 模板列表（含 {code} 路径参数）
DEFINED_METHOD = {}        # url 模板 → method
DEFINED_NAMES = {}         # url 模板 → name


def load_defined_apis():
    """从数据库读取模块绑定的接口定义作为地面真值。"""
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, MODULE_NAME)
        bound_docs = BindingOps.get_bound_docs(session, MODULE_NAME)
        for d in bound_docs:
            if d.doc_type == "api" and d.api_url:
                url = d.api_url.strip()
                if url not in DEFINED_METHOD:
                    DEFINED_APIS.append({
                        "url": url, "method": (d.api_method or "GET").upper(),
                        "name": d.api_name,
                    })
                    DEFINED_URLS.append(url)
                    DEFINED_METHOD[url] = (d.api_method or "GET").upper()
                    DEFINED_NAMES[url] = d.api_name


load_defined_apis()


# ── URL 模板匹配（{code} 视为任意单段通配；末尾 / 归一化）──
def match_template(url: str, tpl: str) -> bool:
    u = [s for s in url.rstrip("/").split("/") if s]
    t = [s for s in tpl.rstrip("/").split("/") if s]
    if len(u) != len(t):
        return False
    for a, b in zip(u, t):
        if b.startswith("{") and b.endswith("}"):
            continue
        if a != b:
            return False
    return True


def extract_urls(text: str) -> set:
    # 仅匹配 ASCII 路径（排除中文步骤文本），防止 /xxx上传 被当作完整 URL
    return set(re.findall(r"/[A-Za-z][A-Za-z0-9_/{}.-]*", text))


# ── 构建初始 state ──
def make_state():
    """模拟 Phase B 工作流前半段（节点1-4）的输出状态。"""
    with get_session_ctx() as session:
        mod = ModuleOps.get_by_name(session, MODULE_NAME)
        bound_docs = BindingOps.get_bound_docs(session, MODULE_NAME)
        # 在 session 内提取所有数据
        apis = []
        for d in bound_docs:
            if d.doc_type == "api" and d.api_url:
                apis.append({
                    "name": d.api_name, "url": d.api_url, "method": d.api_method,
                    "description": d.api_description or "",
                    "parameters": json.loads(d.api_parameters or "[]"),
                    "returns": json.loads(d.api_returns or "[]"),
                    "headers": json.loads(d.api_headers or "[]"),
                    "source": d.id,
                })

    return {
        "confirmed_module": MODULE_NAME,
        "original_input": f"为{MODULE_NAME}模块生成完整测试计划",
        "api_definitions": apis,
        "product_docs": [],
        "related_modules": [],
    }


def run_and_evaluate(run_id: int):
    """运行一次新节点并评估。"""
    components = ChatTestAgentGraph()
    state = make_state()

    print(f"\n{'='*60}")
    print(f"第 {run_id} 轮")
    print(f"{'='*60}")

    t0 = time.time()
    try:
        result = components._generate_excel_plan_thinking(state)
        dt = time.time() - t0
        plan = result.get("excel_plan")
    except Exception as e:
        dt = time.time() - t0
        print(f"[FAIL] ({dt:.1f}s): {e}")
        return {"run": run_id, "success": False, "error": str(e), "time": dt}

    cases = plan.test_cases
    pres = plan.shared_preconditions

    # ── 1. 数量 ──
    stories = list(dict.fromkeys(tc.story for tc in cases))
    total_steps = sum(len(tc.steps.split("\\n")) for tc in cases)
    total_expected = sum(len(tc.expected.split("\\n")) for tc in cases)

    # ── 3. 异常用例 ──
    positive = sum(1 for tc in cases if not tc.is_negative_test)
    negative = sum(1 for tc in cases if tc.is_negative_test)
    neg_stories = set(tc.story for tc in cases if tc.is_negative_test)

    # ── 4. 接口质量（断言类型 + 幻觉检测）──
    eq_count = sum(tc.expected.count("[eq]") for tc in cases)
    contains_count = sum(tc.expected.count("[contains]") for tc in cases)
    ne_count = sum(tc.expected.count("[ne]") for tc in cases)
    db_count = sum(tc.expected.count("[db]") for tc in cases)

    # 步骤中出现的 URL → 匹配地面真值
    urls_in_steps = set()
    for tc in cases:
        urls_in_steps |= extract_urls(tc.steps)

    covered_urls = set()        # 命中的定义接口模板
    matched_step_urls = set()   # 命中的步骤 URL
    hallucinated = set()        # 步骤中未命中任何定义接口的多段路径（疑似幻觉接口）
    param_refs = set()          # 单段路径（如 /endTime）= 参数名/字段引用，非接口调用
    for u in urls_in_steps:
        hit = None
        for tpl in DEFINED_URLS:
            if match_template(u, tpl):
                covered_urls.add(tpl)
                if hit is None:
                    hit = tpl
        if hit:
            matched_step_urls.add(u)
        elif u.count("/") == 1:
            param_refs.add(u)
        else:
            hallucinated.add(u)

    # ── 2. 接口包含情况（覆盖率 + 未覆盖清单）──
    coverage = len(covered_urls) / len(DEFINED_URLS) * 100 if DEFINED_URLS else 0.0
    missing_urls = [u for u in DEFINED_URLS if u not in covered_urls]

    # ── 5. 合理性 ──
    pre_ids_used = set()
    empty_steps = sum(1 for tc in cases if not tc.steps.strip())
    empty_expected = sum(1 for tc in cases if not tc.expected.strip())
    for tc in cases:
        pre_ids_used.update(tc.preconditions)
    bad_pre_refs = pre_ids_used - set(p.id for p in pres)

    # story 分布（每 story 用例数）
    story_counts = {}
    for tc in cases:
        story_counts[tc.story] = story_counts.get(tc.story, 0) + 1
    story_min = min(story_counts.values()) if story_counts else 0
    story_max = max(story_counts.values()) if story_counts else 0

    result = {
        "run": run_id,
        "success": True,
        "time": dt,
        # 1 数量
        "cases": len(cases),
        "shared_preconditions": len(pres),
        "stories": len(stories),
        "story_list": stories,
        # 3 异常
        "positive": positive,
        "negative": negative,
        "negative_ratio": round(negative / len(cases) * 100, 1) if cases else 0.0,
        "neg_stories": len(neg_stories),
        # 4 接口质量
        "eq_assertions": eq_count,
        "contains_assertions": contains_count,
        "ne_assertions": ne_count,
        "db_assertions": db_count,
        "distinct_urls_in_steps": len(urls_in_steps),
        "matched_step_urls": len(matched_step_urls),
        "hallucinated_urls": len(hallucinated),
        "hallucinated_samples": sorted(hallucinated)[:8],
        "param_refs": sorted(param_refs),
        "api_quality_precision": round(len(matched_step_urls) / len(urls_in_steps) * 100, 1)
                                 if urls_in_steps else 0.0,
        # 2 接口包含
        "defined_apis": len(DEFINED_URLS),
        "covered_apis": len(covered_urls),
        "api_coverage_pct": round(coverage, 1),
        "missing_apis": len(missing_urls),
        "missing_samples": missing_urls[:10],
        # 5 合理性
        "pre_ids_used": len(pre_ids_used),
        "pre_ids_available": len(pres),
        "bad_pre_refs": sorted(bad_pre_refs),
        "empty_steps": empty_steps,
        "empty_expected": empty_expected,
        "story_dist_min": story_min,
        "story_dist_max": story_max,
        "step_expected_align": "OK" if total_steps == total_expected else f"MISMATCH({total_steps}vs{total_expected})",
    }

    # 保存完整输出
    out_file = os.path.join(OUTPUT_DIR, f"run_{run_id:02d}.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "shared_preconditions": [p.model_dump() for p in pres],
            "test_cases": [tc.model_dump() for tc in cases],
        }, f, ensure_ascii=False, indent=2)

    print(f"[OK] ({dt:.1f}s): {len(cases)} cases, {len(pres)} pres, {len(stories)} stories, "
          f"pos={positive}/neg={negative}, 接口覆盖 {len(covered_urls)}/{len(DEFINED_URLS)} "
          f"({coverage:.1f}%), 幻觉URL {len(hallucinated)}")
    if hallucinated:
        print(f"   ⚠ 幻觉URL: {sorted(hallucinated)[:5]}")
    if missing_urls:
        print(f"   ⚠ 未覆盖接口({len(missing_urls)}): {missing_urls[:5]}")
    return result


def print_dim(label, results, keys, fmt=lambda v: v, note=""):
    vals = [r[keys] for r in results]
    print(f"  {label}: min={min(vals)}, max={max(vals)}, avg={sum(vals)/len(vals):.1f}{note}")
    if fmt:
        print(f"     明细: {[fmt(r[keys]) for r in results]}")


if __name__ == "__main__":
    # 清理旧产物（防误判：上次残留 run_XX.json 干扰本次统计）
    for f in os.listdir(OUTPUT_DIR):
        if f.startswith("run_") and f.endswith(".json"):
            os.remove(os.path.join(OUTPUT_DIR, f))

    results = []
    for i in range(1, 6):
        results.append(run_and_evaluate(i))

    # ── 汇总 ──
    print(f"\n{'='*60}")
    print("EVALUATION SUMMARY")
    print(f"{'='*60}")
    success_runs = [r for r in results if r["success"]]
    if not success_runs:
        print("All runs failed")
        sys.exit(1)

    print(f"\n■ 1. 数量")
    print_dim("  用例数", success_runs, "cases")
    print_dim("  共享前置", success_runs, "shared_preconditions")
    print_dim("  story数", success_runs, "stories")
    print(f"    story明细: {[r['story_list'] for r in success_runs]}")

    print(f"\n■ 2. 接口包含情况 (定义接口 {len(DEFINED_URLS)})")
    print_dim("  覆盖接口数", success_runs, "covered_apis")
    print_dim("  覆盖率%", success_runs, "api_coverage_pct")
    print_dim("  未覆盖接口数", success_runs, "missing_apis")
    print(f"    未覆盖示例(首次出现): {success_runs[0]['missing_samples']}")

    print(f"\n■ 3. 异常用例数量")
    print_dim("  负向用例数", success_runs, "negative")
    print_dim("  负向占比%", success_runs, "negative_ratio")
    print_dim("  覆盖负向的story数", success_runs, "neg_stories")

    print(f"\n■ 4. 接口质量")
    print_dim("  步骤URL去重数", success_runs, "distinct_urls_in_steps")
    print_dim("  命中真实接口URL", success_runs, "matched_step_urls")
    print_dim("  幻觉URL数", success_runs, "hallucinated_urls")
    print_dim("  精度%(命中/全部)", success_runs, "api_quality_precision")
    print(f"    幻觉URL示例(首次出现): {success_runs[0]['hallucinated_samples']}")
    print_dim("  [eq]断言", success_runs, "eq_assertions")
    print_dim("  [contains]断言", success_runs, "contains_assertions")
    print_dim("  [ne]断言", success_runs, "ne_assertions")
    print_dim("  [db]断言", success_runs, "db_assertions")

    print(f"\n■ 5. 合理性")
    print(f"    步骤/预期对齐: {[r['step_expected_align'] for r in success_runs]}")
    print_dim("  前置被引用数", success_runs, "pre_ids_used")
    print_dim("  前置可用数", success_runs, "pre_ids_available")
    bad = [r["bad_pre_refs"] for r in success_runs]
    print(f"    无效前置引用(应全为0): {bad}")
    print_dim("  空步骤用例", success_runs, "empty_steps", note="(应全为0)")
    print_dim("  空预期用例", success_runs, "empty_expected", note="(应全为0)")
    print_dim("  story用例数min", success_runs, "story_dist_min")
    print_dim("  story用例数max", success_runs, "story_dist_max")

    times = [r["time"] for r in success_runs]
    print(f"\n  耗时: min={min(times):.1f}s, max={max(times):.1f}s, avg={sum(times)/len(times):.1f}s")
    print(f"\n  稳定性: {len(success_runs)}/5 成功")
    print(f"  输出目录: {OUTPUT_DIR}")
