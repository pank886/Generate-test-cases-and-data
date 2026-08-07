"""Phase C: 英文翻译 + 幂等性保障 Mixin

拆分自 generators/__init__.py（2026-08-07 大文件拆分）。

依赖宿主类提供: self._invoke_structured
"""
import json
import os
import re

from observability import get_logger
from prompts.response_model import TranslationResult

logger = get_logger(__name__)


class TranslationMixin:
    """中文标识符 → 英文翻译（LLM + 缓存 + sanitize + 拼音降级）"""

    # ==================== C4: 英文翻译 + C4-1: 幂等性保障 ====================

    @staticmethod
    def _sanitize_en(name: str) -> str:
        """LLM 输出后强制清洗，确保合法 Python identifier。"""
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '', name.replace(' ', '_'))
        if not sanitized or sanitized[0].isdigit():
            sanitized = '_' + sanitized
        return sanitized

    @staticmethod
    def _load_translation_cache(excel_path: str) -> dict:
        """从 Excel 同级目录读取翻译缓存。"""
        cache_path = os.path.join(os.path.dirname(excel_path), "translation_cache.json")
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                logger.warning("读取翻译缓存失败: %s", cache_path, exc_info=True)
        return {}

    @staticmethod
    def _save_translation_cache(excel_path: str, cache: dict) -> None:
        """保存翻译缓存到 Excel 同级目录。"""
        cache_path = os.path.join(os.path.dirname(excel_path), "translation_cache.json")
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            logger.warning("保存翻译缓存失败: %s", cache_path, exc_info=True)

    @staticmethod
    def _pinyin_fallback(text: str) -> str:
        """拼音首字母缩写 Fallback（LLM 翻译失败时使用）。"""
        try:
            from pypinyin import lazy_pinyin
            return ''.join(w[0].upper() for w in lazy_pinyin(text) if w)
        except ImportError:
            # pypinyin 未安装时用简单 hash 兜底
            import hashlib
            return 'M' + hashlib.md5(text.encode()).hexdigest()[:7].upper()

    def _translate_to_en(self, excel_path: str, rows: list[dict]) -> dict:
        """批量翻译 feature/story/title 为英文，带缓存 + sanitize + 降级。

        Returns:
            {"feature_en": {中文: 英文}, "story_en": {...}, "title_en": {...}}
        """
        # 收集待翻译文本
        features = list(dict.fromkeys(r["feature"] for r in rows if r.get("feature")))
        stories = list(dict.fromkeys(r["story"] for r in rows if r.get("story")))
        titles = list(dict.fromkeys(r["title"] for r in rows if r.get("title")))

        # 查缓存
        cache = self._load_translation_cache(excel_path)
        cache_fe = cache.get("feature_en", {})
        cache_st = cache.get("story_en", {})
        cache_ti = cache.get("title_en", {})

        uncached_fe = [f for f in features if f not in cache_fe]
        uncached_st = [s for s in stories if s not in cache_st]
        uncached_ti = [t for t in titles if t not in cache_ti]

        all_uncached = uncached_fe + uncached_st + uncached_ti

        if all_uncached:
            logger.info("\n🌐 翻译 %d 条中文标识符...", len(all_uncached))
            try:
                from prompts.extraction_prompts import translate_to_en_prompt
                prompt = translate_to_en_prompt()
                result = self._invoke_structured(prompt, TranslationResult,
                    method="json_mode",
                    features=json.dumps(uncached_fe, ensure_ascii=False),
                    stories=json.dumps(uncached_st, ensure_ascii=False),
                    titles=json.dumps(uncached_ti, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning("LLM 翻译失败，全部使用拼音 Fallback: %s", e)
                result = None

            if result and isinstance(result, TranslationResult):
                for cn, en in result.feature_en.items():
                    cache_fe[cn] = self._sanitize_en(en)
                for cn, en in result.story_en.items():
                    cache_st[cn] = self._sanitize_en(en)
                for cn, en in result.title_en.items():
                    cache_ti[cn] = self._sanitize_en(en)

            # 拼音 Fallback：LLM 未返回或翻译缺失的条目
            for cn in uncached_fe:
                if cn not in cache_fe:
                    cache_fe[cn] = self._sanitize_en(self._pinyin_fallback(cn))
                    logger.warning("拼音 Fallback: feature '%s' → '%s'", cn, cache_fe[cn])
            for cn in uncached_st:
                if cn not in cache_st:
                    cache_st[cn] = self._sanitize_en(self._pinyin_fallback(cn))
                    logger.warning("拼音 Fallback: story '%s' → '%s'", cn, cache_st[cn])
            for cn in uncached_ti:
                if cn not in cache_ti:
                    cache_ti[cn] = self._sanitize_en(self._pinyin_fallback(cn))
                    logger.warning("拼音 Fallback: title '%s' → '%s'", cn, cache_ti[cn])

            # 存缓存
            cache["feature_en"] = cache_fe
            cache["story_en"] = cache_st
            cache["title_en"] = cache_ti
            self._save_translation_cache(excel_path, cache)

        return {
            "feature_en": cache_fe,
            "story_en": cache_st,
            "title_en": cache_ti,
        }
