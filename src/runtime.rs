use crate::platform::Platform;
use async_channel::Sender;
use serde_json::{Value, json};
use std::{
    io::{BufRead, BufReader, Write},
    path::Path,
    process::{Command, Stdio},
    sync::mpsc,
    thread,
};

#[derive(Debug)]
pub enum Event {
    Bridge(Value),
    Observation(Value),
    Microphone(u64, Value),
    Failure(String),
    BridgeClosed(String),
}

pub struct Runtime {
    pub platform: Platform,
    commands: mpsc::Sender<Value>,
    events: Sender<Event>,
}

impl Runtime {
    pub fn start(contents: &Path, events: Sender<Event>) -> Result<Self, String> {
        let platform = Platform::load(&contents.join("Frameworks/libVoiceDesktop.dylib"))?;
        let trace_directory = contents
            .parent()
            .and_then(Path::parent)
            .ok_or("App trace directory unavailable")?
            .join("validation");
        let mut child = Command::new("/usr/bin/python3")
            .args(["-u"])
            .arg(contents.join("Resources/bridge.py"))
            .arg("--trace-dir")
            .arg(trace_directory)
            .current_dir(std::env::temp_dir())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|e| format!("Cannot start native voice bridge: {e}"))?;
        let mut input = child.stdin.take().ok_or("Bridge input unavailable")?;
        let output = child.stdout.take().ok_or("Bridge output unavailable")?;
        let (commands, receiver) = mpsc::channel::<Value>();
        let writer_events = events.clone();
        let writer_platform = platform.clone();
        thread::spawn(move || {
            for message in receiver {
                let ending = message["type"] == "shutdown";
                if writeln!(input, "{message}")
                    .and_then(|_| input.flush())
                    .is_err()
                {
                    writer_platform.control(json!({"operation": "stop"}));
                    let _ = writer_events.send_blocking(Event::BridgeClosed(
                        "Voice bridge disconnected. Restart Voice App to reconnect.".into(),
                    ));
                    break;
                }
                if ending {
                    break;
                }
            }
        });
        let reader_events = events.clone();
        let reader_commands = commands.clone();
        let reader_platform = platform.clone();
        thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                let Ok(line) = line else { break };
                if line.len() > 8 * 1024 * 1024 {
                    break;
                }
                let Ok(message) = serde_json::from_str::<Value>(&line) else {
                    continue;
                };
                if message["type"] == "desktop" {
                    let id = message["id"].clone();
                    let event_sender = reader_events.clone();
                    let command_sender = reader_commands.clone();
                    let reply = move |result: Value| {
                        if let Some(observation) = result.get("observation") {
                            let _ =
                                event_sender.send_blocking(Event::Observation(observation.clone()));
                        }
                        let _ = command_sender
                            .send(json!({"type": "desktopResult", "id": id, "result": result}));
                    };
                    if matches!(
                        message["operation"].as_str(),
                        Some("stop" | "pause" | "resume")
                    ) {
                        reply(reader_platform.control(message));
                    } else {
                        reader_platform.request(message, reply);
                    }
                } else {
                    let _ = reader_events.send_blocking(Event::Bridge(message));
                }
            }
            reader_platform.control(json!({"operation": "stop"}));
            let _ = reader_events.send_blocking(Event::BridgeClosed(
                "Voice bridge ended. Restart Voice App to reconnect.".into(),
            ));
            let _ = child.wait();
        });
        Ok(Self {
            platform,
            commands,
            events,
        })
    }

    pub fn send(&self, message: Value) {
        let _ = self.commands.send(message);
    }

    pub fn observe(&self) {
        let events = self.events.clone();
        self.platform
            .request(json!({"operation": "observe"}), move |result| {
                let event = match result.get("observation") {
                    Some(value) => Event::Observation(value.clone()),
                    None => Event::Failure(
                        result["error"]
                            .as_str()
                            .unwrap_or("Desktop observation failed.")
                            .into(),
                    ),
                };
                let _ = events.send_blocking(event);
            });
    }

    pub fn request_microphone(&self, generation: u64) {
        let events = self.events.clone();
        self.platform
            .request(json!({"operation": "microphone"}), move |result| {
                let _ = events.send_blocking(Event::Microphone(generation, result));
            });
    }

    pub fn stop(&self) -> Value {
        // Gate acknowledgement is synchronous and in this process. Sending cancellation is independent.
        let result = self.platform.control(json!({"operation": "stop"}));
        self.send(json!({"type": "stop"}));
        result
    }

    pub fn shutdown(&self) {
        self.platform.control(json!({"operation": "shutdown"}));
        self.send(json!({"type": "shutdown"}));
    }
}
