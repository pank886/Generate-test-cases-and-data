"""一次性脚本：用新增 random_code 工厂方法重新生成 智慧用电_36（覆盖写）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import time

EXCEL = r"C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_36/test_plan.xlsx"
USER_CTX = "智慧用电（电表管理）模块：电表新增、查询、删除接口测试数据生成。"


def main():
    from web.tasks import _resolve_api_defs
    api_defs_json = _resolve_api_defs(EXCEL, "", "")
    if not api_defs_json:
        print("FATAL: api_defs resolve failed", file=sys.stderr)
        return 1

    from agent_components.graph.nodes import reload_llm, ChatTestAgentGraph
    reload_llm()
    agent = ChatTestAgentGraph()

    t0 = time.time()
    result = agent._generate_all_yamls(EXCEL, api_defs_json, USER_CTX)
    print("ELAPSED %.1f s" % (time.time() - t0))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
