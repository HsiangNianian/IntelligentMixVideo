//! Windows 静态资源服务：先绑定空闲回环端口，再提供打包资源；进程退出时释放监听。

use std::io;
use tauri::{AssetResolver, Runtime, Url};
use tiny_http::{Header, Method, Request, Response, Server};

/// 返回真实 localhost 地址；端口由系统分配，绑定失败直接阻止窗口启动。
pub fn start<R: Runtime>(assets: AssetResolver<R>) -> Result<Url, Box<dyn std::error::Error>> {
    let server = Server::http(("127.0.0.1", 0)).map_err(io::Error::other)?;
    let authority = format!("localhost:{}", server.server_addr().to_ip().unwrap().port());
    let url = Url::parse(&format!("http://{authority}"))?;
    std::thread::spawn(move || {
        for request in server.incoming_requests() {
            // 页面切换或关闭可以中断响应，不让客户端断连终止资源服务。
            let _ = respond(request, &authority, |path| {
                assets
                    .get(path)
                    .map(|asset| (asset.bytes, asset.mime_type, asset.csp_header))
            });
        }
    });
    Ok(url)
}

/// 仅接受精确 localhost 主机的 GET/HEAD；解析器返回资源字节、MIME 和可选 CSP。
fn respond(
    request: Request,
    authority: &str,
    resolve: impl FnOnce(String) -> Option<(Vec<u8>, String, Option<String>)>,
) -> io::Result<()> {
    let host = request
        .headers()
        .iter()
        .find(|header| header.field.equiv("Host"));
    let response = if host.map(|header| header.value.as_str()) != Some(authority) {
        Response::from_data(Vec::new()).with_status_code(403)
    } else if !matches!(request.method(), Method::Get | Method::Head) {
        Response::from_data(Vec::new()).with_status_code(405)
    } else if let Some((bytes, mime, csp)) =
        resolve(request.url().split('?').next().unwrap_or("/").into())
    {
        let mut response = Response::from_data(bytes);
        for (name, value) in [
            ("Content-Type", Some(mime)),
            ("Content-Security-Policy", csp),
        ] {
            if let Some(value) = value {
                let header = Header::from_bytes(name, value)
                    .map_err(|_| io::Error::other("invalid embedded asset header"))?;
                response.add_header(header);
            }
        }
        response
    } else {
        Response::from_data(Vec::new()).with_status_code(404)
    };
    request.respond(response)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;

    /// 通过真实回环 HTTP 验证资源、查询参数、HEAD、缺失路径和来源/方法限制。
    #[test]
    fn serves_only_embedded_assets_on_the_expected_host() {
        for (method, path, valid_host, expected_status) in [
            ("GET", "/app.js?v=1", true, 200),
            ("HEAD", "/app.js", true, 200),
            ("GET", "/missing", true, 404),
            ("GET", "/app.js", false, 403),
            ("POST", "/app.js", true, 405),
        ] {
            let server = Server::http(("127.0.0.1", 0)).unwrap();
            let address = server.server_addr().to_ip().unwrap();
            assert!(address.ip().is_loopback());
            let authority = format!("localhost:{}", address.port());
            let host = if valid_host {
                authority.clone()
            } else {
                "example.com".into()
            };
            let handler = std::thread::spawn(move || {
                let request = server
                    .recv_timeout(Duration::from_secs(5))
                    .unwrap()
                    .unwrap();
                respond(request, &authority, |path| {
                    (path == "/app.js").then(|| {
                        (
                            b"test asset".to_vec(),
                            "text/javascript".into(),
                            Some("default-src 'self'".into()),
                        )
                    })
                })
                .unwrap();
            });
            let mut stream = TcpStream::connect(address).unwrap();
            stream
                .set_read_timeout(Some(Duration::from_secs(5)))
                .unwrap();
            write!(stream, "{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n").unwrap();
            let mut response = String::new();
            stream.read_to_string(&mut response).unwrap();
            handler.join().unwrap();
            assert!(
                response.starts_with(&format!("HTTP/1.1 {expected_status}")),
                "{response}"
            );
            if expected_status == 200 {
                assert!(response.contains("Content-Type: text/javascript"));
                assert!(response.contains("Content-Security-Policy: default-src 'self'"));
            }
            assert_eq!(
                response.ends_with("test asset"),
                expected_status == 200 && method == "GET"
            );
        }
    }
}
