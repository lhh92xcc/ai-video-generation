# AI Video Generation

一个面向 AI 漫剧和短视频生产的端到端工程 Demo。项目把小说或主题拆分为可编辑、可追踪、可恢复的异步步骤：内容结构化、剧本、分镜、角色资产、参考图、视频片段、配音、字幕和最终成片。

## 核心能力

- 小说 `.txt/.md` 导入、章节切分和 StoryBible。
- 分集大纲、分场剧本、结构化 JSON 分镜和受控 Schema 修复。
- 角色、场景、道具资产库，版本、别名、审核和分镜绑定门禁。
- 可插拔 LLM、图片、视频、TTS、ASR、BGM 和对象存储 Provider。
- 发音词典：只改写发送给 TTS 的 `tts_text`，剧本和字幕保留原文。
- 标准人设图：角色首张合格参考图自动成为身份锚点。
- 镜头身份初审：对含角色的视频抽样执行 InsightFace 相似度检查。
- 身份阈值校准：只读比较候选阈值，不修改生产配置；身份失败镜头支持幂等批量重试。
- 角色声音资产库：可按角色绑定 Edge TTS、ChatTTS、macOS say 或 Mock 声音档案，并按对白行生成多角色音频。
- MuseTalk 接口：支持从视频/音频 Artifact 创建唇形同步任务，完成后可在成片编排中选择 `lip_synced_video`，原始片段可回退。
- Windows GPU 主机运行档案：串行 GPU 锁、自动推进、失败恢复、磁盘清理和一键健康检查；参数按显存和实测结果调整，不绑定具体显卡型号。
- 远程生产队列：后台可查看 Worker、Redis、Ollama、ComfyUI、MuseTalk、GPU 锁、自动 Run 和失败任务。
- Redis Worker 异步任务、幂等、失败重试、批次编排和 Artifact Registry。
- FFmpeg 多镜头拼接、旁白/BGM 混音、中文字幕烧录和临时下载。
- 本地/Mock Artifact 浏览器预览：通过授权的 `/api/v1/artifacts/{artifact_id}/content` 读取图片、视频、音频和下载文件，支持 HTTP Range；资产列表默认优先展示真实图片/视频缩略图，预览失败时提供重试和签名 URL 回退，不暴露磁盘路径或内部存储 URI。
- Vue 创作者前台与内部制作后台；根路径默认进入普通用户前台，后台“概览”提供真实运行摘要和快捷入口，Provider 配置是独立的系统页面；创作者工作区按“内容理解 / 剧本与分镜 / 资产审核 / 媒体生成 / 审核与成片”五阶段导航，并明确区分 10 个核心门槛和 11 个详细执行步骤；镜头清单支持状态筛选和关键词搜索，任务记录支持重复任务聚合；媒体资产默认以图库展示真实图片/视频缩略图，也可切换列表视图，点击后通过授权内容接口预览、播放和下载。

## 流水线

```text
小说/主题
  → StoryBible/脚本/分镜
  → 角色/场景/道具资产审核
  → 标准人设图与分镜关键帧
  → 视频片段 → 身份初审/阈值校准/失败重试
  → 配音/角色声音资产/MuseTalk
  → 字幕/BGM
  → FFmpeg 成片
```

业务层只依赖 Provider/Artifact 契约，具体供应商和模型通过配置切换。长任务由 API 创建并交给 Worker，HTTP 请求不会同步等待模型生成。

## 技术栈

- Backend：Python、FastAPI、Pydantic、SQLAlchemy、PostgreSQL、Redis。
- Frontend：Vue 3、TypeScript、Vite、Nginx。
- Media：FFmpeg、ffprobe、libass、Noto Sans CJK。
- Local AI：Ollama、ComfyUI Flux/Wan、PuLID/InsightFace、Edge TTS。
- Cloud adapters：OpenAI-compatible、SiliconFlow、Alibaba DashScope。

## 快速启动

需要 Docker Desktop、Python 3.11+、Node.js LTS。开发环境默认使用 Mock Provider，不会自动调用外部模型。

```bash
docker compose up -d --build frontend api worker
curl http://127.0.0.1:8000/healthz
```

打开 <http://127.0.0.1:3000> 查看创作者前台，API 地址为 <http://127.0.0.1:8000>。需要进入内部后台时，点击“进入制作后台”；也可以直接打开 `/?view=overview` 查看运行概览，或 `/?view=provider` 管理 Provider 配置。

查看日志：

```bash
docker compose logs -f api worker
```

不使用 Docker 时，可以安装开发依赖并启动内存模式：

```bash
uv sync --dev
uv run uvicorn app.main:app --reload
```

## 接入本地 Ollama/ComfyUI

API 和 Worker 在 Docker 中运行、Ollama/ComfyUI 在宿主机运行时，容器应使用 `host.docker.internal` 访问宿主机：

```bash
AI_VIDEO_LLM_PROVIDER=ollama \
AI_VIDEO_LLM_BASE_URL=http://host.docker.internal:11434/v1 \
AI_VIDEO_LLM_MODEL=qwen2.5:7b \
AI_VIDEO_IMAGE_PROVIDER=comfyui \
AI_VIDEO_IMAGE_BASE_URL=http://host.docker.internal:8188 \
AI_VIDEO_VIDEO_PROVIDER=comfyui_wan_i2v \
AI_VIDEO_VIDEO_BASE_URL=http://host.docker.internal:8188 \
docker compose up -d --build api worker
```

ComfyUI 的模型、工作流和显存参数属于宿主机配置，不会打包进本仓库。Windows GPU 主机应先从单张 512×512 参考图和一个 3 秒、320×576、8fps 的低显存 Wan I2V 镜头开始，并保持串行生成；再根据实际显存、耗时和画面质量调整分辨率、采样步数和时长。配置档案不要求某个固定显卡型号。

## 配置与安全

- `config/config.example.toml`：不含密钥的配置示例。
- `config/pronunciation.toml`：TTS 发音替换表。
- `config/comfyui/`：ComfyUI API workflow 模板。
- `prompts/`：运行时 Prompt，不包含个人密钥。
- 真实密钥只放在未提交的 `.env` 或运行时环境变量中。

公开仓库已排除学习/协作文档、模型、虚拟环境、运行产物、媒体文件和本地配置；这些内容不会进入 Git 历史。

### 运行时选择 Provider

创作者前台的完整生产配置支持分别选择参考图和视频 Provider。选择结果只写入新建任务的快照，不会修改已经运行的任务，也不需要手动编辑 TOML。未配置密钥的云端档案会显示为不可选；本地 Mock、ComfyUI、FFmpeg 和 Wan 档案不需要云端 API Key。

后端接口为 `GET /api/v1/provider-profiles?capability=image` 和 `GET /api/v1/provider-profiles?capability=video`。真实云端档案的密钥只从运行环境读取：`AI_VIDEO_IMAGE_SILICONFLOW_API_KEY`、`AI_VIDEO_IMAGE_OPENAI_COMPATIBLE_API_KEY`、`AI_VIDEO_VIDEO_SILICONFLOW_API_KEY`、`AI_VIDEO_VIDEO_OPENAI_COMPATIBLE_API_KEY`；兼容保留 `AI_VIDEO_IMAGE_API_KEY` 和 `AI_VIDEO_VIDEO_API_KEY`。

## 验证

```bash
uv run pytest -q
uv run python -m compileall -q app tests evaluation
npm run build --prefix frontend
docker compose config --quiet
```

当前回归基线为 `303 passed、7 skipped、1 warning`。真实 Wan、InsightFace、MuseTalk 和外部 API 不在普通测试中自动调用。

创作者前台将流程分为五个业务阶段、十个核心门槛和十一个详细执行步骤：内容理解、剧本与分镜、资产审核、媒体生成、审核与成片；“一键启动完整生产”用于自动 Run，“推进分集生产计划”用于手动选择分集和断点调试。两者都保留剧本、资产和人工审核门禁，BGM 作为可选步骤不阻塞主流程。

## 当前边界

这是用于学习、面试和端到端工程展示的 Demo，不等同于生产 SaaS。身份校准、失败镜头批量重试、声音资产、多角色音频、MuseTalk 任务/Mock/HTTP/Assembly 接口、GPU 主机运维闭环和远程队列页面已完成；真实 MuseTalk 仍需在目标主机配置独立 runtime、wrapper 和模型目录。完整登录会话、组织级权限、全局优先级/成本配额、死信队列、自动发布和正式画面质量验收仍需继续完善。自动身份相似度只是初审，异常镜头仍必须人工看片。

## 作品集 Demo 验收清单

目标是交付一条 45～60 秒、9:16 的动态漫短剧样片，并让面试官能沿着任务、Artifact 和日志复盘整个过程。

- 已具备：小说上传、StoryBible/剧本/分镜 JSON、资产审核门禁、参考图与视频 Provider 抽象、异步 Worker、失败重试、音频/字幕/成片 Artifact、远程队列和创作者前台。
- 本地质量基线：Mac 16GB 使用竖屏 `512×768` 参考图、Wan2.1 I2V `320×576`/`8fps`/`6 steps`/串行生成；参考图和视频 Prompt 默认包含单主体、身份连续性和防变脸约束。模型仍在宿主机，不进入仓库。
- 仍需人工完成：实际跑一遍完整样片，筛掉变脸/手部/动作崩坏镜头，听审旁白并核对字幕，填写身份与声音质量评分。
- 云端扩展边界：即梦尚未写入业务层；拿到官方 endpoint、模型名、鉴权和异步响应样例后，只需新增独立 `VideoGenerationProvider` 适配器，并用单镜头 smoke 验证，再接入 Provider 选择 UI。

这条清单把“工程闭环已完成”和“媒体质量尚未验收”分开，避免把通过单测或 FFprobe 误写成成片质量结论。

## 距离合格作品集 Demo 还差什么

“合格”在这里指：面试官能看到真实的端到端工程闭环，同时样片达到可观看、可解释、可复盘，而不是宣称已经具备商用量产画质。

| 门槛 | 当前状态 | 还需要的证据 |
| --- | --- | --- |
| 小说到结构化剧本/分镜 | 工程链路已具备 | 用一段自有或已授权短文本跑通，并人工检查改编是否忠实、对白是否自然 |
| 角色/场景/道具一致性 | 资产版本、标准人设图、Prompt 和自动初审已具备 | 实际生成 2～3 个角色镜头，筛掉变脸、重复人物、手部和构图失败结果 |
| 真实视频片段 | Wan I2V Provider、断点恢复和 FFprobe 门禁已具备 | 在目标主机完成 3 秒 smoke，再完成 45～60 秒成片并记录耗时、失败和重试 |
| 声音与字幕 | 连续旁白、ASR/对齐、字幕 Artifact 和渲染已具备 | 人工听审自然度、发音、音画同步和字幕可读性；保留评分表和样片 |
| 成片与可复盘性 | Assembly、Artifact、任务、日志和远程队列已具备 | 将最终视频、关键截图、运行报告和一段架构说明放入作品集展示材料 |
| 商用扩展认知 | Provider/Adapter 边界已具备 | 补一页本地方案与云端方案的成本、质量、延迟、版权和失败重试对比 |

当前最关键的缺口不是继续堆功能，而是完成一次真实目标设备验收，并把“通过/失败/人工返工”记录下来。单元测试、HTTP 200、Artifact 成功和 FFprobe 通过，都不能替代画面与声音审核。

## 即梦 API 接入计划

即梦适合作为后续的高质量对照样片 Provider，但在没有官方接口文档、模型名、鉴权方式、请求示例和异步响应示例前，本项目不会猜测 endpoint 或协议，也不会把供应商字段散落到业务层。

接入时保持以下边界：

1. 新增独立 `JimengVideoGenerationProvider`，实现现有 `VideoGenerationProvider` 契约。
2. 适配器负责请求体映射、鉴权、提交任务、轮询/回调、下载、超时、限流、余额和供应商错误码映射。
3. 业务层继续只传镜头 Prompt、negative Prompt、参考图、时长、尺寸和生成尝试号；供应商原始响应写入脱敏 metadata。
4. 先做单镜头 smoke，再做 2～3 镜头对照；确认画面、费用和稳定性后，才接入前台 Provider 选择和批量 Run。
5. API Key 只放 `.env` 或部署环境变量，绝不提交 GitHub；即梦样片与本地 Demo 使用独立配置和预算门禁。

因此，后续你只需要提供即梦官方开发文档中的接口资料和测试 Key（不要把 Key 发到聊天或提交到仓库），我就能在不改动小说、资产、任务和成片契约的前提下接入它。

## Windows GPU 主机

Mac 端只维护代码、Prompt、配置和前端，不下载 Windows/CUDA 模型。将仓库同步到 Windows 后，在 Windows 主机安装 Docker Desktop、Ollama、ComfyUI、Wan/Flux 模型和真实 MuseTalk runtime；Docker 内的 API/Worker 通过 `host.docker.internal` 访问这些宿主机服务。

使用专用配置档案启动：

```powershell
$env:AI_VIDEO_PROFILE = "windows_gpu"
$env:AI_VIDEO_CONFIG = "config/config.windows_gpu.toml"
.\scripts\start-windows-gpu.ps1 -ProjectRoot (Get-Location).Path
```

启动器会检查 Docker Desktop、Ollama、ComfyUI 和 MuseTalk bridge，启动 Compose 的 API、Worker、前端和基础设施，并执行一次健康检查。首次使用真实 MuseTalk 前，需要设置 `MUSETALK_WRAPPER_PATH`、`MUSETALK_MODEL_ROOT`，并让 wrapper 接受 `--video`、`--audio`、`--output`、`--face-region`、`--face-padding`、`--device`（可选 `--model-root`），在 `--output` 写出 MP4。

远程队列页面位于 <http://127.0.0.1:3000> 的“远程队列”；接口为 `GET /api/v1/system/health`、`GET /api/v1/system/queue` 和 `POST /api/v1/system/cleanup`。完整小说生产可以从前台“自动生产”入口创建 `production run`，Worker 会按依赖自动推进 StoryBible、分集、剧本、分镜、参考图、音频、字幕、视频和 Assembly。
