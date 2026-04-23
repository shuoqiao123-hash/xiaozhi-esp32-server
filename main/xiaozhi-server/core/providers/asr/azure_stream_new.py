import asyncio
from typing import TYPE_CHECKING

import azure.cognitiveservices.speech as speechsdk
import opuslib_next

from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = "AzureASR"
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.delete_audio_file = delete_audio_file

        self.speech_key = config.get("speech_key")
        self.service_region = config.get("service_region")
        self.language = config.get("language", "zh-CN")

        self.output_dir = config.get("output_dir", "tmp/")
        self.final_wait_timeout = float(config.get("final_wait_timeout", 1.5))
        self.partial_result_enabled = bool(config.get("partial_result_enabled", True))
        self.end_silence_frames = int(config.get("end_silence_frames", 6))
        self.min_audio_frames = int(config.get("min_audio_frames", 15))

        if not self.speech_key:
            raise ValueError("Azure ASR 需要配置 speech_key")
        if not self.service_region:
            raise ValueError("Azure ASR 需要配置 service_region")

        self.decoder = opuslib_next.Decoder(16000, 1)
        self.audio_stream = None
        self.recognizer = None
        self.is_processing = False
        self.text = ""
        self.partial_text = ""
        self.done_event = None
        self.loop = None
        self.silence_frames = 0
        self.audio_frame_count = 0
        self._current_conn = None

    async def open_audio_channels(self, conn: "ConnectionHandler"):
        await super().open_audio_channels(conn)

    def _reset_text_state(self):
        self.text = ""
        self.partial_text = ""
        self.silence_frames = 0
        self.audio_frame_count = 0

    def _append_text(self, new_text: str):
        new_text = (new_text or "").strip()
        if not new_text:
            return

        old_text = self.text.strip()
        if not old_text:
            self.text = new_text
            return

        if new_text.startswith(old_text):
            self.text = new_text
            return

        if old_text.endswith(new_text):
            return

        self.text = f"{old_text} {new_text}".strip()

    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        if not hasattr(conn, "asr_audio_for_voiceprint"):
            conn.asr_audio_for_voiceprint = []

        if audio:
            conn.asr_audio_for_voiceprint.append(audio)
            if self.is_processing:
                logger.bind(tag=TAG).debug(f"收到音频包: opus长度={len(audio)}")

        if audio and not self.is_processing:
            should_start = False
            if conn.client_listen_mode == "manual":
                should_start = True
            elif audio_have_voice:
                should_start = True

            if should_start:
                logger.bind(tag=TAG).info(">>> Azure 检测到起点，开始流式识别")
                ok = await self._start_azure_session(conn)
                if not ok:
                    return

        if self.is_processing and audio:
            try:
                pcm_frame = self.decoder.decode(audio, 960)
                if pcm_frame and self.audio_stream:
                    self.audio_stream.write(pcm_frame)
                    self.audio_frame_count += 1
                    logger.bind(tag=TAG).debug(
                        f"Azure写入音频帧: frame_count={self.audio_frame_count}, pcm长度={len(pcm_frame)}"
                    )
                else:
                    logger.bind(tag=TAG).warning("音频包解码后无有效PCM数据，已跳过")
            except Exception as e:
                logger.bind(tag=TAG).error(f"推送音频失败: {e}")

        if not self.is_processing:
            return

        if conn.client_listen_mode == "manual":
            return

        if not audio:
            logger.bind(tag=TAG).info("<<< Azure 检测到音频流结束，准备结算")
            await self._finalize_current_utterance(conn)
            return

        if audio_have_voice:
            self.silence_frames = 0
        else:
            self.silence_frames += 1
            if self.silence_frames >= self.end_silence_frames:
                logger.bind(tag=TAG).info(
                    f"<<< Azure 检测到静音结束，静音帧数={self.silence_frames}，准备结算"
                )
                await self._finalize_current_utterance(conn)

    async def _start_azure_session(self, conn: "ConnectionHandler") -> bool:
        if self.is_processing:
            return True

        try:
            self._current_conn = conn
            self._reset_text_state()
            self.done_event = asyncio.Event()
            self.loop = asyncio.get_running_loop()

            speech_config = speechsdk.SpeechConfig(
                subscription=self.speech_key,
                region=self.service_region,
            )
            speech_config.speech_recognition_language = self.language

            stream_format = speechsdk.audio.AudioStreamFormat(
                samples_per_second=16000,
                bits_per_sample=16,
                channels=1,
            )
            self.audio_stream = speechsdk.audio.PushAudioInputStream(stream_format)
            audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)

            self.recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config,
            )

            def _signal_done():
                if self.done_event is None:
                    return
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.done_event.set)
                else:
                    try:
                        self.done_event.set()
                    except Exception:
                        pass

            def on_recognizing(evt):
                if not self.partial_result_enabled:
                    return
                try:
                    if evt.result.reason == speechsdk.ResultReason.RecognizingSpeech:
                        partial = (evt.result.text or "").strip()
                        if partial:
                            self.partial_text = partial
                            logger.bind(tag=TAG).debug(f"Azure 中间结果: {partial}")
                except Exception as e:
                    logger.bind(tag=TAG).debug(f"处理Azure中间结果失败: {e}")

            def on_recognized(evt):
                try:
                    if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                        recognized_text = (evt.result.text or "").strip()
                        if recognized_text:
                            self._append_text(recognized_text)
                            logger.bind(tag=TAG).debug(f"Azure 最终片段识别: {recognized_text}")
                    elif evt.result.reason == speechsdk.ResultReason.NoMatch:
                        logger.bind(tag=TAG).info("Azure 未匹配到有效语音")
                except Exception as e:
                    logger.bind(tag=TAG).debug(f"处理Azure最终结果失败: {e}")

            def on_session_stopped(evt):
                logger.bind(tag=TAG).debug("Azure 会话已停止事件触发")
                _signal_done()

            def on_canceled(evt):
                try:
                    details = getattr(evt, "result", None)
                    if details and getattr(details, "cancellation_details", None):
                        error_details = details.cancellation_details.error_details
                        if error_details:
                            logger.bind(tag=TAG).error(f"Azure 取消: {error_details}")
                except Exception:
                    pass
                _signal_done()

            if self.partial_result_enabled:
                self.recognizer.recognizing.connect(on_recognizing)
            self.recognizer.recognized.connect(on_recognized)
            self.recognizer.session_stopped.connect(on_session_stopped)
            self.recognizer.canceled.connect(on_canceled)

            await asyncio.to_thread(self.recognizer.start_continuous_recognition)
            self.is_processing = True
            logger.bind(tag=TAG).info("Azure 流式识别会话已启动")
            return True

        except Exception as e:
            logger.bind(tag=TAG).error(f"Azure 启动失败: {e}", exc_info=True)
            await self._cleanup_session()
            return False

    async def _send_stop_request(self):
        """
        供 listen:stop 调用。
        当前项目调用时不传 conn，因此这里结束当前活跃连接的 Azure 会话。
        """
        if not self.is_processing:
            logger.bind(tag=TAG).debug("收到 stop 请求，但当前没有活跃的 Azure 会话")
            return

        conn = self._current_conn
        if conn is None:
            logger.bind(tag=TAG).warning("收到 stop 请求，但未记录当前连接，无法完成Azure会话结算")
            return

        logger.bind(tag=TAG).debug("收到 stop 请求，开始结束当前 Azure 会话")
        logger.bind(tag=TAG).info(
            f"Azure stop统计: frame_count={self.audio_frame_count}, accumulated_text={self.text!r}, partial_text={self.partial_text!r}"
        )
        await self._finalize_current_utterance(conn)

    async def _finalize_current_utterance(self, conn: "ConnectionHandler"):
        if not self.is_processing:
            return

        await self._stop_azure_session()

        if len(getattr(conn, "asr_audio_for_voiceprint", [])) >= self.min_audio_frames:
            await self.handle_voice_stop(conn, conn.asr_audio_for_voiceprint)

        conn.asr_audio_for_voiceprint = []
        conn.reset_audio_states()

    async def _stop_azure_session(self):
        if not self.is_processing:
            return

        try:
            if self.audio_stream:
                try:
                    self.audio_stream.close()
                except Exception as e:
                    logger.bind(tag=TAG).debug(f"关闭Azure音频流失败: {e}")

            if self.done_event:
                try:
                    await asyncio.wait_for(self.done_event.wait(), timeout=self.final_wait_timeout)
                except asyncio.TimeoutError:
                    logger.bind(tag=TAG).warning("等待 Azure 最终响应超时，继续执行关闭")

            if self.recognizer:
                try:
                    await asyncio.to_thread(self.recognizer.stop_continuous_recognition)
                except Exception as e:
                    logger.bind(tag=TAG).warning(f"停止 Azure 连续识别失败: {e}")

        except Exception as e:
            logger.bind(tag=TAG).error(f"关闭 Azure 会话时出错: {e}", exc_info=True)
        finally:
            final_text = self.text or self.partial_text or ""
            self.text = final_text
            logger.bind(tag=TAG).info(
                f"Azure 会话已关闭，最终文本: {final_text}，写入帧数: {self.audio_frame_count}"
            )
            await self._cleanup_session(keep_text=True)

    async def _cleanup_session(self, keep_text: bool = False):
        recognizer = self.recognizer
        if recognizer:
            try:
                del recognizer
            except Exception:
                pass

        self.audio_stream = None
        self.recognizer = None
        self.is_processing = False
        self.done_event = None
        self.loop = None
        self.silence_frames = 0
        self.partial_text = ""

        if not keep_text:
            self.text = ""

        self._current_conn = None

    async def speech_to_text(
        self,
        opus_data,
        session_id,
        audio_format="opus",
        artifacts=None,
    ):
        result = self.text
        self.text = ""
        return result, None

    def stop_ws_connection(self):
        return

    async def close(self):
        try:
            await self._stop_azure_session()
        except Exception as e:
            logger.bind(tag=TAG).debug(f"close 时停止 Azure 会话失败: {e}")
        finally:
            if hasattr(self, "decoder") and self.decoder is not None:
                try:
                    del self.decoder
                except Exception:
                    pass
                self.decoder = None
