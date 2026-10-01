# 隧道健康检查与手动恢复

离线生成命令仍然不联网、不注册。这里新增两个**独立、显式启用**的命令；它们没有被生成器、现有 Actions 或定时任务调用。本次开发只执行了合成数据/模拟测试，没有读取真实凭据、建立真实 WARP 隧道或注册新账号。

## 先判断哪一种故障

```sh
python scripts/healthcheck.py --account /安全目录/usque-config.json --mihomo-bin /可信目录/mihomo --run-live-check --output outputs
```

这条命令会启动临时、仅监听 `127.0.0.1` 的 Mihomo，所有探测流量通过单个指定 MASQUE 出站访问 Cloudflare HTTPS trace。不会配置 DIRECT 回退；curl 显式指定本机代理并禁用 `.curlrc` 和 NO_PROXY 绕过。探测结束后终止临时进程并清除临时配置；不打印 trace 的出口 IP、账号值或原始内核日志。

需要已有可信来源的 Mihomo（支持 MASQUE）和 curl（支持 `--fail-with-body`）。不自动安装工具。可用 `--settings` 指定与生成相同的配置文件。

默认最多 3 个不同主机入口，每个 2 次，尝试间隔 2 秒，每次探测最多 20 秒。优先覆盖可用地址族；不同端口的同一主机不计为两个独立入口。`--max-endpoints`、`--retries`、`--timeout` 可调整，但降低尝试数不会降低认证故障的判定门槛。

`outputs/health.json` 使用以下状态：

- `healthy`：HTTP 成功且 Cloudflare trace 明确返回 `warp=on` 或 `warp=plus`；立即停止后续探测
- `network`：全部表现为超时、DNS/连接失败或网络不可达；不会判定账号失效
- `transient`：服务端临时错误/限流，或这些错误与网络问题混合；不会注册新账号
- `auth_failure`：至少两个不同入口，每个至少两次，所有尝试都出现**隧道层**明确认证拒绝
- `unknown`：其他错误、混合认证/网络结果、证据不足、工具/内核启动失败，或返回内容不能确认 WARP

目标网站的 HTTP 401/403、证书错误、配置损坏、节点慢、IPv6 不可用，都不能单独证明 WARP 账号过期。即使是 `auth_failure`，报告仍标记 `expiry_proven: false`，因为设备未 enroll、服务策略等问题也可能产生拒绝。它只允许进入人工确认的恢复流程，不会直接注册。

CLI 退出码：健康 0；输入/工具错误 2；网络/临时故障 3；认证故障 4；不确定 5。代码不读取上次报告触发注册，恢复时必须重新检查当前账号，避免使用过期结论。

## 确认后，只注册一次

新注册会在 Cloudflare 创建设备和凭据，并接受 [Cloudflare Application Terms](https://www.cloudflare.com/application/terms/)。运行前需要对**这一次操作**明确批准；下面的两个批准选项不能放进无人值守的定时脚本。

```sh
python scripts/recover_account.py \
  --account /安全目录/usque-config.json \
  --settings examples/generator.json \
  --mihomo-bin /可信目录/mihomo \
  --usque-bin /可信目录/usque \
  --output outputs \
  --run-live-check \
  --approve-new-registration \
  --accept-cloudflare-terms
```

恢复 CLI 当前只支持具有 POSIX 私有文件权限的环境；GitHub Actions 内直接拒绝执行恢复。本项目没有开通无人值守注册，也没有修改仓库可见性、Secrets 或账户权限。普通 `generate.py` 和 `gen_masque.py` 永远不会调用注册。

执行顺序：

1. 校验输入/输出路径及设置，重新诊断当前账号
2. 健康、网络故障、临时故障或不确定时保持原样，不调用注册器
3. 只有认证证据满足门槛且两个批准选项都存在时，取得本地互斥锁
4. 在独立私有状态目录写“注册已开始”标记，用提供的 Usque 二进制将新账号写入 `pending-account.json`
5. 用新账号在临时环境完成同样的实际隧道验证；成功前不替换旧账号或已有输出
6. 在内存中生成配置，保存被替换文件的私有备份和回滚日志，然后逐文件原子替换账号及生成文件
7. 成功后保留备份，删除已激活的 pending 文件和注册标记；写入不含凭据的 `outputs/recovery.json`

原始账号及恢复状态目录必须放在 `outputs/` 外，避免打包配置时带入原始 access token 和历史备份。设置文件也不能与任何产物目标重名。配置文件、临时账号和备份使用 `0600`，状态目录 `0700`；符号链接、重叠路径及现有目录型输出目标会被拒绝。

## 失败、不确定与继续验证

默认状态目录为原账号目录下的 `.warp-recovery/`；可以用 `--state-dir` 显式指定另一个独立私有目录。

- 注册超时、返回错误或中断：无法排除服务端已经创建账号。保留标记，不自动重试，也不再次创建账号
- 新账号格式无效或隧道检查失败：保留 pending 文件供检查，旧账号/旧配置不替换
- 已有 pending 或不确定注册标记：下一次申请新注册会被阻止
- 有效 pending 账号只需重新验证时，使用 `--resume-pending`，不会再调用 Usque 注册：

```sh
python scripts/recover_account.py \
  --account /安全目录/usque-config.json \
  --mihomo-bin /可信目录/mihomo \
  --output outputs --run-live-check --resume-pending
```

恢复时应传入与首次操作相同的 `--settings`、`--state-dir` 和输出目录。继续验证仍重新检查当前账号；如果当前账号已恢复健康，就保持原样。

普通文件写入异常会尝试还原已替换文件。它不是跨多个文件的整体原子事务；磁盘故障、断电或进程被强杀时，应检查私有 `rollback-*/journal.json`，从其中列出的备份恢复。若返回 `activation_failed` 且 `account_replaced` 为 null，表示需要检查文件实际状态，不能假定回滚已完成。

锁文件不会自动按时间失效，因为旧进程可能仍在执行注册。遇到遗留锁、已开始注册标记或回滚异常，应先人工核对；不要盲目删除后重跑。旧账号备份不会自动删除，也不执行远程账号注销。

## 测试与证据范围

`tests/test_health_recovery.py` 的探测、注册和网络进程全部使用注入假实现或 subprocess mock。覆盖超时不判过期、多个入口/多次认证门槛、成功早停、明确批准/条款门禁、候选验证、注册不确定不重试、恢复已有 pending、日志脱敏、路径保护和中途失败回滚。

这不代表已经验证真实账号能注册或连接。真实执行需要明确批准、用户安全提供已有账号及工具，以及可达的网络。第三方公开节点也不会通过这个 WARP 账号恢复器注册或修复；它们的刷新与健康由生成的 provider 配置交给客户端处理。

认证错误模式依据 [Mihomo MASQUE 实现](https://github.com/MetaCubeX/mihomo/blob/Meta/transport/masque/masque.go) 和 [H2 实现](https://github.com/MetaCubeX/mihomo/blob/Meta/transport/masque/client_h2.go)；注册参数依据 [Usque register 命令](https://github.com/Diniboy1123/usque/blob/main/cmd/register.go)。工具的新版本如果改变错误格式，未识别结果归为 unknown，不自动放宽认证判定。
