#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
RAG 检索效果测试脚本 (不依赖配置系统)
只测试 ChromaRAG 的检索功能
"""

import sys
import os

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'main', 'xiaozhi-server'))

from rag.chroma_rag import ChromaRAG


def test_rag_only():
    """只测试RAG检索,不依赖配置系统"""
    
    print("=" * 80)
    print("RAG 检索效果测试 (独立测试,不依赖配置)")
    print("=" * 80)
    
    # ========== 步骤1: 初始化ChromaRAG ==========
    print("\n[1/2] 初始化ChromaRAG...")
    try:
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
            print("✗ 未找到ChromaDB目录")
            print(f"  请先运行 initialize_chroma_from_pdf.py 建立向量库")
            return False
        
        # 查找本地模型
        models_paths = [
            "./models/bge-large-zh-v1.5",
            "./main/xiaozhi-server/models/bge-large-zh-v1.5"
        ]
        
        model_path = None
        for path in models_paths:
            if os.path.exists(path):
                model_path = path
                print(f"✓ 找到本地模型目录: {os.path.abspath(path)}")
                break
        
        if model_path is None:
            print("✗ 未找到本地模型目录")
            print(f"  请检查以下路径是否存在:")
            for p in models_paths:
                print(f"    - {os.path.abspath(p)}")
            return False
        
        print(f"\n正在加载RAG模型(这可能需要一些时间)...")
        rag = ChromaRAG(persist_directory=chroma_path, model_path=model_path)
        print("✓ ChromaRAG初始化成功")
        
    except Exception as e:
        print(f"✗ ChromaRAG初始化失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    # ========== 步骤2: 测试检索 ==========
    print("\n[2/2] 测试检索功能...")
    print("-" * 80)
    
    test_queries = [
        "无锡旅游景点",
        "水蜜桃特点",
        "无锡美食"
    ]
    
    all_results = []
    
    for query in test_queries:
        print(f"\n{'=' * 80}")
        print(f"测试查询: '{query}'")
        print(f"{'=' * 80}")
        
        try:
            # 检索知识库
            print(f"\n正在检索知识库...")
            docs, metadatas = rag.search(query, n_results=3)
            print(f"✓ 检索成功,找到 {len(docs)} 个相关文档:")
            
            for i, (doc, meta) in enumerate(zip(docs, metadatas), 1):
                print(f"\n  [{i}] 来源: {meta.get('source', '未知')}")
                print(f"      页码: {meta.get('page', '未知')}")
                print(f"      分类: {meta.get('category', '未知')}")
                print(f"      内容预览: {doc[:150]}...")
            
            # 保存结果
            all_results.append({
                'query': query,
                'docs': docs,
                'metadatas': metadatas
            })
            
        except Exception as e:
            print(f"✗ 检索失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    # ========== 步骤3: 总结 ==========
    print(f"\n{'=' * 80}")
    print("测试总结")
    print(f"{'=' * 80}")
    print(f"✓ 共测试 {len(test_queries)} 个查询")
    print(f"✓ RAG检索测试全部成功!")
    
    print(f"\n详细结果:")
    print("-" * 80)
    
    for i, result in enumerate(all_results, 1):
        print(f"\n【查询 {i}】{result['query']}")
        print(f"  检索到的文档数: {len(result['docs'])}")
        for j, (doc, meta) in enumerate(zip(result['docs'], result['metadatas']), 1):
            print(f"    [{j}] {meta.get('source', '未知')} (第{meta.get('page', '未知')}页)")
            print(f"        {doc[:100]}...")
        print("-" * 80)
    
    print(f"\n✓ RAG检索测试完成!")
    print(f"\n注意: 本测试只测试RAG检索,不涉及LLM调用")
    print(f"如需测试LLM+RAG完整流程,请使用 test_llm_rag_pipeline.py")
    return True


if __name__ == "__main__":
    success = test_rag_only()
    sys.exit(0 if success else 1)
