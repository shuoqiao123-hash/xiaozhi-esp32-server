import sys
import os

# 添加项目路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag.pdf_extractor import extract_text_from_directory
from rag.text_splitter import split_text
from rag.chroma_rag import ChromaRAG


def initialize_knowledge_base_from_pdfs(pdf_dir="./data/pdf_knowledge_base", chroma_dir="./chroma_db"):
    """
    从 PDF 目录初始化知识库（支持任意数量的 PDF 文件）
    
    Args:
        pdf_dir: PDF 文件所在目录
        chroma_dir: ChromaDB 存储目录
    """
    print("=" * 60)
    print("正在从 PDF 目录初始化知识库...")
    print("=" * 60)
    
    # 1. 检查目录
    if not os.path.exists(pdf_dir):
        print(f"✗ PDF 目录不存在: {pdf_dir}")
        print(f"正在创建目录: {pdf_dir}")
        os.makedirs(pdf_dir, exist_ok=True)
        return False
    
    # 2. 查找 PDF 文件
    pdf_files = [f for f in os.listdir(pdf_dir) if f.endswith('.pdf')]
    
    if not pdf_files:
        print(f"✗ PDF 目录中没有找到 PDF 文件: {pdf_dir}")
        print(f"请将 PDF 文件复制到: {os.path.abspath(pdf_dir)}")
        return False
    
    print(f"\n✓ 找到 {len(pdf_files)} 个 PDF 文件:")
    for pdf_file in pdf_files:
        print(f"  - {pdf_file}")
    
    # 3. 提取 PDF 文本
    print("\n[1/3] 正在提取 PDF 文本...")
    print("-" * 60)
    
    documents = extract_text_from_directory(pdf_dir)
    
    if not documents:
        print("✗ 没有提取到任何文本")
        return False
    
    print(f"✓ 已提取 {len(documents)} 个文档")
    
    # 4. 分块
    print("\n[2/3] 正在分块...")
    print("-" * 60)
    
    chunks = split_text(documents, chunk_size=500, chunk_overlap=50)
    print(f"✓ 已分成 {len(chunks)} 个文本块")
    print(f"  - 每块大小: 500 字符")
    print(f"  - 重叠大小: 50 字符")
    
    # 5. 初始化 Chroma 并添加文档
    print("\n[3/3] 正在初始化 Chroma 并添加文档...")
    print("-" * 60)
    
    try:
        # 模型路径配置
        # 方式 1: 使用本地模型（推荐，不需要网络）
        # model_path = "./models/bge-large-zh-v1.5"
        # 方式 2: 自动下载模型（需要网络）
        model_path = "./models/bge-large-zh-v1.5"  # 如果使用本地模型，改成 "./models/bge-large-zh-v1.5"
        
        rag = ChromaRAG(persist_directory=chroma_dir, model_path=model_path)
        
        documents_list = [chunk['content'] for chunk in chunks]
        metadatas = [chunk['metadata'] for chunk in chunks]
        ids = [f"doc_{i}" for i in range(len(chunks))]
        
        rag.add_documents(documents_list, metadatas, ids)
        
        print("✓ 知识库初始化完成！")
        print(f"  - PDF 目录: {os.path.abspath(pdf_dir)}")
        print(f"  - ChromaDB 存储目录: {os.path.abspath(chroma_dir)}")
        print(f"  - PDF 文件数量: {len(pdf_files)}")
        print(f"  - 文档数量: {len(documents)}")
        print(f"  - 文本块数量: {len(chunks)}")
        
        print("\n" + "=" * 60)
        print("知识库初始化成功！")
        print("=" * 60)
        print(f"\n你可以通过以下方式查询知识库:")
        print(f"  1. 使用 search_from_ragflow 插件")
        print(f"  2. 直接调用 ChromaRAG.search() 方法")
        print(f"\n示例查询:")
        print(f"  from rag.chroma_rag import ChromaRAG")
        print(f"  rag = ChromaRAG()")
        print(f"  docs, metadatas = rag.search('你的问题', n_results=3)")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print(f"✗ 初始化失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    # 配置 PDF 目录
    pdf_dir = "./data/pdf_knowledge_base"
    
    # ChromaDB 存储目录
    chroma_dir = "./chroma_db"
    
    # 运行初始化
    success = initialize_knowledge_base_from_pdfs(pdf_dir, chroma_dir)
    
    if success:
        print("\n✓ 知识库已成功初始化！")
        sys.exit(0)
    else:
        print("\n✗ 知识库初始化失败！")
        sys.exit(1)
