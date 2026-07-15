# 小智 ESP32 Server 开发问题总结与改进计划

## 📋 问题总结

### 问题一：播放本地音乐工具缺乏容灾机制

**位置**: `plugins_func/functions/play_music.py`

**现状分析**:
- 使用 `difflib.SequenceMatcher` (Ratcliff/Obershelp 算法) 进行模糊匹配
- 相似度阈值设置为 0.4 (40%)
- **仅有扩展名检查，无文件内容验证**

**问题场景**:
```
用户把 "歌曲.txt" 改名为 "歌曲.wav"
→ 系统扫描时会被加入音乐列表（因为扩展名是 .wav）
→ 用户点播时会尝试播放
→ 播放失败（文件内容不是音频）
→ 只有 error 日志，没有 LLM 回复告诉用户问题所在
```

**影响**: 用户体验差，无法得知播放失败的真正原因

---

### 问题二：工具错误直接传递给 TTS 合成

**位置**: `core/connection.py:1233-1243`

**现状分析**:
```python
if result.action in [Action.RESPONSE, Action.NOTFOUND, Action.ERROR]:
    text = result.response if result.response else result.result
    self.tts.tts_one_sentence(self, ContentType.TEXT, content_detail=text)
```

**问题**:
1. 所有 Executor 直接返回 `str(e)` 作为 response
2. 框架没有统一的错误类和错误码枚举
3. 没有 Hook/拦截器机制转换错误为友好提示
4. 原始错误信息（如 "401 Unauthorized"）会被 TTS 直接合成语音

**影响**: 用户听到技术性错误信息，体验极差

---

### 问题三：音频增强算法耦合在 ASR 实现中

**位置**: `core/providers/asr/azure_stream_new.py`

**现状分析**:
```python
# 硬编码的音频处理参数
self.enable_audio_preprocess = True
self.enable_highpass = True
self.enable_agc = True
self.enable_limiter = True
self.highpass_alpha = 0.962
self.agc_target_rms = 2600.0
# ... 更多参数
```

**问题**:
1. 高通滤波、AGC、限幅器算法直接嵌入 ASR 类
2. 其他 ASR provider 如需相同功能必须复制代码
3. 无法独立测试音频增强逻辑
4. 修改算法需改动多处代码

**影响**: 代码重复、维护困难、扩展性差

---

### 问题四：日志系统配置可能存在重复

**位置**:
- `config/logger.py` - 框架日志系统
- `run_log.py` - 启动脚本

**现状分析**:

| 组件 | 功能 | 输出方式 |
|------|------|----------|
| `config/logger.py` | loguru 日志框架 | 控制台 + 文件（带轮转） |
| `run_log.py` | 启动脚本 | 将 stdout/stderr 重定向到文件 |

**结论**: **不是重复配置，而是互补关系**
- `config/logger.py` 是应用层日志系统，使用 loguru 进行结构化日志记录
- `run_log.py` 是进程层启动脚本，捕获所有控制台输出（包括非 loguru 的输出）

**潜在问题**:
- 日志可能写入两个位置：loguru 的 `server.log` 和 run_log 的 `server.{timestamp}.log`
- 配置不统一时可能导致日志分散

---

### 问题五：缺乏 API 服务健康检查和容灾机制

**位置**: `core/connection.py` 及各 provider 实现

**现状分析**:

| 服务 | 初始化方式 | 健康检查 | 失败提示 | 重试机制 |
|------|-----------|----------|----------|----------|
| ASR | `_initialize_asr()` | ❌ 无 | ❌ 仅日志 | ❌ 无 |
| TTS | `_initialize_tts()` | ❌ 无 | ❌ 仅日志 | ❌ 无 |
| LLM | 动态加载 | ❌ 无 | ❌ 仅日志 | ❌ 无 |
| MCP | `_initialize_mcp_endpoint()` | ✅ `is_ready()` | ❌ 仅日志 | ❌ 无 |

**问题场景**:
```
1. 用户启动服务，ASR 配置错误（如 API Key 无效）
2. 服务正常启动，无任何提示
3. 用户说话后，识别失败
4. 系统静默失败或仅记录日志
5. 用户不知道问题所在，反复尝试
```

**现有代码的问题**:

```python
# connection.py:532-533 - 仅记录日志，无用户通知
except Exception as e:
    self.logger.bind(tag=TAG).error(f"实例化组件失败: {e}")

# 各 provider 的错误处理
# ASR/TTS/LLM 失败时只记录日志，不通知用户
```

**缺失的功能**:
1. **启动时健康检查**: 服务启动后不验证各 API 是否可用
2. **连接状态追踪**: 没有统一的服务状态管理
3. **用户通知机制**: API 失败时无法告知用户具体问题
4. **自动重试策略**: 网络波动时无自动恢复机制
5. **降级方案**: 主服务失败时无备用方案

**影响**:
- 用户体验差：不知道服务是否正常
- 排查困难：需要查看日志才能发现问题
- 服务可靠性低：单点故障无恢复能力

---

## 🎯 开发计划

### 优先级 P0（高优先级）

#### 1. 统一错误处理框架

**目标**: 实现错误码枚举、友好提示映射、Hook 机制

**文件结构**:
```
core/
├── errors/
│   ├── __init__.py
│   ├── error_codes.py      # 错误码枚举
│   ├── error_handler.py    # 错误处理器
│   └── error_messages.py   # 友好提示映射
```

**实现内容**:
- [ ] 定义 `ErrorCode` 枚举（网络错误、认证失败、超时等）
- [ ] 创建错误码到友好提示的映射表
- [ ] 实现 `ErrorHandler` 类，统一处理工具调用错误
- [ ] 在 `unified_tool_manager.py` 中集成错误处理器
- [ ] 添加日志记录（详细技术错误）与用户提示（友好消息）分离

**预期收益**:
- 用户听到友好的错误提示
- 开发者通过日志查看详细错误信息
- 统一的错误处理入口，便于维护

---

#### 2. 音频增强模块解耦

**目标**: 创建独立的音频增强模块，支持复用和配置

**文件结构**:
```
core/providers/
├── audio_enhance/
│   ├── __init__.py
│   ├── base.py              # 虚基类
│   ├── factory.py           # 工厂类
│   ├── pipeline.py          # 处理管道
│   ├── processors/
│   │   ├── __init__.py
│   │   ├── highpass.py      # 高通滤波器
│   │   ├── agc.py           # 自动增益控制
│   │   └── limiter.py       # 限幅器
```

**实现内容**:
- [ ] 定义 `AudioEnhancerBase` 虚基类
- [ ] 实现 `AudioPipeline` 组合模式
- [ ] 实现各处理器（HighpassFilter、AGC、Limiter）
- [ ] 创建 `AudioEnhanceFactory` 工厂类
- [ ] 修改 `azure_stream_new.py` 使用新的音频增强模块
- [ ] 添加配置文件支持

**预期收益**:
- 代码复用：所有 ASR 共享音频增强逻辑
- 可测试性：每个组件可独立测试
- 可配置性：通过配置文件调整参数
- 扩展性：新增处理器无需修改现有代码

---

#### 3. API 服务健康检查与容灾机制

**目标**: 实现服务状态监控、用户通知、自动重试

**文件结构**:
```
core/
├── health/
│   ├── __init__.py
│   ├── health_checker.py     # 健康检查器
│   ├── service_status.py     # 服务状态管理
│   └── retry_policy.py       # 重试策略
```

**实现内容**:
- [ ] 定义 `ServiceStatus` 数据类（服务名、状态、错误信息、最后检查时间）
- [ ] 实现 `HealthChecker` 类，支持启动时和运行时健康检查
- [ ] 为各 Provider 添加 `health_check()` 方法
- [ ] 实现服务状态追踪和存储
- [ ] 添加用户通知机制（通过 TTS 告知服务状态）
- [ ] 实现指数退避重试策略（复用现有重试机制）
- [ ] 添加降级方案（如 LLM 失败时使用备用模型）

**健康检查场景划分**:

| 场景 | 触发时机 | 检查深度 | 超时设置 |
|------|----------|----------|----------|
| **启动检查** | 服务启动时 | 完整检查 | 30秒 |
| **运行时检查** | 用户请求前 | 快速检查 | 5秒 |
| **定期检查** | 后台定时 | 完整检查 | 30秒 |

**配置项设计**:

```yaml
health_check:
  enabled: true
  startup_check: true
  periodic_check:
    enabled: true
    interval: 300  # 5分钟检查一次
  timeout:
    asr: 5
    tts: 5
    llm: 10
  retry:
    max_retries: 3
    backoff_factor: 2
    max_delay: 30
```

**用户通知时机**:

| 场景 | 通知方式 | 示例 |
|------|----------|------|
| 启动时服务不可用 | TTS 语音 | "ASR 服务连接失败，请检查配置" |
| 运行时服务中断 | TTS 语音 | "网络不稳定，请稍后再试" |
| 服务恢复 | TTS 语音 | "服务已恢复正常" |

**各服务健康检查实现建议**:

```python
# ASR 健康检查
class ASRProviderBase:
    async def health_check(self) -> ServiceStatus:
        """检查 ASR 服务是否可用"""
        try:
            # 尝试建立连接并发送测试音频
            return ServiceStatus(
                name="ASR",
                status=True,
                message="ASR 服务正常"
            )
        except Exception as e:
            return ServiceStatus(
                name="ASR",
                status=False,
                message=f"ASR 服务不可用: {str(e)}",
                solution="请检查 speech_key 和 service_region 配置"
            )

# TTS 健康检查
class TTSProviderBase:
    async def health_check(self) -> ServiceStatus:
        """检查 TTS 服务是否可用"""
        try:
            # 尝试合成测试文本
            return ServiceStatus(name="TTS", status=True, message="TTS 服务正常")
        except Exception as e:
            return ServiceStatus(
                name="TTS",
                status=False,
                message=f"TTS 服务不可用: {str(e)}",
                solution="请检查 TTS 配置和网络连接"
            )
```

**用户通知流程**:
```
服务启动 → 健康检查 → 失败时通过默认 TTS 告知用户
                     → 记录详细错误日志
                     → 提供解决建议
```

**预期收益**:
- 用户启动时即可知道服务状态
- 故障时提供明确的解决建议
- 自动重试提升服务可靠性
- 降级方案保证基本功能可用

---

### 优先级 P1（中优先级）

#### 4. 播放音乐工具容灾增强

**目标**: 添加文件内容验证，提升用户体验

**实现内容**:
- [ ] 添加音频文件头验证（MP3: ID3/MPEG sync、WAV: RIFF header）
- [ ] 播放失败时返回友好提示（通过错误处理框架）
- [ ] 添加文件损坏检测和跳过机制
- [ ] 支持播放失败自动跳到下一首

**预期收益**:
- 自动过滤无效音频文件
- 播放失败时给出友好提示
- 提升音乐播放的稳定性

---

#### 5. 日志系统整合优化

**目标**: 统一日志输出路径，避免日志分散

**实现内容**:
- [ ] 统一 `config/logger.py` 和 `run_log.py` 的日志目录配置
- [ ] 添加日志文件命名规范（包含版本、模块信息）
- [ ] 优化日志轮转策略（按日期、大小双重轮转）
- [ ] 添加日志清理机制（自动删除过期日志）

**预期收益**:
- 日志集中管理，便于排查问题
- 自动清理，避免磁盘空间浪费
- 统一的命名规范，便于日志分析

---

### 优先级 P2（低优先级）

#### 6. 工具框架增强

**目标**: 提升工具系统的健壮性和可扩展性

**实现内容**:
- [ ] 添加工具调用超时机制（全局可配置）
- [ ] 实现工具调用重试策略（指数退避）
- [ ] 添加工具调用统计和监控
- [ ] 支持工具调用链路追踪

**预期收益**:
- 防止单个工具调用阻塞整个系统
- 提升工具调用的成功率
- 便于性能分析和问题定位

---

## 📊 开发时间估算

| 任务 | 优先级 | 预估工时 | 依赖关系 |
|------|--------|----------|----------|
| 统一错误处理框架 | P0 | 2-3 天 | 无 |
| 音频增强模块解耦 | P0 | 3-4 天 | 无 |
| API 服务健康检查与容灾 | P0 | 3-4 天 | 依赖错误处理框架 |
| 播放音乐容灾增强 | P1 | 1-2 天 | 依赖错误处理框架 |
| 日志系统整合优化 | P1 | 1 天 | 无 |
| 工具框架增强 | P2 | 2-3 天 | 依赖错误处理框架 |

**总计**: 12-17 天

---

## 🔧 技术债务

1. **硬编码问题**: 多处配置硬编码在代码中，应提取到配置文件
2. **异常处理不一致**: 各模块异常处理方式不统一
3. **测试覆盖不足**: 缺少单元测试和集成测试
4. **文档缺失**: API 文档和架构文档不完善

---

## 📝 备注

- 本总结基于当前代码库分析，实际开发中可能发现新问题
- 建议按优先级逐步推进，每完成一个任务进行代码评审
- 开发过程中注意保持向现有代码的向后兼容性
