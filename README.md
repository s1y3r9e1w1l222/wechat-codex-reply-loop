# WeChat Codex Reply Loop

[English](README.en.md)

基于 Codex 与 [iPhone Use](https://github.com/zhongerxin/iPhone-use) 的 iPhone 微信循环回复服务，运行于 macOS。围绕未读检测、上下文生成、发送核对与列表等待，持续执行回复循环。

自动发送模式下的循环流程：**检测未读 → 按队列生成回复 → 核对并发送 → 返回消息列表 → 下一轮检测**。

**当前为实验版配套服务，尚未提供可直接安装的 Codex 插件。** 默认启用预览模式，联系人允许名单为空。已完成文件传输助手的单次真机发送与结果核对；普通联系人身份校准及双会话完整流程尚未验收。目前适用于开发与验证。

## 回复规则

- **处理范围**：仅处理 `allowed` 名单中已完成身份校准的文字单聊。群聊、服务通知、非文字消息及身份不明的会话均由人工处理。
- **回复顺序**：在本轮扫描范围内，按消息列表位置自下而上处理未读会话，最新的最后回复。已入队任务保持原有顺序，不随回复引起的列表重排变化；新输入追加至队尾。
- **免打扰过滤**：开启静音或消息免打扰的会话不进入回复队列；状态无法确认时同样跳过。
- **上下文更新**：生成前设置短暂等待，以便结合连续输入。检测到新消息后，旧版本回复失效，并依据最新上下文重新处理。
- **并发调度**：最多并行执行两个 AI 生成任务，手机操作始终串行。发送遵循队列顺序，不受生成完成顺序影响。
- **发送核对**：发送前复核联系人身份、最新输入、免打扰状态及人工草稿；确认发送后返回消息列表。结果不确定时暂停并提示人工核对，不自动补发。

## 运行条件与支持范围

真实操作需要 Mac、Python 3.9+、可用的 Codex CLI 登录，以及已完成安装、签名和连接的 iPhone Use。iPhone Use 的 Xcode、手机端服务、设备连接要求请按其上游文档完成。本仓库不包含手机执行服务或签名材料。

本次验证版本：iPhone Use **0.3.8**、Codex CLI **0.164.0**、中文微信、竖屏 **440 × 956 点**。其他版本、尺寸、语言尚未验证，布局不符时会停止。运行时微信需在前台且手机可交互；这不是 iOS 后台消息监听。实际检测耗时包含控件读取、翻页和 AI 生成，配置中的 2 秒是两轮扫描间隔，不保证 2 秒内回复。当前扫描最多覆盖配置的页数，不承诺整个历史列表均被覆盖。

多页列表需确认顶部并连续扫描两遍，观察到的顺序和标记一致才接受；中途重排或超时则暂停等待处理。这会增加扫描耗时。

## 先运行离线演示

克隆或下载本仓库后，在项目目录执行：

```sh
git clone https://github.com/s1y3r9e1w1l222/wechat-codex-reply-loop.git
cd wechat-codex-reply-loop
PYTHONPATH=src python3 -m wechat_autoreply demo
```

该演示不连接手机、不调用 Codex，验证从下往上、排除免打扰，以及 AI 乱序完成仍按队列发送。输出 `ok: true` 仅表示离线回放通过。

开发者也可以使用 `python3 -m pip install -e .` 安装 `wechat-reply` 命令；运行依赖只有 Python 标准库，安装构建工具可能需要联网。

## 本地配置与启动

```sh
cp config.example.json config.json
PYTHONPATH=src python3 -m wechat_autoreply --config config.json run
```

也可先执行 `chmod +x 启动微信助手.command`，再双击该文件启动。首次自动复制示例配置，仍是空名单、预览模式。按 Ctrl+C 停止。

`config.json`、校准、聊天状态都留在本地，已设置 Git 忽略。相对路径按配置文件所在目录解析。常用字段：

| 字段 | 含义 |
| --- | --- |
| `allowed` | 自动回复允许名单，填写已校准联系人的本地 ID；默认 `[]`，不处理任何联系人 |
| `plugin_path` | 可选，已安装 iPhone Use 0.3.8 的目录；不填则自动查找 |
| `calibration_path` | 本机页面和联系人身份校准，不能提交到仓库 |
| `state_dir` | 私有状态目录，权限必须为 700 |
| `persona_path` | 回复风格文件，参考 `persona.example.txt`；上限 4000 字、16 KiB |
| `model` | 可选的 Codex 模型名；留空由 CLI 选择，不保证与当前聊天相同 |
| `poll_seconds` | 扫描间隔，默认 2 秒，不代表端到端回复时延 |
| `quiet_seconds` | 生成前等待时长，默认 2 秒，用于结合连续输入 |
| `max_wait_seconds` | 当前任务的生成前等待上限，默认 8 秒，从任务入队起计时 |
| `ai_workers` | AI 生成并发数，默认 2；手机操作不并发 |
| `request_timeout` | 单次 AI 生成时限，默认 90 秒；超时记录失败，本次不发送 |
| `max_scan_pages` | 每遍消息列表扫描的页数上限，默认 3 页，每页约一屏会话 |

### 参数说明

- **生成前等待**：`quiet_seconds` 从当前任务最近一次更新起计时；达到 `max_wait_seconds` 后，不再仅因该等待条件推迟生成。新输入建立新版本任务时会重新计时。该上限不构成端到端回复时限，实际时延仍受页面读取、任务调度、AI 生成及发送影响。
- **生成时限**：`request_timeout` 从本次 AI 生成开始计时。生成提前完成后即可按队列继续处理；超时则终止本次生成并记录失败，不自动发送。
- **扫描范围**：每遍从消息列表顶部开始，最多扫描 `max_scan_pages` 页，相邻页保留重叠区域用于核对顺序。每轮需完成两遍一致性检查；范围外的会话不会自动轮换纳入后续扫描。提高页数上限可扩大覆盖范围，同时增加读取与核对耗时。

### 联系人允许名单

`allowed` 仅控制助手的自动处理范围，不影响用户在微信中的正常聊天。名单项为本机校准时配置的联系人 ID（如 `contact_a`），用于映射已核验的微信联系人，不等同于微信号或手机通讯录条目。

配置示例：

```json
{
  "allowed": ["contact_a"]
}
```

默认值 `[]` 表示不处理任何联系人。名单外的会话不会被打开以读取上下文、生成或发送回复，但列表扫描仍可能观察到其名称、预览及未读标记。加入名单后，仍须完成首次预览，并在启动时显式启用自动发送。当前版本不提供取消名单限制的配置开关。

### AI 生成

回复生成使用独立的临时 `codex exec` 会话及已有 CLI 登录，不需要另配 API 密钥，也不会直接继承当前 Codex 聊天记录。每次只传入对应微信会话的有界上下文，最多 20 条、8000 字符，回复最多 300 字。禁用生成进程的手机、MCP、插件、命令执行和搜索工具。生成仍消耗 Codex 账户额度；聊天上下文会发送至 Codex 服务，数据处理遵循该账户的服务规则。

## 校准与自动发送

手机停在微信消息列表时，可读取基础列表校准：

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json calibrate
```

命令不会自动验证普通联系人身份，已有校准文件也不会被覆盖。发送前必须按 [校准说明](docs/CALIBRATION.md) 在本机确认每个允许联系人的唯一身份、免打扰开关和返回路径。当前需要开发者结合 iPhone Use 完成这一步，尚无普通用户校准向导。不要只把 `verified` 改成 `true` 来绕过验证。

完成真实校准并配置允许名单后，本次运行可显式启用：

先使用普通 `run` 完成一次预览，在另一个终端查看 `status`，确认 `initial_preview_complete: true` 并检查当前任务／草稿，然后停止服务。首次启用、新增联系人或更改身份校准都需要先完成对应预览；尚未预览时 `--send` 会拒绝启动。

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json run --send
```

普通 `run` 始终预览；预览会进入聊天并可能把消息标成已读。预览稿通过本机 `status` 查看，不提供逐条批准发送的界面。不要在自动运行期间同时操作手机或用其他工具切换页面；共享锁只能协调使用同一 iPhone Use Runtime 的工具，无法锁住人的触屏操作。

## 暂停、恢复和人工处理

在另一个终端、同一项目目录执行（与运行进程使用同一配置）：

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json status
PYTHONPATH=src python3 -m wechat_autoreply --config config.json pause
PYTHONPATH=src python3 -m wechat_autoreply --config config.json resume
PYTHONPATH=src python3 -m wechat_autoreply --config config.json stop
```

暂停会立即取消继续发送的意图，已提交给手机的动作不能撤回。界面未知、发送不确定或返回列表失败时进入 `needs_attention`。先在手机上核对实际情况，再按需处理：

```sh
# 只更新记录，不点手机，也不补发；JOB_ID 替换为 status 中的编号
PYTHONPATH=src python3 -m wechat_autoreply --config config.json resolve JOB_ID --outcome sent
# 同样可选 handled 或 abandoned
PYTHONPATH=src python3 -m wechat_autoreply --config config.json rebaseline CONTACT_ID
PYTHONPATH=src python3 -m wechat_autoreply --config config.json resume
```

`retry JOB_ID` 仅面向生成失败或识别歧义的任务，会重新读取后排到队尾。`uncertain`、`sending`、`sent` 禁止重发。重启默认预览，需要重新核对任务。

重启先只读核对允许名单中尚未确认的发送。存在唯一新增己方气泡时更新记录；证据不足则保留 `uncertain` 并进入 `needs_attention`，不会再次点击发送。重新建立基线会结束旧版本任务，仅监控未来输入；重试若无法重建新输入边界会明确拒绝。

## 本地数据与开发

SQLite 保存聊天观察、草稿和发送检查点，控制接口是权限 600 的本地 Unix socket。原始截图和 iPhone Use 的日志由上游保存在它自己的数据目录，业务状态与上游目录分开。`cleanup` 清理超过 `retain_days`（默认 7 天）的历史观察和已结束任务；活跃任务、未解决异常和会话基线会保留，详见 [安全与数据说明](SECURITY.md)。

```sh
PYTHONPATH=src python3 -m wechat_autoreply --config config.json cleanup
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m compileall -q src
```

所有自动测试使用合成数据，无需手机、登录或密钥。贡献方式见 [CONTRIBUTING.md](CONTRIBUTING.md)，公开发布检查见 [docs/RELEASING.md](docs/RELEASING.md)，实际验证范围见 [docs/VALIDATION.md](docs/VALIDATION.md)。

独立评审及修复记录见 [docs/REVIEW.md](docs/REVIEW.md)。

本项目使用 [MIT 许可证](LICENSE)。iPhone Use 的版权和外部依赖见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。这是独立社区项目，与微信、腾讯、Apple 或 OpenAI 无隶属关系。
