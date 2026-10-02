/**
 * 阿里云 ESA 边缘函数 / 边缘 Pages 函数：给拾帧当"国内出口"。
 *
 * 一个文件两个用途：
 *   /probe?token=<PROBE_TOKEN>&xhs=<小红书分享链接>
 *                                  探测：这个边缘节点的出口 IP、B站 API 状态、小红书页面是否有笔记数据。
 *                                  用单独的 PROBE_TOKEN（留空 = 关闭探测）：它会出现在浏览器地址栏和访问日志里，
 *                                  不能是下面那个同时管中继和图片签名的 TOKEN
 *   /relay?url=<目标地址>          中继：拾帧把国内平台的解析请求发到这里，由边缘节点代为访问
 *   /img?url=&e=&s=                图片：浏览器直接从国内边缘节点取国内平台的图（拾帧设 PARSE_VIDEO_EDGE_IMG=1 才会用）
 *   /media?url=&e=&s=[&dl=1&name=] 视频 / 音频：同上，支持拖进度条（Range）；dl=1 时带下载头（拾帧设 PARSE_VIDEO_EDGE_MEDIA=1 才会用）
 *
 * 部署（边缘函数）：ESA 控制台 → 边缘函数 → 新建 → 把本文件贴进去 → 改 TOKEN（要探测再填 PROBE_TOKEN）→
 *     部署后在「版本管理」里发布到生产环境（只点部署只到测试环境，绑定的域名还是旧版本）→ 绑定域名。
 * 部署（边缘 Pages）：把本文件放到项目的 functions/[[path]].js，把末尾的 export default 换成
 *     export function onRequest({ request }) { return handle(request); }
 *
 * 拾帧侧配置（服务器环境变量）：
 *     PARSE_VIDEO_RELAY_CN=https://你的函数域名/relay
 *     PARSE_VIDEO_RELAY_TOKEN=和下面 TOKEN 一样的字符串
 *
 * 中继只用于解析请求（网页 / API，几十到一两百 KB）；视频本体给浏览器的那一路可以走 /media，服务器自己做转换要的原视频仍直连 CDN。
 * Cloudflare Workers 也能原样跑这份代码（同样是 export default { fetch }），但它的出口在海外，B站 会 412，别用。
 */

const TOKEN = "change-me-to-a-long-random-string";
// 探测口令：和 TOKEN 不同的一串；留空就关闭 /probe（平时不用探测时建议留空）
const PROBE_TOKEN = "";

const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36";
const DROP_RESP = new Set(["content-encoding", "content-length", "transfer-encoding", "connection", "set-cookie"]);
const DROP_REQ = new Set(["host", "content-length", "connection", "accept-encoding", "x-relay-token", "x-relay-method", "x-relay-headers"]);

// ESA 要求 ES module：默认导出一个带 fetch 的对象
export default {
  async fetch(request, env, ctx) {
    return handle(request);
  },
};

async function handle(request) {
  const url = new URL(request.url);
  if (url.pathname.endsWith("/relay")) return relay(request, url);
  if (url.pathname.endsWith("/img")) return img(url);
  if (url.pathname.endsWith("/media")) return media(request, url);
  // 探测要带口令。以前任何路径都当探测：爬虫扫一次就让出口 IP 去打一次 B站 API（B站 风控的正是这个 IP），
  // 出口 IP 也公开了，?xhs= 还能让边缘节点替任何人抓任意网址
  // 口令不对和别的路径一样回 404，不让人知道这里有个探测
  if (url.pathname.endsWith("/probe") && PROBE_TOKEN && sameString(url.searchParams.get("token") || "", PROBE_TOKEN)) {
    return probe(url);
  }
  return new Response("not found", { status: 404 });
}

// UTF-8 安全的 base64
const b64e = (s) => btoa(unescape(encodeURIComponent(s)));
const b64d = (s) => decodeURIComponent(escape(atob(s)));

async function relay(request, url) {
  if (!sameString(request.headers.get("x-relay-token") || "", TOKEN)) return new Response("forbidden", { status: 403 });
  const target = url.searchParams.get("url") || "";
  if (!/^https?:\/\//i.test(target)) return new Response("bad url", { status: 400 });

  const method = (request.headers.get("x-relay-method") || "GET").toUpperCase();
  let headers = {};
  try { headers = JSON.parse(b64d(request.headers.get("x-relay-headers") || "e30=")); } catch (e) {}
  for (const k of Object.keys(headers)) if (DROP_REQ.has(k.toLowerCase())) delete headers[k];
  if (!headers["user-agent"] && !headers["User-Agent"]) headers["User-Agent"] = UA;

  const init = { method, headers, redirect: "manual" };   // 跳转交回给拾帧那边的 httpx 处理，每一跳都过它的 SSRF 检查
  if (!["GET", "HEAD"].includes(method)) init.body = await request.arrayBuffer();

  let resp;
  try {
    resp = await fetch(target, init);
  } catch (e) {
    return new Response(String(e), { status: 200, headers: { "x-relay-status": "599", "x-relay-error": String(e).slice(0, 200) } });
  }
  const pairs = [];
  resp.headers.forEach((v, k) => { if (!DROP_RESP.has(k.toLowerCase())) pairs.push([k, v]); });
  if (typeof resp.headers.getSetCookie === "function") {
    for (const c of resp.headers.getSetCookie()) pairs.push(["set-cookie", c]);
  } else if (resp.headers.get("set-cookie")) {
    pairs.push(["set-cookie", resp.headers.get("set-cookie")]);
  }
  const out = {
    "content-type": "application/octet-stream",
    "x-relay-status": String(resp.status),
    "x-relay-headers": b64e(JSON.stringify(pairs)),
  };
  const gzip = shouldGzip(request, resp);
  if (gzip) out["x-relay-encoding"] = "gzip";
  const body = gzip ? resp.body.pipeThrough(new CompressionStream("gzip")) : await resp.arrayBuffer();
  return new Response(body, { status: 200, headers: out });
}

// 回程压缩：fetch 拿到的是解压后的正文，原样回去要按原始大小跨一次太平洋（小红书笔记页 149KB，
// gzip 后 24KB）。不用标准的 content-encoding 头：各家边缘运行时对它有自己的处理（有的见了会自己再压一遍），
// 用自己的 x-relay-encoding；服务器带了 x-relay-accept: gzip 才压，新旧两边先后部署都不会坏
const COMPRESSIBLE = /^(text\/|application\/(json|javascript|x-javascript|xml|[\w.-]+\+(json|xml))\b)/i;

function shouldGzip(request, resp) {
  if (typeof CompressionStream !== "function" || !resp.body) return false;
  if (!/\bgzip\b/i.test(request.headers.get("x-relay-accept") || "")) return false;
  const len = Number(resp.headers.get("content-length") || 0);
  return COMPRESSIBLE.test(resp.headers.get("content-type") || "") && !(len > 0 && len < 1024);
}

// /img?url=&e=&s=  浏览器直接从这里取国内平台的图片，不用绕海外服务器跨两次太平洋。
// 只接受拾帧签过名、没过期的地址，只转白名单里的图片 CDN，只回 image/*，免得被当成通用代理 / 视频带宽。
// 白名单和服务器端 convert/relay.py 的 _EDGE_IMG_HOSTS 保持一致
const IMG_REFERERS = [
  ["xhscdn.com", "https://www.xiaohongshu.com/"],
  ["xiaohongshu.com", "https://www.xiaohongshu.com/"],
  ["douyinpic.com", "https://www.douyin.com/"],
  ["yximgs.com", "https://www.kuaishou.com/"],
  ["hdslb.com", "https://www.bilibili.com/"],
  ["sinaimg.cn", "https://weibo.com/"],
];

const imgReferer = (host) => (IMG_REFERERS.find(([s]) => host === s || host.endsWith("." + s)) || [])[1];

let hmacKey; // 导入一次留着用，不必每张图都 importKey

async function hmacHex(message) {
  hmacKey ||= crypto.subtle.importKey("raw", new TextEncoder().encode(TOKEN), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = new Uint8Array(await crypto.subtle.sign("HMAC", await hmacKey, new TextEncoder().encode(message)));
  return Array.from(mac, (b) => b.toString(16).padStart(2, "0")).join("");
}

function sameString(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// 服务器签的地址：签名对、没过期、在白名单里才放行。不放行返回 Response，放行返回 { target, ref }
async function checkSigned(url, kind, refererOf) {
  const target = url.searchParams.get("url") || "";
  const exp = Number(url.searchParams.get("e") || 0);
  const sig = url.searchParams.get("s") || "";
  if (!(exp > Date.now() / 1000)) return new Response("expired", { status: 403 });
  if (!sameString((await hmacHex(`${kind}\n${exp}\n${target}`)).slice(0, 32), sig)) return new Response("forbidden", { status: 403 });
  let ref;
  try { ref = refererOf(new URL(target).hostname); } catch (e) {}
  if (!ref || !/^https?:\/\//i.test(target)) return new Response("bad url", { status: 400 });
  return { target, ref };
}

async function img(url) {
  const ok = await checkSigned(url, "img", imgReferer);
  if (ok instanceof Response) return ok;
  const upstream = { "User-Agent": UA, Referer: ok.ref, Accept: "image/avif,image/webp,image/*,*/*;q=0.8" };
  let resp;
  try {
    resp = await fetchFollowing(ok.target, upstream);
  } catch (e) {
    return fetchError(e);
  }
  const ctype = resp.headers.get("content-type") || "";
  if (!resp.ok || !ctype.startsWith("image/")) return new Response("upstream " + resp.status, { status: 502 });
  const headers = {
    "content-type": ctype,
    "cache-control": "public, max-age=86400",
    "x-content-type-options": "nosniff",
    "access-control-allow-origin": "*", // iPhone 存相册用 fetch 读图（跨域）
  };
  // fetch 会自动解压：上游带了 content-encoding 时它的长度是压缩后的，和正文对不上，浏览器会截断或一直等
  const len = resp.headers.get("content-length");
  if (len && !resp.headers.get("content-encoding")) headers["content-length"] = len;
  return new Response(resp.body, { headers });
}

// 跳转自己跟，每一跳先查再请求。跳去哪不再按域名白名单卡：起点是签过名的平台地址，跳转是平台 CDN 发的，
// 外人控制不了；而平台的调度域名说换就换（2026-10-02 抖音 365yg.com 时不时跳到腾讯 *.v.smtcdns.com、
// 字节 *.bdcgslb.com，不在名单里的四成抖音视频直接 502）。只守底线：http(s)、不进内网 / 本机、最多跳 4 次；
// 回来的内容类型各接口自己再查。拦下时 502 里带上目标，排查一眼看出是哪
class Blocked extends Error {}

function privateIPv4(host) {
  const m = /^(\d+)\.(\d+)\.(\d+)\.(\d+)$/.exec(host);
  if (!m) return false;
  const [a, b] = [Number(m[1]), Number(m[2])];
  return a === 0 || a === 10 || a === 127 || a >= 224 || (a === 100 && b >= 64 && b < 128)
    || (a === 169 && b === 254) || (a === 172 && b >= 16 && b < 32) || (a === 192 && b === 168);
}

function hopAllowed(url) {
  const host = url.hostname.toLowerCase();
  if (!/^https?:$/.test(url.protocol) || host.startsWith("[")) return false;  // IPv6 字面量一律不跟
  if (host === "localhost" || /\.(localhost|local|internal)$/.test(host)) return false;
  return !privateIPv4(host);
}

async function fetchFollowing(target, headers) {
  let current = target;
  for (let hop = 0; hop < 4; hop++) {
    const resp = await fetch(current, { headers, redirect: "manual" });
    const location = resp.status >= 300 && resp.status < 400 && resp.headers.get("location");
    if (!location) return resp;
    const next = new URL(location, current);
    if (!hopAllowed(next)) throw new Blocked("redirect not allowed: " + next.protocol + "//" + next.hostname);
    current = next.href;
  }
  throw new Blocked("too many redirects");
}

const fetchError = (e) => new Response(e instanceof Blocked ? e.message : "fetch failed", { status: 502 });

// /media?url=&e=&s=[&dl=1&name=]  浏览器直接从这里看 / 下载国内平台的视频和音频：以前要「国内 CDN → 海外服务器 →
// 国内用户」跨两次太平洋，视频是全站流量的六成。只接受签过名的地址、只转白名单里的音视频 CDN、只回音视频，
// 透传 Range（拖进度条）。白名单和服务器端 convert/relay.py 的 _EDGE_MEDIA_HOSTS 保持一致
const DOUYIN = "https://www.douyin.com/";
const XHS = "https://www.xiaohongshu.com/";
const KUAISHOU = "https://www.kuaishou.com/";
const BILIBILI = "https://www.bilibili.com/";
const MEDIA_REFERERS = [
  ["douyinvod.com", DOUYIN], ["365yg.com", DOUYIN], ["zjcdn.com", DOUYIN], ["douyinstatic.com", DOUYIN],
  ["xhscdn.com", XHS], ["xiaohongshu.com", XHS],
  ["kwimgs.com", KUAISHOU], ["kwaicdn.com", KUAISHOU], ["ndcimgs.com", KUAISHOU], ["yximgs.com", KUAISHOU],
  ["bilivideo.com", BILIBILI], ["bilivideo.cn", BILIBILI],
  ["weibocdn.com", "https://weibo.com/"],
];
const mediaReferer = (host) => (MEDIA_REFERERS.find(([s]) => host === s || host.endsWith("." + s)) || [])[1];
const MEDIA_TYPES = /^(video\/|audio\/|application\/octet-stream|binary\/octet-stream)/i;
const PASS_HEADERS = ["content-type", "content-range", "accept-ranges", "last-modified", "etag"];

async function media(request, url) {
  const ok = await checkSigned(url, "media", mediaReferer);
  if (ok instanceof Response) return ok;
  const headers = { "User-Agent": UA, Referer: ok.ref, Accept: "*/*" };
  const range = request.headers.get("range");
  if (range) headers.Range = range;
  let resp;
  try {
    resp = await fetchFollowing(ok.target, headers);
  } catch (e) {
    return fetchError(e);
  }
  if (![200, 206].includes(resp.status) || !MEDIA_TYPES.test(resp.headers.get("content-type") || "")) {
    return new Response("upstream " + resp.status, { status: 502 });
  }
  const out = {
    "cache-control": "public, max-age=86400",
    "x-content-type-options": "nosniff",
    // iPhone 存相册要用 fetch 把整个文件读进来（跨域），进度条要读 content-length
    "access-control-allow-origin": "*",
    "access-control-expose-headers": "content-length, content-range, content-type",
  };
  for (const k of PASS_HEADERS) if (resp.headers.get(k)) out[k] = resp.headers.get(k);
  out["accept-ranges"] ||= "bytes";
  const len = resp.headers.get("content-length");
  if (len && !resp.headers.get("content-encoding")) out["content-length"] = len;
  // 跨域的 <a download> 浏览器不认，要下载就得边缘这边给下载头。文件名只是展示用，这里清洗一遍
  if (url.searchParams.get("dl") === "1") {
    const name = (url.searchParams.get("name") || "video.mp4").replace(/[\x00-\x1f"\\/:*?<>|]+/g, " ").trim().slice(0, 100) || "video.mp4";
    out["content-disposition"] = `attachment; filename*=UTF-8''${encodeURIComponent(name)}`;
  }
  return new Response(resp.body, { status: resp.status, headers: out });
}

async function probe(url) {
  const xhs = url.searchParams.get("xhs") || "";
  const out = {};

  // 出口 IP：先问国内的 ipip，不行再问 ip.sb
  for (const [name, u, pick] of [
    ["ipip", "https://myip.ipip.net/json", (j) => ({ ip: j.data?.ip, location: (j.data?.location || []).join(" ") })],
    ["ip.sb", "https://api.ip.sb/geoip", (j) => ({ ip: j.ip, location: `${j.country || ""} ${j.region || ""} ${j.isp || j.organization || ""}` })],
  ]) {
    try {
      const j = await (await fetch(u, { headers: { "User-Agent": "curl/8" } })).json();
      out.egress = { via: name, ...pick(j) };
      break;
    } catch (e) { out.egress = { error: String(e) }; }
  }

  try {
    const r = await fetch("https://api.bilibili.com/x/web-interface/view?bvid=BV1GJ411x7h7", {
      headers: { "User-Agent": UA, Referer: "https://www.bilibili.com/", Accept: "application/json" },
    });
    const text = await r.text();
    let code = null; try { code = JSON.parse(text).code; } catch (e) {}
    out.bilibili = { status: r.status, code, ok: r.status === 200 && code === 0, sample: text.slice(0, 80) };
  } catch (e) { out.bilibili = { error: String(e) }; }

  if (xhs) {
    // 边缘 / 机房 IP 常被小红书要求登录；带 ?cookie=<登录后的 Cookie 串（encodeURIComponent 过）> 再测一次
    const cookie = url.searchParams.get("cookie") || "";
    try {
      const headers = { "User-Agent": UA, Accept: "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9" };
      if (cookie) headers["Cookie"] = cookie;
      const r = await fetch(xhs, { headers, redirect: "follow" });
      const html = await r.text();
      const title = ((html.match(/<title>(.*?)<\/title>/s) || [])[1] || "").trim().slice(0, 60);
      const loginWall = r.url.includes("/login");
      out.xhs = { status: r.status, finalUrl: r.url.slice(0, 120), title, withCookie: !!cookie, loginWall,
                  hasNote: html.includes("noteDetailMap") && !r.url.includes("/404") && !loginWall };
      if (loginWall && !cookie) out.xhs.hint = "桌面页要求登录；拾帧实际用的是下面的手机分享页，看 xhsMobile";
    } catch (e) { out.xhs = { error: String(e) }; }
    // 拾帧在机房 / 边缘出口上走的是手机分享页（不要求登录），这个才是关键
    try {
      const headers = { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
                        Accept: "text/html,application/xhtml+xml", "Accept-Language": "zh-CN,zh;q=0.9" };
      if (cookie) headers["Cookie"] = cookie;
      const r = await fetch(xhs, { headers, redirect: "follow" });
      const html = await r.text();
      const title = ((html.match(/<title>(.*?)<\/title>/s) || [])[1] || "").trim().slice(0, 60);
      out.xhsMobile = { status: r.status, finalUrl: r.url.slice(0, 120), title, loginWall: r.url.includes("/login"),
                        hasNote: html.includes('"noteData"') && html.includes("imageList") && !r.url.includes("/404") && !r.url.includes("/login") };
    } catch (e) { out.xhsMobile = { error: String(e) }; }
  } else {
    out.xhs = "加 ?xhs=<小红书分享链接> 一起测";
  }
  out.verdict = out.bilibili?.ok ? "B站可用" : "B站不可用";
  if (typeof out.xhsMobile === "object" && !out.xhsMobile.error) out.verdict += out.xhsMobile.hasNote ? "，小红书可用（手机分享页）" : (out.xhs?.hasNote ? "，小红书可用（桌面页）" : "，小红书不可用");
  return new Response(JSON.stringify(out, null, 2), { headers: { "content-type": "application/json; charset=utf-8" } });
}
