# import asyncio
# import math
# import os
# import queue
# import struct
# import time
# import wave
# from typing import Optional, Tuple, List, TYPE_CHECKING
# import azure.cognitiveservices.speech as speechsdk
# from core.providers.asr.base import ASRProviderBase
# from config.logger import setup_logging
# from core.providers.asr.dto.dto import InterfaceType
# if TYPE_CHECKING:
#     from core.connection import ConnectionHandler
# TAG = __name__
# logger = setup_logging()
# class ASRProvider(ASRProviderBase):
#     """Azure Speech Service 流式ASR实现
#     采用队列解耦模式处理 SDK 多线程回调，同时遵循框架规范，
#     通过 self.text 桥接结果，最终由 speech_to_text 方法统一交付。
#     """
#     def __init__(self, config, delete_audio_file):
#         super().__init__()
#         self.interface_type = InterfaceType.STREAM
#         self.config = config
#         self.delete_audio_file = delete_audio_file
#         # Azure配置
#         self.subscription = config.get("speech_key")
#         self.region = config.get("service_region")
#         self.language = config.get("language", "zh-CN")
#         self.output_dir = config.get("output_dir", "tmp/")
#         self.debug_audio_dir = "/root/spanish/main/xiaozhi-server/tmp"
#         self.enable_debug_audio_save = False
#         self.enable_audio_preprocess = True
#         self.enable_highpass = True
#         self.enable_agc = True
#         self.enable_limiter = True
#         self.highpass_alpha = 0.962
#         self.agc_target_rms = 2600.0
#         self.agc_max_gain = 4.0
#         self.agc_smoothing = 0.18
#         self.enable_low_energy_agc_suppression = True
#         self.low_energy_rms = 900.0
#         self.low_energy_min_gain = 1.0
#         self.limiter_threshold = 28000
#         # 识别状态
#         self.text = ""  # 恢复 self.text 用于最终结果交付
#         self.is_processing = False
#         self.speech_recognizer = None
#         self.audio_stream = None
#         self.speech_config = None
#         self.conn = None
#         self._loop = None
#         self._session_pcm_chunks = []
#         self._session_started_at = 0
#         self._prev_input_sample = 0.0
#         self._prev_highpass_output = 0.0
#         self._current_agc_gain = 1.0
#         # 线程安全队列，用于接收 SDK 回调吐出的文本
#         self._result_queue = queue.Queue()
#         # 确保输出目录存在
#         os.makedirs(self.output_dir, exist_ok=True)
#         # 初始化Azure配置
#         self._init_speech_config()
#         logger.bind(tag=TAG).info(
#             f"Azure ASR初始化完成, region: {self.region}, language: {self.language}"
#         )
#     def _init_speech_config(self):
#         """初始化Azure Speech配置"""
#         try:
#             self.speech_config = speechsdk.SpeechConfig(
#                 subscription=self.subscription,
#                 region=self.region
#             )
#             self.speech_config.speech_recognition_language = self.language
#             self.speech_config.set_property(
#                 speechsdk.PropertyId.SpeechServiceResponse_RequestDetailedResultTrueFalse,
#                 "true"
#             )
#         except Exception as e:
#             logger.bind(tag=TAG).error(f"初始化Azure Speech配置失败: {e}")
#             raise
#     def _create_recognizer(self):
#         """创建新的识别器实例"""
#         try:
#             self.audio_stream = speechsdk.audio.PushAudioInputStream(
#                 stream_format=speechsdk.audio.AudioStreamFormat(
#                     samples_per_second=16000,
#                     bits_per_sample=16,
#                     channels=1
#                 )
#             )
#             audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)
#             self.speech_recognizer = speechsdk.SpeechRecognizer(
#                 speech_config=self.speech_config,
#                 audio_config=audio_config
#             )
#             self.speech_recognizer.recognizing.connect(self._on_recognizing)
#             self.speech_recognizer.recognized.connect(self._on_recognized)
#             self.speech_recognizer.canceled.connect(self._on_canceled)
#             self.speech_recognizer.session_stopped.connect(self._on_session_stopped)
#             logger.bind(tag=TAG).debug("Azure识别器创建成功")
#             return True
#         except Exception as e:
#             logger.bind(tag=TAG).error(f"创建Azure识别器失败: {e}")
#             return False
#     def _on_recognizing(self, evt):
#         """中间结果回调（识别中）- 仅用于日志"""
#         text = evt.result.text
#         if text:
#             logger.bind(tag=TAG).debug(f"识别中: {text}")
#     def _on_recognized(self, evt):
#         """最终结果回调 - 将文本塞入队列，立刻返回，不阻塞SDK线程"""
#         if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
#             text = evt.result.text
#             if text:
#                 logger.bind(tag=TAG).info(f"识别到片段: {text}")
#                 self._result_queue.put(text)
#         elif evt.result.reason == speechsdk.ResultReason.NoMatch:
#             logger.bind(tag=TAG).debug("未识别到语音")
#     def _on_canceled(self, evt):
#         """识别取消回调 - 塞入 None 作为结束信号"""
#         reason = evt.result.reason
#         if reason == speechsdk.CancellationReason.Error:
#             error_details = evt.result.error_details
#             logger.bind(tag=TAG).error(f"Azure ASR错误: {error_details}")
#         else:
#             logger.bind(tag=TAG).info(f"Azure ASR取消: {reason}")
#         self._result_queue.put(None)
#     def _on_session_stopped(self, evt):
#         """会话停止回调 - 塞入 None 作为结束信号"""
#         logger.bind(tag=TAG).debug("Azure ASR会话停止")
#         self._result_queue.put(None)
#     def _reset_audio_preprocess_state(self):
#         self._prev_input_sample = 0.0
#         self._prev_highpass_output = 0.0
#         self._current_agc_gain = 1.0

#     def _preprocess_pcm_for_asr(self, pcm_bytes: bytes) -> bytes:
#         if not self.enable_audio_preprocess or not pcm_bytes:
#             return pcm_bytes

#         sample_count = len(pcm_bytes) // 2
#         if sample_count <= 0:
#             return pcm_bytes

#         try:
#             samples = list(struct.unpack(f"<{sample_count}h", pcm_bytes))
#             processed = []

#             local_prev_input = self._prev_input_sample
#             local_prev_output = self._prev_highpass_output

#             for sample in samples:
#                 current = float(sample)
#                 if self.enable_highpass:
#                     filtered = current - local_prev_input + self.highpass_alpha * local_prev_output
#                     local_prev_input = current
#                     local_prev_output = filtered
#                 else:
#                     filtered = current
#                     local_prev_input = current
#                     local_prev_output = filtered
#                 processed.append(filtered)

#             self._prev_input_sample = local_prev_input
#             self._prev_highpass_output = local_prev_output

#             if self.enable_agc and processed:
#                 rms = math.sqrt(sum(sample * sample for sample in processed) / len(processed))
#                 if rms > 1.0:
#                     desired_gain = min(self.agc_target_rms / rms, self.agc_max_gain)
#                 else:
#                     desired_gain = self.agc_max_gain

#                 if self.enable_low_energy_agc_suppression and rms < self.low_energy_rms:
#                     energy_ratio = max(0.0, min(1.0, rms / self.low_energy_rms))
#                     suppression_gain_cap = self.low_energy_min_gain + (
#                         (self.agc_max_gain - self.low_energy_min_gain) * energy_ratio
#                     )
#                     desired_gain = min(desired_gain, suppression_gain_cap)

#                 self._current_agc_gain += (desired_gain - self._current_agc_gain) * self.agc_smoothing
#                 processed = [sample * self._current_agc_gain for sample in processed]

#             if self.enable_limiter:
#                 threshold = float(self.limiter_threshold)
#                 processed = [max(-threshold, min(threshold, sample)) for sample in processed]

#             pcm_int16 = [int(max(-32768, min(32767, round(sample)))) for sample in processed]
#             return struct.pack(f"<{len(pcm_int16)}h", *pcm_int16)
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"音频预处理失败，回退原始PCM: {e}")
#             return pcm_bytes

#     async def open_audio_channels(self, conn: "ConnectionHandler"):
#         """打开音频通道"""
#         self.conn = conn
#         self._loop = asyncio.get_event_loop()
#         await super().open_audio_channels(conn)
#     async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
#         """接收音频数据"""
#         await super().receive_audio(conn, audio, audio_have_voice)
#         # 语音停止检测：client_voice_stop 由 VAD 静默(silero.py)或前端 listen:stop 设置
#         # 参照 doubao_stream：作为正常停止处理，等待 Azure 吐出最终结果，不丢弃
#         if self.is_processing and conn.client_voice_stop:
#             logger.bind(tag=TAG).info("检测到语音停止信号，结束当前识别会话")
#             await self._stop_azure_session(fast_mode=False)
#             return
#         # 如果有声音且未建立连接，创建识别器并开始识别
#         if audio_have_voice and self.speech_recognizer is None and not self.is_processing:
#             try:
#                 self.is_processing = True
#                 self._result_queue = queue.Queue()  # 重置队列
#                 self.text = ""  # 清空旧文本
#                 self._session_pcm_chunks = []
#                 self._session_started_at = int(time.time() * 1000)
#                 self._reset_audio_preprocess_state()
#                 if not self._create_recognizer():
#                     self.is_processing = False
#                     return
#                 await asyncio.to_thread(
#                     self.speech_recognizer.start_continuous_recognition
#                 )
#                 logger.bind(tag=TAG).info("Azure ASR开始连续识别")
#                 # 发送缓存的音频数据 (排除最后1帧，避免与下方当前音频重复)
#                 if hasattr(conn, 'asr_audio') and conn.asr_audio:
#                     cached_audios = conn.asr_audio[-18:-1] if len(conn.asr_audio) > 1 else []
#                     for cached_audio in cached_audios:
#                         try:
#                             pcm_data = self.decode_opus([cached_audio])
#                             if pcm_data and self.audio_stream:
#                                 pcm_bytes = b"".join(pcm_data)
#                                 if pcm_bytes:
#                                     processed_pcm_bytes = self._preprocess_pcm_for_asr(pcm_bytes)
#                                     self._session_pcm_chunks.append(processed_pcm_bytes)
#                                     await asyncio.to_thread(
#                                         self.audio_stream.write, processed_pcm_bytes
#                                     )
#                         except Exception as e:
#                             logger.bind(tag=TAG).debug(f"发送缓存音频失败: {e}")
#             except Exception as e:
#                 logger.bind(tag=TAG).error(f"启动Azure ASR失败: {e}")
#                 self._cleanup_recognizer()
#                 return
#         # 发送当前音频数据
#         if self.speech_recognizer and self.audio_stream and self.is_processing:
#             try:
#                 pcm_data = self.decode_opus([audio])
#                 if pcm_data:
#                     pcm_bytes = b"".join(pcm_data)
#                     if pcm_bytes:
#                         processed_pcm_bytes = self._preprocess_pcm_for_asr(pcm_bytes)
#                         self._session_pcm_chunks.append(processed_pcm_bytes)
#                         await asyncio.to_thread(
#                             self.audio_stream.write, processed_pcm_bytes
#                         )
#             except Exception as e:
#                 logger.bind(tag=TAG).debug(f"发送音频数据失败: {e}")
#     def _save_debug_session_audio(self):
#         try:
#             if not self._session_pcm_chunks:
#                 return

#             os.makedirs(self.debug_audio_dir, exist_ok=True)
#             pcm_bytes = b"".join(self._session_pcm_chunks)
#             if not pcm_bytes:
#                 return

#             ts = self._session_started_at or int(time.time() * 1000)
#             session_id = getattr(self.conn, "session_id", "unknown") if self.conn else "unknown"
#             file_path = os.path.join(self.debug_audio_dir, f"azure_session_{session_id}_{ts}.wav")

#             with wave.open(file_path, "wb") as wf:
#                 wf.setnchannels(1)
#                 wf.setsampwidth(2)
#                 wf.setframerate(16000)
#                 wf.writeframes(pcm_bytes)

#             logger.bind(tag=TAG).info(f"识别音频已保存: {file_path}")
#         except Exception as e:
#             logger.bind(tag=TAG).warning(f"保存识别音频失败: {e}")
#         finally:
#             self._session_pcm_chunks = []
#             self._session_started_at = 0

#     async def _send_stop_request(self):
#         """供 listen:stop 调用，正常停止当前识别会话"""
#         if not self.is_processing:
#             logger.bind(tag=TAG).debug("收到 stop 请求，但当前没有活跃的 Azure 会话")
#             return
#         logger.bind(tag=TAG).info("收到 stop 请求，开始结束当前 Azure 会话")
#         await self._stop_azure_session(fast_mode=False)
#     async def _stop_azure_session(self, fast_mode: bool = False):
#         """统一的停止逻辑，根据是否打断采取不同策略"""
#         if not self.is_processing:
#             return
#         # 立刻置为False，防止重入
#         self.is_processing = False
#         conn = self.conn
#         audio_data = conn.asr_audio.copy() if hasattr(conn, 'asr_audio') else []
#         try:
#             # 1. 关闭音频流，告诉SDK没有新数据了
#             if self.audio_stream:
#                 try:
#                     await asyncio.to_thread(self.audio_stream.close)
#                 except Exception as e:
#                     logger.bind(tag=TAG).debug(f"关闭Azure音频流失败: {e}")
#             # 2. 停止SDK连续识别
#             if self.speech_recognizer:
#                 try:
#                     await asyncio.to_thread(
#                         self.speech_recognizer.stop_continuous_recognition
#                     )
#                 except Exception as e:
#                     logger.bind(tag=TAG).warning(f"停止 Azure 连续识别失败: {e}")
#             # 3. 从队列提取最终文本
#             final_text = ""
#             if fast_mode:
#                 # ★ 打断路径：绝不等待，立刻非阻塞清空队列，拼接已有的文本
#                 while not self._result_queue.empty():
#                     try:
#                         item = self._result_queue.get_nowait()
#                         if item is not None:
#                             final_text += item
#                     except queue.Empty:
#                         break
#             else:
#                 # ★ 正常停止路径：异步等待SDK吐出最后的结果和结束信号(None)
#                 try:
#                     while True:
#                         # 使用 to_thread 防止阻塞主事件循环，设 1.5s 超时兜底
#                         item = await asyncio.to_thread(self._result_queue.get, True, 0.8)
#                         if item is None:
#                             break  # 收到结束信号
#                         final_text += item
#                 except queue.Empty:
#                     logger.bind(tag=TAG).warning("等待 Azure 最终响应超时或队列为空")
#                 except Exception as e:
#                     logger.bind(tag=TAG).warning(f"提取队列结果异常: {e}")
#             # 4. 保存到 self.text，让后续 speech_to_text 可以返回
#             self.text = final_text
#         except Exception as e:
#             logger.bind(tag=TAG).error(f"关闭 Azure 会话时出错: {e}")
#         finally:
#             if self.enable_debug_audio_save and self.text and self.text.strip():
#                 self._save_debug_session_audio()
#             else:
#                 self._session_pcm_chunks = []
#                 self._session_started_at = 0
#             # 5. 资源清理
#             self.speech_recognizer = None
#             self.audio_stream = None
#             logger.bind(tag=TAG).info(f"Azure 会话已关闭，最终文本: {self.text}")
#             # 6. 触发业务逻辑，这将引发 handle_voice_stop -> speech_to_text 的调用链
#             if self.text and conn:
#                 logger.bind(tag=TAG).info(f"Azure stop触发处理: text={self.text}")
#                 conn.reset_audio_states()
#                 if len(audio_data) > 15:
#                     await self.handle_voice_stop(conn, audio_data)
#             elif conn:
#                 conn.reset_audio_states()
#     async def speech_to_text(
#         self,
#         opus_data: List[bytes],
#         session_id: str,
#         audio_format="opus",
#         artifacts: Optional[ASRProviderBase.AudioArtifacts] = None,
#     ) -> Tuple[Optional[str], Optional[str]]:
#         """
#         将语音数据转换为文本
#         从 self.text 取走由 _stop_azure_session 存入的最终结果并清空。
#         """
#         result = self.text
#         self.text = ""  # 取走后清空，防止脏读
#         if artifacts and artifacts.file_path:
#             logger.bind(tag=TAG).info(f"音频已保存到: {artifacts.file_path}")
#         return result, artifacts.file_path if artifacts else None
#     def _cleanup_recognizer(self):
#         """同步清理识别器资源 (用作兜底)"""
#         self.is_processing = False
#         try:
#             if self.speech_recognizer:
#                 try:
#                     self.speech_recognizer.stop_continuous_recognition()
#                 except Exception:
#                     pass
#                 self.speech_recognizer = None
#             if self.audio_stream:
#                 try:
#                     self.audio_stream.close()
#                 except Exception:
#                     pass
#                 self.audio_stream = None
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"清理识别器失败: {e}")
#     def stop_ws_connection(self):
#         """停止识别连接"""
#         self._cleanup_recognizer()
#     async def close(self):
#         """资源清理"""
#         try:
#             if self.is_processing:
#                 await self._stop_azure_session(fast_mode=True)
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"close 时停止 Azure 会话失败: {e}")
#         finally:
#             # 兜底清理
#             self._cleanup_recognizer()
#             self.speech_config = None
#             self.conn = None
#             self._loop = None
#             logger.bind(tag=TAG).info("Azure ASR资源已释放")


import asyncio
import math
import os
import queue
import struct
import time
import wave
from typing import Optional, Tuple, List, TYPE_CHECKING
import opuslib_next
import azure.cognitiveservices.speech as speechsdk
from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType
if TYPE_CHECKING:
    from core.connection import ConnectionHandler
TAG = __name__
logger = setup_logging()
class ASRProvider(ASRProviderBase):
    """Azure Speech Service 流式ASR实现
    采用队列解耦模式处理 SDK 多线程回调，同时遵循框架规范，
    通过 self.text 桥接结果，最终由 speech_to_text 方法统一交付。
    """
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.delete_audio_file = delete_audio_file
        # Azure配置
        self.subscription = config.get("speech_key")
        self.region = config.get("service_region")
        self.language = config.get("language", "zh-CN")
        self.output_dir = config.get("output_dir", "tmp/")
        self.debug_audio_dir = "/root/spanish/main/xiaozhi-server/tmp"
        self.enable_debug_audio_save = False
        self.enable_audio_preprocess = True
        self.enable_highpass = True
        self.enable_agc = True
        self.enable_limiter = True
        self.highpass_alpha = 0.962
        self.agc_target_rms = 2600.0
        self.agc_max_gain = 4.0
        self.agc_smoothing = 0.18
        self.enable_low_energy_agc_suppression = True
        self.low_energy_rms = 900.0
        self.low_energy_min_gain = 1.0
        self.limiter_threshold = 28000
        self.client_voice_stop_grace_ms = 200
        # 停止宽限窗口：收到VAD停止信号后不立即停止，窗口内检测到语音恢复则取消停止，
        # 避免句中停顿被误判为句尾导致"只识别半句"。可经 config["stop_grace_ms"] 调整。
        self.stop_grace_ms = int(config.get("stop_grace_ms", 500))
        # 识别状态
        self.text = ""  # 恢复 self.text 用于最终结果交付
        self._last_partial_text = ""  # 最近一次识别中(partial)结果，停止时作为兜底文本
        self._stop_pending_at = 0.0  # 停止宽限窗口起始时间戳
        self.is_processing = False
        self.speech_recognizer = None
        self.audio_stream = None
        self.speech_config = None
        self.conn = None
        self._loop = None
        self._session_pcm_chunks = []
        self._session_started_at = 0
        self._prev_input_sample = 0.0
        self._prev_highpass_output = 0.0
        self._current_agc_gain = 1.0
        # 复用Opus解码器，避免每帧新建（参照doubao_stream）；每个新会话重建一次以获得干净状态
        self.decoder = opuslib_next.Decoder(16000, 1)
        # 线程安全队列，用于接收 SDK 回调吐出的文本
        self._result_queue = queue.Queue()
        # 确保输出目录存在
        os.makedirs(self.output_dir, exist_ok=True)
        # 初始化Azure配置
        self._init_speech_config()
        logger.bind(tag=TAG).info(
            f"Azure ASR初始化完成, region: {self.region}, language: {self.language}"
        )
    def _init_speech_config(self):
        """初始化Azure Speech配置"""
        try:
            self.speech_config = speechsdk.SpeechConfig(
                subscription=self.subscription,
                region=self.region
            )
            self.speech_config.speech_recognition_language = self.language
            self.speech_config.set_property(
                speechsdk.PropertyId.SpeechServiceResponse_RequestDetailedResultTrueFalse,
                "true"
            )
        except Exception as e:
            logger.bind(tag=TAG).error(f"初始化Azure Speech配置失败: {e}")
            raise
    def _create_recognizer(self):
        """创建新的识别器实例"""
        try:
            self.audio_stream = speechsdk.audio.PushAudioInputStream(
                stream_format=speechsdk.audio.AudioStreamFormat(
                    samples_per_second=16000,
                    bits_per_sample=16,
                    channels=1
                )
            )
            audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)
            self.speech_recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=audio_config
            )
            self.speech_recognizer.recognizing.connect(self._on_recognizing)
            self.speech_recognizer.recognized.connect(self._on_recognized)
            self.speech_recognizer.canceled.connect(self._on_canceled)
            self.speech_recognizer.session_stopped.connect(self._on_session_stopped)
            logger.bind(tag=TAG).debug("Azure识别器创建成功")
            return True
        except Exception as e:
            logger.bind(tag=TAG).error(f"创建Azure识别器失败: {e}")
            return False
    def _on_recognizing(self, evt):
        """中间结果回调（识别中）- 记录最近partial，停止时作为兜底文本"""
        text = evt.result.text
        if text:
            self._last_partial_text = text
            logger.bind(tag=TAG).debug(f"识别中: {text}")
    def _on_recognized(self, evt):
        """最终结果回调 - 将文本塞入队列，立刻返回，不阻塞SDK线程"""
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            text = evt.result.text
            if text:
                logger.bind(tag=TAG).info(f"识别到片段: {text}")
                self._result_queue.put(text)
        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            logger.bind(tag=TAG).debug("未识别到语音")
    def _on_canceled(self, evt):
        """识别取消回调 - 塞入 None 作为结束信号"""
        reason = evt.result.reason
        if reason == speechsdk.CancellationReason.Error:
            error_details = evt.result.error_details
            logger.bind(tag=TAG).error(f"Azure ASR错误: {error_details}")
        else:
            logger.bind(tag=TAG).info(f"Azure ASR取消: {reason}")
        self._result_queue.put(None)
    def _on_session_stopped(self, evt):
        """会话停止回调 - 塞入 None 作为结束信号"""
        logger.bind(tag=TAG).debug("Azure ASR会话停止")
        self._result_queue.put(None)
    def _reset_audio_preprocess_state(self):
        self._prev_input_sample = 0.0
        self._prev_highpass_output = 0.0
        self._current_agc_gain = 1.0

    def _decode_opus_frame(self, opus_packet: bytes) -> bytes:
        """复用 self.decoder 解码单帧Opus为PCM，避免每帧新建解码器。"""
        if not opus_packet or len(opus_packet) == 0:
            return b""
        if self.decoder is None:
            self.decoder = opuslib_next.Decoder(16000, 1)
        try:
            return self.decoder.decode(opus_packet, 960)
        except opuslib_next.OpusError as e:
            logger.bind(tag=TAG).warning(f"Opus解码错误，跳过该帧: {e}")
            return b""
        except Exception as e:
            logger.bind(tag=TAG).debug(f"Opus解码异常，跳过该帧: {e}")
            return b""

    def _preprocess_pcm_for_asr(self, pcm_bytes: bytes) -> bytes:
        if not self.enable_audio_preprocess or not pcm_bytes:
            return pcm_bytes

        sample_count = len(pcm_bytes) // 2
        if sample_count <= 0:
            return pcm_bytes

        try:
            samples = list(struct.unpack(f"<{sample_count}h", pcm_bytes))
            processed = []

            local_prev_input = self._prev_input_sample
            local_prev_output = self._prev_highpass_output

            for sample in samples:
                current = float(sample)
                if self.enable_highpass:
                    filtered = current - local_prev_input + self.highpass_alpha * local_prev_output
                    local_prev_input = current
                    local_prev_output = filtered
                else:
                    filtered = current
                    local_prev_input = current
                    local_prev_output = filtered
                processed.append(filtered)

            self._prev_input_sample = local_prev_input
            self._prev_highpass_output = local_prev_output

            if self.enable_agc and processed:
                rms = math.sqrt(sum(sample * sample for sample in processed) / len(processed))
                if rms > 1.0:
                    desired_gain = min(self.agc_target_rms / rms, self.agc_max_gain)
                else:
                    desired_gain = self.agc_max_gain

                if self.enable_low_energy_agc_suppression and rms < self.low_energy_rms:
                    energy_ratio = max(0.0, min(1.0, rms / self.low_energy_rms))
                    suppression_gain_cap = self.low_energy_min_gain + (
                        (self.agc_max_gain - self.low_energy_min_gain) * energy_ratio
                    )
                    desired_gain = min(desired_gain, suppression_gain_cap)

                self._current_agc_gain += (desired_gain - self._current_agc_gain) * self.agc_smoothing
                processed = [sample * self._current_agc_gain for sample in processed]

            if self.enable_limiter:
                threshold = float(self.limiter_threshold)
                processed = [max(-threshold, min(threshold, sample)) for sample in processed]

            pcm_int16 = [int(max(-32768, min(32767, round(sample)))) for sample in processed]
            return struct.pack(f"<{len(pcm_int16)}h", *pcm_int16)
        except Exception as e:
            logger.bind(tag=TAG).debug(f"音频预处理失败，回退原始PCM: {e}")
            return pcm_bytes

    async def open_audio_channels(self, conn: "ConnectionHandler"):
        """打开音频通道"""
        self.conn = conn
        self._loop = asyncio.get_event_loop()
        await super().open_audio_channels(conn)
    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        """接收音频数据"""
        await super().receive_audio(conn, audio, audio_have_voice)
        # 语音停止检测：client_voice_stop 由 VAD(silero.py) 在 silence_threshold_ms 静默后置位。
        # 为避免用户句中停顿被误判为句尾导致"只识别半句"，采用非阻塞宽限窗口：
        # 收到停止信号后不立即停止，继续把音频喂给Azure；若窗口内检测到语音恢复则取消停止
        # 继续识别整句，否则窗口到期才真正停止。(listen:stop 走 _send_stop_request，不经此处)
        if self.is_processing and conn.client_voice_stop:
            if audio_have_voice:
                # 宽限窗口内语音恢复 → 取消停止，继续识别整句
                logger.bind(tag=TAG).info("停止信号后检测到语音恢复，取消停止继续识别")
                conn.client_voice_stop = False
                self._stop_pending_at = 0.0
            elif self._stop_pending_at == 0.0:
                # 首次收到停止信号，启动宽限窗口
                self._stop_pending_at = time.monotonic()
                logger.bind(tag=TAG).info(
                    f"收到停止信号，进入{self.stop_grace_ms}ms宽限窗口等待语音恢复"
                )
            elif (time.monotonic() - self._stop_pending_at) * 1000 >= self.stop_grace_ms:
                # 宽限窗口到期且未恢复 → 真正停止
                logger.bind(tag=TAG).info("宽限窗口到期，未检测到语音恢复，结束识别会话")
                self._stop_pending_at = 0.0
                await self._stop_azure_session(fast_mode=False)
                return
            # 仍在宽限窗口内：继续向下把音频发送给Azure
        # 如果有声音且未建立连接，创建识别器并开始识别
        if audio_have_voice and self.speech_recognizer is None and not self.is_processing:
            try:
                self.is_processing = True
                self._result_queue = queue.Queue()  # 重置队列
                self.text = ""  # 清空旧文本
                self._last_partial_text = ""  # 清空旧partial
                self._stop_pending_at = 0.0  # 清空宽限窗口
                self._session_pcm_chunks = []
                self._session_started_at = int(time.time() * 1000)
                self._reset_audio_preprocess_state()
                # 每个新会话重建Opus解码器，保证干净的解码状态
                self.decoder = opuslib_next.Decoder(16000, 1)
                if not self._create_recognizer():
                    self.is_processing = False
                    return
                await asyncio.to_thread(
                    self.speech_recognizer.start_continuous_recognition
                )
                logger.bind(tag=TAG).info("Azure ASR开始连续识别")
                # 发送缓存的音频数据 (排除最后1帧，避免与下方当前音频重复)
                if hasattr(conn, 'asr_audio') and conn.asr_audio:
                    cached_audios = conn.asr_audio[-10:-1] if len(conn.asr_audio) > 1 else []
                    for cached_audio in cached_audios:
                        try:
                            pcm_bytes = self._decode_opus_frame(cached_audio)
                            if pcm_bytes and self.audio_stream:
                                processed_pcm_bytes = self._preprocess_pcm_for_asr(pcm_bytes)
                                self._session_pcm_chunks.append(processed_pcm_bytes)
                                await asyncio.to_thread(
                                    self.audio_stream.write, processed_pcm_bytes
                                )
                        except Exception as e:
                            logger.bind(tag=TAG).debug(f"发送缓存音频失败: {e}")
            except Exception as e:
                logger.bind(tag=TAG).error(f"启动Azure ASR失败: {e}")
                self._cleanup_recognizer()
                return
        # 发送当前音频数据
        if self.speech_recognizer and self.audio_stream and self.is_processing:
            try:
                pcm_bytes = self._decode_opus_frame(audio)
                if pcm_bytes:
                    processed_pcm_bytes = self._preprocess_pcm_for_asr(pcm_bytes)
                    self._session_pcm_chunks.append(processed_pcm_bytes)
                    await asyncio.to_thread(
                        self.audio_stream.write, processed_pcm_bytes
                    )
            except Exception as e:
                logger.bind(tag=TAG).debug(f"发送音频数据失败: {e}")
    def _save_debug_session_audio(self):
        try:
            if not self._session_pcm_chunks:
                return

            os.makedirs(self.debug_audio_dir, exist_ok=True)
            pcm_bytes = b"".join(self._session_pcm_chunks)
            if not pcm_bytes:
                return

            ts = self._session_started_at or int(time.time() * 1000)
            session_id = getattr(self.conn, "session_id", "unknown") if self.conn else "unknown"
            file_path = os.path.join(self.debug_audio_dir, f"azure_session_{session_id}_{ts}.wav")

            with wave.open(file_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm_bytes)

            logger.bind(tag=TAG).info(f"识别音频已保存: {file_path}")
        except Exception as e:
            logger.bind(tag=TAG).warning(f"保存识别音频失败: {e}")
        finally:
            self._session_pcm_chunks = []
            self._session_started_at = 0

    async def _send_stop_request(self):
        """供 listen:stop 调用，正常停止当前识别会话"""
        if not self.is_processing:
            logger.bind(tag=TAG).debug("收到 stop 请求，但当前没有活跃的 Azure 会话")
            return
        logger.bind(tag=TAG).info("收到 stop 请求，开始结束当前 Azure 会话")
        await self._stop_azure_session(fast_mode=False)
    async def _drain_final_results(
        self, total_timeout: float = 2.0, per_get_timeout: float = 0.3
    ) -> str:
        """从结果队列提取最终文本，直到收到结束信号(None)或总超时。

        优先以 session_stopped/canceled 投递的 None 作为结束标志，保证 Azure
        在流关闭后吐出的最后一段文本不被丢弃；总超时作为兜底，避免 SDK 异常时
        无限等待。单次 get 空超时不立即退出，继续等待直到 None 或总超时。
        """
        final_text = ""
        deadline = time.monotonic() + total_timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.bind(tag=TAG).warning("等待 Azure 最终结果总超时，使用已收集文本")
                break
            get_timeout = min(per_get_timeout, remaining)
            try:
                item = await asyncio.to_thread(self._result_queue.get, True, get_timeout)
            except queue.Empty:
                # 本轮未收到，继续等直到 deadline 或 None
                continue
            if item is None:
                break  # 收到结束信号
            final_text += item
        # 最终非阻塞扫尾，捡漏 stop 期间到达的结果
        while not self._result_queue.empty():
            try:
                item = self._result_queue.get_nowait()
                if item is not None:
                    final_text += item
            except queue.Empty:
                break
        return final_text

    async def _stop_azure_session(self, fast_mode: bool = False):
        """统一的停止逻辑，根据是否打断采取不同策略"""
        if not self.is_processing:
            return
        # 立刻置为False，防止重入
        self.is_processing = False
        conn = self.conn
        audio_data = conn.asr_audio.copy() if hasattr(conn, 'asr_audio') else []
        try:
            # 1. 先优雅停止连续识别，让SDK有机会finalize当前进行中的utterance。
            #    若先close音频流，Azure会以Canceled结束，丢失进行中的识别结果。
            if self.speech_recognizer:
                try:
                    await asyncio.to_thread(
                        self.speech_recognizer.stop_continuous_recognition
                    )
                except Exception as e:
                    logger.bind(tag=TAG).warning(f"停止 Azure 连续识别失败: {e}")
            # 2. 关闭音频流（清理资源）
            if self.audio_stream:
                try:
                    await asyncio.to_thread(self.audio_stream.close)
                except Exception as e:
                    logger.bind(tag=TAG).debug(f"关闭Azure音频流失败: {e}")
            # 3. 从队列提取最终文本
            if fast_mode:
                # ★ 打断路径：绝不等待，立刻非阻塞清空队列，拼接已有文本
                final_text = ""
                while not self._result_queue.empty():
                    try:
                        item = self._result_queue.get_nowait()
                        if item is not None:
                            final_text += item
                    except queue.Empty:
                        break
            else:
                # ★ 正常停止路径：等待SDK吐出最后的结果和结束信号(None)，带总超时兜底
                final_text = await self._drain_final_results()
            # 4. 兜底：若没有最终结果，使用最近一次partial(识别中)结果，
            #    避免VAD提前触发停止或Azure以Canceled结束时丢失进行中的语音
            if not final_text and self._last_partial_text:
                final_text = self._last_partial_text
                logger.bind(tag=TAG).info(f"使用partial兜底文本: {final_text}")
            # 5. 保存到 self.text，让后续 speech_to_text 可以返回
            self.text = final_text
        except Exception as e:
            logger.bind(tag=TAG).error(f"关闭 Azure 会话时出错: {e}")
        finally:
            if self.enable_debug_audio_save and self.text and self.text.strip():
                self._save_debug_session_audio()
            else:
                self._session_pcm_chunks = []
                self._session_started_at = 0
            self._last_partial_text = ""
            # 6. 资源清理
            self.speech_recognizer = None
            self.audio_stream = None
            logger.bind(tag=TAG).info(f"Azure 会话已关闭，最终文本: {self.text}")
            # 7. 触发业务逻辑，这将引发 handle_voice_stop -> speech_to_text 的调用链
            if self.text and conn:
                logger.bind(tag=TAG).info(f"Azure stop触发处理: text={self.text}")
                conn.reset_audio_states()
                if len(audio_data) > 15:
                    await self.handle_voice_stop(conn, audio_data)
            elif conn:
                conn.reset_audio_states()
            # 8. 清空待处理音频队列，丢弃停止期间堆积的尾音/半句音频，
            #    防止 asr 线程恢复后把这些残留包当成新一轮继续识别触发误回复
            self._stop_pending_at = 0.0
            if conn is not None and hasattr(conn, "asr_audio_queue"):
                cleared = 0
                while True:
                    try:
                        conn.asr_audio_queue.get_nowait()
                        cleared += 1
                    except queue.Empty:
                        break
                if cleared:
                    logger.bind(tag=TAG).info(f"已清空堆积音频包: {cleared}")
    async def speech_to_text(
        self,
        opus_data: List[bytes],
        session_id: str,
        audio_format="opus",
        artifacts: Optional[ASRProviderBase.AudioArtifacts] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        将语音数据转换为文本
        从 self.text 取走由 _stop_azure_session 存入的最终结果并清空。
        """
        result = self.text
        self.text = ""  # 取走后清空，防止脏读
        if artifacts and artifacts.file_path:
            logger.bind(tag=TAG).info(f"音频已保存到: {artifacts.file_path}")
        return result, artifacts.file_path if artifacts else None
    def _cleanup_recognizer(self):
        """同步清理识别器资源 (用作兜底)"""
        self.is_processing = False
        try:
            if self.speech_recognizer:
                try:
                    self.speech_recognizer.stop_continuous_recognition()
                except Exception:
                    pass
                self.speech_recognizer = None
            if self.audio_stream:
                try:
                    self.audio_stream.close()
                except Exception:
                    pass
                self.audio_stream = None
        except Exception as e:
            logger.bind(tag=TAG).debug(f"清理识别器失败: {e}")
    def stop_ws_connection(self):
        """停止识别连接"""
        self._cleanup_recognizer()
    async def close(self):
        """资源清理"""
        try:
            if self.is_processing:
                await self._stop_azure_session(fast_mode=True)
        except Exception as e:
            logger.bind(tag=TAG).debug(f"close 时停止 Azure 会话失败: {e}")
        finally:
            # 兜底清理
            self._cleanup_recognizer()
            self.speech_config = None
            self.conn = None
            self._loop = None
            if hasattr(self, "decoder") and self.decoder is not None:
                try:
                    del self.decoder
                except Exception:
                    pass
                self.decoder = None
            logger.bind(tag=TAG).info("Azure ASR资源已释放")


# import asyncio
# import math
# import os
# import queue
# import struct
# import time
# import wave
# from typing import Optional, Tuple, List, TYPE_CHECKING
# import opuslib_next
# import azure.cognitiveservices.speech as speechsdk
# from core.providers.asr.base import ASRProviderBase
# from config.logger import setup_logging
# from core.providers.asr.dto.dto import InterfaceType
# if TYPE_CHECKING:
#     from core.connection import ConnectionHandler

# TAG = __name__
# logger = setup_logging()


# class ASRProvider(ASRProviderBase):
#     """Azure Speech Service 流式ASR实现（结果驱动版）。

#     与 doubao_stream 对齐：以 Azure 的 `recognized`（句尾最终结果）作为主驱动，
#     拿到最终结果即收尾并触发 handle_voice_stop；VAD 的 client_voice_stop 仅作
#     兜底（Azure 迟迟不分句时强制收尾 + partial 兜底）。从而避免 VAD 与 SDK 对抗
#     造成的"识别半句/停止丢字/残留包误识别"问题。
#     """

#     def __init__(self, config, delete_audio_file):
#         super().__init__()
#         self.interface_type = InterfaceType.STREAM
#         self.config = config
#         self.delete_audio_file = delete_audio_file

#         # Azure配置
#         self.subscription = config.get("speech_key")
#         self.region = config.get("service_region")
#         self.language = config.get("language", "zh-CN")
#         self.output_dir = config.get("output_dir", "tmp/")

#         # 调试音频落盘（默认关闭）
#         self.debug_audio_dir = config.get("debug_audio_dir", "tmp/")
#         self.enable_debug_audio_save = False

#         # 音频预处理参数
#         self.enable_audio_preprocess = True
#         self.enable_highpass = True
#         self.enable_agc = True
#         self.enable_limiter = True
#         self.highpass_alpha = 0.962
#         self.agc_target_rms = 2600.0
#         self.agc_max_gain = 4.0
#         self.agc_smoothing = 0.18
#         self.enable_low_energy_agc_suppression = True
#         self.low_energy_rms = 900.0
#         self.low_energy_min_gain = 1.0
#         self.limiter_threshold = 28000

#         # 兜底收尾：VAD停止后等待Azure最终结果的最长时间(ms)
#         self.abort_wait_ms = int(config.get("abort_wait_ms", 1500))

#         # 识别状态
#         self.text = ""
#         self.is_processing = False
#         self._finished = False  # 本轮是否已收尾（保证 _do_finalize 只执行一次）
#         self._sdk_stopped = False  # SDK 是否已调用 stop_continuous_recognition
#         self._final_event = None  # asyncio.Event，recognized 到达时置位
#         self._last_partial_text = ""  # 最近一次 partial，兜底用
#         self.speech_recognizer = None
#         self.audio_stream = None
#         self.speech_config = None
#         self.conn = None
#         self._loop = None

#         # 调试音频缓冲
#         self._session_pcm_chunks = []
#         self._session_started_at = 0

#         # 音频预处理跨帧状态
#         self._prev_input_sample = 0.0
#         self._prev_highpass_output = 0.0
#         self._current_agc_gain = 1.0

#         # 复用Opus解码器，避免每帧新建；每个新会话重建一次以获得干净状态
#         self.decoder = opuslib_next.Decoder(16000, 1)

#         os.makedirs(self.output_dir, exist_ok=True)
#         self._init_speech_config()
#         logger.bind(tag=TAG).info(
#             f"Azure ASR(结果驱动)初始化完成, region: {self.region}, language: {self.language}"
#         )

#     def _init_speech_config(self):
#         try:
#             self.speech_config = speechsdk.SpeechConfig(
#                 subscription=self.subscription, region=self.region
#             )
#             self.speech_config.speech_recognition_language = self.language
#             self.speech_config.set_property(
#                 speechsdk.PropertyId.SpeechServiceResponse_RequestDetailedResultTrueFalse,
#                 "true",
#             )
#         except Exception as e:
#             logger.bind(tag=TAG).error(f"初始化Azure Speech配置失败: {e}")
#             raise

#     def _create_recognizer(self):
#         try:
#             self.audio_stream = speechsdk.audio.PushAudioInputStream(
#                 stream_format=speechsdk.audio.AudioStreamFormat(
#                     samples_per_second=16000, bits_per_sample=16, channels=1
#                 )
#             )
#             audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)
#             self.speech_recognizer = speechsdk.SpeechRecognizer(
#                 speech_config=self.speech_config, audio_config=audio_config
#             )
#             self.speech_recognizer.recognizing.connect(self._on_recognizing)
#             self.speech_recognizer.recognized.connect(self._on_recognized)
#             self.speech_recognizer.canceled.connect(self._on_canceled)
#             self.speech_recognizer.session_stopped.connect(self._on_session_stopped)
#             logger.bind(tag=TAG).debug("Azure识别器创建成功")
#             return True
#         except Exception as e:
#             logger.bind(tag=TAG).error(f"创建Azure识别器失败: {e}")
#             return False

#     # ---------- SDK 回调（运行在SDK线程） ----------

#     def _on_recognizing(self, evt):
#         """中间结果：记录最近partial，用于兜底。"""
#         text = evt.result.text
#         if text:
#             self._last_partial_text = text
#             logger.bind(tag=TAG).debug(f"识别中: {text}")

#     def _on_recognized(self, evt):
#         """结果驱动核心：Azure判定一句结束 → 投递到事件循环收尾。"""
#         if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
#             text = evt.result.text
#             if text and self._loop:
#                 # 跨线程调度到事件循环，避免在SDK线程里做await
#                 asyncio.run_coroutine_threadsafe(self._handle_final(text), self._loop)
#         elif evt.result.reason == speechsdk.ResultReason.NoMatch:
#             logger.bind(tag=TAG).debug("未识别到语音(NoMatch)")

#     def _on_canceled(self, evt):
#         reason = evt.result.reason
#         if reason == speechsdk.CancellationReason.Error:
#             logger.bind(tag=TAG).error(f"Azure ASR错误: {evt.result.errorDetails}")
#         else:
#             logger.bind(tag=TAG).info(f"Azure ASR取消: {reason}")

#     def _on_session_stopped(self, evt):
#         logger.bind(tag=TAG).debug("Azure ASR会话停止")

#     # ---------- 收尾状态机（运行在事件循环） ----------

#     async def _handle_final(self, text: str):
#         """拿到Azure最终结果 → 置位、触发收尾。先到先得。"""
#         if self._finished or not self.is_processing:
#             return
#         self._finished = True
#         if self._final_event is not None:
#             self._final_event.set()
#         self.text = text
#         logger.bind(tag=TAG).info(f"识别到片段(最终): {text}")
#         await self._do_finalize()

#     async def _abort_session(self, prompt_stop: bool = True):
#         """VAD/手动停止兜底：提示SDK收尾并等待recognized，超时则用partial。"""
#         if self._finished or not self.is_processing:
#             return
#         # 提示SDK收尾（让其finalize进行中的utterance，可能触发recognized）
#         if prompt_stop and self.speech_recognizer and not self._sdk_stopped:
#             self._sdk_stopped = True
#             try:
#                 await asyncio.to_thread(self.speech_recognizer.stop_continuous_recognition)
#             except Exception as e:
#                 logger.bind(tag=TAG).warning(f"停止Azure连续识别失败: {e}")
#         # 等待 _handle_final 接管（recognized 到达会置位 event）
#         if self._final_event is not None:
#             try:
#                 await asyncio.wait_for(
#                     self._final_event.wait(), timeout=self.abort_wait_ms / 1000
#                 )
#             except asyncio.TimeoutError:
#                 pass
#         if self._finished:
#             return  # recognized 已接管并完成收尾
#         # 超时仍未拿到最终结果 → partial 兜底
#         self._finished = True
#         if self._last_partial_text:
#             self.text = self._last_partial_text
#             logger.bind(tag=TAG).info(f"使用partial兜底文本: {self.text}")
#         else:
#             self.text = ""
#         await self._do_finalize()

#     async def _do_finalize(self):
#         """实际的停止SDK + 清理 + 触发业务链。由 _handle_final / _abort_session 调用，
#         `_finished` 保证只执行一次。"""
#         if not self.is_processing:
#             return
#         self.is_processing = False
#         conn = self.conn
#         audio_data = conn.asr_audio.copy() if hasattr(conn, "asr_audio") else []
#         # 停止SDK连续识别（若未停过）
#         if self.speech_recognizer and not self._sdk_stopped:
#             self._sdk_stopped = True
#             try:
#                 await asyncio.to_thread(self.speech_recognizer.stop_continuous_recognition)
#             except Exception as e:
#                 logger.bind(tag=TAG).warning(f"停止Azure连续识别失败: {e}")
#         # 关闭音频流
#         if self.audio_stream:
#             try:
#                 await asyncio.to_thread(self.audio_stream.close)
#             except Exception as e:
#                 logger.bind(tag=TAG).debug(f"关闭Azure音频流失败: {e}")
#         # 清空待处理音频队列，丢弃停止期间堆积的尾音/半句音频，防止被当成新一轮误识别
#         self._clear_asr_audio_queue(conn)
#         # 调试音频落盘
#         if self.enable_debug_audio_save and self.text and self.text.strip():
#             self._save_debug_session_audio()
#         else:
#             self._session_pcm_chunks = []
#             self._session_started_at = 0
#         self._last_partial_text = ""
#         self.speech_recognizer = None
#         self.audio_stream = None
#         logger.bind(tag=TAG).info(f"Azure会话已关闭，最终文本: {self.text}")
#         # 触发业务链：handle_voice_stop -> speech_to_text
#         if self.text and conn:
#             logger.bind(tag=TAG).info(f"Azure最终结果触发处理: text={self.text}")
#             conn.reset_audio_states()
#             await self.handle_voice_stop(conn, audio_data)
#         elif conn:
#             conn.reset_audio_states()

#     def _clear_asr_audio_queue(self, conn):
#         if conn is not None and hasattr(conn, "asr_audio_queue"):
#             cleared = 0
#             while True:
#                 try:
#                     conn.asr_audio_queue.get_nowait()
#                     cleared += 1
#                 except queue.Empty:
#                     break
#             if cleared:
#                 logger.bind(tag=TAG).info(f"已清空堆积音频包: {cleared}")

#     # ---------- 音频预处理 ----------

#     def _reset_audio_preprocess_state(self):
#         self._prev_input_sample = 0.0
#         self._prev_highpass_output = 0.0
#         self._current_agc_gain = 1.0

#     def _decode_opus_frame(self, opus_packet: bytes) -> bytes:
#         """复用 self.decoder 解码单帧Opus为PCM，避免每帧新建解码器。"""
#         if not opus_packet or len(opus_packet) == 0:
#             return b""
#         if self.decoder is None:
#             self.decoder = opuslib_next.Decoder(16000, 1)
#         try:
#             return self.decoder.decode(opus_packet, 960)
#         except opuslib_next.OpusError as e:
#             logger.bind(tag=TAG).warning(f"Opus解码错误，跳过该帧: {e}")
#             return b""
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"Opus解码异常，跳过该帧: {e}")
#             return b""

#     def _preprocess_pcm_for_asr(self, pcm_bytes: bytes) -> bytes:
#         if not self.enable_audio_preprocess or not pcm_bytes:
#             return pcm_bytes
#         sample_count = len(pcm_bytes) // 2
#         if sample_count <= 0:
#             return pcm_bytes
#         try:
#             samples = list(struct.unpack(f"<{sample_count}h", pcm_bytes))
#             processed = []
#             local_prev_input = self._prev_input_sample
#             local_prev_output = self._prev_highpass_output
#             for sample in samples:
#                 current = float(sample)
#                 if self.enable_highpass:
#                     filtered = current - local_prev_input + self.highpass_alpha * local_prev_output
#                     local_prev_input = current
#                     local_prev_output = filtered
#                 else:
#                     filtered = current
#                     local_prev_input = current
#                     local_prev_output = filtered
#                 processed.append(filtered)
#             self._prev_input_sample = local_prev_input
#             self._prev_highpass_output = local_prev_output
#             if self.enable_agc and processed:
#                 rms = math.sqrt(sum(s * s for s in processed) / len(processed))
#                 if rms > 1.0:
#                     desired_gain = min(self.agc_target_rms / rms, self.agc_max_gain)
#                 else:
#                     desired_gain = self.agc_max_gain
#                 if self.enable_low_energy_agc_suppression and rms < self.low_energy_rms:
#                     energy_ratio = max(0.0, min(1.0, rms / self.low_energy_rms))
#                     cap = self.low_energy_min_gain + (self.agc_max_gain - self.low_energy_min_gain) * energy_ratio
#                     desired_gain = min(desired_gain, cap)
#                 self._current_agc_gain += (desired_gain - self._current_agc_gain) * self.agc_smoothing
#                 processed = [s * self._current_agc_gain for s in processed]
#             if self.enable_limiter:
#                 threshold = float(self.limiter_threshold)
#                 processed = [max(-threshold, min(threshold, s)) for s in processed]
#             pcm_int16 = [int(max(-32768, min(32767, round(s)))) for s in processed]
#             return struct.pack(f"<{len(pcm_int16)}h", *pcm_int16)
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"音频预处理失败，回退原始PCM: {e}")
#             return pcm_bytes

#     def _save_debug_session_audio(self):
#         try:
#             if not self._session_pcm_chunks:
#                 return
#             os.makedirs(self.debug_audio_dir, exist_ok=True)
#             pcm_bytes = b"".join(self._session_pcm_chunks)
#             if not pcm_bytes:
#                 return
#             ts = self._session_started_at or int(time.time() * 1000)
#             session_id = getattr(self.conn, "session_id", "unknown") if self.conn else "unknown"
#             file_path = os.path.join(self.debug_audio_dir, f"azure_session_{session_id}_{ts}.wav")
#             with wave.open(file_path, "wb") as wf:
#                 wf.setnchannels(1)
#                 wf.setsampwidth(2)
#                 wf.setframerate(16000)
#                 wf.writeframes(pcm_bytes)
#             logger.bind(tag=TAG).info(f"识别音频已保存: {file_path}")
#         except Exception as e:
#             logger.bind(tag=TAG).warning(f"保存识别音频失败: {e}")
#         finally:
#             self._session_pcm_chunks = []
#             self._session_started_at = 0

#     # ---------- 框架接口 ----------

#     async def open_audio_channels(self, conn: "ConnectionHandler"):
#         self.conn = conn
#         self._loop = asyncio.get_event_loop()
#         await super().open_audio_channels(conn)

#     async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
#         await super().receive_audio(conn, audio, audio_have_voice)

#         # VAD兜底：client_voice_stop 由 silero VAD 在 silence_threshold_ms 静默后置位。
#         # 结果驱动下 Azure 通常已自行吐 recognized 完成收尾；若 Azure 迟迟未分句，则在此兜底。
#         if self.is_processing and conn.client_voice_stop:
#             logger.bind(tag=TAG).info("VAD停止信号兜底，强制收尾当前识别会话")
#             await self._abort_session(prompt_stop=True)
#             return

#         # 建连：有声音且无识别器时，创建识别器并开始连续识别
#         if audio_have_voice and self.speech_recognizer is None and not self.is_processing:
#             try:
#                 self.is_processing = True
#                 self._finished = False
#                 self._sdk_stopped = False
#                 self._final_event = asyncio.Event()
#                 self.text = ""
#                 self._last_partial_text = ""
#                 self._session_pcm_chunks = []
#                 self._session_started_at = int(time.time() * 1000)
#                 self._reset_audio_preprocess_state()
#                 self.decoder = opuslib_next.Decoder(16000, 1)
#                 if not self._create_recognizer():
#                     self.is_processing = False
#                     return
#                 await asyncio.to_thread(
#                     self.speech_recognizer.start_continuous_recognition
#                 )
#                 logger.bind(tag=TAG).info("Azure ASR开始连续识别")
#                 # 发送缓存的音频数据（排除最后1帧，避免与下方当前音频重复）
#                 if hasattr(conn, "asr_audio") and conn.asr_audio:
#                     cached_audios = conn.asr_audio[-10:-1] if len(conn.asr_audio) > 1 else []
#                     for cached_audio in cached_audios:
#                         try:
#                             pcm_bytes = self._decode_opus_frame(cached_audio)
#                             if pcm_bytes and self.audio_stream:
#                                 processed = self._preprocess_pcm_for_asr(pcm_bytes)
#                                 self._session_pcm_chunks.append(processed)
#                                 await asyncio.to_thread(self.audio_stream.write, processed)
#                         except Exception as e:
#                             logger.bind(tag=TAG).debug(f"发送缓存音频失败: {e}")
#             except Exception as e:
#                 logger.bind(tag=TAG).error(f"启动Azure ASR失败: {e}")
#                 self._cleanup_recognizer()
#                 return

#         # 发送当前音频数据
#         if self.speech_recognizer and self.audio_stream and self.is_processing:
#             try:
#                 pcm_bytes = self._decode_opus_frame(audio)
#                 if pcm_bytes:
#                     processed = self._preprocess_pcm_for_asr(pcm_bytes)
#                     self._session_pcm_chunks.append(processed)
#                     await asyncio.to_thread(self.audio_stream.write, processed)
#             except Exception as e:
#                 logger.bind(tag=TAG).debug(f"发送音频数据失败: {e}")

#     async def _send_stop_request(self):
#         """供 listen:stop 调用：强制收尾并尽量拿到最终结果。"""
#         if not self.is_processing:
#             logger.bind(tag=TAG).debug("收到 stop 请求，但当前没有活跃的 Azure 会话")
#             return
#         logger.bind(tag=TAG).info("收到 stop 请求，开始结束当前 Azure 会话")
#         await self._abort_session(prompt_stop=True)

#     async def speech_to_text(
#         self,
#         opus_data: List[bytes],
#         session_id: str,
#         audio_format="opus",
#         artifacts: Optional[ASRProviderBase.AudioArtifacts] = None,
#     ) -> Tuple[Optional[str], Optional[str]]:
#         """从 self.text 取走由 _handle_final/_abort_session 写入的最终结果并清空。"""
#         result = self.text
#         self.text = ""
#         if artifacts and artifacts.file_path:
#             logger.bind(tag=TAG).info(f"音频已保存到: {artifacts.file_path}")
#         return result, artifacts.file_path if artifacts else None

#     def _cleanup_recognizer(self):
#         """同步清理识别器资源（兜底）。"""
#         self.is_processing = False
#         try:
#             if self.speech_recognizer:
#                 try:
#                     self.speech_recognizer.stop_continuous_recognition()
#                 except Exception:
#                     pass
#                 self.speech_recognizer = None
#             if self.audio_stream:
#                 try:
#                     self.audio_stream.close()
#                 except Exception:
#                     pass
#                 self.audio_stream = None
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"清理识别器失败: {e}")

#     def stop_ws_connection(self):
#         """停止识别连接（由 base.handle_voice_stop 末尾调用，此时通常已清理）。"""
#         self._cleanup_recognizer()

#     async def close(self):
#         """资源清理"""
#         try:
#             if self.is_processing:
#                 self._finished = True  # 阻止 _handle_final/_abort_session 做重活
#                 if self.speech_recognizer and not self._sdk_stopped:
#                     self._sdk_stopped = True
#                     try:
#                         await asyncio.to_thread(self.speech_recognizer.stop_continuous_recognition)
#                     except Exception:
#                         pass
#                 if self.audio_stream:
#                     try:
#                         await asyncio.to_thread(self.audio_stream.close)
#                     except Exception:
#                         pass
#                 self.is_processing = False
#         except Exception as e:
#             logger.bind(tag=TAG).debug(f"close 时停止 Azure 会话失败: {e}")
#         finally:
#             self._cleanup_recognizer()
#             self.speech_config = None
#             self.conn = None
#             self._loop = None
#             if hasattr(self, "decoder") and self.decoder is not None:
#                 try:
#                     del self.decoder
#                 except Exception:
#                     pass
#                 self.decoder = None
#             logger.bind(tag=TAG).info("Azure ASR资源已释放")



