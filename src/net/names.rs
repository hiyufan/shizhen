//! 文件名与错误文案的清洗。

use std::path::Path;
use std::sync::LazyLock;

use percent_encoding::{utf8_percent_encode, AsciiSet, NON_ALPHANUMERIC};
use regex::Regex;

static UNSAFE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r#"[\\/:*?"<>|\x00-\x1f]+"#).unwrap_or_else(|_| unreachable!()));
static SPACES: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\s+").unwrap_or_else(|_| unreachable!()));

/// 下载文件名：去掉各系统不认的字符，截到 60 个字符，空了用 fallback。
pub fn safe_filename(name: &str, ext: &str, fallback: &str) -> String {
    let cleaned = UNSAFE.replace_all(name, " ");
    let cleaned = SPACES.replace_all(cleaned.trim().trim_matches('.'), " ");
    let base: String = cleaned.chars().take(60).collect();
    let base = match base.trim() {
        "" => fallback,
        b => b,
    };
    match ext.trim_start_matches('.') {
        "" => base.to_owned(),
        ext => format!("{base}.{ext}"),
    }
}

/// RFC 5987 的 attr-char 里不用编码的几个符号；和 Python `urllib.parse.quote` 一样保留 `-._~`。
const FILENAME_SAFE: &AsciiSet = &NON_ALPHANUMERIC
    .remove(b'-')
    .remove(b'.')
    .remove(b'_')
    .remove(b'~');

/// `Content-Disposition` 的值：中文文件名走 `filename*=UTF-8''...`。
pub fn attachment_header(name: &str) -> String {
    format!(
        "attachment; filename*=UTF-8''{}",
        utf8_percent_encode(name, FILENAME_SAFE)
    )
}

/// 错误信息里别带服务器本地路径。
pub fn scrub_paths(text: &str, data_dir: &Path) -> String {
    let dir = data_dir.to_string_lossy();
    let absolute = std::fs::canonicalize(data_dir)
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut out = text.to_owned();
    for d in [absolute.as_str(), dir.as_ref()] {
        if !d.is_empty() {
            out = out.replace(d, "data");
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filenames() {
        assert_eq!(safe_filename("a/b:c?", "mp4", "x"), "a b c.mp4");
        assert_eq!(safe_filename("  ..  ", "gif", "clip"), "clip.gif");
        assert_eq!(safe_filename("标题", "", "x"), "标题");
        assert_eq!(
            safe_filename(&"长".repeat(100), ".jpg", "x")
                .chars()
                .count(),
            64
        );
    }

    #[test]
    fn attachment_keeps_dots() {
        assert_eq!(
            attachment_header("a b.mp4"),
            "attachment; filename*=UTF-8''a%20b.mp4"
        );
        assert!(attachment_header("标题.jpg").starts_with("attachment; filename*=UTF-8''%E6%A0%87"));
    }

    #[test]
    fn paths_are_scrubbed() {
        let dir = Path::new("/srv/app/data");
        assert_eq!(
            scrub_paths("open /srv/app/data/x.mp4 failed", dir),
            "open data/x.mp4 failed"
        );
    }
}
