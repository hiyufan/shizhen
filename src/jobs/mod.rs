//! 后台任务队列：下载 / 转换共用，带进度，前端轮询。
//!
//! 一个任务就是一个返回 [`JobOutput`] 的 future。队列负责：排队上限、并发上限、
//! 超时、取消、结束后记统计。任务本体（`tasks` 下各文件）只管干活和报进度。
//!
//! 没有回调：并发名额这类"结束时要归还的东西"做成守卫对象随任务移进去，
//! 任务正常结束、失败、超时、被取消都会 drop 它。

mod progress;
pub mod tasks;

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::sync::Semaphore;
use tokio::task::AbortHandle;

pub use progress::{Progress, Span};

use crate::error::TaskResult;
use crate::stats::{Event, Stats};

/// 任务做完交出来的东西。
#[derive(Debug, Default)]
pub struct JobOutput {
    /// 给用户下载的文件
    pub result: Option<PathBuf>,
    pub filename: Option<String>,
    /// 前端直接展示的预览
    pub preview: Option<Preview>,
    /// 给前端的附加信息（比如准备好的原视频信息）
    pub extra: serde_json::Value,
    /// 实况封面图，`/preview` 接口给
    pub cover: Option<PathBuf>,
    /// 实况的 MOV，`/video` 接口给
    pub motion: Option<PathBuf>,
}

/// 任务结果怎么预览。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Preview {
    /// 结果文件本身（GIF、动态照片）
    File,
    /// 实况的封面图
    Cover,
}

impl Preview {
    fn url(self, job_id: &str) -> String {
        match self {
            Preview::File => format!("/api/jobs/{job_id}/file?inline=1"),
            Preview::Cover => format!("/api/jobs/{job_id}/preview"),
        }
    }
}

impl JobOutput {
    fn files(&self) -> impl Iterator<Item = &PathBuf> {
        [&self.result, &self.cover, &self.motion]
            .into_iter()
            .flatten()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum Status {
    Queued,
    Running,
    Done,
    Error,
}

#[derive(Debug)]
struct State {
    status: Status,
    error: Option<String>,
    output: JobOutput,
    finished: Option<Instant>,
}

#[derive(Debug)]
pub struct Job {
    pub id: String,
    pub kind: String,
    pub source_id: Option<String>,
    /// 发起任务的客户端 IP，只有它能取消
    pub owner: String,
    created: Instant,
    progress: Progress,
    state: Mutex<State>,
    abort: Mutex<Option<AbortHandle>>,
}

/// 前端看到的任务。字段和 Python 版一致。
#[derive(Debug, Serialize)]
pub struct JobView {
    pub id: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub status: Status,
    pub progress: f64,
    pub message: String,
    pub error: Option<String>,
    pub filename: Option<String>,
    pub filesize: Option<u64>,
    pub source_id: Option<String>,
    pub preview: Option<String>,
    pub extra: serde_json::Value,
}

impl Job {
    pub fn view(&self) -> JobView {
        let (progress, message) = self.progress.snapshot();
        let s = self.lock();
        JobView {
            id: self.id.clone(),
            kind: self.kind.clone(),
            status: s.status,
            progress: (progress * 10_000.0).round() / 10_000.0,
            message,
            error: s.error.clone(),
            filename: s.output.filename.clone(),
            filesize: s
                .output
                .result
                .as_ref()
                .and_then(|p| std::fs::metadata(p).ok())
                .map(|m| m.len()),
            source_id: self.source_id.clone(),
            preview: s.output.preview.map(|p| p.url(&self.id)),
            extra: s.output.extra.clone(),
        }
    }

    pub fn status(&self) -> Status {
        self.lock().status
    }

    /// 结果文件（任务完成且文件还在时）。
    pub fn result(&self) -> Option<(PathBuf, Option<String>)> {
        let s = self.lock();
        let path = s
            .output
            .result
            .clone()
            .filter(|p| p.exists() && s.status == Status::Done)?;
        Some((path, s.output.filename.clone()))
    }

    pub fn cover(&self) -> Option<PathBuf> {
        self.lock().output.cover.clone().filter(|p| p.exists())
    }

    pub fn motion(&self) -> Option<PathBuf> {
        self.lock().output.motion.clone().filter(|p| p.exists())
    }

    fn is_pending(&self) -> bool {
        matches!(self.status(), Status::Queued | Status::Running)
    }

    fn finish(&self, outcome: Result<JobOutput, String>) {
        let mut s = self.lock();
        if s.finished.is_some() {
            return; // 已经被取消了
        }
        s.finished = Some(Instant::now());
        match outcome {
            Ok(output) => {
                s.status = Status::Done;
                s.output = output;
                self.progress.set(1.0);
            }
            Err(e) => {
                s.status = Status::Error;
                s.error = Some(e);
            }
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// 排队太多，直接拒绝。
#[derive(Debug)]
pub struct QueueFull;

/// 任务的身份信息。
#[derive(Debug)]
pub struct JobSpec {
    pub kind: &'static str,
    pub source_id: Option<String>,
    pub owner: String,
}

#[derive(Debug, Serialize)]
pub struct QueueStats {
    pub pending: usize,
    pub running: usize,
    pub max_concurrent: usize,
    pub max_queue: usize,
}

#[derive(Debug)]
pub struct Jobs {
    jobs: Mutex<HashMap<String, Arc<Job>>>,
    slots: Arc<Semaphore>,
    max_concurrent: usize,
    max_queued: usize,
    timeout: Duration,
    stats: Arc<Stats>,
    outputs_dir: PathBuf,
}

impl Jobs {
    pub fn new(
        max_concurrent: usize,
        max_queued: usize,
        timeout: Duration,
        stats: Arc<Stats>,
        outputs_dir: PathBuf,
    ) -> Self {
        Self {
            jobs: Mutex::default(),
            slots: Arc::new(Semaphore::new(max_concurrent)),
            max_concurrent,
            max_queued,
            timeout,
            stats,
            outputs_dir,
        }
    }

    /// 排队执行。`guard` 在任务结束时被 drop（用来归还每个 IP 的并发名额）。
    pub fn start<F, Fut, G>(&self, spec: JobSpec, guard: G, task: F) -> Result<Arc<Job>, QueueFull>
    where
        F: FnOnce(Progress) -> Fut + Send + 'static,
        Fut: Future<Output = TaskResult<JobOutput>> + Send + 'static,
        G: Send + 'static,
    {
        if self.pending() >= self.max_queued {
            return Err(QueueFull);
        }
        let job = Arc::new(Job {
            id: short_id(),
            kind: spec.kind.to_owned(),
            source_id: spec.source_id,
            owner: spec.owner,
            created: Instant::now(),
            progress: Progress::default(),
            state: Mutex::new(State {
                status: Status::Queued,
                error: None,
                output: JobOutput::default(),
                finished: None,
            }),
            abort: Mutex::default(),
        });
        self.lock().insert(job.id.clone(), Arc::clone(&job));

        let runner = Runner {
            job: Arc::clone(&job),
            data_dir: self
                .outputs_dir
                .parent()
                .map(PathBuf::from)
                .unwrap_or_default(),
            slots: Arc::clone(&self.slots),
            timeout: self.timeout,
            stats: Arc::clone(&self.stats),
        };
        let handle = tokio::spawn(async move {
            let _guard = guard;
            runner.run(task).await;
        });
        *job.abort.lock().unwrap_or_else(PoisonError::into_inner) = Some(handle.abort_handle());
        Ok(job)
    }

    pub fn get(&self, id: &str) -> Option<Arc<Job>> {
        self.lock().get(id).cloned()
    }

    /// 同一个来源正在准备中的任务：爆款链接几个人同时点，只下载一次。
    pub fn find_pending(&self, kind: &str, source_id: &str) -> Option<Arc<Job>> {
        self.lock()
            .values()
            .find(|j| j.kind == kind && j.source_id.as_deref() == Some(source_id) && j.is_pending())
            .cloned()
    }

    /// 取消：中止任务（ffmpeg 随 future 一起被杀），标记为已取消。
    pub fn cancel(&self, id: &str) -> bool {
        let Some(job) = self.get(id).filter(|j| j.is_pending()) else {
            return false;
        };
        job.finish(Err("已取消".into()));
        if let Some(h) = job
            .abort
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take()
        {
            h.abort();
        }
        record(&self.stats, &job);
        true
    }

    pub fn pending(&self) -> usize {
        self.lock().values().filter(|j| j.is_pending()).count()
    }

    pub fn stats(&self) -> QueueStats {
        let jobs = self.lock();
        QueueStats {
            pending: jobs.values().filter(|j| j.is_pending()).count(),
            running: jobs
                .values()
                .filter(|j| j.status() == Status::Running)
                .count(),
            max_concurrent: self.max_concurrent,
            max_queue: self.max_queued,
        }
    }

    /// 任务的结果文件和工作目录；清孤儿时要绕开。
    pub fn known_paths(&self) -> HashSet<PathBuf> {
        let jobs = self.lock();
        let files = jobs
            .values()
            .flat_map(|j| j.lock().output.files().cloned().collect::<Vec<_>>());
        let work = jobs
            .keys()
            .map(|id| self.outputs_dir.join(format!("{id}_work")));
        files
            .chain(work)
            .map(|p| std::fs::canonicalize(&p).unwrap_or(p))
            .collect()
    }

    /// 删掉结束超过 TTL 的任务和它们的文件。
    pub fn sweep(&self, ttl: Duration) {
        let expired: Vec<Arc<Job>> = {
            let mut jobs = self.lock();
            let old: Vec<String> = jobs
                .iter()
                .filter(|(_, j)| j.lock().finished.is_some_and(|t| t.elapsed() > ttl))
                .map(|(k, _)| k.clone())
                .collect();
            old.iter().filter_map(|k| jobs.remove(k)).collect()
        };
        for job in expired {
            job.lock()
                .output
                .files()
                .for_each(|p| crate::fsutil::remove(p));
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Arc<Job>>> {
        self.jobs.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// 跑一个任务的全过程，从 `Jobs::start` 里拆出来，免得那边嵌套太深。
struct Runner {
    job: Arc<Job>,
    /// 错误信息里把这个目录换成 `data`，别把服务器路径给用户看
    data_dir: PathBuf,
    slots: Arc<Semaphore>,
    timeout: Duration,
    stats: Arc<Stats>,
}

impl Runner {
    async fn run<F, Fut>(self, task: F)
    where
        F: FnOnce(Progress) -> Fut,
        Fut: Future<Output = TaskResult<JobOutput>>,
    {
        // 信号量只在进程退出时关闭，那时候任务也不需要跑了
        let Ok(_slot) = self.slots.acquire().await else {
            return;
        };
        self.job.lock().status = Status::Running;

        let outcome =
            match tokio::time::timeout(self.timeout, task(self.job.progress.clone())).await {
                Ok(Ok(output)) => Ok(output),
                Ok(Err(e)) => Err(crate::net::scrub_paths(&e.0, &self.data_dir)),
                Err(_) => Err(timeout_message(self.timeout)),
            };
        if let Err(e) = &outcome {
            tracing::warn!(job = %self.job.id, kind = %self.job.kind, error = %e, "任务失败");
        }
        self.job.finish(outcome);
        record(&self.stats, &self.job);
    }
}

fn record(stats: &Stats, job: &Job) {
    let s = job.lock();
    let error = s.error.as_deref().unwrap_or("");
    stats.record(Event::Job {
        ip: &job.owner,
        kind: &job.kind,
        ok: s.status == Status::Done,
        reason: error,
        elapsed: job.created.elapsed(),
    });
}

fn timeout_message(limit: Duration) -> String {
    let secs = limit.as_secs();
    let human = if secs >= 60 {
        format!("{} 分钟", secs / 60)
    } else {
        format!("{secs} 秒")
    };
    format!("超过 {human}还没做完，已放弃。试试缩短时长或降低尺寸")
}

fn short_id() -> String {
    let b: [u8; 6] = rand::random();
    hex::encode(b)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::error::TaskError;

    fn jobs(max_queued: usize, timeout: Duration) -> Jobs {
        Jobs::new(
            1,
            max_queued,
            timeout,
            Arc::new(Stats::disabled()),
            std::env::temp_dir(),
        )
    }

    fn spec() -> JobSpec {
        JobSpec {
            kind: "gif",
            source_id: None,
            owner: "1.2.3.4".into(),
        }
    }

    async fn wait_done(job: &Job) {
        for _ in 0..200 {
            if !job.is_pending() {
                return;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        panic!("任务没结束");
    }

    #[tokio::test]
    async fn success_error_and_timeout() {
        let q = jobs(10, Duration::from_millis(50));
        let ok = q
            .start(spec(), (), |p| async move {
                p.step(0.5, "干活");
                Ok(JobOutput {
                    filename: Some("a.gif".into()),
                    ..Default::default()
                })
            })
            .unwrap();
        let bad = q
            .start(spec(), (), |_| async { Err(TaskError::new("坏了")) })
            .unwrap();
        let slow = q
            .start(spec(), (), |_| async {
                tokio::time::sleep(Duration::from_secs(5)).await;
                Ok(JobOutput::default())
            })
            .unwrap();
        for j in [&ok, &bad, &slow] {
            wait_done(j).await;
        }
        assert_eq!(ok.view().status, Status::Done);
        assert!((ok.view().progress - 1.0).abs() < 1e-9);
        assert_eq!(bad.view().error.as_deref(), Some("坏了"));
        assert!(slow.view().error.unwrap().contains("还没做完"));
    }

    #[tokio::test]
    async fn guard_is_released_on_cancel() {
        let q = jobs(10, Duration::from_secs(10));
        let released = Arc::new(std::sync::atomic::AtomicBool::new(false));
        struct Guard(Arc<std::sync::atomic::AtomicBool>);
        impl Drop for Guard {
            fn drop(&mut self) {
                self.0.store(true, std::sync::atomic::Ordering::SeqCst);
            }
        }
        let job = q
            .start(spec(), Guard(Arc::clone(&released)), |_| async {
                tokio::time::sleep(Duration::from_secs(60)).await;
                Ok(JobOutput::default())
            })
            .unwrap();
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(q.cancel(&job.id));
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(
            released.load(std::sync::atomic::Ordering::SeqCst),
            "取消后名额必须归还"
        );
        assert_eq!(job.view().error.as_deref(), Some("已取消"));
        assert!(!q.cancel(&job.id), "结束了的任务不能再取消");
    }

    #[tokio::test]
    async fn queue_limit() {
        let q = jobs(1, Duration::from_secs(10));
        let _a = q
            .start(spec(), (), |_| async {
                tokio::time::sleep(Duration::from_secs(60)).await;
                Ok(JobOutput::default())
            })
            .unwrap();
        assert!(q
            .start(spec(), (), |_| async { Ok(JobOutput::default()) })
            .is_err());
    }
}
