import sys
import os

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.pdf_extractor import extract_text_from_directory
from rag.text_splitter import split_text
from rag.chroma_rag import ChromaRAG

def initialize_knowledge_base_from_pdf(pdf_dir: str, chroma_dir: str = "./chroma_db"):
    """从 PDF 初始化知识库"""
    print("=" * 50)
    print("正在提取 PDF 文本...")
    print("=" * 50)
    
    # 1. 提取 PDF 文本
    documents = extract_text_from_directory(pdf_dir)
    print(f"提取了 {len(documents)} 个 PDF 文档")
    
    # 2. 分块
    print("正在分块...")
    chunks = split_text(documents, chunk_size=500, chunk_overlap=50)
    print(f"分成了 {len(chunks)} 个块")
    
    # 3. 初始化 Chroma
    print("正在初始化 Chroma...")
    rag = ChromaRAG()
    
    # 4. 添加到 Chroma
    print("正在添加到 Chroma...")
    documents_list = [chunk['content'] for chunk in chunks]
    metadatas = [chunk['metadata'] for chunk in chunks]
    ids = [f"doc_{i}" for i in range(len(chunks))]
    
    rag.add_documents(documents_list, metadatas, ids)
    
    print("=" * 50)
    print("知识库初始化完成！")
    print("=" * 50)

if __name__ == "__main__":
    # PDF 文件目录
    pdf_dir = "./data/pdf_knowledge_base"
    
    # 初始化
    initialize_knowledge_base_from_pdf(pdf_dir)