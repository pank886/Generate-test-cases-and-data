"""Ollama Embedding 端点降级适配层。

兼容层：优先调用新版 /api/embed 端点。
若 Ollama 服务端版本过旧（v0.1.x）返回 404，自动降级到已废弃的
/api/embeddings 端点，对上层调用方完全透明。

用法 — 直接替换 langchain_ollama.OllamaEmbeddings：
    from agent_components.fallback_embeddings import FallbackOllamaEmbeddings
    embeddings = FallbackOllamaEmbeddings(model="bge-m3", base_url="http://localhost:11434")
"""

import logging

from langchain_ollama.embeddings import OllamaEmbeddings

logger = logging.getLogger(__name__)

# -- 模块级降级状态（避免 Pydantic 字段机制干扰） --
_fallback_state: dict[str, bool] = {}
_fallback_warned: dict[str, bool] = {}


def _should_use_old_api(base_url: str | None) -> bool:
    return _fallback_state.get((base_url or "http://localhost:11434").rstrip("/"), False)


def _mark_old_api(base_url: str | None) -> None:
    key = (base_url or "http://localhost:11434").rstrip("/")
    _fallback_state[key] = True
    if not _fallback_warned.get(key):
        _fallback_warned[key] = True
        logger.warning(
            "Ollama 服务端 (%s) 不支持 /api/embed（版本过旧），"
            "已自动降级为 /api/embeddings。建议升级 Ollama 服务端以获得更好性能。",
            key,
        )


class FallbackOllamaEmbeddings(OllamaEmbeddings):
    """OllamaEmbeddings 降级版：/api/embed 404 时自动切到 /api/embeddings。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if _should_use_old_api(self.base_url):
            return self._embed_via_old_api(texts)

        try:
            return super().embed_documents(texts)
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status != 404:
                raise
            _mark_old_api(self.base_url)
            return self._embed_via_old_api(texts)

    def _embed_via_old_api(self, texts: list[str]) -> list[list[float]]:
        """使用 Ollama 客户端内置的 /api/embeddings 调用（比裸 httpx 更稳）。"""
        embeddings: list[list[float]] = []
        for text in texts:
            result = self._client.embeddings(
                model=self.model,
                prompt=text,
                options=self._default_params,
                keep_alive=self.keep_alive,
            )
            embeddings.append(result["embedding"])
        return embeddings

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        if _should_use_old_api(self.base_url):
            return await self._aembed_via_old_api(texts)

        try:
            return await super().aembed_documents(texts)
        except Exception as e:
            status = getattr(e, "status_code", None)
            if status != 404:
                raise
            _mark_old_api(self.base_url)
            return await self._aembed_via_old_api(texts)

    async def _aembed_via_old_api(self, texts: list[str]) -> list[list[float]]:
        """异步降级到 /api/embeddings。"""
        embeddings: list[list[float]] = []
        for text in texts:
            result = await self._async_client.embeddings(
                model=self.model,
                prompt=text,
                options=self._default_params,
                keep_alive=self.keep_alive,
            )
            embeddings.append(result["embedding"])
        return embeddings
