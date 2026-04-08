from plugins_func.register import register_function, ToolType, ActionResponse, Action
from typing import TYPE_CHECKING
from rag.chroma_rag import ChromaRAG

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__

# 初始化 Chroma RAG
chroma_rag = ChromaRAG()

@register_function(
    "search_from_chroma", 
    {
        "type": "function",
        "function": {
            "name": "search_from_chroma",
            "description": "从本地向量知识库中查询信息，适用于无锡旅游、水蜜桃等相关问题",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "查询的问题"}
                },
                "required": ["question"],
            },
        },
    }, 
    ToolType.SYSTEM_CTL
)
def search_from_chroma(conn: "ConnectionHandler", question=None):
    """从 Chroma 向量库中检索信息"""
    if question and isinstance(question, str):
        try:
            # 检索
            documents, metadatas = chroma_rag.search(question, n_results=3)
            
            # 返回结果
            return ActionResponse(
                type=Action.RESPONSE,
                response={
                    "success": True,
                    "documents": documents,
                    "metadatas": metadatas
                }
            )
        except Exception as e:
            return ActionResponse(
                type=Action.RESPONSE,
                response={
                    "success": False,
                    "error": str(e)
                }
            )