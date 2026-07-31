# 飞书群命令触发器（Cloudflare Workers 免费方案）

这个 Worker 用于接收飞书应用机器人群消息事件。群里发送 `最新资讯消息` 后，Worker 会触发 GitHub Actions 的 `daily-feishu.yml`，由 GitHub Actions 完成采集、生成和飞书推送。

## 成本边界

- Cloudflare Workers 使用 Free plan，不绑定付费功能。
- GitHub Actions spending limit 保持为 `0`，免费额度耗尽时停止，不继续计费。
- 不使用 AWS，不使用付费模型，不使用付费翻译 API。
- 不在本机常驻运行。

## 需要准备

1. 一个飞书开放平台“企业自建应用”，启用机器人能力。
2. 一个 Cloudflare 免费账户。
3. 一个 GitHub fine-grained personal access token：
   - Repository 选择本项目仓库。
   - Permissions 至少开启 `Actions: Read and write`。
   - 不要给无关仓库或无关权限。

## Cloudflare Worker 配置

把 `wrangler.toml.example` 复制为 `wrangler.toml` 后，填入非敏感配置：

```toml
GITHUB_OWNER = "你的 GitHub 用户名或组织名"
GITHUB_REPO = "仓库名"
GITHUB_WORKFLOW_FILE = "daily-feishu.yml"
GITHUB_REF = "main"
```

以下必须设置为 Cloudflare Worker Secrets，不要写入文件：

```bash
FEISHU_VERIFICATION_TOKEN=飞书事件订阅页面的 Verification Token
GITHUB_TOKEN=GitHub fine-grained token
```

如果用 Cloudflare 控制台部署，可在 Worker 的 `Settings > Variables` 中添加：

- `GITHUB_OWNER`
- `GITHUB_REPO`
- `GITHUB_WORKFLOW_FILE`
- `GITHUB_REF`

并把下面两个设置为 encrypted secret：

- `FEISHU_VERIFICATION_TOKEN`
- `GITHUB_TOKEN`

## 飞书开放平台配置

1. 创建企业自建应用。
2. 启用机器人能力，并把应用机器人加入目标群。
3. 在“事件订阅”里填写 Worker URL。
4. Verification Token 填入 Cloudflare Worker Secret。
5. 不启用 Encrypt Key；当前 Worker 明确拒绝加密事件，避免引入额外密钥和复杂依赖。
6. 订阅事件 `im.message.receive_v1`。
7. 权限里启用接收群消息所需权限，并发布应用版本。

群里发送：

```text
最新资讯消息
```

Worker 收到后会触发 GitHub Actions。几分钟后，原有飞书 Webhook 会发送国内/国外分组日报。

## 本地测试

```bash
cd workers/feishu-command-router
npm test
```

测试只模拟飞书事件和 GitHub API，不会真实触发 GitHub Actions。
