# 全球 AI 前沿资讯飞书机器人

这是一个 AI 资讯采集与飞书日报服务。当前推荐的零成本运行方式是 GitHub Actions 定时任务：每天北京时间 10:00 运行一次，采集公开来源、生成国内 10 条 + 国外 10 条日报并发送到飞书群；状态保存在仓库内 JSON 文件，不需要 AWS、数据库或本机常驻。需要电脑关闭后也能响应群消息时，可用 Cloudflare Workers 免费版接收飞书应用机器人事件并触发 GitHub Actions。

项目仍保留 AWS SAM 模板作为可选方案，但零成本路径不创建任何 AWS 资源。系统只处理截至北京时间 09:50 已公开且成功获取的内容，不承诺覆盖全球全部新闻。

## 架构与数据流

```text
官方 RSS / Atom / arXiv / GitHub Release
                  |
                  v
 GitHub Actions schedule(UTC 02:00)
                  |
                  v
 python -m src.cli --action daily
                  |        URL 安全检查、并发采集、去重、分析、评分
                  v
        data/bot-state.json Articles
                  |
          prepare_digest
                  |        时间窗口、质量规则、多样性约束、持久化卡片
                  v
        data/bot-state.json Digests（国内/国外分组）
                  |
          publish_digest / retry
                  |        Deliveries 原子认领与幂等检查
                  v
           飞书群机器人
```

GitHub Actions 使用 UTC Cron，`0 2 * * *` 对应北京时间每天 10:00。`daily` 动作按顺序执行：

| 动作 | 时间 |
|---|---|
| `final_collect` | 10:00 任务开始后立即执行 |
| `prepare_digest` | 采集完成后执行，国内和国外分别最多选 10 条 |
| `publish_digest` | 日报准备完成后执行 |
| `retry_publish_1` / `retry_publish_2` | 仅上次发布状态为 `FAILED` 时重试 |

日报窗口为北京时间前一天 09:50（不含）至当天 09:50（含）。状态文件统一保存 UTC ISO 8601 时间，对外显示北京时间。

## 成本说明

GitHub Actions 方案不使用 AWS，不创建 DynamoDB、Lambda、EventBridge、CloudWatch、VPC、NAT Gateway、EC2、负载均衡器或 RDS。默认 `LLM_ENABLED=false`，不会调用第三方大模型 API。

需要注意的边界：

- GitHub Actions 私有仓库有免费分钟数；把 GitHub Actions spending limit 保持为 0，免费额度耗尽时任务停止而不是继续计费。
- 不要配置 OpenAI 或其他付费模型密钥；需要绝对零成本时保持 `LLM_ENABLED=false`。
- 飞书自定义机器人本身不需要付费。
- `data/bot-state.json` 只保存公开文章元数据、日报状态和发送状态，不保存 Webhook 或 API Key。
- 英文来源不会调用付费翻译或大模型；零成本模式会使用中文摘要模板呈现，避免把英文正文直接贴到群里。

AWS SAM 方案是可选路径，会创建云资源，即使大概率落在免费额度内也不是零成本保证。只追求零成本时不要执行 `sam deploy`。

## 零成本前置条件

- Python 3.12。
- 一个 GitHub 仓库，并启用 Actions。
- 一个飞书群自定义机器人。

## AWS 方案前置条件（可选）

- AWS 账号及可在 `ap-southeast-1` 创建 Lambda、DynamoDB、IAM、Scheduler、Logs、CloudWatch 资源的凭证。
- Python 3.12。
- AWS CLI v2、AWS SAM CLI 与可运行 ARM64 构建容器的 Docker。
- 一个飞书群自定义机器人。

配置 AWS 凭证，推荐使用 IAM Identity Center：

```bash
aws configure sso
aws sso login --profile your-profile
export AWS_PROFILE=your-profile
export AWS_REGION=ap-southeast-1
aws sts get-caller-identity
```

不要使用根用户 Access Key，也不要把凭证写入本项目。

## 创建飞书机器人

1. 在目标飞书群打开“设置 > 群机器人 > 添加机器人 > 自定义机器人”。
2. 启用签名校验，保存 Webhook 和签名密钥。
3. 不要在聊天、代码、README、提交记录或日志中粘贴真实值。
4. 本地真实发送时，可以在终端中临时设置环境变量：

```bash
read -rsp "Feishu webhook: " FEISHU_WEBHOOK_URL && export FEISHU_WEBHOOK_URL
read -rsp "Feishu signing secret: " FEISHU_SIGNING_SECRET && export FEISHU_SIGNING_SECRET
```

GitHub Actions 中不要把真实值写入代码或 workflow，使用仓库 Secrets 注入。AWS SAM 可选方案的模板参数使用 CloudFormation `NoEcho`，Lambda 通过环境变量接收；生产环境可进一步改用现有 Secrets Manager 或 SSM SecureString，这会产生相应 API 调用或存储费用。

## GitHub Actions 零成本运行

1. 将项目推送到 GitHub 仓库，确认 `.env` 没有提交。
2. 在 `Settings > Secrets and variables > Actions` 添加：
   - `FEISHU_WEBHOOK_URL`：飞书自定义机器人的 Webhook。
   - `FEISHU_SIGNING_SECRET`：如果机器人开启签名校验则填写；未开启可留空。
3. 确认 `.github/workflows/daily-feishu.yml` 已提交。该 workflow 使用 `contents: write` 权限，只为提交 `data/bot-state.json` 状态文件。
4. 在 GitHub Billing 中保持 Actions spending limit 为 0。免费额度耗尽时任务会失败或停止，不应继续产生费用。
5. 可在 Actions 页面用 `workflow_dispatch` 手动运行一次。真实运行会向飞书群发送日报。

状态文件 `data/bot-state.json` 会由 workflow 自动创建并提交回仓库。它不包含 Webhook、签名密钥或模型 Key，但包含公开来源文章标题、链接、摘要和发送状态。

默认国内来源使用当前环境可稳定访问的公开 RSS，包括 36氪和 IT之家；国外来源包括 OpenAI、Google AI、DeepMind、Microsoft Research、NVIDIA、arXiv、TechCrunch、MIT Technology Review 等。若某一组在当天窗口内不足 10 条，系统会在卡片里说明“可用来源不足”，不会用低质量内容凑数。

## 群消息触发（零成本可选）

飞书“自定义机器人 Webhook”只能发送消息，不能监听群消息。要实现电脑关闭后在群里发送 `最新资讯消息` 也能触发，需要使用飞书开放平台“应用机器人”事件订阅，再通过 Cloudflare Workers 免费版转发到 GitHub Actions。

```text
飞书群发送：最新资讯消息
        ↓
飞书应用机器人消息事件
        ↓
Cloudflare Worker（Free plan）
        ↓
GitHub Actions workflow_dispatch
        ↓
采集、生成、飞书推送国内/国外分组日报
```

Worker 代码位于 `workers/feishu-command-router/`。部署时只使用 Cloudflare Free plan，不启用付费 Workers 功能；GitHub Actions spending limit 继续保持为 `0`。详细配置见 `workers/feishu-command-router/README.md`。

需要配置的密钥：

- Cloudflare Worker Secret `FEISHU_VERIFICATION_TOKEN`：飞书事件订阅 Verification Token。
- Cloudflare Worker Secret `GITHUB_TOKEN`：GitHub fine-grained token，仅授予本仓库 `Actions: Read and write`。
- GitHub Actions Secret `FEISHU_WEBHOOK_URL`：用于最终把日报发送回飞书群。
- 可选 GitHub Actions Secret `FEISHU_SIGNING_SECRET`：如果飞书自定义机器人开启签名校验。

飞书事件订阅不要启用 Encrypt Key；当前 Worker 会拒绝加密事件，避免引入额外密钥和依赖。

## 本地运行与测试

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

只采集并生成日报，不发送飞书：

```bash
python -m src.cli --action final_collect --state-file data/local-state.json
python -m src.cli --action prepare_digest --state-file data/local-state.json
```

完整本地日跑会读取 `.env`（如果存在）并真实发送到飞书：

```bash
python -m src.cli --action daily --state-file data/local-state.json
```

本地状态文件 `data/local-state*.json` 已被 `.gitignore` 忽略；GitHub Actions 使用的 `data/bot-state.json` 会被提交回仓库，用来保持去重和发送幂等状态。

质量检查：

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

测试只使用 fake、内存仓储和 Mock Transport，不请求真实 AWS、模型或飞书。来源配置位于 `config/sources.yaml`，新增来源前应确认其是允许公开访问的 RSS、Atom 或 API，且不需要登录、绕过付费墙或违反站点限制。

如果要验证可选 AWS SAM 模板，再额外执行：

```bash
sam validate --lint
sam build --use-container
```

## 模型配置

服务支持兼容 OpenAI Chat Completions API 的接口。模型只分析被明确标记为不可信的数据，输出必须通过 Pydantic Schema。模型不可用或关闭时，系统使用原标题、来源摘要和确定性评分生成降级日报。

```bash
export LLM_ENABLED=false
export LLM_BASE_URL=
export LLM_API_KEY=
export LLM_MODEL=
```

启用模型时，通过终端安全读取 `LLM_API_KEY`，不要将真实值写入 `.env` 后提交。`.env.example` 仅列出变量名。

## AWS 部署（可选）

复制示例 SAM 配置，文件已被 `.gitignore` 排除：

```bash
cp samconfig.example.toml samconfig.toml
sam build --use-container
sam deploy --guided
```

首次 `sam deploy --guided` 使用：

- Region：`ap-southeast-1`
- Capabilities：`CAPABILITY_IAM`
- `FeishuWebhookUrl`：从终端环境变量输入，不要保存到版本库
- `FeishuSigningSecret`：从终端环境变量输入
- `LlmEnabled`：无模型时设为 `false`
- `LlmApiKey`：启用模型时安全输入
- `DigestItemLimit`：`5` 至 `20`

也可以在已保护的终端会话中部署：

```bash
sam build --use-container
sam deploy \
  --stack-name global-ai-frontier-bot \
  --region ap-southeast-1 \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    FeishuWebhookUrl="$FEISHU_WEBHOOK_URL" \
    FeishuSigningSecret="$FEISHU_SIGNING_SECRET" \
    LlmEnabled="$LLM_ENABLED" \
    LlmBaseUrl="$LLM_BASE_URL" \
    LlmApiKey="$LLM_API_KEY" \
    LlmModel="$LLM_MODEL"
```

注意 shell 历史、CI 日志和本机进程列表的泄露风险。真实生产环境优先由受保护的部署系统注入参数。

## AWS 验证与手动触发（可选）

获取函数名：

```bash
FUNCTION_NAME=$(aws cloudformation describe-stacks \
  --stack-name global-ai-frontier-bot \
  --region ap-southeast-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`FunctionName`].OutputValue' \
  --output text)
```

检查调度的时区和表达式：

```bash
aws scheduler list-schedules --region ap-southeast-1 \
  --query 'Schedules[?contains(Name, `global-ai-frontier`)].{Name:Name,State:State}'
aws scheduler get-schedule --region ap-southeast-1 --name SCHEDULE_NAME \
  --query '{Expression:ScheduleExpression,Timezone:ScheduleExpressionTimezone,State:State}'
```

手动执行各阶段：

```bash
aws lambda invoke --function-name "$FUNCTION_NAME" \
  --cli-binary-format raw-in-base64-out --payload '{"action":"collect"}' /tmp/result.json
aws lambda invoke --function-name "$FUNCTION_NAME" \
  --cli-binary-format raw-in-base64-out --payload '{"action":"prepare_digest"}' /tmp/result.json
aws lambda invoke --function-name "$FUNCTION_NAME" \
  --cli-binary-format raw-in-base64-out --payload '{"action":"publish_digest"}' /tmp/result.json
```

不要在未准备日报时直接发布。重复执行 `publish_digest` 会被 Deliveries 幂等状态阻止。

## AWS 日志与故障排查（可选）

```bash
sam logs --stack-name global-ai-frontier-bot --region ap-southeast-1 --tail
aws cloudwatch describe-alarms --region ap-southeast-1 \
  --alarm-name-prefix global-ai-frontier
```

日志为结构化 JSON，重点事件包括 `source_collection_failed`、`collection_completed`、`digest_prepared`、`digest_publish_failed` 和 `digest_publish_succeeded`。Webhook、Token、API Key 和签名字段会被隐藏。

常见排查顺序：

1. 检查 Scheduler 状态、时区和目标角色。
2. 查看 Lambda `Errors` 告警和对应日志。
3. 在 Articles 表确认 `published_at`、状态和评分。
4. 在 Digests 表确认当天记录为 `PREPARED`。
5. 在 Deliveries 表查看 `attempt_count`、`error_code` 和 `error_message`。
6. 单个来源失败不会阻止其他来源；连续失败应检查来源是否更换 Feed 或限制访问。

模板创建 Lambda 错误告警但不默认发送通知。可创建 SNS Topic、订阅已验证邮箱，再把 Topic ARN 添加为告警 Action。也可为日志事件创建 Metric Filter，对连续采集失败和 `digest_publish_failed` 建立独立告警。

## AWS 1 美元账单告警（可选）

在 AWS Billing 控制台：

1. 打开 AWS Budgets，创建 Cost Budget。
2. 周期选择 Monthly，预算金额设置为 `1 USD`。
3. 设置 Actual cost 达到 80% 和 100% 时发送邮件。
4. 同时在 Billing Preferences 启用 Free Tier Usage Alerts。

也可使用 AWS CLI `budgets create-budget`，但预算通知需填写实际账号 ID 和邮箱，因此本项目不预置这些个人信息。账单指标主要位于 `us-east-1`，业务资源仍部署在新加坡。

## 安全与数据边界

- 只允许 HTTP/HTTPS 公网 URL，拒绝本机、私网、云元数据地址和 URL 凭证。
- 重定向最多 3 次，每次重定向后重新解析 DNS 并检查地址。
- 来源内容会清除控制字符、限制长度，并仅作为模型待分析数据。
- 不执行网页或 Feed 中的提示词、工具调用或命令。
- 仅保留标题、来源摘要和必要元数据，不保存完整版权文章。
- 依赖版本锁定；更新依赖后应重新运行测试和安全审查。

## 删除 AWS 云资源（可选）

```bash
sam delete --stack-name global-ai-frontier-bot --region ap-southeast-1
aws cloudformation describe-stacks \
  --stack-name global-ai-frontier-bot \
  --region ap-southeast-1
```

第二条命令应返回 Stack 不存在。随后检查 Lambda、DynamoDB、EventBridge Scheduler、CloudWatch Logs、CloudWatch Alarms 和部署用 S3 存储桶。SAM 管理的栈资源会删除；`sam deploy --resolve-s3` 使用的托管制品桶可能由 SAM CLI 管理并被其他栈复用，需要在确认不再使用后单独清理。删除预算告警和手工创建的 SNS Topic，避免遗留资源。
