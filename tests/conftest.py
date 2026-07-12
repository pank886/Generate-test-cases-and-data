"""pytest 共享配置"""
import os
import sys

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


def pytest_addoption(parser):
    """添加自定义 CLI 参数"""
    parser.addoption(
        "--base-url",
        default="http://localhost:8000",
        help="目标服务器地址（默认 http://localhost:8000）",
    )


def pytest_configure(config):
    """注册自定义标记"""
    config.addinivalue_line("markers", "slow: 慢测试（需要等待后台任务完成）")
