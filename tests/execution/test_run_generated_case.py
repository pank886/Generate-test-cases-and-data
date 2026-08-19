# -*- coding: utf-8 -*-
"""实时执行「生成用例」的 pytest 方法（不修改测试框架）。

背景
----
Phase C 生成的测试用例落在测试框架里：
    C:/Users/damai/PycharmMiscProject/testcase/<业务线>/<场景>/
框架入口是 ``run.py``（PycharmMiscProject 根目录），最终用框架自身 pytest
（``pytest.ini`` + ``common/*`` + ``base/*``）执行，随后生成 Allure 报告并
起阻塞式 http 服务器（``serve_report``）。

本模块在「不修改框架」的前提下复刻 ``run.py`` 的 pytest 调用（路径作为位置
参数，见 run.py:74-94），执行某个生成用例，把「完整控制台输出 + junit 结构化
摘要 + 执行信息」写入仓库 ``logs/`` 目录作为测试报告。不调用 ``run.py`` 的
allure generate / serve_report（阻塞、会开浏览器，不适合自动化）。

默认跳过：实时执行会真实请求 dev 后端（conftest.py 自动登录
``https://dev.damaiiot.com:40443``），不能混进仓库默认回归套件。

用法（仓库根目录）::

    # 实时执行 智慧用电_32 生成用例，报告写到 logs/
    RUN_GENERATED_CASE=1 python -m pytest tests/execution/test_run_generated_case.py -v -s

    # 不开环境变量时本模块整体 skip，不影响仓库默认回归套件
    python -m pytest tests/execution/test_run_generated_case.py -v
"""
from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 测试框架根（run.py 所在目录，conftest.py / pytest.ini / common / base 都在这里）
FRAMEWORK_ROOT = Path(r"C:/Users/damai/PycharmMiscProject")
# 框架自带 venv（run.py serve_report 用的就是它）
FRAMEWORK_PY = FRAMEWORK_ROOT / ".venv" / "Scripts" / "python.exe"
# 默认目标：智慧用电_32 生成用例
DEFAULT_CASE = Path("testcase/园区基线/智慧用电_32")
# 仓库 logs/（测试报告落盘目录）
REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs"
# pytest.ini addopts 自动生成的 junit.xml（逐用例结果）
JUNIT_XML = FRAMEWORK_ROOT / "report" / "junit.xml"

pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_GENERATED_CASE"),
    reason="实时执行生成用例：设置 RUN_GENERATED_CASE=1 开启（默认跳过，避免拖慢回归套件）",
)


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _build_cmd(case_rel: Path) -> list[str]:
    """复刻 run.py 的 pytest 参数（run.py:74-94）。路径是位置参数。"""
    return [
        str(FRAMEWORK_PY), "-m", "pytest",
        "-c", "pytest.ini",
        "-v", "-s",
        "--alluredir=./report/temp",
        str(case_rel),
    ]


def _parse_pytest_summary(console: str) -> str:
    """从 pytest 控制台输出提取最后一行结果摘要。"""
    m = re.search(r"={5,}\s*(.*?)\s*={5,}\s*$", console, re.S)
    return m.group(1).strip() if m else "（未找到 pytest 摘要行）"


def _parse_junit() -> str:
    """解析框架自动生成的 report/junit.xml，输出逐用例结果摘要。"""
    if not JUNIT_XML.exists():
        return "（未生成 junit.xml）"
    try:
        root = ET.parse(JUNIT_XML).getroot()
    except ET.ParseError as e:
        return f"（junit.xml 解析失败: {e}）"
    ts = root.find("testsuite")
    if ts is None:
        ts = root
    total = ts.attrib.get("tests", "?")
    failed = ts.attrib.get("failures", "0")
    errors = ts.attrib.get("errors", "0")
    skipped = ts.attrib.get("skipped", "0")
    try:
        passed = max(int(total) - int(failed) - int(errors) - int(skipped), 0)
    except ValueError:
        passed = "?"
    lines = [
        f"junit 汇总: 总数={total}, 通过={passed}, 失败={failed}, 错误={errors}, 跳过={skipped}",
    ]
    for tc in ts.iter("testcase"):
        name = tc.attrib.get("name", "")
        classname = tc.attrib.get("classname", "")
        time = tc.attrib.get("time", "")
        child_tags = [c.tag for c in tc]
        if any(t in ("failure", "error") for t in child_tags):
            status = "失败"
        elif "skipped" in child_tags:
            status = "跳过"
        else:
            status = "通过"
        lines.append(f"  [{status}] {classname}::{name}  ({time}s)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 对外方法
# ---------------------------------------------------------------------------
def run_generated_case(
    case_rel: Path = DEFAULT_CASE,
    logs_dir: Path = LOGS_DIR,
    timeout: int = 1800,
) -> dict:
    """在测试框架根目录执行指定生成用例，把测试报告写入 logs_dir。

    返回: dict {exit_code, summary, report_file, console_chars}
    """
    case_abs = FRAMEWORK_ROOT / case_rel
    if not case_abs.exists():
        raise FileNotFoundError(f"生成用例目录不存在: {case_abs}")
    if not FRAMEWORK_PY.exists():
        raise FileNotFoundError(f"框架 venv 不存在: {FRAMEWORK_PY}")
    logs_dir.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        _build_cmd(case_rel),
        cwd=FRAMEWORK_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=timeout,
    )
    console = (proc.stdout or "") + (proc.stderr or "")

    summary = _parse_pytest_summary(proc.stdout or "")
    junit_summary = _parse_junit()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = logs_dir / f"test_report_{case_rel.name}_{stamp}.log"

    report = "\n".join([
        "=" * 80,
        "测试报告 - 生成用例执行",
        f"生成时间 : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"目标用例 : {case_abs}",
        f"执行方式 : {FRAMEWORK_ROOT / 'pytest.ini'}（框架自身 pytest，未修改框架）",
        f"命令     : {' '.join(_build_cmd(case_rel))}",
        f"退出码   : {proc.returncode}",
        f"执行摘要 : {summary}",
        "",
        junit_summary,
        "",
        "-" * 80,
        "完整控制台输出（pytest -v -s）:",
        "-" * 80,
        console,
    ])
    report_file.write_text(report, encoding="utf-8")
    return {
        "exit_code": proc.returncode,
        "summary": summary,
        "report_file": report_file,
        "console_chars": len(console),
    }


# ---------------------------------------------------------------------------
# pytest 用例（默认跳过；RUN_GENERATED_CASE=1 时执行）
# ---------------------------------------------------------------------------
def test_execute_wiselectric_32():
    """执行 智慧用电_32 生成用例，报告写 logs/。

    断言只要求「报告已落盘且捕获到控制台输出」——本方法是实时执行器，
    用例是否全过由后端决定，结果记入报告，不在此处强制断言。
    """
    result = run_generated_case(DEFAULT_CASE)
    assert result["report_file"].exists(), "测试报告未生成"
    assert result["report_file"].stat().st_size > 0, "测试报告为空"
    assert result["console_chars"] > 0, "未捕获到 pytest 控制台输出"
    print(f"\n[执行器] 摘要: {result['summary']}")
    print(f"[执行器] 报告: {result['report_file']}")
