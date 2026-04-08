import asyncio
import azure.cognitiveservices.speech as speechsdk
import opuslib_next
import json
import uuid
from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from core.providers.asr.dto.dto import InterfaceType

TAG = "AzureASR"
logger = setup_logging()

class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        # 1. 声明为流式接口
        self.interface_type = InterfaceType.STREAM
        self.config = config
        self.delete_audio_file = delete_audio_file
        
        # Azure 配置
        self.speech_key = config.get("speech_key")
        self.service_region = config.get("service_region")
        self.language = config.get("language", "zh-CN")
        
        # 状态管理
        self.decoder = opuslib_next.Decoder(16000, 1)
        self.audio_stream = None
        self.recognizer = None
        self.is_processing = False
        self.text = ""
        self.done_event = asyncio.Event()

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    async def receive_audio(self, conn, audio, audio_have_voice):
        """
        框架每收到一帧音频都会调用此方法
        """
        # 维护音频缓冲区（用于声纹或备份）
        if not hasattr(conn, 'asr_audio_for_voiceprint'):
            conn.asr_audio_for_voiceprint = []
        
        if audio:
            conn.asr_audio_for_voiceprint.append(audio)

        # 逻辑 A：检测到语音起点，初始化 Azure 会话
        if audio_have_voice and not self.is_processing:
            logger.bind(tag=TAG).info(">>> Azure 检测到起点，开始流式识别")
            await self._start_azure_session()

        # 逻辑 B：推送音频数据到 Azure
        if self.is_processing and audio:
            try:
                # 解码并写入 Azure 的 PushStream
                pcm_frame = self.decoder.decode(audio, 960)
                if pcm_frame and self.audio_stream:
                    self.audio_stream.write(pcm_frame)
            except Exception as e:
                logger.bind(tag=TAG).error(f"推送音频失败: {e}")

        # 逻辑 C：重点修复 - 模仿豆包处理“流结束”或“VAD 判定静音”
        # 如果 audio 为空（流结束信号）或者语音活动停止且正在处理中
        if self.is_processing and (not audio or not audio_have_voice):
            logger.bind(tag=TAG).info("<<< Azure 检测到终点或音频流中断，准备结算")
            
            # 停止 Azure 识别并等待最终结果
            await self._stop_azure_session()
            
            # 调用框架的统一处理逻辑
            if len(conn.asr_audio_for_voiceprint) > 15:
                await self.handle_voice_stop(conn, conn.asr_audio_for_voiceprint)
            
            # 重置缓冲区
            conn.asr_audio_for_voiceprint = []
            conn.reset_vad_states()

    async def _start_azure_session(self):
        """初始化 Azure SDK 相关组件"""
        try:
            self.text = ""
            self.done_event.clear()
            
            speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
            speech_config.speech_recognition_language = self.language
            
            # 设置音频流格式 (16k, 16bit, mono PCM)
            stream_format = speechsdk.audio.AudioStreamFormat(16000, 16, 1)
            self.audio_stream = speechsdk.audio.PushAudioInputStream(stream_format)
            audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)
            
            self.recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

            # 注册回调
            def on_recognized(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
                    # 使用线程安全的方式（或简单的 +=）更新文本
                    self.text += evt.result.text
                    logger.bind(tag=TAG).debug(f"Azure 片段识别: {evt.result.text}")

            def on_session_stopped(evt):
                logger.bind(tag=TAG).debug("Azure 会话已停止事件触发")
                self.done_event.set()

            def on_canceled(evt):
                if evt.result.cancellation_details.error_details:
                    logger.bind(tag=TAG).error(f"Azure 取消: {evt.result.cancellation_details.error_details}")
                self.done_event.set()

            self.recognizer.recognized.connect(on_recognized)
            self.recognizer.session_stopped.connect(on_session_stopped)
            self.recognizer.canceled.connect(on_canceled)

            # 启动异步识别
            await asyncio.to_thread(self.recognizer.start_continuous_recognition)
            self.is_processing = True
            
        except Exception as e:
            logger.bind(tag=TAG).error(f"Azure 启动失败: {e}", exc_info=True)
            self.is_processing = False

    async def _stop_azure_session(self):
        """优雅关闭 Azure 会话并获取最终文本"""
        if not self.is_processing:
            return

        try:
            if self.audio_stream:
                # 关键：先关闭流，Azure 才会知道后面没数据了，从而触发最后的 Recognized 事件
                self.audio_stream.close() 

            # 等待最后一个 Recognized 事件（稍微多等一会，Azure 服务器处理末尾有延迟）
            try:
                await asyncio.wait_for(self.done_event.wait(), timeout=0.8)
            except asyncio.TimeoutError:
                logger.bind(tag=TAG).warning("等待 Azure 最终响应超时，强制关闭")

            if self.recognizer:
                await asyncio.to_thread(self.recognizer.stop_continuous_recognition)
        
        except Exception as e:
            logger.bind(tag=TAG).error(f"关闭 Azure 会话时出错: {e}")
        finally:
            self.is_processing = False
            logger.bind(tag=TAG).info(f"Azure 会话已关闭，最终文本: {self.text}")

    async def speech_to_text(self, opus_data, session_id, audio_format):
        """
        框架在 handle_voice_stop 内部或 listen:stop 后会调用此方法
        """
        # 兜底：如果外部已经调用了 stop，但 Azure 还没结算，在这里强制结算
        if self.is_processing:
            logger.bind(tag=TAG).info("speech_to_text 被触发，执行兜底关闭逻辑")
            await self._stop_azure_session()

        result = self.text
        # 结算后清空，防止下一次识别出现重复文本
        self.text = "" 
        return result, None

    def stop_ws_connection(self):
        """清理资源，当 WebSocket 连接断开时调用"""
        if self.is_processing:
            self.is_processing = False
            # 异步执行，不阻塞主线程
            asyncio.create_task(self._stop_azure_session())

    async def close(self):
        """完全销毁时的清理工作"""
        await self._stop_azure_session()
        if self.recognizer:
            del self.recognizer