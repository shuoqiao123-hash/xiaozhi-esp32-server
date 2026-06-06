"""服务端Web搜索工具执行器"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, TYPE_CHECKING

import yaml
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import (
    PromptAgentDefinition,
    WebSearchTool,
    WebSearchApproximateLocation,
)

from ..base import ToolType, ToolDefinition, ToolExecutor
from plugins_func.register import Action, ActionResponse

if TYPE_CHECKING:
    from core.connection import ConnectionHandler


class WebSearchExecutor(ToolExecutor):
    """服务端Web搜索工具执行器"""

    def __init__(self, conn: "ConnectionHandler"):
        self.conn = conn
        self.config = {}
        self._initialized = False
        self._tool_definitions: Dict[str, ToolDefinition] = {}
        self._project_client: AIProjectClient | None = None
        self._openai_client = None
        self._web_search_agent = None
        self._local_config = None
        self._local_config_path = Path("/root/spanish/main/xiaozhi-server/data/.config.yaml")

    def _build_tool_description(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "使用 Foundry Web Search 搜索公共网页并返回最新信息。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "需要搜索的网页查询内容",
                        },
                        "country": {
                            "type": "string",
                            "description": "可选，用户所在国家/地区，两位国家代码，例如 CN、US",
                        },
                        "region": {
                            "type": "string",
                            "description": "可选，地区或省份",
                        },
                        "city": {
                            "type": "string",
                            "description": "可选，城市名称",
                        },
                    },
                    "required": ["query"],
                },
            },
        }

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return

        self._tool_definitions = {
            "web_search": ToolDefinition(
                name="web_search",
                description=self._build_tool_description(),
                tool_type=ToolType.SERVER_WEB_SEARCH,
            )
        }
        self._initialized = True

    def _load_local_config(self) -> Dict[str, Any]:
        if self._local_config is not None:
            return self._local_config

        try:
            if self._local_config_path.exists():
                with self._local_config_path.open("r", encoding="utf-8") as f:
                    self._local_config = yaml.safe_load(f) or {}
            else:
                self._local_config = {}
        except Exception:
            self._local_config = {}
        return self._local_config

    def _get_local_config_value(self, *keys, default=""):
        cfg = self._load_local_config()
        current = cfg
        for key in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(key)
            if current is None:
                return default
        if isinstance(current, str):
            return current.strip()
        return current if current is not None else default

    def _get_project_endpoint(self) -> str:
        return self._get_local_config_value("foundry", "project_endpoint", default="")

    def _ensure_client(self) -> None:
        if self._project_client is not None:
            return

        endpoint = self._get_project_endpoint()
        if not endpoint:
            raise ValueError("缺少 Foundry 项目端点配置，请设置 project_endpoint")

        self._project_client = AIProjectClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        self._openai_client = self._project_client.get_openai_client()

    def _ensure_agent(self):
        if self._web_search_agent is not None:
            return self._web_search_agent

        self._ensure_client()
        country = ""
        region = ""
        city = ""
        web_tool = WebSearchTool()
        self._web_search_agent = self._project_client.agents.create_version(
            agent_name="XiaozhiWebSearchAgent",
            definition=PromptAgentDefinition(
                model=self._get_local_config_value("web_search", "model", default="gpt-5-mini"),
                instructions=(
                    "You are a helpful assistant that searches the web and answers concisely. "
                    "Return only the final answer, without citations, source links, or reference lists."
                ),
                tools=[web_tool],
            ),
            description="Web search agent for Xiaozhi server.",
        )
        return self._web_search_agent

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        if not self.has_tool(tool_name):
            return ActionResponse(
                action=Action.NOTFOUND, response=f"Web搜索工具 {tool_name} 不存在"
            )

        try:
            self._ensure_client()

            query = (arguments.get("query") or "").strip()
            if not query:
                return ActionResponse(
                    action=Action.ERROR, response="web_search 需要 query 参数"
                )

            self._ensure_agent()

            stream_response = self._openai_client.responses.create(
                stream=True,
                tool_choice="required",
                input=query,
                extra_body={
                    "agent_reference": {
                        "name": self._web_search_agent.name,
                        "type": "agent_reference",
                    }
                },
            )

            full_text = []
            for event in stream_response:
                if event.type == "response.output_text.delta":
                    full_text.append(event.delta)
                elif event.type == "response.completed":
                    break

            answer = "".join(full_text).strip()

            if not answer:
                answer = "未获取到有效的网页搜索结果。"

            return ActionResponse(action=Action.RESPONSE, response=answer)

        except Exception as e:
            return ActionResponse(action=Action.ERROR, response=str(e))

    def get_tools(self) -> Dict[str, ToolDefinition]:
        self._ensure_initialized()
        return self._tool_definitions.copy()

    def has_tool(self, tool_name: str) -> bool:
        self._ensure_initialized()
        return tool_name in self._tool_definitions
