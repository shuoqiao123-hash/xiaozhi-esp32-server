import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
TAG = __name__


async def handleAbortMessage(conn: "ConnectionHandler"):
    conn.logger.bind(tag=TAG).info("Abort message received")
    # 设置成打断状态，会自动打断llm、tts任务
    conn.client_abort = True
    # 先保留当前音频上下文，避免打断语句前半段被过早清空
    if hasattr(conn, "abort_audio_cache") and conn.abort_audio_cache:
        conn.logger.bind(tag=TAG).debug(
            f"Abort前保留音频缓存帧数={len(conn.abort_audio_cache)}"
        )
    
    # 仅清理TTS/上报等队列，不立即清空ASR上下文

    conn.clear_queues()
    # 打断客户端说话状态
    await conn.websocket.send(
        json.dumps({"type": "tts", "state": "stop", "session_id": conn.session_id})
    )
    conn.clearSpeakStatus()
    conn.logger.bind(tag=TAG).info("Abort message received-end")
