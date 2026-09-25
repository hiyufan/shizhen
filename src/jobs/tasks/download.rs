//! 需要服务端合并的清晰度（B站 1080p、YouTube 高清等）：合并好给用户下载。

use super::{materialize, unique_stem, MediaSource, Toolkit};
use crate::error::TaskResult;
use crate::jobs::{JobOutput, Progress};
use crate::net::safe_filename;

pub async fn download_for_user(kit: &Toolkit, progress: &Progress, media: &MediaSource, title: &str) -> TaskResult<JobOutput> {
    progress.step(0.02, "正在下载");
    let stem = format!("dl_{}", unique_stem());
    let path = materialize(kit, media, &kit.outputs_dir, &stem, &progress.span(0.02, 0.98)).await?;
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("mp4").to_owned();
    Ok(JobOutput {
        filename: Some(safe_filename(title, &ext, "video")),
        result: Some(path),
        ..JobOutput::default()
    })
}
