# 投资者画像（EXAMPLE）

> 这是开源示例页，不是任何人的真实账户。把本页和 `data/portfolio.yaml` 改成你自己的描述。

## 示例参数

| 维度 | 示例 |
|---|---|
| 账户标记 | EXAMPLE |
| 净值 | 100,000 美元 |
| 现金 | 40,000 美元，不用融资 |
| 单票上限 | 净值的 10% |
| 主题上限 | 净值的 25% |
| 总敞口 | ≤ 100% |
| 单笔风险 | 短线 0.5%，中线 1% |
| 止损合计 | ≤ 净值的 4% |

示例持仓是 AAPL、MSFT、NVDA、AMZN、GOOGL、JPM、UNH、XOM。股数和成本写在 `data/portfolio.yaml`，都标了 EXAMPLE。

## 怎么改成你的

1. 用你的券商截图或导出，改 `data/portfolio.yaml` 的净值、现金、股数、成本和止损。
2. 自选股改 `data/watchlist.txt`，大约保持在你跟得住的数量。新代码到 `config/universe.yaml` 登记主题。
3. 跑 `python scripts/refresh.py`，或等 GitHub Actions 刷新。页面上的市值以行情为准，不要手填价格。
