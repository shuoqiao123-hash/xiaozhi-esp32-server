import os
import re
import html
import queue
import asyncio
import traceback
import threading
import time

import azure.cognitiveservices.speech as speechsdk

from config.logger import setup_logging
from core.providers.tts.base import TTSProviderBase
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType
from core.utils.tts import MarkdownCleaner, convert_percentage_to_range
from core.utils.util import check_model_key

TAG = __name__
logger = setup_logging()


def _extract_locale(voice_name: str, fallback: str = "zh-CN") -> str:
    m = re.match(r"([a-zA-Z]{2,4}-[a-zA-Z]{2,4})", voice_name or "")
    return m.group(1) if m else fallback


def _escape_ssml_text(text: str) -> str:
    return html.escape(text or "", quote=False)


def _normalize_rate(rate_value: int | float | str | None) -> str:
    if rate_value in (None, "", 0, "0"):
        return "-10%"
    try:
        value = int(float(rate_value))
    except (TypeError, ValueError):
        return "-10%"
    if value > 0:
        return f"+{value}%"
    return f"{value}%"


def _normalize_pitch(pitch_value: int | float | str | None) -> str:
    if pitch_value in (None, "", 0, "0"):
        return "0Hz"
    try:
        value = int(float(pitch_value))
    except (TypeError, ValueError):
        return "0Hz"
    if value > 0:
        return f"+{value}Hz"
    return f"{value}Hz"


def _build_ssml(text: str, voice_name: str, rate: str = "0%", pitch: str = "0Hz", style: str | None = None) -> str:
    locale = _extract_locale(voice_name, fallback="zh-CN")
    escaped_text = _escape_ssml_text(text)

    if style:
        content = (
            f"<mstts:express-as style='{style}'>"
            f"<prosody rate='{rate}' pitch='{pitch}'>{escaped_text}</prosody>"
            f"</mstts:express-as>"
        )
    else:
        content = f"<prosody rate='{rate}' pitch='{pitch}'>{escaped_text}</prosody>"

    return (
        f"<speak version='1.0' "
        f"xmlns='http://www.w3.org/2001/10/synthesis' "
        f"xmlns:mstts='http://www.w3.org/2001/mstts' "
        f"xml:lang='{locale}'>"
        f"<voice name='{voice_name}'>"
        f"{content}"
        f"</voice>"
        f"</speak>"
    )


class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)

        self.interface_type = InterfaceType.DUAL_STREAM
        self.audio_file_type = "wav"

        self.speech_key = config.get("speech_key") or config.get("api_key")
        self.service_region = config.get("service_region") or config.get("region")
        self.voice_name = (
            config.get("voice_name")
            or config.get("speaker")
            or config.get("private_voice")
            or "zh-CN-XiaoxiaoNeural"
        )

        self.language = config.get("language") or _extract_locale(self.voice_name, "zh-CN")
        self.tts_timeout = int(config.get("tts_timeout", 20) or 20)
        self.style = config.get("style") or config.get("emotion") or None

        self.enable_ws_reuse = False
        self.activate_session = False
        self.report_on_last = False

        self.audio_params = {
            "speech_rate": -10,
            "pitch": 0,
        }

        if "audio_params" in config and isinstance(config.get("audio_params"), dict):
            self.audio_params["speech_rate"] = config["audio_params"].get("speech_rate", 0)
            self.audio_params["pitch"] = config["audio_params"].get("pitch", 0)

        if "ttsRate" in config:
            self.audio_params["speech_rate"] = int(
                convert_percentage_to_range(
                    config["ttsRate"], min_val=-50, max_val=100, base_val=0
                )
            )

        if "ttsPitch" in config:
            self.audio_params["pitch"] = int(
                convert_percentage_to_range(
                    config["ttsPitch"], min_val=-12, max_val=12, base_val=0
                )
            )

        model_key_msg = check_model_key("TTS", self.speech_key)
        if model_key_msg:
            logger.bind(tag=TAG).error(model_key_msg)

        if not self.speech_key:
            raise ValueError("Azure TTS 配置缺少 speech_key/api_key")
        if not self.service_region:
            raise ValueError("Azure TTS 配置缺少 service_region/region")
        if not self.voice_name:
            raise ValueError("Azure TTS 配置缺少 voice_name/speaker/private_voice")

        logger.bind(tag=TAG).info(
            f"Azure TTSProvider 初始化: voice={self.voice_name}, region={self.service_region}, lang={self.language}, timeout={self.tts_timeout}"
        )

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)
        logger.bind(tag=TAG).info(
            f"Azure TTS 音频通道已打开: sample_rate={getattr(conn, 'sample_rate', 'unknown')}"
        )

    def _build_speech_config(self):
        speech_config = speechsdk.SpeechConfig(
            subscription=self.speech_key,
            region=self.service_region,
        )
        speech_config.speech_synthesis_voice_name = self.voice_name
        speech_config.set_speech_synthesis_output_format(
            speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm
        )
        return speech_config

    def _synthesize_segment_sync(self, text: str) -> bytes:
        speech_config = self._build_speech_config()
        synthesizer = speechsdk.SpeechSynthesizer(
            speech_config=speech_config,
            audio_config=None,
        )

        rate = _normalize_rate(self.audio_params.get("speech_rate"))
        pitch = _normalize_pitch(self.audio_params.get("pitch"))
        ssml = _build_ssml(
            text=text,
            voice_name=self.voice_name,
            rate=rate,
            pitch=pitch,
            style=self.style,
        )

        result = synthesizer.speak_ssml_async(ssml).get()

        if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
            return result.audio_data

        if result.reason == speechsdk.ResultReason.Canceled:
            cancellation_details = result.cancellation_details
            error_message = f"Speech synthesis canceled: {cancellation_details.reason}"
            if cancellation_details.reason == speechsdk.CancellationReason.Error:
                error_message += f", error_details={cancellation_details.error_details}"
            raise RuntimeError(error_message)

        raise RuntimeError(f"Azure TTS 合成失败，未知原因: reason={result.reason}")

    async def text_to_speak(self, text, output_file):
        filtered_text = MarkdownCleaner.clean_markdown(text or "").strip()
        if not filtered_text:
            logger.bind(tag=TAG).debug("Azure TTS 收到空文本，跳过合成")
            return b"" if output_file is None else None

        logger.bind(tag=TAG).debug(f"Azure TTS 开始合成文本: {filtered_text}")

        try:
            audio_bytes = await asyncio.wait_for(
                asyncio.to_thread(self._synthesize_segment_sync, filtered_text),
                timeout=self.tts_timeout,
            )

            if output_file:
                with open(output_file, "wb") as f:
                    f.write(audio_bytes)
                logger.bind(tag=TAG).info(f"Azure TTS 文件生成成功: {output_file}")
                return None

            return audio_bytes

        except asyncio.TimeoutError:
            logger.bind(tag=TAG).error(f"Azure TTS 合成超时: {filtered_text}")
            raise RuntimeError("Azure TTS synthesis timeout")
        except Exception as e:
            logger.bind(tag=TAG).error(f"Azure TTS 合成失败: {e}")
            raise RuntimeError(f"Azure TTS synthesis failed: {e}")

    def tts_text_priority_thread(self):
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
                logger.bind(tag=TAG).debug(
                    f"Azure TTS 收到任务｜{message.sentence_type.name}｜{message.content_type.name}｜会话ID: {self.conn.sentence_id}"
                )

                if message.sentence_type == SentenceType.FIRST:
                    self.conn.client_abort = False

                if self.conn.client_abort:
                    logger.bind(tag=TAG).info("收到打断信息，终止 Azure TTS 文本处理")
                    self.activate_session = False
                    self.tts_text_buff = []
                    self.processed_chars = 0
                    self.is_first_sentence = True
                    continue

                if message.sentence_type == SentenceType.FIRST:
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.is_first_sentence = True
                    self.tts_audio_first_sentence = True

                    try:
                        if not getattr(self.conn, "sentence_id", None):
                            self.conn.sentence_id = message.sentence_id
                        future = asyncio.run_coroutine_threadsafe(
                            self.start_session(self.conn.sentence_id),
                            loop=self.conn.loop,
                        )
                        future.result(timeout=self.tts_timeout)
                    except Exception as e:
                        logger.bind(tag=TAG).error(f"Azure TTS 启动会话失败: {e}")
                        continue

                elif ContentType.TEXT == message.content_type:
                    self.tts_text_buff.append(message.content_detail or "")
                    segment_text = self._get_segment_text()
                    while segment_text:
                        try:
                            logger.bind(tag=TAG).debug(f"Azure TTS 开始处理分段文本: {segment_text}")
                            future = asyncio.run_coroutine_threadsafe(
                                self._stream_segment(segment_text),
                                loop=self.conn.loop,
                            )
                            future.result(timeout=self.tts_timeout + 10)
                        except Exception as e:
                            logger.bind(tag=TAG).error(f"Azure TTS 分段合成失败: {e}")
                            break

                        if self.conn.client_abort:
                            logger.bind(tag=TAG).info("Azure TTS 分段处理中收到打断，终止后续分段")
                            break

                        segment_text = self._get_segment_text()

                elif ContentType.FILE == message.content_type:
                    self._process_remaining_text_stream(opus_handler=self.handle_opus)
                    if message.content_file and os.path.exists(message.content_file):
                        logger.bind(tag=TAG).info(
                            f"Azure TTS 添加音频文件到待播放列表: {message.content_file}"
                        )
                        self._process_audio_file_stream(
                            message.content_file,
                            callback=lambda audio_data: self.handle_audio_file(
                                audio_data, message.content_detail
                            ),
                        )

                if message.sentence_type == SentenceType.LAST:
                    try:
                        self.tts_stop_request = True
                        self._process_remaining_text_stream(opus_handler=self.handle_opus)

                        future = asyncio.run_coroutine_threadsafe(
                            self.finish_session(self.conn.sentence_id),
                            loop=self.conn.loop,
                        )
                        future.result(timeout=self.tts_timeout)

                    except Exception as e:
                        logger.bind(tag=TAG).error(f"Azure TTS 结束会话失败: {e}")
                        continue

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(
                    f"Azure TTS 处理文本失败: {str(e)}, 类型: {type(e).__name__}, 堆栈: {traceback.format_exc()}"
                )
                continue

    async def _stream_segment(self, text: str):
        if not text or not text.strip():
            return

        if self.conn.client_abort:
            logger.bind(tag=TAG).info("Azure TTS 当前已打断，跳过分段合成")
            return

        self.tts_audio_queue.put((SentenceType.FIRST, [], text))
        logger.bind(tag=TAG).info(f"Azure TTS 句子语音生成开始: {text}")

        audio_bytes = await self.text_to_speak(text, None)
        if not audio_bytes:
            logger.bind(tag=TAG).warning(f"Azure TTS 分段未生成音频: {text}")
            return

        audio_chunks = []

        def collect_and_forward(opus_data: bytes):
            audio_chunks.append(opus_data)
            self.handle_opus(opus_data)

        self.wav_to_opus_data_audio_raw_stream(audio_bytes, callback=collect_and_forward)
        logger.bind(tag=TAG).info(f"Azure TTS 句子语音生成成功: {text}")

    async def start_session(self, session_id):
        logger.bind(tag=TAG).debug(f"Azure TTS 开始会话: {session_id}")
        self.activate_session = True

    async def finish_session(self, session_id):
        logger.bind(tag=TAG).debug(f"Azure TTS 结束会话: {session_id}")
        self.activate_session = False
        self.tts_audio_queue.put((SentenceType.LAST, [], None))

    async def close(self):
        logger.bind(tag=TAG).info("Azure TTS 开始关闭资源")
        self.activate_session = False

    def wav_to_opus_data_audio_raw_stream(
        self, raw_data_var, is_end=False, callback=None
    ):
        return self.opus_encoder.encode_pcm_to_opus_stream(
            raw_data_var, is_end, callback=callback
        )

    def _process_remaining_text_stream(self, opus_handler=None):
        self.tts_stop_request = True
        segment_text = self._get_segment_text()
        while segment_text:
            try:
                logger.bind(tag=TAG).debug(f"Azure TTS 处理剩余文本: {segment_text}")
                audio_bytes = asyncio.run(self.text_to_speak(segment_text, None))
                if audio_bytes:
                    self.tts_audio_queue.put((SentenceType.FIRST, [], segment_text))
                    self.wav_to_opus_data_audio_raw_stream(audio_bytes, callback=opus_handler)
                    logger.bind(tag=TAG).info(f"Azure TTS 剩余句子语音生成成功: {segment_text}")
            except Exception as e:
                logger.bind(tag=TAG).error(f"Azure TTS 处理剩余文本失败: {e}")
                break

            if self.conn.client_abort:
                logger.bind(tag=TAG).info("Azure TTS 处理剩余文本时收到打断")
                break

            segment_text = self._get_segment_text()

        self.tts_stop_request = False