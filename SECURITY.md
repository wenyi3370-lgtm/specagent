# 安全策略

## 项目定位

SpecAgent 是**个人作品集项目**(MIT,见 [README 的"局限"一节](README.md#局限坦率地说)),没有安全赏金、SLA 或专职响应。它适合本地使用与小团队内网部署,**不适合**直接作为多租户生产服务。

## 已知安全模型与边界

- **共享 token 模型**:一个部署只有一个 `SPECAGENT_API_TOKEN`,没有多用户账号、租户隔离或细粒度权限。
- 网页上的 "Run project suite" 会在**服务器进程里**执行配置中的 Agent 代码(python 适配器)或按配置请求目标地址——对外暴露前**必须**设置 `SPECAGENT_API_TOKEN` 并加 HTTPS(见 README 的安全提示)。
- 已知遗留问题与边界(如旧 `POST /api/runs` 的 CSRF 暴露、python 适配器超时不强杀线程)集中记录在 [docs/known-issues.md](docs/known-issues.md)。

## 如何报告

1. **不要**在任何 Issue/PR/截图里粘贴 `.env`、API key、token 或数据库连接串;发现泄露请立即吊销并轮换。
2. 行为缺陷或文档问题:直接开 [GitHub Issue](https://github.com/wenyi3370-lgtm/specagent/issues),附最小复现(命令 + 输出 + 版本号 `specagent --version`)。
3. 涉及可被利用的具体漏洞:同样开 Issue 但标题只写概述、细节留到维护者要求再补充;或通过 GitHub 的 "Report a vulnerability"(私密报告)提交。
