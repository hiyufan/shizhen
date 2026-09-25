//! 定期清理：过期任务 / 原视频、没登记在册的孤儿文件、磁盘配额、统计保留期。
//!
//! 一个普通的同步函数，每 5 分钟在阻塞线程池里跑一次；启动时先跑一次，把上个
//! 进程留下的文件清掉（任务和原视频的登记在内存里，重启就没了）。

use std::collections::HashSet;
use std::path::PathBuf;
use std::time::Duration;

use crate::config::Config;
use crate::fsutil;
use crate::jobs::Jobs;
use crate::stats::Stats;
use crate::store::{disk_usage, orphans, Store};

pub struct Cleanup<'a> {
    pub cfg: &'a Config,
    pub jobs: &'a Jobs,
    pub store: &'a Store,
    pub stats: &'a Stats,
}

impl Cleanup<'_> {
    pub fn run(&self) {
        self.jobs.sweep(self.cfg.job_ttl);
        self.store.sweep(self.cfg.source_ttl);
        let known: HashSet<PathBuf> = self
            .jobs
            .known_paths()
            .into_iter()
            .chain(self.store.known_paths())
            .collect();
        let removed = self.sweep_orphans(&known);
        let evicted = self.enforce_quota(&known);
        if removed + evicted > 0 {
            tracing::info!(removed, evicted, "清理临时文件");
        }
        if let Err(e) = self.stats.prune() {
            tracing::warn!(error = %e, "清理过期统计失败");
        }
    }

    /// 删掉超过 TTL 的孤儿。正在写的文件 mtime 一直在更新，不会被误删。
    fn sweep_orphans(&self, known: &HashSet<PathBuf>) -> usize {
        // 任务最长跑 job_timeout；TTL 被调得比它还短时也别碰还在跑的任务
        let floor = self.cfg.job_timeout * 2;
        let outputs = self.cfg.outputs_dir();
        let max_age = |dir: &PathBuf| {
            let ttl = if *dir == outputs {
                self.cfg.job_ttl
            } else {
                self.cfg.source_ttl
            };
            ttl.max(floor)
        };
        let mut removed = 0;
        for o in orphans(&self.cfg.work_dirs(), known) {
            if o.age > max_age(&o.dir) {
                fsutil::remove(&o.path);
                removed += 1;
            }
        }
        removed
    }

    /// 超出配额时降到配额的 80%：先按最久未用删原视频，还不够再删最旧的孤儿。
    fn enforce_quota(&self, known: &HashSet<PathBuf>) -> usize {
        let dirs = self.cfg.work_dirs();
        let used = disk_usage(&dirs);
        let quota = self.cfg.disk_quota_bytes;
        if used <= quota {
            return 0;
        }
        let target = quota / 10 * 8;
        let mut over = self.store.evict_until(used - target);
        if over == 0 {
            return 1;
        }
        // 太新的孤儿多半是正在写的半成品（结果文件要到任务结束才登记），放过
        let mut stale: Vec<_> = orphans(&dirs, known)
            .into_iter()
            .filter(|o| o.age > self.cfg.job_timeout)
            .collect();
        stale.sort_by_key(|o| std::cmp::Reverse(o.age));
        let mut removed = 0;
        for o in stale {
            if over == 0 {
                break;
            }
            over = over.saturating_sub(fsutil::size_of(&o.path));
            fsutil::remove(&o.path);
            removed += 1;
        }
        removed
    }
}

pub const INTERVAL: Duration = Duration::from_secs(300);
