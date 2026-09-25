//! alcedo 的解析结果 -> 前端现有的 JSON。
//!
//! 字段和 Python 版（`dataclasses.asdict(VideoInfo)` + `share_url` / `sig` / `edge`）
//! 一一对应，前端 JS 不用改。清晰度菜单的取舍也在这里：前端只展示"比直接播放的
//! 那条更好"的档位。

use std::collections::BTreeMap;

use alcedo::{Format, VideoInfo};
use serde::Serialize;

use super::token::MediaToken;
use crate::net::{EdgeImages, Signer};

#[derive(Debug, Clone, Serialize)]
pub struct VideoDto {
    pub video_url: String,
    pub cover_url: String,
    pub title: String,
    pub music_url: String,
    pub images: Vec<ImageDto>,
    pub author: AuthorDto,
    pub source: String,
    pub page_url: String,
    pub duration: f64,
    pub width: u32,
    pub height: u32,
    pub formats: Vec<FormatDto>,
    pub video_headers: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ImageDto {
    pub url: String,
    pub live_photo_url: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct AuthorDto {
    pub uid: String,
    pub name: String,
    pub avatar: String,
}

/// 一个清晰度选项。`url` 非空就是直链，前端直接走代理下载；否则 `format_spec`
/// 是签好名的 [`MediaToken`]，交给服务端合并。
#[derive(Debug, Clone, Serialize)]
pub struct FormatDto {
    pub label: String,
    pub format_spec: String,
    pub ext: String,
    pub height: u32,
    pub filesize: u64,
    pub url: String,
    pub codec: String,
}

/// `/api/parse` 成功时的 `data`：结果本身 + 分享地址 + 每个直链的签名 + 边缘取图地址。
#[derive(Debug, Clone, Serialize)]
pub struct ParsedDto {
    #[serde(flatten)]
    pub video: VideoDto,
    pub share_url: String,
    pub sig: BTreeMap<String, String>,
    #[serde(skip_serializing_if = "BTreeMap::is_empty")]
    pub edge: BTreeMap<String, String>,
}

impl VideoDto {
    pub fn from_info(info: &VideoInfo, share_url: &str, signer: &Signer) -> Self {
        let headers: Vec<(String, String)> = info.video_headers.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        Self {
            video_url: info.video_url.clone(),
            cover_url: info.cover_url.clone(),
            title: info.title.clone(),
            music_url: info.music_url.clone(),
            images: info
                .images
                .iter()
                .map(|i| ImageDto { url: i.url.clone(), live_photo_url: i.live_photo_url.clone() })
                .collect(),
            author: AuthorDto {
                uid: info.author.uid.clone(),
                name: info.author.name.clone(),
                avatar: info.author.avatar.clone(),
            },
            source: info.source.map(|s| s.as_str().to_owned()).unwrap_or_default(),
            page_url: if info.page_url.is_empty() { share_url.to_owned() } else { info.page_url.clone() },
            duration: info.duration,
            width: info.width,
            height: info.height,
            formats: menu(info, &headers, signer),
            video_headers: info.video_headers.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
        }
    }

    /// 所有要签名的地址。令牌自带签名，不在这里。
    fn urls<'a>(&'a self, share_url: &'a str) -> Vec<&'a str> {
        let mut urls = vec![self.video_url.as_str(), &self.cover_url, &self.music_url, &self.page_url, share_url];
        for i in &self.images {
            urls.push(&i.url);
            urls.push(&i.live_photo_url);
        }
        urls.extend(self.formats.iter().map(|f| f.url.as_str()));
        urls.retain(|u| !u.is_empty());
        urls
    }
}

impl ParsedDto {
    /// `edge_expires_at` 是边缘取图地址的过期时刻（Unix 秒），要比结果缓存活得久。
    pub fn new(video: VideoDto, share_url: &str, signer: &Signer, edge: Option<(&EdgeImages, u64)>) -> Self {
        let sig = video.urls(share_url).into_iter().map(|u| (u.to_owned(), signer.sign(u))).collect();
        let edge = edge
            .map(|(e, exp)| {
                let images = std::iter::once(&video.cover_url).chain(video.images.iter().map(|i| &i.url));
                images.filter_map(|u| Some((u.clone(), e.url_for(u, exp)?))).collect()
            })
            .unwrap_or_default();
        Self { video, share_url: share_url.to_owned(), sig, edge }
    }
}

/// 清晰度菜单。
///
/// - 直链档：只留比默认直链更清晰的，或者同清晰度但编码不同（抖音的 H.265 体积更小）
/// - 合并档：每个高度一档，编码优先 H.264 > H.265 > AV1（AV1 在老设备上放不了）
/// - 有音轨就加一个"仅音频"
pub fn menu(info: &VideoInfo, headers: &[(String, String)], signer: &Signer) -> Vec<FormatDto> {
    let base = alcedo::model::short_side(info.width, info.height);
    let mut out: Vec<FormatDto> = info
        .formats
        .iter()
        .filter(|f| !f.url.is_empty())
        .filter(|f| f.height > base || (!f.codec.is_empty() && f.height == base))
        .map(direct)
        .collect();

    let mut merged: BTreeMap<u32, &Format> = BTreeMap::new();
    for f in info.formats.iter().filter(|f| f.needs_merge() && f.height > base) {
        let better = merged.get(&f.height).is_none_or(|cur| codec_rank(&f.codec) < codec_rank(&cur.codec));
        if better {
            merged.insert(f.height, f);
        }
    }
    out.extend(merged.values().rev().map(|f| merge(f, headers, signer)));

    if let Some(audio) = info.formats.iter().find(|f| !f.audio_url.is_empty()).map(|f| &f.audio_url) {
        let token = MediaToken { video: String::new(), audio: audio.clone(), headers: headers.to_vec() };
        out.push(FormatDto {
            label: "仅音频".into(),
            format_spec: token.encode(signer),
            ext: "m4a".into(),
            height: 0,
            filesize: 0,
            url: String::new(),
            codec: String::new(),
        });
    }
    out
}

fn codec_rank(codec: &str) -> u8 {
    match codec {
        "" => 0,
        "H.265" => 1,
        _ => 2,
    }
}

fn direct(f: &Format) -> FormatDto {
    FormatDto {
        label: f.label.clone(),
        format_spec: String::new(),
        ext: f.ext.clone(),
        height: f.height,
        filesize: f.filesize,
        url: f.url.clone(),
        codec: f.codec.clone(),
    }
}

fn merge(f: &Format, headers: &[(String, String)], signer: &Signer) -> FormatDto {
    let token = MediaToken { video: f.video_url.clone(), audio: f.audio_url.clone(), headers: headers.to_vec() };
    FormatDto {
        label: f.label.clone(),
        format_spec: token.encode(signer),
        ext: "mp4".into(),
        height: f.height,
        filesize: f.filesize,
        url: String::new(),
        codec: String::new(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fmt(label: &str, h: u32, codec: &str, url: &str, video: &str) -> Format {
        Format {
            label: label.into(),
            url: url.into(),
            ext: "mp4".into(),
            height: h,
            codec: codec.into(),
            video_url: video.into(),
            audio_url: if video.is_empty() { String::new() } else { "https://a/audio.m4s".into() },
            ..Default::default()
        }
    }

    #[test]
    fn menu_keeps_better_direct_and_one_merge_per_height() {
        let info = VideoInfo {
            video_url: "https://v/720.mp4".into(),
            width: 1280,
            height: 720,
            formats: vec![
                fmt("720p", 720, "", "https://v/720.mp4", ""),
                fmt("720p H.265", 720, "H.265", "https://v/720h.mp4", ""),
                fmt("480p", 480, "", "", "https://v/480.m4s"),
                fmt("1080p AV1", 1080, "AV1", "", "https://v/1080a.m4s"),
                fmt("1080p", 1080, "", "", "https://v/1080.m4s"),
                fmt("1080p H.265", 1080, "H.265", "", "https://v/1080h.m4s"),
            ],
            ..Default::default()
        };
        let s = Signer::new(b"k".to_vec());
        let m = menu(&info, &[], &s);
        let labels: Vec<&str> = m.iter().map(|f| f.label.as_str()).collect();
        assert_eq!(labels, ["720p H.265", "1080p", "仅音频"]);
        let tok = MediaToken::decode(&m[1].format_spec, &s).unwrap();
        assert_eq!(tok.video, "https://v/1080.m4s", "同高度优先 H.264");
        assert_eq!(MediaToken::decode(&m[2].format_spec, &s).unwrap().video, "");
    }

    #[test]
    fn every_url_is_signed_and_page_url_falls_back_to_share_url() {
        let info = VideoInfo {
            video_url: "https://v/a.mp4".into(),
            cover_url: "https://c/a.jpg".into(),
            images: vec![alcedo::Image { url: "https://i/1.jpg".into(), live_photo_url: "https://i/1.mp4".into() }],
            ..Default::default()
        };
        let s = Signer::new(b"k".to_vec());
        let dto = ParsedDto::new(VideoDto::from_info(&info, "https://share/x", &s), "https://share/x", &s, None);
        assert_eq!(dto.video.page_url, "https://share/x");
        for u in ["https://v/a.mp4", "https://c/a.jpg", "https://i/1.jpg", "https://i/1.mp4", "https://share/x"] {
            assert!(s.verify(u, &dto.sig[u]), "{u}");
        }
        let json = serde_json::to_value(&dto).unwrap();
        assert!(json.get("edge").is_none());
        assert_eq!(json["images"][0]["live_photo_url"], "https://i/1.mp4");
        assert_eq!(json["author"]["name"], "");
    }
}
