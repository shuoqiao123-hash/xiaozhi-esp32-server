#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
LLM + RAG 完整流程测试脚本
测试流程: 检索知识库 → 构建上下文 → 调用LLM → 生成回答
"""

import sys
import os
import yaml

# 添加项目路径
sys.path.append(os.path.join(os.path.dirname(__file__), 'main', 'xiaozhi-server'))

from rag.chroma_rag import ChromaRAG

# 避免导入 llm.py,直接读取配置后手动创建 LLM 实例


def load_local_config():
    """加载本地配置文件,不调用API"""
    # 获取项目根目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.join(script_dir, 'main', 'xiaozhi-server')
    
    # 读取默认配置 (在项目根目录)
    default_config_path = os.path.join(project_dir, "config.yaml")
    if os.path.exists(default_config_path):
        with open(default_config_path, "r", encoding="utf-8") as f:
            default_config = yaml.safe_load(f)
    else:
        default_config = {}
    
    # 读取自定义配置 (在data文件夹)
    custom_config_path = os.path.join(project_dir, "data/.config.yaml")
    if os.path.exists(custom_config_path):
        with open(custom_config_path, "r", encoding="utf-8") as f:
            custom_config = yaml.safe_load(f)
    else:
        custom_config = {}
    
    # 合并配置
    config = {**default_config, **custom_config}
    
    return config


def test_llm_rag_pipeline():
    """测试LLM+RAG完整流程"""
    
    print("=" * 80)
    print("LLM + RAG 完整流程测试")
    print("=" * 80)
    
    # ========== 步骤1: 加载配置 ==========
    print("\n[1/5] 加载配置...")
    try:
        config = load_local_config()
        print("✓ 本地配置加载成功")
    except Exception as e:
        print(f"✗ 配置加载失败: {str(e)}")
        print("  说明: 这个错误不影响RAG检索测试,但会影响LLM测试")
        print("  如果只需要测试RAG检索,可以继续...")
        config = None
    
    # ========== 步骤2: 初始化ChromaRAG ==========
    print("\n[2/5] 初始化ChromaRAG...")
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
            return False
        
        # 查找本地模型 (bge-large-zh-v1.5)
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
        
        rag = ChromaRAG(persist_directory=chroma_path, model_path=model_path)
        print("✓ ChromaRAG初始化成功")
        
    except Exception as e:
        print(f"✗ ChromaRAG初始化失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    
    # ========== 步骤3: 初始化LLM ==========
    print("\n[3/5] 初始化LLM...")
    try:
        if config is None:
            print("⚠️  未加载配置,跳过LLM初始化")
            print("  如果需要测试LLM+RAG完整流程,请确保配置文件(data/.config.yaml)正确")
            llm = None
        else:
            # 手动创建 LLM 实例,避免导入 llm.py 触发配置加载
            llm_type = config.get('selected_module', {}).get('LLM', '')
            if not llm_type:
                print("⚠️  未找到LLM配置,跳过LLM初始化")
                llm = None
            else:
                print(f"  尝试初始化LLM: {llm_type}")
                # 直接导入LLM模块,不通过 llm.py
                llm_module_path = f"core.providers.llm.{llm_type}.{llm_type}"
                try:
                    import importlib
                    llm_module = importlib.import_module(llm_module_path)
                    llm = llm_module.LLMProvider(config)
                    print("✓ LLM初始化成功")
                except Exception as e:
                    print(f"✗ LLM初始化失败: {str(e)}")
                    print("  说明: LLM初始化失败不影响RAG检索测试")
                    llm = None
    except Exception as e:
        print(f"✗ LLM初始化失败: {str(e)}")
        print("  说明: LLM初始化失败不影响RAG检索测试")
        llm = None
    
    # ========== 步骤4: 测试检索 + 生成 ==========
    print("\n[4/5] 测试检索 + 生成流程...")
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
            # 步骤4.1: 检索知识库
            print(f"\n[4.1] 检索知识库...")
            docs, metadatas = rag.search(query, n_results=3)
            print(f"✓ 检索成功,找到 {len(docs)} 个相关文档:")
            
            for i, (doc, meta) in enumerate(zip(docs, metadatas), 1):
                print(f"  [{i}] 来源: {meta.get('source', '未知')}")
                print(f"      内容预览: {doc[:100]}...")
            
            # 步骤4.2: 构建上下文
            print(f"\n[4.2] 构建上下文...")
            context_text = "# 相关知识库内容\n\n"
            for i, (doc, meta) in enumerate(zip(docs, metadatas), 1):
                context_text += f"## 文档 {i}\n"
                context_text += f"来源: {meta.get('source', '未知')}\n"
                context_text += f"内容: {doc}\n\n"
            
            print(f"✓ 上下文构建完成 (约 {len(context_text)} 字符)")
            
            # 步骤4.3: 构建完整提示词
            print(f"\n[4.3] 构建完整提示词...")
            user_question = query
            
            # 构建RAG提示词模板
            prompt = f"""你是一个知识渊博的助手。请根据以下知识库内容回答用户的问题。

## 知识库内容:
{context_text}

## 用户问题:
{user_question}

## 要求:
1. 请基于知识库内容回答问题
2. 如果知识库中没有相关信息,请如实说明
3. 回答要简洁明了
4. 引用知识库中的具体内容

请开始回答:"""
            
            print(f"✓ 提示词构建完成")
            print(f"  提示词长度: {len(prompt)} 字符")
            
            # 步骤4.4: 调用LLM生成回答
            if llm is None:
                print(f"\n[4.4] 跳过LLM调用(未初始化)")
                print(f"  (只测试RAG检索,不调用LLM)")
                response = None
            else:
                print(f"\n[4.4] 调用LLM生成回答...")
                print(f"  (这可能需要几秒钟...)")
                
                response = llm.chat(prompt)
                
                print(f"\n✓ LLM生成回答成功!")
                print(f"\n{'=' * 80}")
                print("回答结果:")
                print(f"{'=' * 80}")
                print(response)
                print(f"{'=' * 80}")
            
            # 保存结果
            all_results.append({
                'query': query,
                'context_docs': docs,
                'context_metadatas': metadatas,
                'response': response
            })
            
        except Exception as e:
            print(f"✗ 流程执行失败: {str(e)}")
            import traceback
            traceback.print_exc()
            return False
    
    # ========== 步骤5: 总结 ==========
    print(f"\n{'=' * 80}")
    print("测试总结")
    print(f"{'=' * 80}")
    print(f"✓ 共测试 {len(test_queries)} 个查询")
    
    if llm is None:
        print(f"✓ RAG检索测试全部成功!")
        print(f"⚠️  LLM未初始化,跳过LLM+RAG完整流程测试")
        print(f"  如需测试完整流程,请确保配置文件(data/.config.yaml)正确")
    else:
        print(f"✓ LLM+RAG流程全部成功!")
    
    print(f"\n✓ 测试完成!")
    return True


if __name__ == "__main__":
    success = test_llm_rag_pipeline()
    sys.exit(0 if success else 1)
