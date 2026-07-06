"""Axure HTML 演示包解析器。

从 Axure 导出的 .zip 文件中提取页面结构、UI 文本和交互逻辑，
输出结构化文本供存入 product_docs 集合。
"""

import json
import os
import re
import tempfile
import zipfile
from pathlib import Path


class AxureParser:
    """Axure HTML 导出包解析器。"""

    def __init__(self, zip_path: str):
        self.zip_path = zip_path
        self._tmp_dir = None

    def parse(self) -> dict:
        """解析 Axure 导出包，返回结构化结果。

        Returns:
            {
                "project_name": str,
                "pages": [{"name": str, "url": str, "children": [...]}],
                "page_details": {url: {"ui_text": str, "interactions": [str]}},
            }
        """
        with zipfile.ZipFile(self.zip_path, "r") as zf:
            # 提取 sitemap
            sitemap = self._parse_sitemap(zf)

            # 提取项目名
            project_name = sitemap.get("name", os.path.basename(self.zip_path).replace(".zip", ""))

            # 遍历页面，提取 UI 文本和交互
            page_details = {}
            all_pages = self._flatten_pages(sitemap.get("children", []))
            for page in all_pages:
                url = page["url"]
                ui_text = self._extract_ui_text(zf, url)
                interactions = self._extract_interactions_for_page(zf, url)
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

    # ---- Sitemap 解析 ----

    def _parse_sitemap(self, zf: zipfile.ZipFile) -> dict:
        """从 data/sitemap.js 中解析页面树。"""
        try:
            content = zf.read("data/sitemap.js").decode("utf-8")
        except KeyError:
            # 兼容不同版本 Axure
            for name in zf.namelist():
                if "sitemap" in name and name.endswith(".js"):
                    content = zf.read(name).decode("utf-8")
                    break
            else:
                return {"name": "Unknown", "children": []}

        # Axure 的 sitemap.js 格式为: var sitemap = { ... };
        # 提取 JSON 部分
        match = re.search(r"var\s+sitemap\s*=\s*(\{.+?\});?\s*$", content, re.DOTALL)
        if match:
            raw = match.group(1)
            # JS 对象转 JSON：键名加引号，移除尾逗号
            raw = re.sub(r",\s*}", "}", raw)
            raw = re.sub(r"(\w+)\s*:", r'"\1":', raw)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                pass

        # 兜底：可能是较新版本，JSON 直接赋值
        try:
            return json.loads(content)
        except json.JSONDecodeError:
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

    # ---- UI 文本提取 ----

    def _extract_ui_text(self, zf: zipfile.ZipFile, page_url: str) -> str:
        """从页面 HTML 中提取 UI 文本内容。"""
        # Axure 页面文件在 resources/chrome/data/ 或 resources/ 下
        candidates = [
            page_url,
            f"resources/chrome/data/{page_url}",
            f"resources/{page_url}",
        ]
        html_content = None
        for cand in candidates:
            try:
                html_content = zf.read(cand).decode("utf-8")
                break
            except KeyError:
                continue

        if not html_content:
            return ""

        return self._clean_html_to_text(html_content)

    @staticmethod
    def _clean_html_to_text(html: str) -> str:
        """清洗 HTML，提取结构化的 UI 文本。"""
        # 简易清洗：移除 script/style 标签
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<img[^>]*>", "", html, flags=re.IGNORECASE)

        # 提取 data-label（Axure 元素命名属性）
        labels = re.findall(r'data-label="([^"]*)"', html)
        label_text = "\n".join(f"[元素] {l}" for l in labels if l.strip())

        # 提取可见文本（去掉标签，保留结构）
        text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        # 截取有效内容（Axure HTML 通常正文在 body 标签后）
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

    def _extract_interactions_for_page(self, zf: zipfile.ZipFile, page_url: str) -> list:
        """从 data/data.js 中提取结构化交互流（触发条件→动作→目标）。"""
        try:
            content = zf.read("data/data.js").decode("utf-8")
        except KeyError:
            # 兼容旧版本 Axure
            for name in zf.namelist():
                if "data.js" in name:
                    content = zf.read(name).decode("utf-8")
                    break
            else:
                return []

        interactions = []
        page_url_escaped = re.escape(page_url)

        # 策略 1: 匹配 $axure.registerCaseInfo 中的交互
        case_blocks = re.findall(
            r'registerCaseInfo\s*\(([^)]+)\)',
            content,
            re.DOTALL,
        )
        for block in case_blocks:
            if page_url not in block and page_url.replace(".html", "") not in block:
                continue
            # 提取事件类型 (click, change, load 等)
            events = re.findall(r'"event"\s*:\s*"([^"]*)"', block)
            # 提取动作描述
            actions = re.findall(r'"description"\s*:\s*"([^"]*)"', block)
            # 提取动作类型
            action_types = re.findall(r'"action"\s*:\s*"([^"]*)"', block)
            # 提取目标
            targets = re.findall(r'"target"\s*:\s*"([^"]*)"', block)

            for i, ev in enumerate(events):
                act = actions[i] if i < len(actions) else ""
                at = action_types[i] if i < len(action_types) else ""
                tg = targets[i] if i < len(targets) else ""
                flow = f"当 {ev} → {at}"
                if act:
                    flow += f" ({act})"
                if tg:
                    flow += f" 目标: {tg}"
                interactions.append(flow)

        # 策略 2: 匹配 pageData 中的交互
        if not interactions:
            pattern = '"' + page_url_escaped + '"\\s*:\\s*(\\{[^;]+?\\})\\s*[,;]'
            page_data_blocks = re.findall(pattern, content, re.DOTALL)
            for block in page_data_blocks:
                # 提取交互描述
                descs = re.findall(r'"description"\s*:\s*"([^"]*)"', block)
                interactions.extend(descs)
                # 提取交互类型
                types = re.findall(r'"type"\s*:\s*"([^"]*)"', block)
                for t in types:
                    if t not in ("onLoad",):
                        interactions.append(f"[{t}]")

        # 策略 3: 从 HTML 中提取 on[Event] 属性
        for name in zf.namelist():
            if name.endswith(page_url) or name.endswith(page_url.replace(".html", ".htm")):
                try:
                    html_content = zf.read(name).decode("utf-8")
                    pattern = r'data-label="([^"]*)"[^>]*?\b(on\w+)\s*=\s*"([^"]*)"'
                    widget_events = re.findall(pattern, html_content)
                    for label, event, code in widget_events:
                        action_code = code[:60].strip()
                        interactions.append(f"点击[{label}] → 触发{event}: {action_code}...")
                except Exception:
                    pass
                break

        # 去重 + 按逻辑顺序排列
        seen = set()
        ordered = []
        for i in interactions:
            if i not in seen:
                seen.add(i)
                ordered.append(i)
        return ordered[:20]  # 每页最多 20 条交互

    # ---- 转产品文档块 ----

    def to_product_doc_chunks(self, parsed: dict = None) -> list:
        """将解析结果转为产品文档文本块（用于存入 product_docs 集合）。

        每个页面生成一个文本块，包含页面名称、UI 元素、交互逻辑。
        """
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

        # 如果页面太多（>50），只保留前 50 个最重要页面
        return chunks[:50]

    def cleanup(self):
        """清理临时文件。"""
        if self._tmp_dir and os.path.isdir(self._tmp_dir):
            import shutil
            shutil.rmtree(self._tmp_dir, ignore_errors=True)
            self._tmp_dir = None
