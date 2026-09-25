//! 原视频缓存：同一个来源只下载一次，GIF / 实况多次转换都复用它。
//! 还管 data/ 下临时文件的清理和磁盘配额。

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, Instant, SystemTime};

use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::fsutil;

/// 一个可供转换的原视频。
#[derive(Debug, Clone)]
pub struct Source {
    pub id: String,
    pub path: PathBuf,
    pub title: String,
    pub duration: f64,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    pub strip: Option<PathBuf>,
}

/// 给前端的样子。
#[derive(Debug, Clone, Serialize)]
pub struct SourceView {
    pub id: String,
    pub title: String,
    pub duration: f64,
    pub width: u32,
    pub height: u32,
    pub fps: f64,
}

impl Source {
    pub fn view(&self) -> SourceView {
        SourceView {
            id: self.id.clone(),
            title: self.title.clone(),
            duration: (self.duration * 1000.0).round() / 1000.0,
            width: self.width,
            height: self.height,
            fps: self.fps,
        }
    }
}

/// 同一个来源（地址 + 取哪一档）算出同一个 ID，爆款链接只下载一次。
pub fn source_id_for(parts: &[&str]) -> String {
    let digest = Sha256::digest(parts.join("|").as_bytes());
    hex::encode(&digest[..8])
}

struct Entry {
    source: Source,
    last_used: Instant,
}

#[derive(Default)]
pub struct Store {
    sources: Mutex<HashMap<String, Entry>>,
}

impl std::fmt::Debug for Store {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Store").finish_non_exhaustive()
    }
}

impl Store {
    /// 取一个还在磁盘上的原视频，顺带续期。
    pub fn get(&self, id: &str) -> Option<Source> {
        let mut map = self.lock();
        let entry = map.get_mut(id)?;
        if !entry.source.path.exists() {
            map.remove(id);
            return None;
        }
        entry.last_used = Instant::now();
        Some(entry.source.clone())
    }

    pub fn put(&self, source: Source) {
        let id = source.id.clone();
        self.lock().insert(
            id,
            Entry {
                source,
                last_used: Instant::now(),
            },
        );
    }

    pub fn set_strip(&self, id: &str, strip: PathBuf) {
        if let Some(e) = self.lock().get_mut(id) {
            e.source.strip = Some(strip);
        }
    }

    /// 登记在册的文件；清孤儿时要绕开。
    pub fn known_paths(&self) -> HashSet<PathBuf> {
        self.lock()
            .values()
            .flat_map(|e| [Some(&e.source.path), e.source.strip.as_ref()])
            .flatten()
            .map(|p| canonical(p))
            .collect()
    }

    /// 删掉超过 TTL 没用过的原视频。
    pub fn sweep(&self, ttl: Duration) {
        let expired: Vec<Source> = {
            let mut map = self.lock();
            let old: Vec<String> = map
                .iter()
                .filter(|(_, e)| e.last_used.elapsed() > ttl)
                .map(|(k, _)| k.clone())
                .collect();
            old.iter()
                .filter_map(|k| map.remove(k))
                .map(|e| e.source)
                .collect()
        };
        expired.iter().for_each(remove_source_files);
    }

    /// 超出配额时按最久未用删原视频，返回还超出多少字节（交给孤儿清理继续删）。
    pub fn evict_until(&self, mut over_by: u64) -> u64 {
        while over_by > 0 {
            let oldest = {
                let mut map = self.lock();
                let key = map
                    .iter()
                    .min_by_key(|(_, e)| e.last_used)
                    .map(|(k, _)| k.clone());
                key.and_then(|k| map.remove(&k)).map(|e| e.source)
            };
            let Some(src) = oldest else { break };
            over_by = over_by.saturating_sub(fsutil::size_of(&src.path));
            remove_source_files(&src);
        }
        over_by
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Entry>> {
        self.sources.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

fn remove_source_files(src: &Source) {
    fsutil::remove(&src.path);
    if let Some(s) = &src.strip {
        fsutil::remove(s);
    }
}

fn canonical(p: &Path) -> PathBuf {
    std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf())
}

// ------------------------------------------------------------------ 孤儿与配额

/// 工作目录里一个没登记在册的文件（进程重启后内存登记没了，失败的任务也会留半成品）。
#[derive(Debug)]
pub struct Orphan {
    pub path: PathBuf,
    pub age: Duration,
    pub dir: PathBuf,
}

pub fn orphans(dirs: &[PathBuf], known: &HashSet<PathBuf>) -> Vec<Orphan> {
    let now = SystemTime::now();
    dirs.iter()
        .filter_map(|d| std::fs::read_dir(d).ok().map(|it| (d, it)))
        .flat_map(|(d, it)| it.flatten().map(move |e| (d.clone(), e)))
        .filter(|(_, e)| !known.contains(&canonical(&e.path())))
        .filter_map(|(dir, e)| {
            let modified = e.metadata().and_then(|m| m.modified()).ok()?;
            Some(Orphan {
                path: e.path(),
                age: now.duration_since(modified).unwrap_or_default(),
                dir,
            })
        })
        .collect()
}

pub fn disk_usage(dirs: &[PathBuf]) -> u64 {
    dirs.iter()
        .filter_map(|d| std::fs::read_dir(d).ok())
        .flat_map(|it| it.flatten())
        .map(|e| fsutil::size_of(&e.path()))
        .sum()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_file(bytes: usize) -> PathBuf {
        let p = std::env::temp_dir().join(format!("shizhen-store-{}", rand::random::<u32>()));
        std::fs::write(&p, vec![0u8; bytes]).unwrap();
        p
    }

    fn source(id: &str, path: PathBuf) -> Source {
        Source {
            id: id.into(),
            path,
            title: String::new(),
            duration: 1.0,
            width: 1,
            height: 1,
            fps: 1.0,
            strip: None,
        }
    }

    #[test]
    fn ids_are_stable_and_distinct() {
        assert_eq!(source_id_for(&["a", "b"]), source_id_for(&["a", "b"]));
        assert_ne!(source_id_for(&["a", "b"]), source_id_for(&["ab"]));
        assert_eq!(source_id_for(&["x"]).len(), 16);
    }

    #[test]
    fn missing_files_are_forgotten() {
        let store = Store::default();
        let p = tmp_file(1);
        store.put(source("a", p.clone()));
        assert!(store.get("a").is_some());
        std::fs::remove_file(&p).unwrap();
        assert!(store.get("a").is_none());
    }

    #[test]
    fn eviction_removes_least_recently_used_first() {
        let store = Store::default();
        let (old, new) = (tmp_file(10), tmp_file(10));
        store.put(source("old", old.clone()));
        std::thread::sleep(Duration::from_millis(5));
        store.put(source("new", new.clone()));
        assert_eq!(store.evict_until(5), 0);
        assert!(!old.exists());
        assert!(new.exists());
        std::fs::remove_file(new).ok();
    }
}
