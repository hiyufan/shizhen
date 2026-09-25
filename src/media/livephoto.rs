//! 实况照片（iOS）和动态照片（Android）的配对元数据。
//!
//! - **iOS**：一张 JPEG 和一个 MOV 靠同一个 UUID 配对。MOV 里是 mdta 键
//!   `com.apple.quicktime.content.identifier`（ffmpeg 加 `use_metadata_tags` 就能写），
//!   JPEG 里是 EXIF 的 Apple MakerNote，tag 0x0011。
//! - **Android**：一个 JPEG 末尾直接接上 MP4，再加一段 XMP 说明视频从哪开始。
//!
//! EXIF 只写我们需要的几个字段，原图里已有的 EXIF 会被替换掉：ffmpeg 抽出来的帧
//! 本来就没有，平台给的原图基本也被剥过了。

use std::path::Path;

/// MOV 里的配对键，写入由 ffmpeg 完成。
pub const MOV_IDENTIFIER_KEY: &str = "com.apple.quicktime.content.identifier";

const APPLE_MAKERNOTE_HEADER: &[u8] = b"Apple iOS\0\0\x01MM";
const APPLE_CONTENT_IDENTIFIER: u16 = 0x0011;

const TAG_MAKE: u16 = 0x010F;
const TAG_SOFTWARE: u16 = 0x0131;
const TAG_DATETIME: u16 = 0x0132;
const TAG_EXIF_IFD: u16 = 0x8769;
const TAG_DATETIME_ORIGINAL: u16 = 0x9003;
const TAG_MAKERNOTE: u16 = 0x927C;

const TYPE_ASCII: u16 = 2;
const TYPE_LONG: u16 = 4;
const TYPE_UNDEFINED: u16 = 7;

/// 新的配对标识，大写 UUID，和 iPhone 自己生成的格式一致。
pub fn new_identifier() -> String {
    let b: [u8; 16] = rand::random();
    let mut b = b;
    b[6] = (b[6] & 0x0F) | 0x40; // version 4
    b[8] = (b[8] & 0x3F) | 0x80; // variant
    let h = hex::encode_upper(b);
    format!("{}-{}-{}-{}-{}", &h[0..8], &h[8..12], &h[12..16], &h[16..20], &h[20..32])
}

/// 给 JPEG 写上 Apple 的配对标识。`date` 形如 `2026:09:25 14:00:00`。
pub fn write_jpeg_identifier(path: &Path, identifier: &str, date: &str) -> std::io::Result<()> {
    let jpeg = std::fs::read(path)?;
    let app1 = exif_segment(identifier, date);
    let out = replace_exif(&jpeg, &app1).ok_or_else(|| invalid("不是 JPEG 文件"))?;
    std::fs::write(path, out)
}

/// 读回 JPEG 里的配对标识，写完之后自检用。
pub fn read_jpeg_identifier(path: &Path) -> Option<String> {
    let data = std::fs::read(path).ok()?;
    let start = find(&data, APPLE_MAKERNOTE_HEADER)?;
    let note = &data[start..];
    let count = be16(note, 14)?;
    (0..usize::from(count)).find_map(|i| {
        let entry = 16 + 12 * i;
        let (tag, typ) = (be16(note, entry)?, be16(note, entry + 2)?);
        if tag != APPLE_CONTENT_IDENTIFIER || typ != TYPE_ASCII {
            return None;
        }
        let len = usize::try_from(be32(note, entry + 4)?).ok()?;
        let off = usize::try_from(be32(note, entry + 8)?).ok()?;
        let raw = note.get(off..off + len)?;
        Some(String::from_utf8_lossy(raw).trim_end_matches('\0').to_owned())
    })
}

/// MOV 里有没有写进这个标识。
pub fn mov_has_identifier(path: &Path, identifier: &str) -> bool {
    std::fs::read(path).is_ok_and(|d| {
        find(&d, MOV_IDENTIFIER_KEY.as_bytes()).is_some() && find(&d, identifier.as_bytes()).is_some()
    })
}

/// 安卓动态照片：JPEG + XMP + 接在末尾的 MP4。
pub fn write_motion_photo(jpeg: &Path, mp4: &Path, out: &Path, presentation_us: u64) -> std::io::Result<()> {
    let image = std::fs::read(jpeg)?;
    let video = std::fs::read(mp4)?;
    let mut xmp = b"http://ns.adobe.com/xap/1.0/\0".to_vec();
    xmp.extend_from_slice(motion_xmp(video.len(), presentation_us).as_bytes());
    let segment = app1(&xmp).ok_or_else(|| invalid("XMP 太长"))?;

    let cut = after_app_headers(&image).ok_or_else(|| invalid("不是 JPEG 文件"))?;
    let mut result = Vec::with_capacity(image.len() + segment.len() + video.len());
    result.extend_from_slice(&image[..cut]);
    result.extend_from_slice(&segment);
    result.extend_from_slice(&image[cut..]);
    result.extend_from_slice(&video);
    std::fs::write(out, result)
}

// ------------------------------------------------------------------ EXIF 组装

/// 最小的 Apple MakerNote：一个 IFD，里面只有 ContentIdentifier。
/// MakerNote 里的偏移量相对它自己的第一个字节。
fn apple_makernote(identifier: &str) -> Vec<u8> {
    let mut value = identifier.as_bytes().to_vec();
    value.push(0);
    let data_offset = APPLE_MAKERNOTE_HEADER.len() + 2 + 12 + 4;

    let mut note = APPLE_MAKERNOTE_HEADER.to_vec();
    note.extend_from_slice(&1u16.to_be_bytes());
    push_entry(&mut note, APPLE_CONTENT_IDENTIFIER, TYPE_ASCII, value.len(), data_offset);
    note.extend_from_slice(&0u32.to_be_bytes());
    note.extend_from_slice(&value);
    note
}

/// 一个 IFD 条目：值放在数据区，这里只记偏移。
struct Entry {
    tag: u16,
    typ: u16,
    value: Vec<u8>,
}

impl Entry {
    fn ascii(tag: u16, s: &str) -> Self {
        let mut value = s.as_bytes().to_vec();
        value.push(0);
        Self { tag, typ: TYPE_ASCII, value }
    }
}

/// 整个 APP1 Exif 段：大端 TIFF，IFD0（Make / Software / DateTime / Exif 指针）+ Exif IFD。
fn exif_segment(identifier: &str, date: &str) -> Vec<u8> {
    let exif_entries = vec![
        Entry::ascii(TAG_DATETIME_ORIGINAL, date),
        Entry {
            tag: TAG_MAKERNOTE,
            typ: TYPE_UNDEFINED,
            value: apple_makernote(identifier),
        },
    ];
    let ifd0_entries = [
        Entry::ascii(TAG_MAKE, "Apple"),
        Entry::ascii(TAG_SOFTWARE, "ShiZhen"),
        Entry::ascii(TAG_DATETIME, date),
    ];

    // 布局：TIFF 头(8) | IFD0 | IFD0 数据 | Exif IFD | Exif 数据
    let ifd0_len = ifd_len(ifd0_entries.len() + 1);
    let ifd0_data_len: usize = ifd0_entries.iter().map(|e| e.value.len()).sum();
    let exif_ifd_at = 8 + ifd0_len + ifd0_data_len;

    let mut tiff = b"MM\0\x2a\0\0\0\x08".to_vec();
    tiff.extend(ifd(&ifd0_entries, 8, Some(exif_ifd_at)));
    tiff.extend(ifd(&exif_entries, exif_ifd_at, None));

    let mut payload = b"Exif\0\0".to_vec();
    payload.extend(tiff);
    // 值加起来远小于 64KB（标识 36 字节），不会超出一个段的上限
    app1(&payload).unwrap_or_default()
}

fn ifd_len(entries: usize) -> usize {
    2 + 12 * entries + 4
}

/// 编码一个 IFD 和紧随其后的数据区。`at` 是这个 IFD 在 TIFF 里的偏移。
fn ifd(entries: &[Entry], at: usize, exif_pointer: Option<usize>) -> Vec<u8> {
    let count = entries.len() + usize::from(exif_pointer.is_some());
    let mut data_at = at + ifd_len(count);
    let mut head = Vec::new();
    let mut data = Vec::new();
    head.extend_from_slice(&u16::try_from(count).unwrap_or(0).to_be_bytes());
    for e in entries {
        push_entry(&mut head, e.tag, e.typ, e.value.len(), data_at);
        data.extend_from_slice(&e.value);
        data_at += e.value.len();
    }
    if let Some(ptr) = exif_pointer {
        // Exif 指针是 LONG，值（偏移）直接放在条目里
        push_entry(&mut head, TAG_EXIF_IFD, TYPE_LONG, 1, ptr);
    }
    head.extend_from_slice(&0u32.to_be_bytes());
    head.extend(data);
    head
}

fn push_entry(buf: &mut Vec<u8>, tag: u16, typ: u16, count: usize, value_or_offset: usize) {
    buf.extend_from_slice(&tag.to_be_bytes());
    buf.extend_from_slice(&typ.to_be_bytes());
    buf.extend_from_slice(&u32::try_from(count).unwrap_or(0).to_be_bytes());
    buf.extend_from_slice(&u32::try_from(value_or_offset).unwrap_or(0).to_be_bytes());
}

/// 包成一个 APP1 段；超过 64KB 返回 None。
fn app1(payload: &[u8]) -> Option<Vec<u8>> {
    let len = u16::try_from(payload.len() + 2).ok()?;
    let mut seg = vec![0xFF, 0xE1];
    seg.extend_from_slice(&len.to_be_bytes());
    seg.extend_from_slice(payload);
    Some(seg)
}

// ------------------------------------------------------------------ JPEG 段操作

/// 去掉原有的 Exif APP1，把新的放在 SOI（和 JFIF 的 APP0）后面。
fn replace_exif(jpeg: &[u8], exif: &[u8]) -> Option<Vec<u8>> {
    if !jpeg.starts_with(&[0xFF, 0xD8]) {
        return None;
    }
    let mut out = vec![0xFF, 0xD8];
    let mut pos = 2;
    let mut inserted = false;
    while let Some((marker, len)) = segment_at(jpeg, pos) {
        if !matches!(marker, 0xE0..=0xEF) {
            break; // 过了 APPn 区就是图像数据
        }
        let seg = &jpeg[pos..pos + 2 + len];
        if marker != 0xE0 && !inserted {
            out.extend_from_slice(exif);
            inserted = true;
        }
        let is_exif = marker == 0xE1 && seg.get(4..10) == Some(b"Exif\0\0".as_slice());
        if !is_exif {
            out.extend_from_slice(seg);
        }
        pos += 2 + len;
    }
    if !inserted {
        out.extend_from_slice(exif);
    }
    out.extend_from_slice(&jpeg[pos..]);
    Some(out)
}

/// 开头的 APP0 / APP1 段之后的位置（XMP 放这里）。
fn after_app_headers(jpeg: &[u8]) -> Option<usize> {
    if !jpeg.starts_with(&[0xFF, 0xD8]) {
        return None;
    }
    let mut pos = 2;
    while let Some((0xE0 | 0xE1, len)) = segment_at(jpeg, pos) {
        pos += 2 + len;
    }
    Some(pos)
}

/// `pos` 处的段：(标记, 长度含长度字段本身)。
fn segment_at(jpeg: &[u8], pos: usize) -> Option<(u8, usize)> {
    if *jpeg.get(pos)? != 0xFF {
        return None;
    }
    let marker = *jpeg.get(pos + 1)?;
    let len = usize::from(be16(jpeg, pos + 2)?);
    (pos + 2 + len <= jpeg.len()).then_some((marker, len))
}

fn motion_xmp(video_len: usize, presentation_us: u64) -> String {
    format!(
        r#"<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="ShiZhen">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"
    xmlns:Container="http://ns.google.com/photos/1.0/container/"
    xmlns:Item="http://ns.google.com/photos/1.0/container/item/"
    GCamera:MotionPhoto="1"
    GCamera:MotionPhotoVersion="1"
    GCamera:MotionPhotoPresentationTimestampUs="{presentation_us}"
    GCamera:MicroVideo="1"
    GCamera:MicroVideoVersion="1"
    GCamera:MicroVideoOffset="{video_len}"
    GCamera:MicroVideoPresentationTimestampUs="{presentation_us}">
   <Container:Directory>
    <rdf:Seq>
     <rdf:li rdf:parseType="Resource">
      <Container:Item Item:Mime="image/jpeg" Item:Semantic="Primary" Item:Length="0" Item:Padding="0"/>
     </rdf:li>
     <rdf:li rdf:parseType="Resource">
      <Container:Item Item:Mime="video/mp4" Item:Semantic="MotionPhoto" Item:Length="{video_len}" Item:Padding="0"/>
     </rdf:li>
    </rdf:Seq>
   </Container:Directory>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"#
    )
}

fn be16(b: &[u8], at: usize) -> Option<u16> {
    Some(u16::from_be_bytes(b.get(at..at + 2)?.try_into().ok()?))
}

fn be32(b: &[u8], at: usize) -> Option<u32> {
    Some(u32::from_be_bytes(b.get(at..at + 4)?.try_into().ok()?))
}

fn find(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn invalid(msg: &str) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::InvalidData, msg)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 一个最小的"JPEG"：SOI + JFIF APP0 + 旧 Exif APP1 + 假的图像数据 + EOI。
    fn fake_jpeg() -> Vec<u8> {
        let mut j = vec![0xFF, 0xD8];
        j.extend_from_slice(&[0xFF, 0xE0, 0x00, 0x07, b'J', b'F', b'I', b'F', 0]);
        j.extend(app1(b"Exif\0\0OLD-EXIF").unwrap());
        j.extend_from_slice(&[0xFF, 0xDB, 0x00, 0x03, 0x01, 0xFF, 0xD9]);
        j
    }

    fn tmp(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("shizhen-live-{name}-{}", rand::random::<u32>()))
    }

    #[test]
    fn identifier_roundtrip_replaces_old_exif() {
        let p = tmp("a.jpg");
        std::fs::write(&p, fake_jpeg()).unwrap();
        let id = new_identifier();
        write_jpeg_identifier(&p, &id, "2026:09:25 12:00:00").unwrap();
        assert_eq!(read_jpeg_identifier(&p).as_deref(), Some(id.as_str()));

        let bytes = std::fs::read(&p).unwrap();
        assert!(find(&bytes, b"OLD-EXIF").is_none(), "旧 Exif 要被替换掉");
        assert!(bytes.starts_with(&[0xFF, 0xD8, 0xFF, 0xE0]), "JFIF 仍然紧跟 SOI");
        assert!(bytes.ends_with(&[0xFF, 0xD9]), "图像数据原样保留");
        std::fs::remove_file(p).ok();
    }

    #[test]
    fn exif_structure_is_valid_tiff() {
        let seg = exif_segment("ABC", "2026:01:01 00:00:00");
        let tiff = &seg[10..];
        assert_eq!(&tiff[..4], b"MM\0\x2a");
        // IFD0 有 4 个条目，最后一个是指向 Exif IFD 的指针
        assert_eq!(be16(tiff, 8), Some(4));
        let ptr_entry = 8 + 2 + 12 * 3;
        assert_eq!(be16(tiff, ptr_entry), Some(TAG_EXIF_IFD));
        let exif_at = usize::try_from(be32(tiff, ptr_entry + 8).unwrap()).unwrap();
        assert_eq!(be16(tiff, exif_at), Some(2), "Exif IFD 有 DateTimeOriginal 和 MakerNote");
        // Make 的值落在数据区里，读出来是 "Apple"
        let make_off = usize::try_from(be32(tiff, 8 + 2 + 8).unwrap()).unwrap();
        assert_eq!(&tiff[make_off..make_off + 5], b"Apple");
    }

    #[test]
    fn identifier_format() {
        let id = new_identifier();
        assert_eq!(id.len(), 36);
        assert_eq!(id.as_bytes()[14], b'4');
        assert_eq!(id, id.to_uppercase());
    }

    #[test]
    fn motion_photo_layout() {
        let (jpg, mp4, out) = (tmp("m.jpg"), tmp("m.mp4"), tmp("out.jpg"));
        std::fs::write(&jpg, fake_jpeg()).unwrap();
        std::fs::write(&mp4, b"FAKEMP4DATA").unwrap();
        write_motion_photo(&jpg, &mp4, &out, 1_500_000).unwrap();
        let bytes = std::fs::read(&out).unwrap();
        assert!(bytes.ends_with(b"FAKEMP4DATA"));
        let xmp_at = find(&bytes, b"http://ns.adobe.com/xap/1.0/").unwrap();
        assert!(xmp_at > find(&bytes, b"OLD-EXIF").unwrap(), "XMP 放在已有 APP1 之后");
        assert!(find(&bytes, b"MicroVideoOffset=\"11\"").is_some());
        for p in [jpg, mp4, out] {
            std::fs::remove_file(p).ok();
        }
    }
}
