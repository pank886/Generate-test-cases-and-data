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
from html import unescape
from pathlib import Path
from urllib.parse import unquote

from langchain_text_splitters import RecursiveCharacterTextSplitter

import config

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
            # Axure RP 9+ 可能用 document.js 替代 data.js
            global_data_js = self._find_data_file(root, "data.js")
            if global_data_js is None:
                global_data_js = self._find_data_file(root, "document.js")
            global_data_js_content = None
            if global_data_js:
                global_data_js_content = global_data_js.read_text(encoding="utf-8", errors="replace")

            use_per_page_data_js = global_data_js_content is None

            # RP9 document.js 内含 sitemap 树（文件夹层级），用于必填字段按父级分组
            page_paths = {}
            if global_data_js_content and "rootNodes" in global_data_js_content:
                page_paths = self._parse_rp9_sitemap(global_data_js_content)
                if page_paths:
                    logger.info("   [RP9] document.js sitemap 树解析成功: %d 页含父级路径", len(page_paths))

            # 遍历页面，提取 UI 文本和交互
            page_details = {}
            all_pages = self._flatten_pages(sitemap.get("children", []))
            for page in all_pages:
                url = page["url"]
                decoded_url = unquote(url)

                # 实例方法查找 HTML 路径（自动走 _page_path_cache）
                html_path = self._find_page_html(root, decoded_url)

                ui_text = self._extract_ui_text_from_html(html_path)

                # 页面块四段：HTML 只读一次，复用字符串级提取
                page_path = page_paths.get(decoded_url) or page["name"]
                html_content = ""
                if html_path is not None:
                    html_content = html_path.read_text(encoding="utf-8", errors="replace")
                # 页面样式坐标（RP9 files/{页面}/styles.css），用于导航识别与顶栏按位剔除
                tops = None
                if html_path is not None:
                    page_stem = Path(decoded_url).stem
                    tops = AxureParser._load_page_widget_tops(root, page_stem)
                nav_hit = AxureParser._find_nav_container(html_content, tops)
                nav_panel_id = nav_hit[0] if nav_hit else None
                nav_top = tops.get(nav_panel_id) if (nav_hit and tops and nav_panel_id) else None
                dialogs = AxureParser._extract_dialogs_from_html(html_content, page_path)
                # 主块作用域：剔除导航 + 全部面板块面板（弹窗必填/筛选不混入主块 ②③）
                main_scope = html_content
                if nav_panel_id:
                    main_scope = AxureParser._strip_container(main_scope, nav_panel_id)
                for d in dialogs:
                    main_scope = AxureParser._strip_container(main_scope, d["panel_id"])
                required_fields = AxureParser._extract_required_fields(main_scope)
                filters = AxureParser._extract_filters(main_scope)
                # 主页面 ④ 排除集：导航面板 + 全部面板块面板
                exclude_ids = [nav_panel_id] if nav_panel_id else []
                exclude_ids += [d["panel_id"] for d in dialogs]
                page_explanation = self._extract_page_explanation(
                    html_content, exclude_ids=exclude_ids, nav_panel_id=nav_panel_id,
                    tops=tops, nav_top=nav_top,
                )
                embedded_images = AxureParser._extract_embedded_images(html_content)

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
                    "page_path": page_path,
                    "ui_text": ui_text,
                    "interactions": interactions,
                    "required_fields": required_fields,
                    "filters": filters,
                    "page_explanation": page_explanation,
                    "nav_panel_id": nav_panel_id,
                    "dialogs": dialogs,
                    "embedded_images": embedded_images,
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
        except Exception:
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

    @staticmethod
    def _load_page_widget_tops(root: Path, page_stem: str) -> dict[str, float]:
        """读取页面样式 styles.css 中全部 widget 的 top 坐标（RP9 每页独立样式）。

        返回 {widget_id: top_px}；页面样式缺失或解析失败返回 {}。
        坐标用于导航识别（多候选取 top 最小）与顶栏 post-nav 散件按位剔除。
        """
        tops: dict[str, float] = {}
        for path in root.rglob("styles.css"):
            if "__MACOSX" in path.parts:
                continue
            if path.parent.name == page_stem and "files" in path.parts:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return {}
                for m in re.finditer(r"#(u\d+)\s*\{([^}]*)\}", text):
                    tm = re.search(r"top:\s*(-?\d+(?:\.\d+)?)px", m.group(2))
                    if tm:
                        tops[m.group(1)] = float(tm.group(1))
                return tops
        return {}

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

        # 策略 3: 递归降级
        for path in root.rglob(filename):
            if "__MACOSX" in path.parts:
                continue
            # 允许 "data" 或 "plugins/sitemap" 等 Axure 标准目录
            if path.parent.name in ("data", "sitemap"):
                return path

        return None

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

        # 提取 body 内容（保留隐藏元素）。
        # 2026-08-17 修复：不再截断到 2000 字符——截断会静默丢弃页面后半段文本
        # （需求注释、必填星号标签等）。页面体积控制改由 to_product_doc_chunks
        # 的 RecursiveCharacterTextSplitter 按 config.CHUNK_SIZE 负责。
        body_match = re.search(r"<body[^>]*>(.*?)</body>", html, re.DOTALL | re.IGNORECASE)
        body_text = ""
        if body_match:
            body = body_match.group(1)
            body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.DOTALL | re.IGNORECASE)
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            body_text = body

        parts = []
        if label_text:
            parts.append("## 页面元素\n" + label_text)
        if body_text:
            parts.append("## 页面文本\n" + body_text)
        if not parts:
            parts.append(text)

        return "\n\n".join(parts)

    # ---- 必填字段提取 ----

    # 红色必填星号：<span style="color:#D9001B;">*</span><span>字段名</span>
    _STAR_SPAN_RE = re.compile(
        r'<span[^>]*style="[^"]*color\s*:\s*#\s*[Dd]9001B[^"]*"[^>]*>\s*[*＊]\s*</span>',
        re.IGNORECASE,
    )

    @staticmethod
    def _extract_required_fields(html: str) -> list[dict]:
        """从 HTML 字符串提取必填字段（红色 #D9001B 星号 + 后随标签文本）。

        表单必填项以 `<span style="color:#D9001B;">*</span><span>字段名</span>`
        标记（智慧用电等 Axure 导出）。返回 [{"field": "字段名"}]，
        按出现顺序去重。个别标签后可能混入相邻文本（Axure 文本节点拼接），
        长度上限 40 防御误抓。
        """
        fields = []
        seen: set[str] = set()
        for m in AxureParser._STAR_SPAN_RE.finditer(html):
            # 星号后的标签：取后随 span 文本，截至 </p> / <br，上限 300 字符
            seg = html[m.end():m.end() + 300]
            seg = seg.split("</p>", 1)[0]
            seg = seg.split("<br", 1)[0]
            spans = re.findall(r"<span[^>]*>([^<]*)</span>", seg)
            label = unescape("".join(spans)).strip()
            label = re.sub(r"\s+", " ", label)
            label = re.sub(r"[:：、;；\s]+$", "", label).strip()
            if not label or len(label) > 40:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            fields.append({"field": label})
        return fields

    @staticmethod
    def _extract_required_fields_from_html(html_path: Path | None) -> list[dict]:
        """从页面 HTML 文件提取必填字段（红色星号 + 后随标签文本）。"""
        if html_path is None:
            return []
        html = html_path.read_text(encoding="utf-8", errors="replace")
        return AxureParser._extract_required_fields(html)

    # ---- 筛选项提取（select/option，过滤原型占位符）----

    # Axure 未命名下拉框的占位符选项：请选择 / 选项 / 多选
    @staticmethod
    def _is_placeholder_option(opt: str) -> bool:
        """判断是否为原型占位符选项（请选择/选项/多选）。"""
        o = opt.strip()
        if not o:
            return True
        return o == "选项" or o == "多选" or o.startswith("请选择")

    @staticmethod
    def _prev_field_label(html: str, pos: int) -> str:
        """取 select 前最近的 <p><span>字段名</span></p> 文本（字段名启发式）。

        Axure 中表单字段名常以 <p><span>字段名：</span></p> 紧邻下拉框出现；
        未命名控件前 400 字符内无此结构则返回 ""（该筛选项无字段名，跳过）。
        """
        pre = html[max(0, pos - 400):pos]
        hits = list(re.finditer(
            r"(?:<p>\s*)?<span[^>]*>([^<]{1,40})</span>\s*(?:</p>)?", pre, re.S,
        ))
        if not hits:
            return ""
        label = unescape(hits[-1].group(1)).strip()
        label = re.sub(r"[:：、;；\s]+$", "", label).strip()
        if not label or len(label) > 40:
            return ""
        return label

    @staticmethod
    def _extract_filters(html: str) -> list[dict]:
        """从 HTML 字符串提取筛选项（下拉框字段名 → 选项集）。

        返回 [{"field": "计费方案", "options": ["计费方案名称（分时定价）", ...]}, ...]。

        过滤规则：
          - 选项占位符（请选择/选项/多选）剔除
          - 去重后真实选项 < 2 视为退化（如 `周期/周期`、`住户名称`×4）跳过
          - select 前 400 字符内无字段名标签的未命名控件跳过
        按字段名去重（同名保留首次出现的选项集）。
        """
        results = []
        seen_fields: set[str] = set()
        for m in re.finditer(r"<select\b[^>]*>", html):
            end = html.find("</select>", m.end())
            if end == -1:
                continue
            inner = html[m.end():end]
            opts = []
            for om in re.finditer(r"<option[^>]*>(.*?)</option>", inner, re.S):
                o = re.sub(r"<[^>]+>", "", om.group(1))
                o = unescape(o).strip()
                o = re.sub(r"\s+", " ", o)
                if o and not AxureParser._is_placeholder_option(o):
                    opts.append(o)
            # 去重保序
            uniq = []
            for o in opts:
                if o not in uniq:
                    uniq.append(o)
            if len(uniq) < 2:
                continue  # 退化选项（不足 2 个真实值），非有效筛选项
            field = AxureParser._prev_field_label(html, m.start())
            if not field or field in seen_fields:
                continue
            seen_fields.add(field)
            results.append({"field": field, "options": uniq})
        return results

    @staticmethod
    def _extract_filters_from_html(html_path: Path | None) -> list[dict]:
        """从页面 HTML 文件提取筛选项（下拉框字段名 → 选项集）。"""
        if html_path is None:
            return []
        html = html_path.read_text(encoding="utf-8", errors="replace")
        return AxureParser._extract_filters(html)

    # ---- 页面说明提取（页面内文字说明，整体复制保留原结构）----

    @staticmethod
    def _extract_explanation(html: str) -> str:
        """从 HTML 字符串提取文字说明——**整体复制，原结构搬运，不改结构**。

        去 script/style，块级/换行标签（br、p、div、li、tr、h1-h6 闭合）转 \n
        保留段落结构，不裁剪、不关键词过滤、不摘要。
        不依赖 <body> 包裹，可直接用于弹窗状态内容片段。
        """
        b = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
        b = re.sub(r"<style[^>]*>.*?</style>", "", b, flags=re.S | re.I)
        # 保留原结构：块级/换行标签转换行
        b = re.sub(r"<\s*(?:br|/p|/div|/li|/tr|/td|/h\d)\b[^>]*>", "\n", b, flags=re.I)
        b = re.sub(r"<[^>]+>", "", b)
        b = unescape(b)
        b = re.sub(r"[ \t]+", " ", b)
        b = re.sub(r"\n\s*\n+", "\n", b)
        return b.strip()

    @staticmethod
    def _extract_page_explanation(
        html: str,
        exclude_ids: list[str] | None = None,
        nav_panel_id: str | None = None,
        tops: dict[str, float] | None = None,
        nav_top: float | None = None,
    ) -> str:
        """从页面 HTML 字符串提取主页面说明（顶栏剔除 + 排除容器后整体复制）。

        - nav_panel_id 存在：从导航容器起点截断 → 顶栏（平台名/菜单）天然剔除
        - tops/nav_top 存在且 nav_top 合理（< 500）：再剔除导航后紧邻的
          post-nav 顶栏散件簇（面包屑 / 智慧大脑 / 工作台 / 资源中心），
          按位置（top < nav_top + 30）+ 顶栏类/面包屑形态判定，DOM 连续段停止
        - exclude_ids（导航容器、面板块面板等）：先整体剥离（div 配平），再整体复制
        主页面 ④ = 查询表单 + 需求规格；面板块 ④ 由各自面板内容单独提取。
        """
        if not html:
            return ""
        if nav_panel_id:
            nav_start = AxureParser._find_nav_container_start(html, tops)
            if nav_start != -1:
                html = html[nav_start:]
            if tops and nav_top is not None and 0 <= nav_top < 500:
                html = AxureParser._strip_post_nav_cluster(html, tops, nav_top)
        for div_id in (exclude_ids or []):
            if div_id:
                html = AxureParser._strip_container(html, div_id)
        # 截断后可能已无 <body> 开标签，取 `</body>` 前内容即可
        b = re.search(r"</body>", html, re.S)
        if b:
            html = html[:b.start()]
        html = re.sub(r"<body[^>]*>", "", html, flags=re.I)
        return AxureParser._extract_explanation(html)

    # ---- 顶栏 post-nav 散件簇剔除（面包屑 / 智慧大脑 / 工作台 / 资源中心）----

    #: 顶栏散件特征类（平台名/面包屑/智能入口等，模板级结构信号，非业务描述）
    TOPBAR_CLASS_RE = re.compile(
        r"box_1|box_2|_图片|_二级标题|_三级标题|_线段1|_形状1|nopointer"
    )

    @staticmethod
    def _is_topbar_class(cls: str) -> bool:
        """widget class 是否属于顶栏散件特征类。"""
        return bool(AxureParser.TOPBAR_CLASS_RE.search(cls))

    @staticmethod
    def _is_breadcrumb_text(html: str, o: int) -> bool:
        """widget 自身文本是否为面包屑形态（'智慧用电 / 电表管理'，X / Y）。

        只读该 widget 自身的 `uNN_text` 子节点（不含嵌套 widget），避免正文误判。
        """
        widm = re.match(r'<div\s+id="(u\d+)"', html[o:o + 64])
        if not widm:
            return False
        tm = re.search(r'id="' + widm.group(1) + r'_text"[^>]*>(.*?)</div>',
                       html[o:o + 2000], re.S)
        if not tm:
            return False
        t = re.sub(r"<[^>]+>", "", tm.group(1))
        t = t.replace("&nbsp;", " ").replace("\xa0", " ")
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            return False
        return bool(re.fullmatch(r"\S+\s*/\s*\S+", t))

    @staticmethod
    def _strip_post_nav_cluster(html: str, tops: dict[str, float], nav_top: float) -> str:
        """剔除导航截断片段后紧邻的 post-nav 顶栏散件簇（面包屑 / 智慧大脑 / 工作台）。

        仅扫描片段内顶层 widget（导航容器之后的同级散件，即原 #base 直接子级）：
        按 DOM 连续段处理——段内 widget 满足「class 属顶栏特征类」或「自身文本为
        面包屑形态且位于导航附近（top < nav_top + 40）」即剔除；遇到第一个不满足的
        widget（弹窗面板、正文、表格、查询表单等）即停止连续段，保护后续内容。
        top 坐标缺失时顶栏类判定不设位置门槛（靠连续段约束）。
        """
        threshold = nav_top + 40.0
        ranges = []
        in_run = False
        depth = 0
        i = 0
        n = len(html)
        while i < n:
            o = html.find("<div", i)
            c = html.find("</div>", i)
            if o == -1 and c == -1:
                break
            if c != -1 and (o == -1 or c < o):
                if depth > 0:
                    depth -= 1
                i = c + 6
                continue
            gt = html.find(">", o, n)
            if gt == -1:
                break
            if html[gt - 1:gt + 1] == "/>":
                i = gt + 1
                continue
            depth += 1
            if depth == 1:
                tag = html[o:gt + 1]
                widm = re.match(r'<div\s+id="(u\d+)"', tag)
                top = tops.get(widm.group(1)) if widm else None
                cm = re.search(r'class="([^"]*)"', tag)
                cls = cm.group(1) if cm else ""
                is_tb = AxureParser._is_topbar_class(cls)
                is_bc = (AxureParser._is_breadcrumb_text(html, o)
                         and top is not None and top < threshold)
                if is_tb or is_bc:
                    end = AxureParser._find_div_balanced_end(html, o)
                    if end != -1:
                        ranges.append((o, end))
                        in_run = True
                        i = end
                        depth = 0
                        continue
                elif in_run:
                    break
            i = gt + 1
        for o, end in reversed(ranges):
            html = html[:o] + html[end:]
        return html

    @staticmethod
    def _extract_embedded_images(html: str) -> list[str]:
        """提取页面 HTML 内嵌图片路径（<img src>，相对 zip 内路径）。

        排除 data: URI 与 http(s) 外链，去重保序。
        用于无可提取内容页面的兜底展示（页面本身的图片）。
        """
        seen = []
        for m in re.finditer(r'<img\b[^>]*\bsrc="([^"]+)"', html, re.I):
            src = m.group(1).strip()
            if src.lower().startswith(("data:", "http://", "https://")):
                continue
            if src not in seen:
                seen.append(src)
        return seen

    # ---- 容器操作（div 配平剥离 / 直接子级状态识别）----

    @staticmethod
    def _find_div_balanced_end(html: str, start: int) -> int:
        """从某 `<div ...>` 开标签起点开始，返回配平闭合 `</div>` 之后的位置。

        支持嵌套 div；自闭合 `<div ... />` 不计入深度（防护误配平）。
        找不到配平闭合返回 -1。
        """
        depth = 0
        pos = start
        n = len(html)
        while pos < n:
            o = html.find("<div", pos)
            c = html.find("</div>", pos)
            if o == -1 and c == -1:
                return -1
            if c != -1 and (o == -1 or c < o):
                depth -= 1
                pos = c + 6
                if depth == 0:
                    return pos
            else:
                gt = html.find(">", o)
                if gt == -1:
                    return -1
                if html[gt - 1:gt + 1] == "/>":  # 自闭合，不计入深度
                    pos = gt + 1
                    continue
                depth += 1
                pos = gt + 1
        return -1

    @staticmethod
    def _strip_container(html: str, div_id: str) -> str:
        """按 div id 找到容器，配平剥离整个容器（含自闭合防护）。"""
        m = re.search(r'<div id="' + re.escape(div_id) + r'"', html)
        if not m:
            return html
        end = AxureParser._find_div_balanced_end(html, m.start())
        if end == -1:
            return html
        return html[:m.start()] + html[end:]

    @staticmethod
    def _find_direct_child_panel_states(html: str, container_start: int, container_end: int) -> list[tuple]:
        """深度感知：只取 ax_default_hidden 容器的**直接子级** panel_state。

        返回 [(state_id, label, state_start, state_end), ...]。
        嵌套子面板（如 计费规则 u362 分时 状态嵌套在 u354 内）随其父状态
        一起跳过，不会被误判为独立弹窗。state 段含嵌套子面板 HTML。
        """
        results = []
        pos = html.find(">", container_start) + 1  # 容器开标签之后
        if pos <= 0 or pos >= container_end:
            return results
        depth = 0
        i = pos
        while i < container_end:
            o = html.find("<div", i, container_end)
            c = html.find("</div>", i, container_end)
            if o == -1 and c == -1:
                break
            if c != -1 and (o == -1 or c < o):
                # 闭合 div
                if depth > 0:
                    depth -= 1
                i = c + 6
                continue
            # 开 div
            gt = html.find(">", o, container_end)
            if gt == -1:
                break
            open_tag = html[o:gt + 1]
            if html[gt - 1:gt + 1] == "/>":  # 自闭合
                i = gt + 1
                continue
            depth += 1
            if depth == 1:
                sm = re.match(r'<div\s+id="(u\d+_state\d+)"', open_tag)
                if sm:
                    label = ""
                    lm = re.search(r'data-label="([^"]*)"', open_tag)
                    if lm:
                        label = lm.group(1)
                    end = AxureParser._find_div_balanced_end(html, o)
                    if end != -1 and end <= container_end:
                        results.append((sm.group(1), label, o, end))
                        depth = 0
                        i = end
                        continue
            i = gt + 1
        return results

    # ---- 导航面板 / 弹窗识别 ----

    @staticmethod
    def _html_to_text(html: str) -> str:
        """去标签取文本（压缩空白），用于弹窗表单标记判定 / 标题启发式。"""
        t = re.sub(r"<[^>]+>", " ", html)
        t = unescape(t)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    @staticmethod
    def _is_meaningless_state_label(label: str) -> bool:
        """判断动态面板状态标签是否为占位名（状态 1 / State 1 / State1 等）。"""
        return bool(re.match(r"^(状态|State)\s*\d*$", label.strip(), re.IGNORECASE))

    @staticmethod
    def _guess_dialog_title(state_html: str) -> str:
        """无业务标签弹窗：从状态内容前 300 字符提取「操作+对象」标题。

        如 "* 收费类型： 新增收费配置" → "新增收费配置"。
        提取不到返回 ""（调用方降级用原始标签）。
        """
        text = AxureParser._html_to_text(state_html)[:300]
        m = re.search(r"(新增|添加|创建|编辑|修改|查看|删除|配置)\s*([^\s*：:，,；;、。]{1,20})", text)
        if m:
            return (m.group(1) + m.group(2)).strip()
        return ""

    @staticmethod
    def _find_nav_container(html: str, tops: dict[str, float] | None = None) -> tuple[str, int] | None:
        """识别导航（结构树）面板容器。

        遍历页面动态面板状态，若某状态内容含 `<a class="link">` 页面链接
        （Axure 结构树导航的特征），其所在容器即导航面板。
        返回 (容器 id, 容器 `<div id=uNN` 开标签下标)；无则 None。
        若传入 tops（页面 widget top 坐标），多个候选时优先取 top 最小的容器
        ——如 企业结算配置 的整页包装面板 u795(top=1940) 与真实导航 u970(top=107)，
        前者包裹整页也含链接，必须让位给真正位于顶部的结构树。
        """
        candidates = []
        for m in re.finditer(r'<div id="(u\d+_state\d+)"[^>]*>', html):
            end = AxureParser._find_div_balanced_end(html, m.start())
            if end == -1:
                continue
            if re.search(r'<a[^>]*class="[^"]*\blink\b', html[m.end():end]):
                pid = m.group(1).split("_state")[0]
                pm = re.search(r'<div id="' + re.escape(pid) + r'"', html)
                if pm:
                    candidates.append((pid, pm.start()))
        if not candidates:
            return None
        if tops:
            best = None
            for pid, off in candidates:
                top = tops.get(pid)
                if top is not None:
                    if best is None or top < best[1]:
                        best = (pid, off, top)
            if best is not None:
                return best[0], best[1]
        return candidates[0]

    @staticmethod
    def _find_nav_container_id(html: str, tops: dict[str, float] | None = None) -> str | None:
        """导航容器 div id（如 "u53"）；无导航返回 None。"""
        hit = AxureParser._find_nav_container(html, tops)
        return hit[0] if hit else None

    @staticmethod
    def _find_nav_container_start(html: str, tops: dict[str, float] | None = None) -> int:
        """导航容器 `<div id=uNN` 开标签下标；无导航返回 -1。"""
        hit = AxureParser._find_nav_container(html, tops)
        return hit[1] if hit else -1

    @staticmethod
    def _extract_block_title(state_html: str) -> str:
        """从弹窗默认（可见）状态提取块标题（纯结构，无 LLM）。

        取第一个**非表单字段的正文段落**文本：widget class 须为正文/标题类
        （_文本段落 / _一级标题 / _二级标题 / _三级标题 / _标题），跳过
        表格、按钮、box、形状、线段等容器；跳过必填星号（* 开头）与
        字段标签（：结尾）等表单文本。提取不到返回 ""（调用方回退标签）。
        """
        title_class = re.compile(r"_文本段落|_一级标题|_二级标题|_三级标题|_标题")
        for m in re.finditer(r'<div id="(u\d+)"[^>]*class="([^"]*)"[^>]*>', state_html):
            cls = m.group(2)
            if ('table_cell' in cls or 'box_' in cls or '_表格' in cls
                    or '_形状' in cls or '_线段' in cls or 'button' in cls):
                continue
            if not title_class.search(cls):
                continue
            end = AxureParser._find_div_balanced_end(state_html, m.start())
            if end == -1:
                continue
            seg = state_html[m.start():end]
            tm = re.search(r'id="' + m.group(1) + r'_text"[^>]*>(.*?)</div>', seg, re.S)
            if not tm:
                tm = re.search(r'<p[^>]*>\s*<span[^>]*>([^<]+)</span>', seg)
                if not tm:
                    continue
                t = tm.group(1).strip()
            else:
                t = re.sub(r'<[^>]+>', '', tm.group(1))
                t = re.sub(r'\s+', ' ', t).strip()
            if not t:
                continue
            if t.startswith(('*', '＊')) or t.endswith(('：', ':')):
                continue
            return t
        return ''

    @staticmethod
    def _extract_dialogs_from_html(html: str, page_path: str) -> list[dict]:
        """从页面 HTML 提取隐藏动态面板 → 面板块（每个面板 1 块，状态合并）。

        通用结构规则（无任何业务描述）：
          - 容器 class 含 ax_default_hidden 且直接子级含 panel_state（动态面板）
          - 面板含 ≥1 个「业务状态（标签非 状态 N/State N）」或「含表单标记」→ 成块
          - 全占位状态且无表单标记 → 不成块（并入主页面）
          - 块标题 = 默认（可见）状态内容标题（_extract_block_title）；
            无则回退默认状态标签 → 首个业务状态标签 → 内容启发式
        返回 [{"panel_id", "state", "title", "required_fields", "filters", "explanation"}]。
        块标题 title 默认 "{page_path}/{标题}"，入库后可在前端改名。
        """
        dialogs = []
        form_markers = config.AXURE_FORM_MARKERS
        for m in re.finditer(r'<div id="(u\d+)"[^>]*class="[^"]*ax_default_hidden[^"]*"[^>]*>', html):
            pid = m.group(1)
            cend = AxureParser._find_div_balanced_end(html, m.start())
            if cend == -1:
                continue
            states = AxureParser._find_direct_child_panel_states(html, m.start(), cend)
            if not states:
                continue
            # 成块判定：任一状态 业务标签 或 含表单标记
            if not any(
                (not AxureParser._is_meaningless_state_label(label)) or
                any(mk in AxureParser._html_to_text(html[s:s2]) for mk in form_markers)
                for _sid, label, s, s2 in states
            ):
                continue
            # 默认（可见）状态：style 无 hidden
            default = None
            for sid, label, sstart, send in states:
                sm = re.search(r'<div id="' + re.escape(sid) + r'"[^>]*style="([^"]*)"', html)
                if sm and 'hidden' not in sm.group(1):
                    default = (sid, label, sstart, send)
                    break
            if default is None:
                default = states[0]
            _did, dlabel, ds, de = default
            seg = html[ds:de]
            title = AxureParser._extract_block_title(seg)
            if not title:
                if AxureParser._is_meaningless_state_label(dlabel):
                    for _sid, label, _s, _s2 in states:
                        if not AxureParser._is_meaningless_state_label(label):
                            title = label
                            break
                    if not title:
                        title = AxureParser._guess_dialog_title(seg) or dlabel
                else:
                    title = dlabel
            # 面板内全部状态合并为块内容（②必填 ③筛选 ④说明）
            all_html = "".join(html[s:s2] for _sid, _label, s, s2 in states)
            dialogs.append({
                "panel_id": pid,
                "state": dlabel,
                "title": f"{page_path}/{title}",
                "required_fields": AxureParser._extract_required_fields(all_html),
                "filters": AxureParser._extract_filters(all_html),
                "explanation": AxureParser._extract_explanation(all_html),
            })
        return dialogs

    # ---- RP9 document.js sitemap 树解析 ----

    @staticmethod
    def _match_js_bracket(text: str, open_pos: int) -> int:
        """返回从 open_pos（[ 或 (）开始的配对闭括号下标（含）。"""
        stack = []
        i = open_pos
        n = len(text)
        while i < n:
            ch = text[i]
            if ch in "([":
                stack.append(ch)
            elif ch in ")]":
                stack.pop()
                if not stack:
                    return i
            elif ch in "\"'":
                quote = ch
                i += 1
                while i < n:
                    if text[i] == "\\":
                        i += 2
                        continue
                    if text[i] == quote:
                        break
                    i += 1
            i += 1
        return -1

    @staticmethod
    def _split_js_elements(inner: str, var_map: dict) -> list:
        """按顶层逗号切分 JS 参数列表，逐项解码为 Python 对象。"""
        items = []
        stack = []
        i, n = 0, len(inner)
        seg_start = 0
        while i < n:
            ch = inner[i]
            if ch in "([":
                stack.append(ch)
            elif ch in ")]":
                if stack:
                    stack.pop()
            elif ch in "\"'":
                quote = ch
                i += 1
                while i < n:
                    if inner[i] == "\\":
                        i += 2
                        continue
                    if inner[i] == quote:
                        break
                    i += 1
            elif ch == "," and not stack:
                items.append(AxureParser._decode_js_value(inner[seg_start:i].strip(), var_map))
                seg_start = i + 1
            i += 1
        tail = inner[seg_start:].strip()
        if tail:
            items.append(AxureParser._decode_js_value(tail, var_map))
        return items

    @staticmethod
    def _decode_js_value(s: str, var_map: dict):
        """解码单个 JS 值：_(k,v,...) 字典 / [...] 数组 / 字符串 / 变量引用。"""
        if not s:
            return None
        if s.startswith("_(") and s.endswith(")"):
            args = AxureParser._split_js_elements(s[2:-1], var_map)
            d = {}
            for k in range(0, len(args) - 1, 2):
                key = var_map.get(args[k], args[k])
                d[key] = args[k + 1]
            return d
        if s.startswith("[") and s.endswith("]"):
            return AxureParser._split_js_elements(s[1:-1], var_map)
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1].replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
        return var_map.get(s, s)  # 变量引用 → 查字典，查不到原样返回

    @staticmethod
    def _parse_rp9_sitemap(document_js: str) -> dict:
        """解析 RP9 `data/document.js` 中的 sitemap 树，返回 {url: "父级/页面名"}。

        document.js 是压缩的 `$axure.loadDocument((function(){...})())` 格式，
        页面树用 `_(k1,v1,k2,v2,...)` 字典构造器 + 变量字典表
        （`var b="configuration",...,r="rootNodes",...`）描述；文件夹 type="Folder"
        带 children 数组嵌套，Wireframe 节点带 url。

        同名页（如 企业公摊生成 与 企业公摊生成_1.html）靠父级路径区分：
        企业预付费管理/企业公摊生成 vs 企业后付费管理/企业公摊生成。
        解析失败返回 {}（调用方降级为扁平 page_name）。
        """
        try:
            # 1. 定位变量字典表语句（含 r="rootNodes" 的 var 语句）
            anchor = re.search(r'\b([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"rootNodes"', document_js)
            if not anchor:
                return {}
            stmt_start = document_js.rfind("var ", 0, anchor.start())
            stmt_end = document_js.find(";", anchor.start())
            if stmt_start == -1 or stmt_end == -1:
                return {}
            preamble = document_js[stmt_start:stmt_end]
            var_map = {}
            for m in re.finditer(
                r"([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
                r'("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|true|false|-?\d+(?:\.\d+)?|0x[0-9A-Fa-f]+)',
                preamble,
            ):
                name, raw = m.group(1), m.group(2)
                if raw in ("true", "false"):
                    var_map[name] = raw
                elif raw[0] in "\"'":
                    var_map[name] = raw[1:-1].replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
                else:
                    var_map[name] = raw

            # 2. 定位 rootNodes 字典调用 `_(r, [...])` 并解码整棵树
            root_var = anchor.group(1)
            call_re = re.compile(r"_\s*\(\s*" + re.escape(root_var) + r"\s*,\s*\[")
            m = call_re.search(document_js)
            if not m:
                return {}
            call_end = AxureParser._match_js_bracket(document_js, m.start())
            if call_end == -1:
                return {}
            tree = AxureParser._decode_js_value(document_js[m.start():call_end + 1], var_map)
            nodes = tree.get("rootNodes", []) if isinstance(tree, dict) else []

            # 3. 遍历树，收集 url → "父级/页面名"
            result = {}
            def walk(items, parent_path=""):
                for node in items:
                    if not isinstance(node, dict):
                        continue
                    page_name = node.get("pageName") or ""
                    if parent_path and page_name:
                        path = f"{parent_path}/{page_name}"
                    else:
                        path = page_name or parent_path
                    if node.get("type") == "Folder":
                        walk(node.get("children") or [], path)
                    elif node.get("url"):
                        result[unquote(node["url"])] = path or page_name
                    elif page_name:
                        result[page_name] = path or page_name
            walk(nodes)
            return result
        except Exception as e:
            logger.warning("RP9 document.js sitemap 解析失败，降级为扁平页面名: %s", e)
            return {}

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

    @staticmethod
    def _build_block_sections(
        required_fields: list | None,
        filters: list | None,
        explanation: str | None,
        interactions: list | None = None,
    ) -> str:
        """组装块体四段（②必填/③筛选/④说明/⑤交互），空段跳过。"""
        body_parts = []
        # ② 必填字段
        req_fields = [rf.get("field", "") for rf in (required_fields or [])
                      if (rf.get("field") or "").strip()]
        if req_fields:
            body_parts.append("### 必填字段\n  " + " | ".join(req_fields))
        # ③ 筛选项（字段: 选项1\选项2）
        filter_strs = [
            f'{f.get("field", "")}: ' + "\\".join(f.get("options", []))
            for f in (filters or []) if f.get("field")
        ]
        if filter_strs:
            body_parts.append("### 筛选项\n  " + " | ".join(filter_strs))
        # ④ 页面说明（整体复制，保留原结构）
        expl = (explanation or "").strip()
        if expl:
            body_parts.append("### 页面说明\n" + expl)
        # ⑤ 交互流程（保留既有，避免检索回归；仅主块有）
        if interactions:
            body_parts.append("### 交互流程")
            body_parts.extend(f"  - {ia}" for ia in interactions)
        return "\n\n".join(body_parts)

    def _append_chunk(self, chunks: list, header: str, body: str, page_name: str,
                      splitter: RecursiveCharacterTextSplitter) -> None:
        """按体积决定整块或切分追加（每块重挂页面头，自含来源页信息）。"""
        if not body:
            chunks.append({"content": header, "page_name": page_name})
        elif len(header) + len(body) <= config.CHUNK_SIZE:
            chunks.append({"content": f"{header}\n\n{body}", "page_name": page_name})
        else:
            for piece in splitter.split_text(body):
                chunks.append({"content": f"{header}\n\n{piece}", "page_name": page_name})

    def to_product_doc_chunks(self, parsed: dict = None) -> list[dict]:
        """将解析结果转为产品文档文本块（主块 + 弹窗子块，四段结构）。

        返回格式: [{"content": str, "page_name": str}, ...]
        用于填充 document_chunks.content 和 document_chunks.page_name。

        2026-08-17 修复：原先每页一整块、靠 _clean_html_to_text 的 body[:2000]
        截断控制体积，导致页面后半段（需求注释、必填星号标签）被静默丢弃。
        现改用与产品文档链路（ingest/pipelines.py）同规格的
        RecursiveCharacterTextSplitter 按 config.CHUNK_SIZE/OVERLAP 拆分，
        每块重挂页面头，保证每块自含来源页信息。

        页面块四段 v2：每页 = 主块 + 每弹窗子块，块标题（①目录）含弹窗名
        （如 电表管理/添加），入库后可在前端改名。
        """
        if parsed is None:
            parsed = self.parse()

        # 与 ingest/pipelines.py 产品文档链路同规格的切分器
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=config.CHUNK_SIZE,
            chunk_overlap=config.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "，", " "],
        )

        page_details = parsed.get("page_details", {})
        all_pages = list(page_details.items())
        if len(all_pages) > 50:
            logger.warning("页面总数 %d > 50，已截断至 50 页", len(all_pages))
            all_pages = all_pages[:50]

        chunks = []
        for url, detail in all_pages:
            # 块名 = 页面路径（RP9 树含父级，如 企业预付费管理/企业公摊生成），作①目录
            page_path = detail.get("page_path") or detail.get("page_name") or url

            # ---- 主块 ----
            main_header = f"## 页面: {page_path}\n路径: {url}"
            main_body = AxureParser._build_block_sections(
                detail.get("required_fields"),
                detail.get("filters"),
                detail.get("page_explanation"),
                detail.get("interactions"),
            )
            if not main_body and detail.get("embedded_images"):
                # 仅有图片页面：不生成 "路径: xxx.html" 无用文本，仅保留页面标签。
                # 前端据此（chunk 无 ### 段 + page_images 有图）渲染原图而非文本。
                main_header = f"## 页面: {page_path}"
            self._append_chunk(chunks, main_header, main_body, page_path, splitter)

            # ---- 弹窗子块（每弹窗一块，标题 = 块标题 ①目录）----
            for d in detail.get("dialogs", []):
                dialog_title = d.get("title") or f"{page_path}/{d.get('state', '')}"
                dialog_header = f"## 页面: {dialog_title}\n路径: {url}（弹窗）"
                dialog_body = AxureParser._build_block_sections(
                    d.get("required_fields"),
                    d.get("filters"),
                    d.get("explanation"),
                )
                self._append_chunk(chunks, dialog_header, dialog_body, dialog_title, splitter)

        return chunks

    def cleanup(self):
        """清理临时文件。"""
        if self._tmp_dir and os.path.isdir(self._tmp_dir):
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
