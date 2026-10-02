# WARP MASQUE 离线配置生成器

把已有 Usque / WARP 账号转换成 `outputs/` 目录里的客户端配置。默认使用原生 MASQUE；不需要网页、Worker、服务器部署，也不会自动注册新账号。

本次整合以 `warp-masque-actions` 为主，吸收 `usque-custom-pro` 的可配置入口、DNS / MTU、AI / IP 分流和多格式导出思路，集中为一个 Python 生成核心。

## 直接生成到目录

需要 Python 3.10+。已有 `usque-config.json` 时：

```sh
python -m pip install -r requirements.txt
python scripts/generate.py --account /安全目录/usque-config.json --settings examples/generator.json --output outputs
```

默认生成：

- `outputs/warp-masque.yaml`：完整 Mihomo 配置，内嵌 MASQUE 节点和策略组
- `outputs/warp-masque-provider.yaml`：只有 `proxies` 的节点集合，供已有 Mihomo 配置引用
- `outputs/warp-masque-shadowrocket.txt`：沿用原项目的 `masque://` 链接格式
- `outputs/manifest.json`：文件清单、节点数和验证范围，不含账号信息

默认 8 个候选地址 × 7 个端口，共 56 个节点；去掉原来重复地址的官方域名别名。它们复用同一个账号，**不代表 56 个账号、国家或不同出口**。本生成器不会探测入口，也不保证服务解锁或当前可用性。

输出含私钥，默认请保存在私有位置。生成器不复制原始账号文件、设备 ID 或 access token，CLI 本身不提交 Git；下面的手动 Actions 发布选项会在逐次确认后提交生成文件。POSIX 系统上输出目录为 `0700`，文件为 `0600`；Windows 请另外检查访问权限。

## 同时输出非 MASQUE 节点配置

使用 `examples/external-providers.json`，可同时输出含 MASQUE + 第三方节点 provider 的 `warp-masque.yaml` 和独立的 `external-direct.yaml`。后者不依赖 WARP 账号，也不依赖本地 Usque 桥接。

```sh
python scripts/generate.py --account /安全目录/usque-config.json --settings examples/external-providers.json --output outputs
# 只生成独立的第三方节点配置，无需账号：
python scripts/generate.py --external-only --settings examples/external-providers.json --output outputs
```

这部分复用 donor 的 US/JP/SG 等国家公开订阅能力。生成时不下载实际节点，客户端运行时才拉取；不保证来源中的每个节点可用或服务解锁。动态 provider 显式排除 MASQUE，独立配置强制直接连接第三方节点；目标是支持这些 provider 字段的 Mihomo 内核。详见 [第三方节点说明](docs/external-providers.md)。

## 修改参数

编辑不含账号密钥的 `examples/generator.json`，然后重新运行同一条命令：

- `endpoint_source`：`curated` 使用原项目候选地址；`account` 使用账号返回的地址
- `endpoints`：可额外设置为自定义 IPv4 / IPv6 / 主机名数组，覆盖地址来源；端口放在 `ports`
- `family`：`dual`、`ipv4` 或 `ipv6`；主机名通过节点 `ip-version` 限定解析
- `ports`、`sni`、`dns`、`mtu`：严格校验，不默默修正非法端口或地址
- `chatgpt_route` / `other_ai_route`：`AI`、`WARP`、`PROXY`、`DIRECT` 或 `REJECT`，直接控制实际分流规则
- `custom_ip_rules`：自定义 IPv4 / IPv6 CIDR，优先于 AI 和远程规则集
- `ai_health_url`：可选 AI 测试组 URL；连通测试不等于登录、地区或服务可用性检测
- `ruleset_profile`：`acl4ssr` 沿用主项目规则源；`minimal` 无远程规则集依赖

完整示例、优先级、H2 及多格式边界见 [使用与迁移说明](docs/offline-generator.md)。

## GitHub Actions：下载或手动提交 outputs

`生成 WARP MASQUE 配置` 仅由你手动点 **Run workflow** 触发，没有定时任务。默认 `output_destination=artifact`，只提供保留 1 天的下载；选择 `repository` 才会生成并提交到本次选中分支的 `outputs/`。

**公开风险：真实 MASQUE 完整配置、provider 和 Shadowrocket 链接含同一个账号的私钥。提交到公开仓库后，任何人都可能复制和使用；Git 历史、fork、下载和缓存不能保证清除。删除文件或改回 artifact 不会撤销已公开的密钥。** 不接受这点时，请使用私有仓库的 artifact 模式。

1. 先确认运行的分支已包含本版工作流。此改动在 PR 中时，默认 `main` 仍可能是旧注册流程，不能把旧按钮当作新流程；合并与运行是两次独立操作
2. 真实账号需由你自己在 GitHub 配置已有账号的 Actions Secret `WARP_ACCOUNT_JSON`，以及 Actions Variable `WARP_GENERATION_ENABLED=true`。不创建新账号，不把 JSON 发到聊天、Issue 或源码中
3. 打开 Actions → `生成 WARP MASQUE 配置` → Run workflow，选择含新版代码的目标分支
4. `mode=account` 复用已有账号；`sample` 的 WARP 部分是不可连接样例；`external-only` 只生成外部 provider，不需要账号或 Secret
5. `include_external_nodes` 默认勾选：account/sample 同时输出 MASQUE + 外部节点组合配置和独立 `external-direct.yaml`。取消后为纯 WARP；external-only 始终只输出独立配置
6. 想持久保存时选择 `output_destination=repository`，并自行勾选 `confirm_publish_outputs`：确认将可能含私钥的配置提交到本仓库。这一项默认不勾选，sample 和 external-only 发布也要确认
7. 由你自己点击 Run workflow。成功后，`publish` 任务会将白名单文件提交至所选分支；内容相同不产生新提交。随后在仓库 `outputs/` 打开所需文件，点 Raw 复制实际链接

不需要你手动上传生成文件。公开账号模式必须选择 repository 并逐次确认；`account + artifact` 仍要求私有仓库和 `confirm_private_artifact`。Secret 缺失、门禁未通过或账号格式错误都会停止，不会注册替代账号。每次都需新的 Run workflow 请求，**Re-run jobs / Re-run failed jobs 被拒绝**；失败后检查原因再新开一次运行。

默认 account + 外部节点开关生成并提交：

- `outputs/warp-masque.yaml`：完整 MASQUE + 外部 provider 配置
- `outputs/warp-masque-provider.yaml`：MASQUE 节点 provider
- `outputs/warp-masque-shadowrocket.txt`：MASQUE 链接
- `outputs/external-direct.yaml`：独立非 MASQUE 第三方 provider 配置
- `outputs/manifest.json`：生成文件清单

external-only 只保留最后两项。配置可选的本地桥接输出见 [详细说明](docs/offline-generator.md)，手动工作流默认不生成。发布只保留本次生成的白名单文件，会移除以前已跟踪、这次不再生成的白名单文件；所以 sample 会替换所选分支的真实配置，external-only 会移除当前 MASQUE 文件，**旧版本仍在 Git 历史中**。本地 CLI 不做这个清理。

发布时使用最新远端分支为父提交，保留其他路径的变更，拒绝符号链接和非白名单文件；发生并发更新/分支保护拒绝就停止，不强推、不自动绕过。只有 publish 任务有短期 `contents: write` 权限；GitHub 的这一权限不能限制到目录，目录限制由发布器白名单实施。生成任务只读，原始账号 JSON、token、环境、日志、缓存都不纳入发布。

生成结果仍先经 1 天 artifact 传给发布任务，仅含同一组校验后的文件。公开发布失败时请留意这份临时下载也可能含私钥；不要因 push 失败就认为配置没有离开运行器。本次仅提供流程代码，不会自动配置 Secret、更改可见性/仓库权限或替你触发真实账号运行。更多排错见 [发布说明](docs/manual-publication.md)。

## 先验证，不写文件

```sh
python scripts/generate.py --account /安全目录/usque-config.json --check
python -m unittest discover -s tests -v
```

测试仅使用非功能性合成数据，不注册、不登录真实账号、不启动代理。`--check` 检查参数和导出结构，不验证 ECDSA 密钥真实性或网络连通性。

旧命令仍可使用：

```sh
python scripts/gen_masque.py /安全目录/usque-config.json outputs
```

已有 `build(cfg)` 的 `(links, yaml, count)` 返回结构保留。旧工作流的“每跑一次注册一个账号”行为已移除；首次注册请单独参考 [Usque 官方文档](https://github.com/Diniboy1123/usque)，以后一直复用保存的账号文件。

## 故障检查与确认后恢复

新增独立的 `healthcheck.py` 和 `recover_account.py`：多入口重试，区分健康、网络、临时故障、认证拒绝和未知状态；超时不会当成账号过期。重新注册必须逐次明确批准并接受服务条款，新账号在验证成功前不会替换旧文件，保留私有回滚备份。默认生成和 Actions 不会调用恢复器。见 [健康检查与手动恢复](docs/health-and-recovery.md)。

## 客户端与范围

- Mihomo：需要支持 `type: masque` 的内核。不要只按“稳定版 / Alpha”标签判断；参考 [官方 MASQUE 文档](https://wiki.metacubex.one/config/proxies/masque/)，并用安装的内核 `mihomo -t` 实测解析
- Shadowrocket：保留原项目 URI 方言；该链接不承载完整分流、SNI、MTU 等配置，不保证所有客户端版本兼容
- sing-box / VLESS：可选输出仅连接**同机已运行的 Usque SOCKS**。不能把它们当作远程 VLESS 节点或原生 MASQUE 转换
- 第三方节点 provider 明确 opt-in；不宣称国家标签等于已验证出口，不添加网页或 Worker 部署

仓库原有 Opera / Proton / Windscribe 工作流与 `worker/` 代码未改动，它们不属于这个默认生成流程；部分历史工作流会注册账号并提交含凭据配置，**不要为了本流程运行它们**。历史 `configs/` 文件不读取、不删除；本次整合也不说明历史文件是否安全。

## 整合记录

- 主项目基线：[`a18457d1`](https://github.com/frederick1986/warp-masque-actions/commit/a18457d1a77e57553fcf96eb5a967c979590048f)
- 能力参考：[`usque-custom-pro@8719fa08`](https://github.com/frederick1986/usque-custom-pro/commit/8719fa082b1ddd16e1465cc4718db8e1f926a514)
- 修复借鉴方案中的 CIDR 只解析未写入、AI 路由选项未作用于最终规则的问题
- 未复制捐赠项目网页、部署配置或任何实际账号配置；恢复器仅在明确批准后调用用户提供的官方 Usque 工具
