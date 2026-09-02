# EdgeOps · 网关 AI 运维助手（AIOps_Edge）

为 **EdgeOS 网关**（ESP32-P4 + C6 边缘物联网网关，源码 `D:\develop\ESP32\P4\zigbeetomqtt`）打造的 AI 运维助手 Agent：

- **运维知识库**：从网关 DOCS（54 份文档）提炼的 11 个主题知识（构建烧录 / OTA / 故障排查 / API 对接 / 安全限制…），Markdown 分片 + 关键词检索，可扩展
- **运维交互系统**：ChatGPT 风格三栏 Web UI + FastAPI 后端；提问后 Agent 自动「规划 → 检索知识 → 查询网关实时状态 → 流式生成回答」

```
用户提问 ──▶ ① 诊断规划(LLM, JSON) ──▶ ② 材料收集(代码执行)
                                          ├─ 知识库检索（本地 Markdown）
                                          ├─ 网关实时查询（REST 只读白名单 28 项）
                                          └─ OTA manifest 检查
            ◀── ③ 综合回答（流式 Markdown，结论引用实时数据）
```

## 快速开始

```bat
:: 1. 准备配置（可跳过：也可启动后在页面左下角 ⚙ 对接配置 里填写）
copy config.example.json config.json
::    编辑 config.json：填 LLM api_key、网关地址 + 对接令牌（Web 后台→用户管理→生成 API Token）、OTA 服务器

:: 2. 启动（方式一：双击）
EdgeOps助手.bat
::    自动定位 Python（PATH → 常规安装目录），首次运行自动 pip 安装依赖，
::    之后托盘模式启动并自动打开浏览器。无需手动装依赖。

:: 2. 启动（方式二：命令行托盘模式）
python run_agent.py

:: 2. 启动（方式三：前台模式 / Linux）
python run_agent.py --no-tray
```

浏览器自动打开 `http://127.0.0.1:8766`。

> 双击启动黑窗闪退或报 Python 未找到：bat 为纯 ASCII 编写（兼容 GBK/UTF-8 控制台），
> 会依次探测 PATH 上的 python、`%LocalAppData%\Programs\Python\Python31x`；
> 若都没有，按提示安装 Python 并勾选 "Add python.exe to PATH"。

## CLI 用法

```bat
python -m edgeops test                 :: 测试 LLM/网关/OTA 连通性
python -m edgeops kb                   :: 列出知识库主题
python -m edgeops kb ota               :: 查看《OTA 升级》知识文档
python -m edgeops search 反复重启       :: 知识库关键词检索
python -m edgeops ask 网关反复重启怎么办  :: 命令行直接提问
python -m edgeops web --port 8766      :: 启动 Web 服务
```

## 配置说明（config.json）

| 段 | 字段 | 说明 |
|----|------|------|
| llm | base_url / api_key / model | OpenAI 兼容接口（DeepSeek/Qwen/GLM…均可） |
| llm | system_prompt | 自定义系统提示词（须保留 `{KB_INDEX}` 占位符） |
| gateway | url | 网关地址，如 `http://192.168.1.100` |
| gateway | token | **必填**。永久对接令牌：Web 后台 → 用户管理 → 生成 API Token（NVS 存储，admin 权限，无过期）。对接仅用 Token，不走账号密码 |
| ota | url / token | OTA 服务器（默认 `http://10.10.0.6:8080`） |
| agent | max_kb_topics / max_gateway_calls | 单轮检索上限，防材料过大 |
| agent | history_rounds | 上下文记忆轮数 |

环境变量等价：`EDGEOPS_LLM_BASE_URL / EDGEOPS_LLM_API_KEY / EDGEOPS_LLM_MODEL / EDGEOPS_GATEWAY_URL / EDGEOPS_GATEWAY_TOKEN / EDGEOPS_OTA_URL`。

## 网关实时查询白名单（只读）

助手可自动调用以下网关 API（均在固件 handler 中实际存在，`GET` 只读）：

`system/info` `system/health` `system/health/components` `system/resources` `system/tasks` `system/timers` `system/audit` · `devices` `drivers` `drivers/diag` · `alarms` `alarms/stats` `logs` `history` · `ota/status` `ota/check` `ota/storage` `ota/fonts` `ota/database` · `mqtt/stats` `mqtt/client/status` `mqtt/topics` · `zigbee/network` `zigbee/devices` · `rules` `scenes` `spaces` `notifications`

**安全设计**：Agent 只做只读查询；一切写操作（重启、OTA 升级、删设备）由助手给出步骤，用户在 Web 后台执行。

## 知识库维护

```
knowledge/
├── kb.json          # 索引：id / file / title / summary / keywords（检索依据）
└── 01~11-*.md       # 主题文档（运维手册式，自包含）
```

- 新增主题：加一份 md + kb.json 加一条（keywords 决定检索命中率）
- 修改即时生效（服务重启后重新加载）
- 知识内容与源文档冲突时，以网关 `DOCS/` 为准

## 目录结构

```
AIOps_Edge/
├── edgeops/          # Python 包
│   ├── agent.py      # Agent 编排（规划→材料→回答）
│   ├── gateway.py    # 网关 REST 客户端 + 查询白名单
│   ├── knowledge.py  # 知识库加载/检索
│   ├── llm.py        # OpenAI 兼容客户端（流式/JSON/重试/修复）
│   ├── prompts.py    # 系统提示词模板
│   ├── tools.py      # 材料收集（知识/网关/OTA manifest）
│   ├── webui.py      # FastAPI（会话/任务/轮询/配置）
│   └── cli.py        # CLI 子命令
├── knowledge/        # ★ 运维知识库（11 主题 + 索引）
├── web/index.html    # 单文件三栏前端
├── run_agent.py      # 托盘 + 服务启动入口
├── config.example.json / requirements.txt
└── sessions/         # 运行时生成的会话持久化（原子写）
```

## 设计说明（沿用 Auto485 助手的成熟模式）

- **两阶段协议**：规划阶段用 json_mode 输出材料清单（知识主题 + 网关查询 + OTA），代码校验白名单后执行——「提示约束软引导 + 代码校验硬兜底」
- **过程可见**：诊断材料卡片（哪些知识被检索、哪些 API 被调用、成败与摘要）+ 阶段日志 + 推理流实时展示 + 停止按钮
- **任务持久化**：会话原子落盘（sessions/*.session.json），服务重启不丢对话；中断任务标记失败
- **密钥星号回显**：纯星号提交视为未修改，明文只落 config.json
- **UI 版本指纹**：index.html md5 注入，前端更新自动提示刷新
