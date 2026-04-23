import httpx
import openai
import time
import uuid
from openai.types import CompletionUsage

from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.llm.base import LLMProviderBase

TAG = __name__
logger = setup_logging()


def _perf_trace_id(session_id):
    return session_id or uuid.uuid4().hex[:8]


class LLMProvider(LLMProviderBase):
    def __init__(self, config):
        self.api_key = config.get("api_key")
        self.endpoint = (config.get("endpoint") or config.get("azure_endpoint") or "").rstrip("/")
        self.api_version = config.get("api_version", "2024-10-21")
        self.deployment_name = (
            config.get("deployment_name")
            or config.get("azure_deployment")
            or config.get("model_name")
        )
        self.model_name = self.deployment_name

        timeout = config.get("timeout", 300)
        self.timeout = int(timeout) if timeout else 300

        param_defaults = {
            "max_tokens": int,
            "temperature": lambda x: round(float(x), 1),
            "top_p": lambda x: round(float(x), 1),
            "frequency_penalty": lambda x: round(float(x), 1),
        }
        self.thinking = config.get("thinking", False)

        for param, converter in param_defaults.items():
            value = config.get(param)
            try:
                setattr(
                    self,
                    param,
                    converter(value) if value not in (None, "") else None,
                )
            except (ValueError, TypeError):
                setattr(self, param, None)

        logger.debug(
            f"Azure意图识别参数初始化: {self.temperature}, {self.max_tokens}, {self.top_p}, {self.frequency_penalty}"
        )

        model_key_msg = check_model_key("LLM", self.api_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

        if not self.endpoint:
            raise ValueError("Azure OpenAI 配置缺少 endpoint / azure_endpoint")
        if not self.deployment_name:
            raise ValueError("Azure OpenAI 配置缺少 deployment_name / azure_deployment / model_name")

        self.client = openai.AzureOpenAI(
            api_key=self.api_key,
            azure_endpoint=self.endpoint,
            api_version=self.api_version,
            timeout=httpx.Timeout(self.timeout),
        )

        logger.bind(tag=TAG).info(
            f"Azure OpenAI LLMProvider 初始化: deployment={self.deployment_name}, endpoint={self.endpoint}, api_version={self.api_version}, timeout={self.timeout}"
        )

    @staticmethod
    def normalize_dialogue(dialogue):
        """自动修复 dialogue 中缺失 content 的消息"""
        for msg in dialogue:
            if "role" in msg and "content" not in msg:
                msg["content"] = ""
        return dialogue

    def _build_request_params(self, dialogue, functions=None, **kwargs):
        request_params = {
            "model": self.deployment_name,
            "messages": dialogue,
            "stream": True,
        }

        if functions is not None:
            request_params["tools"] = functions

        optional_params = {
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", self.top_p),
            "frequency_penalty": kwargs.get("frequency_penalty", self.frequency_penalty),
        }

        for key, value in optional_params.items():
            if value is not None:
                request_params[key] = value

        return request_params

    def _create_stream(self, request_params):
        extra_body_params = {"enable_thinking": bool(self.thinking)}

        try:
            return self.client.chat.completions.create(
                **request_params,
                extra_body=extra_body_params,
            )
        except TypeError as e:
            if "extra_body" not in str(e):
                raise
            logger.bind(tag=TAG).warning(
                "当前 Azure OpenAI SDK/服务不支持 extra_body，忽略 enable_thinking 参数继续请求"
            )
            return self.client.chat.completions.create(**request_params)

    def response(self, session_id, dialogue, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)
        trace_id = _perf_trace_id(session_id)

        request_build_start = time.perf_counter()
        request_params = self._build_request_params(dialogue, None, **kwargs)

        logger.bind(tag=TAG).info(
            f"性能埋点 trace_id={trace_id} 阶段=AzureLLM请求准备完成 对话条数={len(dialogue)} 工具数=0 耗时={time.perf_counter() - request_build_start:.3f}秒"
        )

        llm_connect_start_time = time.perf_counter()
        responses = self._create_stream(request_params)
        stream_ready_time = time.perf_counter()

        logger.bind(tag=TAG).info(
            f"性能埋点 trace_id={trace_id} 阶段=Azure普通问答LLM流返回 耗时={stream_ready_time - llm_connect_start_time:.3f}秒"
        )

        is_active = True
        first_token_logged = False
        first_token_start = stream_ready_time
        first_sentence_logged = False
        sentence_buffer = ""

        for chunk in responses:
            try:
                delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                content = getattr(delta, "content", "") if delta else ""
            except IndexError:
                content = ""

            if content:
                if "💭" in content:
                    is_active = False
                    content = content.split("💭")[0]
                if "out" in content:
                    is_active = True
                    content = content.split("out")[-1]

                if is_active:
                    if content and not first_token_logged:
                        logger.bind(tag=TAG).info(
                            f"性能埋点 trace_id={trace_id} 阶段=Azure普通问答首Token 耗时={time.perf_counter() - first_token_start:.3f}秒"
                        )
                        first_token_logged = True

                    sentence_buffer += content
                    if not first_sentence_logged and any(
                        p in sentence_buffer for p in ["。", "！", "？", ".", "!", "?"]
                    ):
                        logger.bind(tag=TAG).info(
                            f"性能埋点 trace_id={trace_id} 阶段=Azure普通问答首句完成 耗时={time.perf_counter() - first_token_start:.3f}秒"
                        )
                        first_sentence_logged = True

                    yield content

    def response_with_functions(self, session_id, dialogue, functions=None, **kwargs):
        dialogue = self.normalize_dialogue(dialogue)
        trace_id = _perf_trace_id(session_id)

        request_build_start = time.perf_counter()
        request_params = self._build_request_params(dialogue, functions, **kwargs)

        logger.bind(tag=TAG).info(
            f"性能埋点 trace_id={trace_id} 阶段=Azure工具问答LLM请求准备完成 对话条数={len(dialogue)} 工具数={len(functions or [])} 耗时={time.perf_counter() - request_build_start:.3f}秒"
        )

        llm_connect_start_time = time.perf_counter()
        stream = self._create_stream(request_params)
        stream_ready_time = time.perf_counter()

        logger.bind(tag=TAG).info(
            f"性能埋点 trace_id={trace_id} 阶段=Azure工具问答LLM流返回 耗时={stream_ready_time - llm_connect_start_time:.3f}秒"
        )

        first_token_logged = False
        first_token_start = stream_ready_time
        first_sentence_logged = False
        sentence_buffer = ""

        for chunk in stream:
            if getattr(chunk, "choices", None):
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", "")
                tool_calls = getattr(delta, "tool_calls", None)

                if content and not first_token_logged:
                    logger.bind(tag=TAG).info(
                        f"性能埋点 trace_id={trace_id} 阶段=Azure工具问答首Token 耗时={time.perf_counter() - first_token_start:.3f}秒"
                    )
                    first_token_logged = True

                if content:
                    sentence_buffer += content
                    if not first_sentence_logged and any(
                        p in sentence_buffer for p in ["。", "！", "？", ".", "!", "?"]
                    ):
                        logger.bind(tag=TAG).info(
                            f"性能埋点 trace_id={trace_id} 阶段=Azure工具问答首句完成 耗时={time.perf_counter() - first_token_start:.3f}秒"
                        )
                        first_sentence_logged = True

                yield content, tool_calls

            elif isinstance(getattr(chunk, "usage", None), CompletionUsage):
                usage_info = getattr(chunk, "usage", None)
                logger.bind(tag=TAG).info(
                    f"Token 消耗：输入 {getattr(usage_info, 'prompt_tokens', '未知')}，"
                    f"输出 {getattr(usage_info, 'completion_tokens', '未知')}，"
                    f"共计 {getattr(usage_info, 'total_tokens', '未知')}"
                )