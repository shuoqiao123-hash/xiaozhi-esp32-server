import asyncio
import base64
import os
import re

import azure.cognitiveservices.speech as speechsdk
from core.providers.tts.base import TTSProviderBase
from config.logger import setup_logging

TAG = __name__
logger = setup_logging()

def _extract_locale(voice_name: str, fallback: str = "en-ES") -> str:
    # 从 voice_name 提取 locale（形如 en-US / zh-CN / es-ES）
    m = re.match(r"([a-zA-Z]{2,4}-[a-zA-Z]{2,4})", voice_name or "")
    return m.group(1) if m else fallback

def _build_ssml(text: str, voice_name: str) -> str:
    locale = _extract_locale(voice_name)
    # 可按需调整 prosody/style，默认不强制风格
    return f"""
        <speak version='1.0'
            xmlns='http://www.w3.org/2001/10/synthesis'
            xmlns:mstts='http://www.w3.org/2001/mstts'
            xml:lang='{locale}'>
        <voice name='{voice_name}'>
            <prosody rate='20%'>
            <mstts:express-as style='cheerful'>
                {text}
            </mstts:express-as>
            </prosody>
        </voice>
        </speak>
        """.strip()

class TTSProvider(TTSProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__(config, delete_audio_file)
        self.speech_key = config.get("speech_key")
        self.service_region = config.get("service_region")
        self.voice_name = config.get("private_voice")
        # logger.bind(tag=TAG).info(f"voice_name: {self.voice_name}")
    async def text_to_speak(self, text, output_file):
        """
            将文本转换为语音，并将音频数据保存到文件或以字节流形式返回。
        """
        try:
            speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
            speech_config.speech_synthesis_voice_name = self.voice_name


            # 可选：设置音频输出格式（例如：mp3, wav 等）。默认通常是 WAV。
            # 设置为 MP3 可以获得更小的文件大小，如果您需要 WAV，可以设置为 OGG_OPUS 或其他格式
            # speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Audio16Khz32KBitRateMonoMp3)

            # 2. 构造语音合成器
            speech_config.set_speech_synthesis_output_format(speechsdk.SpeechSynthesisOutputFormat.Riff16Khz16BitMonoPcm)
            speech_synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
            ssml = _build_ssml(text, self.voice_name)
            # print(self.voice_name)
            # result = speech_synthesizer.speak_ssml_async(ssml).get()
            # result = await asyncio.to_thread(speech_synthesizer.speak_ssml_async, ssml)
            def sync_speak():
                # 在新的线程中同步调用并等待结果
                future = speech_synthesizer.speak_ssml_async(ssml)
                # .get() 会阻塞当前线程，直到 TTS 完成，并返回 SpeechSynthesisResult
                return future.get() 
                
            # 在异步上下文中运行这个阻塞的同步函数
            result = await asyncio.to_thread(sync_speak)
            # 4. 检查结果并处理音频数据
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                audio_bytes = speechsdk.AudioDataStream(result)

                if output_file:
                    # 写入原始音频字节到文件
                    audio_bytes.save_to_wav_file(file_name=output_file)
                else:
                    return result.audio_data

            elif result.reason == speechsdk.ResultReason.Canceled:
                cancellation_details = result.cancellation_details
                error_message = f"Speech synthesis canceled: {cancellation_details.reason}. "
                if cancellation_details.reason == speechsdk.CancellationReason.Error:
                    error_message += f"Error details: {cancellation_details.error_details}"

                # print(error_message)
                raise Exception(f"TTS Error: {error_message}")

        except Exception as e:
            # print(f"An error occurred: {e}")
            raise Exception(f"text_to_speak error: {e}")

