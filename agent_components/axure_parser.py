"""Axure HTML 演示包解析器。

从 Axure 导出的 .zip 文件中提取页面结构、UI 文本和交互逻辑，
输出结构化文本供存入 product_docs 集合。

查找策略：先按标准 Axure 目录结构精确定位，找不到再递归降级搜索。
"""

import json5
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

logger = logging.getLogger(__name__)


class AxureParser:
    """Axure HTML 导出包解析器。

    支持任意嵌套层级的目录结构：
      - 扁平: page1.html, data/sitemap.js
      - 一层嵌套: myproject/page1.html, myproject/data/sitemap.js
      - 多层嵌套: myproject/a/page1.html, myproject/a/b/data/sitemap.js
    """

    def __init__(self, zip_path: str):
        self.zip_path = zip_path
        self._tmp_dir = None
        # 缓存：page_url → 实际文件路径，避免重复全盘遍历
        self._page_path_cache: dict = {}

    def parse(self) -> dict:
        """解析 Axure 导出包，返回结构化结果。

        支持两种 Axure 导出格式：
          1. 旧格式 (RP 8): data/sitemap.js + data/data.js（全局交互文件）
          2. 新格式 (RP 9+): plugins/sitemap/ + files/页面名/data.js（每页独立交互）

        先将 zip 解压到临时目录，再按"精确路径 → 递归降级"策略查找文件。
        data.js 只读一次、每页 html_path 只查一次（走缓存），避免 O(N) 次全盘遍历。
        """
        self._tmp_dir = tempfile.mkdtemp(prefix="axure_")
        try:
            with zipfile.ZipFile(self.zip_path, "r") as zf:
                zf.extractall(self._tmp_dir)

            root = Path(self._tmp_dir)

            # 提取 sitemap
            sitemap = self._parse_sitemap(root)

            # 如果 sitemap 解析失败（新格式没有 var sitemap = {...}），从 HTML 文件发现页面
            if not sitemap.get("children"):
                sitemap = self._discover_pages_from_html(root)

            # 提取项目名
            project_name = sitemap.get("name", os.path.basename(self.zip_path).replace(".zip", ""))

            # 判断数据格式：全局 data/data.js（旧格式）还是 files/页面名/data.js（新格式）
            global_data_js = self._find_data_file(root, "data.js")
            global_data_js_content = None
            if global_data_js:
                global_data_js_content = global_data_js.read_text(encoding="utf-8", errors="replace")

            use_per_page_data_js = global_data_js_content is None

            # 遍历页面，提取 UI 文本和交互
            page_details = {}
            all_pages = self._flatten_pages(sitemap.get("children", []))
            for page in all_pages:
                url = page["url"]
                decoded_url = unquote(url)

                # 实例方法查找 HTML 路径（自动走 _page_path_cache）
                html_path = self._find_page_html(root, decoded_url)

                ui_text = self._extract_ui_text_from_html(html_path)

                # 新格式：每个页面有独立的 data.js；旧格式：用全局 data.js
                if use_per_page_data_js:
                    per_page_data = self._find_page_data_js(root, decoded_url)
                else:
                    per_page_data = global_data_js_content

                interactions = self._extract_interactions_for_page(
                    url, decoded_url, per_page_data, html_path
                )
                page_details[url] = {
                    "page_name": page["name"],
                    "ui_text": ui_text,
                    "interactions": interactions,
                }

            return {
                "project_name": project_name,
                "pages": sitemap.get("children", []),
                "page_details": page_details,
            }
        finally:
            self.cleanup()

    # ---- Sitemap 解析 ----

    @staticmethod
    def _parse_sitemap(root: Path) -> dict:
        """查找并解析 sitemap.js（先精确路径，再递归降级）。

        使用 json5 解析，天然兼容 JS 对象的尾逗号、注释、单引号。
        """
        sitemap_path = AxureParser._find_data_file(root, "sitemap.js")
        if sitemap_path is None:
            return {"name": "Unknown", "children": []}

        content = sitemap_path.read_text(encoding="utf-8")

        # 提取 var sitemap = 后面的 JS 对象
        match = re.search(r'var\s+sitemap\s*=\s*([\s\S]+?);?\s*$', content)
        try:
            if match:
                return json5.loads(match.group(1))
            return json5.loads(content)
        except (ValueError, json5.Json5Exception):
            return {"name": "Unknown", "children": []}

    @staticmethod
    def _flatten_pages(children: list, parent_path: str = "") -> list:
        """递归展开页面树为列表。"""
        pages = []
        for child in children:
            url = child.get("url", "")
            pages.append({
                "name": child.get("name", "?"),
                "url": url,
                "path": f"{parent_path}/{child.get('name', '?')}" if parent_path else child.get("name", "?"),
            })
            if child.get("children"):
                pages.extend(AxureParser._flatten_pages(child["children"], pages[-1]["path"]))
        return pages

    # ======================== 页面发现（新格式降级） ========================

    @staticmethod
    def _discover_pages_from_html(root: Path) -> dict:
        """从 HTML 文件发现页面（Axure RP 9+ 格式降级方案）。

        新格式没有 var sitemap = {...}，页面以独立 .html 文件存在根目录。
        扫描项目目录下的 .html 文件，从 <title> 提取页面名。
        """
        # 找到项目根目录（含 .html 文件和 files/ 子目录的那个目录）
        project_dir = root
        for subdir in root.iterdir():
            if subdir.is_dir() and "__MACOSX" not in subdir.name:
                has_html = bool(list(subdir.glob("*.html")))
                has_files = (subdir / "files").is_dir()
                if has_html and has_files:
                    project_dir = subdir
                    break

        # 收集所有页面 HTML（排除工具页面）
        skip_names = {"start", "start_c_1", "start_with_pages", "index",
                       "resources", "Other", "reload", "chrome"}
        pages = []
        for html_file in sorted(project_dir.glob("*.html")):
            stem = html_file.stem
            if stem in skip_names:
                continue

            # 从 <title> 提取页面名，降级用文件名
            page_name = stem
            try:
                content = html_file.read_text(encoding="utf-8", errors="replace")
                title_m = re.search(r"<title>([^<]*)</title>", content)
                if title_m:
                    page_name = title_m.group(1).strip()
            except (OSError, UnicodeDecodeError):
                pass

            pages.append({"name": page_name, "url": unquote(html_file.name), "children": []})

        project_name = project_dir.name
        return {"name": project_name, "children": pages}

    @staticmethod
    def _find_page_data_js(root: Path, page_url: str) -> str | None:
        """查找页面对应的 data.js（Axure RP 9+ 格式：files/页面名/data.js）。"""
        page_stem = Path(unquote(page_url)).stem
        # 在 files/ 子目录下查找同名文件夹中的 data.js
        for path in root.rglob("data.js"):
            if "__MACOSX" in path.parts:
                continue
            if path.parent.name == page_stem and "files" in path.parts:
                return path.read_text(encoding="utf-8", errors="replace")
        return None

    # ======================== 文件查找 ========================

    @staticmethod
    def _find_data_file(root: Path, filename: str) -> Path | None:
        """查找 data 目录下的 JS 文件（先精确路径，再递归降级）。

        标准 Axure 导出结构：data/ 在解压根目录下或一层子目录下。
        注意：只匹配父目录名为 "data" 的文件，排除 files/页面名/data.js 等。
        """
        # 策略 1: 直接查找 root/data/filename
        candidate = root / "data" / filename
        if candidate.is_file():
            return candidate

        # 策略 2: 查找 root/*/data/filename（一层嵌套）
        for subdir in root.iterdir():
            if subdir.is_dir() and "__MACOSX" not in subdir.name:
                candidate = subdir / "data" / filename
                if candidate.is_file():
                    return candidate

        # 策略 3: 递归降级（只匹配父目录名为 "data" 的，防止误匹配 files/页面/data.js）
        for path in root.rglob(filename):
            if "__MACOSX" in path.parts:
                continue
            if path.parent.name == "data":
                return path

        return None

    @staticmethod
    def _find_file_recursive(root: Path, filename: str) -> Path | None:
        """递归遍历目录树，按文件名查找（带大小写不敏感降级）。"""
        # 先精确大小写匹配
        for path in root.rglob(filename):
            if "__MACOSX" in path.parts:
                continue
            return path

        # 降级：大小写不敏感匹配（Linux/macOS 文件系统区分大小写）
        filename_lower = filename.lower()
        for path in root.rglob("*"):
            if "__MACOSX" in path.parts:
                continue
            if not path.is_file():
                continue
            if path.name.lower() == filename_lower:
                return path

        return None

    @staticmethod
    def _path_components_match(file_path: str, target_url: str) -> bool:
        """检查 file_path 末尾的路径组件是否与 target_url 完全一致。

        例如:
          _path_components_match("a/sub/page1.html", "sub/page1.html") → True
          _path_components_match("a/b/sub/page1.html", "sub/page1.html") → False
             (末尾组件是 "b/sub/page1.html" ≠ "sub/page1.html")
        """
        file_parts = Path(file_path).parts
        target_parts = Path(target_url).parts
        if len(target_parts) > len(file_parts):
            return False
        return file_parts[-len(target_parts):] == target_parts

    def _find_page_html(self, root: Path, page_url: str) -> Path | None:
        """查找页面 HTML 文件（先精确路径 + 缓存，再递归降级）。

        page_url 已经过 URL 解码（如 "页面.html" 而非 "%E9%A1%B5%E9%9D%A2.html"）。
        """
        # 缓存命中
        if page_url in self._page_path_cache:
            cached = self._page_path_cache[page_url]
            if cached.is_file():
                return cached
            else:
                del self._page_path_cache[page_url]

        result = self._find_page_html_impl(root, page_url)
        if result is not None:
            self._page_path_cache[page_url] = result
        return result

    @staticmethod
    def _find_page_html_impl(root: Path, page_url: str) -> Path | None:
        """页面查找实现：精确路径 → 常见子目录 → 路径组件匹配 → 递归降级。"""
        # 去掉开头的 /（如果有）
        clean_url = page_url.lstrip("/")

        # ---- 策略 1: 精确路径 ----
        candidate = root / clean_url
        if candidate.is_file():
            return candidate

        # ---- 策略 2: 常见 Axure 子目录 ----
        for prefix in ("pages", "html", "page"):
            candidate = root / prefix / clean_url
            if candidate.is_file():
                return candidate

        # ---- 策略 3: 路径组件精确匹配（只遍历 .html/.htm，避免 rglob("*") 的性能灾难）----
        target_parts = Path(clean_url).parts
        target_len = len(target_parts)

        for ext in ("*.html", "*.htm"):
            for path in root.rglob(ext):
                if "__MACOSX" in path.parts:
                    continue
                file_parts = path.parts
                if len(file_parts) < target_len:
                    continue
                if file_parts[-target_len:] == target_parts:
                    return path

        # ---- 策略 4: 按 basename 匹配（大小写不敏感降级）----
        basename = Path(clean_url).name
        basename_lower = basename.lower()

        for ext in ("*.html", "*.htm"):
            for path in root.rglob(ext):
                if "__MACOSX" in path.parts:
                    continue
                if path.name.lower() == basename_lower:
                    return path

        # ---- 策略 5: basename 去扩展名后模糊匹配 ----
        stem = Path(clean_url).stem.lower()
        if stem:
            for ext in ("*.html", "*.htm"):
                for path in root.rglob(ext):
                    if "__MACOSX" in path.parts:
                        continue
                    if path.stem.lower() == stem:
                        return path

        return None

    # ---- UI 文本提取 ----

    @staticmethod
    def _extract_ui_text_from_html(html_path: Path | None) -> str:
        """从 HTML 文件路径提取 UI 文本（html_path 由外部缓存查找提供）。"""
        if html_path is None:
            return ""
        html_content = html_path.read_text(encoding="utf-8", errors="replace")
        return AxureParser._clean_html_to_text(html_content)

    @staticmethod
    def _clean_html_to_text(html: str) -> str:
        """清洗 HTML，提取结构化的 UI 文本。

        保留 display:none / visibility:hidden 中的内容（动态面板状态），
        提取 data-label 和中继器相关属性。
        """
        # 移除 script/style 标签（但不移除隐藏 div）
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # 提取 data-label（Axure 元素命名属性）
        labels = re.findall(r'data-label="([^"]*)"', html)

        # 提取动态面板状态名（data-ax-* 属性）
        panel_states = re.findall(r'data-ax-state="([^"]*)"', html)
        repeater_data = re.findall(r'data-ax-repeater="([^"]*)"', html)
        # 提取中继器内的文本标签
        repeater_labels = re.findall(r'data-ax-repeater-label="([^"]*)"', html)

        label_text = "\n".join(f"[元素] {l}" for l in labels if l.strip())

        if panel_states:
            label_text += "\n" + "\n".join(f"[动态面板状态] {s}" for s in panel_states if s.strip())
        if repeater_data:
            label_text += "\n" + "\n".join(f"[中继器数据] {r}" for r in repeater_data if r.strip())
        if repeater_labels:
            label_text += "\n" + "\n".join(f"[中继器元素] {r}" for r in repeater_labels if r.strip())

        # 提取可见文本（不去掉标签内的文本，包括隐藏元素）
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # 截取 body 内容（保留隐藏元素）
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
        body_text = ""
        if body_match:
            body = body_match.group(1)
            body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            body_text = body[:2000] if len(body) > 2000 else body

        parts = []
        if label_text:
            parts.append("## 页面元素\n" + label_text)
        if body_text:
            parts.append("## 页面文本\n" + body_text)
        if not parts:
            parts.append(text[:2000])

        return "\n\n".join(parts)

    # ---- 交互提取 ----

    @staticmethod
    def _extract_brace_content(text: str, start_pos: int) -> str:
        """从 start_pos 开始提取括号内完整内容（支持嵌套括号）。

        例如从 "registerCaseInfo({...nested()...})" 中提取完整的 {...}，
        不会像 ([^)]+) 那样在第一个 ) 处截断。
        """
        # 找到第一个 '('
        brace_start = text.find("(", start_pos)
        if brace_start == -1:
            return ""
        depth = 0
        for i in range(brace_start, len(text)):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return text[brace_start + 1:i]
        return ""

    def _extract_interactions_for_page(
        self,
        url_encoded: str,
        url_decoded: str,
        data_js_content: str | None,
        html_path: Path | None,
    ) -> list:
        """从 data.js 中提取结构化交互流。

        Args:
            url_encoded: sitemap 中的原始 URL（可能含 URL 编码）
            url_decoded: URL 解码后的页面路径
            data_js_content: parse() 预先读取的 data.js 全文（避免每页重读）
            html_path: parse() 预先查找到的页面 HTML 路径（走 _page_path_cache）
        """
        if not data_js_content:
            return []

        content = data_js_content

        interactions = []

        page_basename = Path(url_decoded).name  # 如 "page1.html"
        page_stem = Path(url_decoded).stem      # 如 "page1"
        url_pattern = re.escape(url_encoded)

        # ---- 策略 1: registerCaseInfo（括号计数提取，不截断嵌套括号）----
        for match in re.finditer(r"registerCaseInfo\s*\(", content):
            block = AxureParser._extract_brace_content(content, match.start())
            if not block:
                continue

            # 精确匹配页面引用
            matched = False
            if url_pattern in block:
                matched = True
            elif url_decoded in block:
                matched = True
            elif re.search(r'["\'/]' + re.escape(page_basename) + r'["\']', block):
                matched = True
            elif re.search(r'["\'/]' + re.escape(page_stem) + r'["\']', block):
                matched = True

            if not matched:
                continue

            events = re.findall(r'"event"\s*:\s*"([^"]*)"', block)
            actions = re.findall(r'"description"\s*:\s*"([^"]*)"', block)
            action_types = re.findall(r'"action"\s*:\s*"([^"]*)"', block)
            targets = re.findall(r'"target"\s*:\s*"([^"]*)"', block)

            for i, ev in enumerate(events):
                act = actions[i] if i < len(actions) else ""
                at = action_types[i] if i < len(action_types) else ""
                tg = targets[i] if i < len(targets) else ""

                # 动态面板归属标记：target 含 panel/state 时加前缀
                is_panel = tg and ("panel" in tg.lower() or "state" in tg.lower())

                flow = f"当 {ev} → {at}"
                if is_panel:
                    flow = f"[动态面板] {flow}"
                if act:
                    flow += f" ({act})"
                if tg:
                    flow += f" 目标: {tg}"
                interactions.append(flow)

        # ---- 策略 1.5: pageData.push 数组形式（兼容旧版 Axure）----
        if not interactions:
            push_blocks = re.findall(
                r'pageData\.push\s*\(\s*\{([^}]*?url\s*:\s*["\']'
                + re.escape(page_basename)
                + r'["\'][^}]*?)}\s*\)',
                content,
                re.DOTALL,
            )
            for block in push_blocks:
                descs = re.findall(r'"description"\s*:\s*"([^"]*)"', block)
                interactions.extend(descs)
                types = re.findall(r'"type"\s*:\s*"([^"]*)"', block)
                for t in types:
                    if t not in ("onLoad",):
                        interactions.append(f"[{t}]")

        # ---- 策略 2: 匹配 pageData 键值对块 ----
        if not interactions:
            page_patterns = [
                re.escape(url_encoded),
                re.escape(url_decoded),
                re.escape(page_basename),
            ]
            page_data_blocks = []
            for pp in page_patterns:
                pattern = r'"(' + pp + r')"\s*:\s*(\{[^;]+?\})\s*[,;]'
                page_data_blocks = re.findall(pattern, content, re.DOTALL)
                if page_data_blocks:
                    break

            for block_match in page_data_blocks:
                block = block_match[1] if isinstance(block_match, tuple) else block_match
                descs = re.findall(r'"description"\s*:\s*"([^"]*)"', block)
                interactions.extend(descs)
                types = re.findall(r'"type"\s*:\s*"([^"]*)"', block)
                for t in types:
                    if t not in ("onLoad",):
                        interactions.append(f"[{t}]")

        # ---- 策略 3: 从页面 HTML 中提取 on[Event] 属性（用外部传入的 html_path）----
        if html_path is not None:
            try:
                html_content = html_path.read_text(encoding="utf-8", errors="replace")
                pattern = r'data-label="([^"]*)"[^>]*?\b(on\w+)\s*=\s*"([^"]*)"'
                widget_events = re.findall(pattern, html_content)
                for label, event, code in widget_events:
                    action_code = code[:60].strip()
                    interactions.append(f"点击[{label}] → 触发{event}: {action_code}...")
            except (OSError, UnicodeDecodeError):
                pass

        # 去重
        seen = set()
        ordered = []
        for i in interactions:
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        if len(ordered) > 20:
            logger.warning("页面交互数 %d > 20，已截断至 20 条", len(ordered))
        return ordered[:20]

    # ---- 转产品文档块 ----

    def to_product_doc_chunks(self, parsed: dict = None) -> list:
        """将解析结果转为产品文档文本块（用于存入 product_docs 集合）。"""
        if parsed is None:
            parsed = self.parse()

        chunks = []
        page_details = parsed.get("page_details", {})
        for url, detail in page_details.items():
            lines = [
                f"## 页面: {detail['page_name']}",
                f"路径: {url}",
            ]
            if detail["ui_text"]:
                lines.append(detail["ui_text"])
            if detail["interactions"]:
                lines.append("## 交互流程")
                for ia in detail["interactions"]:
                    lines.append(f"  - {ia}")

            chunks.append("\n".join(lines))

        if len(chunks) > 50:
            logger.warning("页面总数 %d > 50，已截断至 50 页", len(chunks))
        return chunks[:50]

    def cleanup(self):
        """清理临时文件。"""
        if self._tmp_dir and os.path.isdir(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
