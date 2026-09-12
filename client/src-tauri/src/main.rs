//! 原生程序入口将启动交给共享运行模块，发布版 Windows 隐藏控制台。

// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// 调用共享入口启动桌面客户端。
fn main() {
    client_lib::run()
}
