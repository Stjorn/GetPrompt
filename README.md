# GetPrompt

面向本地与自托管 AI Agent 的 LLM 请求采集、协议解剖与用户输入分析工具集。

它通过覆盖 Agent 的模型 API Base URL，将请求先转发到本机反向代理，在不修改 Agent 源码的情况下保存其发往 LLM 的完整 JSON 请求体，再把响应流式转发回 Agent。

当前支持分析三类常见协议：

- Anthropic Messages
- OpenAI Chat Completions
- OpenAI Responses

已在 Claude Code、OpenCode、Codex、DeepSeek Harness 和 Trae 上验证。不同版本的 Agent 可能改变配置方式或请求结构，欢迎提交兼容性反馈。

> [!WARNING]
> 捕获内容可能包含源代码、文件路径、系统提示词、用户输入、工具执行结果和其他敏感信息。请只采集你有权测试的流量，不要提交或公开 `captures/`、`.env`、凭据、Cookie 或真实业务数据。

## 工作原理

```text
正常链路
Agent ────HTTPS──────────> LLM API

采集链路
Agent ──HTTPS──> 127.0.0.1:8099 ──> LLM API
                  │
                  ├── 原始请求体
                  ├── 脱敏后的请求元数据
                  └── user 角色文本视图
```

相比 TLS 中间人代理，这种方式不需要安装 CA 证书。前提是目标 Agent 允许覆盖模型服务的 Base URL。

代理会保留请求方法、路径、查询参数、请求体和流式响应。鉴权头仍会转发给上游，但写入 `.meta.json` 前会对常见敏感头进行脱敏。

## 能力边界

适用：

- 本地 CLI、IDE Agent 或桌面 Agent
- 允许设置 Base URL 的 Agent
- 自托管或出口配置可控的 Agent 服务
- OpenAI/Anthropic 兼容网关

不适用：

- 最终 Prompt 在厂商服务器上拼装、且模型出口不可控的 SaaS Agent
- 只允许使用厂商内置模型、无法覆盖 API 地址的客户端
- 需要生产级高可用、TLS、鉴权或多租户隔离的场景

例如，某些 IDE 即使允许填写自有 API Key，也会先把请求发到厂商后端完成 Prompt 拼装。此时本机 `127.0.0.1` 反向代理不在最终模型链路上，需要把代理部署到公网 HTTPS 地址。

## 安装

要求：

- Python 3.12+
- 推荐使用 [uv](https://docs.astral.sh/uv/)

```bash
uv sync
```

项目只依赖 `aiohttp`。

## 快速开始

### 1. 启动代理

```bash
uv run python capture/capture_proxy.py <UPSTREAM_ROOT> [PORT] [FALLBACK_SESSION]
```

参数：

| 参数 | 说明 | 默认值 |
|---|---|---|
| `UPSTREAM_ROOT` | 实际模型服务根地址，不要以 `/` 结尾 | `https://api.anthropic.com` |
| `PORT` | 本地监听端口 | `8099` |
| `FALLBACK_SESSION` | 请求没有 session header 时使用的归档目录名 | `_unknown` |

代理只监听 `127.0.0.1`。启动成功后会显示：

```text
listening on http://127.0.0.1:8099 -> <UPSTREAM_ROOT>
```

### 2. 测试

仓库中的探针文件只保存 Base URL 或非敏感配置，不包含 API Key。

#### Claude Code

使用 [`capture/claude_probe_settings.json`](capture/claude_probe_settings.json)：

```powershell
# 终端 A
uv run python capture/capture_proxy.py https://api.anthropic.com

# 终端 B
claude --settings capture/claude_probe_settings.json -p "reply with just: ok"
```

Claude Code 的 settings 中 `env` 可能覆盖当前 shell 环境，因此建议显式传入 `--settings`。

#### OpenCode

[`capture/openai_probe_settings.json`](capture/openai_probe_settings.json) 展示了通过 `OPENCODE_CONFIG` 覆盖 provider Base URL 的方式。使用前请将文件中的 provider ID 改成你自己的 provider ID。

完全退出已有 OpenCode 进程后再从带环境变量的终端启动：

```powershell
# 终端 A：替换成你的 OpenAI-compatible 上游根地址
uv run python capture/capture_proxy.py https://api.example.com

# 终端 B
$env:OPENCODE_CONFIG = (Resolve-Path "capture/openai_probe_settings.json")
# 然后从当前终端启动 OpenCode CLI 或桌面程序
```

#### Codex

[`capture/codex_probe_home/config.toml`](capture/codex_probe_home/config.toml) 是隔离的 `CODEX_HOME` 示例。密钥通过 `PROBE_KEY` 环境变量提供：

```powershell
# 终端 A：替换成 config.toml 中 provider 对应的上游根地址
uv run python capture/capture_proxy.py https://api.example.com

# 终端 B
$env:CODEX_HOME = (Resolve-Path "capture/codex_probe_home")
$env:PROBE_KEY = "<your-api-key>"
codex exec --skip-git-repo-check "reply with just: ok"
```

不要把日常使用的真实 Codex Home 当作探针目录。

#### DeepSeek Harness（GUI）

完全退出正在运行的 DeepSeek Harness，然后分别在两个终端执行：

```powershell
# 终端 A
uv run python capture/capture_proxy.py https://api.deepseek.com/anthropic

# 终端 B：从同一个终端启动 GUI，使其继承临时 Base URL
$env:DEEPSEEK_BASE_URL = "http://127.0.0.1:8099"
& "<path-to-DeepSeek Harness.exe>"
```

在 GUI 中新建会话并发送测试问题。这个环境变量只对终端 B 启动的进程及其子进程生效，正常启动的 DeepSeek Harness 配置不会被改写。

[`capture/deepseek_harness_probe.yml`](capture/deepseek_harness_probe.yml) 是可选的 CLI overlay；使用 `dsh --patch` 测试时可加载它，GUI 实测流程不依赖该文件。

#### Trae（GUI）

Trae 的自定义模型选择 OpenAI Chat Completions 格式，并把基础地址设为本地代理。Trae 的模型请求未观察到稳定 session header，建议为每次测试提供 `FALLBACK_SESSION`：

```powershell
uv run python capture/capture_proxy.py https://api.deepseek.com 8099 trae_case_01
```

Trae 中的自定义模型示例：

```text
API 格式: OpenAI Chat Completions
完整 URL: 关闭
请求地址: http://127.0.0.1:8099
模型 ID: 你的上游实际模型 ID
```

![image-20261026175401347](C:\Users\邵天武\AppData\Roaming\Typora\typora-user-images\image-20261026175401347.png)

保存自定义模型后，在 Trae GUI 中选择它，新建会话并发送测试问题。

代理终端出现以下日志即表示请求已经落盘：

```text
[captured] #1 POST /v1/messages -> claude_<session-id>\123456_001_v1_messages
```

## 输出目录

```text
captures/
├── proxy/
│   ├── claude_<session-id>/
│   ├── codex_<session-id>/
│   ├── deepseek_<session-id>/
│   ├── opencode_<session-id>/
│   ├── <fallback-session>/
│   └── _unknown/
└── jev/
```

每个请求最多生成三个文件：

```text
123456_001_v1_messages             # 原始请求体
123456_001_v1_messages.meta.json   # 路径、上游、脱敏请求头等元数据
123456_001_v1_messages.user.json   # user 消息及文本块视图
```

已识别的 session header：

- `x-claude-code-session-id`
- `x-deepseek-harness-session-id`
- `x-opencode-session-id`
- `session-id` / `thread-id`

真实 header 优先于 `FALLBACK_SESSION`。

## 分析抓包

### 结构概览

```bash
uv run python analysis/inspect_capture.py captures/proxy/<session>/<request>
```

输出请求级字段、system/developer 上下文、工具清单和消息块结构。

### 关键文本

```bash
uv run python analysis/extract_key.py captures/proxy/<session>/<request>
```

短文本完整显示；长文本显示标题结构、开头和结尾。

### Jev 分类试点

`analysis/jev_pilot.py` 将 user 文本块分为：

- `user_input`
- `template_context`
- `tool_output`
- `other_noise`

实际调用需要在本地 `.env` 中提供：

```dotenv
OPENROUTER_API_KEY=<your-key>
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
JEV_MODEL=typesafe/jev-1.13
```

然后运行：

```bash
uv run python analysis/jev_pilot.py --latest
```

Jev 使用 OpenRouter 的 `/api/alpha/decisions` 端点，而不是 Chat Completions。

## 协议差异

| 维度 | Anthropic Messages | OpenAI Chat Completions | OpenAI Responses |
|---|---|---|---|
| 对话字段 | `messages[]` | `messages[]` | `input[]` |
| 系统上下文 | 顶层 `system` | `system` 消息 | `developer` 消息或 `instructions` |
| 文本块 | `text` | 字符串或 `text` | `input_text` / `output_text` |
| 工具定义 | `tools[].input_schema` | `tools[].function` | 平铺工具定义 |
| 常见工具结果 | `tool_result` | `tool` 消息 | `function_call_output` |

几个重要结论：

1. `role=user` 不等于用户亲手输入。Agent 可能把规则、环境信息、附件说明和工具结果放进 user 消息。
2. 工具循环中的最后一条 user 消息可能只有工具结果。
3. 一次用户操作可能触发标题生成、压缩、重试等多个模型请求。
4. 同一种协议在不同 Agent 中仍可能使用不同的 Prompt 模板与工具结构。

## 安全说明

- 代理只绑定本机回环地址，不提供 TLS 或访问控制。
- `.meta.json` 仅对已知鉴权头做尽力脱敏；未知供应商可能使用其他敏感 header。
- 原始请求体不会脱敏。
- 不要把 `captures/`、`.env`、SQLite、日志或探针运行目录提交到版本库。
- 不要在不可信网络上暴露本代理。
- 请遵守目标 Agent、模型供应商及数据所有者的授权和使用条款。

## 项目结构

```text
.
├── capture/
│   ├── capture_proxy.py
│   ├── claude_probe_settings.json
│   ├── openai_probe_settings.json
│   ├── deepseek_harness_probe.yml
│   └── codex_probe_home/config.toml
├── analysis/
│   ├── inspect_capture.py
│   ├── extract_key.py
│   └── jev_pilot.py
├── pyproject.toml
└── uv.lock
```

## 开发状态

这是研究与实验工具，不是生产网关。当前重点包括：

- 扩充多 Agent、多版本样本矩阵
- 对 user 文本块进行结构规则与分类模型组合判别
- 增加请求分类、重复请求识别和更完整的敏感头策略
- 为协议解析与代理转发补充自动化测试

欢迎提交 Issue 或 Pull Request，并附上经过脱敏的结构描述；不要上传真实抓包。
