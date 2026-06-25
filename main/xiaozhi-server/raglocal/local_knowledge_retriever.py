from typing import List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from sentence_transformers import SentenceTransformer

# 默认配置常量 / Default configuration constants
DEFAULT_QDRANT_PATH = "/root/spanish/main/xiaozhi-server/data/qdrant_raglocal_m3"
DEFAULT_COLLECTION_NAME = "knowledge_local_m3"
DEFAULT_MODEL_PATH = "/root/spanish/main/xiaozhi-server/models/bge-m3"
DEFAULT_EMBEDDING_DIMS = 1024
DEFAULT_TOP_K = 5

class LocalKnowledgeRetriever:
    """
    本地知识库检索器，使用Qdrant存储向量，SentenceTransformer生成嵌入。
    Local knowledge retriever using Qdrant for vector storage and SentenceTransformer for embeddings.
    """
    # 类级别共享客户端和模型（避免重复初始化） / Class-level shared client and model (avoid re-init)
    _shared_client = None
    _shared_client_key = None
    _shared_model = None
    _shared_model_key = None
    def __init__(
        self,
        qdrant_path: str = DEFAULT_QDRANT_PATH,
        collection_name: str = DEFAULT_COLLECTION_NAME,
        model_path: str = DEFAULT_MODEL_PATH,
        embedding_dims: int = DEFAULT_EMBEDDING_DIMS,
        top_k: int = DEFAULT_TOP_K,
    ):
        # 保存实例参数 / Store instance params
        self.qdrant_path = qdrant_path
        self.collection_name = collection_name
        self.model_path = model_path
        self.embedding_dims = embedding_dims
        self.top_k = top_k
        
        # 共享Qdrant客户端（基于路径） / Shared Qdrant client (by path)
        client_key = (self.qdrant_path,)
        if (
            LocalKnowledgeRetriever._shared_client is None
            or LocalKnowledgeRetriever._shared_client_key != client_key
        ):
            LocalKnowledgeRetriever._shared_client = QdrantClient(path=self.qdrant_path)
            LocalKnowledgeRetriever._shared_client_key = client_key
        
        # 共享Embedding模型（基于模型路径和维度） / Shared embedding model (by path and dims)
        model_key = (self.model_path, self.embedding_dims)
        if (
            LocalKnowledgeRetriever._shared_model is None
            or LocalKnowledgeRetriever._shared_model_key != model_key
        ):
            LocalKnowledgeRetriever._shared_model = SentenceTransformer(self.model_path)
            LocalKnowledgeRetriever._shared_model_key = model_key
        self.client = LocalKnowledgeRetriever._shared_client
        self.model = LocalKnowledgeRetriever._shared_model
        # 确保集合存在，若不存在则创建 / Ensure collection exists, create if not
        self._ensure_collection()

    def _ensure_collection(self):
        """检查并创建Qdrant集合（如不存在） / Check and create Qdrant collection if missing."""
        collections = self.client.get_collections().collections
        if any(col.name == self.collection_name for col in collections):
            return
        # 使用余弦相似度，向量存储于磁盘 / Use cosine distance, store vectors on disk
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.embedding_dims, distance=Distance.COSINE, on_disk=True),
        )

    def search(self, query: str, top_k: int | None = None) -> List[dict]:
        """
        检索与查询最相似的文档片段。
        Retrieve top-k document chunks most similar to the query.
        """
        query = (query or "").strip()
        if not query:
            return []
        limit = top_k or self.top_k
        # 生成查询向量并归一化 / Generate query embedding and normalize
        query_vector = self.model.encode(query, normalize_embeddings=True).tolist()
        
        # 执行向量检索，返回payload，不含向量本身 / Perform vector search, return payload without vectors
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        ).points

        # 提取结果，过滤空内容 / Extract results, filter empty content
        chunks = []
        for item in results:
            payload = item.payload or {}
            content = payload.get("content", "")
            if not content:
                continue
            chunks.append(
                {
                    "doc_id": payload.get("doc_id", ""),
                    "chunk_id": payload.get("chunk_id", ""),
                    "title": payload.get("title", ""),
                    "source": payload.get("source", ""),
                    "content": content,
                    "score": item.score,
                }
            )
        return chunks