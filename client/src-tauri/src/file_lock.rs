//! 目录文件锁的独占获取：短暂重试后仍被占用才报告竞争，供本地设置与本地预览视频共用。
//! flock 锁属于打开文件描述；本进程任一线程创建子进程时（WebKit 网页进程、内置服务、调试日志终端等），
//! 子进程在 exec 之前继承全部描述符，刚关闭的锁要等它 exec 后才真正释放。紧接着的下一次获取若立即失败，
//! 会把这种瞬间占用误报为“正在被其他操作使用”，所以在一个短时限内重试。

use std::{
    fs::{File, TryLockError},
    thread,
    time::{Duration, Instant},
};

/// 重试总时限与间隔：足以覆盖子进程 exec 前的瞬间，真正的长时间占用也不会让报错明显变慢。
const WAIT: Duration = Duration::from_millis(500);
const INTERVAL: Duration = Duration::from_millis(10);

/// 获取独占锁；时限内一直被占用时返回 WouldBlock，其他错误立即返回。
pub fn lock_exclusive(file: &File) -> Result<(), TryLockError> {
    let deadline = Instant::now() + WAIT;
    loop {
        match file.try_lock() {
            Err(TryLockError::WouldBlock) if Instant::now() < deadline => thread::sleep(INTERVAL),
            result => return result,
        }
    }
}

#[cfg(test)]
mod tests {
    //! 用临时锁文件验证重试与时限；执行 cargo test --locked --lib file_lock。
    use super::{lock_exclusive, WAIT};
    use std::{
        fs::{File, OpenOptions, TryLockError},
        path::PathBuf,
        time::{Duration, Instant},
    };

    /// 每个用例使用独立的锁文件，结束时删除。
    struct LockFile(PathBuf);
    impl LockFile {
        /// 在系统临时目录创建随机命名的锁文件路径。
        fn new() -> Self {
            Self(std::env::temp_dir().join(format!("imv-lock-test-{}", uuid::Uuid::new_v4())))
        }
        /// 每次打开得到新的打开文件描述，与业务代码每次操作重新打开锁文件一致。
        fn open(&self) -> File {
            OpenOptions::new()
                .read(true)
                .write(true)
                .create(true)
                .truncate(false)
                .open(&self.0)
                .unwrap()
        }
    }
    impl Drop for LockFile {
        /// 即使断言失败也删除锁文件。
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }

    /// 回归：并行测试或客户端里其他线程创建子进程时，子进程在 exec 前继承了刚释放的锁的描述符；
    /// 用同一打开文件描述的副本模拟它，副本稍后关闭时重新获取成功，而不是立即报告竞争。
    #[cfg(unix)]
    #[test]
    fn waits_for_lock_briefly_held_by_inherited_descriptor() {
        let path = LockFile::new();
        let first = path.open();
        first.try_lock().unwrap();
        let inherited = first.try_clone().unwrap();
        drop(first);
        let release = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            drop(inherited);
        });
        let second = path.open();
        assert!(matches!(second.try_lock(), Err(TryLockError::WouldBlock)));
        lock_exclusive(&second).unwrap();
        release.join().unwrap();
    }

    /// 一直被其他操作占用时，等满时限后报告竞争，交给调用方提示用户重试。
    #[test]
    fn reports_contention_after_wait() {
        let path = LockFile::new();
        let holder = path.open();
        holder.try_lock().unwrap();
        let started = Instant::now();
        assert!(matches!(
            lock_exclusive(&path.open()),
            Err(TryLockError::WouldBlock)
        ));
        assert!(started.elapsed() >= WAIT);
    }
}
