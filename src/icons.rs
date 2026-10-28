use gpui::{Styled, Svg, px, svg};

// One 16-point outline set, embedded in the binary; no icon font or network assets.
#[derive(Clone, Copy)]
pub enum Icon {
    Open,
    Collapse,
    Hide,
    Show,
    Mic,
    MicOff,
    Speaker,
    SpeakerOff,
    Stop,
    Play,
    Restart,
    Details,
    DetailsOpen,
    Accessibility,
    Quit,
    Minus,
    Plus,
}

pub fn icon(kind: Icon) -> Svg {
    let shapes = match kind {
        Icon::Open => {
            r#"<rect x="2" y="2.5" width="12" height="11" rx="2"/><path d="M10 3v10M7 6 5 8l2 2"/>"#
        }
        Icon::Collapse => {
            r#"<rect x="2" y="2.5" width="12" height="11" rx="2"/><path d="M10 3v10M5 6l2 2-2 2"/>"#
        }
        Icon::Show => r#"<path d="m10 4-4 4 4 4"/>"#,
        Icon::Hide => r#"<path d="m4 4 4 4-4 4m5-8 4 4-4 4"/>"#,
        Icon::Mic => {
            r#"<rect x="6" y="1.5" width="4" height="8" rx="2"/><path d="M3.5 7.5a4.5 4.5 0 0 0 9 0M8 12v2.5M5.5 14.5h5"/>"#
        }
        Icon::MicOff => {
            r#"<path d="M6 5V3.5a2 2 0 0 1 4 0v4M6 8v.5a2 2 0 0 0 2 2M3.5 7.5A4.5 4.5 0 0 0 11 11M12.5 7.5v1M8 12v2.5M5.5 14.5h5M2 2l12 12"/>"#
        }
        Icon::Speaker => {
            r#"<path d="M7.5 3 4 6H2v4h2l3.5 3zM10 5a4 4 0 0 1 0 6M12 2.5a7 7 0 0 1 0 11"/>"#
        }
        Icon::SpeakerOff => r#"<path d="M7.5 3 4 6H2v4h2l3.5 3zM11 6l3 4M14 6l-3 4"/>"#,
        Icon::Stop => {
            r#"<rect x="3.5" y="3.5" width="9" height="9" rx="1" fill="currentColor" stroke="none"/>"#
        }
        Icon::Play => r#"<path d="m5 2.5 8 5.5-8 5.5z"/>"#,
        Icon::Restart => r#"<path d="M13.5 7a5.5 5.5 0 1 0-1.5 5M13.5 2v5h-5"/>"#,
        Icon::Details => r#"<path d="m4 6 4 4 4-4"/>"#,
        Icon::DetailsOpen => r#"<path d="m4 10 4-4 4 4"/>"#,
        Icon::Accessibility => {
            r#"<circle cx="8" cy="2.5" r="1"/><path d="M3 5.5h10M8 5.5v4M8 9.5l-3 5M8 9.5l3 5"/>"#
        }
        Icon::Quit => r#"<path d="M8 1.5v6M4 3.5a6 6 0 1 0 8 0"/>"#,
        Icon::Minus => r#"<path d="M3 8h10"/>"#,
        Icon::Plus => r#"<path d="M3 8h10M8 3v10"/>"#,
    };
    let data = format!(
        r#"<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{shapes}</svg>"#
    );
    svg().data(data.as_bytes()).size(px(16.)).flex_shrink_0()
}
