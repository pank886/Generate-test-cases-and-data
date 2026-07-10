"""关键流程集成测试 — 使用 D:\ai_test\md测试 中的真实文件。

运行方式:
  cd 项目根目录
  python -m pytest tests/test_key_flows.py -v

或单步调试:
  python tests/test_key_flows.py
"""

import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())

TEST_DATA_DIR = r"D:\ai_test\md测试"

# ============================================================
# 测试用例 1：MD 文件 → 接口提取流程
# ============================================================

class TestApiDocExtraction:
    """验证 post.md 的上传→LLM提取→确认入库完整链路。"""

    MD_PATH = os.path.join(TEST_DATA_DIR, "post.md")

    def test_1_file_exists(self):
        """前置：测试文件存在"""
        assert os.path.exists(self.MD_PATH), f"文件不存在: {self.MD_PATH}"
        assert self.MD_PATH.endswith(".md")

    def test_2_extract_apis(self):
        """LLM 从 MD 中提取接口定义（process_api_doc_extract）"""
        from ingest_v2 import process_api_doc_extract
        result = process_api_doc_extract(self.MD_PATH)
        assert result["module_name"], "应提取到模块名"
        assert len(result["apis"]) > 0, f"应提取到至少 1 个接口，实际 0"
        print(f"  模块: {result['module_name']}")
        print(f"  接口数: {len(result['apis'])}")
        for api in result["apis"][:5]:
            print(f"    {api['method']} {api['url']} — {api['name']}")
        assert all(
            k in api for api in result["apis"]
            for k in ("name", "url", "method", "description")
        ), "接口定义缺少必填字段"

    def test_3_commit_apis(self):
        """确认入库（commit_api_docs）"""
        from ingest_v2 import process_api_doc_extract, commit_api_docs
        extracted = process_api_doc_extract(self.MD_PATH)
        apis = extracted["apis"]
        module = extracted["module_name"]

        result = commit_api_docs(
            self.MD_PATH, module, apis, delete_original=False,
        )
        assert result["api_count"] == len(apis), \
            f"入库接口数不匹配: {result['api_count']} vs {len(apis)}"
        print(f"  入库完成: {result['api_count']} 个接口, doc_ids={result['doc_ids'][:3]}...")

    def test_4_search_committed_apis(self):
        """检索已入库的接口定义"""
        from agent_components.dual_chroma import get_chroma_db
        db = get_chroma_db()
        results = db.search_api_defs("健身房设施", k=5)
        assert len(results) > 0, "应检索到至少 1 条接口定义"
        print(f"  检索到 {len(results)} 条接口定义")
        for r in results[:3]:
            name = r.metadata.get("api_name", "?")
            print(f"    - {name}")
        return results


# ============================================================
# 测试用例 2：DOCX → 产品文档入库流程
# ============================================================

class TestProductDocIngestion:
    """验证 .docx 的提取→切块→LLM模块分析→ChromaDB入库完整链路。"""

    DOCX_PATH = os.path.join(TEST_DATA_DIR, "健身房预约模块需求文档.docx")

    def test_1_file_exists(self):
        assert os.path.exists(self.DOCX_PATH)

    def test_2_process_product_doc(self):
        """全流程：提取→切块→LLM→ChromaDB→SQLite"""
        from ingest_v2 import process_product_doc
        result = process_product_doc(self.DOCX_PATH, progress_cb=lambda p, m: None)
        assert result["doc_id"], "应生成 doc_id"
        assert result["chunks"] > 0, f"应生成至少 1 个文本块，实际 {result['chunks']}"
        assert result["module_name"], f"应提取到模块名，实际为空"
        print(f"  doc_id: {result['doc_id']}")
        print(f"  模块名: {result['module_name']}")
        print(f"  关联模块: {result['related_modules']}")
        print(f"  文本块数: {result['chunks']}")

    def test_3_search_product_docs(self):
        """检索已入库的产品文档"""
        from agent_components.dual_chroma import get_chroma_db
        db = get_chroma_db()
        results = db.search_product_docs("健身房预约", k=5)
        assert len(results) > 0, "应检索到产品文档片段"
        print(f"  检索到 {len(results)} 条")
        for r in results[:2]:
            print(f"    [{r.metadata.get('doc_id', '?')}] {r.page_content[:80]}...")

        return results


# ============================================================
# 测试用例 3：Axure ZIP → 原型解析入库流程
# ============================================================

class TestAxureProcessing:
    """验证 .zip Axure 原型的解析→页面提取→入库完整链路。"""

    ZIP_PATH = os.path.join(TEST_DATA_DIR, "健身房管理.zip")

    def test_1_file_exists(self):
        assert os.path.exists(self.ZIP_PATH)

    def test_2_process_axure(self):
        """全流程：解压→解析 sitemap→提取UI文本→分析模块→入库"""
        from ingest_v2 import process_axure_zip
        result = process_axure_zip(self.ZIP_PATH, progress_cb=lambda p, m: None)
        assert result["doc_id"], "应生成 doc_id"
        assert result["module_name"], "应解析出项目名/模块名"
        assert result["chunks"] > 0, f"应解析出至少 1 个页面块，实际 {result['chunks']}"
        print(f"  doc_id: {result['doc_id']}")
        print(f"  模块名: {result['module_name']}")
        print(f"  页面块数: {result['chunks']}")


# ============================================================
# 清理：删除本次测试产生的数据
# ============================================================

@pytest.fixture(autouse=True)
def cleanup_test_data():
    """每轮测试后清理 ChromaDB + SQLite 中本次测试产生的数据。"""
    yield
    # 测试清理在 conftest.py 或手动执行:
    # DocOps.delete_document(session, doc_id)
    # db.delete_by_doc_id(doc_id)


# ============================================================
# 主入口（非 pytest 模式）
# ============================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("=" * 60)
    print("测试 1: MD 接口提取")
    print("=" * 60)
    t1 = TestApiDocExtraction()
    t1.test_1_file_exists()
    t1.test_2_extract_apis()
    t1.test_3_commit_apis()
    t1.test_4_search_committed_apis()

    print()
    print("=" * 60)
    print("测试 2: DOCX 产品文档入库")
    print("=" * 60)
    t2 = TestProductDocIngestion()
    t2.test_1_file_exists()
    t2.test_2_process_product_doc()
    t2.test_3_search_product_docs()

    print()
    print("=" * 60)
    print("测试 3: Axure 原型解析入库")
    print("=" * 60)
    t3 = TestAxureProcessing()
    t3.test_1_file_exists()
    t3.test_2_process_axure()

    print()
    print("=" * 60)
    print("全部关键流程测试通过 ✅")
    print("=" * 60)
