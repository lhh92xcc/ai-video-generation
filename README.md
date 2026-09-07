# AI Video Generation

[![CI](https://github.com/lhh92xcc/ai-video-generation/actions/workflows/ci.yml/badge.svg)](https://github.com/lhh92xcc/ai-video-generation/actions/workflows/ci.yml)

一个面向 AI 漫剧和短视频生产的端到端工程 Demo。项目把小说或主题拆分为可编辑、可追踪、可恢复的异步步骤：内容结构化、剧本、分镜、角色资产、参考图、视频片段、配音、字幕和最终成片。

## 核心能力

- 小说 `.txt/.md` 导入、章节切分和 StoryBible。
- 分集大纲、分场剧本、结构化 JSON 分镜和受控 Schema 修复。
- 角色、场景、道具资产库，版本、别名、审核和分镜绑定门禁。
- 可插拔 LLM、图片、视频、TTS、ASR、BGM 和对象存储 Provider。
- 发音词典：只改写发送给 TTS 的 `tts_text`，剧本和字幕保留原文。
- 标准人设图：角色首张合格参考图自动成为身份锚点。
- 逐镜头身份关键帧：标准人设图完成后，含角色镜头可按 `auto`、`always` 或 `off` 策略生成身份锁定关键帧；视频任务优先使用当前镜头的关键帧。
- 镜头身份初审：对含角色的视频抽样执行 InsightFace 相似度检查。
- 身份阈值校准：只读比较候选阈值，不修改生产配置；身份失败镜头支持幂等批量重试。
- 角色声音资产库：可按角色绑定 Edge TTS、ChatTTS、macOS say 或 Mock 声音档案，并按对白行生成多角色音频。
- MuseTalk 接口：支持从视频/音频 Artifact 创建唇形同步任务，完成后可在成片编排中选择 `lip_synced_video`，原始片段可回退。
- Windows GPU 主机运行档案：串行 GPU 锁、自动推进、失败恢复、磁盘清理和一键健康检查；参数按显存和实测结果调整，不绑定具体显卡型号。
- 远程生产队列：后台可查看 Worker、Redis、Ollama、ComfyUI、MuseTalk、GPU 锁、自动 Run 和失败任务。
- 运行日志：后台按时间回看任务状态、阶段尝试、错误码、安全运行上下文和 Artifact 摘要；不展示 Prompt、密钥或存储路径。
- Redis Worker 异步任务、幂等、失败重试、批次编排和 Artifact Registry。
- FFmpeg 多镜头拼接、旁白/BGM 混音、中文字幕烧录和临时下载。
- 本地/Mock Artifact 浏览器预览：通过授权的 `/api/v1/artifacts/{artifact_id}/content` 读取图片、视频、音频和下载文件，支持 HTTP Range；资产列表默认优先展示真实图片/视频缩略图，预览失败时提供重试和签名 URL 回退，不暴露磁盘路径或内部存储 URI。
- Vue 创作者前台与内部制作后台；根路径默认进入普通用户前台，后台“概览”提供真实运行摘要和快捷入口，Provider 配置是独立的系统页面；创作者工作区按“内容理解 / 剧本与分镜 / 资产审核 / 媒体生成 / 审核与成片”五阶段导航，并明确区分 10 个核心门槛和 11 个详细执行步骤；镜头清单支持状态筛选和关键词搜索，任务记录支持重复任务聚合；媒体资产默认以图库展示真实图片/视频缩略图，也可切换列表视图，点击后通过授权内容接口预览、播放和下载。
- 作品集 Demo 就绪度面板：把真实任务/Artifact 的机器门禁与改编、角色、动作、声音、字幕、成片观感等人工验收清单分开显示；人工勾选只保存在当前浏览器，不会伪造服务端质量结论。
- 作品集交付报告：就绪度面板可按当前分集在浏览器端下载 Markdown 和 JSON 验收报告，包含机器门禁、人工审核状态、成片规格、Provider 证据和下一步，不包含密钥、本地路径或存储地址。
- 作品集真实运动门禁：当前分集按镜头只读取最新的成功视频任务和对应 Artifact，拒绝 `mock`、`local_fixture`、`ffmpeg_motion`、未知 Provider 及静态/Fixture 元数据，避免历史重试产物污染正式样片判断。
- 本地作品集 runner 的 `--shots` 支持 1～12：1～7 个镜头用于 smoke/断点演练，正式作品集目标为 8～12 个镜头，默认 10 个；`--stop-after-shot` 与 `--resume` 使用同一范围。
- 创作者前台已完成一轮可读性与视觉层级优化：统一提升正文、辅助说明、质量档案卡和作品集门禁的字号与间距，增加轻量渐变背景、层次阴影和清晰状态反馈；移动端仍使用单列布局。该优化只改善操作体验，不改变任务、Provider 或质量结论。
- 自动生产 Run 完成态已闭环：Worker/scheduler 在推进 DAG 时只把 `created`/`reused` 任务视为下一波工作，不会把用于界面追踪的已完成任务 ID误判为新任务；最终 Assembly 成功后 Run 会稳定进入 `completed`。

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

打开 <http://127.0.0.1:3000> 查看创作者前台，API 地址为 <http://127.0.0.1:8000>。需要进入内部后台时，点击“进入制作后台”；也可以直接打开 `/?view=overview` 查看运行概览、`/?view=logs` 查看运行日志，或 `/?view=provider` 管理 Provider 配置。

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

ComfyUI 的模型、工作流和显存参数属于宿主机配置，不会打包进本仓库。目标 GPU 主机应先在前台选择 `local_safe`，从单张严格 9:16 参考图和一个 3 秒、288×512、12fps 的低压 Wan I2V 镜头开始，并保持串行生成；确认稳定后再切换 `local_balanced`。低压档用于缩小失败范围，不建议直接作为最终成片档案。配置档案不要求某个固定显卡型号。

Windows 配置中的 ComfyUI 模型名默认与本地目标 workflow 对齐：`flux1-schnell-Q4_K_S.gguf` 和 `wan2.1-i2v-14b-480p-Q4_K_S.gguf`。如果目标主机安装的是同系列其他量化文件，只需通过环境变量覆盖模型名，不要修改业务代码。

## 配置与安全

- `config/config.example.toml`：不含密钥的配置示例。
- `config/pronunciation.toml`：TTS 发音替换表。
- `config/comfyui/`：ComfyUI API workflow 模板。
- `prompts/`：运行时 Prompt，不包含个人密钥。
- 真实密钥只放在未提交的 `.env` 或运行时环境变量中。

公开仓库已排除学习/协作文档、模型、虚拟环境、运行产物、媒体文件和本地配置；这些内容不会进入 Git 历史。

### 运行时选择 Provider

创作者前台的完整生产配置支持分别选择参考图和视频 Provider；内部后台的 Provider 页面进一步按“字幕识别 / 参考图生成 / 视频片段”三个能力页签展示同一套安全档案，便于检查 API/Worker 的实际配置状态。选择结果只写入新建任务的快照，不会修改已经运行的任务，也不需要手动编辑 TOML。未配置密钥的云端档案会显示为不可选；本地 Mock、ComfyUI、FFmpeg 和 Wan 档案不需要云端 API Key。

后端接口为 `GET /api/v1/provider-profiles?capability=image` 和 `GET /api/v1/provider-profiles?capability=video`。真实云端档案的密钥只从运行环境读取：`AI_VIDEO_IMAGE_SILICONFLOW_API_KEY`、`AI_VIDEO_IMAGE_OPENAI_COMPATIBLE_API_KEY`、`AI_VIDEO_VIDEO_SILICONFLOW_API_KEY`、`AI_VIDEO_VIDEO_OPENAI_COMPATIBLE_API_KEY`；兼容保留 `AI_VIDEO_IMAGE_API_KEY` 和 `AI_VIDEO_VIDEO_API_KEY`。

视觉质量档案通过 `GET /api/v1/visual-quality-profiles` 提供给创作者前台，当前包含 `local_safe`、`local_balanced` 和 `high_quality` 三档。它们统一约束参考图/视频分辨率、采样步数、CFG、身份权重、帧率和 I2V 噪声增强参数；选择结果会写入新任务快照，后续修改默认档案不会改变历史任务。质量档案不等同于画质保证，真实 ComfyUI/Wan 运行仍需在目标 GPU 主机人工验收。

逐镜头身份关键帧策略通过 `shot_keyframe_mode` 传入分集任务计划和完整生产 Run：`auto` 在当前图片 Provider 支持身份输入时为含角色镜头生成关键帧，否则回退标准人设图；`always` 在身份锁定能力不可用时返回 `SHOT_KEYFRAME_PROVIDER_UNSUPPORTED` 阻塞；`off` 完全关闭该阶段。计划器会先等待标准人设图成功，再按镜头生成关键帧，最后创建视频片段任务。关键帧任务保存分集、镜头编号和标准身份锚点，历史缺少这些字段的参考图仍按旧的标准图选择规则兼容处理。

默认使用 `local_safe`；Windows GPU 配置档案默认使用 `local_balanced`。也可以通过 `AI_VIDEO_VISUAL_QUALITY_PROFILE` 或 TOML 的 `[visual_quality].default_profile` 设置默认档案，前台新建完整生产、分集计划或单镜头视频任务时可直接通过卡片选择。

| 档案 | 参考图 | 视频片段 | 适用场景 |
| --- | --- | --- | --- |
| `local_safe` | 432×768，4 steps | 288×512，12fps，6 steps | 首次联调、低显存和失败范围控制 |
| `local_balanced` | 576×1024，6 steps | 432×768，16fps，8 steps | Windows 运行主机的首轮作品集样片 |
| `high_quality` | 720×1280，8 steps | 576×1024，16fps，12 steps | 单任务高质量候选，必须先实测 |

档案只提供可复现的起始参数；显存、耗时、身份一致性和动作质量必须以目标机器的真实 smoke 与人工看片为准。
当前视频档案的 `noise_aug_strength` / `motion_zoom` 分别为 `local_safe=0.012/1.04`、`local_balanced=0.010/1.03`、`high_quality=0.008/1.02`；这些参数用于降低参考帧漂移和过强运镜，不是画质保证。

### 视频运动 Prompt 约束

视频任务不会只把模型生成的原始画面描述直接交给 I2V Provider。服务层通过
`app/media/visual_prompts.py` 的 `build_video_motion_prompt()` 统一补充当前镜头的景别、运镜、地点、已审核可见角色、当前镜头精确绑定的 `ready` 资产视觉事实和连续性要求；运行时说明见
[`prompts/video-motion-generation.txt`](prompts/video-motion-generation.txt)。每个镜头被限制为一个 3～5 秒连续镜头和一种可读的轻微动作（例如呼吸、一次眨眼、小幅转头或衣物轻动），并明确禁止新增人物、切镜、换场、大幅变形和同时发生多个复杂动作。

参考图 Prompt 还会把“单一主体、清晰轮廓、明确视觉焦点、干净背景和稳定比例”等正向质量护栏直接写入最终 Prompt，以兼容没有独立 negative-conditioning 分支的 Flux workflow。多角色镜头会明确标记主角色身份，其他角色降为侧脸、剪影或视觉次要对象，减少关键帧阶段的换脸风险。

角色、场景和道具事实按 `asset_key` + `version` 从仓储读取，并把外观、氛围、材质与连续性字段写入 `approved_asset_facts` 任务快照和 `video_clip` Artifact metadata；未审核或找不到的资产不会被猜测补全。这样每个视频任务都能复盘“哪一版资产 → 哪段 Prompt → 哪个 Artifact”，减少同一角色跨镜头换脸和道具漂移。

服务层还会根据景别追加动作安全策略：近景/特写只允许眨眼、呼吸或眼神等微动作，并禁止未设定的说话动作；中景只允许小幅转头、呼吸或克制手势；远景只允许衣物、头发或光线的轻微变化；道具特写只允许焦点、反光或材质微变化。这样能把“画面描述”和“可执行的动作预算”分开，降低 Wan 在短镜头中变脸、手部变形和运动失控的概率。

该组约束是 Provider 无关的输入基线，用于减少变脸、动作崩坏和镜头语义漂移；它不是画质保证。真实 ComfyUI/Wan 运行仍需要在目标 GPU 主机人工筛选镜头，并记录失败和重试结果。

最终 Assembly 默认使用 `libx264`、`medium` preset、`CRF 18`、`animation` tune，并把音频统一输出为 48 kHz 双声道 AAC。编码参数会写入 `rendered_video` Artifact metadata，便于对比不同质量档案；它只能减少二次压缩损失，不能凭空提高模型生成细节。

## 验证

```bash
uv run pytest -q
uv run python -m compileall -q app tests evaluation
npm run build --prefix frontend
docker compose config --quiet
```

本次提交前全量回归为 `341 passed、7 skipped、1 warning`；前端生产构建为 `61 modules transformed`，并通过 Python compileall、`docker compose config --quiet` 和 `git diff --check`。新增回归覆盖自动 Run 从小说入口推进到旁白、字幕、视频片段和最终 Assembly，并验证完成态；该回归使用可播放 Fixture/Assembly 测试替身，不把 Fixture 当作作品集画面。运行日志页面使用现有任务查询接口，不新增测试数据；真实 Wan、InsightFace、MuseTalk 和外部 API 不在普通测试中自动调用。

公开仓库配置了 `.github/workflows/ci.yml`：推送或提交 Pull Request 时自动安装 FFmpeg、执行锁定依赖安装、后端测试、Python 编译检查、前端生产构建和 Docker Compose 配置校验。CI 不需要任何供应商密钥，也不会调用真实模型或付费 API。

创作者前台将流程分为五个业务阶段、十个核心门槛和十一个详细执行步骤：内容理解、剧本与分镜、资产审核、媒体生成、审核与成片；“一键启动完整生产”用于自动 Run，“推进分集生产计划”用于手动选择分集和断点调试。两者都保留剧本、资产和人工审核门禁，BGM 作为可选步骤不阻塞主流程。

`POST /api/v1/novel-projects/{project_id}/production-runs` 是完整小说到成片的专用入口，默认且强制开启 `production_mode`；如果旧客户端显式传入 `false`，接口会拒绝请求，避免误创建只生成剧本/分镜的内容层计划。需要保持内容层兼容行为时，请使用 `POST /api/v1/novel-projects/{project_id}/episode-task-plans`，该接口仍默认 `production_mode=false`。

## 当前边界

这是用于学习、面试和端到端工程展示的 Demo，不等同于生产 SaaS。身份校准、失败镜头批量重试、声音资产、多角色音频、MuseTalk 任务/Mock/HTTP/Assembly 接口、GPU 主机运维闭环和远程队列页面已完成；真实 MuseTalk 仍需在目标主机配置独立 runtime、wrapper 和模型目录。完整登录会话、组织级权限、全局优先级/成本配额、死信队列、自动发布和正式画面质量验收仍需继续完善。自动身份相似度只是初审，异常镜头仍必须人工看片。

## 作品集 Demo 验收清单

目标是交付一条 45～60 秒、9:16 的动态漫短剧样片，并让面试官能沿着任务、Artifact 和日志复盘整个过程。

- 已具备：小说上传、StoryBible/剧本/分镜 JSON、资产审核门禁、参考图与视频 Provider 抽象、异步 Worker、失败重试、音频/字幕/成片 Artifact、远程队列和创作者前台。
- 本地低压基线：使用严格 9:16 的 `432×768` 参考图、Wan2.1 I2V `288×512`/`12fps`/`6 steps`/串行生成；参考图锚点采用正面中性人设、2D 漫画线稿和无道具背景，视频 Prompt 默认包含身份连续性、防变脸和风格锁定约束。模型仍在宿主机，不进入仓库。
- 仍需人工完成：实际跑一遍完整样片，筛掉变脸/手部/动作崩坏镜头，听审旁白并核对字幕，填写身份与声音质量评分。
- 运行报告：`scripts/run-local-portfolio-sample.py` 完成或通过 `--stop-after-shot` 暂停后都会写出统一结构的 `report.json`，其中包含 `portfolio_readiness`、目标规格、机器门禁、人工审核模板和阻塞原因；暂停阶段的未执行步骤标记为 `pending`，真实运行的身份初审没有结果时仍会阻塞就绪状态，`--mock-media` 结果只能作为工程联调证据，不能直接作为作品集成片。
- 作品集 runner 的配置不会绑定某一台机器：优先使用显式 `--config`，其次使用 `AI_VIDEO_CONFIG`/`AI_VIDEO_PROFILE`，只有未选择运行档案时才在本机自动采用存在的 `config/config.local.toml`，否则回退公开的 `config/config.example.toml`。Windows 拉取公开仓库后应设置 `AI_VIDEO_PROFILE=windows_gpu`，不需要也不会依赖 Mac 私有配置。
- 作品集报告版本：本地 runner 与创作者前台导出的 JSON 报告当前使用 schema version `2`；正式运行中 `unavailable`、`error`、`no_face`、`reference_no_face` 和未知身份状态都会阻塞机器门禁，只有含角色镜头全部 `passed` 才能通过；纯场景/道具镜头的 `not_applicable` 不计入审核数量，也不会单独阻塞。
- 正式样片门禁：非 Mock 且非预览模式只允许已登记的真实视频 Provider（当前为 `comfyui_wan_i2v`、`openai_compatible`、`siliconflow`）；`ffmpeg_motion`、`local_fixture` 和 `mock` 会在启动前被拒绝，片段 Artifact 还会再次校验 Provider/运动元数据。需要在 Mac 上只验证流程时可使用 `--preview-only`，但报告会标记 `sample_mode=preview_only`，不会被当作正式作品集证据；报告中的 `real_motion_provider` 记录实际配置和观察到的 Provider 来源。
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
| 成片与可复盘性 | Assembly、Artifact、任务、日志、远程队列和就绪度面板已具备 | 将最终视频、关键截图、`report.json` 和一段架构说明放入作品集展示材料 |
| 跨机器质量可复现 | runner 支持 `--quality-profile`，checkpoint/report 会记录完整参数快照 | 在目标 GPU 主机用同一档案重跑，并把真实耗时、失败镜头和人工筛选结果补入报告 |
| 商用扩展认知 | Provider/Adapter 边界已具备 | 补一页本地方案与云端方案的成本、质量、延迟、版权和失败重试对比 |

当前最关键的缺口不是继续堆功能，而是完成一次真实目标 GPU 主机验收，并把“通过/失败/人工返工”记录下来。单元测试、HTTP 200、Artifact 成功和 FFprobe 通过，都不能替代画面与声音审核。

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
$env:COMFYUI_ROOT = "D:\AI\ComfyUI"
.\scripts\start-windows-gpu.ps1 -ProjectRoot (Get-Location).Path
```

启动器会检查 Docker Desktop、Ollama、ComfyUI 和 MuseTalk bridge，启动 Compose 的 API、Worker、前端和基础设施，并执行一次媒体前置检查。前置检查除了端口，还会读取仓库中的三个 workflow JSON，核对必需节点/占位符，并在设置 `COMFYUI_ROOT` 后检查 Flux、Wan、PuLID、Wan 文本编码器、VAE、InsightFace 和 ComfyUI custom nodes 是否实际存在；检查过程只读，不会下载或覆盖模型。

前置检查通过后，可以让启动器执行一镜头真实媒体 Smoke，先确认目标主机确实能够调用参考图和 Wan I2V 模型：

```powershell
.\scripts\start-windows-gpu.ps1 `
  -ProjectRoot (Get-Location).Path `
  -ComfyUIRoot $env:COMFYUI_ROOT `
  -RunPortfolioSmoke `
  -SmokeQualityProfile local_safe `
  -SmokeShotDuration 3
```

该命令只生成 1 个 3 秒镜头，报告和产物默认写入 `.tmp/windows-portfolio-smoke/`，也可通过 `-SmokeOutputDir` 修改目录。Smoke 会调用真实配置的 Provider；Mock、FFprobe 通过或静态预览都不能证明画面质量。确认单镜头稳定并人工检查身份、动作和声音后，再逐步切换到 `local_balanced` 和 8～12 镜头正式作品集运行。

也可以单独执行严格检查：

```powershell
.\scripts\check-windows-gpu.ps1 `
  -ProjectRoot (Get-Location).Path `
  -ComfyUIRoot $env:COMFYUI_ROOT `
  -RequireComfyUI `
  -ValidateComfyUIAssets `
  -RequireOllamaModel `
  -RequireComposeMedia
```

如果要把 MuseTalk 作为正式对话镜头的必需依赖，再追加 `-RequireMuseTalk`；启动器也支持同名参数。如果暂时只检查 Docker/API 基础设施，可省略媒体检查参数。首次使用真实 MuseTalk 前，需要设置 `MUSETALK_WRAPPER_PATH`、`MUSETALK_MODEL_ROOT`，并让 wrapper 接受 `--video`、`--audio`、`--output`、`--face-region`、`--face-padding`、`--device`（可选 `--model-root`），在 `--output` 写出 MP4。

远程队列页面位于 <http://127.0.0.1:3000> 的“远程队列”；接口为 `GET /api/v1/system/health`、`GET /api/v1/system/queue` 和 `POST /api/v1/system/cleanup`。完整小说生产可以从前台“自动生产”入口创建 `production run`，Worker 会按依赖自动推进 StoryBible、分集、剧本、分镜、参考图、音频、字幕、视频和 Assembly。
