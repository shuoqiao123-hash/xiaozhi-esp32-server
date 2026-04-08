import os
import json
import gzip
import struct
import asyncio
import websockets
import opuslib_next
from core.providers.asr.base import ASRProviderBase
from config.logger import setup_logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler

TAG = __name__
logger = setup_logging()


class ASRProvider(ASRProviderBase):
    def __init__(self, config, delete_audio_file):
        super().__init__()
        self.interface_type = "stream"
        self.config = config
        self.delete_audio_file = delete_audio_file
        
        # 【关键修复】兼容新框架基类的强制依赖
        self.output_dir = config.get("output_dir", "tmp/")
        os.makedirs(self.output_dir, exist_ok=True)  # 确保目录存在，防止磁盘检查报错
        
        # 核心状态管理
        self.text = ""
        self.is_processing = False
        self.asr_ws = None
        self.forward_task = None
        
        # 豆包专属协议状态
        self.decoder = opuslib_next.Decoder(16000, 1)
        self.audio_buffer = bytearray()
        self.seq = 1

        # 豆包配置 (解决 403 的关键)
        self.appid = str(config.get("appid"))
        self.access_key = config.get("access_token")
        self.ws_url = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"

    def requires_file(self) -> bool:
        """告诉基类：我们是流式，不需要保存文件，跳过无用的文件IO"""
        return False

    async def open_audio_channels(self, conn):
        await super().open_audio_channels(conn)

    async def receive_audio(self, conn: "ConnectionHandler", audio, audio_have_voice):
        # 1. 新框架要求：填充缓存
        await super().receive_audio(conn, audio, audio_have_voice)
        if not audio:
            return

        # 2. 建连
        if audio_have_voice and self.asr_ws is None and not self.is_processing:
            try:
                self.is_processing = True
                self.text = ""
                self.seq = 1
                self.audio_buffer.clear()

                headers = {
                    "X-Api-Resource-Id": "volc.seedasr.sauc.duration",
                    "X-Api-App-Key": self.appid,
                    "X-Api-Access-Key": self.access_key,
                    "X-Api-Request-Id": str(__import__('uuid').uuid4())
                }

                self.asr_ws = await websockets.connect(
                    self.ws_url, additional_headers=headers,
                    max_size=1000000000, ping_interval=None, ping_timeout=None,
                )

                payload = {
                    "user": {"uid": "user_" + str(int(asyncio.get_event_loop().time()))},
                    "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
                    "request": {"model_name": "bigmodel", "enable_itn": True, "show_utterances": True}
                }
                await self._send_packet(0x01, payload)
                
                # 启动后台接收任务
                self.forward_task = asyncio.create_task(self._forward_asr_results(conn))

                # 倒灌缓存
                if conn.asr_audio and len(conn.asr_audio) > 0:
                    for cached_audio in conn.asr_audio[-10:]:
                        try:
                            self.audio_buffer.extend(self.decoder.decode(cached_audio, 960))
                        except: pass
                            
            except Exception as e:
                logger.bind(tag=TAG).error(f"建立豆包 ASR 连接失败: {str(e)}")
                await self._cleanup_connection()

        # 3. 实时推送 (保留旧代码优秀的 200ms 攒包机制)
        if self.asr_ws and self.is_processing:
            try:
                pcm_frame = self.decoder.decode(audio, 960) if conn.audio_format != "pcm" else audio
                self.audio_buffer.extend(pcm_frame)
                if len(self.audio_buffer) >= 6400:
                    await self._send_payload(bytes(self.audio_buffer), is_last=False)
                    self.audio_buffer.clear()
            except Exception as e:
                logger.bind(tag=TAG).error(f"发送实时音频失败: {e}")

    async def _forward_asr_results(self, conn: "ConnectionHandler"):
        """纯粹的后台打工人：只负责解析实时结果更新 self.text。"""
        try:
            while self.asr_ws and not conn.stop_event.is_set():
                try:
                    response = await self.asr_ws.recv()
                    data = response
                    header_size = data[0] & 0x0f
                    msg_type = (data[1] >> 4) & 0x0f
                    content_compress = data[2] & 0x0f
                    payload = data[header_size * 4:]
                    if data[1] & 0x0f & 0x01: 
                        payload = payload[4:]
                    
                    if msg_type in [0x08, 0x09]:
                        content = payload[4:]
                        if content_compress == 1: 
                            content = gzip.decompress(content)
                        res_json = json.loads(content.decode('utf-8'))
                        if "result" in res_json:
                            utterances = res_json["result"].get("utterances", [])
                            definite_found = False
                            for utt in utterances:
                                if utt.get("definite", False):
                                    self.text = utt["text"]
                                    definite_found = True
                                    break
                            if not definite_found:
                                self.text = res_json["result"].get("text", self.text)
                    elif msg_type == 0x0F:
                        logger.bind(tag=TAG).error(f"火山服务端返回错误: {data.hex()}")
                        break
                except websockets.ConnectionClosed:
                    break
        except asyncio.CancelledError:
            pass 
        except Exception as e:
            logger.bind(tag=TAG).debug(f"结果监听异常退出: {e}")

    async def speech_to_text(self, opus_data, session_id, audio_format, artifacts=None):
        """
        核心结算点！由新框架的 VAD 判定用户说完话后自动调用。
        """
        if not self.is_processing:
            return "", None

        try:
            # 1. 发送最后一包 (带负数序列号)
            if self.asr_ws:
                await self._send_payload(bytes(self.audio_buffer), is_last=True)
                self.audio_buffer.clear()
                
                # 2. 稍微等一下，让服务端把最后累积的结果吐出来
                await asyncio.sleep(0.2)
            
            final_text = self.text.strip()
            if len(final_text) < 2:
                logger.bind(tag=TAG).info(f"过滤无效超短文本: {final_text}")
                final_text = ""

            return final_text, None
        except Exception as e:
            logger.bind(tag=TAG).error(f"ASR 结算过程异常: {e}")
            return self.text, None
        finally:
            # 3. 结算完毕，彻底销毁连接，等待下一轮开口
            await self._cleanup_connection()

    def stop_ws_connection(self):
        if self.asr_ws:
            asyncio.create_task(self.asr_ws.close())
            self.asr_ws = None
        self.is_processing = False

    async def close(self):
        await self._cleanup_connection()
        if self.forward_task:
            self.forward_task.cancel()
            try:
                await self.forward_task
            except asyncio.CancelledError:
                pass
            self.forward_task = None
        if hasattr(self, "decoder") and self.decoder is not None:
            try:
                del self.decoder
                self.decoder = None
            except Exception:
                pass

    async def _cleanup_connection(self):
        self.is_processing = False
        if self.asr_ws:
            try:
                await self.asr_ws.close()
            except Exception:
                pass
            self.asr_ws = None
        self.audio_buffer.clear()

    # ================= 火山引擎底层协议构建 (100% 沿用旧代码) =================

    async def _send_payload(self, pcm, is_last=False):
        if not self.asr_ws: return
        try:
            compressed = gzip.compress(pcm)
            msg_type = 0x02
            flags = 0x03 if is_last else 0x01
            header = bytearray([0x11, (msg_type << 4) | flags, 0x01, 0x00])
            seq = -self.seq if is_last else self.seq
            header.extend(struct.pack('>i', seq))
            header.extend(struct.pack('>I', len(compressed)))
            await self.asr_ws.send(header + compressed)
            self.seq += 1
        except Exception as e:
            logger.bind(tag=TAG).debug(f"发送音频帧失败: {e}")

    async def _send_packet(self, msg_type, payload):
        try:
            p_bytes = gzip.compress(json.dumps(payload).encode())
            header = bytearray([0x11, (msg_type << 4) | 0x01, 0x01, 0x00])
            header.extend(struct.pack('>i', self.seq))
            header.extend(struct.pack('>I', len(p_bytes)))
            await self.asr_ws.send(header + p_bytes)
            self.seq += 1
        except Exception as e:
            logger.bind(tag=TAG).debug(f"发送控制帧失败: {e}")
