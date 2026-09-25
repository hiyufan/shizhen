//! 任务进度。
//!
//! 下载、ffmpeg 这些步骤拿到一个 [`Span`]，只管报告"自己这一段做到了几成"，
//! 由 Span 换算成整个任务的进度。不用回调函数：进度是一份共享状态，前端轮询时读。

use std::sync::{Arc, Mutex, PoisonError};

#[derive(Debug, Default)]
struct State {
    value: f64,
    message: String,
}

/// 一个任务的进度，可以 clone 给多个步骤；都指向同一份状态。
#[derive(Debug, Clone, Default)]
pub struct Progress(Arc<Mutex<State>>);

impl Progress {
    /// 进度 + 文案一起更新。
    pub fn step(&self, value: f64, message: &str) {
        let mut s = self.lock();
        s.value = value.clamp(0.0, 1.0);
        message.clone_into(&mut s.message);
    }

    pub fn set(&self, value: f64) {
        self.lock().value = value.clamp(0.0, 1.0);
    }

    pub fn snapshot(&self) -> (f64, String) {
        let s = self.lock();
        (s.value, s.message.clone())
    }

    /// 把 `[from, to]` 这一段交给某个步骤。
    pub fn span(&self, from: f64, to: f64) -> Span {
        Span {
            progress: self.clone(),
            from,
            to,
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        // 进度只是展示用，某个线程 panic 过也照样能读写
        self.0.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// 任务进度里的一段。步骤报告 0..1，换算成 `from..to`。
#[derive(Debug, Clone)]
pub struct Span {
    progress: Progress,
    from: f64,
    to: f64,
}

impl Span {
    pub fn set(&self, fraction: f64) {
        let f = fraction.clamp(0.0, 1.0);
        self.progress.set(self.from + (self.to - self.from) * f);
    }

    /// 再切一小段（比如合并音视频时视频占前 80%，音频占后 20%）。
    pub fn sub(&self, from: f64, to: f64) -> Span {
        let width = self.to - self.from;
        self.progress.span(self.from + width * from, self.from + width * to)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn spans_map_into_their_range() {
        let p = Progress::default();
        let s = p.span(0.2, 0.6);
        s.set(0.5);
        assert!((p.snapshot().0 - 0.4).abs() < 1e-9);
        s.sub(0.5, 1.0).set(1.0);
        assert!((p.snapshot().0 - 0.6).abs() < 1e-9);
        s.set(7.0);
        assert!((p.snapshot().0 - 0.6).abs() < 1e-9, "越界的值要夹住");
    }
}
