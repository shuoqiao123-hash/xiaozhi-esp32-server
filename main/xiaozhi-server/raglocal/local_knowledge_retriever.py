from typing import List
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from sentence_transformers import SentenceTransformer
DEFAULT_QDRANT_PATH = "/root/spanish/main/xiaozhi-server/data/qdrant_raglocal"
DEFAULT_COLLECTION_NAME = "knowledge_local"
DEFAULT_MODEL_PATH = "/root/spanish/main/xiaozhi-server/models/bge-large-zh-v1.5"
DEFAULT_EMBEDDING_DIMS = 1024
DEFAULT_TOP_K = 5
class LocalKnowledgeRetriever:
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
        self.qdrant_path = qdrant_path
        self.collection_name = collection_name
        self.model_path = model_path
        self.embedding_dims = embedding_dims
        self.top_k = top_k

        client_key = (self.qdrant_path,)
        if (
            LocalKnowledgeRetriever._shared_client is None
            or LocalKnowledgeRetriever._shared_client_key != client_key
        ):
            LocalKnowledgeRetriever._shared_client = QdrantClient(path=self.qdrant_path)
            LocalKnowledgeRetriever._shared_client_key = client_key
        model_key = (self.model_path, self.embedding_dims)
        if (
            LocalKnowledgeRetriever._shared_model is None
            or LocalKnowledgeRetriever._shared_model_key != model_key
        ):
            LocalKnowledgeRetriever._shared_model = SentenceTransformer(self.model_path)
            LocalKnowledgeRetriever._shared_model_key = model_key
        self.client = LocalKnowledgeRetriever._shared_client
        self.model = LocalKnowledgeRetriever._shared_model
        self._ensure_collection()
    def _ensure_collection(self):
        collections = self.client.get_collections().collections
        if any(col.name == self.collection_name for col in collections):
            return
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(size=self.embedding_dims, distance=Distance.COSINE, on_disk=True),
        )
    def search(self, query: str, top_k: int | None = None) -> List[dict]:
        query = (query or "").strip()
        if not query:
            return []
        limit = top_k or self.top_k
        query_vector = self.model.encode(query, normalize_embeddings=True).tolist()
        results = self.client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        ).points
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