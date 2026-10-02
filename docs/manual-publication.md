# 手动 Actions：三份 WARP 配置与持久输出

由仓库所有者自行运行。没有注册、账号刷新、恢复器、代理托管或定时任务。纯第三方直连聚合应在独立的 FreeNodes 项目处理，本流程不修改那个项目。

## 三份独立配置和出口区别

默认 include_external_nodes=true，已有账号生成：

- outputs/masque.yaml：只有 WARP MASQUE 节点，完整配置
- outputs/usque-custom-pro.yaml：原项目中转用途，主出口选择第三方链，附带必要 WARP 节点
- outputs/combined.yaml：主选择可切换“纯 WARP 出口”和“WARP 中转第三方出口”，完整配置
- outputs/manifest.json：文件、节点数、来源/过滤计数等辅助元数据，无原始账号或节点凭据

三份都可独立导入，不依赖彼此。后两份每个第三方节点都通过 dialer-proxy: WARP中转 拨号，专用组只含纯 WARP 自动组/节点，形成“设备 → WARP → 第三方节点 → 网站”，不指回混合组形成循环。donor 文件的主出口不提供纯 WARP 选择；combined 明确提供两类出口，所以二者不是改个文件名的同一份内容。必要的 WARP relay 设置仍可配置。

中转由你本机的 Mihomo 内核执行，Actions 不托管服务，不启动 WARP 隧道。三份都需要支持 MASQUE 的内核。原有国内/局域网、明确直连和直连优先服务规则保留；这里保证第三方节点的拨号链，不宣称所有流量都必经代理。

纯 WARP 文件中 FREE/已选国家路由目标会映射到 WARP 自动组，manifest 记录仅含目标名称的映射；组合配置保留原目标。取消第三方选项则不下载源，仅生成 masque.yaml + manifest。旧 generate.py 的离线 provider API 保持兼容，新发布器不接收它的旧文件名或纯 external.yaml。

## 静态快照来源与边界

采用 donor usque-custom-pro 的 Au1rxx/free-vpn-subscriptions 国家源，例如：

https://raw.githubusercontent.com/Au1rxx/free-vpn-subscriptions/main/output/by-country/clash-US.yaml

默认 US/JP/SG，可选 HK/TW/KR，仅这组固定 HTTPS URL 可用。下载限制超时和 8 MiB/来源，拒绝重定向及压缩响应。只提取通过校验的 proxies，不导入上游规则、监听端口、provider 或任何全局配置；不执行远程内容。

解析拒绝 YAML 别名、重复键、过深结构及资源超限；过滤 MASQUE、不支持的协议与不完整节点。协议选项保留，节点使用确定性新名字并跨国去重；所有国家组引用一并更新。上游自带拨号链和平台接口设置不会进入最终配置。stable 模式沿用 donor 协议排除项，all 仍只接受当前快照解析器支持且通过校验的类型，不意味着所有 Mihomo 插件均兼容。

任一来源下载失败、超限、文档结构非法或过滤后无可用结构节点，整次生成失败，停止上传/发布，不生成半份结果。manifest 记录来源计数和过滤情况，不把结构校验当作连通性证明。过滤器也不能穷尽每个协议的 cipher/凭据组合和嵌套选项，源中的“usable”只代表通过静态过滤，不承诺每条都被当前内核接受；CI 用合成节点验证三份配置的内核语法。客户端不再下载第三方节点订阅；新开手动运行才刷新快照。ACL4SSR 规则集与 GeoIP（启用时）仍是独立运行时依赖。

第三方节点可能失效、缺乏授权或存在隐私风险；国家标签不保证真实出口。健康检测不证明 AI/流媒体解锁，WARP 链不使第三方运营者可信。未验证真实账号握手、第三方出口或服务解锁，也不会自动恢复账号。

## 手动运行与公开风险

- artifact 是默认值，下载保留 1 天。sample 使用无效 WARP fixture；account artifact 仅允许私有仓库、WARP_GENERATION_ENABLED=true 和单次 confirm_private_artifact
- repository 提交到 Run workflow 所选分支，不允许 tag。每次必须勾选默认 false 的 confirm_publish_outputs；account 还需用户自己设置已有账号 Secret WARP_ACCOUNT_JSON 和变量 WARP_GENERATION_ENABLED=true
- 三份真实 YAML 都含 WARP 私钥。公开仓库任何人可复制和使用，删除当前文件无法保证清除 Git 历史、fork 或缓存。应由你自己评估风险、配置 Secret、勾选并点击 Run workflow，不让代办工具替你触发真实账号公开发布
- Re-run 旧任务不算新确认，GITHUB_RUN_ATTEMPT 不是 1 就拒绝。修复后新开运行并重新确认
- 合并代码不等于运行。PR 未合并时 main 可能仍是旧注册流程，必须检查所选分支确有新版输入选项；成功执行前不应把尚不存在的输出地址当作下载链接

账号只在内存读入，不复制原始 JSON、设备 ID 或 access token。导出仍含客户端所需私钥，不能因“无原始 JSON”当作不敏感。

## 受限提交、失败与旧文件

新文件白名单只有 masque.yaml、usque-custom-pro.yaml、combined.yaml、manifest.json。上传前和发布前都核对名称及 manifest，拒绝额外文件、嵌套目录、符号链接、硬链接、特殊文件或过大文件。只读 generate 任务通过 1 天 artifact 传递同一组文件；隔离的 publish 任务不接收账号 Secret。

发布覆盖本次输出，移除不再生成的已知旧产物：旧 warp-masque/provider/Shadowrocket、external/external-direct、本地桥接等。不会修改其他路径或清除历史。sample 会替换当前真实配置；取消第三方选项会移除当前两份中转文件，历史私钥仍存在。目标 outputs 若有非允许文件或链接，拒绝整个发布，由用户检查。

.gitignore 继续忽略本地 exports。发布器使用隔离 Git index，只构建白名单树，不暂存工作区，不运行 Git hooks/filters，不打印 diff、私钥或原始 Git 错误。产物来自点击运行时的代码/设置快照；运行期间的新源码更新会保留，但新设置需下一次运行才影响产物。

工作流仅一个仓库级 concurrency group，cancel-in-progress=false。GitHub 可能替换待运行 pending run，不保证每个排队请求都执行。发布器先 fetch 最新目标分支，保留其他路径，再普通 fast-forward push；fetch 后若有人抢先提交则失败，不强推、不 rebase、不自动重试。

只有 publish job 请求短期 contents: write。GitHub 不能把此 token 细分到 outputs 目录，目录限制由脚本实现，因此只运行可信分支。如组织策略或分支保护拒绝，需用户决定如何处理；不会绕过或自动修改设置。

即使 push 失败，1 天 artifact 仍可能存在且含私钥，不能认为配置没有离开运行器。按固定错误提示检查 Secret、变量、来源可达性及分支策略，不要粘贴配置或上传完整日志包。已经公开的密钥需自行评估撤销/替换；恢复器不会自动为此注册。

## 获取文件

等 publish 成功，在本次所选分支 outputs/ 打开所需 YAML，点击 Raw 复制 GitHub 实际提供的链接，单独导入任一份即可。本次代码更新不会预先创建真实输出，也不提供尚未存在的下载链接。

参考：[GitHub 手动运行工作流](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/manually-running-a-workflow)、[工作流语法与 job 权限](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。
