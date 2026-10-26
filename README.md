# hermes-plugin-nai-image

将 [astrbot_plugin_nai_image](https://github.com/woakato/astrbot_plugin_nai_image) (v2.9.0) 完整移植适配为 **Hermes Agent** 原生生图插件。

## 核心能力
- **三个原生 Agent 工具**：`nai_generate_image`（生图并自动附带 `MEDIA:` 回发到网页与 QQ）、`nai_search_grimoire`（检索 19,663 条实测法典词条）、`nai_quota`（查询直连点数）。
- **双通道支持**：NAI 直连（`POST /api/web/jobs` 任务接口优先 + `GET /generate` 兜底）与 OpenAI 兼容通道（`/v1/images/generations`）。
- **增强特性**：画风槽位（同时切换底模 + 画师串）、`【法典:词条名】` 宏展开、多角色左右分区防串色（`char1_prompt` / `char2_prompt`）、侧栏 `/nai-image` 终末地风格在线测试与槽位管理终端。

## 安装方式
在 Hermes Web UI「插件管理 → Git 安装插件」中输入：
```text
LinYoNv/hermes-plugin-nai-image
```
