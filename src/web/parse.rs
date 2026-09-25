//! 解析接口：`/api/parse`，以及上游兼容的两个旧接口。

use std::str::FromStr;

use axum::extract::{Query, State};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use super::{ClientIp, Shared};
use crate::error::ApiResult;
use crate::net::is_public_url;
use crate::parse::{ParseService, VideoDto};
use crate::stats::Event;

#[derive(Deserialize)]
pub struct ParseQuery {
    url: String,
}

/// 解析分享文本 / 链接，返回可下载的视频、图片和清晰度选项。
pub async fn api_parse(State(state): State<Shared>, ClientIp(ip): ClientIp, Query(q): Query<ParseQuery>) -> ApiResult<Response> {
    state.limits.parse.hit(&ip)?;

    let Some(share_url) = ParseService::extract_url(&q.url) else {
        record(&state, &ip, "", false, "unsupported", std::time::Duration::ZERO);
        return Ok(reject("没有找到链接，请粘贴完整的分享内容"));
    };
    if !is_public_url(&share_url, state.cfg.ssrf_dns).await {
        record(&state, &ip, &ParseService::platform_of(&share_url), false, "unsupported", std::time::Duration::ZERO);
        return Ok(reject("不支持这个地址"));
    }

    let outcome = state.parse.parse(&share_url).await;
    let reason = if outcome.from_cache { "cache" } else { outcome.reply.reason.as_deref().unwrap_or("") };
    record(&state, &ip, &outcome.source, outcome.reply.ok(), reason, outcome.elapsed);
    Ok(Json(outcome.reply.as_ref()).into_response())
}

fn reject(msg: &str) -> Response {
    Json(json!({ "code": 400, "msg": msg, "reason": "unsupported" })).into_response()
}

fn record(state: &Shared, ip: &str, source: &str, ok: bool, reason: &str, elapsed: std::time::Duration) {
    state.stats.record(Event::Parse { ip, source, ok, reason, elapsed });
}

/// 上游兼容：`/video/share/url/parse?url=`。
pub async fn legacy_share(State(state): State<Shared>, ClientIp(ip): ClientIp, Query(q): Query<ParseQuery>) -> ApiResult<Response> {
    state.limits.parse.hit(&ip)?;
    let Some(url) = ParseService::extract_url(&q.url) else {
        return Ok(Json(json!({ "code": 400, "msg": "未检测到有效的分享链接" })).into_response());
    };
    if !is_public_url(&url, state.cfg.ssrf_dns).await {
        return Ok(Json(json!({ "code": 400, "msg": "未检测到有效的分享链接" })).into_response());
    }
    Ok(legacy_reply(&state, state.parse.info(&url).await, &url))
}

#[derive(Deserialize)]
pub struct IdQuery {
    source: String,
    video_id: String,
}

/// 上游兼容：`/video/id/parse?source=douyin&video_id=`。
pub async fn legacy_id(State(state): State<Shared>, ClientIp(ip): ClientIp, Query(q): Query<IdQuery>) -> ApiResult<Response> {
    state.limits.parse.hit(&ip)?;
    let Ok(source) = alcedo::Source::from_str(&q.source) else {
        return Ok(Json(json!({ "code": 500, "msg": format!("不支持的平台: {}", q.source) })).into_response());
    };
    let result = state.parse.client().parse_id(source, &q.video_id).await;
    Ok(legacy_reply(&state, result, ""))
}

fn legacy_reply(state: &Shared, result: alcedo::Result<alcedo::VideoInfo>, share_url: &str) -> Response {
    match result {
        Ok(info) => {
            let data = VideoDto::from_info(&info, share_url, &state.signer);
            Json(json!({ "code": 200, "msg": "解析成功", "data": data })).into_response()
        }
        Err(e) => Json(json!({ "code": 500, "msg": e.to_string() })).into_response(),
    }
}
