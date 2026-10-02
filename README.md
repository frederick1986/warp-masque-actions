# WARP MASQUE 离线配置生成器

把已有 Usque / WARP 账号转换成 `outputs/` 目录里的客户端配置。默认使用原生 MASQUE；不需要网页、Worker、服务器部署，也不会自动注册新账号。

当前手动 Actions 保存三份可独立导入的完整 YAML：`masque.yaml`、`usque-custom-pro.yaml`、`combined.yaml`。后两份都通过 WARP 中转第三方节点，不在此仓库发布纯第三方直连聚合文件。

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

## 三份配置：纯 WARP、原项目中转、合并选择

手动 Actions 使用新的 `scripts/generate_bundle.py`，默认下载 donor `usque-custom-pro` 所用的公开国家源（Au1rxx/free-vpn-subscriptions，US/JP/SG），过滤、去重，将实际节点写进配置：

- `outputs/masque.yaml`：只含 WARP MASQUE 节点，保留完整选择组和规则
- `outputs/usque-custom-pro.yaml`：按原项目中转用途，以第三方链为主出口；包含必要 WARP 节点，每个第三方节点通过 `dialer-proxy: WARP中转` 拨号，形成“设备 → WARP → 第三方节点 → 网站”
- `outputs/combined.yaml`：同时提供纯 WARP 出口与 WARP 中转第三方出口，主选择组可切换两类出口
- `outputs/manifest.json`：辅助清单、节点数、来源与过滤计数，不包含原始账号或节点凭据

三份 YAML 各有自己的节点、组和规则，单独导入即可，不依赖彼此；客户端不再运行时下载第三方节点订阅，ACL4SSR 规则集仍按原设置下载。中转由你本机的 Mihomo 执行，Actions 只下载公开源、生成和保存配置，不托管中转服务。后两份也需要支持 MASQUE 的 Mihomo。

专用 `WARP中转` 组只选择纯 WARP 节点，避免指回外部节点形成循环。原有明确直连规则、国内/局域网和直连优先服务保持语义；保证的是第三方节点的拨号链，不代表所有网站都强制经过代理。纯第三方聚合用途应留在独立的 FreeNodes 项目，本次不修改那个项目。

任一所选来源下载失败、超限、YAML 非法或过滤后没有可用结构的节点都会停止，不提交半份或占位结果。结构可解析不等于在线或服务解锁；再次手动运行才会刷新内嵌快照。第三方节点可能随时失效，WARP 链也不会让第三方运营者变得可信。

不需要第三方节点时取消 Actions 的 `include_external_nodes`，仅保存 masque.yaml 和 manifest。原来的离线 `generate.py` / `gen_masque.py` 接口仍保留兼容，不自动联网；旧 provider 引用等格式见 [兼容接口与第三方来源](docs/external-providers.md)。新发布流程只接受这里的三份 YAML 与 manifest，不接受纯 external 导出。

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

`生成 WARP MASQUE 配置` 仅由你手动点 **Run workflow** 触发，没有定时任务。默认 `output_destination=artifact`，提供保留 1 天的下载；选择 `repository` 才提交到本次选中分支的 `outputs/`。

**公开风险：三份真实配置都含 WARP 私钥。公开仓库中的任何人都可能复制和使用；Git 历史、fork、下载和缓存无法保证清除。删除文件或改回 artifact 不会撤销已公开的密钥。** 不接受这点时，使用私有仓库的 artifact 模式。

1. 先确认运行的分支已包含本版工作流。PR 未合并时 `main` 仍可能是旧注册流程，不能把旧按钮当作新流程；合并与运行是两次独立操作
2. 由你自己在 GitHub 设置已有账号的 Actions Secret `WARP_ACCOUNT_JSON`，以及 Actions Variable `WARP_GENERATION_ENABLED=true`；不要把 JSON 发到聊天、Issue 或源码中
3. 打开 Actions → `生成 WARP MASQUE 配置` → Run workflow，选含新版代码的目标分支与 `mode=account`；`sample` 只用于不可连接的 WARP 样例
4. `include_external_nodes` 默认勾选，生成上述三套完整配置；取消只生成纯 WARP
5. 要持久保存则选 `output_destination=repository`，并自行勾选默认关闭的 `confirm_publish_outputs`，确认将可能含私钥的配置提交到本仓库。sample 发布也要确认
6. 由你自己点击 Run workflow。成功后 publish 任务提交三份 YAML 与 manifest，内容相同不产生新提交；在所选分支的 outputs/ 打开文件，点 Raw 复制实际链接

不需要手动上传生成文件。公开账号模式必须选 repository 并逐次确认；account + artifact 仍需私有仓库和 `confirm_private_artifact`。Secret 缺失、门禁失败或账号无效会停止，不注册替代账号。每次必须新开 Run workflow；**Re-run jobs / Re-run failed jobs 被拒绝**，应检查原因后重新选择和确认。

发布只保留本次导出，移除这次不再生成的已知旧产物，包括旧 external/provider/link/bridge 文件。sample 会替换当前真实配置，取消第三方选项会移除当前两份中转配置；**旧版本及私钥仍在 Git 历史中**。本地 CLI 不做清理。

发布先取远端最新分支为父提交，只改受限的 outputs 路径；并发更新或分支保护拒绝就停止，不强推。仅 publish 任务请求短期 `contents: write`，GitHub 不能把此权限细分到目录，目录限制由白名单实现。生成任务只读，原始账号 JSON、token、环境、日志和缓存不发布。

结果经 1 天 artifact 传给发布任务；即使公开 push 失败，这份临时下载仍可能含私钥，不能认为配置没有离开运行器。本次仅提供代码，不自动配置 Secret、更改可见性/仓库权限或替你触发真实账号运行。详见 [手动发布说明](docs/manual-publication.md)。

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
