import asyncio
import json
import traceback
from typing import Any

from mem0 import Memory
from mem0.configs.embeddings.base import BaseEmbedderConfig
from sentence_transformers import SentenceTransformer

from ..base import MemoryProviderBase, logger

TAG = __name__


class LocalBGEEmbedder:
    """封装本地模型，适配 Mem0。"""

    def __init__(self, model_path: str, device: str = "cpu", embedding_dims: int = 1024):
        self.model = SentenceTransformer(model_path, device=device)
        self.config = BaseEmbedderConfig(
            model=model_path,
            embedding_dims=embedding_dims,
            model_kwargs={"device": device},
        )

    def embed(self, text, memory_action=None):
        if isinstance(text, str):
            texts = [text]
            is_single = True
        else:
            texts = list(text)
            is_single = False

        embeddings = self.model.encode(texts, normalize_embeddings=True)
        embeddings = embeddings.tolist()
        return embeddings[0] if is_single else embeddings


class MemoryProvider(MemoryProviderBase):
    _shared_client = None
    _shared_client_key = None
    _shared_init_error = None

    def __init__(self, config, summary_memory=None):
        super().__init__(config)
        self.client = None
        self.use_mem0 = False

        self.model_path = config.get(
            "embedding_model_path",
            "/root/spanish/main/xiaozhi-server/models/bge-m3",
        )
        self.embedding_dims = int(config.get("embedding_model_dims", 1024))
        self.embedding_device = config.get("embedding_device", "cpu")
        self.qdrant_path = config.get("qdrant_path", "/root/spanish/main/xiaozhi-server/data/qdrant_storage_m3")
        self.collection_prefix = config.get("collection_prefix", "memories_m3")

        # llm_config = config.get("llm", {})
        llm_provider = config.get("provider", "openai")
        llm_inner_config = {'model': config.get("model"), 'openai_base_url': config.get("base_url"), 'api_key': config.get("api_key")}
        client_key = (
            self.qdrant_path,
            self.collection_prefix,
            self.embedding_dims,
            self.embedding_device,
            self.model_path,
            llm_provider,
            llm_inner_config.get("model"),
            llm_inner_config.get("openai_base_url"),
        )
        try:
            if (
                MemoryProvider._shared_client is not None
                and MemoryProvider._shared_client_key == client_key
            ):
                self.client = MemoryProvider._shared_client
                self.use_mem0 = True
                logger.bind(tag=TAG).info(
                    f"复用本地 Mem0 单例客户端: qdrant={self.qdrant_path}, model={self.model_path}"
                )
                return

            mem0_config = {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "path": self.qdrant_path,
                        "collection_name": self.collection_prefix,
                        "embedding_model_dims": self.embedding_dims,
                        "on_disk": True,
                    },
                },
                "llm": {
                    "provider": llm_provider,
                    "config": llm_inner_config,
                },
                "embedder": {
                    "provider": "huggingface",
                    "config": {
                        "model": self.model_path,
                        "embedding_dims": self.embedding_dims,
                        "model_kwargs": {"device": self.embedding_device},
                    },
                },
            }

            self.client = Memory.from_config(mem0_config)
            MemoryProvider._shared_client = self.client
            MemoryProvider._shared_client_key = client_key
            MemoryProvider._shared_init_error = None

            self.use_mem0 = True
            logger.bind(tag=TAG).info(
                f"本地 Mem0 初始化成功: qdrant={self.qdrant_path}, model={self.model_path}"
            )
        except Exception as e:
            MemoryProvider._shared_init_error = str(e)
            logger.bind(tag=TAG).error(f"本地 Mem0 初始化失败: {str(e)}")
            logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
            self.use_mem0 = False

    def _build_collection_name(self) -> str:
        role_id = getattr(self, "role_id", None) or "default"
        safe_role_id = "".join(
            c if c.isalnum() or c in ("_", "-") else "_" for c in str(role_id)
        )
        return f"{self.collection_prefix}_{safe_role_id}"

    def _extract_content(self, text: Any) -> Any:
        if not isinstance(text, str):
            return text

        try:
            stripped = text.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                data = json.loads(stripped)
                if isinstance(data, dict) and isinstance(data.get("content"), str):
                    return data["content"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        return text

    def _get_client_kwargs(self):
        return {
            "user_id": self.role_id,
    
        }

    async def save_memory(self, msgs, session_id=None):
        if not self.use_mem0:
            logger.bind(tag=TAG).warning("本地记忆未启用，跳过保存")
            return None
        if len(msgs) < 2:
            logger.bind(tag=TAG).info(f"本地记忆跳过保存，消息条数不足: {len(msgs)}")
            return None

        try:
            messages = []
            valid_roles = {"user"}

            for message in msgs:
                if message.role not in valid_roles:
                    continue

                content = self._extract_content(message.content)
                if not isinstance(content, str):
                    continue

                content = content.strip()
                if not content:
                    continue

                messages.append({"role": message.role, "content": content})

            if len(messages) < 2:
                logger.bind(tag=TAG).info("本地记忆跳过保存，有效消息不足")
                return None

            logger.bind(tag=TAG).info(
                f"开始保存本地记忆: role_id={self.role_id}, client_kwargs={self._get_client_kwargs()}, count={len(messages)}, content={json.dumps(messages, ensure_ascii=False)}"
            )

            result = await asyncio.to_thread(
                self.client.add,
                messages,
                **self._get_client_kwargs(),
            )
            visible_memories = await asyncio.to_thread(
                self.client.get_all,
                limit=20,
                **self._get_client_kwargs(),
            )
            logger.bind(tag=TAG).info(
                f"保存后本地记忆全量调试结果: role_id={self.role_id}, all_memories={visible_memories}"
            )
            logger.bind(tag=TAG).info(
                f"保存本地记忆成功: role_id={self.role_id}, result={result}"
            )
            return result
        except Exception as e:
            logger.bind(tag=TAG).error(f"保存本地记忆失败: {str(e)}")
            logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
            return None

    async def query_memory(self, query: str) -> str:
        if not self.use_mem0:
            logger.bind(tag=TAG).warning("本地记忆未启用，跳过查询")
            return ""
        try:
            if not getattr(self, "role_id", None):
                logger.bind(tag=TAG).warning("本地记忆查询跳过：role_id为空")
                return ""

            search_query = self._extract_content(query)
            if not isinstance(search_query, str):
                logger.bind(tag=TAG).warning(f"本地记忆查询跳过：query类型异常 {type(search_query)}")
                return ""

            search_query = search_query.strip()
            if not search_query:
                logger.bind(tag=TAG).info("本地记忆查询跳过：query为空")
                return ""

            logger.bind(tag=TAG).info(
                f"开始查询本地记忆: role_id={self.role_id}, client_kwargs={self._get_client_kwargs()}, query={search_query}"
            )

            results = await asyncio.to_thread(
                self.client.search,
                search_query,
                limit=10,
                threshold=0.0,
                rerank=False,
                **self._get_client_kwargs(),
            )
            logger.bind(tag=TAG).info(
                f"本地记忆原始查询结果: role_id={self.role_id}, query={search_query}, raw_results={results}"
            )
            all_memories = await asyncio.to_thread(
                self.client.get_all,
                limit=20,
                **self._get_client_kwargs(),
            )
            logger.bind(tag=TAG).info(
                f"本地记忆全量调试结果: role_id={self.role_id}, all_memories={all_memories}"
            )
            direct_vector_results = await asyncio.to_thread(
                self.client._search_vector_store,
                search_query,
                self._get_client_kwargs(),
                10,
                0.0,
            )
            logger.bind(tag=TAG).info(
                f"本地记忆底层向量查询结果: role_id={self.role_id}, query={search_query}, vector_results={direct_vector_results}"
            )
            if not results or "results" not in results:
                logger.bind(tag=TAG).info(
                    f"查询本地记忆成功但无结果: role_id={self.role_id}, raw_results={results}"
                )
                return ""

            memories = []
            for entry in results["results"]:
                timestamp = entry.get("updated_at") or entry.get("created_at", "")
                formatted_time = ""
                if timestamp:
                    try:
                        dt = str(timestamp).split(".")[0]
                        formatted_time = dt.replace("T", " ")
                    except Exception:
                        formatted_time = str(timestamp)

                memory = entry.get("memory") or entry.get("content", "")
                if not memory:
                    continue

                if formatted_time:
                    memories.append((str(timestamp), f"[{formatted_time}] {memory}"))
                else:
                    memories.append(("", str(memory)))

            memories.sort(key=lambda x: x[0], reverse=True)
            memories_str = "\n".join(f"- {memory[1]}" for memory in memories)
            logger.bind(tag=TAG).info(
                f"查询本地记忆成功: role_id={self.role_id}, hit_count={len(memories)}"
            )
            return memories_str
        except Exception as e:
            logger.bind(tag=TAG).error(f"查询本地记忆失败: {str(e)}")
            logger.bind(tag=TAG).error(f"详细错误: {traceback.format_exc()}")
            return ""
