//! 健康检查和站长统计。

use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::extract::{Query, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{Html, IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{request_base, Shared};
use crate::error::{ApiError, ApiResult};
use crate::stats::SummaryQuery;
use crate::store::disk_usage;

/// 给负载均衡 / 监控用。
pub async fn health(State(state): State<Shared>) -> Json<Value> {
    let dirs = state.cfg.work_dirs();
    let used = tokio::task::spawn_blocking(move || disk_usage(&dirs)).await.unwrap_or(0);
    Json(json!({
        "ok": true,
        "jobs": state.jobs.stats(),
        "disk_used": used,
        "disk_quota": state.cfg.disk_quota_bytes,
        "cache": state.parse.cache_len(),
        "version": env!("CARGO_PKG_VERSION"),
    }))
}

/// 统计页的时间范围：(跨度, 每格宽度)。
const RANGES: &[(&str, u64, u64)] = &[
    ("24h", 86_400, 3_600),
    ("7d", 7 * 86_400, 86_400),
    ("30d", 30 * 86_400, 86_400),
    ("90d", 90 * 86_400, 86_400),
];

#[derive(Deserialize)]
pub struct StatsQuery {
    #[serde(default)]
    range: String,
    #[serde(default)]
    token: String,
    /// JS 的 getTimezoneOffset()：UTC 减本地时间的分钟数
    #[serde(default)]
    tz: i64,
}

/// 没配令牌、令牌不对时假装这个页面不存在。
fn authorize(state: &Shared, headers: &HeaderMap, token: &str) -> ApiResult<()> {
    let bearer = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer ").or_else(|| v.strip_prefix("bearer ")))
        .unwrap_or("");
    let given = if token.is_empty() { bearer } else { token };
    if state.stats.check_token(given) {
        Ok(())
    } else {
        Err(ApiError::not_found("Not Found"))
    }
}

/// 按时间分桶的使用量：浏览 / 解析 / 任务 / 下载 / 人数，以及各平台成功率。
pub async fn stats_api(State(state): State<Shared>, headers: HeaderMap, Query(q): Query<StatsQuery>) -> ApiResult<Json<Value>> {
    authorize(&state, &headers, &q.token)?;
    let (name, span, step) = RANGES.iter().find(|(n, ..)| *n == q.range).copied().unwrap_or(RANGES[0]);
    let tz_offset = (-q.tz * 60).clamp(-14 * 3600, 14 * 3600);
    let step_i = i64::try_from(step).unwrap_or(3600);

    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or(Duration::ZERO).as_secs_f64();
    #[allow(clippy::cast_precision_loss)] // 秒数，精度足够
    let since = {
        let raw = now - span as f64;
        // 对齐到桶的起点，最左一格才是完整的
        raw - (raw + tz_offset as f64).rem_euclid(step as f64)
    };
    let query = SummaryQuery { since, until: now, step: step_i, tz_offset };

    let stats = Arc::clone(&state.stats);
    let summary = tokio::task::spawn_blocking(move || {
        stats.flush()?;
        stats.summary(&query)
    })
    .await
    .map_err(|_| ApiError::internal("统计查询失败"))?
    .map_err(|e| ApiError::internal(format!("统计查询失败: {e}")))?;

    let mut body = serde_json::to_value(summary).map_err(|_| ApiError::internal("统计序列化失败"))?;
    body["range"] = json!(name);
    Ok(Json(body))
}

#[derive(Deserialize)]
pub struct PageQuery {
    #[serde(default)]
    token: String,
}

pub async fn stats_page(State(state): State<Shared>, headers: HeaderMap, Query(q): Query<PageQuery>) -> ApiResult<Response> {
    authorize(&state, &headers, &q.token)?;
    let base = state.site.base_url(&request_base(&headers, state.cfg.trust_proxy));
    let ranges: Vec<&str> = RANGES.iter().map(|(n, ..)| *n).collect();
    let html = state
        .site
        .stats(&q.token, &ranges, &base)
        .map_err(|e| ApiError::internal(format!("页面渲染失败: {e}")))?;
    let mut resp = (StatusCode::OK, Html(html)).into_response();
    let h = resp.headers_mut();
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    h.insert("x-robots-tag", HeaderValue::from_static("noindex"));
    Ok(resp)
}
