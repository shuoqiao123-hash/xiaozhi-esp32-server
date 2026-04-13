import argparse
import hashlib
import json
import os
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer
from rag.pdf_extractor import extract_text_from_pdf
from rag.text_splitter import split_text
DEFAULT_QDRANT_PATH = "/root/spanish/main/xiaozhi-server/data/qdrant_storage"
DEFAULT_COLLECTION_NAME = "knowledge_local"
DEFAULT_MODEL_PATH = "/root/spanish/main/xiaozhi-server/models/bge-large-zh-v1.5"
DEFAULT_EMBEDDING_DIMS = 1024
DEFAULT_CHUNK_SIZE = 500
DEFAULT_CHUNK_OVERLAP = 80

def parse_args():
    parser = argparse.ArgumentParser(description="Import a PDF into local Qdrant knowledge collection")
    parser.add_argument("--pdf", required=True, help="Absolute path to PDF file")
    parser.add_argument("--doc-id", required=True, help="Unique document id in Qdrant payload")
    parser.add_argument("--title", required=True, help="Document title stored in payload")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION_NAME, help="Qdrant collection name")
    parser.add_argument("--qdrant-path", default=DEFAULT_QDRANT_PATH, help="Local Qdrant storage path")
    parser.add_argument("--model-path", default=DEFAULT_MODEL_PATH, help="SentenceTransformer model path")
    parser.add_argument("--embedding-dims", type=int, default=DEFAULT_EMBEDDING_DIMS, help="Embedding vector size")
    parser.add_argument("--chunk-size", type=int, default=DEFAULT_CHUNK_SIZE, help="Chunk size")
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP, help="Chunk overlap")
    return parser.parse_args()
def build_documents(pdf_path: str, doc_id: str, title: str):
    pages = extract_text_from_pdf(pdf_path)
    documents = []
    for page in pages:
        documents.append(
            {
                "content": page["text"],
                "metadata": {
                    "doc_id": doc_id,
                    "title": title,
                    "source": pdf_path,
                },
            }
        )
    return documents

def ensure_collection(client: QdrantClient, collection_name: str, embedding_dims: int):
    collections = client.get_collections().collections
    if any(col.name == collection_name for col in collections):
        return
    client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=embedding_dims, distance=Distance.COSINE, on_disk=True),
    )

def reset_doc_points(client: QdrantClient, collection_name: str, doc_id: str):
    points, _ = client.scroll(
        collection_name=collection_name,
        scroll_filter=None,
        limit=10000,
        with_payload=True,
        with_vectors=False,
    )
    target_ids = [point.id for point in points if (point.payload or {}).get("doc_id") == doc_id]
    if target_ids:
        client.delete(collection_name=collection_name, points_selector=target_ids)
def build_point_id(doc_id: str, chunk_id: str) -> int:
    digest = hashlib.md5(f"{doc_id}:{chunk_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)

def main():
    args = parse_args()
    pdf_path = os.path.abspath(args.pdf)
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    documents = build_documents(pdf_path, args.doc_id, args.title)
    chunks = split_text(documents, chunk_size=args.chunk_size, chunk_overlap=args.chunk_overlap)
    if not chunks:
        raise RuntimeError("No chunks generated from PDF")
    model = SentenceTransformer(args.model_path)
    client = QdrantClient(path=args.qdrant_path)
    ensure_collection(client, args.collection, args.embedding_dims)
    reset_doc_points(client, args.collection, args.doc_id)
    texts = [chunk["content"] for chunk in chunks]
    vectors = model.encode(texts, normalize_embeddings=True).tolist()

    points = []
    for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
        chunk_id = f"{args.doc_id}_{idx:04d}"
        payload = {
            "doc_id": args.doc_id,
            "chunk_id": chunk_id,
            "title": chunk["metadata"]["title"],
            "source": chunk["metadata"]["source"],
            "content": chunk["content"],
        }
        points.append(PointStruct(id=build_point_id(args.doc_id, chunk_id), vector=vector, payload=payload))
    client.upsert(collection_name=args.collection, points=points)
    preview = {
        "pdf_path": pdf_path,
        "collection_name": args.collection,
        "doc_id": args.doc_id,
        "title": args.title,
        "chunk_count": len(points),
        "first_chunk": points[0].payload,
    }
    print(json.dumps(preview, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
