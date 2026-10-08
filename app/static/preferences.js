/* Presentation preferences only. No requests, no server configuration writes. */
(() => {
  'use strict';
  const LANG_KEY='specagent_ui_language',THEME_KEY='specagent_ui_theme';
  const read=key=>{try{return sessionStorage.getItem(key)}catch{return null}};
  const languageValues=['original','zh-CN','en'],themeValues=['dark','light','system'];
  let language=languageValues.includes(read(LANG_KEY))?read(LANG_KEY):'original';
  let theme=themeValues.includes(read(THEME_KEY))?read(THEME_KEY):'dark';
  const systemTheme=matchMedia('(prefers-color-scheme: light)');
  function applyTheme(){document.documentElement.dataset.theme=theme==='system'?(systemTheme.matches?'light':'dark'):theme}
  applyTheme();systemTheme.addEventListener('change',()=>{if(theme==='system')applyTheme()});

  // Source keys remain reviewable. Only authored UI is eligible for translation.
  const copy=new Map();
  const english={
    'Set':'设置','Clear':'清除','API token':'API 令牌','API token required or wrong':'需要 API 令牌或令牌错误',
    'Target agent':'被测 Agent','Run project suite':'运行项目测试','Cancel run':'取消运行','Cancel the running project suite':'取消正在执行的项目测试',
    'Project tools':'项目工具','Run options':'运行选项','Label':'标签','Run label':'运行标签','Run':'运行',
    'Compare project run against':'选择项目运行的对比基线','Run for triage and export':'用于分诊和导出的运行',
    'Validate':'校验','Triage':'分诊','Report HTML':'HTML 报告','Export JUnit':'导出 JUnit','Export JSON':'导出 JSON',
    'Verify':'复验','Pre-fix run':'修复前运行','Suggestion ID':'建议 ID','Fix suggestion ID':'修复建议 ID',
    'Fix suggestions':'修复建议','Draft':'草稿','Behavior requirements for a draft':'草稿行为要求','Read-only YAML draft':'只读 YAML 草稿',
    'Copy':'复制','Download YAML':'下载 YAML','Run behavior audit':'运行行为审查','Reset demo':'重置演示',
    'Playwright for':'行为测试工具，适用于','AI Agents.':'AI Agent。',
    '01 · Behavior Spec':'01 · 行为规格','02 · Test Generator':'02 · 用例生成','03 · Agent Runner':'03 · Agent 执行',
    '04 · Deterministic Judge':'04 · 确定性判定','05 · Regression Diff':'05 · 回归对比','06 · CI Gate':'06 · CI 门禁',
    'Behavior score':'行为得分','Passed':'通过','Failed':'失败','Errors':'错误','Compiler':'编译器',
    'Compiled rules':'编译后的规则','Test results':'测试结果','Runs':'运行次数','Last run':'最近运行','Created':'创建时间',
    'Metrics':'指标','Run history':'运行历史','★ = baseline':'★ = 基线','Agent':'Agent','Started':'开始时间','Score':'得分',
    'Regression diff':'回归差异','Not connected':'未连接','Idle':'空闲','New session':'新会话','Send':'发送',
    'loading…':'正在加载…','Open':'查看','Set baseline':'设为基线','Diff vs ★':'与基线对比','Diff vs…':'选择对比运行…',
    'Report':'报告','Copy diff':'复制 diff','Download fix.diff':'下载 fix.diff','Copy git apply command':'复制 git apply 命令',
    'Behavior pass rate':'行为通过率','Critical violations':'Critical 违规率','New regressions':'新回归','Flaky rate':'不稳定率','Tool accuracy':'工具准确率','Latency P95':'延迟 P95',
    'Decline':'拒绝','Approve':'批准','Awaiting approval':'等待审批','Connecting…':'正在连接…','Unavailable':'不可用',
    'History':'历史','Error':'错误','Declined by you':'你已拒绝','Executed':'已执行','Not executed':'未执行','Tool call':'工具调用',
    'needs you':'需要你审批','Approval required ·':'需要审批 ·','Approving…':'正在批准…','Declining…':'正在拒绝…',
    'Offline · deterministic':'离线 · 确定性流程','Offline workflow · deterministic':'离线工作流 · 确定性结果',
    'Deterministic results · verbatim':'确定性结果 · 保留原文',
    'Tests the built-in demo agent (or TARGET_AGENT_URL) — set SPECAGENT_PROJECT_CONFIG to run your own project with "Run project suite" above.':'测试内置演示 Agent 或 TARGET_AGENT_URL。配置 SPECAGENT_PROJECT_CONFIG 后，可用上方运行项目测试按钮测试自己的项目。',
  };
  for(const [en,zh] of Object.entries(english))copy.set(en,[zh,en]);
  const chinese={
    '运行':'Run','状态':'Status','操作者':'Operator',
    '通知':'Notifications','通知历史':'Notification history','通知内容':'Notification content','邮件':'Email','PR 评论':'PR comment',
    '接收地址和凭据由部署者配置，浏览器仅管理渠道开关。通知只包含运行摘要。':'The operator configures destinations and credentials. The browser manages channel switches. Notifications contain only run summaries.',
    '选择已完成的运行':'Select a finished run','选择通知渠道':'Select a notification channel','预览通知':'Preview notification','发送前确认':'Confirm before sending','确认发送':'Confirm send',
    '我已核对摘要和所选渠道，明确授权发送这次通知。':'I reviewed the summary and selected channel, and explicitly authorize this notification.',
    '失败或发送中可能已经送达。不会自动重发，请先核对接收端。':'Failed or sending deliveries may have arrived. No automatic retry; check the destination first.',
    '时间':'Time','渠道':'Channel','启用渠道':'Enable channel','关闭渠道':'Disable channel','渠道已启用':'Channel enabled','渠道已关闭':'Channel disabled','凭据未配置':'Credentials not configured',
    '待确认':'Awaiting confirmation','发送中，结果尚未确定':'Sending; outcome unknown','已发送':'Sent','发送失败，可能已送达':'Failed; may have arrived','预览已过期':'Preview expired','暂无通知记录。':'No notification records.',
    '正在读取通知…':'Loading notifications…','正在发送通知…':'Sending notification…','通知已发送。':'Notification sent.',
    '服务端通知配置无效，请联系部署者。':'Server notification configuration is invalid. Contact the operator.','尚未配置通知接收目标，请联系部署者。':'No notification destinations configured. Contact the operator.',
    '当前账号可查看历史，不能发送通知。':'This account can view history but cannot send notifications.','渠道开关不会自动发送。每次发送都需要预览并明确确认。':'Channel switches do not send automatically. Each delivery requires a preview and explicit confirmation.',
    '预览尚未发送。确认后将发送到所选渠道。':'The preview has not been sent. Confirmation sends to the selected channel.',
    '发送失败，可能已送达。不会自动重发，请先核对接收端。':'Delivery failed and may have arrived. No automatic retry; check the destination first.',
    '渠道尚未启用。':'Channel is not enabled.','服务端凭据尚未配置。':'Server credentials are not configured.','预览已过期或配置发生变化，请重新预览。':'Preview expired or configuration changed. Preview again.',
    '这次通知已尝试发送，请查看历史。':'This delivery was already attempted. Check history.','发送通知需要登录或配置共享令牌。':'Sign in or configure a shared token to send notifications.','没有此项目的权限。':'You do not have access to this project.',
    '中文 / English':'Switch language','账号登录':'Account sign-in','账号和密码由部署者管理。项目权限可在顶部管理。':'The operator manages accounts and passwords. Manage project access from the top bar.',
    '项目权限':'Project access','退出登录':'Sign out','管理员':'Administrator','编辑用户':'Editor','查看用户':'Viewer','未授权':'No access',
    '登录工作台':'Sign in to the dashboard','使用部署者提供的账号访问已授权的项目。':'Use an account provided by the operator to access your projects.',
    '用户名':'Username','密码':'Password','登录':'Sign in','正在登录…':'Signing in…',
    '账号和密码由部署者管理。没有账号时，请联系管理员。':'The operator manages accounts and passwords. Contact an administrator if you need an account.',
    '查看用户可读取项目历史。编辑用户可执行测试和修改项目记录。':'Viewers can read project history. Editors can run tests and update project records.',
    '用户':'User','权限':'Access','保存权限':'Save access','撤销权限':'Revoke access','正在读取权限…':'Loading access…','权限已保存。':'Access saved.','权限已撤销。':'Access revoked.',
    '权限按项目生效，管理员可访问全部项目。':'Access applies per project. Administrators can access all projects.','此项目尚未授权普通用户。':'No regular users have access to this project.',
    '没有可访问的项目，请联系管理员。':'No projects are available. Contact an administrator.','用户名或密码错误。':'Incorrect username or password.',
    '登录尝试过多，请稍后再试。':'Too many sign-in attempts. Try again later.','没有此操作的权限。':'You do not have permission for this action.','请求失败，请稍后重试。':'Request failed. Try again later.','登录服务暂时不可用。':'Sign-in is temporarily unavailable.',
    '（显示已截断）':' (display truncated)',
    '无标签':'No label','未识别本机调用者':'Unidentified local caller','Agent 人工审批':'Agent human approval','本机调用':'Local invocation',
    '此基线早于历史记录功能，原设置时间和操作者未知。不会补造历史。':'This baseline predates history tracking. Its original time and operator are unknown; no history is invented.',
    '取消当前基线前确认':'Confirm before clearing the current baseline','输入完整当前基线 ID':'Enter the full current baseline ID','确认取消基线':'Confirm clear baseline','返回':'Back',
    '运行标签（可清空，最多 256 字）':'Run label (can be cleared; up to 256 characters)','保存标签':'Save label',
    '显示值已脱敏，保存会替换整个标签。此操作不改变测试结果。':'The displayed value is redacted. Saving replaces the entire label without changing test results.',
    '运行移入回收站后不再出现在默认历史、项目数量和指标中。执行、轨迹、规格、建议及复验关联仍保留，按 ID 可继续读取并可恢复。':'Trashed runs are excluded from default history, project counts and metrics. Executions, traces, specs, suggestions and verification links remain readable by ID and can be restored.',
    '记录仍为 running 或已在回收站，不能删除。':'The record is running or already in trash and cannot be deleted.','输入完整运行 ID 确认':'Enter the full run ID to confirm','确认移入回收站':'Confirm move to trash',
    '项目配置:无法连接服务器':'Project configuration: cannot connect to the server','请先选择一次运行':'Select a run first',
    '达到步数上限':'Step limit reached','超出时间预算':'Time budget exceeded','模型调用失败':'Model call failed',
    '调用':'Call','结果':'Result','审批':'Approval','恢复':'Resume','模型请求':'Model request',
    '请先批准或拒绝当前动作，再切换历史会话。':'Approve or decline the current action before switching sessions.',
    '会话正在执行，请稍后刷新日志。':'The session is running. Refresh its log later.','活动会话已失效，请新建会话。':'The active session expired. Create a new session.',
    '连接中断。请刷新历史日志核对执行结果，审批动作不要重复提交。':'Connection interrupted. Refresh history to check the outcome before submitting an approval again.',
    '执行你的 Agent 并写入一次新的 run(不会设为 baseline)':'Run your Agent and save a new run without setting a baseline',
    '重新运行测试,并与修复前的 run 对比':'Rerun tests and compare with the pre-fix run',
    '只写入 .specagent/suggestions/,不修改项目文件':'Write only to .specagent/suggestions/; project files are unchanged',
    '用草稿替换真实规格文件(原文件自动备份为 .bak)':'Replace the spec with the draft; the original is backed up as .bak',
    '把该 run 设为项目 baseline':'Set this run as the project baseline',
    '语言 / Language':'Language','项目基线':'Project baseline','最近一次 (last)':'Last run (last)','未记录':'Not recorded',
    'OPENAI_API_KEY 未设置，不能用 LLM 扩展。配置 run.llm_expand 时也会跳过，并返回与命令行相同的警告。':'OPENAI_API_KEY is unset; LLM expansion is unavailable. Configured run.llm_expand is also skipped, with the same CLI warning.',
    '尚无修复建议。Agent 批准后生成的建议会显示在这里。':'No fix suggestions yet. Approved Agent suggestions will appear here.',
    '这个时间范围没有已结束的运行。':'No finished runs in this time range.','Critical 违规率':'Critical violation rate','当前没有基线。':'No current baseline.',
    '尚无修改历史。新操作才会开始记录。':'No change history; only new operations are recorded.',
    '未配置 OPENAI_API_KEY:将运行固定的确定性流程(validate → run → triage → summary),输入文本不会被解释。':'OPENAI_API_KEY is unset. A fixed deterministic workflow runs (validate → run → triage → summary); input text is not interpreted.',
    '跳到主要内容':'Skip to main content','主题':'Theme','深色':'Dark','浅色':'Light','跟随系统':'System',
    '查看项目历史':'View project history','演示 Agent 行为要求':'Demo agent behavior requirements',
    '设置与关于':'Settings and About','关闭':'Close','刷新配置':'Refresh configuration',
    '服务端当前配置，只读展示。密钥已配置不代表连接成功。已有 Agent 会话仍保留创建时的模型。':'Read-only server configuration. A configured key does not prove connectivity. Existing Agent sessions retain their original model.',
    '正在检查服务端项目配置…':'Checking server project configuration…','运行进度':'Run progress','已完成用例':'Completed test cases',
    '准备运行…':'Preparing run…','正在检查项目配置…':'Checking project configuration…','对比哪次运行':'Compare against',
    '设为基线':'Set as baseline','用 LLM 扩展用例':'Expand cases with LLM','生成草稿':'Generate draft','刷新建议':'Refresh suggestions',
    '修复服务端 Agent 代码后，按配置重新运行，与修复前的运行比较。':'After fixing the server Agent code, rerun the configured suite and compare with the pre-fix run.',
    '建议不会自动应用。请在本地用 git apply 应用后点 Verify。批准后的建议保存在服务端。':'Suggestions are not applied automatically. Apply them locally with git apply, then verify. Approved suggestions remain on the server.',
    '这不会修改任何文件；要写入规则文件请用 Agent 面板的审批流程。':'This does not modify files. Use the Agent panel approval flow to write rules.',
    '配置 OPENAI_API_KEY 时，输入文本会发送给配置的 LLM 服务商。不要输入密钥或个人数据。':'When OPENAI_API_KEY is configured, input is sent to the configured LLM provider. Do not enter keys or personal data.',
    '描述 Agent 应遵守的规则':'Describe the rules the Agent must follow',
    '用自然语言定义 Agent 应该怎么做。SpecAgent 编译规则、生成行为测试、采集工具调用轨迹、判定违规，并与 Baseline 对比找出新引入的行为回归。':'Define expected Agent behavior in natural language. SpecAgent compiles rules, generates tests, collects tool traces, checks violations and compares against a baseline to find new regressions.',
    '自然语言 → 机器可测试规则(YAML 可编辑)':'Natural language → testable rules (editable YAML)',
    '正常、边界、改写、社工、注入用例':'Normal, boundary, paraphrase, social engineering and injection cases',
    '执行 Agent 并采集 tool trace':'Run the Agent and collect its tool trace',
    'required / forbidden / 审批时序 → PASS / FAIL':'Required / forbidden calls / approval order → PASS / FAIL',
    '与 Baseline 对比:NEW / FIXED / PERSISTENT / FLAKY':'Compare with baseline: NEW / FIXED / PERSISTENT / FLAKY',
    'specagent run → Critical 回归使 PR 失败':'specagent run → critical regressions fail the PR',
    '重复执行详情':'Repeat execution details','关闭详情':'Close details','Projects · 项目':'Projects','刷新项目':'Refresh projects',
    '创建项目':'Create project','项目 ID':'Project ID','项目名称':'Project name','项目说明（可选）':'Project description (optional)',
    '例如 fincare-review':'e.g. fincare-review','例如 FinCare 行为审查':'e.g. FinCare behavior review','记录项目用途，勿填写密钥':'Describe the project purpose; do not enter keys',
    '创建并查看':'Create and view','项目名称与说明':'Project name and description','适配器资料':'Adapter metadata',
    '此入口只保存项目 ID、名称和说明。项目选择用于查看历史。被测 Agent、适配器和规格由服务端配置决定，创建项目不会切换测试目标。':'This saves only the project ID, name and description. Project selection filters history. The tested Agent, adapter and specs come from server configuration; creating a project does not change the target.',
    '项目资料创建为网页功能。接入自己的 Agent 请在本机运行 specagent init，配置 specagent.yaml 后由部署者设置 SPECAGENT_PROJECT_CONFIG 并重启服务。':'Project metadata creation is a web feature. To connect your Agent, run specagent init locally and configure specagent.yaml. The deployer then sets SPECAGENT_PROJECT_CONFIG and restarts the server.',
    '规则与规格':'Rules and specs','查看当前 YAML':'View current YAML','刷新版本':'Refresh versions','历史版本':'Historical version',
    '历史规格版本':'Historical spec version','查看版本':'View version','对比起点':'Compare from','规格对比起点':'Spec comparison baseline',
    '对比版本':'Compare versions','规格版本差异':'Spec version diff','复制源内容':'Copy source','下载源内容':'Download source',
    '查看编译后的规则':'View compiled rules','查看本次规格版本':'View this run’s spec version','对比完成':'Comparison complete',
    '版本按编译后的规则去重。注释或排版变化不会生成新版本。当前 YAML 来自服务端配置的项目。':'Versions are deduplicated by compiled rules. Comments and formatting do not create a version. Current YAML comes from the server project.',
    '命令行关联操作：specagent validate 查看当前规则校验；specagent report --run <id> 查看关联运行。历史源内容和版本对比暂为网页专属只读功能。':'Related CLI commands: specagent validate checks current rules; specagent report --run <id> displays a linked run. Historical source and version comparisons are read-only web features.',
    '内容已隐藏凭据和服务端绝对路径。复制、下载和对比使用这里显示的内容。':'Credentials and server paths are hidden. Copy, download and compare use the displayed content.',
    '这个版本尚无关联运行。':'This version has no linked runs yet.',
    '指标趋势':'Metric trends','时间范围':'Time range','趋势时间范围':'Trend time range','最近 7 天':'Last 7 days','最近 30 天':'Last 30 days','最近 90 天':'Last 90 days','全部时间':'All time','刷新趋势':'Refresh trends',
    '只展示已结束的运行，包括已取消的运行。时间按 UTC 显示；点击或按 Enter 选择数据点查看运行。等价命令行：specagent metrics --project <project> 查看当前数值，历史曲线为网页功能。':'Shows finished runs, including canceled runs. Times are UTC; click or press Enter on a point to view its run. CLI: specagent metrics --project <project> shows current values; historical charts are a web feature.',
    '将鼠标移到数据点或用 Tab 聚焦，查看精确数值。':'Hover over a point or focus it with Tab to see exact values.',
    '查看历史数值与关联运行':'View historical values and linked runs','运行时间 (UTC)':'Run time (UTC)','运行':'Run','通过率':'Pass rate','新回归':'New regressions','工具准确率':'Tool accuracy',
    '搜索':'Search','运行 ID、标签或 Agent':'Run ID, label or Agent','状态':'Status','全部状态':'All statuses','已完成':'Completed','运行中':'Running','已取消':'Canceled','错误':'Error',
    '基线':'Baseline','全部记录':'All records','当前基线':'Current baseline','非基线':'Not baseline','查看':'View','正常历史':'Active history','回收站':'Trash','每页':'Per page','搜索 / 刷新':'Search / refresh',
    '搜索、分页、标签修改及可恢复删除为网页功能。删除前展示影响，当前基线和 running 记录受保护。按运行 ID 仍可读取回收站中的报告与规格关联。':'Search, pagination, labels and recoverable deletion are web features. Deletion previews its impact; current baselines and running records are protected. Reports and spec links remain readable by ID in trash.',
    '上一页':'Previous','下一页':'Next','操作':'Actions','通过 / 失败 / 错误':'Passed / Failed / Errors','基线历史':'Baseline history','刷新历史':'Refresh history','取消当前基线':'Clear current baseline',
    '修改和取消均保留历史。共享 token 无法区分具体账号，显示共享令牌用户；CLI 显示本机进程用户。操作者信息由服务端记录。':'Changes and clears retain history. Shared tokens cannot identify accounts; they show a shared-token user. CLI shows the local process user. The server records the operator.',
    '时间（UTC）':'Time (UTC)','操作者 / 入口':'Operator / source','原基线':'Previous baseline','新基线':'New baseline',
    '历史会话':'Saved conversations','选择已保存的会话':'Choose a saved conversation','刷新':'Refresh','查看日志':'View log','更多会话':'More conversations','继续会话':'Resume conversation','下载日志':'Download log',
    '让 Agent 帮你运行、分诊、起草规则':'Ask the Agent to run tests, triage failures or draft rules',
    'Agent 只提出建议,所有判定仍由确定性 judge 给出。执行代码、写入规格或改动 baseline 之前,都会先停下来等你批准。':'The Agent proposes; the deterministic judge decides. Before running code, writing specs or changing a baseline, it pauses for your approval.',
    '运行测试并分诊失败':'Run tests and triage failures','解释最新一次运行里的回归':'Explain regressions in the latest run','起草一条更严格的规则':'Draft a stricter rule',
    'AI proposes, rules verify — PASS/FAIL 只由确定性 judge 给出':'AI proposes, rules verify — only the deterministic judge decides PASS/FAIL',
    'Agent 对话记录':'Agent conversation log','Agent 执行过程':'Agent execution','执行时间线':'Execution timeline','工具调用和执行结果':'Tool calls and results',
    '给 Agent 的消息':'Message to the Agent','描述你想做的事,例如:运行测试并解释失败的规则':'Describe a task, e.g. run tests and explain failing rules',
    'Enter 发送 · Shift+Enter 换行':'Enter to send · Shift+Enter for a new line',
    '日志保存在项目的 .specagent/agent-logs/，重启后仍可回看。继续会话需要服务器中的活动会话，空闲 1 小时后失效。':'Logs are saved in .specagent/agent-logs/ and remain readable after restart. Resuming requires an active server session; idle sessions expire after one hour.',
    '版本':'Version','数据库':'Database','认证模式':'Authentication','Agent API':'Agent API','共享令牌':'Shared token','本机无令牌':'Local, no token','已启用':'Enabled','未启用':'Disabled',
    '共享令牌不能区分具体个人。本机无令牌模式限本机部署使用。设置不提供账号编辑。':'Shared tokens cannot identify individuals. No-token mode is intended for local deployment. Account editing is not available here.',
    'LLM 配置状态':'LLM configuration','密钥':'API key','已配置':'Configured','未配置':'Not configured','仅空白，配置无效':'Whitespace only; invalid configuration','已安装':'Installed','未安装或不可检测':'Not installed or undetectable',
    '服务地址':'Service endpoint','自定义 OpenAI 兼容服务':'Custom OpenAI-compatible service','OpenAI SDK 默认地址':'OpenAI SDK default endpoint','连接验证':'Connection check','未验证':'Not checked',
    '当前没有 LLM key，Agent 使用离线工作流，草稿使用确定性编译。':'No LLM key is configured. The Agent uses an offline workflow and drafts use deterministic compilation.',
    '此页面不发送模型请求。配置与实际连通、额度或模型权限是不同的状态。':'This page sends no model requests. Configuration does not verify connectivity, quota or model permissions.',
    '模型与用途':'Models and purposes','Agent / 草稿':'Agent / drafts','模型来源':'Model source','行为编译 / 扩展 / 语义裁决':'Behavior compilation / expansion / advisory judge',
    '显示新请求使用的配置模型。没有 key 时不会调用这些模型。语义裁决只作建议，CI 门禁仍由确定性规则判定。':'Shows models configured for new requests. Without a key, these models are not called. Semantic judgments are advisory; deterministic rules decide the CI gate.',
    '服务端测试目标':'Server test target','项目':'Project','适配器':'Adapter','执行方式':'Execution method','模块入口':'Module entry point','演示版本':'Demo variant','端点变量名称':'Endpoint variable name','名称不符合格式':'Invalid variable name','端点配置':'Endpoint configuration','已配置（地址隐藏）':'Configured (address hidden)',
    '被测模型':'Target model','不使用 LLM':'No LLM used','由被测 Agent 决定，当前未读取模块或远端服务':'Determined by the target Agent; its module and remote service have not been inspected',
    '运行与审批':'Execution and approvals','并发 / 超时':'Concurrency / timeout','重复 / 重试':'Repeats / retries','用例扩展':'Case expansion','配置为启用':'Configured on','配置为关闭':'Configured off','门禁严重度':'Gate severities','无':'None','源码读取':'Source access','配置为允许':'Configured allowed','配置为禁止':'Configured denied','Agent 步数 / 预算':'Agent steps / time budget',
    '源码读取权限来自服务端项目配置，并在创建 Agent 会话时确定。执行需确认的操作仍需人工审批。浏览器不能修改这些配置。':'Source access comes from server project configuration and is fixed when the Agent session is created. Operations requiring confirmation still need human approval. The browser cannot change these settings.',
    '项目配置':'Project configuration','无效或无法读取':'Invalid or unreadable','错误内容已截断，请在服务端检查完整配置。':'Errors are truncated. Check the full configuration on the server.',
    '能力与操作范围':'Capabilities and scope','判定与门禁':'Judgment and gate','确定性规则':'Deterministic rules','草稿':'Drafts','预览、复制与下载':'Preview, copy and download','修复建议':'Fix suggestions','查看与复验，本机应用':'View and verify; apply locally','设置':'Settings','只读配置展示':'Read-only configuration',
    '项目选择用于历史筛选，测试目标仍由服务端配置决定。密钥、地址、服务器路径和 Agent 指令不在此页面展示。':'Project selection filters history. Server configuration determines the test target. Keys, addresses, server paths and Agent instructions are hidden.',
    '内置确定性演示 Agent':'Built-in deterministic demo Agent','通过 HTTP 协议读取响应与轨迹':'Read responses and traces over HTTP','OpenAI Responses 工具调用适配器':'OpenAI Responses tool-call adapter','LangGraph 事件轨迹适配器':'LangGraph event-trace adapter','本机 Python callable 适配器':'Local Python callable adapter',
    '只读取项目配置。规格文件、被测模块和远端服务未在此页面验证。':'Only project configuration is read. Specs, target modules and remote services are not verified here.',
    '未配置项目测试目标，旧演示运行仍按其请求和服务端环境选择目标。':'No project target is configured. Legacy demo runs still select a target from their request and server environment.',
    '项目配置无效。':'Project configuration is invalid.','无法读取项目配置。':'Project configuration cannot be read.',
    '正在读取服务端配置…':'Reading server configuration…','需要 API token。请关闭窗口，输入 token 后重试。':'An API token is required. Close this dialog, enter a token and try again.',
    '正在加载…':'Loading…','加载中…':'Loading…','处理中…':'Working…','完成':'Done','正在创建项目…':'Creating project…',
    '项目 ID 和名称不能为空。':'Project ID and name are required.','项目已创建并选中。创建项目不会切换服务端测试目标。':'Project created and selected. The server test target is unchanged.',
    '项目已创建，但列表刷新失败。请点击刷新项目。':'Project created, but list refresh failed. Select Refresh projects.','项目 ID 已存在，请使用其他 ID。':'Project ID already exists; choose another ID.',
    '暂无项目。可创建项目资料，或运行一次测试。':'No projects yet. Create project metadata or run a test.',
    '回收站为空。':'Trash is empty.','No runs yet — 没有符合条件的运行。':'No matching runs yet.','当前基线或 running 记录不能删除。':'Current baselines and running records cannot be deleted.',
    '其他页的基线运行 ID':'Baseline run ID from another page','按 ID 对比':'Compare by ID','请输入基线运行 ID':'Enter a baseline run ID','修改标签':'Edit label','恢复运行':'Restore run','删除（可恢复）':'Delete (recoverable)',
    '当前基线不能删除。':'The current baseline cannot be deleted.','运行仍在执行，不能删除。':'The run is still executing and cannot be deleted.','运行已在回收站。':'The run is already in trash.','运行不存在。':'Run not found.','确认的运行 ID 不匹配。':'The confirmation run ID does not match.',
    '标签已保存。':'Label saved.','已移入回收站。可切换到回收站恢复。':'Moved to trash. Switch to trash to restore.','取消':'Cancel','未删除。':'Nothing deleted.','运行已恢复，重新纳入正常历史和指标。':'Run restored to active history and metrics.',
    '当前没有基线。默认 diff 无法比较，新回归趋势显示未评估。':'No current baseline. Default diff is unavailable; regression trends are unevaluated.',
    '尚无修改历史。旧数据的设置时间和操作者未知。':'No change history. Earlier baseline times and operators are unknown.',
    '共享令牌用户':'Shared-token user','本机网页用户':'Local browser user','命令行':'CLI','网页':'Web','人工审批':'Human approval','设置基线':'Set baseline','取消基线':'Clear baseline',
    '基线已取消，运行和修改历史仍保留。':'Baseline cleared; runs and change history are retained.','基线已发生变化，未取消。请刷新历史后重新确认。':'Baseline changed and was not cleared. Refresh history and confirm again.',
    '正在读取已保存的重复结果…':'Reading saved repeat results…','次数':'Attempt','延迟':'Latency','轨迹':'Trace','响应':'Response','轨迹（JSON）':'Trace (JSON)',
    '内容已达到执行或展示上限，以下为保留的部分。':'Execution or display limits were reached. The retained portion is shown below.',
    '没有保存重复详情。旧记录、单次执行或开始前取消的记录无法还原逐次结果。':'No repeat details saved. Earlier records, single executions and pre-start cancellations cannot reconstruct individual attempts.',
    '每次状态是当时的确定性 Judge 结果；整轮 FLAKY、取消或无工具验证的最终状态另计。LLM 裁决仍每用例一次，不会重跑 Agent。':'Each attempt shows its original deterministic judgment. Final FLAKY, cancellation or no-tool validation states are separate. LLM judgment remains once per case; viewing does not rerun the Agent.',
    '比较第几次':'Compare attempt','与第几次':'With attempt','对比两次':'Compare attempts','正在对比…':'Comparing…','对比使用展示范围内的内容，结果已截断。':'Comparison uses the displayed portion; results are truncated.',
    '等待服务端创建运行记录。':'Waiting for the server to create a run record.','进度暂不可用，将重试。':'Progress is unavailable; retrying.','无法读取最终进度。':'Cannot read final progress.',
    '此运行没有实时进度记录，展示已有摘要。':'No live progress was recorded for this run. The saved summary is shown.','运行已结束，计数为最终判定。':'Run finished; counts are final judgments.','进度记录中断。请查看运行错误。':'Progress recording stopped. Check the run error.','用例执行完成，正在保存最终结果。':'Cases finished; saving final results.','正在执行，计数为暂定结果，将在结束时确认。':'Running; counts are provisional and will be confirmed at completion.',
    '正在加载趋势…':'Loading trends…','没有已结束的运行。':'No finished runs yet.',
    '历史日志只读，可继续活动会话或新建会话':'History is read-only; resume an active session or create a new one','先批准或拒绝上方的待处理动作':'Approve or decline the pending action above',
    '我已审阅以上变更,由我本人批准':'I have reviewed these changes and approve them personally','仅限人类批准:CLI 的 --yes 对这个动作无效':'Human approval only: CLI --yes cannot approve this action',
    '固定流程输出,未经模型改写':'Fixed workflow output, not rewritten by a model','由确定性 judge 产生,模型无法改写':'Produced by the deterministic judge; the model cannot rewrite it','查看建议':'View suggestion',
    '无法连接服务器,请检查服务是否在运行':'Cannot connect. Check that the server is running.','需要 API token:请在右上角输入 token 后重试':'An API token is required. Enter it at the top and retry.','Agent API 未启用:服务端需要设置 SPECAGENT_API_TOKEN':'Agent API is disabled. Configure SPECAGENT_API_TOKEN on the server.',
    '活动会话已失效。历史日志仍可查看，请新建会话继续工作。':'The active session expired. Logs remain readable; create a new session to continue.',
    'Run history 与 Metrics 已刷新':'Run history and metrics refreshed','预览':'Preview','文本内容':'Text content',
  };
  for(const [zh,en] of Object.entries(chinese))copy.set(zh,[zh,en]);
  for(const pair of [...copy.values()]){if(!copy.has(pair[0]))copy.set(pair[0],pair);if(!copy.has(pair[1]))copy.set(pair[1],pair)}

  // Anchored templates translate only fixed prose; captures retain their bytes.
  const templates=[
    [/^影响:(.*)$/,m=>[m[0],'Effect: '+translate(m[1])]],
    [/^修改标签 · (.*)$/,m=>[m[0],'Edit label · '+m[1]]],
    [/^删除运行前确认 · (.*)$/,m=>[m[0],'Confirm before deleting run · '+m[1]]],
    [/^项目 (.*) · 标签 (.*) · (\d+) 条执行 · (\d+) 条违规$/,m=>[m[0],'Project '+m[1]+' · label '+m[2]+' · '+m[3]+' executions · '+m[4]+' violations']],
    [/^取消后不删除任何运行、规格、证据或历史。后续默认对比和新回归趋势会显示无基线，原有历史 CI 结果保留。此操作仅影响 (.*)。$/,m=>[m[0],'Clearing retains all runs, specs, evidence and history. Default comparisons and new regression trends will show no baseline; historical CI results remain. This affects only '+m[1]+'.']],
    [/^(基线历史读取失败|趋势加载失败|对比失败|读取重复详情失败|项目配置无效)[：:](.*)$/s,m=>[m[0],({'基线历史读取失败':'Baseline history failed','趋势加载失败':'Trends failed','对比失败':'Comparison failed','读取重复详情失败':'Repeat details failed','项目配置无效':'Invalid project configuration'}[m[1]])+': '+m[2]]],
    [/^项目配置:加载失败\(HTTP (\d+)\)$/,m=>[m[0],'Project configuration failed (HTTP '+m[1]+')']],
    [/^下载失败 \(HTTP (\d+)\)$/,m=>[m[0],'Download failed (HTTP '+m[1]+')']],
    [/^请求失败 · HTTP (\d+)$/,m=>[m[0],'Request failed · HTTP '+m[1]]],
    [/^(\w+)（未执行）$/,m=>[m[0],m[1]+' (not executed)']],
    [/^v(\d+) · (\d+) 条规则 · (.*)$/,m=>[m[0],'v'+m[1]+' · '+m[2]+' rules · '+m[3]]],
    [/^(.*) · (\d+) 条规则 · (.*)$/,m=>[m[0],(m[1]==='当前文件'?'Current file':m[1])+' · '+m[2]+' rules · '+m[3]]],
    [/^v(\d+) 与 v(\d+) 的源内容差异$/,m=>[m[0],'Source diff: v'+m[1]+' vs v'+m[2]]],
    [/^新增规则 (.*) · 移除规则 (.*)$/,m=>[m[0],'Added rules '+(m[1]==='无'?'none':m[1])+' · removed rules '+(m[2]==='无'?'none':m[2])]],
    [/^最近 (.*)$/,m=>[m[0],'Latest '+m[1]]],
    [/^(.*)，(\d+) 次运行，时间 UTC$/,m=>[m[0],translate(m[1])+'; '+m[2]+' runs; UTC time']],
    [/^(YAML|JSON) 规格源内容$/,m=>[m[0],m[1]+' spec source']],
    [/^(.*) · (活动会话|只读日志) · (\d+) 条记录$/,m=>[m[0],m[1]+' · '+(m[2]==='活动会话'?'active session':'read-only log')+' · '+m[3]+' records']],
    [/^规则 (.*) · 修复前 (.*)$/,m=>[m[0],'Rule '+m[1]+' · pre-fix '+m[2]]],
    [/^文件 (.*)$/,m=>[m[0],'Files '+m[1]]],
    [/^(.*) （在项目目录执行）$/,m=>[m[0],m[1]+' (run in the project directory)']],
    [/^等价命令行[:：]\s*(.*)$/s,m=>['等价命令行: '+m[1],'CLI equivalent: '+m[1]]],
    [/^读取于 (.*) · 配置快照，连接未验证$/,m=>[m[0],'Read at '+m[1]+' · configuration snapshot; connection not checked']],
    [/^配置读取失败（HTTP (\d+)）$/,m=>[m[0],'Configuration read failed (HTTP '+m[1]+')']],
    [/^正在查看 (.*) 的历史。(.*)$/s,m=>[m[0],'Viewing history for '+m[1]+'. '+m[2].trimStart().replace(/^Run project suite 将运行服务端配置的 (.*)。$/,'Run project suite uses server target $1.').replace('尚未配置项目测试目标，可由部署者配置服务端。','No project target configured; a deployer can configure it on the server.')]],
    [/^操作目标项目 (.*)$/,m=>[m[0],'Target project '+m[1]]],
    [/^(.*) · (\d+) \/ (\d+) 次运行(，仅显示最新 (\d+) 次，请缩小时间范围)? · (新回归数与当前基线 (.*) 比较；更换基线会重算历史比较|未设基线，新回归未评估) · 截至 (.*) UTC$/,m=>[m[0],m[1]+' · '+m[2]+' / '+m[3]+' runs'+(m[4]?', latest '+m[5]+' shown; narrow the time range':'')+' · '+(m[7]?'New regressions compare with current baseline '+m[7]+'; changing it recomputes historical comparisons':'No baseline; new regressions unevaluated')+' · as of '+m[8]+' UTC']],
    [/^Testing: (.*) · (.*) · (\d+) rules · (\d+) cases · gate: (.*)$/,m=>['测试目标: '+m[1]+' · '+m[2]+' · '+m[3]+' 条规则 · '+m[4]+' 个用例 · 门禁: '+m[5],m[0]]],
    [/^(\d+) runs · (.*)$/,m=>[m[1]+' 次运行 · '+m[2],m[0]]],
    [/^共 (\d+) 条 · 第 (\d+) 页 · (\d+)–(\d+) · (正常历史|回收站)$/,m=>[m[0],m[1]+' records · page '+m[2]+' · '+m[3]+'–'+m[4]+' · '+(m[5]==='回收站'?'trash':'active history')]],
    [/^共 (\d+) 条 · (\d+)–(\d+)$/,m=>[m[0],m[1]+' records · '+m[2]+'–'+m[3]]],
    [/^查看第 (\d+) 次$/,m=>[m[0],'View attempt '+m[1]]],
    [/^第 (\d+) 次 · (\w+)( · 未执行)?$/,m=>[m[0],'Attempt '+m[1]+' · '+m[2]+(m[3]?' · not executed':'')]],
    [/^第 (\d+) 次 (\w+) 与第 (\d+) 次 (\w+)$/,m=>[m[0],'Attempt '+m[1]+' '+m[2]+' vs attempt '+m[3]+' '+m[4]]],
    [/^延迟 (\d+(?:\.\d+)?) ms · (\d+) 条轨迹$/,m=>[m[0],'Latency '+m[1]+' ms · '+m[2]+' trace events']],
    [/^计划重复 (\d+|未知) 次$/,m=>[m[0],'Requested repeats: '+(m[1]==='未知'?'unknown':m[1])]],
    [/^实际执行 (\d+) 次$/,m=>[m[0],'Executed attempts: '+m[1]]],
    [/^保存 (\d+) 条$/,m=>[m[0],'Saved: '+m[1]]],
    [/^最终 (\w+)$/,m=>[m[0],'Final '+m[1]]],
    [/^重复执行 · (.*)$/,m=>[m[0],'Repeat execution · '+m[1]]],
    [/^已完成 (\d+) \/ (\d+) · 通过 (\d+) · 失败 (\d+)（不稳定 (\d+)） · 错误 (\d+) · 取消 (\d+)$/,m=>[m[0],'Completed '+m[1]+' / '+m[2]+' · passed '+m[3]+' · failed '+m[4]+' (flaky '+m[5]+') · errors '+m[6]+' · canceled '+m[7]]],
    [/^最近完成 #(.*)$/,m=>[m[0],'Latest completed #'+m[1]]],
    [/^(\d+) events$/,m=>[m[1]+' 条事件',m[0]]],
    [/^audit complete · run (.*)$/,m=>['审查完成 · 运行 '+m[1],m[0]]],
    [/^确认有效至 (.*)$/,m=>[m[0],'Confirmation valid until '+m[1]]],
    [/^(\d+) 条记录$/,m=>[m[0],m[1]+' records']],
    [/^project suite complete · run (.*)$/,m=>['项目测试完成 · 运行 '+m[1],m[0]]],
    [/^指定基线运行 ID (.*)$/,m=>[m[0],'Baseline run ID '+m[1]]],
    [/^Diff (.*) vs another run$/,m=>['将 '+m[1]+' 与其他运行对比',m[0]]],
    [/^(.*) · (\d+) 个行为版本(。运行后会保存历史规格。)?$/,m=>[m[0],m[1]+' · '+m[2]+' behavior versions'+(m[3]?'. Run tests to save spec history.':'')]],
    [/^当前配置项目 (.*) 的源内容$/,m=>[m[0],'Current source for server project '+m[1]]],
    [/^正在查看 v(\d+) 的已保存源内容$/,m=>[m[0],'Viewing saved source for v'+m[1]]],
    [/^当前 YAML · (.*)$/,m=>[m[0],'Current YAML · '+m[1]]],
    [/^关联运行 · 共 (\d+) 次(，显示最近 50 次)?$/,m=>[m[0],'Linked runs · '+m[1]+(m[2]?' (latest 50 shown)':'')]],
    [/^(\d+) \/ (\d+) 秒$/,m=>[m[0],m[1]+' / '+m[2]+' seconds']],
    [/^审批记录 · (.*)$/,m=>[m[0],'Approval record · '+m[1]]],
    [/^查看项目 (.*)$/,m=>[m[0],'View project '+m[1]]],
    [/^✓ 你已批准 · (.*)$/,m=>[m[0],'✓ Approved by you · '+m[1]]],
    [/^✕ 你已拒绝 · (.*)$/,m=>[m[0],'✕ Declined by you · '+m[1]]],
    [/^⏸ Agent 已暂停:(\d+) 个动作等待你决定$/,m=>[m[0],'⏸ Agent paused: '+m[1]+' action(s) await your decision']],
  ];
  const protectedSelector='script,style,pre,code,textarea,input,[data-verbatim],.trace,.violation,.bubble,.tc-v,.project-description,button[data-project-id],button[data-run-id],#projectSel option,#toolsRun option,#verifyPreRun option,#agentHistorySelect option,#rules,#tests,#diffBody,#triageResult,#projectSummary,#validateResult,#verifyResult,#projectGate,#runsBody td.mono,.approval-summary,.repeat-diff>p.good';
  const textSources=new WeakMap(),attributeSources=new WeakMap();
  function translate(source){
    if(language==='original')return source;
    const trimmed=source.trim(),pair=copy.get(trimmed),index=language==='en'?1:0;
    let translated=pair?.[index];
    if(translated===undefined){for(const [pattern,render] of templates){const match=trimmed.match(pattern);if(match){translated=render(match)[index];break}}}
    return translated===undefined?source:source.slice(0,source.indexOf(trimmed))+translated+source.slice(source.indexOf(trimmed)+trimmed.length);
  }
  function translateText(node){
    if(!node.parentElement||(node.parentElement.closest(protectedSelector)&&!node.parentElement.closest('[data-ui]'))||!node.data.trim())return;
    const previous=textSources.get(node),source=previous&&node.data===previous.output?previous.source:node.data,output=translate(source);
    textSources.set(node,{source,output});if(node.data!==output)node.data=output;
  }
  function translateAttributes(node){
    if(node.matches('script,style,[data-verbatim],#languageSelect option'))return;
    let records=attributeSources.get(node);if(!records){records={};attributeSources.set(node,records)}
    for(const key of ['aria-label','placeholder','title']){
      const value=node.getAttribute(key);if(!value)continue;
      const previous=records[key],source=previous&&value===previous.output?previous.source:value,output=translate(source);
      records[key]={source,output};if(value!==output)node.setAttribute(key,output);
    }
  }
  function visit(root){
    if(root.nodeType===Node.TEXT_NODE){translateText(root);return}
    if(root.nodeType!==Node.ELEMENT_NODE)return;
    translateAttributes(root);root.querySelectorAll('[aria-label],[placeholder],[title]').forEach(translateAttributes);
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node;while((node=walker.nextNode()))translateText(node);
    for(const pre of root.matches('pre')?[root]:root.querySelectorAll('pre')){pre.tabIndex=0;if(!pre.hasAttribute('aria-label'))pre.setAttribute('aria-label',translate('文本内容'))}
    for(const th of root.matches('th')?[root]:root.querySelectorAll('thead th')){if(!th.hasAttribute('scope'))th.scope='col'}
  }
  function start(){
    const languageControl=document.getElementById('languageSelect'),themeControl=document.getElementById('themeSelect'),status=document.getElementById('preferencesStatus');
    languageControl.value=language;themeControl.value=theme;
    function refresh(){document.documentElement.lang=language==='en'?'en':'zh-CN';document.documentElement.dataset.language=language;visit(document.body)}
    function save(key,value){try{sessionStorage.setItem(key,value);return true}catch{return false}}
    languageControl.addEventListener('change',()=>{language=languageControl.value;const saved=save(LANG_KEY,language);refresh();status.textContent=language==='en'?'Interface language changed'+(saved?'':'; storage unavailable, reload resets the preference'):'界面语言已切换'+(saved?'':'。浏览器存储不可用，重载后恢复默认')});
    themeControl.addEventListener('change',()=>{theme=themeControl.value;const saved=save(THEME_KEY,theme);applyTheme();status.textContent=language==='en'?'Theme changed'+(saved?'':'; storage unavailable, reload resets the preference'):'主题已切换'+(saved?'':'。浏览器存储不可用，重载后恢复默认')});
    refresh();
    const pending=new Set();let frame=0;
    const observer=new MutationObserver(records=>{for(const record of records){if(record.type==='childList'){for(const node of record.addedNodes)pending.add(node)}else pending.add(record.target)}if(frame)return;frame=requestAnimationFrame(()=>{frame=0;for(const node of pending){if(node.isConnected)visit(node)}pending.clear()})});
    observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-label','placeholder','title']});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
