//! 把准备好的原视频转成 GIF、iPhone 实况或安卓动态照片。

use super::{timestamps, unique_stem, Toolkit};
use crate::config::limits::{GIF_MAX_SECONDS, LIVE_MAX_SECONDS};
use crate::error::{TaskError, TaskResult};
use crate::fsutil::PartialFile;
use crate::jobs::{JobOutput, Preview, Progress};
use crate::media::livephoto::{self, MOV_IDENTIFIER_KEY};
use crate::media::{Container, Dither, Gif, Segment};
use crate::net::safe_filename;
use crate::store::Source;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Gif,
    LivePhoto,
    MotionPhoto,
}

/// 前端传来的转换参数，已经过校验。
#[derive(Debug, Clone)]
pub struct ConvertSpec {
    pub format: Format,
    pub start: f64,
    pub end: Option<f64>,
    pub fps: u32,
    pub width: u32,
    pub dither: Dither,
    pub speed: f64,
    /// 实况的封面取哪一帧；不给就取中间
    pub key_time: Option<f64>,
}

pub async fn convert(
    kit: &Toolkit,
    progress: &Progress,
    src: &Source,
    spec: &ConvertSpec,
) -> TaskResult<JobOutput> {
    let stem = safe_filename(&src.title, "", "clip")
        .chars()
        .take(40)
        .collect::<String>();
    match spec.format {
        Format::Gif => gif(kit, progress, src, spec, &stem).await,
        Format::LivePhoto => live_photo(kit, progress, src, spec, &stem).await,
        Format::MotionPhoto => motion_photo(kit, progress, src, spec, &stem).await,
    }
}

async fn gif(
    kit: &Toolkit,
    progress: &Progress,
    src: &Source,
    spec: &ConvertSpec,
    stem: &str,
) -> TaskResult<JobOutput> {
    let (start, duration) = clamp_range(src.duration, spec.start, spec.end, GIF_MAX_SECONDS);
    let id = unique_stem();
    let out = PartialFile::new(kit.outputs_dir.join(format!("{id}.gif")));
    progress.step(0.05, "正在生成 GIF");
    let g = Gif {
        src: &src.path,
        dst: out.path(),
        start,
        duration,
        fps: spec.fps.clamp(4, 30),
        width: spec.width.clamp(120, 960),
        dither: spec.dither,
        speed: spec.speed,
    };
    kit.ffmpeg.gif(&g, &progress.span(0.05, 0.95)).await?;
    Ok(JobOutput {
        result: Some(out.keep()),
        filename: Some(format!("{stem}.gif")),
        preview: Some(Preview::File),
        ..JobOutput::default()
    })
}

/// iPhone 实况：一段 H.264 MOV + 一张封面 JPG，写入同一个标识，打成 zip。
async fn live_photo(
    kit: &Toolkit,
    progress: &Progress,
    src: &Source,
    spec: &ConvertSpec,
    stem: &str,
) -> TaskResult<JobOutput> {
    let (start, duration) = clamp_range(src.duration, spec.start, spec.end, LIVE_MAX_SECONDS);
    let id = unique_stem();
    let ident = livephoto::new_identifier();
    let (local, utc) = timestamps();
    let mov = PartialFile::new(kit.outputs_dir.join(format!("{id}.MOV")));
    let jpg = PartialFile::new(kit.outputs_dir.join(format!("{id}.JPG")));

    progress.step(0.05, "正在编码视频");
    let meta = [(MOV_IDENTIFIER_KEY, ident.clone()), ("creation_time", utc)];
    let seg = Segment {
        src: &src.path,
        dst: mov.path(),
        start,
        duration,
        container: Container::Mov,
        metadata: &meta,
    };
    kit.ffmpeg
        .segment(&seg, Some(&progress.span(0.05, 0.85)))
        .await?;

    progress.step(0.9, "正在生成封面");
    kit.ffmpeg
        .frame(
            &src.path,
            jpg.path(),
            key_frame(spec.key_time, start, duration),
        )
        .await?;
    livephoto::write_jpeg_identifier(jpg.path(), &ident, &local)?;
    let paired = livephoto::mov_has_identifier(mov.path(), &ident)
        && livephoto::read_jpeg_identifier(jpg.path()).as_deref() == Some(ident.as_str());
    if !paired {
        return Err(TaskError::new("实况元数据写入失败"));
    }

    let name = format!("IMG_{}", id[..4].to_uppercase());
    let bundle = PartialFile::new(kit.outputs_dir.join(format!("{id}_live.zip")));
    super::live::zip_pairs(
        bundle.path(),
        &[
            (format!("{name}.JPG"), jpg.path()),
            (format!("{name}.MOV"), mov.path()),
        ],
    )?;
    Ok(JobOutput {
        result: Some(bundle.keep()),
        filename: Some(format!("{stem}_实况.zip")),
        preview: Some(Preview::Cover),
        extra: serde_json::json!({ "identifier": ident }),
        cover: Some(jpg.keep()),
        motion: Some(mov.keep()),
    })
}

/// 安卓动态照片：封面 JPG 后面直接接上 MP4。
async fn motion_photo(
    kit: &Toolkit,
    progress: &Progress,
    src: &Source,
    spec: &ConvertSpec,
    stem: &str,
) -> TaskResult<JobOutput> {
    let (start, duration) = clamp_range(src.duration, spec.start, spec.end, LIVE_MAX_SECONDS);
    let id = unique_stem();
    let mp4 = PartialFile::new(kit.outputs_dir.join(format!("{id}_mp.mp4")));
    let jpg = PartialFile::new(kit.outputs_dir.join(format!("{id}_mp.jpg")));
    let out = PartialFile::new(kit.outputs_dir.join(format!("{id}_motion.jpg")));

    progress.step(0.05, "正在编码视频");
    let seg = Segment {
        src: &src.path,
        dst: mp4.path(),
        start,
        duration,
        container: Container::Mp4,
        metadata: &[],
    };
    kit.ffmpeg
        .segment(&seg, Some(&progress.span(0.05, 0.85)))
        .await?;

    progress.step(0.9, "正在合成动态照片");
    let still = key_frame(spec.key_time, start, duration);
    kit.ffmpeg.frame(&src.path, jpg.path(), still).await?;
    livephoto::write_jpeg_identifier(jpg.path(), &livephoto::new_identifier(), &timestamps().0)?;
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)] // 片段不超过 10 秒
    let presentation_us = ((still - start) * 1_000_000.0) as u64;
    livephoto::write_motion_photo(jpg.path(), mp4.path(), out.path(), presentation_us)?;
    Ok(JobOutput {
        result: Some(out.keep()),
        filename: Some(format!("MVIMG_{stem}.jpg")),
        preview: Some(Preview::File),
        ..JobOutput::default()
    })
}

/// 起止时间夹到视频范围内，长度不超过 `max_len`。返回 (起点, 时长)。
fn clamp_range(total: f64, start: f64, end: Option<f64>, max_len: f64) -> (f64, f64) {
    let start = start.max(0.0).min((total - 0.1).max(0.0));
    let end = end
        .unwrap_or(total)
        .min(total)
        .max(start + 0.1)
        .min(start + max_len);
    (start, end - start)
}

/// 封面帧：给了就夹到片段内，没给取中间。
fn key_frame(key: Option<f64>, start: f64, duration: f64) -> f64 {
    key.map_or(start + duration / 2.0, |k| {
        k.clamp(start, (start + duration - 0.05).max(start))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn range_is_clamped() {
        assert_eq!(clamp_range(20.0, 5.0, Some(8.0), 30.0), (5.0, 3.0));
        assert_eq!(
            clamp_range(20.0, 5.0, None, 10.0),
            (5.0, 10.0),
            "超长截到上限"
        );
        assert_eq!(clamp_range(20.0, -3.0, Some(2.0), 30.0), (0.0, 2.0));
        let (s, d) = clamp_range(20.0, 50.0, None, 30.0);
        assert!((s - 19.9).abs() < 1e-9 && d > 0.0, "起点越界夹到末尾前");
    }

    #[test]
    fn key_frame_defaults_to_middle() {
        assert!((key_frame(None, 2.0, 4.0) - 4.0).abs() < 1e-9);
        assert!((key_frame(Some(100.0), 2.0, 4.0) - 5.95).abs() < 1e-9);
        assert!((key_frame(Some(0.0), 2.0, 4.0) - 2.0).abs() < 1e-9);
    }
}
