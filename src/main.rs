use std::{
    env,
    error::Error,
    fs,
    path::{Path, PathBuf},
    process::Command,
};

const APP_PY: &str = include_str!("../app.py");
const INDEX_HTML: &str = include_str!("../index.html");
const PROMPT_RACK_ICNS: &[u8] = include_bytes!("../PromptRack.icns");
const AUTO_BG_PY: &str = include_str!("../auto-bg.py");
const AUTO_BG_HOOK: &str = include_str!("../auto-bg-hook.sh");

fn main() {
    if let Err(err) = run() {
        eprintln!("prompt-rack: {err}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Box<dyn Error>> {
    let support_dir = support_dir()?;
    let runtime_dir = support_dir.join("runtime");
    fs::create_dir_all(&runtime_dir)?;
    fs::create_dir_all(&support_dir)?;

    write_if_changed(&runtime_dir.join("app.py"), APP_PY.as_bytes())?;
    write_if_changed(&runtime_dir.join("index.html"), INDEX_HTML.as_bytes())?;
    write_if_changed(&runtime_dir.join("PromptRack.icns"), PROMPT_RACK_ICNS)?;
    write_if_changed(&runtime_dir.join("auto-bg.py"), AUTO_BG_PY.as_bytes())?;
    write_if_changed(&runtime_dir.join("auto-bg-hook.sh"), AUTO_BG_HOOK.as_bytes())?;
    fs::set_permissions(runtime_dir.join("auto-bg-hook.sh"), std::os::unix::fs::PermissionsExt::from_mode(0o755))?;

    let status = Command::new(python_cmd()?)
        .arg(runtime_dir.join("app.py"))
        .args(env::args().skip(1))
        .env("PROMPT_RACK_STATE_DIR", &support_dir)
        .status()?;

    std::process::exit(status.code().unwrap_or(1));
}

fn python_cmd() -> Result<String, Box<dyn Error>> {
    if let Some(path) = env::var_os("PROMPT_RACK_PYTHON") {
        let path = path.to_string_lossy().into_owned();
        if python_ok(&path) {
            return Ok(path);
        }
        return Err(format!("PROMPT_RACK_PYTHON does not have PyObjC/WebKit: {path}").into());
    }

    for candidate in [
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
        "python3",
    ] {
        if python_ok(candidate) {
            return Ok(candidate.to_string());
        }
    }

    Err("no Python 3 with AppKit, Quartz, and WebKit modules found".into())
}

fn python_ok(candidate: &str) -> bool {
    Command::new(candidate)
        .arg("-c")
        .arg("import AppKit, Quartz, WebKit")
        .status()
        .map(|status| status.success())
        .unwrap_or(false)
}

fn support_dir() -> Result<PathBuf, Box<dyn Error>> {
    if let Some(path) = env::var_os("PROMPT_RACK_HOME") {
        return Ok(PathBuf::from(path));
    }
    let home = env::var_os("HOME").ok_or("HOME is not set")?;
    Ok(PathBuf::from(home).join("Library/Application Support/Prompt Rack"))
}

fn write_if_changed(path: &Path, bytes: &[u8]) -> Result<(), Box<dyn Error>> {
    if fs::read(path)
        .map(|current| current == bytes)
        .unwrap_or(false)
    {
        return Ok(());
    }
    fs::write(path, bytes)?;
    Ok(())
}
