use libloading::Library;
use raw_window_handle::{HasWindowHandle, RawWindowHandle};
use serde_json::{Value, json};
use std::{
    ffi::{CStr, CString, c_char, c_void},
    path::Path,
    sync::Arc,
};

type Control = unsafe extern "C" fn(*const c_char) -> *mut c_char;
type Free = unsafe extern "C" fn(*mut c_char);
type Reply = unsafe extern "C" fn(*const c_char, *mut c_void);
type Request = unsafe extern "C" fn(*const c_char, Option<Reply>, *mut c_void);
type Layout = unsafe extern "C" fn(*mut c_void, bool, bool);
type Captions = unsafe extern "C" fn(*const c_char);
type Callback = Box<dyn FnOnce(Value) + Send>;

struct Native {
    _library: Library,
    control: Control,
    free: Free,
    request: Request,
    layout: Layout,
    captions: Captions,
}

#[derive(Clone)]
pub struct Platform(Arc<Native>);

impl Platform {
    pub fn load(path: &Path) -> Result<Self, String> {
        // Only load our signed, bundled macOS adapter at its exact bundle path.
        unsafe {
            let library = Library::new(path).map_err(|e| e.to_string())?;
            let control = *library
                .get::<Control>(b"voice_desktop_control\0")
                .map_err(|e| e.to_string())?;
            let free = *library
                .get::<Free>(b"voice_desktop_free\0")
                .map_err(|e| e.to_string())?;
            let request = *library
                .get::<Request>(b"voice_desktop_request\0")
                .map_err(|e| e.to_string())?;
            let layout = *library
                .get::<Layout>(b"voice_sidebar_layout\0")
                .map_err(|e| e.to_string())?;
            let captions = *library
                .get::<Captions>(b"voice_sidebar_captions\0")
                .map_err(|e| e.to_string())?;
            Ok(Self(Arc::new(Native {
                _library: library,
                control,
                free,
                request,
                layout,
                captions,
            })))
        }
    }

    pub fn control(&self, value: Value) -> Value {
        let encoded = CString::new(value.to_string()).expect("JSON cannot contain unescaped NUL");
        // The synchronous control API returns an owned C string; free it through its allocator.
        unsafe {
            let raw = (self.0.control)(encoded.as_ptr());
            if raw.is_null() {
                return json!({"success": false, "error": "Local control returned no result."});
            }
            let result = serde_json::from_slice(CStr::from_ptr(raw).to_bytes()).unwrap_or_else(
                |_| json!({"success": false, "error": "Invalid local control result."}),
            );
            (self.0.free)(raw);
            result
        }
    }

    pub fn request(&self, value: Value, reply: impl FnOnce(Value) + Send + 'static) {
        let keep_alive = self.clone();
        let callback: Callback = Box::new(move |value| {
            reply(value);
            drop(keep_alive);
        });
        let context = Box::into_raw(Box::new(callback)).cast::<c_void>();
        let encoded = CString::new(value.to_string()).expect("JSON cannot contain unescaped NUL");
        // Swift copies the request before returning and invokes the callback exactly once.
        unsafe {
            (self.0.request)(encoded.as_ptr(), Some(deliver), context);
        }
    }

    pub fn layout(&self, window: &gpui::Window, collapsed: bool, hidden: bool) {
        if let Ok(handle) = HasWindowHandle::window_handle(window)
            && let RawWindowHandle::AppKit(handle) = handle.as_raw()
        {
            // Called on GPUI's main thread with its live NSView. The adapter owns only positioning.
            unsafe {
                (self.0.layout)(handle.ns_view.as_ptr(), collapsed, hidden);
            }
        }
    }

    pub fn captions(&self, value: Value) {
        let encoded = CString::new(value.to_string()).expect("JSON cannot contain unescaped NUL");
        // Read-only, click-through captions. Called from GPUI's main thread.
        unsafe { (self.0.captions)(encoded.as_ptr()) };
    }
}

unsafe extern "C" fn deliver(raw: *const c_char, context: *mut c_void) {
    // Reclaim the one callback allocated by request(). Copy Swift's borrowed string immediately.
    let callback = unsafe { Box::from_raw(context.cast::<Callback>()) };
    let result = if raw.is_null() {
        json!({"success": false, "error": "Local desktop response was empty."})
    } else {
        unsafe { serde_json::from_slice(CStr::from_ptr(raw).to_bytes()) }.unwrap_or_else(
            |_| json!({"success": false, "error": "Invalid local desktop response."}),
        )
    };
    callback(result);
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_adapter_stop_is_sticky_across_the_c_abi() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join(".build/package/Voice App.app/Contents/Frameworks/libVoiceDesktop.dylib");
        let platform = Platform::load(&path)
            .expect("Build the bundled adapter with scripts/build.sh before testing");
        let stopped = platform.control(json!({"operation":"stop"}));
        assert_eq!(stopped["success"], true);
        let epoch = stopped["epoch"].as_u64().unwrap();
        assert_eq!(
            platform.control(json!({"operation":"resume", "epoch":epoch}))["success"],
            false
        );
        assert_eq!(
            platform.control(json!({"operation":"resume", "epoch":epoch, "explicit":true}))["success"],
            true
        );
        platform.control(json!({"operation":"share", "enabled":false}));
        let (sender, receiver) = std::sync::mpsc::channel();
        platform.request(json!({"operation":"observe"}), move |result| {
            sender.send(result).unwrap();
        });
        let result = receiver
            .recv_timeout(std::time::Duration::from_secs(2))
            .unwrap();
        assert_eq!(result["success"], false);
        assert!(result["error"].as_str().unwrap().contains("sharing is off"));
    }
}
