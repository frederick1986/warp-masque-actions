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

输出含私钥，请保存在私有位置。生成器不复制原始账号文件、设备 ID 或 access token，也不会把文件提交到 Git。POSIX 系统上输出目录为 `0700`，文件为 `0600`；Windows 请另外检查访问权限。

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

## GitHub Actions

`生成 WARP MASQUE 配置` 现在是**手动触发、复用账号**的工作流，没有定时任务。

1. `mode=sample`：公开或私有仓库都能测试，只使用仓库里的无效合成密钥，产物不能连接
2. `mode=account`：必须是**私有仓库**，已由仓库所有者设置 `WARP_GENERATION_ENABLED=true`，并由其自行安全配置已有账号 Secret `WARP_ACCOUNT_JSON`
3. 运行时还必须勾选确认：私有仓库成员可下载含私钥的产物
4. 配置写入运行器 `outputs/`，以 artifact 提供下载，保留 1 天；不会提交回仓库或上传原始账号 JSON

当前公开仓库不满足真实账号生成条件。改变可见性或配置 Secret 是独立的账号设置步骤，不是合并代码就会执行的操作。不要将真实账号放进源码、Issue、PR、日志或聊天。

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

## 客户端与范围

- Mihomo：需要支持 `type: masque` 的内核。不要只按“稳定版 / Alpha”标签判断；参考 [官方 MASQUE 文档](https://wiki.metacubex.one/config/proxies/masque/)，并用安装的内核 `mihomo -t` 实测解析
- Shadowrocket：保留原项目 URI 方言；该链接不承载完整分流、SNI、MTU 等配置，不保证所有客户端版本兼容
- sing-box / VLESS：可选输出仅连接**同机已运行的 Usque SOCKS**。不能把它们当作远程 VLESS 节点或原生 MASQUE 转换
- 不自动引入免费第三方节点、国家出口、注册器、网页或 Worker 部署

仓库原有 Opera / Proton / Windscribe 工作流与 `worker/` 代码未改动，它们不属于这个默认生成流程；部分历史工作流会注册账号并提交含凭据配置，**不要为了本流程运行它们**。历史 `configs/` 文件不读取、不删除；本次整合也不说明历史文件是否安全。

## 整合记录

- 主项目基线：[`a18457d1`](https://github.com/frederick1986/warp-masque-actions/commit/a18457d1a77e57553fcf96eb5a967c979590048f)
- 能力参考：[`usque-custom-pro@8719fa08`](https://github.com/frederick1986/usque-custom-pro/commit/8719fa082b1ddd16e1465cc4718db8e1f926a514)
- 修复借鉴方案中的 CIDR 只解析未写入、AI 路由选项未作用于最终规则的问题
- 未复制捐赠项目网页、注册逻辑、部署配置或任何实际账号配置
