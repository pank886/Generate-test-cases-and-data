"""孤儿文件检测测试。

覆盖场景:
  1. 无孤儿 — 已知文件覆盖所有磁盘文件
  2. 有孤儿 — 磁盘多了未知文件
  3. 多目录扫描 — pdf/docx/axure/md/product 全覆盖
  4. 已知名称为空 — 全部磁盘文件都是孤儿
  5. 目录不存在 — 不崩溃
  6. meta.json 标记正确

测试函数: web.app._scan_orphan_files
"""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def tmp_upload_dirs():
    """创建临时上传目录结构，测试后自动清理。"""
    tmp = tempfile.mkdtemp(prefix="test_orphan_")
    # 创建所有扫描目录
    for sub in ["pdf", "docx", "product", "axure", "md"]:
        os.makedirs(os.path.join(tmp, "uploads", sub), exist_ok=True)
    yield tmp
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


def _make_file(base: str, rel_path: str, content: str = "test"):
    """在 base 下创建文件，自动创建父目录。"""
    full = os.path.join(base, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    # 确保 mtime 稳定（避免 CI 极快速度导致 age=0）
    time.sleep(0.01)
    return os.path.basename(full)


# ============================================================
# 测试
# ============================================================


class TestOrphanDetection:
    """_scan_orphan_files 单元测试。"""

    def _call(self, base: str, known: set) -> list[dict]:
        from web.app import _scan_orphan_files
        return _scan_orphan_files(known, base)

    # ---- 正常情况 ----

    def test_no_orphans_when_all_known(self, tmp_upload_dirs):
        """所有磁盘文件都在 known 中 → 返回空列表。"""
        names = set()
        for sub, ext in [("pdf", ".pdf"), ("docx", ".docx"),
                         ("axure", ".zip"), ("md", ".md")]:
            names.add(_make_file(tmp_upload_dirs, f"uploads/{sub}/known{ext}"))
        # product 目录也放一个
        names.add(_make_file(tmp_upload_dirs, "uploads/product/known.pdf"))

        orphans = self._call(tmp_upload_dirs, names)
        assert orphans == [], f"预期无孤儿，实际: {orphans}"

    # ---- 孤儿情况 ----

    def test_orphan_detected(self, tmp_upload_dirs):
        """磁盘有文件但 known 为空 → 全部标记为孤儿。"""
        _make_file(tmp_upload_dirs, "uploads/pdf/orphan.pdf")
        _make_file(tmp_upload_dirs, "uploads/axure/orphan.zip")

        orphans = self._call(tmp_upload_dirs, set())
        assert len(orphans) == 2, f"预期 2 个孤儿，实际: {orphans}"
        for o in orphans:
            assert "orphan" in o["path"]
            assert o["age_days"] >= 0
            assert isinstance(o["meta_exists"], bool)

    def test_partial_known(self, tmp_upload_dirs):
        """部分已知、部分未知 → 只返回未知的。"""
        known = _make_file(tmp_upload_dirs, "uploads/pdf/known.pdf")
        orphan = _make_file(tmp_upload_dirs, "uploads/docx/orphan.docx")

        orphans = self._call(tmp_upload_dirs, {known})
        assert len(orphans) == 1
        assert orphan in orphans[0]["path"]

    # ---- 边界情况 ----

    def test_empty_known_set(self, tmp_upload_dirs):
        """known 为空 → 所有文件都是孤儿。"""
        names = [
            _make_file(tmp_upload_dirs, f"uploads/{d}/{n}")
            for d, n in [("pdf", "a.pdf"), ("docx", "b.docx"), ("axure", "c.zip")]
        ]
        orphans = self._call(tmp_upload_dirs, set())
        assert len(orphans) == len(names)

    def test_empty_disk(self, tmp_upload_dirs):
        """磁盘无文件 → 返回空。"""
        orphans = self._call(tmp_upload_dirs, {"some_file.pdf"})
        assert orphans == []

    def test_missing_directory_handled(self, tmp_upload_dirs):
        """扫描目录不存在 → 不崩溃，只扫描存在的目录。"""
        # 只创建 pdf 目录，其他都不存在
        os.makedirs(os.path.join(tmp_upload_dirs, "uploads", "pdf"), exist_ok=True)
        _make_file(tmp_upload_dirs, "uploads/pdf/only.pdf")
        orphans = self._call(tmp_upload_dirs, set())
        assert len(orphans) == 1  # 只有一个文件，其他目录静默跳过

    # ---- meta.json 标记 ----

    def test_meta_exists_flag(self, tmp_upload_dirs):
        """存在 .meta.json 的文件 → meta_exists=True。"""
        name = _make_file(tmp_upload_dirs, "uploads/pdf/with_meta.pdf")
        with open(os.path.join(tmp_upload_dirs, "uploads/pdf/with_meta.pdf.meta.json"), "w") as f:
            f.write('{"chunks": 5}')
        orphans = self._call(tmp_upload_dirs, set())
        assert len(orphans) == 1
        assert orphans[0]["meta_exists"] is True

    def test_meta_missing_flag(self, tmp_upload_dirs):
        """没有 .meta.json → meta_exists=False。"""
        _make_file(tmp_upload_dirs, "uploads/pdf/no_meta.pdf")
        orphans = self._call(tmp_upload_dirs, set())
        assert len(orphans) == 1
        assert orphans[0]["meta_exists"] is False

    # ---- product 目录 ----

    def test_product_dir_scanned(self, tmp_upload_dirs):
        """product 目录也被扫描。"""
        _make_file(tmp_upload_dirs, "uploads/product/p1.pdf")
        _make_file(tmp_upload_dirs, "uploads/product/p2.docx")
        orphans = self._call(tmp_upload_dirs, set())
        assert len(orphans) == 2
