# Action 缓存与权限验证

日期为 2026-10-08，基于 PR #23 合并后的 main。此项先补发布前验证，版本保持 0.10.0，旧 Action 输入、门禁规则和 CLI 行为不变。

## 已有演示的第二次运行

用户明确允许重跑演示 PR #3 并更新其中已有的机器人评论。已重新运行 [37610139967 的 attempt 2](https://github.com/wenyi3370-lgtm/specagent/actions/runs/37610139967/attempts/2)，源提交仍为 `516de9eaa013804a5cbaab7b7562b6f8ef9c6752`。该工作流按设计保持红色，不合并演示 PR。

baseline 步骤在本次运行开始时直接命中并恢复前一次运行的缓存，日志有 Cache hit、Cache restored successfully，键仍为 `specagent-db-Linux-5d560b6b858aeb1988b0ea0bba3c7de1b355c3c4`。所以这是跨运行验证，不只是同次运行内的保存和恢复。此缓存属于 `refs/pull/3/merge`，不能据此声称已验证 main 到另一个 PR 的跨分支恢复。

[机器人评论 6036376078](https://github.com/wenyi3370-lgtm/specagent/pull/3#issuecomment-6036376078) 的 created_at 仍为 2026-10-07 10:51:51 UTC，updated_at 变为 2026-10-08 14:16:28 UTC，run id 从 `run_1a115fdaaa8_7e321a` 变为 `run_1a11bdf5b8e_648fcb`。查询匹配 SpecAgent 标题的机器人评论仍只有一条，已验证更新已有评论。新运行仍报告 4 个新回归、39 个稳定通过。

attempt 2 在最后 gate 失败前上传 demo-baseline 和 demo-candidate，产物 ID 为 11556681549、11555683872。第一次运行的产物另已有下载及 HTML/JUnit 核对记录，见 [发布证据核对](release-evidence-audit.md)。

## 新的自动验证

Action 新增三个输出，不改变原有输入。

| 输出 | 含义 |
|---|---|
| gate-exit-code | CLI 退出码，0 通过、1 门禁拦截、2 配置错误 |
| cache-matched-key | 实际恢复的缓存键，没恢复时为空，不把前缀恢复的 cache-hit=false 误判为没恢复 |
| comment-status | 尝试评论后 posted 或 unavailable，跳过评论时为空。unavailable 本身不区分权限拒绝与网络错误，应结合日志 |

`.github/workflows/action-integration.yml` 增加三个任务。

- baseline-cache 仅在默认分支的 push 或手动运行建立 FinCare 基线缓存，不能由 PR 的代码写默认分支缓存。
- cache-consumer 在 PR 运行候选版。如果有缓存，必须有 4 个实际新回归、CLI exit 1 和 Action failure。首个 PR 的 base 尚不含本工作流时，缓存缺失明确记 pending-main-baseline，不能把绿色任务说成跨分支验证通过。一旦工作流已合入 base，后续 PR 缺少缓存会直接失败，避免长期静默跳过跨分支验证。
- readonly-comment 以真实 contents: read、pull-requests: read 的 GitHub token 尝试评论。它先运行私有离线基线，再验证评论 unavailable 后仍有 4 个新回归、exit 1 和报告产物。工作流不用 pull_request_target，不提供额外 secrets 或写权限。

证据检查脚本读取真实 CLI JSON 与 Action 输出，拒绝配置错误、缺少输出、错误的任务 outcome、没有实际回归，以及未尝试评论就声称降级通过。两个 PR 任务分别上传 cache-evidence.json 与 readonly-evidence.json，保留状态和触发 ref，不能用整条工作流全绿替代这些证据。

GitHub 对缓存有分支访问限制，当前或默认分支、PR base 的缓存可恢复，但不同 PR 的 merge ref 缓存不能随意共享。设计依据见 [官方缓存文档](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)，实际匹配键输出见 [actions/cache restore 定义](https://github.com/actions/cache/blob/main/restore/action.yml)。本 PR 合并后先建立 main 缓存，再在 v0.11 发布 PR 验证跨分支恢复。

用户目前没有外部账号的 fork，已明确选择先验证受限 token。本次测试不能证明真实 fork 的事件、checkout 和缓存行为；该范围继续留在 U10，不能整体关闭。

### GitHub 受限 token 的实测结果

[集成运行 37793205110](https://github.com/wenyi3370-lgtm/specagent/actions/runs/37793205110) 的 head 为 `69d07d21dbff5dff7194d477db67503d7c92b529`。只读评论请求实际返回 `Resource not accessible by integration (addComment)`，随后发出警告，readonly-evidence.json 为 status=verified、comment_status=unavailable、gate_exit=1、new_regressions=4。已下载核对证据产物 11557975677，报告产物 11557975661/11557925856 也已上传，受限 token 降级已验证。

同一运行的 cache-evidence.json 产物 11557112687 已下载，明确为 pending-main-baseline，日志 Cache not found。不是跨分支验证通过。两份证据里的 source_sha 为执行时的 PR merge SHA `80d399d87f3ba36446555bbcc04e816b35b88bce`，不是 PR head。默认分支缓存任务在 PR 事件按条件 SKIPPED，等待合并后的 main push 建立缓存。

## 本机验证与发布准备

### 默认分支缓存已建立

PR #24 已合并为 `eac1266c5010ae4b72d610295b513b639e5c3ad4`。[main 集成运行 37794616932](https://github.com/wenyi3370-lgtm/specagent/actions/runs/37794616932) 的 baseline-cache 成功，日志确认保存 `specagent-db-Linux-eac1266c5010ae4b72d610295b513b639e5c3ad4`。GitHub 缓存 API 核对 ref=refs/heads/main、大小 7768 字节。生产缓存已完成，发布 PR 仍需下载消费证据，不能仅凭 main 成功宣布跨分支恢复通过。

### main 到发布 PR 的恢复已实测

[PR #25 集成运行 37795679379](https://github.com/wenyi3370-lgtm/specagent/actions/runs/37795679379) 源 head 为 `ddcdc889bf5f7c608268654d62623a4d7f89a60e`。日志明确 Cache restored successfully 与 Cache restored from key，键为 main 的上述缓存键。已下载产物 11558302950，cache-evidence.json 为 status=verified、gate_exit=1、new_regressions=4，触发 ref 为 refs/pull/25/merge。source_sha 为执行时 PR merge SHA `4c86f6e3a756528e91635d31a7b01cc685d2558b`，与源 head 区别记录。

同次运行的只读 token 仍实际返回 Resource not accessible by integration (addComment)，证据产物 11558621006 为 verified、comment_status=unavailable、四个新回归与 gate_exit=1。候选报告 11557779752 与只读评论任务报告 11557714348/11557614495 均已上传。跨分支恢复与受限 token 降级已通过，真实 fork 仍未验证。上文 pending 记录保留为首次运行的历史，不是当前待办。

新增检查使用临时 FinCare 项目和两个临时 SQLite 数据库，生成真实有基线与无基线的 CLI JSON。首轮 52 项 Action 集成、既有 Action 结构和 CI helper 测试通过，12.96 秒；本机完整 867 passed、11 个既有警告、451.25 秒。随后只补“base 已含工作流就必须恢复缓存”的严格检查及一项测试，最终专项 53 passed、8.15 秒。GitHub 最终结果见交接记录及 PR 描述。旧断言未修改，首轮全量不冒充新增一项后的重跑。

为下一阶段准备，另在系统临时目录从跟踪的打包文件构建当前 0.10.0 wheel（264056 字节），以 --no-index、--no-deps、--target 非编辑安装到独立目录。已确认 app 从该目录而非源码导入、包元数据与版本一致、CLI --version 和健康接口为 0.10.0，七份新增/既有网页 HTML、JS、CSS 均在 wheel 内。这不是全新无依赖环境，第三方依赖复用本机 venv，也不能冒充尚未制作的 0.11.0 验证。该操作不修改本机安装、仓库配置或真实 .env。
