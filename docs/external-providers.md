# 外部 Clash-format 节点与独立直连配置

这部分移植 donor `usque-custom-pro` 的 `pages/app.js` 中真实的免费落地 provider 路径：`freeProviderUrl`、三组健康检查、按国家选择、服务范围和可选 WARP 链式连接。它并不是把 MASQUE 节点改名为 VLESS，也不依赖本地 SOCKS/VLESS 桥接。

## 明确启用

直接运行 CLI、不提供设置覆盖时，仍默认生成 WARP-only 配置。手动运行 GitHub Actions 时，`include_external_nodes` 复选框默认启用，会同时生成 MASQUE 与外部节点配置；取消勾选可保留 WARP-only。CLI 可以在生成设置里明确加入：

```json
{
  "external_providers": {
    "enabled": true,
    "countries": ["US", "JP", "SG"],
    "use_warp": false,
    "scope": "ai-streaming",
    "protocol_mode": "stable"
  }
}
```

完整例子见 [`examples/external-providers.json`](../examples/external-providers.json)。它不覆盖主项目的候选地址、端口和导出格式，默认保留 56 个 MASQUE 候选组合及原有格式，并增加外部 provider。沿用已有账号生成：

```sh
python scripts/generate.py --account /private/usque-config.json \
  --settings examples/external-providers.json --output /private/exports
```

只要非 MASQUE 外部节点配置时，无需任何账号输入：

```sh
python scripts/generate.py --external-only \
  --settings examples/external-providers.json --output /private/external-exports
```

这条命令仅生成 `external-direct.yaml` 和 manifest。manifest 的 `node_count: 0` 表示没有内联节点，`external_provider_count: 9` 表示默认三个国家的九个远程 provider；没有下载远程内容，所以不会伪报真实可用节点数。

生成器只写 provider 引用，不会读取真实订阅、探测出口或下载节点。客户端加载该配置后，才会访问远程 provider 和健康检查地址。

- `enabled`：CLI/设置默认值为 `false`；外部节点示例与 Actions 默认启用的 `include_external_nodes` 会明确设置为 `true`
- `countries`：启用后的默认候选为 `US`、`JP`、`SG`；另可选 `HK`、`TW`、`KR`，只生成选择的国家，重复值自动去重
- `use_warp`：默认 `false`；设为 `true` 时，仅完整 WARP 配置的订阅下载和节点连接都经 `WARP中转`
- `scope`：`ai-streaming` / `ai-only` / `streaming-only` / `all-foreign`；控制完整配置里哪些服务选择组优先使用外部节点，不会覆盖用户已经明确写出的 DIRECT、WARP 或其他路由规则
- `protocol_mode`：默认 `stable`，沿用 donor 排除 `hysteria2|tuic|wireguard|http|https|socks4|socks5`；`all` 允许其他内核支持的类型。两个模式都额外排除 `masque`

与 donor 推荐预设不同，CLI 基础设置默认关闭第三方 provider，手动 Actions 的外部节点选项则默认启用。开启后默认直接连接第三方节点；需要 WARP 链时明确设置 `use_warp: true`。

## 来源与国家分组

国家订阅 URL 沿用 donor 的 [Au1rxx/free-vpn-subscriptions](https://github.com/Au1rxx/free-vpn-subscriptions)：

```text
https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/by-country/clash-US.yaml
```

其他国家替换文件名里的 `US`。这里不提供任意订阅 URL、认证头或脚本执行入口。

每个国家创建 general / AI / stream 三个独立 provider。三者来源相同，缓存路径和健康检查相互独立；默认 US/JP/SG 共 9 个 provider。订阅每小时更新，单文件限制 8 MiB。国家标签来自上游分类，不代表已验证真实出口国家。

启用后，主配置的自定义 CIDR/AI 路由可引用 `FREE` 和已选择的国家代码。未启用或未选择的国家必须拒绝，避免出现没有实际 provider 的假国家路由。完整配置原有规则顺序保持不变。

## 服务组与检测边界

- `⚡ 免费落地自动`：gstatic 204，120 秒，5000 毫秒超时
- `🤖 AI自动优选`：ChatGPT 页面，200–399，120 秒，9000 毫秒超时
- `🎬 流媒体自动优选`：Netflix 页面，200–399，120 秒，9000 毫秒超时
- `🌍 免费落地可手动`：选择上述自动组或 general provider 的单个节点
- 国家组，如 `🇺🇸 US`：只使用对应国家 general provider

这些测试只是 HTTP 可达性检测，不能证明账户可登录、AI/流媒体已解锁、Netflix 内容库、出口 IP 地区、速度或长期可用性。流媒体组的 Netflix 检查也不能代表其他流媒体服务。

默认 `ai-streaming` 优先改动 AI、奈飞和国外媒体选择组；YouTube 仍保留原有优先级。`all-foreign` 也让主选择、YouTube、电报、FCM 等组优先选外部节点，微软/苹果保留原来直连优先。WARP-only 自动、手动和故障转移组不会被掺入外部节点。

`use_warp: true` 时，`WARP中转` 默认选择一个专用 WARP 自动组，也允许手选 WARP 节点。这个自动组只引用内联 MASQUE 节点，不会指回外部 provider 或混合策略组，因此没有反向依赖环。

## 两份完整配置的区别

启用外部 provider 后，生成器额外输出 `external-direct.yaml`：

- `warp-masque.yaml`：原有 MASQUE 节点、服务规则和选填外部 provider 共存；可以配置 WARP 链
- `external-direct.yaml`：独立配置，只引用外部 provider；没有内联 MASQUE、WARP 密钥、WARP 策略组或本地桥接依赖，订阅下载与节点连接都强制 `DIRECT`

这里 `DIRECT` 表示客户端直接连接所选第三方代理服务器，不表示网站请求都绕过代理。外部独立配置默认仍经免费自动组访问网站，局域网网段直连，另有内联 AI 和部分流媒体域名分组。它不复制主配置的自定义 CIDR、WARP/AI 路由选项、ACL4SSR 远程规则集或 GeoIP 依赖；需要这些策略时使用完整 WARP 配置或自行编辑独立配置。

独立文件的运行不需要 WARP 账号或 Usque。即使设置里开启了 `use_warp`，这个文件也强制直接连接。仅生成独立配置的纯 Python 接口不需要账号：

```python
from external_providers import direct_only_config
config = direct_only_config({"enabled": True, "countries": ["US", "JP", "SG"]})
```

该模块位于 `scripts/`，导入时需把该目录放入 Python 模块路径；接口只返回字典，不写文件。

远程内容可能变化，所以独立文件不是靠“上游永远没有 MASQUE”的假设实现：每个 provider 明确设置 `exclude-type: masque...`，并用 `override.dialer-proxy: DIRECT` 覆盖上游可能携带的链。详见 [Mihomo provider 官方文档](https://wiki.metacubex.one/en/config/proxy-providers/)。这也意味着它需要支持这些字段的 Mihomo；不能承诺老版 Clash 内核兼容，更不能宣称“任意非 MASQUE 客户端”都支持。

如果远程订阅不可用、格式不被内核支持、所有节点被过滤掉或检测均失败，这份配置没有可保证的第三方出口。请在本地检查 provider 状态；成功解析 YAML 不等于代理可用。

## 第三方信任与验证范围

这些免费节点由第三方维护。本项目不保证其安全性、隐私、授权、稳定性或地域标签准确性。服务运营者仍能观察其代理连接相关元数据；WARP 链式连接不会让第三方节点变成可信服务。不要因自动组名称或一次健康检查成功，就把敏感流量交给不信任的代理。

离线测试使用保留 IP、无效账号 fixture 或不存在的合成 SS 服务器，覆盖默认关闭、国家去重、三种检查、范围选择、循环依赖、字段校验、规则保留、直连覆盖和 MASQUE 排除字段。

```sh
python -m unittest discover -s tests -p test_external_providers.py -v
MIHOMO_BIN=/trusted/path/mihomo python -m unittest discover -s tests -p test_external_providers.py -v
```

可选内核测试仅运行 `mihomo -t`；把生产 URL 替换为本机不可达测试 URL，并预置合成 provider 缓存。验证过 Mihomo v1.19.32 的配置语法；没有启动代理、读取真实订阅、建立 WARP 隧道或验证第三方节点连通性。`-t` 不证明运行时每条远程节点都可用。
