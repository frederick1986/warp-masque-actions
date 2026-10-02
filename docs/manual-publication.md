# 手动 Actions 生成与持久输出

这是一条由仓库所有者自行执行的公开发布流程。CLI 离线生成、网络诊断与手动恢复保持独立；此工作流不运行恢复器，不注册、不刷新账号，不自动调度。不要把此流程当作密钥保管服务。

## 执行边界

- `artifact` 是默认值。sample/external-only 可在公开仓库下载；account artifact 仅允许私有仓库、`WARP_GENERATION_ENABLED=true` 和单次 `confirm_private_artifact`
- `repository` 会提交到 Run workflow 中选择的分支（不允许 tag），每次必须勾选 `confirm_publish_outputs`，默认 false。account 还要求用户自己设置已有账号 Secret `WARP_ACCOUNT_JSON` 和 `WARP_GENERATION_ENABLED=true`
- 公开账号配置含私钥。它可能被任何人复制和使用，删除当前文件也无法保证清除 Git 历史、fork 或缓存。应由你自己审阅风险、配置 Secret、勾选和点击 Run workflow；不要让代办工具代为触发含真实账号的发布
- 重新运行旧任务不算新确认，`GITHUB_RUN_ATTEMPT` 不是 1 时拒绝。修复失败后新开一次手动运行，重新选择分支/模式/目的地并确认
- 代码合并不等于运行。PR 未合并时，默认分支仍可能使用旧流程；必须确认所选分支确有新版输入选项。没有执行成功前，不存在本流程承诺的输出或 Raw 链接

账号的设备 ID、access token 与完整账号 JSON 不复制到导出，Secret 不存入文件。生成器只在内存读入账号，输出的是客户端实际所需字段。MASQUE 配置依然包含私钥，不能因“无原始 JSON”就视为不敏感。

## 输出及更新语义

account/sample 的默认导出为：

- `outputs/warp-masque.yaml`
- `outputs/warp-masque-provider.yaml`
- `outputs/warp-masque-shadowrocket.txt`
- `outputs/manifest.json`
- 勾选外部节点时另有 `outputs/external-direct.yaml`

external-only 仅有 `outputs/external-direct.yaml` 和 `outputs/manifest.json`。显式修改设置启用本地桥接时，另允许 `outputs/sing-box-usque-local.json` 与 `outputs/sing-box-vless-local.json`，含义与限制见 [离线生成器说明](offline-generator.md)。这些文件之外，不允许发布任何其他名称、嵌套目录、符号链接、硬链接或特殊文件。

生成目录在运行器临时空间，每次从干净目录生成。上传前和提交前都核对固定文件白名单与 manifest 的实际文件集合。生成结果经保留 1 天的 artifact 传递给隔离的发布任务；只读生成任务没有写仓库的 token，发布任务不接收账号 Secret。

发布覆盖本次输出，并移除所选分支 `outputs/` 下“属于白名单但这次未生成”的旧文件。不清理其他路径，也不删除历史版本。若目标 outputs 下已有非白名单文件或符号链接，拒绝整个发布，请自己检查。sample 会把当前 WARP 配置替换为无效样例，external-only 会移除当前 MASQUE 文件，历史中的密钥仍存在。

`.gitignore` 继续忽略本地 `outputs/`，防止普通 `git add` 误提交。本发布器用隔离 Git index 只构建白名单文件的树，不暂存工作区内容，不运行 Git hooks/filters，不打印 diff、账号、私钥或原始 Git 错误。

## 并发、权限与失败

工作流使用同一个仓库级 concurrency group，`cancel-in-progress: false`，避免两个运行同时写入。GitHub 可能替换排队中的 pending run，不保证多个待运行请求都执行；以实际运行状态为准。

生成配置取自点击运行时的代码/设置快照；运行期间有人更新设置，新设置会保留在分支上，但本次产物仍反映较早快照，需要新开运行才会采用。发布器先 fetch 目标分支最新 SHA，在其树上只替换 outputs 白名单，保留别的文件和更新，再做普通 fast-forward push。内容未变则无提交。若 fetch 之后别人又提交，push 失败，不强推、不 rebase、不自动重试，也不替你改变分支保护。

顶层权限为 contents: read，仅 publish 任务请求 contents: write。GitHub 无法把 token 的 contents 权限细分到 outputs 目录，目录约束由发布器实现；因此只运行你信任的分支与脚本。如组织策略或分支保护禁止写入，需要你决定如何处理，代码不会修改设置或绕过要求。

publish 失败时，1 天 artifact 仍可能存在并含相同的私钥，不能把推送失败理解为没有传出。失败后不要上传更多诊断文件或粘贴配置来排错；先看固定的错误提示，检查 Secret 是否存在、变量是否为 true、分支及写入策略。已经公开的凭据需自行评估撤销/替换；恢复器不会为此自动执行注册。

## 获取链接

等 publish 成功后，打开这次选择分支的 `outputs/`，打开目标文件，点 Raw 并复制 GitHub 实际提供的地址。外部配置在客户端运行时才下载第三方 provider，生成成功不等于节点在线、地域准确或服务解锁。本代码更新不会预先创建真实输出，也不提供尚未存在的下载链接。

参考：[GitHub 手动运行工作流](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/manually-running-a-workflow)、[工作流语法与 job 权限](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)。
