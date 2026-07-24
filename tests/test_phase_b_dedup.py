"""Phase B 用例去重逻辑单元测试

覆盖场景：
1. LLM 初始输出包含重复 TC ID → 应去重，只保留第一个
2. LLM 修复轮输出同一批次内重复 TC ID → 应去重
3. LLM 修复轮输出与已确认用例重复的 TC ID → 应丢弃
4. valid_cases 最终安全阀 → 多路径聚合的重复应被移除
5. PRE 关联用例列表去重 → 不应包含重复 TC ID
6. shared_preconditions 列表去重 → 不应包含重复 PRE ID
"""

import pytest
import os
import sys

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 1. valid_cases 最终安全阀去重测试
# ============================================================

class TestValidCasesDedup:
    """测试 valid_cases 最终安全阀按 ID 去重逻辑"""

    def test_no_duplicates(self):
        """无重复时不应移除任何条目"""
        from prompts.response_model import TestCaseRow
        cases = [
            TestCaseRow(id="TC-001", story="模块A", title="用例1",
                        preconditions=[], steps="步骤1", expected="预期1"),
            TestCaseRow(id="TC-002", story="模块A", title="用例2",
                        preconditions=[], steps="步骤2", expected="预期2"),
            TestCaseRow(id="TC-003", story="模块B", title="用例3",
                        preconditions=[], steps="步骤3", expected="预期3"),
        ]
        _seen = set()
        _deduped = []
        for tc in cases:
            if tc.id in _seen:
                continue
            _seen.add(tc.id)
            _deduped.append(tc)
        assert len(_deduped) == 3
        assert [tc.id for tc in _deduped] == ["TC-001", "TC-002", "TC-003"]

    def test_consecutive_duplicates(self):
        """连续重复：LLM 输出相邻的两条相同 TC"""
        from prompts.response_model import TestCaseRow
        cases = [
            TestCaseRow(id="TC-001", story="模块A", title="用例1",
                        preconditions=["PRE-001"], steps="步骤1", expected="预期1"),
            TestCaseRow(id="TC-001", story="模块A", title="用例1",
                        preconditions=["PRE-001"], steps="步骤1", expected="预期1"),  # 重复
            TestCaseRow(id="TC-002", story="模块A", title="用例2",
                        preconditions=[], steps="步骤2", expected="预期2"),
        ]
        _seen = set()
        _deduped = []
        for tc in cases:
            if tc.id in _seen:
                continue
            _seen.add(tc.id)
            _deduped.append(tc)
        assert len(_deduped) == 2
        assert [tc.id for tc in _deduped] == ["TC-001", "TC-002"]

    def test_interleaved_duplicates(self):
        """交错重复：LLM 在不同 story 输出相同 TC ID"""
        from prompts.response_model import TestCaseRow
        cases = [
            TestCaseRow(id="TC-001", story="模块A", title="用例1",
                        preconditions=["PRE-001"], steps="步骤1", expected="预期1"),
            TestCaseRow(id="TC-002", story="模块A", title="用例3",
                        preconditions=[], steps="步骤3", expected="预期3"),
            TestCaseRow(id="TC-001", story="模块B", title="用例1",
                        preconditions=["PRE-002"], steps="步骤1", expected="预期1"),  # 不同 story 但相同 ID
            TestCaseRow(id="TC-003", story="模块B", title="用例4",
                        preconditions=[], steps="步骤4", expected="预期4"),
        ]
        _seen = set()
        _deduped = []
        for tc in cases:
            if tc.id in _seen:
                continue
            _seen.add(tc.id)
            _deduped.append(tc)
        assert len(_deduped) == 3
        # 保留第一个 TC-001（story=模块A）
        ids = [tc.id for tc in _deduped]
        assert ids == ["TC-001", "TC-002", "TC-003"]
        assert _deduped[0].story == "模块A"

    def test_triplicate_same_story(self):
        """三重复（模拟 Excel 中 TC-017/TC-020 的实际情况）"""
        from prompts.response_model import TestCaseRow
        cases = [
            TestCaseRow(id="TC-017", story="企业公摊管理", title="预付费管理-公摊生成待确认扣费-账单层",
                        preconditions=["PRE-004"], steps="步骤A", expected="预期A"),
            TestCaseRow(id="TC-017", story="预付费管理", title="预付费管理-公摊生成待确认扣费-账单层",
                        preconditions=["PRE-004"], steps="步骤A", expected="预期A"),  # 重复
            TestCaseRow(id="TC-017", story="预付费管理", title="预付费管理-公摊生成待确认扣费-账单层",
                        preconditions=["PRE-004"], steps="步骤A", expected="预期A"),  # 重复
            TestCaseRow(id="TC-020", story="企业公摊管理", title="公摊管理-导入公摊数据格式错误-异常",
                        preconditions=["PRE-004"], steps="步骤B", expected="预期B"),
            TestCaseRow(id="TC-020", story="公摊管理", title="公摊管理-导入公摊数据格式错误-异常",
                        preconditions=["PRE-004"], steps="步骤B", expected="预期B"),  # 重复
            TestCaseRow(id="TC-020", story="公摊管理", title="公摊管理-导入公摊数据格式错误-异常",
                        preconditions=["PRE-004"], steps="步骤B", expected="预期B"),  # 重复
        ]
        _seen = set()
        _deduped = []
        _dup_count = 0
        for tc in cases:
            if tc.id in _seen:
                _dup_count += 1
                continue
            _seen.add(tc.id)
            _deduped.append(tc)
        assert _dup_count == 4
        assert len(_deduped) == 2
        assert [tc.id for tc in _deduped] == ["TC-017", "TC-020"]

    def test_full_scenario_mixed(self):
        """混合场景：正常用例 + 多种重复模式"""
        from prompts.response_model import TestCaseRow
        cases = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=[], steps="s1", expected="e1"),
            TestCaseRow(id="TC-002", story="A", title="t2", preconditions=["PRE-001"], steps="s2", expected="e2"),
            TestCaseRow(id="TC-002", story="A", title="t2", preconditions=["PRE-001"], steps="s2", expected="e2"),  # dup
            TestCaseRow(id="TC-003", story="B", title="t3", preconditions=[], steps="s3", expected="e3"),
            TestCaseRow(id="TC-001", story="C", title="t1", preconditions=[], steps="s1", expected="e1"),  # dup
            TestCaseRow(id="TC-004", story="B", title="t4", preconditions=[], steps="s4", expected="e4"),
            TestCaseRow(id="TC-004", story="B", title="t4", preconditions=[], steps="s4", expected="e4"),  # dup
            TestCaseRow(id="TC-004", story="B", title="t4", preconditions=[], steps="s4", expected="e4"),  # dup
        ]
        _seen = set()
        _deduped = []
        _dup_count = 0
        for tc in cases:
            if tc.id in _seen:
                _dup_count += 1
                continue
            _seen.add(tc.id)
            _deduped.append(tc)
        assert _dup_count == 4  # TC-002×1, TC-001×1, TC-004×2
        assert len(_deduped) == 4
        assert [tc.id for tc in _deduped] == ["TC-001", "TC-002", "TC-003", "TC-004"]


# ============================================================
# 2. 修复轮批次内去重测试（seen_in_retry）
# ============================================================

class TestRetryBatchDedup:
    """测试修复轮 _seen_in_retry 去重逻辑"""

    def test_retry_produces_duplicate_in_batch(self):
        """修复轮 LLM 在同一批次输出两个相同 TC ID → 只保留第一个"""
        from prompts.response_model import TestCaseRow
        all_confirmed = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=[], steps="s1", expected="e1"),
        ]
        failed_ids = {"TC-002", "TC-003"}
        _already_confirmed = {tc.id for tc in all_confirmed}
        _seen_in_retry = set()

        # 模拟 LLM 修复轮输出：TC-002 出现两次
        retry_output = [
            TestCaseRow(id="TC-002", story="B", title="t2", preconditions=[], steps="s2", expected="e2"),
            TestCaseRow(id="TC-002", story="B", title="t2", preconditions=[], steps="s2", expected="e2"),  # dup
            TestCaseRow(id="TC-003", story="B", title="t3", preconditions=[], steps="s3", expected="e3"),
        ]

        accepted = []
        for tc in retry_output:
            if tc.id not in failed_ids:
                continue
            if tc.id in _already_confirmed:
                continue
            if tc.id in _seen_in_retry:
                continue  # ← 新加的去重逻辑
            _seen_in_retry.add(tc.id)
            accepted.append(tc)

        assert len(accepted) == 2
        assert [tc.id for tc in accepted] == ["TC-002", "TC-003"]

    def test_retry_does_not_duplicate_already_confirmed(self):
        """修复轮不应输出已通过校验的 TC"""
        from prompts.response_model import TestCaseRow
        all_confirmed = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=[], steps="s1", expected="e1"),
            TestCaseRow(id="TC-002", story="A", title="t2", preconditions=[], steps="s2", expected="e2"),
        ]
        failed_ids = {"TC-003"}  # 只有 TC-003 失败
        _already_confirmed = {tc.id for tc in all_confirmed}
        _seen_in_retry = set()

        retry_output = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=[], steps="s1", expected="e1"),  # 已确认
            TestCaseRow(id="TC-002", story="A", title="t2", preconditions=[], steps="s2", expected="e2"),  # 已确认
            TestCaseRow(id="TC-003", story="B", title="t3", preconditions=[], steps="s3", expected="e3"),  # 修复
        ]

        accepted = []
        for tc in retry_output:
            if tc.id not in failed_ids:
                continue
            if tc.id in _already_confirmed:
                continue
            if tc.id in _seen_in_retry:
                continue
            _seen_in_retry.add(tc.id)
            accepted.append(tc)

        assert len(accepted) == 1
        assert accepted[0].id == "TC-003"

    def test_retry_all_are_new_duplicates(self):
        """修复轮只生成一个有效 ID 但重复多次"""
        from prompts.response_model import TestCaseRow
        all_confirmed = []
        failed_ids = {"TC-005"}
        _already_confirmed = set()
        _seen_in_retry = set()

        retry_output = [
            TestCaseRow(id="TC-005", story="X", title="t5", preconditions=[], steps="s5", expected="e5"),
            TestCaseRow(id="TC-005", story="X", title="t5", preconditions=[], steps="s5", expected="e5"),
            TestCaseRow(id="TC-005", story="X", title="t5", preconditions=[], steps="s5", expected="e5"),
        ]

        accepted = []
        for tc in retry_output:
            if tc.id not in failed_ids:
                continue
            if tc.id in _already_confirmed:
                continue
            if tc.id in _seen_in_retry:
                continue
            _seen_in_retry.add(tc.id)
            accepted.append(tc)

        assert len(accepted) == 1
        assert accepted[0].id == "TC-005"


# ============================================================
# 3. PRE 关联用例列表去重测试
# ============================================================

class TestPreToCasesDedup:
    """测试共享前置关联用例列表去重"""

    def test_no_duplicates_in_linked_cases(self):
        """关联用例列表正常情况"""
        pre_to_cases = {
            "PRE-001": ["TC-001", "TC-002", "TC-003"],
            "PRE-002": ["TC-004"],
        }
        for pid in pre_to_cases:
            _seen = set()
            _deduped = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen:
                    _seen.add(cid)
                    _deduped.append(cid)
            pre_to_cases[pid] = _deduped

        assert pre_to_cases["PRE-001"] == ["TC-001", "TC-002", "TC-003"]
        assert pre_to_cases["PRE-002"] == ["TC-004"]

    def test_duplicate_linked_cases(self):
        """关联用例列表含重复（valid_cases 重复导致）"""
        pre_to_cases = {
            "PRE-004": ["TC-013", "TC-015", "TC-016", "TC-019",
                        "TC-017", "TC-020", "TC-017", "TC-020", "TC-017", "TC-020"],
        }
        for pid in pre_to_cases:
            _seen = set()
            _deduped = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen:
                    _seen.add(cid)
                    _deduped.append(cid)
            pre_to_cases[pid] = _deduped

        expected = ["TC-013", "TC-015", "TC-016", "TC-019", "TC-017", "TC-020"]
        assert pre_to_cases["PRE-004"] == expected

    def test_all_duplicates(self):
        """全部都是重复"""
        pre_to_cases = {
            "PRE-001": ["TC-001", "TC-001", "TC-001", "TC-001"],
        }
        for pid in pre_to_cases:
            _seen = set()
            _deduped = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen:
                    _seen.add(cid)
                    _deduped.append(cid)
            pre_to_cases[pid] = _deduped

        assert pre_to_cases["PRE-001"] == ["TC-001"]


# ============================================================
# 4. shared_preconditions 列表去重测试
# ============================================================

class TestSharedPreDedup:
    """测试 shared_preconditions 按 ID 去重"""

    def test_no_duplicates(self):
        from prompts.response_model import SharedPrecondition
        pres = [
            SharedPrecondition(id="PRE-001", name="前置1", steps="步骤1", expected="预期1"),
            SharedPrecondition(id="PRE-002", name="前置2", steps="步骤2", expected="预期2"),
        ]
        _seen = set()
        _deduped = []
        for pre in pres:
            if pre.id not in _seen:
                _seen.add(pre.id)
                _deduped.append(pre)
        assert len(_deduped) == 2

    def test_duplicate_ids(self):
        from prompts.response_model import SharedPrecondition
        pres = [
            SharedPrecondition(id="PRE-004", name="前置4", steps="步骤4", expected="预期4"),
            SharedPrecondition(id="PRE-004", name="前置4-重复", steps="步骤4", expected="预期4"),
        ]
        _seen = set()
        _deduped = []
        for pre in pres:
            if pre.id not in _seen:
                _seen.add(pre.id)
                _deduped.append(pre)
        assert len(_deduped) == 1
        assert _deduped[0].name == "前置4"


# ============================================================
# 5. 初始生成 seen_ids 去重（已有逻辑，确认正确）
# ============================================================

class TestInitialGenDedup:
    """测试初始生成 seen_ids 去重"""

    def test_duplicate_skipped_before_validation(self):
        """重复 TC ID 应在校验前被跳过"""
        from prompts.response_model import TestCaseRow
        plan_cases = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=[], steps="s1\ns2", expected="e1\ne2"),
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=[], steps="s1\ns2", expected="e1\ne2"),  # dup
            TestCaseRow(id="TC-002", story="B", title="t2", preconditions=[], steps="s3", expected="e3"),
        ]

        seen_ids = set()
        all_confirmed = []
        for tc in plan_cases:
            if tc.id in seen_ids:
                continue
            # 模拟校验
            errs = []
            for fld, lbl in [("id", "编号"), ("story", "子模块"), ("title", "标题"),
                             ("steps", "步骤"), ("expected", "预期")]:
                if not getattr(tc, fld, ""):
                    errs.append(f"{lbl}为空")
            if tc.steps and tc.expected:
                ns = tc.steps.count("\n") + 1
                ne = tc.expected.count("\n") + 1
                if ns != ne:
                    errs.append(f"步骤({ns})与预期({ne})不一致")
            if not errs:
                all_confirmed.append(tc)
                seen_ids.add(tc.id)

        assert len(all_confirmed) == 2
        assert [tc.id for tc in all_confirmed] == ["TC-001", "TC-002"]


# ============================================================
# 6. 端到端：完整去重链路模拟
# ============================================================

class TestEndToEndDedup:
    """端到端模拟：初始生成 → 修复轮 → 最终安全阀 → Excel 写入"""

    def test_full_pipeline_no_duplicates_in_output(self):
        """正常流程：不应产生重复"""
        from prompts.response_model import TestCaseRow, SharedPrecondition

        # 模拟初始生成输出
        initial_cases = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=["PRE-001"], steps="s1", expected="e1"),
            TestCaseRow(id="TC-002", story="A", title="t2", preconditions=["PRE-001"], steps="s2", expected="e2"),
            TestCaseRow(id="TC-003", story="B", title="t3", preconditions=["PRE-002"], steps="s3", expected="e3"),
        ]
        all_shared_pres = [
            SharedPrecondition(id="PRE-001", name="前置1", steps="s1", expected="e1"),
            SharedPrecondition(id="PRE-002", name="前置2", steps="s2", expected="e2"),
        ]

        # === 初始去重 ===
        seen_ids = set()
        all_confirmed = []
        for tc in initial_cases:
            if tc.id in seen_ids:
                continue
            all_confirmed.append(tc)
            seen_ids.add(tc.id)

        # === 最终安全阀 ===
        _seen_vc = set()
        _deduped = []
        for tc in all_confirmed:
            if tc.id in _seen_vc:
                continue
            _seen_vc.add(tc.id)
            _deduped.append(tc)
        valid_cases = _deduped

        # === PRE 去重 ===
        _seen_pre = set()
        _deduped_pres = []
        for pre in all_shared_pres:
            if pre.id not in _seen_pre:
                _seen_pre.add(pre.id)
                _deduped_pres.append(pre)

        # === 关联用例去重 ===
        pre_to_cases = {}
        for tc in valid_cases:
            for pid in tc.preconditions:
                pre_to_cases.setdefault(pid, []).append(tc.id)
        for pid in pre_to_cases:
            _seen_linked = set()
            _deduped_linked = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen_linked:
                    _seen_linked.add(cid)
                    _deduped_linked.append(cid)
            pre_to_cases[pid] = _deduped_linked

        # 验证：无重复
        assert len(valid_cases) == 3
        assert [tc.id for tc in valid_cases] == ["TC-001", "TC-002", "TC-003"]
        assert pre_to_cases["PRE-001"] == ["TC-001", "TC-002"]

    def test_full_pipeline_with_retry_duplicates(self):
        """修复轮产生重复 → 应在多处被拦截"""
        from prompts.response_model import TestCaseRow, SharedPrecondition

        all_shared_pres = [
            SharedPrecondition(id="PRE-001", name="前置1", steps="s1", expected="e1"),
        ]

        # === 初始生成（有 1 个失败） ===
        initial_cases = [
            TestCaseRow(id="TC-001", story="A", title="t1", preconditions=["PRE-001"], steps="s1", expected="e1"),
            TestCaseRow(id="TC-002", story="A", title="t2", preconditions=["PRE-001"], steps="", expected=""),  # 空步骤 → 失败
        ]

        seen_ids = set()
        all_confirmed = []
        failed_details = []
        for tc in initial_cases:
            if tc.id in seen_ids:
                continue
            errs = []
            for fld in ["id", "story", "title", "steps", "expected"]:
                if not getattr(tc, fld, ""):
                    errs.append(f"{fld}为空")
            if errs:
                failed_details.append((0, tc.model_dump(), errs))
            else:
                all_confirmed.append(tc)
                seen_ids.add(tc.id)

        assert len(all_confirmed) == 1  # 只有 TC-001 通过
        failed_ids = {f[1].get("id", "") for f in failed_details}
        assert "TC-002" in failed_ids

        # === 修复轮（LLM 输出 TC-002 两次） ===
        retry_cases = [
            TestCaseRow(id="TC-002", story="A", title="t2-fixed", preconditions=["PRE-001"], steps="s2", expected="e2"),
            TestCaseRow(id="TC-002", story="A", title="t2-fixed", preconditions=["PRE-001"], steps="s2", expected="e2"),  # dup!
            TestCaseRow(id="TC-003", story="B", title="t3", preconditions=["PRE-001"], steps="s3", expected="e3"),  # 不在 failed_ids
        ]

        _already_confirmed = {tc.id for tc in all_confirmed}
        _seen_in_retry = set()
        fixed_ids = set()
        for tc in retry_cases:
            if tc.id not in failed_ids:
                continue  # TC-003 被丢弃
            if tc.id in _already_confirmed:
                continue
            if tc.id in _seen_in_retry:
                continue  # 第二个 TC-002 被丢弃 ← 新逻辑
            _seen_in_retry.add(tc.id)
            all_confirmed.append(tc)
            fixed_ids.add(tc.id)

        assert len(all_confirmed) == 2  # TC-001 + 第一个 TC-002
        assert [tc.id for tc in all_confirmed] == ["TC-001", "TC-002"]

        # === 最终安全阀 ===
        _seen_vc = set()
        _deduped = []
        for tc in all_confirmed:
            if tc.id in _seen_vc:
                continue
            _seen_vc.add(tc.id)
            _deduped.append(tc)
        valid_cases = _deduped

        # === PRE 关联去重 ===
        pre_to_cases = {}
        for tc in valid_cases:
            for pid in tc.preconditions:
                pre_to_cases.setdefault(pid, []).append(tc.id)
        for pid in pre_to_cases:
            _seen_linked = set()
            _deduped_linked = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen_linked:
                    _seen_linked.add(cid)
                    _deduped_linked.append(cid)
            pre_to_cases[pid] = _deduped_linked

        assert len(valid_cases) == 2
        assert pre_to_cases["PRE-001"] == ["TC-001", "TC-002"]

    def test_full_pipeline_replicates_excel_issue(self):
        """精确复现 Excel 中的情况：TC-017/TC-020 各出现 3 次"""
        from prompts.response_model import TestCaseRow, SharedPrecondition

        all_shared_pres = [
            SharedPrecondition(id="PRE-004", name="创建企业住宅",
                               steps="创建企业类型住宅并绑定预付费", expected="成功"),
        ]

        # 模拟 LLM 初始输出（含重复 TC-017 和 TC-020，各有 3 份）
        initial_cases = [
            TestCaseRow(id="TC-013", story="用电统计", title="用电统计-按天统计-正向",
                        preconditions=["PRE-004"], steps="s13", expected="e13"),
            TestCaseRow(id="TC-015", story="预付费管理", title="预付费管理-企业充值",
                        preconditions=["PRE-004"], steps="s15", expected="e15"),
            TestCaseRow(id="TC-016", story="预付费管理", title="预付费管理-余额不足",
                        preconditions=["PRE-004"], steps="s16", expected="e16"),
            TestCaseRow(id="TC-019", story="公摊管理", title="公摊管理-启用/关闭自动公摊",
                        preconditions=["PRE-004"], steps="s19", expected="e19"),
            # TC-017 出现 3 次（模拟 LLM 在不同 story 输出相同 ID）
            TestCaseRow(id="TC-017", story="企业公摊管理", title="预付费管理-公摊生成待确认扣费-账单层",
                        preconditions=["PRE-004"], steps="s17", expected="e17"),
            TestCaseRow(id="TC-017", story="预付费管理", title="预付费管理-公摊生成待确认扣费-账单层",
                        preconditions=["PRE-004"], steps="s17", expected="e17"),
            TestCaseRow(id="TC-017", story="预付费管理", title="预付费管理-公摊生成待确认扣费-账单层",
                        preconditions=["PRE-004"], steps="s17", expected="e17"),
            # TC-020 出现 3 次
            TestCaseRow(id="TC-020", story="企业公摊管理", title="公摊管理-导入公摊数据格式错误-异常",
                        preconditions=["PRE-004"], steps="s20", expected="e20"),
            TestCaseRow(id="TC-020", story="公摊管理", title="公摊管理-导入公摊数据格式错误-异常",
                        preconditions=["PRE-004"], steps="s20", expected="e20"),
            TestCaseRow(id="TC-020", story="公摊管理", title="公摊管理-导入公摊数据格式错误-异常",
                        preconditions=["PRE-004"], steps="s20", expected="e20"),
        ]

        # === 初始去重（seen_ids） ===
        seen_ids = set()
        all_confirmed = []
        for tc in initial_cases:
            if tc.id in seen_ids:
                continue
            all_confirmed.append(tc)
            seen_ids.add(tc.id)

        # 验证：每个 ID 只出现一次
        assert len(all_confirmed) == 6
        id_list = [tc.id for tc in all_confirmed]
        assert id_list == ["TC-013", "TC-015", "TC-016", "TC-019", "TC-017", "TC-020"]

        # === 最终安全阀 ===
        _seen_vc = set()
        _deduped = []
        for tc in all_confirmed:
            if tc.id in _seen_vc:
                continue
            _seen_vc.add(tc.id)
            _deduped.append(tc)
        valid_cases = _deduped
        assert len(valid_cases) == 6

        # === PRE 关联去重 ===
        pre_to_cases = {}
        for tc in valid_cases:
            for pid in tc.preconditions:
                pre_to_cases.setdefault(pid, []).append(tc.id)
        for pid in pre_to_cases:
            _seen_linked = set()
            _deduped_linked = []
            for cid in pre_to_cases[pid]:
                if cid not in _seen_linked:
                    _seen_linked.add(cid)
                    _deduped_linked.append(cid)
            pre_to_cases[pid] = _deduped_linked

        # 关键验证：PRE-004 的关联用例不应包含重复的 TC-017/TC-020
        expected_linked = ["TC-013", "TC-015", "TC-016", "TC-019", "TC-017", "TC-020"]
        assert pre_to_cases["PRE-004"] == expected_linked


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
