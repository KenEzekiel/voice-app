mod icons;
mod platform;
mod runtime;
mod ui;

use gpui::{
    App, AppContext, Bounds, WindowBackgroundAppearance, WindowBounds, WindowKind, WindowOptions,
    point, px, size,
};
use std::path::PathBuf;

fn main() {
    let contents: PathBuf = std::env::current_exe()
        .expect("Executable path unavailable")
        .parent()
        .expect("Missing MacOS directory")
        .parent()
        .expect("Missing Contents directory")
        .into();
    gpui_platform::application().run(move |cx: &mut App| {
        let display = cx.primary_display();
        let bounds = display.as_ref().map(|display| {
            let screen = display.bounds();
            Bounds::new(
                point(
                    screen.origin.x + screen.size.width - px(84.),
                    screen.origin.y + (screen.size.height - px(292.)) / 2.,
                ),
                size(px(72.), px(292.)),
            )
        });
        cx.on_window_closed(|cx, _| {
            if cx.windows().is_empty() {
                cx.quit();
            }
        })
        .detach();
        cx.open_window(
            WindowOptions {
                window_bounds: bounds.map(WindowBounds::Windowed),
                display_id: display.as_ref().map(|display| display.id()),
                titlebar: None,
                kind: WindowKind::PopUp,
                focus: false,
                is_movable: false,
                is_resizable: false,
                is_minimizable: false,
                window_background: WindowBackgroundAppearance::Transparent,
                window_min_size: Some(size(px(28.), px(88.))),
                app_id: Some("tech.kennezekiel.voice-app".into()),
                ..Default::default()
            },
            |window, cx| cx.new(|cx| ui::Sidebar::new(contents.clone(), window, cx)),
        )
        .expect("Cannot open Voice App sidebar");
    });
}
