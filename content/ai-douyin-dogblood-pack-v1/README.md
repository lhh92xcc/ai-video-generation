# 《她只剩一层皮》：AI 竖屏狗血短剧试播包

这是一套面向本仓库流水线的原创试播素材：1 个项目、3 集、每集 60 秒、9:16 竖屏。内容借鉴公开行业分析中常见的“隐藏身份、牺牲式恋爱、背叛、复仇、反转、结尾悬念”和“拟人化水果”表现形式，但人物、世界观、事件和台词均为本包原创改写。

## 可直接上传的文件

`source/她只剩一层皮.md` 是 UTF-8 Markdown 原文，当前项目的小说入口直接接受 `.md` 文件。建议在前台创建小说项目时填写：

- 项目名：`她只剩一层皮｜AI水果狗血短剧`
- 目标集数：`3`
- 每集目标时长：`60` 秒
- 版权状态：`pending`（先人工审核，确认后再改为 `confirmed`）

命令行上传示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/novel-projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"她只剩一层皮｜AI水果狗血短剧","language":"zh-CN","target_episode_count":3,"target_episode_duration_seconds":60,"rights_status":"pending"}'

curl -X POST http://127.0.0.1:8000/api/v1/novel-projects/<PROJECT_ID>/sources \
  -F 'file=@content/ai-douyin-dogblood-pack-v1/source/她只剩一层皮.md;type=text/markdown' \
  -F 'rights_status=pending'
```

上传后按项目流水线依次生成 StoryBible、分集、分场剧本、分镜；资产审核为 `ready` 后再提交参考图、视频片段、配音、字幕和成片任务。不要把网页文章或现成短剧的台词直接当作原文发布。

## 结构化文件

- `project-manifest.json`：项目清单、版权提醒和灵感来源。
- `structured/story-bible.json`：可校验的 `StoryBibleContent`。
- `structured/episode-outlines.json`：3 集 `EpisodeOutlineContent`。
- `structured/episode-01.script.json` ～ `episode-03.script.json`：可校验的 `EpisodeScriptContent`。
- `structured/episode-01.shots.json` ～ `episode-03.shots.json`：`shots` 数组，字段符合 `ShotContent`；当前 API 没有“直接上传分镜 JSON”的入口，可用于工作台人工录入、脚本版本接口或 Provider 测试。

结构化 JSON 不包含 UUID、参考图 ID 或已审核资产引用；这些由项目资产库和审核流程生成。视觉 Prompt 已按 9:16、拟人化 3D 水果、连续角色外观来写，正式生成前仍应人工检查人物一致性、台词、时长和平台合规。

## 公开灵感来源（仅用于套路研究）

1. [36氪：《你们爱看的狗血短剧，她自己写完从来不看第二遍》](https://36kr.com/p/3931869914545539)，2026-08-10。文章提到“前三秒钩子、快速反转”、隐藏身份、扮猪吃老虎、重生复仇等常见结构。
2. [澎湃新闻：《AI短剧新赛道！从水果大剧到餐具、蔬菜，内容内卷到离谱》](https://m.thepaper.cn/newsDetail_forward_33071238)，2026-04-29。文章分析“AI + 拟人化载体 + 背叛/牺牲/复仇/手撕反派”的组合，以及水果、餐具、蔬菜、手机等载体变体。

上述页面是行业观察，不是本项目授权剧本。本包不复制其完整文本、人物或现成剧名；发布前仍需由创作者确认素材、声音、音乐、肖像和平台审核状态。
