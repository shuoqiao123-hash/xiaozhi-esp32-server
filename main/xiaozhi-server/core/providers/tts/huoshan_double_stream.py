import os
import uuid
import json
import queue
import asyncio
import traceback
import websockets

from typing import Callable, Any
from config.logger import setup_logging
from core.utils.util import check_model_key
from core.providers.tts.base import TTSProviderBase
from core.utils.tts import MarkdownCleaner, convert_percentage_to_range
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType


TAG = __name__
logger = setup_logging()

# ============================================================
# 二进制协议常量定义（新框架新增，旧代码无此部分）
# ============================================================
PROTOCOL_VERSION = 0b0001
DEFAULT_HEADER_SIZE = 0b0001

# Message Type
FULL_CLIENT_REQUEST = 0b0001
AUDIO_ONLY_RESPONSE = 0b1011
FULL_SERVER_RESPONSE = 0b1001
ERROR_INFORMATION = 0b1111

# Message Type Specific Flags
MsgTypeFlagNoSeq = 0b0000
MsgTypeFlagPositiveSeq = 0b1
MsgTypeFlagLastNoSeq = 0b10
MsgTypeFlagNegativeSeq = 0b11
MsgTypeFlagWithEvent = 0b100

# Message Serialization
NO_SERIALIZATION = 0b0000
JSON = 0b0001

# Message Compression
COMPRESSION_NO = 0b0000
COMPRESSION_GZIP = 0b0001

# ============================================================
# 事件码定义（新框架新增，旧代码无此部分）
# ============================================================
EVENT_NONE = 0
EVENT_Start_Connection = 1
EVENT_FinishConnection = 2

EVENT_ConnectionStarted = 50
EVENT_ConnectionFailed = 51
EVENT_ConnectionFinished = 52

# 上行 Session 事件
EVENT_StartSession = 100
EVENT_CancelSession = 101
EVENT_FinishSession = 102

# 下行 Session 事件
EVENT_SessionStarted = 150
EVENT_SessionCanceled = 151
EVENT_SessionFinished = 152
EVENT_SessionFailed = 153

# 上行通用事件
EVENT_TaskRequest = 200

# 下行 TTS 事件
EVENT_TTSSentenceStart = 350
EVENT_TTSSentenceEnd = 351
EVENT_TTSResponse = 352


# ============================================================
# 协议对象定义（新框架新增，替代旧代码的 HTTP JSON 协议）
# ============================================================
class Header:
    def __init__(
        self,
        protocol_version=PROTOCOL_VERSION,
        header_size=DEFAULT_HEADER_SIZE,
        message_type: int = 0,
        message_type_specific_flags: int = 0,
        serial_method: int = NO_SERIALIZATION,
        compression_type: int = COMPRESSION_NO,
        reserved_data=0,
    ):
        self.header_size = header_size
        self.protocol_version = protocol_version
        self.message_type = message_type
        self.message_type_specific_flags = message_type_specific_flags
        self.serial_method = serial_method
        self.compression_type = compression_type
        self.reserved_data = reserved_data

    def as_bytes(self) -> bytes:
        return bytes(
            [
                (self.protocol_version << 4) | self.header_size,
                (self.message_type << 4) | self.message_type_specific_flags,
                (self.serial_method << 4) | self.compression_type,
                self.reserved_data,
            ]
        )


class Optional:
    def __init__(
        self, event: int = EVENT_NONE, sessionId: str = None, sequence: int = None
    ):
        self.event = event
        self.sessionId = sessionId
        self.errorCode: int = 0
        self.connectionId: str | None = None
        self.response_meta_json: str | None = None
        self.sequence = sequence

    def as_bytes(self) -> bytes:
        option_bytes = bytearray()
        if self.event != EVENT_NONE:
            option_bytes.extend(self.event.to_bytes(4, "big", signed=True))
        if self.sessionId is not None:
            session_id_bytes = str.encode(self.sessionId)
            size = len(session_id_bytes).to_bytes(4, "big", signed=True)
            option_bytes.extend(size)
            option_bytes.extend(session_id_bytes)
        if self.sequence is not None:
            option_bytes.extend(self.sequence.to_bytes(4, "big", signed=True))
        return option_bytes


class Response:
    def __init__(self, header: Header, optional: Optional):
        self.optional = optional
        self.header = header
        self.payload: bytes | None = None


# ============================================================
# TTS 提供者（旧代码重构，适配新框架）
# ============================================================
class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        # ----------------------------------------------------------
        # [变更1] 接口类型：SINGLE_STREAM → DUAL_STREAM
        # ----------------------------------------------------------
        self.ws = None
        self.interface_type = InterfaceType.DUAL_STREAM
        self._monitor_task = None  # 监听任务引用（新框架新增）

        # ----------------------------------------------------------
        # [变更2] 配置加载：兼容旧配置键，适配新配置体系
        # ----------------------------------------------------------
        self.appId = config.get("appid")
        self.access_token = config.get("access_token")
        # 兼容旧键 "resource_Id"(大写I) 和新键 "resource_id"(小写i)
        self.resource_id = config.get("resource_id") or config.get("resource_Id")
        self.cluster = config.get("cluster")

        # 资源类型判断
        self.resource_type = True if self.resource_id == "seed-tts-2.0" else False
        self.activate_session = False
        self.session_audio_finished_event = asyncio.Event()

        # 新增：音频帧计数，用于调试乱序问题
        self._audio_frame_count = 0
        self._audio_frame_sent_count = 0  # 已发送到 handle_opus 的帧数
        self._expected_audio_frame_count = 0
        self._audio_frame_event = asyncio.Event()

        # 配音角色：兼容旧键 "voice" 和新键 "speaker"
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("speaker") or config.get("voice", "zh_female_cancan_mars_bigtts")

        # ----------------------------------------------------------
        # [变更3] 新框架的参数化音频配置（替代旧代码的硬编码方式）
        # ----------------------------------------------------------
        default_audio_params = {
            "speech_rate": 0,
            "loudness_rate": 0
        }
        default_additions = {
            "aigc_metadata": {},
            "cache_config": {},
            "post_process": {
                "pitch": 0
            }
        }
        default_mix_speaker = {}

        self.audio_params = {**default_audio_params, **config.get("audio_params", {})}
        self.additions = {**default_additions, **config.get("additions", {})}
        self.mix_speaker = {**default_mix_speaker, **config.get("mix_speaker", {})}

        # 百分比动态调整（新框架新增功能）
        if "ttsVolume" in config:
            self.audio_params["loudness_rate"] = int(convert_percentage_to_range(
                config["ttsVolume"], min_val=-50, max_val=100, base_val=0
            ))
        if "ttsRate" in config:
            self.audio_params["speech_rate"] = int(convert_percentage_to_range(
                config["ttsRate"], min_val=-50, max_val=100, base_val=0
            ))
        if "ttsPitch" in config:
            self.additions["post_process"]["pitch"] = int(convert_percentage_to_range(
                config["ttsPitch"], min_val=-12, max_val=12, base_val=0
            ))

        # ----------------------------------------------------------
        # [变更4] WebSocket 连接配置（替代旧代码的 HTTP api_url）
        # ----------------------------------------------------------
        self.ws_url = config.get("ws_url")
        self.authorization = config.get("authorization")
        self.header = {"Authorization": f"{self.authorization}{self.access_token}"}
        enable_ws_reuse_value = config.get("enable_ws_reuse", True)
        self.enable_ws_reuse = False if str(enable_ws_reuse_value).lower() == 'false' else True

        # ----------------------------------------------------------
        # [变更5] 移除旧代码的本地创建项：
        #   - self.sample_rate (改用 conn.sample_rate)
        #   - self.opus_encoder (改用父类提供)
        #   - self.pcm_buffer (新框架无需客户端缓冲)
        #   - self.audio_format (硬编码 pcm，现由协议处理)
        #   - self.api_url (改用 ws_url)
        # ----------------------------------------------------------

        model_key_msg = check_model_key("TTS", self.access_token)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

    # ==============================================================
    # 连接管理（新框架新增，替代旧代码的 HTTP 无状态连接）
    # ==============================================================

    async def open_audio_channels(self, conn):
        """覆写父类方法：在建立音频通道时同步采样率到 audio_params"""
        try:
            await super().open_audio_channels(conn)
            # [变更] 采样率从 conn 动态获取，不再硬编码 24000
            self.audio_params["sample_rate"] = conn.sample_rate
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to open audio channels: {str(e)}")
            self.ws = None
            raise

    async def _ensure_connection(self):
        """建立新的 WebSocket 连接，并启动监听任务（仅第一次）"""
        try:
            if self.ws:
                if self.enable_ws_reuse:
                    logger.bind(tag=TAG).info(f"使用已有链接...")
                    return self.ws
                else:
                    try:
                        await self.finish_connection()
                    except:
                        pass
            logger.bind(tag=TAG).debug("开始建立新连接...")
            ws_header = {
                "X-Api-App-Key": self.appId,
                "X-Api-Access-Key": self.access_token,
                "X-Api-Resource-Id": self.resource_id,
                "X-Api-Connect-Id": uuid.uuid4(),
            }
            self.ws = await websockets.connect(
                self.ws_url, additional_headers=ws_header, max_size=1000000000
            )
            logger.bind(tag=TAG).debug("WebSocket连接建立成功")

            # 连接建立成功后，启动监听任务
            if self._monitor_task is None or self._monitor_task.done():
                logger.bind(tag=TAG).debug("启动监听任务...")
                self._monitor_task = asyncio.create_task(self._start_monitor_tts_response())

            return self.ws
        except Exception as e:
            logger.bind(tag=TAG).error(f"建立连接失败: {str(e)}")
            self.ws = None
            raise

    async def finish_connection(self):
        """发送 FinishConnection 事件，等待服务端返回 EVENT_ConnectionFinished"""
        try:
            if self.ws:
                logger.bind(tag=TAG).debug("开始关闭连接...")
                header = Header(
                    message_type=FULL_CLIENT_REQUEST,
                    message_type_specific_flags=MsgTypeFlagWithEvent,
                    serial_method=JSON,
                ).as_bytes()
                optional = Optional(event=EVENT_FinishConnection).as_bytes()
                payload = str.encode("{}")
                await self.send_event(self.ws, header, optional, payload)
        except:
            pass

    # ==============================================================
    # 会话管理（新框架新增，旧代码无会话概念）
    # ==============================================================

    async def start_session(self, session_id):
        """启动 TTS 会话"""
        logger.bind(tag=TAG).debug(f"开始会话～～{session_id}")
        try:
            # 等待上一个会话结束，最多等待3次
            for _ in range(3):
                if not self.activate_session:
                    break
                logger.bind(tag=TAG).debug(f"等待上一个会话结束...")
                await asyncio.sleep(0.1)
            else:
                logger.bind(tag=TAG).debug("等待上一个会话超时，清除连接状态...")
                await self.close()

            self.activate_session = True
            self.session_audio_finished_event.clear()
            # 重置音频帧计数
            self._audio_frame_count = 0
            self._audio_frame_sent_count = 0
            self._audio_frame_event.clear()

            await self._ensure_connection()

            header = Header(
                message_type=FULL_CLIENT_REQUEST,
                message_type_specific_flags=MsgTypeFlagWithEvent,
                serial_method=JSON,
            ).as_bytes()
            optional = Optional(
                event=EVENT_StartSession, sessionId=session_id
            ).as_bytes()
            payload = self.get_payload_bytes(
                event=EVENT_StartSession, speaker=self.voice
            )
            await self.send_event(self.ws, header, optional, payload)
            logger.bind(tag=TAG).debug("会话启动请求已发送")
        except Exception as e:
            logger.bind(tag=TAG).error(f"启动会话失败: {str(e)}")
            await self.close()
            raise

    async def finish_session(self, session_id):
        """结束 TTS 会话"""
        logger.bind(tag=TAG).debug(f"关闭会话～～{session_id}")
        try:
            if self.ws:
                header = Header(
                    message_type=FULL_CLIENT_REQUEST,
                    message_type_specific_flags=MsgTypeFlagWithEvent,
                    serial_method=JSON,
                ).as_bytes()
                optional = Optional(
                    event=EVENT_FinishSession, sessionId=session_id
                ).as_bytes()
                payload = str.encode("{}")
                await self.send_event(self.ws, header, optional, payload)
                logger.bind(tag=TAG).debug("会话结束请求已发送")
        except Exception as e:
            logger.bind(tag=TAG).error(f"关闭会话失败: {str(e)}")
            await self.close()
            raise

    async def cancel_session(self, session_id):
        """取消 TTS 会话，释放服务端资源"""
        logger.bind(tag=TAG).debug(f"取消会话，释放服务端资源～～{session_id}")
        try:
            if self.ws:
                header = Header(
                    message_type=FULL_CLIENT_REQUEST,
                    message_type_specific_flags=MsgTypeFlagWithEvent,
                    serial_method=JSON,
                ).as_bytes()
                optional = Optional(
                    event=EVENT_CancelSession, sessionId=session_id
                ).as_bytes()
                payload = str.encode("{}")
                await self.send_event(self.ws, header, optional, payload)
                logger.bind(tag=TAG).debug("会话取消请求已发送")
        except Exception as e:
            logger.bind(tag=TAG).error(f"取消会话失败: {str(e)}")
            await self.close()
            raise

    # ==============================================================
    # 资源清理（重构，替代旧代码的 close）
    # ==============================================================

    async def close(self):
        """
        [变更] 资源清理方法
        - 旧代码：仅调用 super().close() + 关闭本地 opus_encoder
        - 新框架：需额外取消 monitor_task + 关闭 WebSocket
        - 移除了本地 opus_encoder.close()（改由父类管理）
        """
        self.activate_session = False
        # 取消监听任务
        if self._monitor_task:
            try:
                self._monitor_task.cancel()
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.bind(tag=TAG).warning(f"关闭时取消监听任务错误: {e}")
            self._monitor_task = None

        if self.ws:
            try:
                await self.ws.close()
            except:
                pass
            self.ws = None

    # ==============================================================
    # 文本处理线程（核心业务逻辑重构）
    # ==============================================================

    def tts_text_priority_thread(self):
        """
        [变更] 从旧代码的同步 HTTP 模式重构为新框架的 WebSocket 会话模式
        
        旧代码流程：
          FIRST → 初始化缓冲区 → TEXT → 缓冲+分段 → asyncio.run(to_tts_single_stream) → LAST → flush 残余
          
        新框架流程：
          FIRST → start_session → TEXT → run_coroutine_threadsafe(text_to_speak) → LAST → finish_session
          
        关键变更：
          1. asyncio.run() → asyncio.run_coroutine_threadsafe(self.conn.loop)
          2. 新增会话生命周期管理（start/finish_session）
          3. 新增打断处理（conn.client_abort → cancel_session）
          4. 移除客户端文本分段逻辑（服务端处理流式）
          5. 移除 pcm_buffer 缓冲（monitor task 处理音频接收）
        """
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
                logger.bind(tag=TAG).debug(
                    f"收到TTS任务｜{message.sentence_type.name} ｜ {message.content_type.name} | 会话ID: {self.conn.sentence_id}"
                )

                if message.sentence_type == SentenceType.FIRST:
                    # [变更] 旧代码：self.tts_stop_request = False
                    # 新框架：self.conn.client_abort = False
                    self.conn.client_abort = False
                    # 重置音频帧计数
                    self._audio_frame_count = 0
                    self._expected_audio_frame_count = 0
                    self._audio_frame_event.clear()
                    logger.bind(tag=TAG).debug("重置音频帧计数，开始新的句子")

                # [变更] 新增打断处理：检测后立即释放服务端资源
                if self.conn.client_abort:
                    try:
                        logger.bind(tag=TAG).info("收到打断信息，终止TTS文本处理线程")
                        if self.enable_ws_reuse:
                            asyncio.run_coroutine_threadsafe(
                                self.cancel_session(self.conn.sentence_id),
                                loop=self.conn.loop,
                            )
                        else:
                            asyncio.run_coroutine_threadsafe(
                                self.finish_connection(),
                                loop=self.conn.loop,
                            )
                        continue
                    except Exception as e:
                        logger.bind(tag=TAG).error(f"取消TTS会话失败: {str(e)}")
                        continue

                if message.sentence_type == SentenceType.FIRST:
                    # [变更] 新增会话启动流程
                    try:
                        if not getattr(self.conn, "sentence_id", None):
                            self.conn.sentence_id = uuid.uuid4().hex
                            logger.bind(tag=TAG).debug(f"自动生成新的 会话ID: {self.conn.sentence_id}")

                        logger.bind(tag=TAG).debug("开始启动TTS会话...")
                        future = asyncio.run_coroutine_threadsafe(
                            self.start_session(self.conn.sentence_id),
                            loop=self.conn.loop,
                        )
                        future.result()
                        self.before_stop_play_files.clear()
                        logger.bind(tag=TAG).debug("TTS会话启动成功")
                    except Exception as e:
                        logger.bind(tag=TAG).error(f"启动TTS会话失败: {str(e)}")
                        continue

                if ContentType.TEXT == message.content_type:
                    # [变更] 旧代码：缓冲文本 → _get_segment_text() → to_tts_single_stream()
                    # 新框架：直接发送完整文本，服务端处理流式
                    if message.content_detail:
                        try:
                            logger.bind(tag=TAG).debug(
                                f"开始发送TTS文本: {message.content_detail}"
                            )
                            # [变更] asyncio.run() → run_coroutine_threadsafe(self.conn.loop)
                            future = asyncio.run_coroutine_threadsafe(
                                self.text_to_speak(message.content_detail, None),
                                loop=self.conn.loop,
                            )
                            future.result()
                            logger.bind(tag=TAG).debug("TTS文本发送成功")
                        except Exception as e:
                            logger.bind(tag=TAG).error(f"发送TTS文本失败: {str(e)}")
                            continue

                elif ContentType.FILE == message.content_type:
                    logger.bind(tag=TAG).info(
                        f"添加音频文件到待播放列表: {message.content_file}"
                    )
                    if message.content_file and os.path.exists(message.content_file):
                        self._process_audio_file_stream(
                            message.content_file,
                            callback=lambda audio_data: self.handle_audio_file(audio_data, message.content_detail)
                        )

                if message.sentence_type == SentenceType.LAST:
                    try:
                        logger.bind(tag=TAG).debug("开始结束TTS会话...")
                        # 确保会话 ID 一致
                        current_session_id = self.conn.sentence_id
                        logger.bind(tag=TAG).debug(f"结束会话使用的会话ID: {current_session_id}")

                        # 记录当前已收到的音频帧数
                        frames_before_finish = self._audio_frame_count
                        logger.bind(tag=TAG).debug(f"发送FinishSession前，已收到音频帧 #{frames_before_finish}")

                        # 关键修复：额外等待一段时间，确保所有音频帧都被处理
                        # 火山河 TTS 服务端可能先完成后面的句子，再完成前面的
                        # 如果不等待，前端收到 "stop" 信号后进入聆听模式，但音频帧还在路上
                        import time
                        expected_wait_time = 2.0  # 额外等待2秒
                        initial_frame_count = self._audio_frame_count

                        # 定义等待音频帧稳定的异步函数
                        async def wait_for_audio_frames_stable():
                            wait_start_time = time.time()
                            last_frame_count = initial_frame_count
                            stable_count = 0
                            max_stable_iterations = 10  # 连续10次不变才认为稳定

                            logger.bind(tag=TAG).debug(
                                f"开始等待音频帧稳定，初始帧数={initial_frame_count}，等待时间={expected_wait_time}秒"
                            )

                            while time.time() - wait_start_time < expected_wait_time:
                                await asyncio.sleep(0.2)  # 每200ms检查一次
                                current_count = self._audio_frame_count

                                if current_count == last_frame_count:
                                    stable_count += 1
                                    if stable_count >= max_stable_iterations:
                                        logger.bind(tag=TAG).debug(
                                            f"音频帧数已稳定：{current_count}，停止等待"
                                        )
                                        break
                                else:
                                    stable_count = 0
                                    logger.bind(tag=TAG).debug(
                                        f"音频帧数变化：{last_frame_count} -> {current_count}"
                                    )
                                    last_frame_count = current_count

                            final_frame_count = self._audio_frame_count
                            actual_wait_time = time.time() - wait_start_time
                            logger.bind(tag=TAG).debug(
                                f"等待结束：初始={initial_frame_count}，最终={final_frame_count}，"
                                f"实际等待={actual_wait_time:.2f}秒"
                            )
                            return final_frame_count

                        # 在事件循环中运行等待函数
                        wait_future = asyncio.run_coroutine_threadsafe(
                            wait_for_audio_frames_stable(),
                            loop=self.conn.loop,
                        )
                        wait_future.result()  # 等待等待完成

                        future = asyncio.run_coroutine_threadsafe(
                            self.finish_session(current_session_id),
                            loop=self.conn.loop,
                        )
                        future.result()

                        logger.bind(tag=TAG).debug("FinishSession已发送，阻塞等待音频接收完毕...")

                        # 重置事件，确保等待正确的会话结束
                        self.session_audio_finished_event.clear()

                        # 添加超时机制，避免永久等待
                        async def wait_with_timeout():
                            try:
                                await asyncio.wait_for(
                                    self.session_audio_finished_event.wait(),
                                    timeout=10.0  # 10秒超时
                                )
                                return True
                            except asyncio.TimeoutError:
                                logger.bind(tag=TAG).warning("等待音频接收完毕超时")
                                return False

                        wait_future = asyncio.run_coroutine_threadsafe(
                            wait_with_timeout(),
                            loop=self.conn.loop,
                        )
                        wait_result = wait_future.result()

                        if wait_result:
                            frames_after_finish = self._audio_frame_count
                            logger.bind(tag=TAG).debug(
                                f"音频接收完毕，TTS会话真正结束。 "
                                f"发送前帧数={frames_before_finish}，最终帧数={frames_after_finish}"
                            )
                        else:
                            logger.bind(tag=TAG).warning("TTS会话超时，继续处理")

                    except Exception as e:
                        logger.bind(tag=TAG).error(f"结束TTS会话失败: {str(e)}")
                        continue

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"处理TTS文本失败: {str(e)}, 类型: {type(e).__name__}, 堆栈: {traceback.format_exc()}"
                )
                continue

    # ==============================================================
    # 文本发送（重构，HTTP POST → WebSocket 协议）
    # ==============================================================

    async def text_to_speak(self, text, _):
        """
        [变更] 从 HTTP POST 请求重构为 WebSocket 发送
        
        旧代码：
          1. 构建 HTTP headers + JSON payload
          2. aiohttp.ClientSession.post()
          3. 同步等待响应流
          4. 解析 JSON → base64 解码 → PCM 缓冲 → 帧切分 → Opus 编码
          
        新框架：
          1. 检查 WebSocket 连接
          2. send_text() 通过二进制协议发送文本
          3. 立即返回，音频由 monitor_task 异步接收处理
        """
        try:
            if self.ws is None:
                logger.bind(tag=TAG).warning(f"WebSocket连接不存在，终止发送文本")
                return

            filtered_text = MarkdownCleaner.clean_markdown(text)

            if filtered_text:
                await self.send_text(self.voice, filtered_text, self.conn.sentence_id)
            return
        except Exception as e:
            logger.bind(tag=TAG).error(f"发送TTS文本失败: {str(e)}")
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
            raise

    # ==============================================================
    # 后台监听任务（新框架新增，旧代码无此组件）
    # ==============================================================

    async def _start_monitor_tts_response(self):
        """
        [新框架新增] 后台常驻协程，持续监听 WebSocket 响应
        
        这是新框架最核心的架构变化：
        - 旧代码在 text_to_speak() 中同步接收音频
        - 新框架将接收逻辑完全解耦到此协程中
        - 负责解析二进制协议、处理各种事件、编码音频
        """
        try:
            while not self.conn.stop_event.is_set():
                try:
                    msg = await self.ws.recv()
                    res = self.parser_response(msg)
                    self.print_response(res, "send_text res:")

                    # 优先处理连接级别事件
                    if res.optional.event == EVENT_ConnectionFinished:
                        logger.bind(tag=TAG).debug(f"链接关闭成功～～")
                        break

                    # 只处理当前活跃会话的响应
                    if res.optional.sessionId and self.conn.sentence_id != res.optional.sessionId:
                        logger.bind(tag=TAG).debug(
                            f"忽略非当前会话的响应: {res.optional.event}, "
                            f"当前会话={self.conn.sentence_id}, 收到会话={res.optional.sessionId}"
                        )
                        continue

                    if res.optional.event == EVENT_SessionCanceled:
                        logger.bind(tag=TAG).debug(f"释放服务端资源成功～～")
                        self.activate_session = False
                        self.session_audio_finished_event.set()
                    elif not self.resource_type and res.optional.event == EVENT_TTSSentenceStart:
                        json_data = json.loads(res.payload.decode("utf-8"))
                        self.tts_text = json_data.get("text", "")
                        logger.bind(tag=TAG).debug(f"句子语音生成开始: {self.tts_text}")
                        self.tts_audio_queue.put(
                            (SentenceType.FIRST, [], self.tts_text)
                        )
                    elif (
                        res.optional.event == EVENT_TTSResponse
                        and res.header.message_type == AUDIO_ONLY_RESPONSE
                    ):
                        # 处理 seed-tts-2.0 文本字幕
                        if self.resource_type and self.conn.tts_MessageText:
                            logger.bind(tag=TAG).info(
                                f"句子语音生成成功： {self.conn.tts_MessageText}"
                            )
                            self.tts_audio_queue.put(
                                (SentenceType.FIRST, [], self.conn.tts_MessageText)
                            )
                            self.conn.tts_MessageText = None

                        # 记录音频帧接收，用于调试乱序问题
                        payload_len = len(res.payload) if res.payload else 0
                        self._audio_frame_count += 1
                        logger.bind(tag=TAG).debug(
                            f"收到音频帧 #{self._audio_frame_count}: payload_size={payload_len}, "
                            f"session={res.optional.sessionId}"
                        )
                        self.wav_to_opus_data_audio_raw_stream(res.payload, callback=self.handle_opus)
                    elif not self.resource_type and res.optional.event == EVENT_TTSSentenceEnd:
                        logger.bind(tag=TAG).info(f"句子语音生成成功：{self.tts_text}")
                    elif res.optional.event == EVENT_SessionFinished:
                        logger.bind(tag=TAG).debug(
                            f"会话结束～～，已收到音频帧 #{self._audio_frame_count}"
                        )
                        self.activate_session = False
                        self._process_before_stop_play_files()

                        # 记录音频帧总数，用于调试
                        logger.bind(tag=TAG).info(
                            f"TTS会话完成，音频帧总数={self._audio_frame_count}"
                        )
                        self.session_audio_finished_event.set()
                        if not self.enable_ws_reuse:
                            await self.finish_connection()
                except websockets.ConnectionClosed:
                    logger.bind(tag=TAG).warning("WebSocket连接已关闭")
                    break
                except Exception as e:
                    logger.bind(tag=TAG).error(
                        f"Error in _start_monitor_tts_response: {e}"
                    )
                    traceback.print_exc()
                    break
            if self.ws:
                try:
                    await self.ws.close()
                except:
                    pass
                self.ws = None
        finally:
            self.activate_session = False
            self._monitor_task = None
            self.session_audio_finished_event.set()

    # ==============================================================
    # 二进制协议通信方法（新框架新增，替代旧代码的 HTTP 通信）
    # ==============================================================

    async def send_event(
        self,
        ws: websockets.WebSocketClientProtocol,
        header: bytes,
        optional: bytes | None = None,
        payload: bytes = None,
    ):
        """发送二进制协议消息"""
        try:
            full_client_request = bytearray(header)
            if optional is not None:
                full_client_request.extend(optional)
            if payload is not None:
                payload_size = len(payload).to_bytes(4, "big", signed=True)
                full_client_request.extend(payload_size)
                full_client_request.extend(payload)
            await ws.send(full_client_request)
        except websockets.ConnectionClosed:
            logger.bind(tag=TAG).error(f"ConnectionClosed")
            raise

    async def send_text(self, speaker: str, text: str, session_id):
        """发送文本任务请求"""
        header = Header(
            message_type=FULL_CLIENT_REQUEST,
            message_type_specific_flags=MsgTypeFlagWithEvent,
            serial_method=JSON,
        ).as_bytes()
        optional = Optional(event=EVENT_TaskRequest, sessionId=session_id).as_bytes()
        payload = self.get_payload_bytes(
            event=EVENT_TaskRequest, text=text, speaker=speaker
        )
        return await self.send_event(self.ws, header, optional, payload)

    # ==============================================================
    # 二进制协议解析方法（新框架新增，替代旧代码的 JSON 解析）
    # ==============================================================

    def read_res_content(self, res: bytes, offset: int):
        """读取响应中的字符串内容段"""
        content_size = int.from_bytes(res[offset : offset + 4], "big", signed=True)
        offset += 4
        content = res[offset : offset + content_size].decode('utf-8')
        offset += content_size
        return content, offset

    def read_res_payload(self, res: bytes, offset: int):
        """读取响应中的 payload 段"""
        payload_size = int.from_bytes(res[offset : offset + 4], "big", signed=True)
        offset += 4
        payload = res[offset : offset + payload_size]
        offset += payload_size
        return payload, offset

    def parser_response(self, res) -> Response:
        """解析 WebSocket 二进制响应为 Response 对象"""
        if isinstance(res, str):
            raise RuntimeError(res)
        response = Response(Header(), Optional())
        header = response.header
        num = 0b00001111
        header.protocol_version = res[0] >> 4 & num
        header.header_size = res[0] & 0x0F
        header.message_type = (res[1] >> 4) & num
        header.message_type_specific_flags = res[1] & 0x0F
        header.serialization_method = res[2] >> num
        header.message_compression = res[2] & 0x0F
        header.reserved = res[3]

        offset = 4
        optional = response.optional
        if header.message_type == FULL_SERVER_RESPONSE or AUDIO_ONLY_RESPONSE:
            if header.message_type_specific_flags == MsgTypeFlagWithEvent:
                optional.event = int.from_bytes(res[offset:8], "big", signed=True)
                offset += 4
                if optional.event == EVENT_NONE:
                    return response
                elif optional.event == EVENT_ConnectionStarted:
                    optional.connectionId, offset = self.read_res_content(res, offset)
                elif optional.event == EVENT_ConnectionFailed:
                    optional.response_meta_json, offset = self.read_res_content(res, offset)
                elif (
                    optional.event == EVENT_SessionStarted
                    or optional.event == EVENT_SessionFailed
                    or optional.event == EVENT_SessionFinished
                ):
                    optional.sessionId, offset = self.read_res_content(res, offset)
                    optional.response_meta_json, offset = self.read_res_content(res, offset)
                else:
                    optional.sessionId, offset = self.read_res_content(res, offset)
                    response.payload, offset = self.read_res_payload(res, offset)
        elif header.message_type == ERROR_INFORMATION:
            optional.errorCode = int.from_bytes(
                res[offset : offset + 4], "big", signed=True
            )
            offset += 4
            response.payload, offset = self.read_res_payload(res, offset)
        return response

    # ==============================================================
    # 连接启动与辅助方法
    # ==============================================================

    async def start_connection(self):
        """发送 StartConnection 事件"""
        header = Header(
            message_type=FULL_CLIENT_REQUEST,
            message_type_specific_flags=MsgTypeFlagWithEvent,
        ).as_bytes()
        optional = Optional(event=EVENT_Start_Connection).as_bytes()
        payload = str.encode("{}")
        return await self.send_event(self.ws, header, optional, payload)

    def print_response(self, res, tag_msg: str):
        """调试：打印响应信息"""
        logger.bind(tag=TAG).debug(f"===>{tag_msg} header:{res.header.__dict__}")
        logger.bind(tag=TAG).debug(f"===>{tag_msg} optional:{res.optional.__dict__}")

    def get_payload_bytes(
        self,
        uid="1234",
        event=EVENT_NONE,
        text="",
        speaker="",
        audio_format="pcm",
    ):
        """
        [变更] 构建请求 payload
        旧代码：硬编码的 JSON payload，直接传 HTTP body
        新框架：包装为二进制协议所需的 JSON 结构，包含 namespace 和 additions
        """
        req_params = {
            "text": text,
            "speaker": speaker,
            "audio_params": {**self.audio_params, "format": audio_format},
            "additions": json.dumps(self.additions)
        }
        if self.mix_speaker:
            req_params["mix_speaker"] = self.mix_speaker

        return str.encode(
            json.dumps(
                {
                    "user": {"uid": uid},
                    "event": event,
                    "namespace": "BidirectionalTTS",
                    "req_params": req_params
                }
            )
        )

    # ==============================================================
    # 音频编码处理（重构，适配新框架的线程安全要求）
    # ==============================================================

    def audio_to_opus_data_stream(
        self, audio_file_path, callback: Callable[[Any], Any] = None
    ):
        """
        [变更] 重写父类方法：使用独立临时编码器，避免并发冲突
        
        旧代码：无此方法，直接使用 self.opus_encoder（不安全）
        新框架：创建独立临时编码器处理音频文件，与 TTS 流式编码器隔离
        原因：monitor_task 在 event loop 线程使用 self.opus_encoder，
              同时 tts_text_priority_thread 处理音乐文件也用同一个 encoder，
              共享的 encoder.buffer 非线程安全，会触发 SILK resampler 断言失败。
        """
        from core.utils.util import audio_to_data_stream
        return audio_to_data_stream(
            audio_file_path, is_opus=True, callback=callback,
            sample_rate=self.conn.sample_rate, opus_encoder=None
        )

    def wav_to_opus_data_audio_raw_stream(self, raw_data_var, is_end=False, callback: Callable[[Any], Any] = None):
        """将原始 PCM 数据编码为 Opus 流"""
        return self.opus_encoder.encode_pcm_to_opus_stream(raw_data_var, is_end, callback=callback)

    # ==============================================================
    # 非流式 to_tts（重构，HTTP → WebSocket）
    # ==============================================================

    def to_tts(self, text: str) -> list:
        """
        [变更] 非流式音频生成，适配 WebSocket 二进制协议
        
        旧代码：requests.post() HTTP 请求 → JSON 解析 → base64 解码 → PCM 缓冲 → Opus 编码
        新框架：建立独立 WebSocket → 二进制协议通信 → parser_response 解析 → Opus 编码
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            session_id = uuid.uuid4().__str__().replace("-", "")
            audio_data = []

            async def _generate_audio():
                ws_header = {
                    "X-Api-App-Key": self.appId,
                    "X-Api-Access-Key": self.access_token,
                    "X-Api-Resource-Id": self.resource_id,
                    "X-Api-Connect-Id": uuid.uuid4(),
                }
                ws = await websockets.connect(
                    self.ws_url, additional_headers=ws_header, max_size=1000000000
                )

                try:
                    # 启动会话
                    header = Header(
                        message_type=FULL_CLIENT_REQUEST,
                        message_type_specific_flags=MsgTypeFlagWithEvent,
                        serial_method=JSON,
                    ).as_bytes()
                    optional = Optional(
                        event=EVENT_StartSession, sessionId=session_id
                    ).as_bytes()
                    payload = self.get_payload_bytes(
                        event=EVENT_StartSession, speaker=self.voice
                    )
                    await self.send_event(ws, header, optional, payload)

                    # 发送文本
                    header = Header(
                        message_type=FULL_CLIENT_REQUEST,
                        message_type_specific_flags=MsgTypeFlagWithEvent,
                        serial_method=JSON,
                    ).as_bytes()
                    optional = Optional(
                        event=EVENT_TaskRequest, sessionId=session_id
                    ).as_bytes()
                    payload = self.get_payload_bytes(
                        event=EVENT_TaskRequest, text=text, speaker=self.voice
                    )
                    await self.send_event(ws, header, optional, payload)

                    # 发送结束会话请求
                    header = Header(
                        message_type=FULL_CLIENT_REQUEST,
                        message_type_specific_flags=MsgTypeFlagWithEvent,
                        serial_method=JSON,
                    ).as_bytes()
                    optional = Optional(
                        event=EVENT_FinishSession, sessionId=session_id
                    ).as_bytes()
                    payload = str.encode("{}")
                    await self.send_event(ws, header, optional, payload)

                    # 接收音频数据
                    while True:
                        msg = await ws.recv()
                        res = self.parser_response(msg)

                        if (
                            res.optional.event == EVENT_TTSResponse
                            and res.header.message_type == AUDIO_ONLY_RESPONSE
                        ):
                            self.wav_to_opus_data_audio_raw_stream(
                                res.payload,
                                callback=lambda opus_frame: audio_data.append(opus_frame)
                            )
                        elif res.optional.event == EVENT_SessionFinished:
                            break
                finally:
                    try:
                        await ws.close()
                    except:
                        pass

            loop.run_until_complete(_generate_audio())
            loop.close()
            return audio_data

        except Exception as e:
            logger.bind(tag=TAG).error(f"生成音频数据失败: {str(e)}")
            return []
