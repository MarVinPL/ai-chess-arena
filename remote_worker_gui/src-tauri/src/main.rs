#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    arena_worker_lib::run();
}
