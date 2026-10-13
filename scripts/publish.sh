#!/usr/bin/env bash
# 把当前这份开源目录推到一个新建的空公开仓库，历史里只有这一次提交。
# 先在 GitHub 新建空仓库，不要勾选 README 或 license。
# 用法：PUBLIC_REPO=https://github.com/YOU/invest-buddy.git bash scripts/publish.sh
set -euo pipefail

if [[ -z "${PUBLIC_REPO:-}" ]]; then
  echo "用法: PUBLIC_REPO=https://github.com/YOU/invest-buddy.git bash scripts/publish.sh" >&2
  echo "先新建空的公开仓库，不要勾选 README 或 license。" >&2
  exit 1
fi

root=$(cd "$(dirname "$0")/.." && pwd)
if ! grep -q "EXAMPLE" "$root/data/portfolio.yaml"; then
  echo "拒绝发布：data/portfolio.yaml 不是 EXAMPLE 示例账户。" >&2
  exit 1
fi
id_mail=$(printf '\x63\x68\x65\x6e\x67\x7a\x68\x69\x66\x75')
id_user=$(printf '\x52\x69\x63\x68\x61\x72\x64\x43\x68\x65\x6e\x67\x32\x30')
id_issue=$(printf '%s%s' 'AI 研究' '投递')
if grep -R -n -E "${id_mail}|${id_user}|${id_issue}" "$root" \
  --exclude-dir=.git --exclude-dir=site/data --exclude=publish.sh >/dev/null; then
  echo "拒绝发布：目录里还有个人身份或旧的 issue 投递标记。" >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/tree"
tar -C "$root" \
  --exclude=.git \
  --exclude=.venv \
  --exclude=__pycache__ \
  --exclude=.wrangler \
  --exclude=.env \
  -cf - . | tar -C "$work/tree" -xf -
git -C "$work/tree" init -b main
git -C "$work/tree" add .
git -C "$work/tree" \
  -c user.name="${GIT_AUTHOR_NAME:-invest-buddy}" \
  -c user.email="${GIT_AUTHOR_EMAIL:-invest-buddy@users.noreply.github.com}" \
  commit -m "Initial open-source invest-buddy."
git -C "$work/tree" remote add origin "$PUBLIC_REPO"
git -C "$work/tree" push -u origin main
echo "已推到 $PUBLIC_REPO"
