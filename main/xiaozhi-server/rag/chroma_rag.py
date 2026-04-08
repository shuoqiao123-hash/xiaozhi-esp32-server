import chromadb
import os
from sentence_transformers import SentenceTransformer

class ChromaRAG:
    def __init__(self, persist_directory: str = "./chroma_db", model_path: str = None):
        """初始化 Chroma RAG
        
        Args:
            persist_directory: ChromaDB 存储目录
            model_path: Embedding 模型路径，如果为 None 则使用默认模型
        """
        # 如果指定了模型路径，使用本地模型
        if model_path and os.path.exists(model_path):
            print(f"使用本地模型: {model_path}")
            self.embedding_model = SentenceTransformer(model_path)
        else:
            # 使用默认模型
            default_model = 'BAAI/bge-large-zh-v1.5'
            print(f"使用模型: {default_model}")
            self.embedding_model = SentenceTransformer(default_model)
        
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.collection = self.client.get_or_create_collection(
            name="knowledge_base",
            metadata={"hnsw:space": "cosine"}
        )
    
    def add_documents(self, documents, metadatas=None, ids=None):
        """添加文档到向量库"""
        if ids is None:
            ids = [f"doc_{i}" for i in range(len(documents))]
        
        if metadatas is None:
            metadatas = [{} for _ in documents]
        
        embeddings = self.embedding_model.encode(documents).tolist()
        
        self.collection.add(
            documents=documents,
            embeddings=embeddings,
            ids=ids,
            metadatas=metadatas
        )
    
    def search(self, query: str, n_results: int = 3):
        """检索文档"""
        query_embedding = self.embedding_model.encode([query]).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=n_results
        )
        return results['documents'][0], results['metadatas'][0]
