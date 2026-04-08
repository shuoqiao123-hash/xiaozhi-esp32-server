import os
import time
import queue
import json
import base64
import aiohttp
import asyncio
import requests
import traceback
from config.logger import setup_logging
from core.utils.tts import MarkdownCleaner
from core.providers.tts.base import TTSProviderBase
from core.utils import opus_encoder_utils, textUtils
from core.providers.tts.dto.dto import SentenceType, ContentType, InterfaceType

TAG = __name__
logger = setup_logging()

class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        # 1. 初始化接口类型和基础配置
        self.interface_type = InterfaceType.SINGLE_STREAM
        
        # 豆包 V3 默认配置
        self.api_url = config.get("api_url", "https://openspeech.bytedance.com/api/v3/tts/unidirectional")
        self.appid = config.get("appid")
        self.access_token = config.get("access_token")
        self.resource_Id = config.get("resource_Id")  # 对应火山的 Resource ID
        
        # 配音角色处理
        if config.get("private_voice"):
            self.voice = config.get("private_voice")
        else:
            self.voice = config.get("voice", "zh_female_cancan_mars_bigtts")

        self.audio_format = "pcm"  # 强制使用 pcm 以便后续 Opus 编码
        self.before_stop_play_files = []

        # 2. 创建 Opus 编码器 (豆包 V3 建议采样率 24000)
        self.sample_rate = int(config.get("sample_rate", 24000))
        self.opus_encoder = opus_encoder_utils.OpusEncoderUtils(
            sample_rate=self.sample_rate, channels=1, frame_size_ms=60
        )

        # 3. PCM 缓冲区
        self.pcm_buffer = bytearray()

    def tts_text_priority_thread(self):
        """流式文本处理线程"""
        while not self.conn.stop_event.is_set():
            try:
                message = self.tts_text_queue.get(timeout=1)
                if message.sentence_type == SentenceType.FIRST:
                    self.tts_stop_request = False
                    self.processed_chars = 0
                    self.tts_text_buff = []
                    self.before_stop_play_files.clear()
                elif ContentType.TEXT == message.content_type:
                    self.tts_text_buff.append(message.content_detail)
                    segment_text = self._get_segment_text()
                    if segment_text:
                        self.to_tts_single_stream(segment_text)

                elif ContentType.FILE == message.content_type:
                    logger.bind(tag=TAG).info(f"添加音频文件到待播放列表: {message.content_file}")
                    if message.content_file and os.path.exists(message.content_file):
                        self._process_audio_file_stream(message.content_file, callback=lambda audio_data: self.handle_audio_file(audio_data, message.content_detail))

                if message.sentence_type == SentenceType.LAST:
                    self._process_remaining_text_stream(True)

            except queue.Empty:
                continue
            except Exception as e:
                logger.bind(tag=TAG).error(f"处理TTS文本失败: {str(e)}, 堆栈: {traceback.format_exc()}")

    def _process_remaining_text_stream(self, is_last=False):
        """处理剩余文本并生成语音"""
        full_text = "".join(self.tts_text_buff)
        remaining_text = full_text[self.processed_chars :]
        if remaining_text:
            segment_text = textUtils.get_string_no_punctuation_or_emoji(remaining_text)
            if segment_text:
                self.to_tts_single_stream(segment_text, is_last)
                self.processed_chars += len(full_text)
            else:
                self._process_before_stop_play_files()
        else:
            self._process_before_stop_play_files()

    def to_tts_single_stream(self, text, is_last=False):
        """执行 TTS 合成"""
        try:
            text = MarkdownCleaner.clean_markdown(text)
            asyncio.run(self.text_to_speak(text, is_last))
        except Exception as e:
            logger.bind(tag=TAG).error(f"Failed to generate TTS stream: {e}")
        return None

    async def text_to_speak(self, text, is_last):
        """接入豆包 V3 HTTP 单向流式接口"""
        headers = {
            "X-Api-App-Id": str(self.appid),
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_Id,
            "Content-Type": "application/json",
        }

        payload = {
            "user": {"uid": "user_azero_ai"},
            "req_params": {
                "text": text,
                "speaker": self.voice,
                "audio_params": {
                    "format": "pcm",
                    "sample_rate": self.sample_rate,
                    "enable_timestamp": True
                },
                "additions": json.dumps({
                    "explicit_language": "zh",
                    "disable_markdown_filter": True
                })
            }
        }

        frame_bytes = int(self.sample_rate * 1 * 60 / 1000 * 2)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.api_url, json=payload, headers=headers, timeout=15) as resp:
                    if resp.status != 200:
                        err_msg = await resp.text()
                        logger.bind(tag=TAG).error(f"豆包 TTS 请求失败: {resp.status}, {err_msg}")
                        return

                    self.pcm_buffer.clear()
                    self.tts_audio_queue.put((SentenceType.FIRST, [], text))

                    async for line in resp.content:
                        if not line:
                            continue
                        try:
                            chunk_json = json.loads(line.decode("utf-8"))
                            code = chunk_json.get("code")
                            audio_payload = chunk_json.get("data")
                            if code == 0 and audio_payload is not None:
                                raw_pcm = base64.b64decode(chunk_json["data"])
                                self.pcm_buffer.extend(raw_pcm)

                                while len(self.pcm_buffer) >= frame_bytes:
                                    frame = bytes(self.pcm_buffer[:frame_bytes])
                                    del self.pcm_buffer[:frame_bytes]

                                    self.opus_encoder.encode_pcm_to_opus_stream(
                                        frame,
                                        end_of_stream=False,
                                        callback=self.handle_opus
                                    )
                            elif code == 20000000:
                                break
                        except Exception as parse_e:
                            logger.bind(tag=TAG).error(f"解析豆包返回帧异常: {parse_e}")

                    if len(self.pcm_buffer) > 0:
                        self.opus_encoder.encode_pcm_to_opus_stream(
                            bytes(self.pcm_buffer),
                            end_of_stream=True,
                            callback=self.handle_opus
                        )
                        self.pcm_buffer.clear()

                    if is_last:
                        self._process_before_stop_play_files()

        except Exception as e:
            logger.bind(tag=TAG).error(f"text_to_speak 异常: {e}")
            self.tts_audio_queue.put((SentenceType.LAST, [], None))

    def to_tts(self, text: str) -> list:
        """同步非流式接口"""
        start_time = time.time()
        text = MarkdownCleaner.clean_markdown(text)
        headers = {
            "X-Api-App-Id": str(self.appid),
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_Id,
            "Content-Type": "application/json",
        }
        payload = {
            "user": {"uid": "user_sync"},
            "req_params": {
                "text": text,
                "speaker": self.voice,
                "audio_params": {"format": "pcm", "sample_rate": self.sample_rate}
            }
        }

        try:
            response = requests.post(self.api_url, json=payload, headers=headers, timeout=10)
            if response.status_code != 200:
                return []

            opus_datas = []
            frame_bytes = int(self.sample_rate * 1 * 60 / 1000 * 2)
            temp_pcm = bytearray()

            for line in response.iter_lines():
                if not line: continue
                data = json.loads(line)
                if data.get("code") == 0 and "data" in data:
                    temp_pcm.extend(base64.b64decode(data["data"]))
                    while len(temp_pcm) >= frame_bytes:
                        frame = bytes(temp_pcm[:frame_bytes])
                        del temp_pcm[:frame_bytes]
                        self.opus_encoder.encode_pcm_to_opus_stream(
                            frame,
                            end_of_stream=False,
                            callback=lambda opus: opus_datas.append(opus)
                        )
            
            if temp_pcm:
                self.opus_encoder.encode_pcm_to_opus_stream(
                    bytes(temp_pcm),
                    end_of_stream=True,
                    callback=lambda opus: opus_datas.append(opus)
                )

            return opus_datas
        except Exception as e:
            logger.bind(tag=TAG).error(f"to_tts 同步请求异常: {e}")
            return []

    async def close(self):
        """资源清理，修复末尾关键字错误"""
        await super().close()
        if hasattr(self, "opus_encoder"):
            self.opus_encoder.close()