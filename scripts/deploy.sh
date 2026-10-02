#!/usr/bin/env bash
# 部署：等手上的任务做完 → 构建上线 → 自检，不过就回滚到上一个镜像。
#
# 平时由 GitHub Actions 在 CI 通过后 SSH 进来触发（.github/workflows/ci.yml 的 deploy）：
# 那把部署密钥在 authorized_keys 里被锁死成只能跑本脚本（command=，restrict），
# 登进来开不了 shell、转发不了端口；SSH 带过来的命令只被当成要部署的提交 SHA，
# 而且必须是 GitHub 上 main 里的提交。钥匙丢了，别人最多让服务器重新部署一遍
# main 上已有的代码。
#
# 手动用法：
#   scripts/deploy.sh               部署 GitHub 上 main 的最新提交（不查 CI，自己负责）
#   scripts/deploy.sh <sha>         部署 main 上指定的提交
#   FORCE=1 scripts/deploy.sh       已经是最新也重新构建上线一遍
#   SIMULATE_FAILURE=1 FORCE=1 ...  假装自检没过，演练回滚
# 日志同时写在 /var/log/shizhen-deploy.log。
#
# 只拿健康检查和首页判断成败；真实链接的解析只记日志不回滚——抖音这些平台本身
# 时好时坏，拿它们定生死会误回滚。
#
# 线上跑的是哪个提交记在 .git/deployed-sha，不看 HEAD：服务器上这份代码也拿来开发，
# 本地一 commit HEAD 就变了，但容器里还是旧镜像。
#
# 所有逻辑都在函数里、最后一行才调用：部署时 git merge 会改写本文件，bash 是边读
# 边执行的，这样能保证整个文件先读完。
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
SERVICE=app
CONTAINER=shizhen-app-1
IMAGE=shizhen-app
LOCAL_URL=http://127.0.0.1:8000
SITE_URL=${SITE_URL:-https://ynvan.com}
KEEP_ROLLBACKS=${KEEP_ROLLBACKS:-3}
IDLE_WAIT_SECONDS=${IDLE_WAIT_SECONDS:-600}
LOG_FILE=${LOG_FILE:-/var/log/shizhen-deploy.log}
LOCK_FILE=${LOCK_FILE:-/run/shizhen-deploy.lock}
# 冒烟测试用的链接（只记日志）：一个 B 站视频、一个抖音视频
SMOKE_URLS=${SMOKE_URLS:-"https://www.bilibili.com/video/BV1GJ411x7h7 https://v.douyin.com/pe2jeHe8nDs/"}

log() { echo "[deploy $(date '+%F %T')] $*" || true; }

# 有转换 / 打包任务在跑就等它们做完再重启，最多等 IDLE_WAIT_SECONDS
wait_idle() {
  local deadline=$((SECONDS + IDLE_WAIT_SECONDS)) busy
  while (( SECONDS < deadline )); do
    busy=$(curl -fsS -m 5 "$LOCAL_URL/api/health" | jq '.jobs.running + .jobs.pending' 2>/dev/null) || return 0
    [[ "$busy" == 0 ]] && return 0
    log "还有 $busy 个任务在跑，等一会儿"
    sleep 10
  done
  log "等了 ${IDLE_WAIT_SECONDS}s 任务还没做完，照常部署"
}

# 上线后自检：容器 healthy、本机首页和 /api/health 正常、公网首页 200
verify() {
  local deadline=$((SECONDS + 120)) health=missing
  while (( SECONDS < deadline )); do
    health=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo missing)
    [[ "$health" == healthy ]] && break
    sleep 3
  done
  [[ "$health" == healthy ]] || { log "容器状态 $health"; return 1; }
  curl -fsS -m 10 -o /dev/null "$LOCAL_URL/" || { log "本机首页打不开"; return 1; }
  curl -fsS -m 10 "$LOCAL_URL/api/health" | jq -e '.ok == true' >/dev/null || { log "/api/health 不正常"; return 1; }
  curl -fsS -m 15 -o /dev/null "$SITE_URL/" || { log "公网首页 $SITE_URL 打不开"; return 1; }
  if [[ -n "${SIMULATE_FAILURE:-}" ]]; then
    log "SIMULATE_FAILURE：假装自检没过（演练回滚）"
    return 1
  fi
}

smoke() {
  local url result
  for url in $SMOKE_URLS; do
    result=$(curl -fsS -m 90 -G "$LOCAL_URL/api/parse" --data-urlencode "url=$url" \
      | jq -r '"code=\(.code) \(.reason // "") 视频=\(.data.video_url // "" | length > 0) 图=\(.data.images // [] | length)"' 2>/dev/null) \
      || result="请求失败"
    log "冒烟 $url → $result"
  done
}

# 只换回旧镜像，不动代码：工作区里可能有还没推送的提交，reset 会把它们弄丢。
# 记录改回旧提交，下次部署同一个提交会重新构建再试。
rollback() {
  local prev=$1
  log "回滚到 ${prev:0:7}"
  docker tag "$IMAGE:rollback-${prev:0:7}" "$IMAGE:latest"
  docker compose up -d --no-build "$SERVICE"
  echo "$prev" > "$STATE_FILE"
  # 演练开关只管上线那次自检，回滚后的自检要真查
  if SIMULATE_FAILURE='' verify; then
    log "回滚完成，线上是 ${prev:0:7}"
  else
    log "回滚后自检仍然不过，需要人工处理"
  fi
}

# 回滚标签只留最近 KEEP_ROLLBACKS 个；本项目没人用的旧镜像（每个 ~2.5GB）一并清掉
prune_images() {
  local old tag
  old=$(docker images "$IMAGE" --format '{{.CreatedAt}}\t{{.Tag}}' | grep -P '\trollback-' | sort -r \
    | tail -n +$((KEEP_ROLLBACKS + 1)) | cut -f2) || true
  for tag in $old; do
    docker rmi -f "$IMAGE:$tag" >/dev/null && log "清掉旧回滚镜像 $tag"
  done
  docker image prune -f --filter "label=com.docker.compose.project=shizhen" >/dev/null
}

main() {
  # GitHub 那头断了（任务被取消、网络抖）也要把部署做完，半路被杀会停在「打了标签
  # 没重启」：先忽略 HUP / PIPE（忽略会被子进程继承，所以要在起 tee 之前），输出写
  # 不回去就只写日志文件（tee -p 遇到断掉的管道不退出）
  trap '' HUP PIPE
  exec > >(tee -p -a "$LOG_FILE") 2>&1

  cd "$REPO_DIR"
  exec 9>"$LOCK_FILE"
  flock -w 900 9 || { log "等了 15 分钟上一次部署还没结束"; return 1; }

  local requested=${1:-${SSH_ORIGINAL_COMMAND:-}} deployed head target
  STATE_FILE=$(git rev-parse --absolute-git-dir)/deployed-sha
  git fetch -q origin main
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[0-9a-f]{40}$ ]]; then
      log "只接受 40 位提交 SHA，拒绝：${requested:0:60}"
      return 2
    fi
    if ! git cat-file -e "$requested^{commit}" 2>/dev/null \
        || ! git merge-base --is-ancestor "$requested" origin/main; then
      log "${requested:0:7} 不是 GitHub 上 main 里的提交，拒绝"
      return 2
    fi
    target=$requested
  else
    target=$(git rev-parse origin/main)
  fi
  head=$(git rev-parse HEAD)
  deployed=$(cat "$STATE_FILE" 2>/dev/null || echo "$head")

  if [[ "$deployed" == "$target" && -z "${FORCE:-}" ]]; then
    log "线上已经是 ${target:0:7}，不用部署"
    return 0
  fi
  if [[ "$deployed" != "$target" ]] && git merge-base --is-ancestor "$target" "$deployed"; then
    log "线上的 ${deployed:0:7} 比 ${target:0:7} 还新，跳过"   # 先触发的部署后到了
    return 0
  fi
  if ! git diff --quiet || ! git diff --cached --quiet; then
    log "工作区有没提交的改动，不自动部署（构建用的就是工作区）"
    return 1
  fi
  # 构建用的是工作区，所以它得正好是 target：落后就快进；本地已经提交到 target 就直接用
  if [[ "$head" != "$target" ]] && ! git merge-base --is-ancestor "$head" "$target"; then
    if git merge-base --is-ancestor "$head" origin/main; then
      log "工作区已经在 ${head:0:7}，比 ${target:0:7} 新，等它自己的那次部署"
      return 0
    fi
    log "工作区的 ${head:0:7} 有没推送到 GitHub 的提交，不自动部署"
    return 1
  fi

  log "部署 ${deployed:0:7} → ${target:0:7}：$(git log -1 --format=%s "$target")"
  wait_idle
  docker tag "$IMAGE:latest" "$IMAGE:rollback-${deployed:0:7}"
  git merge -q --ff-only "$target"
  # 依赖层走缓存，镜像里的 yt-dlp 每天最多刷新一次（见 Dockerfile 的 YTDLP_REFRESH）
  if ! YTDLP_REFRESH=$(date +%F) docker compose up -d --build "$SERVICE" || ! verify; then
    log "上线失败"
    rollback "$deployed"
    return 1
  fi
  echo "$target" > "$STATE_FILE"
  log "上线成功 ${target:0:7}"
  smoke
  prune_images
}

main "$@"
