#!/bin/sh
# Workers Builds 构建命令保持这一条。静态页在 site/，接口在 worker/。
set -e
test -f site/index.html
test -f site/data/meta.json
test -f wrangler.toml
test -f worker/index.js
test -f worker/watchlist-core.mjs
test -f worker/portfolio-core.mjs
test -f worker/research-core.mjs
echo "static site ok"
