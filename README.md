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
- Redis Worker 异步任务、幂等、失败重试、批次编排和 Artifact Registry。
- FFmpeg 多镜头拼接、旁白/BGM 混音、中文字幕烧录和临时下载。
- Vue 创作者前台与内部制作后台。

## 流水线

```text
小说/主题
  → StoryBible/脚本/分镜
  → 角色/场景/道具资产审核
  → 标准人设图与分镜关键帧
  → 视频片段
  → 配音/字幕/BGM
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

打开 <http://127.0.0.1:3000> 查看前台，API 地址为 <http://127.0.0.1:8000>。

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

ComfyUI 的模型、工作流和显存参数属于宿主机配置，不会打包进本仓库。RTX 4060 Ti 8GB 应从单张 512×512 参考图和一个 3 秒、320×576、8fps、4 步的 Wan I2V 镜头开始，并保持串行生成；是否稳定需要在目标 Windows 机器上实测。

## 配置与安全

- `config/config.example.toml`：不含密钥的配置示例。
- `config/pronunciation.toml`：TTS 发音替换表。
- `config/comfyui/`：ComfyUI API workflow 模板。
- `prompts/`：运行时 Prompt，不包含个人密钥。
- 真实密钥只放在未提交的 `.env` 或运行时环境变量中。

公开仓库已排除学习/协作文档、模型、虚拟环境、运行产物、媒体文件和本地配置；这些内容不会进入 Git 历史。

## 验证

```bash
uv run pytest -q
uv run python -m compileall -q app tests evaluation
npm run build --prefix frontend
docker compose config --quiet
```

当前回归基线为 `271 passed、7 skipped、1 warning`。真实 Wan、InsightFace 和外部 API 不在普通测试中自动调用。

## 当前边界

这是用于学习、面试和端到端工程展示的 Demo，不等同于生产 SaaS。完整登录会话、组织级权限、全局并发/成本调度、死信队列、自动发布、MuseTalk 主流程和正式画面质量验收仍需继续完善。自动身份相似度只是初审，异常镜头仍必须人工看片。
