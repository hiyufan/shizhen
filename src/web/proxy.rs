//! `/api/proxy`：把第三方直链转发给浏览器。
//!
//! 补 Referer / UA，透传 Range，可选加下载头。只转发带有效签名的地址（即
//! /api/parse 返回过的），不做开放代理。

use axum::body::Body;
use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::Response;
use futures_util::StreamExt;
use serde::Deserialize;

use super::{ClientIp, Shared};
use crate::error::{ApiError, ApiResult};
use crate::limits::looks_like_image;
use crate::net::{attachment_header, is_public_url, registrable_host, safe_filename};
use crate::stats::Event;

#[derive(Deserialize)]
pub struct ProxyQuery {
    url: String,
    #[serde(default)]
    filename: String,
    #[serde(default)]
    download: u8,
    #[serde(default)]
    sig: String,
}

/// 上游响应里原样带给浏览器的头。
const PASSTHROUGH: &[header::HeaderName] = &[
    header::CONTENT_TYPE,
    header::CONTENT_LENGTH,
    header::CONTENT_RANGE,
    header::ACCEPT_RANGES,
    header::LAST_MODIFIED,
    header::ETAG,
];

pub async fn api_proxy(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    headers: HeaderMap,
    Query(q): Query<ProxyQuery>,
) -> ApiResult<Response> {
    state.limits.proxy.hit(&ip)?;
    if !state.signer.verify(&q.url, &q.sig) {
        return Err(ApiError::forbidden("这个地址不是解析结果里的，拒绝转发"));
    }
    if !is_public_url(&q.url, state.cfg.ssrf_dns).await {
        return Err(ApiError::bad_request("不支持的地址"));
    }

    // 并发名额留给视频流（长连接、一直占带宽），图片不占
    let mut slot = if looks_like_image(&q.url) { None } else { Some(state.limits.streams.acquire(&ip)?) };

    let range = headers.get(header::RANGE).and_then(|v| v.to_str().ok());
    let upstream = state
        .kit
        .media
        .get(&q.url, &[], range)
        .await
        .map_err(|e| ApiError::new(StatusCode::BAD_GATEWAY, crate::net::describe_error(&e)))?;
    if upstream.status().as_u16() >= 400 {
        return Err(ApiError::new(upstream.status(), "源站拒绝了请求"));
    }
    let content_type = upstream.headers().get(header::CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
    if content_type.starts_with("image/") {
        slot = None; // 地址没认出来但其实是图片，早点把名额还回去
    }

    let mut resp = Response::builder().status(upstream.status());
    let out = resp.headers_mut().ok_or_else(|| ApiError::internal("响应构造失败"))?;
    for name in PASSTHROUGH {
        if let Some(v) = upstream.headers().get(name) {
            out.insert(name, v.clone());
        }
    }
    out.entry(header::ACCEPT_RANGES).or_insert(HeaderValue::from_static("bytes"));
    out.insert(header::CACHE_CONTROL, HeaderValue::from_static("private, max-age=3600"));
    if q.download == 1 {
        out.insert(header::CONTENT_DISPOSITION, attachment(&q.filename, &content_type));
        state.stats.record(Event::Download { ip: &ip, source: &registrable_host(&q.url) });
    }

    // 名额跟着响应体走：传完、出错或者浏览器断开，流被 drop 时归还
    let body = upstream.bytes_stream().map(move |chunk| {
        let _held = &slot;
        chunk
    });
    resp.body(Body::from_stream(body)).map_err(|_| ApiError::internal("响应构造失败"))
}

/// `attachment; filename*=UTF-8''...`，没给文件名按类型补一个扩展名。
fn attachment(filename: &str, content_type: &str) -> HeaderValue {
    let ext = if content_type.contains("video") {
        "mp4"
    } else if content_type.contains("jpeg") {
        "jpg"
    } else if content_type.contains("png") {
        "png"
    } else if content_type.contains("webp") {
        "webp"
    } else {
        ""
    };
    let mut name = if filename.is_empty() { safe_filename("media", ext, "media") } else { filename.to_owned() };
    let tail: String = name.chars().rev().take(5).collect();
    if !ext.is_empty() && !name.to_lowercase().ends_with(&format!(".{ext}")) && !tail.contains('.') {
        name = format!("{name}.{ext}");
    }
    HeaderValue::from_str(&attachment_header(&name)).unwrap_or_else(|_| HeaderValue::from_static("attachment"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn attachment_names() {
        let v = attachment("标题_720p.mp4", "video/mp4");
        assert!(v.to_str().unwrap().starts_with("attachment; filename*=UTF-8''%E6%A0%87"));
        assert!(attachment("", "image/jpeg").to_str().unwrap().ends_with("media.jpg"));
        assert!(attachment("cover", "image/png").to_str().unwrap().ends_with("cover.png"));
    }
}
