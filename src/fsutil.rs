//! 文件系统小工具。

use std::path::{Path, PathBuf};

/// 正在写的文件：出错 / 取消 / panic 时自动删掉，写完调 [`PartialFile::keep`] 才留下。
///
/// 用 Drop 兜底而不是在每个出错分支里手动删，这样"任务被取消"（future 被直接丢掉，
/// 后面的代码根本不会执行）也不会留下半截文件占磁盘。
#[derive(Debug)]
pub struct PartialFile {
    path: PathBuf,
    keep: bool,
}

impl PartialFile {
    pub fn new(path: impl Into<PathBuf>) -> Self {
        Self {
            path: path.into(),
            keep: false,
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// 写完了，交出路径，之后不再自动删除。
    pub fn keep(mut self) -> PathBuf {
        self.keep = true;
        std::mem::take(&mut self.path)
    }
}

impl Drop for PartialFile {
    fn drop(&mut self) {
        if !self.keep && !self.path.as_os_str().is_empty() {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

/// 一个工作目录：出作用域时整个删掉（实况打包的中间文件用）。
#[derive(Debug)]
pub struct WorkDir(PathBuf);

impl WorkDir {
    pub fn create(path: impl Into<PathBuf>) -> std::io::Result<Self> {
        let path = path.into();
        std::fs::create_dir_all(&path)?;
        Ok(Self(path))
    }

    pub fn join(&self, name: impl AsRef<Path>) -> PathBuf {
        self.0.join(name)
    }
}

impl Drop for WorkDir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// 文件或目录占用的字节数；读不到的算 0。
pub fn size_of(path: &Path) -> u64 {
    let Ok(meta) = std::fs::symlink_metadata(path) else {
        return 0;
    };
    if !meta.is_dir() {
        return meta.len();
    }
    std::fs::read_dir(path)
        .map(|entries| entries.flatten().map(|e| size_of(&e.path())).sum())
        .unwrap_or(0)
}

/// 删除文件或目录，不存在不算错。
pub fn remove(path: &Path) {
    let _ = if path.is_dir() {
        std::fs::remove_dir_all(path)
    } else {
        std::fs::remove_file(path)
    };
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!("shizhen-{name}-{}", rand::random::<u32>()))
    }

    #[test]
    fn partial_file_is_removed_unless_kept() {
        let p = tmp("partial");
        std::fs::write(&p, b"x").unwrap();
        drop(PartialFile::new(&p));
        assert!(!p.exists());

        std::fs::write(&p, b"x").unwrap();
        let kept = PartialFile::new(&p).keep();
        assert!(kept.exists());
        std::fs::remove_file(kept).unwrap();
    }

    #[test]
    fn workdir_is_removed_with_contents() {
        let d = tmp("work");
        {
            let w = WorkDir::create(&d).unwrap();
            std::fs::write(w.join("a"), b"12345").unwrap();
            assert_eq!(size_of(&d), 5);
        }
        assert!(!d.exists());
    }
}
