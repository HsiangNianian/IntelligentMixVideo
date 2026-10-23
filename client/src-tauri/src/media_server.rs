//! 预览媒体服务：在 127.0.0.1 的系统分配端口提供内置示例视频与本机保存的预览视频，支持单个字节范围请求，
//! 供模板预览的 IMS SDK 与基础视频播放。网页里的 blob URL 在系统 WebKitGTK 中让 SDK 的 `<video>` 一直等不到
//! canplaythrough（SDK 报告在播放、画面却定格），Chromium 里也明显更晚就绪；同一视频走 HTTP 时各平台都能正常缓冲。
//! 地址带每次启动随机生成的令牌，只接受精确的 `127.0.0.1:端口` 主机与 GET/HEAD（另应答 CORS 预检）；
//! 本机视频只认各槽位元数据当前指向的数据文件（见 `preview_videos::open`）。固定数量的工作线程并发应答，
//! 长视频的读取不阻塞其他请求，同时避免本机大量请求无限创建线程；监听随进程退出释放。

use std::fs::File;
use std::io::{self, Cursor, Read, Seek, SeekFrom};
use std::sync::{Arc, OnceLock};
use tiny_http::{Header, Method, Request, Response, Server, StatusCode};
use uuid::Uuid;

/// 媒体服务地址前缀 `http://127.0.0.1:端口/令牌`；前端拼上 `/sample/N` 或 `/local/数据文件名`。
pub struct MediaServer(pub String);

/// 同时应答请求的工作线程数；预览最多三段视频，留出范围请求余量即可。
const WORKERS: usize = 4;

/// 内置示例：按编号 1～3 返回完整字节。
type SampleSource = Box<dyn Fn(u8) -> Option<Arc<Vec<u8>>> + Send + Sync>;
/// 本机视频：按数据文件名打开，返回文件与 MIME，不存在为 None。
type LocalSource = Box<dyn Fn(&str) -> Result<Option<(File, String)>, String> + Send + Sync>;

/// 媒体来源。
pub struct Sources {
    pub sample: SampleSource,
    pub local: LocalSource,
}

/// 绑定回环端口并在后台应答；绑定失败返回错误，由调用方决定是否阻止启动。
pub fn start(sources: Sources) -> io::Result<MediaServer> {
    let server = Server::http(("127.0.0.1", 0)).map_err(io::Error::other)?;
    let port = server
        .server_addr()
        .to_ip()
        .ok_or_else(|| io::Error::other("媒体服务没有绑定 IP 地址"))?
        .port();
    let host = format!("127.0.0.1:{port}");
    let token = Uuid::new_v4().simple().to_string();
    let base = format!("http://{host}/{token}");
    let server = Arc::new(server);
    let context = Arc::new((sources, host, token));
    for index in 0..WORKERS {
        let server = Arc::clone(&server);
        let context = Arc::clone(&context);
        std::thread::Builder::new()
            .name(format!("preview-media-{index}"))
            .spawn(move || {
                // recv 阻塞等待请求；服务关闭后返回错误，工作线程随之退出。
                while let Ok(request) = server.recv() {
                    let (sources, host, token) = &*context;
                    let _ = respond(request, host, token, sources);
                }
            })
            .map_err(io::Error::other)?;
    }
    Ok(MediaServer(base))
}

/// 内置示例视频的读取：正式包从内嵌资源读取（Tauri 内嵌时压缩，首次请求解压一次后缓存，之后的范围请求不再解压）；
/// 开发模式不内嵌前端资源，改读源码 `public/preview/`。编号 1～3 之外与读不到时为 None。
pub fn samples<R: tauri::Runtime>(
    assets: tauri::AssetResolver<R>,
) -> impl Fn(u8) -> Option<Arc<Vec<u8>>> + Send + Sync + 'static {
    let cache: [OnceLock<Option<Arc<Vec<u8>>>>; 3] = Default::default();
    move |number| {
        let slot = cache.get(usize::from(number).checked_sub(1)?)?;
        slot.get_or_init(|| {
            let path = format!("/preview/sample-{number}.mp4");
            let bytes = if tauri::is_dev() {
                std::fs::read(format!("{}/../public{path}", env!("CARGO_MANIFEST_DIR"))).ok()
            } else {
                assets.get(path).map(|asset| asset.bytes)
            };
            bytes.map(Arc::new)
        })
        .clone()
    }
}

/// 前端读取媒体服务地址前缀。
#[tauri::command]
pub fn preview_media_base(server: tauri::State<'_, MediaServer>) -> String {
    server.0.clone()
}

/// 要发送的媒体：内置示例在内存里，本机视频在磁盘上。
enum Media {
    Memory(Arc<Vec<u8>>),
    Disk(File),
}

/// 让共享的示例字节可以交给 `Cursor` 读取，不复制。
struct Shared(Arc<Vec<u8>>);
impl AsRef<[u8]> for Shared {
    fn as_ref(&self) -> &[u8] {
        &self.0
    }
}

/// 校验主机、方法与令牌后按路径找到媒体并按范围应答；其他情况返回 403/404/405/416，不说明原因。
fn respond(request: Request, host: &str, token: &str, sources: &Sources) -> io::Result<()> {
    let header = |name: &'static str| {
        request
            .headers()
            .iter()
            .find(|header| header.field.equiv(name))
            .map(|header| header.value.as_str().to_owned())
    };
    if header("Host").as_deref() != Some(host) {
        return request.respond(empty(403));
    }
    if *request.method() == Method::Options {
        let response = empty(204)
            .with_header(field("Access-Control-Allow-Methods", "GET, HEAD"))
            .with_header(field("Access-Control-Allow-Headers", "Range"));
        return request.respond(response);
    }
    if !matches!(request.method(), Method::Get | Method::Head) {
        return request.respond(empty(405));
    }
    let path = request
        .url()
        .split('?')
        .next()
        .unwrap_or_default()
        .to_owned();
    let found = path
        .strip_prefix('/')
        .and_then(|path| path.strip_prefix(token))
        .and_then(|path| path.strip_prefix('/'))
        .map(|route| match route.split_once('/') {
            Some(("sample", number)) => Ok(number
                .parse()
                .ok()
                .and_then(|number| (sources.sample)(number))
                .map(|bytes| (Media::Memory(bytes), "video/mp4".to_owned()))),
            Some(("local", file)) => (sources.local)(file)
                .map(|found| found.map(|(file, kind)| (Media::Disk(file), kind))),
            _ => Ok(None),
        });
    let (media, kind) = match found {
        Some(Ok(Some(found))) => found,
        Some(Err(_)) => return request.respond(empty(500)),
        _ => return request.respond(empty(404)),
    };
    let len = match &media {
        Media::Memory(bytes) => bytes.len() as u64,
        Media::Disk(file) => file.metadata()?.len(),
    };
    let (status, start, end) = match byte_range(header("Range").as_deref(), len) {
        Ok(None) => (200, 0, len.saturating_sub(1)),
        Ok(Some((start, end))) => (206, start, end),
        Err(()) => {
            let response =
                empty(416).with_header(field("Content-Range", &format!("bytes */{len}")));
            return request.respond(response);
        }
    };
    let size = if len == 0 { 0 } else { end - start + 1 };
    let body: Box<dyn Read + Send> = match media {
        Media::Memory(bytes) => {
            let mut cursor = Cursor::new(Shared(bytes));
            cursor.set_position(start);
            Box::new(cursor.take(size))
        }
        Media::Disk(mut file) => {
            file.seek(SeekFrom::Start(start))?;
            Box::new(file.take(size))
        }
    };
    let mut headers = vec![
        Header::from_bytes("Content-Type", kind.as_bytes())
            .unwrap_or_else(|_| field("Content-Type", "application/octet-stream")),
        field("Accept-Ranges", "bytes"),
        field("Cache-Control", "no-store"),
        field("Access-Control-Allow-Origin", "*"),
    ];
    if status == 206 {
        headers.push(field(
            "Content-Range",
            &format!("bytes {start}-{end}/{len}"),
        ));
    }
    request.respond(Response::new(
        StatusCode(status),
        headers,
        body,
        Some(size as usize),
        None,
    ))
}

/// 解析单个字节范围（`bytes=a-b`、`bytes=a-`、`bytes=-n`），返回闭区间。没有 Range、写法无法识别或请求多个范围时
/// 返回 None 发送完整内容（HTTP 允许忽略 Range）；起点越过结尾或后缀长度为 0 时返回 Err，应答 416。
fn byte_range(header: Option<&str>, len: u64) -> Result<Option<(u64, u64)>, ()> {
    let Some(spec) = header.and_then(|value| value.trim().strip_prefix("bytes=")) else {
        return Ok(None);
    };
    let Some((first, last)) = spec.split_once('-').filter(|_| !spec.contains(',')) else {
        return Ok(None);
    };
    let number = |value: &str| value.trim().parse::<u64>().ok();
    let (start, end) = match (first.trim().is_empty(), last.trim().is_empty()) {
        (true, true) => return Ok(None),
        (true, false) => match number(last) {
            Some(0) => return Err(()),
            Some(suffix) => (len.saturating_sub(suffix), len.checked_sub(1).ok_or(())?),
            None => return Ok(None),
        },
        (false, true) => match number(first) {
            Some(start) => (start, len.checked_sub(1).ok_or(())?),
            None => return Ok(None),
        },
        (false, false) => match (number(first), number(last)) {
            (Some(start), Some(end)) if start <= end => (start, end.min(len.saturating_sub(1))),
            _ => return Ok(None),
        },
    };
    if start >= len || start > end {
        Err(())
    } else {
        Ok(Some((start, end)))
    }
}

/// 没有正文的应答，带上跨域许可（SDK 以 crossOrigin 读取视频并画进 WebGL）。
fn empty(status: u16) -> Response<io::Empty> {
    Response::new_empty(StatusCode(status)).with_header(field("Access-Control-Allow-Origin", "*"))
}

/// 由固定名称与 ASCII 值组成的响应头。
fn field(name: &str, value: &str) -> Header {
    Header::from_bytes(name, value).expect("ASCII 响应头")
}

#[cfg(test)]
mod tests {
    //! 真实回环 HTTP 验证令牌、主机、方法、内置示例与本机文件的完整与范围读取，以及范围解析的边界；
    //! 执行 cargo test --locked --lib media_server。
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    /// 示例 1 为 0～9 十个字节；本机文件 `video-1-abc` 为固定内容，其余名称不存在，`video-2-bad` 读取出错。
    fn server(directory: &std::path::Path) -> MediaServer {
        let path = directory.join("video-1-abc");
        std::fs::write(&path, b"local video bytes").unwrap();
        start(Sources {
            sample: Box::new(|number| (number == 1).then(|| Arc::new(b"0123456789".to_vec()))),
            local: Box::new(move |file| match file {
                "video-1-abc" => Ok(Some((File::open(&path).unwrap(), "video/webm".into()))),
                "video-2-bad" => Err("读取失败".into()),
                _ => Ok(None),
            }),
        })
        .unwrap()
    }

    /// 发送一次请求并返回完整原始响应；`host` 为 None 时使用服务自己的主机。
    fn send(base: &str, method: &str, path: &str, host: Option<&str>, extra: &str) -> String {
        let authority = base
            .strip_prefix("http://")
            .unwrap()
            .split('/')
            .next()
            .unwrap();
        let mut stream = TcpStream::connect(authority).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let host = host.unwrap_or(authority);
        write!(
            stream,
            "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n{extra}\r\n"
        )
        .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).unwrap();
        response
    }

    /// 令牌后的路径。
    fn route(base: &str, rest: &str) -> String {
        format!("/{}{rest}", base.rsplit('/').next().unwrap())
    }

    /// 示例与本机文件的完整读取、范围读取、HEAD 与越界范围。
    #[test]
    fn serves_samples_and_local_files_with_ranges() {
        let directory =
            std::env::temp_dir().join(format!("imv-media-server-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let MediaServer(base) = server(&directory);

        let full = send(&base, "GET", &route(&base, "/sample/1?t=1"), None, "");
        assert!(full.starts_with("HTTP/1.1 200"), "{full}");
        assert!(
            full.contains("Accept-Ranges: bytes")
                && full.contains("Access-Control-Allow-Origin: *")
        );
        assert!(full.contains("Content-Type: video/mp4") && full.ends_with("\r\n\r\n0123456789"));

        let part = send(
            &base,
            "GET",
            &route(&base, "/sample/1"),
            None,
            "Range: bytes=2-5\r\n",
        );
        assert!(
            part.starts_with("HTTP/1.1 206") && part.contains("Content-Range: bytes 2-5/10"),
            "{part}"
        );
        assert!(part.ends_with("\r\n\r\n2345"));
        assert!(send(
            &base,
            "GET",
            &route(&base, "/sample/1"),
            None,
            "Range: bytes=-3\r\n"
        )
        .ends_with("\r\n\r\n789"));

        let local = send(
            &base,
            "GET",
            &route(&base, "/local/video-1-abc"),
            None,
            "Range: bytes=6-\r\n",
        );
        assert!(
            local.starts_with("HTTP/1.1 206") && local.contains("Content-Type: video/webm"),
            "{local}"
        );
        assert!(
            local.contains("Content-Range: bytes 6-16/17")
                && local.ends_with("\r\n\r\nvideo bytes")
        );

        let head = send(&base, "HEAD", &route(&base, "/local/video-1-abc"), None, "");
        assert!(
            head.starts_with("HTTP/1.1 200")
                && head.contains("Content-Length: 17")
                && head.ends_with("\r\n\r\n"),
            "{head}"
        );

        let beyond = send(
            &base,
            "GET",
            &route(&base, "/sample/1"),
            None,
            "Range: bytes=10-\r\n",
        );
        assert!(
            beyond.starts_with("HTTP/1.1 416") && beyond.contains("Content-Range: bytes */10"),
            "{beyond}"
        );
        let _ = std::fs::remove_dir_all(&directory);
    }

    /// 令牌、主机、方法或媒体不符时拒绝；预检带跨域许可；读取出错为 500。
    #[test]
    fn rejects_other_hosts_tokens_methods_and_media() {
        let directory =
            std::env::temp_dir().join(format!("imv-media-server-test-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let MediaServer(base) = server(&directory);
        for (method, path, host, status) in [
            ("GET", "/0123/sample/1".to_owned(), None, 404),
            ("GET", route(&base, "/sample/1"), Some("localhost"), 403),
            ("POST", route(&base, "/sample/1"), None, 405),
            ("GET", route(&base, "/sample/2"), None, 404),
            ("GET", route(&base, "/sample/x"), None, 404),
            ("GET", route(&base, "/local/..%2Fvideo-1-abc"), None, 404),
            ("GET", route(&base, "/local/video-2-bad"), None, 500),
            ("GET", route(&base, "/other/1"), None, 404),
            ("OPTIONS", route(&base, "/sample/1"), None, 204),
        ] {
            let response = send(&base, method, &path, host, "Content-Length: 0\r\n");
            assert!(
                response.starts_with(&format!("HTTP/1.1 {status}")),
                "{method} {path}: {response}"
            );
            if method == "OPTIONS" {
                assert!(response.contains("Access-Control-Allow-Headers: Range"));
            }
        }
        let _ = std::fs::remove_dir_all(&directory);
    }

    /// 范围解析：完整、闭区间、开放结尾、后缀、截到结尾、多个范围与无法识别的写法忽略，越界为 Err。
    #[test]
    fn parses_single_byte_ranges() {
        for (header, expected) in [
            (None, Ok(None)),
            (Some("bytes=0-0"), Ok(Some((0, 0)))),
            (Some("bytes=3-"), Ok(Some((3, 9)))),
            (Some("bytes=-4"), Ok(Some((6, 9)))),
            (Some("bytes=-40"), Ok(Some((0, 9)))),
            (Some("bytes=5-99"), Ok(Some((5, 9)))),
            (Some("bytes=1-2,4-5"), Ok(None)),
            (Some("bytes=a-b"), Ok(None)),
            (Some("items=1-2"), Ok(None)),
            (Some("bytes=10-"), Err(())),
            (Some("bytes=-0"), Err(())),
        ] {
            assert_eq!(byte_range(header, 10), expected, "{header:?}");
        }
        assert_eq!(byte_range(Some("bytes=0-"), 0), Err(()));
    }
}
