#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ChromaRAG 检索效果测试脚本
"""

import sys
import os

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'main', 'xiaozhi-server'))

from rag.chroma_rag import ChromaRAG

def test_chroma_retrieval():
    """测试Chroma向量库检索效果"""
    
    print("=" * 70)
    print("ChromaRAG 检索效果测试")
    print("=" * 70)
    
    # 初始化RAG
    print("\n[1/3] 初始化ChromaRAG...")
    try:
        # 尝试不同的数据库路径
        chroma_paths = [
            "./chroma_db",
            "./data/chroma_db",
            "./main/xiaozhi-server/chroma_db",
            "./main/xiaozhi-server/data/chroma_db"
        ]
        
        chroma_path = None
        for path in chroma_paths:
            if os.path.exists(path):
                chroma_path = path
                print(f"✓ 找到ChromaDB目录: {os.path.abspath(path)}")
                break
        
        if chroma_path is None:
            print("✗ 未找到ChromaDB目录,请先建立向量库")
            print(f"  可能的目录: {chroma_paths}")
            return False
        
        # 尝试找到本地模型
        models_paths = [
            "./models/bge-large-zh-v1.5",
            "./main/xiaozhi-server/models/bge-large-zh-v1.5",
            "/root/spanish/main/xiaozhi-server/models/bge-large-zh-v1.5"
        ]
        
        model_path = None
        for path in models_paths:
            if os.path.exists(path):
                model_path = path
                print(f"✓ 找到本地模型: {os.path.abspath(path)}")
                break
        
        if model_path is None:
            print("✗ 未找到本地模型,请将模型放在models目录下")
            print(f"  可能的路径: {models_paths}")
            return False
        
        rag = ChromaRAG(persist_directory=chroma_path, model_path=model_path)
        print("✓ ChromaRAG初始化成功")
        
    except Exception as e:
        print(f"✗ ChromaRAG初始化失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    # 测试检索
    print("\n[2/3] 测试检索功能...")
    test_queries = [
        "无锡旅游景点",
        "水蜜桃特点",
        "无锡美食"
    ]
    
    all_results = []
    
    for query in test_queries:
        print(f"\n  查询: '{query}'")
        try:
            docs, metadatas = rag.search(query, n_results=3)
            
            print(f"  ✓ 检索成功,找到 {len(docs)} 个相关文档:")
            
            for i, (doc, metadata) in enumerate(zip(docs, metadatas), 1):
                print(f"\n    [{i}] 相似度文档")
                print(f"        来源: {metadata.get('source', '未知')}")
                print(f"        分类: {metadata.get('category', '未知')}")
                print(f"        内容预览: {doc[:100]}...")
                
                # 保存结果
                all_results.append({
                    'query': query,
                    'doc_index': i,
                    'document': doc,
                    'metadata': metadata
                })
                
        except Exception as e:
            print(f"  ✗ 检索失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    # 总结
    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)
    print(f"✓ 共测试 {len(test_queries)} 个查询")
    print(f"✓ 共检索到 {len(all_results)} 个相关文档")
    
    # 显示详细结果
    print("\n详细结果:")
    print("-" * 70)
    
    for result in all_results:
        print(f"\n查询: {result['query']}")
        print(f"文档来源: {result['metadata'].get('source', '未知')}")
        print(f"内容: {result['document'][:200]}...")
        print("-" * 70)
    
    print("\n✓ ChromaRAG检索测试完成!")
    return True

if __name__ == "__main__":
    success = test_chroma_retrieval()
    sys.exit(0 if success else 1)
