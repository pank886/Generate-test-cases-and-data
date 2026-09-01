# -*- coding: utf-8 -*-
"""重新生成 37 到 智慧用电_37_regenerated。

验证 O1/O2/O3/O8 四条 prompt 铁律 + O2 校验器 + D5 数据维护的实际效果。
流程：_resolve_api_defs（SQL 模块作用域）→ _generate_py_file → _generate_all_yamls
"""
import io
import os
import sys
import json
import logging

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 脚本已迁移至 tests/tools/，根目录不在默认 sys.path：显式加入项目根
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import infrastructure.config as config  # noqa: E402  (loads settings + .env)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(name)s %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])

from agent_components.graph.nodes import ChatTestAgentGraph  # noqa: E402
from web.tasks import _resolve_api_defs  # noqa: E402

BASE = r'C:/Users/damai/PycharmMiscProject/testcase/园区基线/智慧用电_37_regenerated'
excel_path = os.path.join(BASE, 'test_plan.xlsx')

print(f'=== DeepSeek ready: {config.DEEPSEEK_READY}, model={config.LLM_MODEL}')

api_defs_json = _resolve_api_defs(excel_path, '', '')
if not api_defs_json:
    raise SystemExit('M8: SQL 无接口定义，阻断重生成')
print(f'=== api_defs_json: {len(api_defs_json)} chars')

user_ctx = (
    '智慧用电-电表管理：添加电表（单一费率/分时/绑定计费方案/网关接入协议必选 正向；'
    '编号重复/必填字段缺失/电表分类枚举非法/初始读数负数/初始读数超精度/收费未选方案/'
    '网关接入未选协议/名称超长/编号含SQL注入 负向）；获取电表列表（分页筛选 正向；'
    '非法页码/非法排序值 负向）；获取上级电表列表（正向）；删除电表（正向/已绑定计费方案/不存在编码 负向）。'
    '重点覆盖添加与删除接口的增删校验与数据准备，查询接口作为数据验证辅助。'
)

agent = ChatTestAgentGraph()

print('=== [1/2] _generate_py_file ...')
py_result = agent._generate_py_file(excel_path)
print('=== py_result:', json.dumps(py_result, ensure_ascii=False, indent=2))

print('=== [2/2] _generate_all_yamls ...')
yaml_result = agent._generate_all_yamls(excel_path, api_defs_json, user_ctx)
print('=== yaml_result:', json.dumps(yaml_result, ensure_ascii=False, indent=2))
print('=== DONE')
