# 自动部署与上线清单

基础部署（`docker compose up -d`）见[自部署与配置](self-hosting.md#生产部署)。这里是在那之上的两件事：push 到 main 自动上线，以及公开上线后要做的收录工作。

## 自动部署（CI/CD）

普通抖音账号助手单独运行，部署入口见 [apps/douyin-inbox/](../apps/douyin-inbox/README.md)。网站 CI 不启动或重启账号浏览器。

push 到 main 且修改了核心或 Web 运行文件，测试通过就自动上线。仅修改 `apps/douyin-inbox/` 或文档时完成检查，不重新部署网站。

`.github/workflows/ci.yml`：每次 push / PR 分别运行核心和账号助手的离线测试、语法检查、README 嵌入同步检查和部署脚本的 shellcheck，改到 `Dockerfile` / 依赖时再构建一遍镜像；push 到 main、涉及核心 / Web 运行文件且都通过，就 SSH 到服务器跑 `scripts/deploy.sh`：等进行中的转换任务做完 → 打回滚标签 → 构建上线 → 自检（容器健康、首页、`/api/health`、公网首页），不过就自动退回上一个镜像。回滚镜像留最近 3 个，日志在 Actions 里和服务器的 `/var/log/shizhen-deploy.log`。

部署密钥在服务器上被锁死成只能跑部署脚本，开不了 shell、转发不了端口；SSH 带过去的只被当成要部署的提交 SHA，且必须是 GitHub 上 main 里的提交：

```bash
ssh-keygen -t ed25519 -N "" -C shizhen-actions-deploy -f deploy_key
echo "restrict,command=\"$PWD/scripts/deploy.sh\" $(cat deploy_key.pub)" >> ~/.ssh/authorized_keys
gh secret set DEPLOY_SSH_KEY < deploy_key                  # 私钥传上去后在服务器上删掉
gh secret set DEPLOY_HOST --body "<服务器 IP>"
for f in /etc/ssh/ssh_host_*_key.pub; do echo "<服务器 IP> $(cut -d' ' -f1,2 "$f")"; done | gh secret set DEPLOY_KNOWN_HOSTS
```

`SITE_URL` 默认 `https://ynvan.com`，自检会访问它，部署别的域名记得改。手动部署：`scripts/deploy.sh`（GitHub 上 main 的最新提交）、`FORCE=1 scripts/deploy.sh`（重新构建一遍）。

## 上线后：让搜索引擎收录

- 设置 `PARSE_VIDEO_SITE_URL`，canonical、sitemap、OG 图都靠它生成正式地址
- 在百度 / Google / Bing 站长平台验证站点（验证用的 `<meta>` 填进 `PARSE_VIDEO_SITE_VERIFICATION`）并提交 `/sitemap.xml`
- 运行 `python scripts/push_urls.py` 向百度主动推送
- 国内服务器需要 ICP 备案

## 搜索与推广效果

配置 `PARSE_VIDEO_STATS_TOKEN` 后，`/stats` 展示搜索与推广来源、落地页及后续操作；新版本上线后开始采集，历史来源不能回填。推广链接生成、统计口径和三家搜索站长平台的报表入口见[搜索表现与推广来源](analytics.md)。
