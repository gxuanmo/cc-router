# cc-router

本地 HTTP 代理，放在 Claude Code 和 LLM 后端之间，根据请求内容自动路由。

## 项目约定

- **语言**：Python 3.10+，无外部数据库依赖
- **入口**：`python router.py`，监听 `127.0.0.1:8082`
- **Web UI**：`http://127.0.0.1:8082/status`（SPA，由 `index.html` + JS 组件渲染）
- **配置**：`config.yaml`，支持热重载（改完自动生效，无需重启）
- **架构**：`config.py`（配置+供应商）+ `core.py`（检测+转发+过滤）+ `router.py`（Starlette 应用 + API）

## 文件职责

| 文件 | 职责 |
|------|------|
| `router.py` | 入口 + Starlette 路由 + API 端点 + 静态文件服务 |
| `core.py` | 图片检测、参数过滤、请求转发、SSE 流式 |
| `config.py` | YAML 加载、热重载、23 个供应商预设和参数白名单 |
| `config.yaml` | 用户配置（填 API Key 即可用） |
| `index.html` | Web UI HTML shell（挂载点 + CSS + 回退脚本） |
| `main.js` | 前端入口 — 状态管理、API 轮询、视图渲染 |
| `components.js` | 组件基类（Component）+ 注册系统（componentRegistry） |
| `components/*.js` | 12 个 UI 组件（App、Overview 等） |

## 新增供应商

在 `config.py` 的 `PARAM_WHITELISTS` 加参数白名单，`PROVIDER_PRESETS` 加预设。认证头在 `core.py` 的 `build_headers()` 里按 provider 类型分发。

## 验证

```bash
python router.py                          # 启动
curl http://127.0.0.1:8082/health         # 健康检查
curl http://127.0.0.1:8082/api/stats      # 路由统计
curl http://127.0.0.1:8082/api/presets    # 供应商列表
PYTHONPATH=. pytest tests/ -v             # 单元测试（33 个用例）
```

## E2E 冒烟测试

```bash
# 1. 启动服务
python router.py &

# 2. 等待就绪
until curl -s http://127.0.0.1:8082/health; do sleep 0.5; done

# 3. Playwright 交互验证
python -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    page = p.chromium.launch(headless=True).new_page()
    page.goto('http://127.0.0.1:8082/status', wait_until='networkidle')
    page.wait_for_timeout(1500)
    # 6 视图切换
    for v in ['logs','providers','config','api','whitelist','overview']:
        page.locator(f'button.nav[data-view=\"{v}\"]').click()
        assert page.locator(f'#{v}').evaluate('el => el.classList.contains(\"active\")'), f'{v} not active'
    # 刷新按钮
    page.locator('#overview button:has-text(\"刷新\")').first.click()
    page.wait_for_timeout(1000)
    assert '路由' in page.locator('#hero-status').text_content()
    print('E2E PASSED')
"
```

```yaml /checkup
test_command: "PYTHONPATH=. pytest tests/ -v"
smoke_test_entry: "python router.py"
e2e_command: "python -c \"<上面的 Playwright 脚本>\""
```
