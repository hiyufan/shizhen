//! 小红书 / 抖音自带的实况（原图 + 短视频）原样打包：不裁剪、尽量不重编码。

use std::io::Write;
use std::path::{Path, PathBuf};

use super::{timestamps, unique_stem, Toolkit};
use crate::config::limits::LIVE_ITEM_MAX_BYTES;
use crate::error::{TaskError, TaskResult};
use crate::fsutil::{PartialFile, WorkDir};
use crate::jobs::{JobOutput, Progress};
use crate::media::livephoto::{self, MOV_IDENTIFIER_KEY};
use crate::media::{Container, Segment};
use crate::net::{safe_filename, Download};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LiveFormat {
    LivePhoto,
    MotionPhoto,
}

#[derive(Debug, Clone)]
pub struct LiveItem {
    pub image_url: String,
    pub video_url: String,
}

/// 打包好的一张：封面 JPG，和 iPhone 实况的 MOV（动态照片没有，视频已经接在 JPG 里）。
struct Pair {
    image: PathBuf,
    mov: Option<PathBuf>,
}

pub async fn pair_live(kit: &Toolkit, progress: &Progress, items: &[LiveItem], fmt: LiveFormat, title: &str) -> TaskResult<JobOutput> {
    let id = unique_stem();
    let stem: String = safe_filename(title, "", "live").chars().take(40).collect();
    let work = WorkDir::create(kit.outputs_dir.join(format!("{id}_work")))?;

    let total = items.len().max(1);
    let mut pairs = Vec::with_capacity(items.len());
    for (i, item) in items.iter().enumerate() {
        #[allow(clippy::cast_precision_loss)]
        progress.step(i as f64 / total as f64, &format!("正在处理第 {}/{total} 张", i + 1));
        pairs.push(pair_one(kit, &work, i + 1, item, fmt).await?);
    }

    progress.step(0.97, "正在打包");
    let output = match (fmt, pairs.as_slice()) {
        (LiveFormat::LivePhoto, _) => bundle_live(kit, &id, &stem, &pairs)?,
        (LiveFormat::MotionPhoto, [single]) => {
            let dest = kit.outputs_dir.join(format!("{id}_motion.jpg"));
            std::fs::rename(&single.image, &dest)?;
            (dest, format!("MVIMG_{stem}.jpg"))
        }
        (LiveFormat::MotionPhoto, _) => bundle_motion(kit, &id, &stem, &pairs)?,
    };
    Ok(JobOutput {
        result: Some(output.0),
        filename: Some(output.1),
        extra: serde_json::json!({ "count": pairs.len() }),
        ..JobOutput::default()
    })
}

async fn pair_one(kit: &Toolkit, work: &WorkDir, n: usize, item: &LiveItem, fmt: LiveFormat) -> TaskResult<Pair> {
    let raw_image = work.join(format!("{n:04}.img"));
    let video = work.join(format!("{n:04}.mp4"));
    let jpg = work.join(format!("{n:04}.jpg"));
    download(kit, &item.image_url, &raw_image).await?;
    download(kit, &item.video_url, &video).await?;

    // 平台给的可能是 webp / png / heic，实况配对只认 JPEG
    kit.ffmpeg.to_jpeg(&raw_image, &jpg).await?;
    let ident = livephoto::new_identifier();
    livephoto::write_jpeg_identifier(&jpg, &ident, &timestamps().0)?;

    if fmt == LiveFormat::MotionPhoto {
        let out = work.join(format!("MVIMG_{n:04}.jpg"));
        livephoto::write_motion_photo(&jpg, &video, &out, 0)?;
        return Ok(Pair { image: out, mov: None });
    }

    let mov = work.join(format!("{n:04}.MOV"));
    if kit.ffmpeg.remux_live(&video, &mov, &ident).await.is_err() {
        // 少数不是 H.264 的，重编码一次
        let probe = kit.ffmpeg.probe(&video).await?;
        let duration = if probe.duration > 0.0 { probe.duration } else { 3.0 };
        let meta = [(MOV_IDENTIFIER_KEY, ident.clone())];
        let seg = Segment { src: &video, dst: &mov, start: 0.0, duration, container: Container::Mov, metadata: &meta };
        kit.ffmpeg.segment(&seg, None).await?;
    }
    if !livephoto::mov_has_identifier(&mov, &ident) {
        return Err(TaskError::new("实况元数据写入失败"));
    }
    Ok(Pair { image: jpg, mov: Some(mov) })
}

async fn download(kit: &Toolkit, url: &str, dest: &Path) -> TaskResult<()> {
    kit.media
        .download(Download { url, headers: &[], dest, max_bytes: LIVE_ITEM_MAX_BYTES }, None)
        .await
}

fn bundle_live(kit: &Toolkit, id: &str, stem: &str, pairs: &[Pair]) -> TaskResult<(PathBuf, String)> {
    let prefix = id[..2].to_uppercase();
    let mut entries = Vec::new();
    for (i, p) in pairs.iter().enumerate() {
        let name = format!("IMG_{prefix}{:02}", i + 1);
        entries.push((format!("{name}.JPG"), p.image.as_path()));
        if let Some(mov) = &p.mov {
            entries.push((format!("{name}.MOV"), mov.as_path()));
        }
    }
    let dest = PartialFile::new(kit.outputs_dir.join(format!("{id}_live.zip")));
    zip_pairs(dest.path(), &entries)?;
    let count = if pairs.len() > 1 { format!("x{}", pairs.len()) } else { String::new() };
    Ok((dest.keep(), format!("{stem}_实况{count}.zip")))
}

fn bundle_motion(kit: &Toolkit, id: &str, stem: &str, pairs: &[Pair]) -> TaskResult<(PathBuf, String)> {
    let entries: Vec<(String, &Path)> = pairs
        .iter()
        .enumerate()
        .map(|(i, p)| (format!("MVIMG_{stem}_{}.jpg", i + 1), p.image.as_path()))
        .collect();
    let dest = PartialFile::new(kit.outputs_dir.join(format!("{id}_motion.zip")));
    zip_pairs(dest.path(), &entries)?;
    Ok((dest.keep(), format!("{stem}_动态照片x{}.zip", pairs.len())))
}

/// 打成不压缩的 zip：照片和视频本来就压缩过了，再压只是白费 CPU。
pub(super) fn zip_pairs(dest: &Path, entries: &[(String, &Path)]) -> TaskResult<()> {
    let file = std::fs::File::create(dest)?;
    let mut zip = zip::ZipWriter::new(file);
    let options = zip::write::SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    for (name, path) in entries {
        zip.start_file(name.as_str(), options).map_err(zip_error)?;
        zip.write_all(&std::fs::read(path)?)?;
    }
    zip.finish().map_err(zip_error)?;
    Ok(())
}

fn zip_error(e: zip::result::ZipError) -> TaskError {
    TaskError::new(format!("打包失败: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn zip_contains_entries_uncompressed() {
        let dir = std::env::temp_dir().join(format!("shizhen-zip-{}", rand::random::<u32>()));
        std::fs::create_dir_all(&dir).unwrap();
        let (a, b) = (dir.join("a"), dir.join("b"));
        std::fs::write(&a, b"AAAA").unwrap();
        std::fs::write(&b, b"BB").unwrap();
        let out = dir.join("x.zip");
        zip_pairs(&out, &[("IMG_1.JPG".into(), a.as_path()), ("IMG_1.MOV".into(), b.as_path())]).unwrap();
        let bytes = std::fs::read(&out).unwrap();
        assert!(bytes.windows(4).any(|w| w == b"AAAA"), "Stored 模式下内容原样可见");
        assert!(bytes.windows(9).any(|w| w == b"IMG_1.MOV"));
        std::fs::remove_dir_all(dir).ok();
    }
}
