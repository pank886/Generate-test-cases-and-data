"""LLM 客户端单例管理与通用调用封装。

2026-08-07 大文件拆分：自 ``agent_components/nodes.py`` 56–76（reload_llm/_get_llm）
与 920–1036（_invoke_think/_invoke_structured/_load_factory_methods）迁移。
``nodes.py`` 顶部 re-export ``reload_llm`` / ``_get_llm``，既有
``from agent_components.nodes import reload_llm, _get_llm`` 用法不变。
"""

import json
import threading
from typing import Callable, Optional, Type

from pydantic import BaseModel

import config
from observability import get_logger, log_thinking
from agent_components.llm.deepseek import DeepSeekChatOpenAI

logger = get_logger(__name__)


# 全局共享的 LLM 客户端单例（避免多个 ChatTestAgentGraph 实例重复创建）
_llm_instance: Optional[DeepSeekChatOpenAI] = None
_llm_lock = threading.Lock()


def reload_llm():
    """重置 LLM 单例，下次 _get_llm 调用时使用最新配置重建（支持热重载）。"""
    global _llm_instance
    with _llm_lock:
        _llm_instance = None


def _get_llm() -> DeepSeekChatOpenAI:
    global _llm_instance
    if _llm_instance is None:
        with _llm_lock:
            if _llm_instance is None:  # 双重检查锁，防并发竞态
                _llm_instance = DeepSeekChatOpenAI(
                    model=config.LLM_MODEL,
                    base_url=config.LLM_BASE_URL,
                    api_key=config.LLM_API_KEY(),
                    temperature=config.LLM_TEMPERATURE,
                    max_tokens=config.LLM_MAX_TOKENS,
                )
    return _llm_instance


def load_factory_methods() -> str:
    """数据工厂方法清单（prompt 注入文本）。

    薄壳：实现已归位 data_factory/registry.py（单一事实源 methods.yaml v2，
    目录+分类详情渲染、缓存、旧结构兼容均在 registry 内）。
    """
    from data_factory.registry import render_for_prompt
    return render_for_prompt()


def invoke_think(bound_llm, messages, max_retries: int | None = None,
                 label: str = "LLM", reasoning_label: str | None = None) -> str:
    """通用 thinking 调用：LLM 返回空 content 时复用同一输入有限重试。

    2026-08-03 P2：deepseek-v4-flash 偶发返回空 content（json.loads 报
    "Expecting value: line 1 column 1 (char 0)"）。空 content 时后续解析/校验
    都拿不到内容，故在此统一监测：复用同一份 messages 重试，重试耗尽仍空则抛错。

    所有涉及 LLM 输出的节点（thinking 模式手动解析 content 的节点）统一走此方法：
      - content 为空 → 重试（默认 config.MAX_RETRIES 次 = 重试 2 次）
      - content 非空 → 立即返回文本（解析/校验失败由调用方按既有逻辑处理）
      - 已有外层重试循环的调用方传 max_retries=0，避免双重重试

    Args:
        bound_llm: 已 bind 的 LLM 客户端（含 temperature / thinking / json 配置）
        messages: 一次构造好的 prompt 消息列表（重试时原样复用，即"复用概要输入"）
        max_retries: 空响应重试次数，None 用 config.MAX_RETRIES
        label: 日志中的节点名
        reasoning_label: 非 None 时采集 result.reasoning_content（单节点 thinking，
            thinking+json_object 场景 thinking 不进 content）落 thinking_trace.log，
            弥补单节点可观测性损失（2026-08-11）。

    Returns:
        非空响应文本

    Raises:
        RuntimeError: 连续 (max_retries+1) 次返回空 content
    """
    retries = config.MAX_RETRIES if max_retries is None else max_retries
    for attempt in range(retries + 1):
        result = bound_llm.invoke(messages)
        if reasoning_label:
            _rc = getattr(result, "reasoning_content", None)
            if _rc:
                log_thinking(f"{reasoning_label}_thinking", label, str(_rc),
                             prompt_label="reasoning_content")
        text = result.content if hasattr(result, "content") else str(result)
        if text and text.strip():
            return text
        if attempt < retries:
            logger.warning(
                "   ⚠️ %s 返回空 content（第 %d 次），复用同一输入重试第 %d 次",
                label, attempt + 1, attempt + 2)
    raise RuntimeError(f"{label} 连续 {retries + 1} 次返回空 content，已终止")


def invoke_structured(llm, prompt, model_class: Type[BaseModel], method_features: dict,
                      max_retries: int = config.MAX_RETRIES,
                      method: str = "function_calling",
                      thinking: bool = False,
                      temperature: float | None = None,
                      log_label: str = "",
                      pre_validate: Callable[[dict], dict] | None = None,
                      **kwargs) -> BaseModel:
    """调用 LLM 并校验结构化输出，失败时自动重试。

    Args:
        llm: 当前实例的 LLM 客户端（nodes.py 薄转发传入 self.llm）
        prompt: ChatPromptTemplate
        model_class: Pydantic 模型类
        method_features: METHOD_FEATURES 配置表（method 与 thinking 兼容性，保留在 nodes.py）
        max_retries: 最大重试次数（默认 2）
        method: 结构化输出方法，可选 "function_calling" / "json_mode" / "json_schema"
        thinking: 是否使用深度思考模式（由 method_features 判定兼容性）
        temperature: 温度参数，None 使用全局默认值
        log_label: 不为空时将原始输出写入 thinking_trace.log
        pre_validate: json_mode 下在 Pydantic 构造前对 dict 执行的回调（用于注入 _annotations 等元数据）
        **kwargs: prompt 模板变量
    """
    # 根据 method 特性配置 thinking 开关
    features = method_features.get(method)
    llm_kwargs = {}
    if features is None:
        logger.warning("未知 method '%s'，使用保守配置（禁用 thinking）", method)
        llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif not features["supports_thinking"]:
        if thinking:
            logger.warning("%s 不支持 thinking=True，已自动禁用 thinking", method)
        llm_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    elif thinking and config.ENABLE_THINKING:
        llm_kwargs["extra_body"] = {"thinking": {"type": "enabled"}}

    last_error = None
    # max_tokens 在 model_kwargs 中，仅温度走 bind
    _bind_kwargs: dict = {}
    if temperature is not None:
        _bind_kwargs["temperature"] = temperature
    _llm = llm.bind(**_bind_kwargs) if _bind_kwargs else llm
    # json_mode：不绑 pydantic（避免 chain 内部先校验、跳过 pre_validate 注入 _annotations），
    # 由下方 pre_validate + model_class(**result) 统一处理（2026-08-12 修复：{param} 标注注入失效）
    _schema = None if method == "json_mode" else model_class
    # chain 在重试间不变，只需构建一次
    chain = prompt | _llm.with_structured_output(
        _schema, method=method, **llm_kwargs
    )

    for attempt in range(1 + max_retries):
        try:
            result = chain.invoke(kwargs)
            if result is None:
                raise ValueError("LLM 返回了空结果（None）")
            # ── pre_validate 钩子：json_mode 下在 Pydantic 构造前修改 dict ──
            if pre_validate and isinstance(result, dict):
                result = pre_validate(result)
            if log_label:
                from observability import log_thinking
                _raw = result.model_dump() if hasattr(result, "model_dump") else str(result)
                log_thinking(log_label, "", f"shared_preconditions={len(_raw.get('shared_preconditions',[]))}条, test_cases={len(_raw.get('test_cases',[]))}条\n{json.dumps(_raw, indent=2, ensure_ascii=False)[:8000]}",
                             prompt_label=log_label)
            if isinstance(result, dict):
                result = model_class(**result)
            return result
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                logger.warning("输出校验失败，第 %d 次重试 (%s): %s",
                               attempt + 1, type(e).__name__, e, exc_info=True)

    raise RuntimeError(
        f"LLM 结构化输出校验失败（本调用内重试 {max_retries} 次，外层修复轮独立计数）: {last_error}"
    )
