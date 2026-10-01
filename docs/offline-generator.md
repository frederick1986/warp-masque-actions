# 使用、导出边界与迁移

## 一个账号、多种入口

生成分为两件事：已有账号作为输入；离线转换作为输出。生成器没有注册 API，不下载或调用 Usque，也不会创建密钥、UUID 或访问令牌。注册、失效账号修复及真实连通测试是使用者单独执行的步骤。

`--account` 显式读取一个文件；`--account-env NAME` 显式读取指定环境变量。二者互斥，不会自动查找仓库里的 `config.json` 或 `.env`。账号文件只读取，所需字段为 `private_key`、`endpoint_pub_key` 以及至少一个 `ipv4` / `ipv6`。PEM 会去掉头尾，转换为 base64 DER；IP 自动补 `/32` 或 `/128`。参数检查不替代内核的 ECDSA DER 校验。

`--check` 在内存中完成转换，只打印节点数和文件数，不写任何输出。实际生成的每个文件使用临时文件原子替换；不建立历史快照、不清理以前生成的额外格式。调整格式后，如旧产物已不需要，请自行清理旧目录或换一个空目录。不要把输出目录设成源码目录。

## 自定义设置

JSON 可以只包含需要覆盖的字段。完整默认值见 `examples/generator.json`。例如：

```json
{
  "endpoints": ["192.0.2.1", "2001:db8::1", "masque.example.com"],
  "ports": [443, 8443],
  "family": "dual",
  "sni": "consumer-masque.cloudflareclient.com",
  "dns": ["1.1.1.1", "9.9.9.9"],
  "mtu": 1280,
  "chatgpt_route": "WARP",
  "other_ai_route": "AI",
  "custom_ip_rules": [
    {"cidr": "198.51.100.0/24", "target": "DIRECT"},
    {"cidr": "2001:db8:100::/48", "target": "WARP"}
  ],
  "ruleset_profile": "minimal",
  "formats": ["mihomo", "provider"]
}
```

以上 IP 和域名是文档示例，不能连接。`endpoints` 只写主机，不带协议或端口。IPv6 可带方括号；同值会规范化去重。节点数是去重地址和端口的笛卡尔积，超过 `max_nodes`（默认 500）报错，不进行隐式截断。`mtu` 范围 1280–9000，端口 1–65535；DNS 当前仅接受 IP，以保证导出的一致性。

### 分流优先级

1. 按输入顺序排列的自定义 IP / CIDR 规则
2. ChatGPT / OpenAI 明确域名规则
3. 主项目其他 AI 域名规则
4. 选定的远程规则集（`acl4ssr`）
5. 内联局域网 / 回环 / 链路本地网段直连
6. `acl4ssr` 保留中国 IP 的 `GEOIP,CN` 直连兜底
7. 最终 `MATCH` 交给“漏网之鱼”组，默认跟随节点选择组

目标 `WARP` 指向 WARP 自动选择组，`PROXY` 指向主选择组，`AI` 指向 AI 选择组；另有内置 `DIRECT`、`REJECT`。不提供假国家组；US / JP / FREE 等会被拒绝。纯 WARP 不能承诺选择国家。

`chatgpt_route` 和 `other_ai_route` 修改的是最终规则，而不是仅改变界面选项。远程 AI 规则统一跟随 `other_ai_route`，明确 ChatGPT 规则排在它们之前。自定义 IP 规则带 `no-resolve`；域名请求尚无目标 IP 时，它们不会强制为该域名查询 DNS。更具体/重叠 CIDR 请自行把优先规则放前面。

`ai_health_url` 非空时会真正创建 AI `url-test` 组，并成为 AI 选择组第一项。它只证明测试 URL 的网络响应，不能判断登录、验证码、订阅权益、出口地区或 AI 解锁。客户端启动后才进行检测，生成阶段不会请求该 URL。

### H2

默认是 QUIC。要用 H2，设置 `network: "h2"`，同时使用 `endpoint_source: "account"` 读取 `endpoint_h2_v4/v6`，或明确指定适合 H2 的地址。`formats` 仅选择 `mihomo` / `provider`（也可同时生成独立的本地桥接模板）。不自动把 QUIC 候选地址当成 H2 地址，不导出未验证的 H2 Shadowrocket 链接。

## Provider 用法

把 `warp-masque-provider.yaml` 放在已有 Mihomo 配置旁，并使用文件 provider：

```yaml
proxy-providers:
  warp:
    type: file
    path: ./warp-masque-provider.yaml
proxy-groups:
  - name: WARP
    type: select
    use: [warp]
rules:
  - MATCH,WARP
```

这是引用示例，不会自动编辑现有客户端文件；完整配置直接用 `warp-masque.yaml` 即可，不依赖同目录 provider。

## 可选 sing-box / VLESS 本地桥接

两种导出都需要使用者事先在同一台电脑运行自己的 Usque SOCKS，例如参照当前版本 `usque socks --help`，绑定 `127.0.0.1:1080`。生成器不启动、不安装服务。

- `formats` 加 `singbox-local`：输出 `sing-box-usque-local.json`，本地 mixed 入站 `127.0.0.1:2080` → 本地 Usque SOCKS `127.0.0.1:1080`
- `formats` 加 `vless-local`：输出 `sing-box-vless-local.json`，本地 VLESS 入站 `127.0.0.1:2081` → 同一本地 SOCKS
- VLESS 必须在 `bridge.uuid` 填入你已有的本地桥接 UUID，不自动生成凭据；不要把真实 UUID 提交到仓库
- 可用 `bridge.socks_port` / `mixed_port` / `vless_port` 修改端口；所有监听地址固定回环地址，端口不能冲突
- 两种 JSON 没有 WARP 私钥，也没有原生 MASQUE 出站；未运行 Usque 时无法工作
- 它们是独立的最小本地桥接模板，不携带 Mihomo 的 AI/CIDR/规则集策略；不是远程免费代理，也不能直接分享给别人的设备使用

参考官方 [SOCKS 出站](https://sing-box.sagernet.org/configuration/outbound/socks/) 和 [VLESS 入站](https://sing-box.sagernet.org/configuration/inbound/vless/)。本次没有增加公网监听、远程服务器或第三方免费订阅。

## 迁移变化

- `scripts/gen_masque.py ACCOUNT OUTPUT` 保留；现在复用 `scripts/warp_generator.py`，并额外生成 provider 和 manifest
- 默认目录从旧文档中的 `dist/` 改为 `outputs/`；显式提供的旧输出目录仍支持
- 默认去掉重复官方域名别名，56 个候选节点；所有节点明确使用 Mihomo 默认的 consumer SNI（可以覆盖）
- 账号完全复用；不再每次 Actions 执行 `usque register`，不上传原始 `usque-config.json`
- 完整配置保留主项目的服务分组/ACL4SSR 源、故障转移和漏网之鱼组、微软/苹果直连优先，以及 `GEOIP,CN` 直连兜底。ACL4SSR 模式运行时仍需要规则集和 GeoIP 数据；`minimal` 只有内联规则，无这些数据库依赖
- DNS 简化为可配置 IP nameserver + fake-IP，未照搬原来的中国/境外 geosite DNS policy、proxy-server-nameserver、sniffer 和 profile 持久化选项；原来的自动组改为 lazy 检测，所有健康检测间隔统一由 health_interval 控制。已有复杂客户端配置建议导入 provider 保留自己的 DNS/嗅探/持久化策略
- 默认不启用外部控制接口，也不包含 Worker UI。代理和 DNS 监听均限本机
- `Shadowrocket .txt` 沿用主项目链接方言，只表示 endpoint / port / keys / IPv4 / DNS / UDP；不会无依据地声称 SNI、MTU、自定义规则已转换到该格式。对这些配置有要求时使用已验证支持的 Mihomo YAML
- 原有 Opera / Proton / Windscribe / Worker 未整合进默认流程，也没有改变其生产设置或历史文件
- 没有添加调度周期、自动提交或最近三版快照；以后可独立决定是否需要

## 验证范围

```sh
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

离线测试覆盖输入校验、双栈去重、账号复用、AI 和 CIDR 的实际规则、完整配置与 provider 一致、URI 编码、loopback 桥接、私有文件权限、日志脱敏、兼容入口及 Actions 私有仓库门禁。

可选内核测试需自己已有可信来源的 Mihomo 可执行文件以及 OpenSSL：

```sh
MIHOMO_BIN=/安全目录/mihomo python -m unittest discover -s tests -p 'test_mihomo_smoke.py' -v
```

另可用 `SING_BOX_BIN=/安全目录/sing-box python -m unittest discover -s tests -p test_local_bridge_smoke.py -v` 检查两个本地桥接 JSON；只运行 `check`，不启动服务。

Mihomo 内核测试只在临时目录生成未注册的合成 ECDSA 密钥，执行 `mihomo -t`；不会启动代理、登录 WARP 或运行真实出口测速。仓库测试 fixture 刻意使用无效 DER，不能作为真实账号使用。实际 Shadowrocket 导入、真实 WARP 握手和 AI 解锁不在离线测试保证范围内。
