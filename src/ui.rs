use crate::icons::{Icon, icon};
use crate::runtime::{Event, Runtime};
use base64::Engine;
use gpui::{
    AccessibleAction, Div, FocusHandle, FontWeight, Image, ImageFormat, KeyDownEvent, ObjectFit,
    Role, ScrollHandle, SharedString, Stateful, Window, div, img, prelude::*, px, relative, rgb,
};
use serde_json::{Value, json};
use std::{path::PathBuf, sync::Arc};

const SURFACE: u32 = 0x171a1e;
const CARD: u32 = 0x22262c;
const HOVER: u32 = 0x30363e;
const TEXT: u32 = 0xf3f4f5;
const MUTED: u32 = 0xafb6bf;
const ACCENT: u32 = 0xa6e3ce;
const STOP: u32 = 0xae3545;
const STOP_HOVER: u32 = 0xc34153;
const BORDER: u32 = 0x3b424c;

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Action {
    Expand,
    Hide,
    Details,
    Restart,
    Start,
    Mute,
    Silence,
    SubmitTranscript,
    Stop,
    Permissions,
    Quit,
    IdleShorter,
    IdleLonger,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Connection {
    Disconnected,
    Connecting,
    Connected,
    Disconnecting,
}

struct Outcome {
    text: String,
    failed: bool,
}

struct Session {
    status: String,
    message: String,
    email: String,
    plan: String,
    local_speech: bool,
    connection: Connection,
    outcome: Option<Outcome>,
    outcome_current: bool,
    heard: Option<String>,
    heard_partial: bool,
    caption_turn: u64,
    reply_ready: bool,
    shared: bool,
    muted: bool,
    suppressed: bool,
    audio_pending: bool,
    stopped: bool,
    generation: u64,
    wants_session: bool,
    idle_minutes: u64,
    settings_pending: bool,
    settings_error: Option<String>,
}

impl Default for Session {
    fn default() -> Self {
        Self {
            status: "Paused".into(),
            message: "Checking your Codex account…".into(),
            email: String::new(),
            plan: String::new(),
            local_speech: true,
            connection: Connection::Disconnected,
            outcome: None,
            outcome_current: false,
            heard: None,
            heard_partial: false,
            caption_turn: 0,
            reply_ready: false,
            shared: false,
            muted: false,
            suppressed: false,
            audio_pending: false,
            stopped: false,
            generation: 0,
            wants_session: false,
            idle_minutes: 5,
            settings_pending: false,
            settings_error: None,
        }
    }
}

impl Session {
    fn compact_captions(&self) -> Value {
        if self.stopped
            || self.connection == Connection::Disconnected
            || self.connection == Connection::Disconnecting
        {
            return json!({});
        }
        let heard = self.heard.as_deref().unwrap_or("");
        let heard = if self.heard_partial && heard.trim().is_empty() {
            "Listening…"
        } else {
            heard
        };
        let (agent, title) = if self.heard_partial {
            ("", "Codex")
        } else if let Some(outcome) = self.outcome.as_ref().filter(|_| self.outcome_current) {
            (
                outcome.text.as_str(),
                if outcome.failed {
                    "Needs attention"
                } else {
                    "Codex"
                },
            )
        } else if self.status == "Working" && !self.stopped {
            (self.message.as_str(), "Working")
        } else {
            ("", "Codex")
        };
        let completed = !self.heard_partial && self.outcome_current && self.outcome.is_some();
        json!({"user": heard, "agent": agent, "agentTitle": title, "request": self.caption_turn.to_string(),
            "phase": if completed { "complete" } else { "active" }})
    }

    fn should_auto_start(&self) -> bool {
        self.generation == 0
            && !self.email.is_empty()
            && self.connection == Connection::Disconnected
    }

    fn begin_session(&mut self) -> u64 {
        self.generation += 1;
        self.wants_session = true;
        self.connection = Connection::Connecting;
        self.stopped = true;
        self.heard = None;
        self.heard_partial = false;
        self.outcome = None;
        self.outcome_current = false;
        self.reply_ready = false;
        self.audio_pending = false;
        self.muted = false;
        self.suppressed = true;
        self.message = "Starting voice and desktop control…".into();
        self.generation
    }

    fn end_session(&mut self) -> u64 {
        self.generation += 1;
        self.wants_session = false;
        self.shared = false;
        self.connection = Connection::Disconnecting;
        self.heard = None;
        self.heard_partial = false;
        self.stop("Stopping session. Desktop actions are blocked.");
        self.generation
    }

    fn accept_session_started(&mut self, generation: u64) -> bool {
        if !self.wants_session || generation != self.generation {
            return false;
        }
        self.connection = Connection::Connected;
        self.stopped = false;
        self.status = "Listening".into();
        self.message = "Listening. Desktop control is ready.".into();
        true
    }

    fn compact_voice_control(&self) -> (Action, &'static str, bool) {
        match self.connection {
            Connection::Disconnected => (Action::Start, "Start", !self.email.is_empty()),
            Connection::Connecting | Connection::Disconnecting => (Action::Start, "…", false),
            Connection::Connected => (Action::Restart, "Restart", true),
        }
    }

    fn request_audio(&mut self, muted: bool, suppressed: bool) -> Option<Value> {
        if self.connection != Connection::Connected || self.audio_pending {
            return None;
        }
        self.audio_pending = true;
        Some(json!({"type": "audioControls", "muted": muted, "suppressed": suppressed}))
    }

    fn display_status(&self) -> &str {
        if self.generation > 0 {
            match self.connection {
                Connection::Disconnected => return "Session off",
                Connection::Connecting => return "Starting",
                Connection::Disconnecting => return "Stopping",
                _ => {}
            }
        }
        if self.stopped {
            return "Work stopped";
        }
        match self.connection {
            Connection::Connecting => "Connecting",
            Connection::Disconnecting => "Stopping session",
            Connection::Disconnected if self.email.is_empty() => "Checking account",
            Connection::Disconnected => "Session off",
            Connection::Connected if self.status == "Working" => "Working",
            Connection::Connected if self.status == "Speaking" => "Playing reply",
            Connection::Connected if self.muted => "Mic muted",
            Connection::Connected if self.status == "Transcribing" => "Waiting for transcript",
            Connection::Connected if self.status == "Hearing" => "Hearing you",
            Connection::Connected if self.status == "Paused" => "Hearing you",
            Connection::Connected => "Listening",
        }
    }

    fn headline(&self) -> &str {
        if self.generation > 0 {
            match self.connection {
                Connection::Connecting => return "Starting your session…",
                Connection::Disconnecting => return "Stopping your session…",
                Connection::Disconnected => return "Start your session",
                _ => {}
            }
        }
        if self.stopped {
            return "Desktop actions are stopped";
        }
        match self.connection {
            Connection::Connecting => "Connecting voice…",
            Connection::Disconnecting => "Ending the voice session…",
            Connection::Disconnected if self.email.is_empty() => "Checking your Codex account…",
            Connection::Disconnected if !self.shared => "Start your session",
            Connection::Disconnected => "Start your session",
            Connection::Connected if self.status == "Working" => "Working on your request",
            Connection::Connected if self.status == "Speaking" => "Microphone paused for playback",
            Connection::Connected if self.muted => "Unmute to give a new request",
            Connection::Connected if self.status == "Transcribing" => {
                "Waiting for recognized words"
            }
            Connection::Connected if self.status == "Hearing" => "Listening to your request",
            Connection::Connected if self.status == "Paused" => "Finish your thought",
            Connection::Connected => "Ready for your next command",
        }
    }

    fn guidance(&self) -> &str {
        if self.connection == Connection::Connected
            && self.muted
            && !self.stopped
            && self.status != "Working"
            && self.status != "Speaking"
        {
            "Your mic is paused. Choose Listen again to give a new request."
        } else {
            &self.message
        }
    }

    fn stop(&mut self, message: impl Into<String>) {
        self.reply_ready = false;
        self.outcome_current = false;
        self.stopped = true;
        self.status = "Stopped".into();
        self.message = message.into();
    }
    fn fail(&mut self, message: impl Into<String>) {
        self.outcome_current = true;
        let text = message.into();
        self.outcome = Some(Outcome {
            text: text.clone(),
            failed: true,
        });
        self.stop(text);
    }

    fn bridge(&mut self, value: &Value) -> bool {
        match value["type"].as_str().unwrap_or("") {
            "ready" => {
                self.idle_minutes = value["idleResetMinutes"]
                    .as_u64()
                    .filter(|m| (1..=60).contains(m))
                    .unwrap_or(5);
                self.email = value["email"].as_str().unwrap_or("").into();
                self.plan = value["plan"].as_str().unwrap_or("").into();
                self.local_speech = value["speechBackend"].as_str() != Some("native");
                self.message = "Start a session to speak and control your desktop. You can question, correct, or stop at any time.".into();
            }
            "settings" => {
                if let Some(minutes) = value["idleResetMinutes"]
                    .as_u64()
                    .filter(|m| (1..=60).contains(m))
                {
                    self.idle_minutes = minutes;
                }
                self.settings_pending = false;
                self.settings_error = value["message"].as_str().map(str::to_owned);
            }
            "sessionReset" => {
                self.heard = None;
                self.heard_partial = false;
                self.outcome = None;
                self.outcome_current = false;
                self.reply_ready = false;
                self.caption_turn += 1;
            }
            "voice" => {
                let state = value["state"].as_str().unwrap_or("paused");
                if state == "stopped" {
                    self.stop(
                        value["message"]
                            .as_str()
                            .unwrap_or("Work stopped. Restart the session when you are ready."),
                    );
                }
                if !self.stopped {
                    self.status = match state {
                        "listening" => "Listening",
                        "working" => "Working",
                        "speaking" => "Speaking",
                        "hearing" => "Hearing",
                        "transcribing" => "Transcribing",
                        "stopped" => "Stopped",
                        _ => "Paused",
                    }
                    .into();
                    self.message = value["message"].as_str().unwrap_or("").into();
                    if state == "working" || state == "hearing" {
                        self.outcome_current = false;
                        self.reply_ready = false;
                    }
                }
            }
            "connection" => match value["state"].as_str() {
                Some("connecting") => {
                    self.connection = Connection::Connecting;
                }
                Some("connected") => {
                    self.connection = Connection::Connected;
                    if self.stopped {
                        self.message = "Session connected. Preparing desktop control…".into();
                    }
                }
                Some("disconnecting") => {
                    self.connection = Connection::Disconnecting;
                    self.audio_pending = false;
                }
                Some("disconnected") => {
                    self.connection = Connection::Disconnected;
                    self.audio_pending = false;
                }
                _ => {}
            },
            "audioControls"
                if matches!(
                    self.connection,
                    Connection::Connecting | Connection::Connected
                ) =>
            {
                if let (Some(muted), Some(suppressed)) =
                    (value["muted"].as_bool(), value["suppressed"].as_bool())
                {
                    self.muted = muted;
                    self.suppressed = suppressed;
                    self.audio_pending = false;
                    if muted && self.heard_partial {
                        self.heard = None;
                        self.heard_partial = false;
                    }
                }
            }
            "action" if !self.stopped => {
                self.outcome_current = false;
                self.message = value["message"].as_str().unwrap_or("").into();
            }
            "transcript" if value["role"] == "user" => {
                if let Some(text) = value["text"].as_str() {
                    if !self.heard_partial
                        && (value["partial"] == true || self.heard.as_deref() != Some(text))
                    {
                        self.caption_turn += 1;
                    }
                    self.heard = Some(text.to_owned());
                    self.heard_partial = value["partial"] == true;
                    if self.heard_partial {
                        self.outcome_current = false;
                    }
                }
            }
            "result" if !self.stopped => {
                self.outcome_current = true;
                self.reply_ready = true;
                self.outcome = Some(Outcome {
                    text: value["text"].as_str().unwrap_or("").into(),
                    failed: false,
                });
            }
            "actionFailure" if !self.stopped => {
                self.outcome_current = true;
                self.outcome = Some(Outcome {
                    text: value["message"]
                        .as_str()
                        .unwrap_or("Observe again before retrying.")
                        .into(),
                    failed: true,
                });
            }
            "localStop" => {
                self.stop("Work stopped. Restart the session when you are ready.");
                return true;
            }
            "failure" => {
                let error = value["message"]
                    .as_str()
                    .unwrap_or("Session failed. Restart the session to retry.");
                self.fail(error);
                return true;
            }
            _ => {}
        }
        false
    }
}

pub struct Sidebar {
    session: Session,
    runtime: Option<Arc<Runtime>>,
    collapsed: bool,
    hidden: bool,
    details_open: bool,
    preview_hovered: bool,
    reveal_failure: bool,
    preview: Option<Arc<Image>>,
    observed_app: String,
    focus: FocusHandle,
    heard_scroll: ScrollHandle,
}

impl Sidebar {
    pub fn new(contents: PathBuf, window: &mut Window, cx: &mut Context<Self>) -> Self {
        window.set_window_title("Voice App");
        let (sender, receiver) = async_channel::bounded(32);
        let mut session = Session::default();
        let runtime = match Runtime::start(&contents, sender) {
            Ok(runtime) => Some(Arc::new(runtime)),
            Err(error) => {
                session.stop(error);
                None
            }
        };
        if let Some(runtime) = &runtime {
            runtime.platform.layout(window, true, false);
            let runtime = runtime.clone();
            window.on_window_should_close(cx, move |_, _| {
                runtime.shutdown();
                true
            });
        }
        cx.spawn(async move |this, cx| {
            while let Ok(event) = receiver.recv().await {
                if this
                    .update(cx, |this, cx| {
                        this.event(event);
                        cx.notify();
                    })
                    .is_err()
                {
                    break;
                }
            }
        })
        .detach();
        let focus = cx.focus_handle();
        // GPUI keyboard focus is internal; this does not activate the floating OS window.
        window.focus(&focus, cx);
        Self {
            session,
            runtime,
            collapsed: true,
            hidden: false,
            details_open: false,
            preview_hovered: false,
            reveal_failure: false,
            preview: None,
            observed_app: String::new(),
            focus,
            heard_scroll: ScrollHandle::new(),
        }
    }

    fn event(&mut self, event: Event) {
        match event {
            Event::Bridge(value) if self.runtime.is_some() => {
                let kind = value["type"].as_str().unwrap_or("");
                let generation = value["generation"].as_u64();
                if kind == "sessionStarted" {
                    if generation.is_some_and(|g| self.session.accept_session_started(g)) {
                        let result = self
                            .runtime
                            .as_ref()
                            .unwrap()
                            .platform
                            .control(json!({"operation":"resume", "explicit":true}));
                        if result["success"] != true {
                            self.stop_session();
                        } else {
                            self.runtime.as_ref().unwrap().observe();
                        }
                    }
                    return;
                }
                if kind == "sessionStopped" {
                    if generation == Some(self.session.generation) && !self.session.wants_session {
                        self.session.connection = Connection::Disconnected;
                        self.session.message =
                            "Session stopped. Voice and desktop sharing are off.".into();
                    }
                    return;
                }
                if kind == "sessionFailed" {
                    if generation == Some(self.session.generation) {
                        let message = value["message"]
                            .as_str()
                            .unwrap_or("Session failed. Choose Start session to retry.")
                            .to_string();
                        if self.session.wants_session {
                            self.stop_session();
                        } else {
                            self.session.connection = Connection::Disconnected;
                        }
                        self.session.fail(message);
                        self.reveal_failure = true;
                    }
                    return;
                }
                if kind == "sessionStopRequested" {
                    self.stop_session();
                    return;
                }
                if self.session.generation > 0
                    && !self.session.wants_session
                    && !matches!(kind, "ready" | "settings")
                {
                    return;
                }
                if kind == "connection"
                    && value["state"] == "connected"
                    && self.session.generation > 0
                    && self.session.stopped
                    && self.session.connection == Connection::Connecting
                {
                    return;
                }
                self.reveal_failure |= kind == "failure";
                if kind == "transcript" && value["role"] == "user" {
                    self.heard_scroll.scroll_to_bottom();
                }
                if self.session.bridge(&value) {
                    self.stop_session();
                } else if kind == "ready" && self.session.should_auto_start() {
                    self.start_session();
                }
            }
            Event::Observation(value) if self.session.shared => {
                if let Some(runtime) = &self.runtime {
                    runtime
                        .platform
                        .control(json!({"operation":"snapshot", "observation":{"screenshot":value["screenshot"], "app":value["app"], "observedAt":value["observedAt"]}}));
                }
                self.observed_app = value["app"].as_str().unwrap_or("Desktop").into();
                self.preview = value["screenshot"]
                    .as_str()
                    .and_then(|url| url.split_once(','))
                    .and_then(|(_, data)| {
                        base64::engine::general_purpose::STANDARD.decode(data).ok()
                    })
                    .map(|bytes| Arc::new(Image::from_bytes(ImageFormat::Jpeg, bytes)));
            }
            Event::Microphone(generation, value) => {
                if generation != self.session.generation
                    || !self.session.wants_session
                    || self.session.connection != Connection::Connecting
                    || !self.session.shared
                {
                    return;
                }
                if value["success"] == true {
                    if let Some(runtime) = &self.runtime {
                        runtime.send(
                            json!({"type": "session", "action":"start", "generation":generation, "expectedAccount": self.session.email}),
                        );
                    }
                } else {
                    self.stop_session();
                    self.reveal_failure = true;
                    self.session.fail(
                        value["error"]
                            .as_str()
                            .unwrap_or("Microphone access was not granted."),
                    );
                }
            }
            Event::BridgeClosed(error) => {
                if let Some(runtime) = self.runtime.take() {
                    runtime.shutdown();
                }
                self.session.connection = Connection::Disconnected;
                self.session.wants_session = false;
                self.session.generation += 1;
                self.session.shared = false;
                self.preview = None;
                self.observed_app.clear();
                self.session.fail(error);
                self.reveal_failure = true;
            }
            Event::Failure(error) => {
                self.reveal_failure = true;
                self.stop_session();
                self.session.fail(error);
            }
            _ => {}
        }
    }

    fn start_session(&mut self) {
        let Some(runtime) = &self.runtime else { return };
        let generation = self.session.begin_session();
        runtime.stop();
        let result = runtime
            .platform
            .control(json!({"operation":"share", "enabled":true}));
        if result["success"] == true {
            self.session.shared = true;
            self.preview = None;
            self.observed_app.clear();
            runtime.request_microphone(generation);
        } else {
            let message = result["error"]
                .as_str()
                .unwrap_or("Allow desktop permissions, then choose Start session.")
                .to_string();
            self.stop_session();
            self.session.fail(message);
            self.reveal_failure = true;
        }
    }

    fn stop_session(&mut self) {
        let generation = self.session.end_session();
        if let Some(runtime) = &self.runtime {
            runtime.stop();
            runtime
                .platform
                .control(json!({"operation":"share", "enabled":false}));
            runtime.send(json!({"type":"session", "action":"stop", "generation":generation}));
        }
        self.preview = None;
        self.observed_app.clear();
    }

    fn act(&mut self, action: Action, window: &mut Window, cx: &mut Context<Self>) {
        if matches!(action, Action::Quit) {
            if let Some(runtime) = &self.runtime {
                runtime.shutdown();
            }
            cx.quit();
            return;
        }
        if matches!(action, Action::Hide) {
            self.hidden = !self.hidden;
            self.preview_hovered = false;
            window.focus(&self.focus, cx);
            if let Some(runtime) = &self.runtime {
                runtime.platform.layout(window, self.collapsed, self.hidden);
            }
            cx.notify();
            return;
        }
        if matches!(action, Action::Expand) {
            self.collapsed = !self.collapsed;
            window.focus(&self.focus, cx);
            if let Some(runtime) = &self.runtime {
                runtime.platform.layout(window, self.collapsed, self.hidden);
            }
            cx.notify();
            return;
        }
        if matches!(action, Action::Details) {
            self.details_open = !self.details_open;
            cx.notify();
            return;
        }
        let Some(runtime) = &self.runtime else { return };
        match action {
            Action::IdleShorter | Action::IdleLonger if !self.session.settings_pending => {
                let minutes = if matches!(action, Action::IdleShorter) {
                    self.session.idle_minutes.saturating_sub(1).max(1)
                } else {
                    (self.session.idle_minutes + 1).min(60)
                };
                self.session.settings_pending = true;
                self.session.settings_error = None;
                runtime.send(json!({"type":"idleTimeout", "minutes":minutes}));
            }
            Action::Start | Action::Restart
                if !self.session.email.is_empty()
                    && !matches!(
                        self.session.connection,
                        Connection::Connecting | Connection::Disconnecting
                    ) =>
            {
                self.start_session();
            }
            Action::Mute if self.session.connection == Connection::Connected => {
                if let Some(command) = self.session.request_audio(!self.session.muted, true) {
                    runtime.send(command);
                }
            }
            Action::Silence if self.session.connection == Connection::Connected => {
                if !self.session.audio_pending {
                    if self.session.suppressed && self.session.reply_ready && !self.session.stopped
                    {
                        self.session.reply_ready = false;
                        self.session.audio_pending = true;
                        runtime.send(json!({"type":"playReply"}));
                    } else if !self.session.suppressed
                        && let Some(command) = self.session.request_audio(true, true)
                    {
                        runtime.send(command);
                    }
                }
            }
            Action::SubmitTranscript
                if self.session.connection == Connection::Connected
                    && self.session.heard_partial =>
            {
                self.session.heard_partial = false;
                self.session.status = "Working".into();
                self.session.message = "Sending your heard request to the desktop worker…".into();
                runtime.send(json!({"type": "submitTranscript"}));
            }
            Action::Stop => self.stop_session(),
            Action::Permissions => {
                runtime
                    .platform
                    .control(json!({"operation": "allowAccessibility"}));
                self.session.message =
                    "Enable Voice App in Accessibility, then choose Start session.".into();
            }
            _ => {}
        }
        cx.notify();
    }

    fn button(
        &self,
        id: &'static str,
        label: &str,
        action: Action,
        enabled: bool,
        cx: &mut Context<Self>,
    ) -> Stateful<Div> {
        let stop = matches!(action, Action::Stop);
        let drawer_handle = self.hidden && matches!(action, Action::Hide);
        let accessible = match action {
            Action::Stop => "Stop session",
            Action::Restart => "Restart session",
            Action::Start => match self.session.connection {
                Connection::Connecting => "Starting session",
                Connection::Disconnecting => "Stopping session",
                _ => "Start session",
            },
            Action::Mute if self.session.muted => "Listen again",
            Action::Mute => "Mute mic",
            Action::Hide if self.hidden => "Show voice sidebar",
            Action::Hide => "Hide voice sidebar",
            Action::Expand if self.collapsed => "Open voice sidebar",
            Action::Expand => "Collapse voice sidebar",
            Action::Details if self.details_open => "Hide details",
            Action::Details => "Session details",
            Action::IdleShorter => "Decrease idle reset delay",
            Action::IdleLonger => "Increase idle reset delay",
            _ => label,
        };
        let entity = cx.entity().downgrade();
        let glyph = match action {
            Action::Hide if self.hidden => Icon::Show,
            Action::Hide => Icon::Hide,
            Action::Expand if self.collapsed => Icon::Open,
            Action::Expand => Icon::Collapse,
            Action::Restart => Icon::Restart,
            Action::Start | Action::SubmitTranscript => Icon::Play,
            Action::Mute if self.session.muted => Icon::MicOff,
            Action::Mute => Icon::Mic,
            Action::Silence if self.session.suppressed => Icon::SpeakerOff,
            Action::Silence => Icon::Speaker,
            Action::Stop => Icon::Stop,
            Action::Details if self.details_open => Icon::DetailsOpen,
            Action::Details => Icon::Details,
            Action::Permissions => Icon::Accessibility,
            Action::Quit => Icon::Quit,
            Action::IdleShorter => Icon::Minus,
            Action::IdleLonger => Icon::Plus,
        };
        let icon_only = matches!(
            action,
            Action::Expand | Action::Hide | Action::IdleShorter | Action::IdleLonger
        ) || (self.collapsed && matches!(action, Action::Mute | Action::Restart));
        div()
            .id(id)
            .role(Role::Button)
            .aria_label(SharedString::from(accessible.to_string()))
            .aria_description(if enabled && stop {
                "Escape immediately blocks actions and stops voice and desktop sharing."
            } else if enabled {
                ""
            } else if self.session.audio_pending && matches!(action, Action::Mute | Action::Silence)
            {
                "Waiting for microphone confirmation"
            } else if self.session.email.is_empty() {
                "Waiting for the Codex account check"
            } else if matches!(action, Action::Silence) && self.session.connection == Connection::Connected {
                "A completed request will make a reply available. Speakers stay muted while listening."
            } else {
                "Start a session to use audio controls"
            })
            .when(stop, |button| button.aria_keyshortcuts("Escape"))
            .when(matches!(action, Action::Details), |button| {
                button.aria_expanded(self.details_open)
            })
            .w_full()
            .h(px(if stop && !self.collapsed { 44. } else { 36. }))
            .flex_shrink_0()
            .flex()
            .items_center()
            .justify_center()
            .gap(px(6.))
            .rounded_lg()
            .text_size(px(if self.collapsed { 11. } else { 12. }))
            .font_weight(FontWeight::SEMIBOLD)
            .bg(rgb(if drawer_handle { SURFACE } else if stop { STOP } else { CARD }))
            .text_color(rgb(if enabled { TEXT } else { MUTED }))
            .border_1()
            .border_color(rgb(if stop { STOP } else if matches!(action, Action::Restart) && self.session.shared { ACCENT } else { BORDER }))
            .when(drawer_handle, |button| button.border_0())
            .when(matches!(action, Action::Start | Action::Restart), |button| {
                button.aria_description("Start or restart voice and desktop sharing together. Hover previews the latest snapshot while a session is active.")
                    .on_hover(cx.listener(|this, hovered, _, _| {
                        this.preview_hovered = *hovered;
                        if let Some(runtime) = &this.runtime {
                            runtime.platform.control(json!({"operation":"preview", "visible":*hovered && this.session.shared && this.collapsed && !this.hidden}));
                        }
                    }))
            })
            .when(enabled, |button| {
                button
                    .focusable()
                    .tab_stop(true)
                    .cursor_pointer()
                    .when(!drawer_handle, |button| button
                        .hover(|style| style.bg(rgb(if stop { STOP_HOVER } else { HOVER })))
                        .focus(|style| style.border_color(rgb(ACCENT))))
                    .when(drawer_handle, |button| button
                        .focus_visible(|style| style.border_1().border_color(rgb(ACCENT))))
                    .on_click(cx.listener(move |this, _, window, cx| this.act(action, window, cx)))
                    .on_key_down(cx.listener(move |this, event: &KeyDownEvent, window, cx| {
                        if matches!(event.keystroke.key.as_str(), "enter" | "space") {
                            this.act(action, window, cx);
                            cx.stop_propagation();
                        }
                    }))
                    .on_a11y_action(AccessibleAction::Click, move |_, window, cx| {
                        let _ = entity.update(cx, |this, cx| this.act(action, window, cx));
                    })
            })
            .child(icon(glyph).text_color(rgb(if enabled { TEXT } else { MUTED })))
            .when(!icon_only, |button| button.child(label.to_owned()))
    }
}

// A static voice mark, not an audio level meter or a live recording indicator.
fn voice_mark() -> Div {
    div()
        .flex()
        .items_center()
        .justify_center()
        .gap(px(3.))
        .h(px(26.))
        .children(
            [12., 23., 17., 10.]
                .map(|height| div().w(px(3.)).h(px(height)).rounded_full().bg(rgb(ACCENT))),
        )
}

impl Render for Sidebar {
    fn render(&mut self, window: &mut Window, cx: &mut Context<Self>) -> impl IntoElement {
        if std::mem::take(&mut self.reveal_failure) && self.collapsed && !self.hidden {
            self.collapsed = false;
            if let Some(runtime) = &self.runtime {
                runtime.platform.layout(window, false, false);
            }
        }
        if let Some(runtime) = &self.runtime {
            runtime.platform.captions(self.session.compact_captions());
        }
        let runtime_ready = self.runtime.is_some();
        let root = div()
            .id("voice-sidebar")
            .role(Role::Application)
            .aria_label("Voice App")
            .track_focus(&self.focus)
            .size_full()
            .flex()
            .flex_col()
            .font_family(".AppleSystemUIFont")
            .text_size(px(13.))
            .text_color(rgb(TEXT))
            .bg(rgb(SURFACE))
            .rounded(px(20.))
            .border_1()
            .border_color(rgb(BORDER))
            .overflow_hidden()
            .on_key_down(cx.listener(|this, event: &KeyDownEvent, window, cx| {
                match event.keystroke.key.as_str() {
                    "escape" => this.act(Action::Stop, window, cx),
                    "tab" if event.keystroke.modifiers.shift => window.focus_prev(cx),
                    "tab" => window.focus_next(cx),
                    _ => return,
                }
                cx.stop_propagation();
            }));
        if self.hidden {
            return root.rounded(px(10.)).border_0().child(
                self.button("drawer-open", "Show", Action::Hide, true, cx)
                    .h_full()
                    .aria_expanded(false)
                    .aria_description(SharedString::from(format!(
                        "{}. Open the drawer to access microphone and Stop controls. Escape stops the session while focused.",
                        self.session.display_status()
                    ))),
            );
        }
        if self.collapsed {
            let (voice_action, voice_label, voice_enabled) = self.session.compact_voice_control();
            return root
                .px(px(7.))
                .py(px(12.))
                .gap(px(8.))
                .items_center()
                .child(voice_mark())
                .child(
                    div()
                        .id("compact-state")
                        .role(Role::Status)
                        .aria_label(SharedString::from(self.session.display_status().to_owned()))
                        .text_size(px(10.))
                        .text_color(rgb(MUTED))
                        .child(self.session.display_status().to_owned()),
                )
                .child(
                    div()
                        .w_full()
                        .flex()
                        .gap(px(4.))
                        .child(div().w(px(26.)).child(self.button(
                            "open",
                            "Open",
                            Action::Expand,
                            true,
                            cx,
                        )))
                        .child(div().w(px(26.)).child(self.button(
                            "compact-hide",
                            "Hide",
                            Action::Hide,
                            true,
                            cx,
                        ))),
                )
                .child(self.button(
                    "compact-session",
                    voice_label,
                    voice_action,
                    runtime_ready && voice_enabled,
                    cx,
                ))
                .child(self.button(
                    "compact-mic",
                    if self.session.muted { "Listen" } else { "Mute" },
                    Action::Mute,
                    self.session.connection == Connection::Connected && !self.session.audio_pending,
                    cx,
                ))
                .child(div().flex_1())
                .child(self.button(
                    "compact-stop",
                    "Stop",
                    Action::Stop,
                    runtime_ready && self.session.connection != Connection::Disconnected,
                    cx,
                ));
        }
        let headline = self.session.headline();
        let header = div()
            .flex()
            .items_center()
            .gap(px(10.))
            .px(px(20.))
            .pt(px(16.))
            .pb(px(12.))
            .flex_shrink_0()
            .child(voice_mark())
            .child(
                div()
                    .text_size(px(17.))
                    .font_weight(FontWeight::SEMIBOLD)
                    .child("Voice"),
            )
            .child(
                div()
                    .text_size(px(10.))
                    .text_color(rgb(MUTED))
                    .px(px(6.))
                    .py(px(2.))
                    .border_1()
                    .border_color(rgb(BORDER))
                    .rounded(px(5.))
                    .child("Codex"),
            )
            .child(div().flex_1())
            .child(div().w(px(36.)).child(self.button(
                "expanded-hide",
                "Hide",
                Action::Hide,
                true,
                cx,
            )))
            .child(
                div()
                    .w(px(36.))
                    .child(self.button("collapse", "›", Action::Expand, true, cx)),
            );
        let status = div()
            .id("expanded-state")
            .role(Role::Status)
            .aria_label(SharedString::from(self.session.display_status().to_owned()))
            .flex()
            .items_center()
            .gap(px(8.))
            .text_size(px(12.))
            .text_color(rgb(
                if self.session.stopped || self.session.connection != Connection::Connected {
                    MUTED
                } else {
                    ACCENT
                },
            ))
            .child(div().size(px(7.)).rounded_full().bg(rgb(
                if self.session.stopped || self.session.connection != Connection::Connected {
                    MUTED
                } else {
                    ACCENT
                },
            )))
            .child(self.session.display_status().to_owned())
            .child(div().flex_1())
            .when(self.session.connection == Connection::Connected, |view| {
                view.child(
                    div()
                        .text_color(rgb(MUTED))
                        .child(if self.session.audio_pending {
                            "Applying audio…"
                        } else if self.session.muted {
                            "Mic muted"
                        } else {
                            "Mic on"
                        }),
                )
            });
        let preview = div()
            .id("desktop-preview")
            .role(Role::Image)
            .aria_label(SharedString::from(format!(
                "Latest desktop observation: {}. This is a snapshot, not a live stream.",
                self.observed_app
            )))
            .h(px(140.))
            .flex_shrink_0()
            .rounded(px(10.))
            .bg(rgb(CARD))
            .overflow_hidden()
            .flex()
            .items_center()
            .justify_center()
            .when_some(self.preview.clone(), |view, image| {
                view.child(img(image).size_full().object_fit(ObjectFit::Contain))
            })
            .when(self.preview.is_none(), |view| {
                view.child(
                    div()
                        .p(px(20.))
                        .text_color(rgb(MUTED))
                        .text_size(px(13.))
                        .line_height(relative(1.5))
                        .child("Waiting for the first desktop observation…"),
                )
            });
        let body = div().id("sidebar-content").flex_1().min_h(px(0.)).overflow_y_scroll()
            .px(px(20.)).pt(px(12.)).pb(px(20.))
            .child(status)
            .child(div().mt(px(10.)).mb(px(10.)).text_size(px(20.))
                .line_height(relative(1.3)).font_weight(FontWeight::MEDIUM).child(headline.to_owned()))
            .child(div().id("current-action").role(Role::Status)
                .aria_label(SharedString::from(self.session.guidance().to_owned()))
                .text_size(px(14.)).line_height(relative(1.55)).text_color(rgb(MUTED))
                .mb(px(20.)).child(self.session.guidance().to_owned()))
            .when_some(self.session.outcome.as_ref().filter(|_| !self.session.heard_partial), |view, outcome| {
                view.child(div().id("last-outcome").role(if outcome.failed { Role::Alert } else { Role::Status })
                    .aria_label(SharedString::from(outcome.text.clone()))
                    .p(px(12.)).mb(px(20.)).rounded_lg().bg(rgb(CARD))
                    .child(div().text_size(px(12.)).font_weight(FontWeight::SEMIBOLD)
                        .child(if outcome.failed { "Last error" } else { "Last result" }))
                    .child(div().mt(px(6.)).text_size(px(13.)).line_height(relative(1.5))
                        .text_color(rgb(MUTED)).child(outcome.text.clone())))
            })
            .when(self.session.connection == Connection::Connected || self.session.heard.is_some(), |view| {
                let heard = self.session.heard.clone().filter(|text| !text.trim().is_empty()).unwrap_or_else(||
                    if self.session.heard_partial { "Speech detected. Waiting for recognized words…".into() }
                    else { "Your recognized words will appear here.".into() });
                view.child(div().id("last-heard").role(Role::Status).aria_label(SharedString::from(format!("Last heard: {heard}")))
                    .mb(px(20.)).text_size(px(13.)).line_height(relative(1.5))
                    .child(div().text_size(px(12.)).font_weight(FontWeight::SEMIBOLD).child(if self.session.heard_partial { "Hearing · live caption" } else { "Heard request" }))
                    .child(div().id("heard-transcript-scroll").mt(px(6.)).max_h(px(156.)).overflow_y_scroll()
                        .track_scroll(&self.heard_scroll).text_color(rgb(MUTED)).child(heard))
                    .when(self.session.heard_partial && self.session.heard.as_ref().is_some_and(|text| !text.trim().is_empty()) && self.session.connection == Connection::Connected, |view|
                        view.child(div().mt(px(8.)).text_size(px(12.)).text_color(rgb(MUTED))
                            .child(if self.session.stopped || self.session.muted { "Automatic sending paused." }
                                else { "Sends after 2 s without new words. Mute to cancel." }))
                            .child(div().mt(px(10.)).child(self.button("submit-transcript", "Send heard request", Action::SubmitTranscript, !self.session.stopped && !self.session.muted, cx)))))
            })
            .when(self.session.shared, |view| view
            .child(div().flex().items_center().justify_between().gap(px(8.)).mb(px(10.))
                .child(div().text_size(px(12.)).font_weight(FontWeight::SEMIBOLD).child("Desktop snapshot"))
                .child(div().text_size(px(11.)).text_color(rgb(MUTED)).child(
                    if self.observed_app.is_empty() { "Waiting…".into() } else { self.observed_app.clone() })))
            .child(preview)
            .child(div().mt(px(9.)).mb(px(14.)).text_size(px(11.)).text_color(rgb(MUTED))
                .line_height(relative(1.5)).child("Latest observation · updates after actions"))
            )
            .when(!self.session.shared, |view| view.child(div().text_size(px(12.)).text_color(rgb(MUTED))
                .line_height(relative(1.5)).child(if self.session.local_speech {
                    "Speech is transcribed on this Mac. Recognized requests and desktop snapshots go to Codex. Stop session turns off capture and desktop sharing."
                } else {
                    "Start session connects native Codex voice and shares desktop snapshots. Stop session turns off capture and desktop sharing."
                })))
            .when(self.details_open, |view| {
                let account = if self.session.email.is_empty() { "Checking your Codex account…".into() }
                    else { format!("{} · ChatGPT {}", self.session.email, self.session.plan) };
                view.child(div().mt(px(24.)).flex().flex_col().gap(px(12.))
                    .child(div().text_size(px(12.)).font_weight(FontWeight::SEMIBOLD).child("Session details"))
                    .child(div().id("codex-account").role(Role::Label).aria_label(SharedString::from(account.clone()))
                        .text_size(px(12.)).text_color(rgb(MUTED)).line_height(relative(1.5)).child(account))
                    .child(div().text_size(px(12.)).text_color(rgb(MUTED)).line_height(relative(1.5))
                        .child("Start session pairs voice with desktop control. Restart clears conversation context while preserving desktop work. Stop session blocks actions immediately and turns off voice and sharing. Last heard is excluded from diagnostic traces."))
                    .child(self.button("permissions", "Accessibility settings", Action::Permissions, runtime_ready, cx))
                    .child(div().mt(px(10.)).text_size(px(12.)).child("New session after inactivity")
                        .child(div().mt(px(8.)).flex().items_center().gap(px(10.))
                            .child(div().w(px(36.)).child(self.button("idle-less", "Decrease", Action::IdleShorter, runtime_ready && !self.session.settings_pending && self.session.idle_minutes > 1, cx)))
                            .child(div().flex_1().child(format!("{} min", self.session.idle_minutes)))
                            .child(div().w(px(36.)).child(self.button("idle-more", "Increase", Action::IdleLonger, runtime_ready && !self.session.settings_pending && self.session.idle_minutes < 60, cx))))
                        .child(div().mt(px(6.)).text_size(px(11.)).text_color(rgb(MUTED)).child("Saved on this Mac. Active work is never reset."))
                        .when_some(self.session.settings_error.as_ref(), |view, error| view.child(div().id("idle-setting-error").mt(px(6.)).role(Role::Alert).child(error.clone()))))
                    .child(self.button("quit", "Quit Voice App", Action::Quit, true, cx)))
            });
        let footer = div()
            .flex_shrink_0()
            .px(px(20.))
            .pt(px(16.))
            .pb(px(18.))
            .border_t_1()
            .border_color(rgb(BORDER))
            .flex()
            .flex_col()
            .gap(px(10.))
            .child({
                let (action, _, enabled) = self.session.compact_voice_control();
                self.button(
                    "session-primary",
                    match self.session.connection {
                        Connection::Disconnected => "Start session",
                        Connection::Connecting => "Starting session…",
                        Connection::Disconnecting => "Stopping session…",
                        Connection::Connected => "Restart session",
                    },
                    action,
                    runtime_ready && enabled,
                    cx,
                )
            })
            .when(self.session.connection == Connection::Connected, |view| {
                view.child(
                    div()
                        .mb(px(8.))
                        .text_size(px(11.))
                        .line_height(relative(1.4))
                        .text_color(rgb(MUTED))
                        .child("Speakers stay muted while listening."),
                )
                .child(
                    div()
                        .flex()
                        .gap(px(8.))
                        .child(div().flex_1().min_w(px(0.)).child(self.button(
                            "mic",
                            if self.session.muted {
                                "Listen again"
                            } else {
                                "Mute mic"
                            },
                            Action::Mute,
                            !self.session.audio_pending,
                            cx,
                        )))
                        .child(div().flex_1().min_w(px(0.)).child(self.button(
                            "speech",
                            if self.session.suppressed {
                                "Play reply"
                            } else {
                                "Stop reply"
                            },
                            Action::Silence,
                            !self.session.audio_pending
                                && (!self.session.suppressed
                                    || (self.session.reply_ready && !self.session.stopped)),
                            cx,
                        ))),
                )
            })
            .child(self.button(
                "stop",
                "Stop session",
                Action::Stop,
                runtime_ready && self.session.connection != Connection::Disconnected,
                cx,
            ))
            .child(
                div()
                    .flex()
                    .items_center()
                    .gap(px(8.))
                    .child(div().flex_1().child(self.button(
                        "details",
                        if self.details_open {
                            "Hide details"
                        } else {
                            "Session details"
                        },
                        Action::Details,
                        true,
                        cx,
                    )))
                    .child(div().text_size(px(10.)).text_color(rgb(MUTED)).child(
                        if self.session.local_speech {
                            "On-device speech"
                        } else {
                            "Subscription voice"
                        },
                    )),
            );
        root.child(header).child(body).child(footer)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn opening_auto_starts_only_once_after_account_is_ready() {
        let mut session = Session::default();
        assert!(!session.should_auto_start());
        session.bridge(&json!({"type":"ready", "email":"account@example.test", "plan":"prolite"}));
        assert!(session.should_auto_start());
        let first = session.begin_session();
        assert!(!session.should_auto_start());
        session.accept_session_started(first);
        assert!(!session.should_auto_start());
        session.end_session();
        session.connection = Connection::Disconnected;
        session.bridge(&json!({"type":"ready", "email":"account@example.test", "plan":"prolite"}));
        assert!(!session.should_auto_start());
    }

    #[test]
    fn session_stop_and_restart_invalidate_late_start_acknowledgements() {
        let mut session = Session::default();
        let first = session.begin_session();
        assert_eq!(session.display_status(), "Starting");
        let stop = session.end_session();
        assert!(stop > first);
        assert!(!session.accept_session_started(first));
        assert!(!session.shared);
        assert!(session.stopped);
        let restart = session.begin_session();
        assert!(!session.accept_session_started(first));
        session.shared = true;
        assert!(session.accept_session_started(restart));
        assert_eq!(session.display_status(), "Listening");
        assert!(!session.stopped);
        assert_eq!(session.compact_voice_control().0, Action::Restart);
        session.end_session();
        assert!(!session.accept_session_started(restart));
        assert_eq!(session.compact_captions(), json!({}));
    }

    #[test]
    fn compact_voice_can_recover_after_failed_start_without_bypassing_stop() {
        let mut session = Session {
            shared: true,
            email: "test@example.com".into(),
            ..Session::default()
        };
        assert_eq!(
            session.compact_voice_control(),
            (Action::Start, "Start", true)
        );
        session.bridge(&json!({"type":"connection","state":"connecting"}));
        assert!(!session.compact_voice_control().2);
        session.bridge(&json!({"type":"localStop"}));
        session.bridge(&json!({"type":"connection","state":"disconnected"}));
        session.bridge(&json!({"type":"failure","message":"Native voice startup failed"}));
        assert_eq!(
            session.compact_voice_control(),
            (Action::Start, "Start", true)
        );
        session.bridge(&json!({"type":"connection","state":"connected"}));
        session.bridge(&json!({"type":"voice","state":"listening"}));
        assert!(session.stopped);
        assert_eq!(
            session.compact_voice_control(),
            (Action::Restart, "Restart", true)
        );
        session.stopped = false; // Session recovery now uses an explicit restart.
        assert_eq!(session.compact_voice_control().0, Action::Restart);
        session.stop("Explicit stop");
        assert_eq!(session.compact_voice_control().0, Action::Restart);
    }

    #[test]
    fn reply_readiness_and_playback_status_follow_the_current_request() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"result", "text":"Done"}));
        assert!(session.reply_ready);
        session.bridge(&json!({"type":"voice", "state":"hearing"}));
        assert!(!session.reply_ready);
        session.bridge(&json!({"type":"audioControls", "muted":true, "suppressed":false}));
        session.bridge(&json!({"type":"voice", "state":"speaking", "message":"Microphone paused for playback"}));
        assert_eq!(session.display_status(), "Playing reply");
        assert_eq!(session.guidance(), "Microphone paused for playback");
        session.stop("Work stopped");
        assert_eq!(session.display_status(), "Work stopped");
    }

    #[test]
    fn listening_hearing_waiting_and_working_have_distinct_states() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        for (state, label) in [
            ("listening", "Listening"),
            ("hearing", "Hearing you"),
            ("transcribing", "Waiting for transcript"),
            ("working", "Working"),
        ] {
            session.bridge(&json!({"type":"voice", "state":state}));
            assert_eq!(session.display_status(), label);
        }
        session.bridge(&json!({"type":"transcript", "role":"user", "text":"", "partial":true}));
        assert!(session.heard_partial);
        assert_eq!(session.compact_captions()["user"], "Listening…");
        session.bridge(
            &json!({"type":"transcript", "role":"user", "text":"Make a list", "partial":true}),
        );
        assert_eq!(session.heard.as_deref(), Some("Make a list"));
        session.bridge(&json!({"type":"transcript", "role":"user", "text":"Make a list"}));
        assert!(!session.heard_partial);
        session.stop("Stopped");
        session.bridge(&json!({"type":"voice", "state":"transcribing"}));
        assert_eq!(session.display_status(), "Work stopped");
    }

    #[test]
    fn heard_request_survives_status_updates_and_ignores_assistant_transcripts() {
        let mut session = Session::default();
        session
            .bridge(&json!({"type":"transcript", "role":"user", "text":"Change the red square"}));
        session.bridge(&json!({"type":"voice", "state":"working", "message":"Inspecting"}));
        session
            .bridge(&json!({"type":"transcript", "role":"assistant", "text":"Unverified claim"}));
        assert_eq!(session.heard.as_deref(), Some("Change the red square"));
    }
    #[test]
    fn working_remains_visible_while_microphone_is_muted() {
        let mut session = Session {
            connection: Connection::Connected,
            stopped: false,
            ..Default::default()
        };
        session.bridge(&json!({"type":"voice", "state":"working", "message":"Opening the editor"}));
        session.bridge(&json!({"type":"transcript", "role":"user", "text":"Unsubmitted interruption", "partial":true}));
        session.bridge(&json!({"type":"audioControls", "muted":true, "suppressed":true}));
        assert!(session.heard.is_none() && !session.heard_partial);
        session.bridge(&json!({"type":"voice", "state":"working", "message":"Microphone muted. Desktop work continues."}));
        session.bridge(&json!({"type":"action", "message":"Pressing a control"}));
        assert!(session.muted && !session.stopped);
        assert_eq!(session.display_status(), "Working");
        assert_eq!(session.headline(), "Working on your request");
        assert_eq!(session.guidance(), "Pressing a control");
        session.bridge(&json!({"type":"voice", "state":"paused", "message":"Command finished."}));
        assert_eq!(session.display_status(), "Mic muted");
        session.bridge(&json!({"type":"transcript", "role":"user", "text":"Committed request"}));
        session.bridge(&json!({"type":"audioControls", "muted":true, "suppressed":true}));
        assert_eq!(session.heard.as_deref(), Some("Committed request"));
    }

    #[test]
    fn muted_mic_is_not_presented_as_listening() {
        let mut session = Session {
            connection: Connection::Connected,
            muted: true,
            ..Default::default()
        };
        session.bridge(
            &json!({"type":"voice", "state":"listening", "message":"Listening. Describe a task."}),
        );
        assert_eq!(session.display_status(), "Mic muted");
        assert!(session.guidance().contains("Listen again"));
        session.bridge(&json!({"type":"voice", "state":"working", "message":"Clicking a control"}));
        assert_eq!(session.display_status(), "Working");
        assert_eq!(session.guidance(), "Clicking a control");
        session.stop("Stopped locally");
        assert_eq!(session.display_status(), "Work stopped");
        assert_eq!(session.guidance(), "Stopped locally");
    }

    #[test]
    fn audio_controls_wait_for_native_confirmation_and_block_repeated_requests() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        assert_eq!(
            session.request_audio(true, true).unwrap(),
            json!({"type":"audioControls", "muted":true, "suppressed":true})
        );
        assert!(!session.muted && !session.suppressed);
        assert!(session.audio_pending);
        assert!(session.request_audio(false, false).is_none());
        session.bridge(&json!({"type":"audioControls", "muted":true, "suppressed":true}));
        assert!(session.muted && session.suppressed);
        assert!(!session.audio_pending);
        assert!(session.request_audio(false, true).is_some());
        session.bridge(&json!({"type":"connection", "state":"disconnecting"}));
        assert!(!session.audio_pending);
        session.bridge(&json!({"type":"audioControls", "muted":false, "suppressed":false}));
        assert!(session.muted && session.suppressed);
        assert!(session.request_audio(false, false).is_none());
    }

    #[test]
    fn microphone_reconnection_waits_for_ack_and_preserves_stop() {
        let mut session = Session {
            connection: Connection::Connected,
            muted: true,
            ..Default::default()
        };
        assert!(session.request_audio(false, true).is_some());
        session.bridge(&json!({"type":"connection", "state":"connecting"}));
        assert_eq!(session.display_status(), "Connecting");
        assert!(session.audio_pending && session.muted);
        session.stop("Stopped locally");
        session.bridge(&json!({"type":"audioControls", "muted":false, "suppressed":true}));
        session.bridge(&json!({"type":"connection", "state":"connected"}));
        assert!(!session.audio_pending && !session.muted && session.suppressed);
        assert_eq!(session.display_status(), "Work stopped");
        assert!(session.stopped);
    }

    #[test]
    fn compact_bubbles_follow_current_request_without_reusing_old_reply() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"transcript","role":"user","text":"Open files"}));
        session.bridge(&json!({"type":"result","text":"Files opened"}));
        assert_eq!(session.compact_captions()["agent"], "Files opened");
        assert_eq!(session.compact_captions()["phase"], "complete");
        session.bridge(
            &json!({"type":"transcript","role":"user","text":"Collapse sidebar","partial":true}),
        );
        assert_eq!(session.compact_captions()["user"], "Collapse sidebar");
        assert_eq!(session.compact_captions()["agent"], "");
        assert_eq!(session.compact_captions()["phase"], "active");
        session.bridge(&json!({"type":"transcript","role":"user","text":"Collapse sidebar"}));
        session.bridge(
            &json!({"type":"voice","state":"working","message":"Finding collapse control"}),
        );
        assert_eq!(
            session.compact_captions()["agent"],
            "Finding collapse control"
        );
        assert_eq!(session.compact_captions()["phase"], "active");
        session.bridge(&json!({"type":"actionFailure","message":"Pane still visible"}));
        assert_eq!(session.compact_captions()["agentTitle"], "Needs attention");
        assert_eq!(session.compact_captions()["agent"], "Pane still visible");
        session.bridge(&json!({"type":"result","text":"Sidebar collapsed"}));
        assert_eq!(session.compact_captions()["agent"], "Sidebar collapsed");
        session.bridge(&json!({"type":"connection","state":"disconnected"}));
        assert_eq!(session.compact_captions(), json!({}));
    }

    #[test]
    fn stop_dismisses_bubbles_and_late_events_cannot_restore_them() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"transcript","role":"user","text":"Open files"}));
        session.bridge(&json!({"type":"result","text":"Files opened"}));
        assert_eq!(session.compact_captions()["agent"], "Files opened");
        session.stop("Stopped locally");
        assert_eq!(session.compact_captions(), json!({}));
        for event in [
            json!({"type":"voice","state":"listening"}),
            json!({"type":"result","text":"Late answer"}),
            json!({"type":"transcript","role":"user","text":"Buffered words","partial":true}),
        ] {
            session.bridge(&event);
            assert_eq!(session.compact_captions(), json!({}));
        }
        assert_eq!(session.outcome.as_ref().unwrap().text, "Files opened");
        assert!(session.stopped);
    }

    #[test]
    fn idle_session_reset_clears_conversation_without_reopening_stopped_work() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"ready","idleResetMinutes":5}));
        session.bridge(&json!({"type":"transcript","role":"user","text":"Old request"}));
        session.bridge(&json!({"type":"result","text":"Old result"}));
        session.stop("Stopped");
        session.bridge(&json!({"type":"sessionReset"}));
        assert!(session.heard.is_none() && session.outcome.is_none() && session.stopped);
        assert_eq!(session.compact_captions(), json!({}));
        session.settings_pending = true;
        session.bridge(&json!({"type":"settings","idleResetMinutes":7,"message":null}));
        assert_eq!(session.idle_minutes, 7);
        assert!(!session.settings_pending);
        session.bridge(&json!({"type":"settings","idleResetMinutes":7,"message":"Save failed"}));
        assert_eq!(session.settings_error.as_deref(), Some("Save failed"));
    }

    #[test]
    fn caption_animation_identity_changes_only_for_a_new_request() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"transcript","role":"user","text":"Open","partial":true}));
        assert_eq!(session.compact_captions()["user"], "Open");
        let first = session.compact_captions()["request"].clone();
        session
            .bridge(&json!({"type":"transcript","role":"user","text":"Open files","partial":true}));
        assert_eq!(session.compact_captions()["request"], first);
        session.bridge(&json!({"type":"transcript","role":"user","text":"Open files"}));
        session.bridge(&json!({"type":"result","text":"Files open"}));
        assert_eq!(session.compact_captions()["request"], first);
        session.bridge(&json!({"type":"transcript","role":"user","text":"Close","partial":true}));
        assert_ne!(session.compact_captions()["request"], first);
    }

    #[test]
    fn setup_guidance_follows_connection_and_sharing() {
        let mut session = Session::default();
        assert_eq!(session.display_status(), "Checking account");
        session.bridge(&json!({"type":"ready", "email":"account@example.test", "plan":"prolite"}));
        assert_eq!(session.headline(), "Start your session");
        session.shared = true;
        assert_eq!(session.headline(), "Start your session");
        session.connection = Connection::Connecting;
        assert_eq!(session.display_status(), "Connecting");
        session.bridge(&json!({"type":"connection", "state":"connected"}));
        session.bridge(&json!({"type":"voice", "state":"listening", "message":"Listening"}));
        assert_eq!(session.headline(), "Ready for your next command");
        session.connection = Connection::Disconnecting;
        assert_eq!(session.display_status(), "Stopping session");
    }

    #[test]
    fn delayed_voice_cannot_relabel_local_stop() {
        let mut session = Session::default();
        session.stop("Stopped locally");
        session.bridge(&json!({"type":"voice", "state":"working", "message":"late"}));
        session.bridge(&json!({"type":"result", "text":"late completion"}));
        assert_eq!(session.status, "Stopped");
        assert_eq!(session.message, "Stopped locally");
    }
    #[test]
    fn native_connection_acknowledges_a_pending_connection() {
        let mut session = Session {
            connection: Connection::Connecting,
            ..Default::default()
        };
        session.bridge(&json!({"type":"connection", "state":"connected"}));
        assert_eq!(session.connection, Connection::Connected);
    }
    #[test]
    fn bridge_failure_requests_local_stop() {
        let mut session = Session::default();
        assert!(session.bridge(&json!({"type":"failure", "message":"Disconnected"})));
        assert!(session.stopped);
        assert_eq!(session.status, "Stopped");
    }

    #[test]
    fn spoken_stop_exposes_explicit_resume() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"voice", "state":"stopped", "message":"Stopped by speech"}));
        assert!(session.stopped && session.connection == Connection::Connected);
        assert_eq!(session.message, "Stopped by speech");
    }

    #[test]
    fn transport_loss_releases_connection_without_reopening_work() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"failure", "message":"Native audio stopped"}));
        session.bridge(&json!({"type":"connection", "state":"disconnecting"}));
        assert_eq!(session.connection, Connection::Disconnecting);
        session.bridge(&json!({"type":"connection", "state":"disconnected"}));
        session.bridge(&json!({"type":"voice", "state":"listening", "message":"late"}));
        assert_eq!(session.connection, Connection::Disconnected);
        assert!(session.stopped);
        assert_eq!(
            session.outcome.as_ref().unwrap().text,
            "Native audio stopped"
        );
    }

    #[test]
    fn worker_failure_keeps_audio_connected() {
        let mut session = Session {
            connection: Connection::Connected,
            ..Default::default()
        };
        session.bridge(&json!({"type":"failure", "message":"Task failed. Resume work to retry."}));
        assert_eq!(session.connection, Connection::Connected);
        assert!(session.stopped);
    }

    #[test]
    fn listening_does_not_erase_results_or_action_errors() {
        let mut session = Session::default();
        session.bridge(&json!({"type":"result", "text":"Heading updated."}));
        session.bridge(&json!({"type":"voice", "state":"listening", "message":"Listening"}));
        assert_eq!(session.outcome.as_ref().unwrap().text, "Heading updated.");
        assert!(!session.outcome.as_ref().unwrap().failed);
        session
            .bridge(&json!({"type":"actionFailure", "message":"Control rejected. Observe again."}));
        session.bridge(&json!({"type":"voice", "state":"listening", "message":"Listening"}));
        assert_eq!(
            session.outcome.as_ref().unwrap().text,
            "Control rejected. Observe again."
        );
        assert!(session.outcome.as_ref().unwrap().failed);
    }
}
