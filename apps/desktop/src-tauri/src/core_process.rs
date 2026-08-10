use std::env;
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use uuid::Uuid;

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
struct ProcessReadiness {
    #[serde(rename = "type")]
    message_type: String,
    port: u16,
    protocol_version: u32,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct CoreConnectionInfo {
    pub status: String,
    pub port: u16,
    pub token: String,
    pub protocol_version: u32,
}

#[derive(Debug)]
enum StartupStatus {
    Starting,
    Ready(u16),
    Offline(String),
}

type StartupSignal = Arc<(Mutex<StartupStatus>, Condvar)>;
type SharedChild = Arc<Mutex<Option<Child>>>;

#[derive(Clone)]
pub struct CoreProcessManager {
    child: SharedChild,
    startup: StartupSignal,
    token: String,
}

fn parse_process_readiness(line: &str) -> Result<ProcessReadiness, String> {
    let readiness: ProcessReadiness =
        serde_json::from_str(line).map_err(|error| format!("invalid lifecycle JSON: {error}"))?;

    if readiness.message_type != "process.ready" {
        return Err("expected process.ready lifecycle message".to_string());
    }
    if readiness.port == 0 {
        return Err("process.ready port must be non-zero".to_string());
    }
    if readiness.protocol_version != PROTOCOL_VERSION {
        return Err(format!(
            "unsupported lifecycle protocol version: {}",
            readiness.protocol_version
        ));
    }

    Ok(readiness)
}

impl CoreProcessManager {
    pub fn start() -> Result<Self, String> {
        let python = env::var_os("JARVIS_PYTHON")
            .ok_or_else(|| "JARVIS_PYTHON is not set; run scripts/dev.ps1".to_string())?;
        Self::start_with_python(Path::new(&python))
    }

    fn start_with_python(python: impl AsRef<Path>) -> Result<Self, String> {
        Self::start_with_python_config(python.as_ref(), None)
    }

    #[cfg(test)]
    fn start_with_python_and_data_dir(
        python: impl AsRef<Path>,
        data_dir: impl AsRef<Path>,
    ) -> Result<Self, String> {
        Self::start_with_python_config(python.as_ref(), Some(data_dir.as_ref()))
    }

    fn start_with_python_config(python: &Path, data_dir: Option<&Path>) -> Result<Self, String> {
        let token = Uuid::new_v4().to_string();
        let mut command = Command::new(python);
        command
            .arg("-m")
            .arg("jarvis_core")
            .env("JARVIS_AUTH_TOKEN", &token)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        if let Some(data_dir) = data_dir {
            command.env("JARVIS_DATA_DIR", data_dir);
        }

        #[cfg(windows)]
        command.creation_flags(0x0800_0000);

        let mut child = command
            .spawn()
            .map_err(|error| format!("failed to start Python Core: {error}"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "Python Core stdout was not piped".to_string())?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| "Python Core stderr was not piped".to_string())?;

        let child = Arc::new(Mutex::new(Some(child)));
        let startup = Arc::new((Mutex::new(StartupStatus::Starting), Condvar::new()));

        read_lifecycle_stdout(stdout, Arc::clone(&startup));
        forward_stderr(stderr);
        monitor_process(Arc::clone(&child), Arc::clone(&startup));

        Ok(Self {
            child,
            startup,
            token,
        })
    }

    pub fn wait_for_connection(&self, timeout: Duration) -> Result<CoreConnectionInfo, String> {
        let deadline = Instant::now() + timeout;
        let (status_lock, status_changed) = &*self.startup;
        let mut status = status_lock
            .lock()
            .map_err(|_| "Core startup state lock was poisoned".to_string())?;

        loop {
            match &*status {
                StartupStatus::Ready(port) => {
                    return Ok(CoreConnectionInfo {
                        status: "READY".to_string(),
                        port: *port,
                        token: self.token.clone(),
                        protocol_version: PROTOCOL_VERSION,
                    });
                }
                StartupStatus::Offline(reason) => return Err(reason.clone()),
                StartupStatus::Starting => {}
            }

            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err("timed out waiting for Python Core process.ready".to_string());
            }

            let (next_status, wait_result) = status_changed
                .wait_timeout(status, remaining)
                .map_err(|_| "Core startup state lock was poisoned".to_string())?;
            status = next_status;
            if wait_result.timed_out() && matches!(*status, StartupStatus::Starting) {
                return Err("timed out waiting for Python Core process.ready".to_string());
            }
        }
    }

    pub fn shutdown(&self) -> Result<(), String> {
        set_startup_status(
            &self.startup,
            StartupStatus::Offline("Python Core stopped".to_string()),
        );
        let child = self
            .child
            .lock()
            .map_err(|_| "Python Core process lock was poisoned".to_string())?
            .take();

        if let Some(mut child) = child {
            match child
                .try_wait()
                .map_err(|error| format!("failed to inspect Python Core: {error}"))?
            {
                Some(_) => {}
                None => {
                    child
                        .kill()
                        .map_err(|error| format!("failed to kill Python Core: {error}"))?;
                    child
                        .wait()
                        .map_err(|error| format!("failed to wait for Python Core: {error}"))?;
                }
            }
        }

        Ok(())
    }

    #[cfg(test)]
    fn is_running(&self) -> bool {
        let Ok(mut child) = self.child.lock() else {
            return false;
        };
        let Some(child_process) = child.as_mut() else {
            return false;
        };

        match child_process.try_wait() {
            Ok(None) => true,
            Ok(Some(_)) | Err(_) => {
                *child = None;
                false
            }
        }
    }
}

fn read_lifecycle_stdout(stdout: impl std::io::Read + Send + 'static, startup: StartupSignal) {
    thread::spawn(move || {
        let mut received_readiness = false;
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => match parse_process_readiness(&line) {
                    Ok(readiness) if !received_readiness => {
                        received_readiness = true;
                        set_startup_status(&startup, StartupStatus::Ready(readiness.port));
                    }
                    Ok(_) => {
                        eprintln!("[jarvis-core] duplicate process.ready lifecycle message");
                    }
                    Err(error) => {
                        eprintln!("[jarvis-core] invalid stdout lifecycle message: {error}");
                        set_startup_status(&startup, StartupStatus::Offline(error));
                        return;
                    }
                },
                Err(error) => {
                    let reason = format!("failed to read Python Core stdout: {error}");
                    eprintln!("[jarvis-core] {reason}");
                    set_startup_status(&startup, StartupStatus::Offline(reason));
                    return;
                }
            }
        }

        if !received_readiness {
            set_startup_status(
                &startup,
                StartupStatus::Offline(
                    "Python Core stdout closed before process.ready".to_string(),
                ),
            );
        }
    });
}

fn forward_stderr(stderr: impl std::io::Read + Send + 'static) {
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines() {
            match line {
                Ok(line) => eprintln!("[jarvis-core] {line}"),
                Err(error) => {
                    eprintln!("[jarvis-core] failed to read stderr: {error}");
                    return;
                }
            }
        }
    });
}

fn monitor_process(child: SharedChild, startup: StartupSignal) {
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(100));
        let exit = {
            let Ok(mut child_guard) = child.lock() else {
                set_startup_status(
                    &startup,
                    StartupStatus::Offline("Python Core process lock was poisoned".to_string()),
                );
                return;
            };
            let Some(child_process) = child_guard.as_mut() else {
                return;
            };

            match child_process.try_wait() {
                Ok(Some(status)) => {
                    *child_guard = None;
                    Some(Ok(status))
                }
                Ok(None) => None,
                Err(error) => {
                    *child_guard = None;
                    Some(Err(error))
                }
            }
        };

        match exit {
            Some(Ok(status)) => {
                set_startup_status(
                    &startup,
                    StartupStatus::Offline(format!("Python Core exited with {status}")),
                );
                return;
            }
            Some(Err(error)) => {
                set_startup_status(
                    &startup,
                    StartupStatus::Offline(format!("failed to monitor Python Core: {error}")),
                );
                return;
            }
            None => {}
        }
    });
}

fn set_startup_status(startup: &StartupSignal, status: StartupStatus) {
    let (status_lock, status_changed) = &**startup;
    if let Ok(mut current) = status_lock.lock() {
        if matches!(*current, StartupStatus::Offline(_)) {
            return;
        }
        *current = status;
        status_changed.notify_all();
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::{Arc, Condvar, Mutex};
    use std::time::Duration;

    use super::{parse_process_readiness, set_startup_status, CoreProcessManager, StartupStatus};

    fn core_python() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../core/.venv/Scripts/python.exe")
    }

    struct TestDataDir {
        path: PathBuf,
    }

    impl TestDataDir {
        fn unique() -> Self {
            let path = std::env::temp_dir()
                .join(format!("jarvis-rust-memory-test-{}", uuid::Uuid::new_v4()));
            fs::create_dir_all(&path).expect("temporary JARVIS data directory");
            Self { path }
        }
    }

    impl Drop for TestDataDir {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.path);
        }
    }

    #[test]
    fn parses_process_ready_lifecycle_message() {
        let readiness =
            parse_process_readiness(r#"{"type":"process.ready","port":54321,"protocolVersion":1}"#)
                .expect("valid process.ready message");

        assert_eq!(readiness.port, 54321);
        assert_eq!(readiness.protocol_version, 1);
    }

    #[test]
    fn rejects_unknown_lifecycle_message() {
        let error =
            parse_process_readiness(r#"{"type":"core.ready","port":54321,"protocolVersion":1}"#)
                .expect_err("WebSocket readiness is not process readiness");

        assert!(error.contains("process.ready"));
    }

    #[test]
    fn rejects_zero_port_and_incompatible_protocol() {
        let zero_port =
            parse_process_readiness(r#"{"type":"process.ready","port":0,"protocolVersion":1}"#)
                .expect_err("port zero is not an actual bound port");
        assert!(zero_port.contains("port"));

        let wrong_version =
            parse_process_readiness(r#"{"type":"process.ready","port":54321,"protocolVersion":2}"#)
                .expect_err("unsupported protocol must be rejected");
        assert!(wrong_version.contains("protocol"));
    }

    #[test]
    fn offline_startup_status_cannot_be_overwritten_by_late_readiness() {
        let startup = Arc::new((Mutex::new(StartupStatus::Starting), Condvar::new()));

        set_startup_status(
            &startup,
            StartupStatus::Offline("process exited".to_string()),
        );
        set_startup_status(&startup, StartupStatus::Ready(54321));

        let (status, _) = &*startup;
        let status = status.lock().expect("startup status lock");
        assert!(matches!(
            &*status,
            StartupStatus::Offline(reason) if reason == "process exited"
        ));
    }

    #[test]
    fn starts_core_authenticates_and_reaps_it() {
        let data_dir = TestDataDir::unique();
        let manager =
            CoreProcessManager::start_with_python_and_data_dir(core_python(), &data_dir.path)
                .expect("Python Core should start from the bootstrap virtual environment");

        let connection = manager
            .wait_for_connection(Duration::from_secs(10))
            .expect("Core should emit process.ready");

        assert_eq!(connection.status, "READY");
        assert_ne!(connection.port, 0);
        assert_eq!(connection.protocol_version, 1);
        assert!(!connection.token.is_empty());
        assert!(manager.is_running());
        assert!(data_dir.path.join("memory.db").is_file());

        let probe = r#"
import asyncio
import json
import sys

from websockets.asyncio.client import connect


async def main():
    port = int(sys.argv[1])
    token = sys.argv[2]
    async with connect(f"ws://127.0.0.1:{port}") as websocket:
        await websocket.send(json.dumps({
            "version": 1,
            "type": "auth",
            "payload": {"token": token},
        }))
        ready = json.loads(await websocket.recv())
        assert ready == {
            "version": 1,
            "type": "core.ready",
            "payload": {"state": "IDLE"},
        }


asyncio.run(main())
"#;
        let probe_output = Command::new(core_python())
            .arg("-c")
            .arg(probe)
            .arg(connection.port.to_string())
            .arg(&connection.token)
            .output();

        manager
            .shutdown()
            .expect("Core should be killed and waited");
        assert!(!manager.is_running());

        let probe_output = probe_output.expect("heartbeat probe should run");
        assert!(
            probe_output.status.success(),
            "heartbeat probe failed: {}",
            String::from_utf8_lossy(&probe_output.stderr)
        );
    }
}
