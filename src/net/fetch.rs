//! 拉 CDN 直链：给代理转发用，也给转换前把原视频下到本地用。

use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use futures_util::StreamExt;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use tokio::io::AsyncWriteExt;

use crate::error::{TaskError, TaskResult};
use crate::fsutil::PartialFile;
use crate::jobs::Span;

use super::referer::headers_for;

const MAX_REDIRECTS: usize = 8;

/// 拉媒体直链的客户端。进程内一个，连接池共用。
///
/// 播放器拖进度条会打一连串 Range 请求，每个都重新握手的话实测 ~1.4s/次；
/// 连接池复用后省掉握手和 TCP 慢启动。
#[derive(Debug, Clone)]
pub struct MediaClient {
    http: reqwest::Client,
}

/// 一次下载的描述。
#[derive(Debug)]
pub struct Download<'a> {
    pub url: &'a str,
    /// 解析器给的额外请求头（Referer / UA 等）
    pub headers: &'a [(String, String)],
    pub dest: &'a Path,
    pub max_bytes: u64,
}

impl MediaClient {
    pub fn new(proxy: Option<&str>, check_dns: bool) -> Result<Self, String> {
        let mut builder = reqwest::Client::builder()
            .dns_resolver(Arc::new(alcedo::http::ssrf::SafeResolver::new(check_dns)))
            // 每一跳都过一遍地址检查：外网地址 302 到内网也拦得住
            .redirect(reqwest::redirect::Policy::custom(|attempt| {
                if attempt.previous().len() >= MAX_REDIRECTS {
                    attempt.error("重定向太多")
                } else if alcedo::http::ssrf::url_allowed(attempt.url()) {
                    attempt.follow()
                } else {
                    attempt.error("源站跳转到了不允许的地址")
                }
            }))
            .connect_timeout(Duration::from_secs(30))
            .read_timeout(Duration::from_secs(120))
            .pool_idle_timeout(Duration::from_secs(300))
            .pool_max_idle_per_host(40)
            // 原样转发：视频本来就不压缩，压缩了的图片也得保持原字节给浏览器
            .no_gzip()
            .no_brotli()
            .no_zstd()
            .no_deflate();
        builder = match proxy {
            Some(p) => builder.proxy(reqwest::Proxy::all(p).map_err(|e| format!("代理地址无效: {e}"))?),
            None => builder.no_proxy(),
        };
        let http = builder.build().map_err(|e| format!("HTTP 客户端构造失败: {e}"))?;
        Ok(Self { http })
    }

    /// 发一个 GET，返回还没读正文的响应（代理转发时边读边写给浏览器）。
    pub async fn get(
        &self,
        url: &str,
        extra: &[(String, String)],
        range: Option<&str>,
    ) -> Result<reqwest::Response, reqwest::Error> {
        let mut headers = to_header_map(&headers_for(url, extra));
        if let Some(r) = range.and_then(|r| HeaderValue::from_str(r).ok()) {
            headers.insert(reqwest::header::RANGE, r);
        }
        self.http.get(url).headers(headers).send().await
    }

    /// 下载到文件。超过上限、出错、被取消都会删掉半截文件。
    pub async fn download(&self, d: Download<'_>, progress: Option<&Span>) -> TaskResult<()> {
        let resp = self
            .get(d.url, d.headers, None)
            .await
            .map_err(|e| TaskError::new(describe(&e)))?;
        if !resp.status().is_success() {
            return Err(TaskError::new(format!("源站拒绝了请求（HTTP {}）", resp.status().as_u16())));
        }
        let total = resp.content_length().unwrap_or(0);
        if total > d.max_bytes {
            return Err(too_large(d.max_bytes));
        }

        let partial = PartialFile::new(d.dest);
        let mut file = tokio::fs::File::create(partial.path()).await?;
        let mut body = resp.bytes_stream();
        let mut done = 0u64;
        while let Some(chunk) = body.next().await {
            let chunk = chunk.map_err(|e| TaskError::new(describe(&e)))?;
            done += chunk.len() as u64;
            if done > d.max_bytes {
                return Err(too_large(d.max_bytes));
            }
            file.write_all(&chunk).await?;
            if let (Some(p), true) = (progress, total > 0) {
                #[allow(clippy::cast_precision_loss)] // 只是进度条
                p.set(done as f64 / total as f64);
            }
        }
        file.flush().await?;
        partial.keep();
        Ok(())
    }
}

fn to_header_map(pairs: &[(String, String)]) -> HeaderMap {
    let mut map = HeaderMap::new();
    for (k, v) in pairs {
        if let (Ok(name), Ok(value)) = (HeaderName::try_from(k.as_str()), HeaderValue::from_str(v)) {
            map.insert(name, value);
        }
    }
    map
}

fn too_large(max: u64) -> TaskError {
    TaskError::new(format!("文件超过 {} MB 上限", max >> 20))
}

/// 给用户看的网络错误：只说类别，不把内部细节（地址、证书链）甩出去。
pub fn describe(e: &reqwest::Error) -> String {
    if e.is_timeout() {
        "源站响应超时".into()
    } else if e.is_redirect() {
        "源站跳转到了不允许的地址".into()
    } else if e.is_connect() {
        "连不上源站".into()
    } else {
        "拉取失败".into()
    }
}
