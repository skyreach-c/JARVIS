mod core_process;

use std::io;
use std::time::Duration;

use core_process::{CoreConnectionInfo, CoreProcessManager};
use tauri::{LogicalSize, Manager, RunEvent, State, WebviewWindow};

#[cfg(test)]
mod tests {
    use super::window_size;

    #[test]
    fn expands_and_collapses_the_same_window_to_locked_sizes() {
        assert_eq!(window_size(false), (116.0, 140.0));
        assert_eq!(window_size(true), (360.0, 440.0));
    }
}

fn window_size(expanded: bool) -> (f64, f64) {
    if expanded {
        (360.0, 440.0)
    } else {
        (116.0, 140.0)
    }
}

#[tauri::command]
async fn get_core_connection(
    core: State<'_, CoreProcessManager>,
) -> Result<CoreConnectionInfo, String> {
    let core = core.inner().clone();
    tauri::async_runtime::spawn_blocking(move || core.wait_for_connection(Duration::from_secs(10)))
        .await
        .map_err(|error| format!("Core readiness task failed: {error}"))?
}

#[tauri::command]
fn set_expanded(window: WebviewWindow, expanded: bool) -> Result<(), String> {
    let (width, height) = window_size(expanded);
    window
        .set_size(LogicalSize::new(width, height))
        .map_err(|error| format!("failed to resize JARVIS window: {error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let core = CoreProcessManager::start().map_err(io::Error::other)?;
            app.manage(core);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_core_connection, set_expanded])
        .build(tauri::generate_context!())
        .expect("error while building JARVIS");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit) {
            if let Some(core) = app_handle.try_state::<CoreProcessManager>() {
                if let Err(error) = core.shutdown() {
                    eprintln!("[jarvis-desktop] failed to stop Python Core: {error}");
                }
            }
        }
    });
}
