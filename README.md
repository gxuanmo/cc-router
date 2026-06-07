# cc-router

本地代理，放在 Claude Code 和 LLM 后端之间。自动检测请求里有没有图片，有图走多模态模型，没图走便宜的文本模型。

内置 23 个供应商预设。浏览器打开 `http://127.0.0.1:8082/status` 就能管理。

## 快速开始

```bash
pip install -r requirements.txt
python router.py
# 浏览器打开 http://127.0.0.1:8082/status 配置
```

**Windows**：双击 `start.bat`

## Claude Code 配置

配置 Claude Code 走本地代理，代替直连后端：

```bash
export ANTHROPIC_BASE_URL="http://127.0.0.1:8082"
export ANTHROPIC_AUTH_TOKEN="cc-router"
export ANTHROPIC_API_KEY=""
```

项目里提供了一个可复制的完整模板：[.claude/settings.cc-router.example.json](.claude/settings.cc-router.example.json)。

使用方式：

1. 打开状态页 `http://127.0.0.1:8082/status`，点击「复制 settings.json」。
2. 手动把 JSON 里的字段合并到 `~/.claude/settings.json`。
3. 不要整文件覆盖你的现有配置，尤其是你已有的 `mcpServers`、`permissions`、`statusLine`、`theme`。

模板里的 `ANTHROPIC_BASE_URL` 固定为 `http://127.0.0.1:8082`，请求会先进入 cc-router，再由 cc-router 按文本/图片自动路由到后端模型。`ANTHROPIC_AUTH_TOKEN` 使用项目约定的 `cc-router`，`ANTHROPIC_API_KEY` 保持空字符串，避免把真实 Key 写进仓库。

模板里的 `mcpServers` 命令和 `statusLine.command` 是本机示例，复制前确认你的机器上装了 `chrome-devtools-mcp`、`context7-mcp`、`playwright-mcp`，并按实际路径调整 `node C:/Users/Administrator/.claude/statusline-wrapper.mjs`。

核心字段如下：

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8082",
    "ANTHROPIC_AUTH_TOKEN": "cc-router",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "deepseek-v4-pro[1m]",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4-flash",
    "CLAUDE_CODE_SUBAGENT_MODEL": "deepseek-v4-flash"
  },
  "mcpServers": {
    "chrome-devtools": {
      "command": "chrome-devtools-mcp",
      "args": ["--isolated"]
    },
    "context7": {
      "command": "context7-mcp"
    },
    "playwright": {
      "command": "playwright-mcp"
    }
  },
  "permissions": {
    "defaultMode": "bypassPermissions"
  },
  "skipDangerousModePermissionPrompt": true,
  "statusLine": {
    "command": "node C:/Users/Administrator/.claude/statusline-wrapper.mjs",
    "type": "command"
  },
  "theme": "dark"
}
```

## 内置供应商（23 个）

### 国产官方
DeepSeek · 阿里百炼 · 百炼 Coding 专线 · 智谱 GLM · 智谱 GLM 国际 · 百度千帆 · Kimi · Kimi Coding 专线 · MiniMax · MiniMax 国际 · 阶跃星辰 · 阶跃星辰国际 · 火山引擎 Agentplan · 豆包 Seed · Longcat · 小米 MiMo · 蚂蚁百灵

### 聚合网关
OpenRouter · 硅基流动 · 硅基流动国际 · ModelScope 魔搭 · Novita AI

### 官方
Anthropic

## 管理端点

| 端点 | 说明 |
|------|------|
| `GET /status` | Web 管理面板（总览 / 路由日志 / 供应商 / 配置编辑 / API 检查 / 参数白名单） |
| `GET /health` | JSON 健康检查 |
| `GET /api/stats` | 路由统计 JSON |
| `GET /api/config` | 读写 config.yaml |
| `POST /api/config/provider` | 结构化更新后端配置（Web UI 弹窗用） |
| `GET /api/presets` | 供应商预设列表 |
