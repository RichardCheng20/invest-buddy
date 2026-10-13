# Invest Buddy

开源的美股投研工作台。静态页面，数据由 GitHub Actions 写回仓库，再用 Cloudflare Workers 部署。页面是中文的。仓库里的持仓、自选和净值都是 **EXAMPLE**，拿去之前改成你自己的。

规则输出只供对照清单，不是投资建议，也不是下单指令。

## 免责声明

本软件按 MIT 许可证提供，见 [LICENSE](LICENSE)。作者和贡献者不提供投资、税务或法律建议。示例持仓、示例成本和示例止损都是假数据。行情、财报和模型输出可能缺失或错误。用真钱之前，你要自己核对券商账户，并自己承担亏损。

## 按这个顺序设置

1. **Fork** 这个仓库到你自己的 GitHub 账号。如果手里只有孤立分支，先用下面的 `scripts/publish.sh` 推到一个新建的空公开仓库，再从那个仓库 fork。
2. 编辑 `wrangler.toml`：把 `GH_REPO` 的 `YOUR_GITHUB_USER/invest-buddy` 改成 `你的用户名/你的仓库名`。`name` 是 Worker 的名字，可以改。Worker 会拒绝还留着 `YOUR_GITHUB_USER` 的配置。
3. **部署 Cloudflare Workers**。在仓库根目录：

   ```bash
   npx wrangler deploy
   ```

   不要加 `--assets=./site`。`wrangler.toml` 已经把 `site/` 作为静态资源，并且让 `/api/*` 先进入 `worker/index.js`。Workers Builds 的 Build command 用 `bash build.sh`，Deploy command 用 `npx wrangler deploy`。
4. **Cloudflare Access** 挡住整站。打开 [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)，Access → Applications → Add an application → Self-hosted。域名选部署完成后 Cloudflare 控制台里显示的那台 Worker 主机，或你自己绑定的域名，路径留空。身份源用 One-time PIN。策略 Allow，Include → Emails，填你自己的邮箱。
5. **三个 Worker secrets**（不要写进 Git）：

   ```bash
   npx wrangler secret put GH_WATCHLIST_TOKEN
   npx wrangler secret put ACCESS_TEAM_DOMAIN
   npx wrangler secret put ACCESS_AUD
   ```

   `GH_WATCHLIST_TOKEN` 是 Fine-grained personal access token，只勾你的这个仓库，Contents 和 Actions 都要 Read and write。`ACCESS_TEAM_DOMAIN` 只填主机名，例如 `your-team.cloudflareaccess.com`，不要加 `https://`。`ACCESS_AUD` 是该 Access 应用的 Audience (AUD) Tag，多个用英文逗号分隔。配完再执行一次 `npx wrangler deploy`。
6. **LLM 钥匙**。GitHub → 仓库 Settings → Secrets and variables → Actions → New repository secret。至少建 `LLM_API_KEY`。没有这把钥匙时，研究工作流直接跳过，不算失败。地址和模型名可以另建同名 secret，也可以不建，那时用 `config/research.yaml` 里的 DeepSeek 默认值。可选：`FRED_API_KEY`（没有就跳过宏观序列），`SEC_EDGAR_USER_AGENT`（SEC 要求的联系方式，例如 `Example example@example.com`）。
7. **SMTP**。同一页再加五个 Actions secrets：`SMTP_HOST`、`SMTP_PORT`（一般 `587`）、`SMTP_USER`、`SMTP_PASS`、`REPORT_TO`（收件邮箱，多个用英文逗号）。发送时刻在 `config/email.yaml`。
8. **导入自选股**。部署并登录 Access 之后，打开自选页，用 `+` 加入代码；或直接改 `data/watchlist.txt`（空格或换行分隔）。新代码若要计入主题，加到 `config/universe.yaml` 对应的 `tickers`。页面上的分组和这个文件是同一套。
9. **修改持仓**。编辑 `data/portfolio.yaml`：净值、现金、股数、`avg_cost`、`stop`、`entry_date`。示例行都标了 EXAMPLE，成本和止损是整数，不是真实成交。保存后在持仓详情里也可以改成本和止损，接口是 `POST /api/watchlist`，`{"op":"risk",...}`，只改这四个字段。本地或 Actions 再跑 `python scripts/refresh.py`，页面才会按新持仓重算。

本地预览：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/refresh.py --session auto
python -m http.server 8080 --directory site
```

打开 `http://127.0.0.1:8080`。静态预览没有 Access 登录，自选和「立即研究」的写入会失败，这是预期的。

## 密钥清单

| 名字 | 放在哪 | 用途 |
| --- | --- | --- |
| `GH_REPO` | `wrangler.toml` 的 `[vars]`，或 Worker 变量 | `用户名/仓库名`。占位符不能部署后还留着 |
| `GH_BRANCH` | 同上，默认 `main` | Contents API 和 workflow 的分支 |
| `GH_WATCHLIST_TOKEN` | Worker secret | 改自选、改持仓字段、触发刷新和立即研究 |
| `ACCESS_TEAM_DOMAIN` | Worker secret | Access 团队域名 |
| `ACCESS_AUD` | Worker secret | Access 应用受众 |
| `LLM_API_KEY` | Actions secret | 定时研究和「立即研究」。`DEEPSEEK_API_KEY` 只是别名，可以不建 |
| `LLM_BASE_URL` | Actions secret，可选 | 盖过 `config/research.yaml`。默认 `https://api.deepseek.com` |
| `LLM_MODEL_FAST` | Actions secret，可选 | 分析师和辩论。默认 `deepseek-flash` |
| `LLM_MODEL_DEEP` | Actions secret，可选 | 经理。默认 `deepseek-v4-pro` |
| `FRED_API_KEY` | Actions secret，可选 | 宏观序列 |
| `SEC_EDGAR_USER_AGENT` | Actions secret，可选 | SEC 要求的联系方式 |
| `SMTP_HOST` | Actions secret | 发信服务器 |
| `SMTP_PORT` | Actions secret | `587` 走 STARTTLS，`465` 走 SSL |
| `SMTP_USER` | Actions secret | 发信账号，也是 From |
| `SMTP_PASS` | Actions secret | 发信密码或应用专用密码 |
| `REPORT_TO` | Actions secret | 收件人 |

仓库里只应出现 `.env.example` 的空值。`.env`、`.dev.vars` 和 `.wrangler/` 已忽略。

## 页面

- 总览：环境分、净值、敞口、回撤、去杠杆计划和当天规则动作
- 全球市场、市场情绪、持仓、机会扫描、事件、决策、画像、框架、日报
- 自选可以加删。持仓里的股票不能从自选删掉
- 详情里的「立即研究」保留。它只跑当前代码，不推进每天的轮换

信号定义在 `content/signal_rules.md`，门槛在 `config/signal_rules.yaml`。宽度用观察列表，不是标普 500 全成分。

## 行情刷新

`.github/workflows/refresh.yml`：

| 用途 | UTC | 北京时间 |
| --- | --- | --- |
| 收盘后主跑 | 周一至周五 21:30 | 周二至周六 05:30 |
| 收盘后备跑 | 周一至周五 22:20 | 周二至周六 06:20 |
| 下午主跑 | 周一至周五 05:00 | 周一至周五 13:00 |
| 下午备跑 | 周一至周五 05:40 | 周一至周五 13:40 |

行情来自 Yahoo Finance，不需要钥匙。有变化时用 `GITHUB_TOKEN` 推回默认分支。默认分支若开了保护，要允许 `github-actions[bot]` 推送。

## AI 研究

研究走 OpenAI 兼容的 Chat Completions。四个设置是 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL_FAST`、`LLM_MODEL_DEEP`。环境变量非空时盖过 `config/research.yaml`。正式钥匙是 `LLM_API_KEY`。旧配置里的 `DEEPSEEK_API_KEY` 只在 `LLM_API_KEY` 为空时当作同一把钥匙。

默认示例是 DeepSeek：`https://api.deepseek.com`，快速模型 `deepseek-flash`，经理模型 `deepseek-v4-pro`。官方文档把 OpenAI 兼容地址写成 `https://api.deepseek.com`。主机名是 `api.deepseek.com` 时，脚本改用 TradingAgents 的 DeepSeek 客户端，这样思考模型来回传递 `reasoning_content` 不会在第二轮报错。

其它供应商只改这四个值。下面的地址对过各自的文档，模型名是文档里的例子，以你账号里开通的为准。

| 供应商 | `LLM_BASE_URL` | 快速模型示例 | 经理模型示例 |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com` | `deepseek-flash` | `deepseek-v4-pro` |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` | `gpt-4o` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o-mini` | `openai/gpt-4o` |
| Moonshot / Kimi（国内） | `https://api.moonshot.cn/v1` | `kimi-k2.6` | `kimi-k3` |
| Moonshot / Kimi（国际） | `https://api.moonshot.ai/v1` | `kimi-k2.6` | `kimi-k3` |
| 通义千问 / DashScope 兼容模式（北京，旧域名仍可用） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen3.8-flash` | `qwen3.8-max` |
| 通义千问（弗吉尼亚） | `https://dashscope-us.aliyuncs.com/compatible-mode/v1` | `qwen3.8-flash` | `qwen3.8-max` |
| 智谱 | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.7-flash` | `glm-5.3` |
| 本机 Ollama | `http://127.0.0.1:11434/v1` | 已 `ollama pull` 的模型名 | 另一只或同一只 |

可直接贴进 `.env` 或 Actions secrets 的例子（不要填真实钥匙）：

```bash
# DeepSeek（默认）
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL_FAST=deepseek-flash
LLM_MODEL_DEEP=deepseek-v4-pro

# OpenAI
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_FAST=gpt-4o-mini
LLM_MODEL_DEEP=gpt-4o

# 本机 Ollama。127.0.0.1 可以不填钥匙。Actions 的运行器访问不到你电脑上的 Ollama。
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL_FAST=qwen3:latest
LLM_MODEL_DEEP=qwen3:latest
```

千问如果已经切到业务空间域名，把 `LLM_BASE_URL` 换成 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`，钥匙必须和这个地域是同一把。Anthropic 官方接口不是 OpenAI 兼容格式。设 `LLM_PROVIDER=anthropic`，或把 `LLM_BASE_URL` 设成 `https://api.anthropic.com`，脚本会改走 TradingAgents 自带的 Anthropic 客户端。钥匙放 `LLM_API_KEY`，也可以只放 `ANTHROPIC_API_KEY`。模型名用 Anthropic 控制台里的 Claude ID。

定时在 `config/research.yaml` 的 `schedule`：`timezone`、`weekdays`（ISO，周一是 1）、`time`、`tolerance_minutes`。默认 `Asia/Shanghai` 的 `07:30`，周二到周六，容差 25 分钟。DeepSeek 折扣时段大约是北京时间 **00:30–08:30**，默认钟点落在这段里。中国没有夏令时。GitHub cron 读不到 yaml，所以工作流在这段 UTC 窗口里每 30 分钟醒一次，脚本用 zoneinfo 换算本地钟点，只有对准才调用模型。有夏令时的时区也由 zoneinfo 处理。把 `time` 改到这个 UTC 窗口之外时，必须同时改 `.github/workflows/research.yml` 的 cron。同一收盘日已经研究过就不再花一次钱。

结果直接写入 `data/research/<TICKER>.json`，并追加 `data/research/history/<TICKER>.jsonl`，然后提交。不读取、不回复任何 issue 评论，仓库里也不写死 issue 编号。

「立即研究」走 `POST /api/research`。先校验 Access，再用 `GH_WATCHLIST_TOKEN` 对「AI 研究」做 `workflow_dispatch`，并带上这只代码。它不受定时钟点限制，也不推进每天的轮换。同一只 30 分钟内只接受一次，全账户北京时间每天最多 10 次，次数记在 `data/research/dispatch.json`。

分析师和辩论用快速模型，经理用推理模型。费用按 `config/research.yaml` 里的标价估算，表里没有的模型记 0。单次最多 5 只，每只 7 分钟。TradingAgents 以依赖安装，许可证见 `NOTICE`。

本地只检查入库逻辑，不调用模型：

```bash
pip install PyYAML jsonschema
python scripts/research.py --self-check
```

## 邮件报告

`.github/workflows/email.yml` 生成 HTML 并经 SMTP 发送。邮件没有背景色，也没有白字；小节用粗体彩色文字，并带 emoji 前缀。

| 信 | 本地时间 | 时区 | UTC cron |
| --- | --- | --- | --- |
| 早报 | 08:00，周二至周六 | `Asia/Shanghai` | `0 0 * * 2-6` |
| 盘前 | 08:30，周一至周五 | `America/New_York` | `30 12 * * 1-5` 和 `30 13 * * 1-5` |

盘前的两个 UTC 时刻分别对应美东夏令时和冬令时。脚本用时区数据库看「现在是不是配置里的本地钟点」，对不上就退出且不发信。要改时间，编辑 `config/email.yaml` 的 `time`、`weekdays`、`timezone`。夏令时不要手改 UTC。

手动试发：Actions → 邮件报告 → Run workflow，kind 选 `morning` 或 `premarket`。`auto` 只在对准钟点时发送。

## 推到新建的空仓库

先在 GitHub 新建空的公开仓库，不要勾选 README、license 或 .gitignore。在这份目录里：

```bash
PUBLIC_REPO=https://github.com/YOU/invest-buddy.git bash scripts/publish.sh
```

脚本会拒绝没有 EXAMPLE 标记、或仍含个人身份字符串的目录，并只用一次新提交推到那个空仓库的 `main`。
