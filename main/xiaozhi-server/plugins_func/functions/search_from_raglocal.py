from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from raglocal.local_knowledge_retriever import LocalKnowledgeRetriever
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.connection import ConnectionHandler
TAG = __name__
logger = setup_logging()

SEARCH_FROM_RAGLOCAL_FUNCTION_DESC = {
    "type": "function",
    "function": {
        "name": "search_from_raglocal",
        "description": "Consulta la información de la base de conocimiento local cuando el usuario pregunta sobre el conocimiento relevante de la base de conocimiento española o local",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string", "description": "Preguntas de consulta"}},
            "required": ["question"],
        },
    },
}

@register_function(
    "search_from_raglocal", SEARCH_FROM_RAGLOCAL_FUNCTION_DESC, ToolType.SYSTEM_CTL
)
def search_from_raglocal(conn: "ConnectionHandler", question=None):
    question = question if isinstance(question, str) else (str(question) if question is not None else "")
    question = question.strip()
    if not question:
        return ActionResponse(Action.RESPONSE, None, "知识库查询问题不能为空。")

    logger.bind(tag=TAG).info(f"调用本地知识库检索: question={question}")

    raglocal_config = conn.config.get("plugins", {}).get("search_from_raglocal", {})
    retriever = LocalKnowledgeRetriever(
        qdrant_path=raglocal_config.get("qdrant_path", "/root/spanish/main/xiaozhi-server/data/qdrant_raglocal_m3"),
        collection_name=raglocal_config.get("collection_name", "knowledge_local_m3"),
        model_path=raglocal_config.get("embedding_model_path", "/root/spanish/main/xiaozhi-server/models/bge-m3"),
        embedding_dims=int(raglocal_config.get("embedding_model_dims", 1024)),
        top_k=int(raglocal_config.get("top_k", 5)),
    )

    chunks = retriever.search(question)
    if not chunks:
        return ActionResponse(Action.REQLLM, "根据本地知识库查询结果，没有相关信息。", None)
    contents = []
    for chunk in chunks[:5]:
        title = chunk.get("title", "")
        content = chunk.get("content", "")
        if not content:
            continue
        if title:
            contents.append(f"[{title}]\n{content}")
        else:
            contents.append(content)
    if not contents:
        return ActionResponse(Action.REQLLM, "根据本地知识库查询结果，没有相关信息。", None)
    context_text = f"# 关于问题【{question}】查到知识库如下\n"
    context_text += "```\n\n\n".join(contents)
    context_text += "\n```"
    return ActionResponse(Action.REQLLM, context_text, None)