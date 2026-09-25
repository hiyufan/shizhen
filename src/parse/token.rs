//! 媒体令牌：高清档"要服务端合并"时，前端拿着它来下载 / 准备原视频。
//!
//! 里面装着音视频两条直链和要带的请求头，外面一层 HMAC。前端只是原样传回来，
//! 改一个字节签名就对不上，所以服务端可以放心照着它去拉——和直链签名同一个道理。
//!
//! 放在 `format_spec` 字段里，前端代码不用改（Python 版这个字段装 yt-dlp 表达式）。

use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
use base64::Engine;
use serde::{Deserialize, Serialize};

use crate::net::Signer;

const PREFIX: &str = "m1.";

/// 要合并的两条轨；`audio` 为空表示只要视频，`video` 为空表示只要音频。
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MediaToken {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub video: String,
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub audio: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub headers: Vec<(String, String)>,
}

impl MediaToken {
    pub fn encode(&self, signer: &Signer) -> String {
        let body = B64.encode(serde_json::to_vec(self).unwrap_or_default());
        let sig = signer.sign(&body);
        format!("{PREFIX}{body}.{sig}")
    }

    /// 签名不对、格式不对都返回 `None`。
    pub fn decode(token: &str, signer: &Signer) -> Option<Self> {
        let (body, sig) = token.strip_prefix(PREFIX)?.rsplit_once('.')?;
        if !signer.verify(body, sig) {
            return None;
        }
        let token: Self = serde_json::from_slice(&B64.decode(body).ok()?).ok()?;
        (!token.video.is_empty() || !token.audio.is_empty()).then_some(token)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_and_tamper() {
        let s = Signer::new(b"k".to_vec());
        let t = MediaToken {
            video: "https://v/1.m4s".into(),
            audio: "https://a/1.m4s".into(),
            headers: vec![("Referer".into(), "https://www.bilibili.com/".into())],
        };
        let enc = t.encode(&s);
        assert_eq!(MediaToken::decode(&enc, &s), Some(t));

        let mut forged = enc.clone();
        forged.insert(5, 'x');
        assert!(MediaToken::decode(&forged, &s).is_none());
        assert!(MediaToken::decode(&enc, &Signer::new(b"other".to_vec())).is_none());
        assert!(
            MediaToken::decode("bv*[height<=1080]+ba", &s).is_none(),
            "yt-dlp 表达式不是令牌"
        );
    }
}
