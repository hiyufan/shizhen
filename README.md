# 求原图 · ESA 边缘中继

这个分支只放阿里云 ESA 的边缘函数 [`relay.js`](relay.js)，给「函数和Pages」绑定仓库自动部署用。网站和服务器代码在 [`main`](https://github.com/hiyufan/shizhen/tree/main) 分支，中继的作用和服务器配置见 [docs/esa-relay.md](https://github.com/hiyufan/shizhen/blob/main/docs/esa-relay.md)。

## 绑定仓库

1. ESA 控制台 → 边缘计算和 AI → 函数和Pages → 创建 → 导入 GitHub 仓库，选这个仓库。
2. 生产分支选 `esa-relay`；函数入口填 `relay.js`；安装命令、构建命令、静态资源目录都留空。
3. 在项目里加变量：
   - `RELAY_TOKEN`：和服务器的 `PARSE_VIDEO_RELAY_TOKEN` 一致。必填。
   - `PROBE_TOKEN`：要用 `/probe` 探测出口时才加，必须和 `RELAY_TOKEN` 不同；平时不加。
   口令类变量有「加密」选项时选上。仓库是公开的，真口令只放在 ESA 里，不要写进代码。
4. 部署成功后，把服务器 `PARSE_VIDEO_RELAY_CN` 里的域名绑到这个项目。

之后推到 `esa-relay` 分支，ESA 会自动构建并发布到生产环境。

## 确认口令生效

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'x-relay-token: <口令>' 'https://<域名>/relay?url='
```

- `400`：口令读到了，而且和请求里的一致（服务器保活用的就是这个请求）。
- `403`：变量生效了，但和请求里的口令不一致。
- `503`：函数没读到 `RELAY_TOKEN`，所有路径都拒绝服务。检查变量名，保存后重新部署一次。

函数会依次从 `fetch` 的参数、全局对象和 `process.env` 里找变量。都没有、或者代码里的 `TOKEN` 还是示例值时，一律回 503，不会拿公开的示例口令当真口令。

## 修改

改 `relay.js` 后推到这个分支即可。协议要和 `main` 上的 `src/qiuyuantu/convert/relay.py` 对应；服务器和边缘是先后上线的，两边的新旧版本要能混着跑。
