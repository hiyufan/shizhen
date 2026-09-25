//! 任务查询、取消和结果文件。

use std::sync::Arc;

use axum::extract::{Path, Query, Request, State};
use axum::response::Response;
use axum::Json;
use serde::Deserialize;
use serde_json::json;

use super::files::{content_type_for, send};
use super::{ClientIp, Shared};
use crate::error::{ApiError, ApiResult};
use crate::jobs::{Job, JobView};

fn job(state: &Shared, id: &str) -> ApiResult<Arc<Job>> {
    state
        .jobs
        .get(id)
        .ok_or_else(|| ApiError::not_found("任务不存在或已过期"))
}

pub async fn status(
    State(state): State<Shared>,
    Path(id): Path<String>,
) -> ApiResult<Json<JobView>> {
    Ok(Json(job(&state, &id)?.view()))
}

pub async fn cancel(
    State(state): State<Shared>,
    ClientIp(ip): ClientIp,
    Path(id): Path<String>,
) -> ApiResult<Json<serde_json::Value>> {
    let job = job(&state, &id)?;
    if !job.owner.is_empty() && job.owner != ip {
        return Err(ApiError::forbidden("只能取消自己的任务"));
    }
    Ok(Json(json!({ "cancelled": state.jobs.cancel(&id) })))
}

#[derive(Deserialize)]
pub struct FileQuery {
    #[serde(default)]
    inline: u8,
}

pub async fn file(
    State(state): State<Shared>,
    Path(id): Path<String>,
    Query(q): Query<FileQuery>,
    req: Request,
) -> ApiResult<Response> {
    let (path, filename) = job(&state, &id)?
        .result()
        .ok_or_else(|| ApiError::not_found("结果还没准备好"))?;
    let name = filename.unwrap_or_else(|| {
        path.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    });
    let download = (q.inline != 1).then_some(name.as_str());
    Ok(send(&path, content_type_for(&path), download, req).await)
}

/// 实况照片的封面图（zip 里的 JPG）。
pub async fn cover(
    State(state): State<Shared>,
    Path(id): Path<String>,
    req: Request,
) -> ApiResult<Response> {
    let path = job(&state, &id)?
        .cover()
        .ok_or_else(|| ApiError::not_found("没有预览"))?;
    Ok(send(&path, "image/jpeg", None, req).await)
}

/// 实况照片的 MOV，页面上按住预览用。
pub async fn motion(
    State(state): State<Shared>,
    Path(id): Path<String>,
    req: Request,
) -> ApiResult<Response> {
    let path = job(&state, &id)?
        .motion()
        .ok_or_else(|| ApiError::not_found("没有视频"))?;
    Ok(send(&path, "video/quicktime", None, req).await)
}
