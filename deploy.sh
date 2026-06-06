#!/usr/bin/env bash
set -e

# Deploy target is kept out of the repo. Create a local, gitignored `.deploy.env`
# from `.deploy.env.example` with your own values:
#   DEPLOY_HOST=user@host
#   DEPLOY_PATH=~/your-project-dir
[ -f .deploy.env ] && source .deploy.env

MSG="${1:-update}"
: "${DEPLOY_HOST:?Set DEPLOY_HOST (e.g. in .deploy.env — see .deploy.env.example)}"
DEPLOY_PATH="${DEPLOY_PATH:-~/sandbag}"

git add .
git diff --cached --quiet || git commit -m "$MSG"
git push origin main

ssh "$DEPLOY_HOST" "cd $DEPLOY_PATH && git pull origin main && cd sales-tracker && docker compose down && docker compose up -d --build && docker ps"
