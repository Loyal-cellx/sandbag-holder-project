#!/usr/bin/env bash
set -e

MSG="${1:-update}"

git add .
git commit -m "$MSG"
git push origin main

ssh loyal@dataworks "cd ~/sandbag && git pull origin main && cd sales-tracker && docker compose down && docker compose up -d --build && docker ps"
