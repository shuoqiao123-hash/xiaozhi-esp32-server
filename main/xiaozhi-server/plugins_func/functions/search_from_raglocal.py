from config.logger import setup_logging
from plugins_func.register import register_function, ToolType, ActionResponse, Action
from raglocal.local_knowledge_retriever import LocalKnowledgeRetriever
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.connection import ConnectionHandler
TAG = __name__
logger = setup_logging()

# 定义本地知识库检索函数的描述（OpenAI function calling 格式） / Define function description for local knowledge search (OpenAI function calling format)
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

# 注册函数到插件系统，类型为系统控制型工具 / Register function to plugin system as SYSTEM_CTL tool
@register_function(
    "search_from_raglocal", SEARCH_FROM_RAGLOCAL_FUNCTION_DESC, ToolType.SYSTEM_CTL
)
def search_from_raglocal(conn: "ConnectionHandler", question=None):
    """
    本地知识库检索函数，根据用户提问从 Qdrant 中检索相关片段。
    Local knowledge retrieval function: search relevant chunks from Qdrant based on user question.
    """
    # 规范化问题参数（确保为字符串且去除首尾空格） / Normalize question param (ensure string and strip)
    question = question if isinstance(question, str) else (str(question) if question is not None else "")
    question = question.strip()
    if not question:
        # 空问题直接返回错误响应 / Return error response for empty question
        return ActionResponse(Action.RESPONSE, None, "知识库查询问题不能为空。")
    
    logger.bind(tag=TAG).info(f"调用本地知识库检索: question={question}")
    
    # 从连接配置中读取该插件的配置参数 / Read plugin config from connection config
    raglocal_config = conn.config.get("plugins", {}).get("search_from_raglocal", {})

    # 初始化本地知识库检索器（指定 Qdrant 路径、集合名、Embedding 模型等） / Init local knowledge retriever (Qdrant path, collection, embedding model, etc.)
    retriever = LocalKnowledgeRetriever(
        qdrant_path=raglocal_config.get("qdrant_path", "/root/spanish/main/xiaozhi-server/data/qdrant_raglocal_m3"),
        collection_name=raglocal_config.get("collection_name", "knowledge_local_m3"),
        model_path=raglocal_config.get("embedding_model_path", "/root/spanish/main/xiaozhi-server/models/bge-m3"),
        embedding_dims=int(raglocal_config.get("embedding_model_dims", 1024)),
        top_k=int(raglocal_config.get("top_k", 5)),
    )

    chunks = retriever.search(question)
    # 若无结果，返回提示给 LLM 继续处理 / If no result, return prompt to LLM for further handling
    if not chunks:
        return ActionResponse(Action.REQLLM, "根据本地知识库查询结果，没有相关信息。", None)

    # 提取前5条结果的内容（含标题） / Extract contents of top 5 results (including titles)
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

    # 构建返回给 LLM 的上下文文本（采用 Markdown 代码块格式） / Build context text for LLM (Markdown code block format)
    context_text = f"# 关于问题【{question}】查到知识库如下\n"
    context_text += "```\n\n\n".join(contents)
    context_text += "\n```"

    # 返回要求 LLM 继续处理并附带检索到的上下文 / Return Action.REQLLM with the context
    return ActionResponse(Action.REQLLM, context_text, None)