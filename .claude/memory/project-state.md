---
name: cc-router-project-state
description: cc-router 项目当前状态 — Starlette 1.0+ lifespan 迁移、安全加固、33 测试全通过
metadata:
  type: project
---

项目于 2026-06-10 新增根路径重定向、YAML 异常捕获加固。2026-06-07 完成 Starlette 1.0+ lifespan 迁移、安全加固（信息泄漏修复）。2026-06-01 完成 config save API、Claude Code settings 模板导出、Web UI console 打磨。2026-05-30 完成 Web UI 主题改造、MiMo 路由修复、5 code-review bug 修复。

## 当前状态

- 后端：Python/Starlette，router.py 监听 127.0.0.1:8082，已迁移到 Starlette 1.0+ lifespan 模式
- 前端：ES6 组件化 SPA，三主题（dark/light/midnight），Material Symbols 图标
- API：POST /api/config（完整 YAML 写入+引号归一化）、POST /api/config/provider（结构化 provider 配置）
- UI：Overview 快捷操作（复制 settings.json、测试 API、打开配置）、polished console 空状态
- 测试：33/33 通过（PYTHONPATH=. pytest tests/ -v）
- 最新提交 4c2983f @ master

## 2026-06-10 变更

- **根路径重定向**：新增 `GET /` → 307 到 `/status`，解决访问根路径 404 问题
- **YAML 异常捕获**：`config.py` `yaml.safe_load()` 新增 `yaml.YAMLError` 捕获，防止损坏的 YAML 文件导致裸奔异常
- **.gitignore 补充**：新增 `node_modules/`、`package-lock.json`、`package.json` 排除

## 2026-06-07 变更

- **Starlette 1.0+ lifespan 迁移**：`add_event_handler("shutdown")` → `lifespan` async context manager，新增 `test_create_app_lifespan_provides_and_closes_http_client` 和 `test_main_reaches_uvicorn_run_without_legacy_event_handler` 测试
- **安全加固**：5 处 `str(exc)` 信息泄漏 → 泛化错误消息 + 日志记录（api_config_get、api_config_post、api_config_provider_post ×2、YAML 解析）、流式错误路径新增 `resp.aclose()` 防连接池泄漏、`load_status_page()` 新增 try-catch
- **依赖修复**：删除不存在的 `httpx2` 包
- **文档同步**：CLAUDE.md/AGENTS.md 测试数量 30→33

## 2026-06-01 变更

- **config save API**：新增 `POST /api/config`，含 `normalize_config_quotes()` YAML 引号归一化，BackendConfig/ServerConfig/LoggingConfig 验证
- **settings 模板导出**：前端 `copyClaudeSettings()` + `CLAUDE_SETTINGS_TEMPLATE`，`.claude/settings.cc-router.example.json` 完整模板
- **console polish**：empty-state 组件、backend-item 状态 chip、hero-kicker、quick-action-list
- **config.example.yaml**：安全的可提交配置模板，`config.yaml` 从 git 移除（已 gitignore）
- **安全修复**：502 错误信息泛化（不泄漏后端连接细节）
- **代码质量**：`uniqueModels()` O(n²)→O(n) Set、删除重复 `import re`、`renderConfigSummary` 复用 `parseConfigSection()`、合并重复 CSS 块
- **文档同步**：CLAUDE.md/AGENTS.md 测试数量 23→30、TRD.md §5.3 流式代码示例与实际实现同步
- **artifact 清理**：删除 theme/v/screenshot PNG、ui-image/、畸形 JSON 测试文件

## 2026-05-30 修复

- **latin-1 header bug**：`backend.name`（中文）写入 HTTP 头导致 502。改 `backend.provider`
- **MiMo 模型名**：`mimo-v2.5[1M]` → `mimo-v2.5`，新增 `mimo-v2.5-pro`
- **CLAUDE_CODE_SIMPLE**：修复 MiMo 400（system role 兼容）
- **YAML 拼接 bug**：`saveProviderConfig()` 正则拼 YAML → 新 API 端点 `/api/config/provider` + yaml 库安全写入
- **start.bat 退化**：硬编码路径 → 还原 Python 检查 / pip install / cd 逻辑
- **5 code-review bug fixes**：null guard、body type check、YAML comment 保留、regex section 边界、dict 类型校验

## 已知技术债

- 10/12 组件 template() 未调用
- toast() 重复三处
- /api/presets 无意义轮询
- load_status_page() 无缓存
- stats["errors"] 多计 502 重试
- `normalize_config_quotes` 和 `validate_config_mapping` 在 router.py（应迁至 config.py）
- `CLAUDE_SETTINGS_TEMPLATE` 三处硬编码（main.js + settings.cc-router.example.json + README），应统一从后端提供
- 前端 YAML 解析（`cleanYamlScalar`/`parseConfigSection`）应改为结构化 API
