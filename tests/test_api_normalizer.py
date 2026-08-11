"""Step 1: normalize_api_url 单元测试（D1 规范化函数）。

覆盖: 去域名 / 去 query / 去尾斜杠 / 保留大小写 / 路径参数字面量 / 幂等。
"""

from agent_components.api_annotations import normalize_api_url


class TestNormalizeApiUrl:
    def test_domain_stripped(self):
        assert normalize_api_url("http://host/order/list") == "/order/list"

    def test_https_domain_stripped(self):
        assert normalize_api_url("https://park.example.com/gymFacility/add") == "/gymFacility/add"

    def test_query_dropped(self):
        assert normalize_api_url("/order/list?status=pending&page=1") == "/order/list"

    def test_domain_and_query_stripped(self):
        assert normalize_api_url("http://host/order/list?x=1") == "/order/list"

    def test_trailing_slash_stripped(self):
        assert normalize_api_url("/order/list/") == "/order/list"

    def test_root_slash_preserved(self):
        assert normalize_api_url("/") == "/"

    def test_path_params_preserved_literal(self):
        assert normalize_api_url("/order/{id}") == "/order/{id}"

    def test_case_preserved(self):
        assert normalize_api_url("/gymFacility/add") == "/gymFacility/add"

    def test_plain_path_unchanged(self):
        assert normalize_api_url("/a/b") == "/a/b"

    def test_empty_string_unchanged(self):
        assert normalize_api_url("") == ""

    def test_none_unchanged(self):
        assert normalize_api_url(None) is None

    def test_idempotent(self):
        u = "http://Host/Order/List/?x=1"
        assert normalize_api_url(normalize_api_url(u)) == normalize_api_url(u)
