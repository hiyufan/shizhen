//! 媒体处理：ffmpeg 调用和实况 / 动态照片的元数据。

pub mod ffmpeg;
pub mod livephoto;

pub use ffmpeg::{Container, Dither, Ffmpeg, Gif, Segment};
