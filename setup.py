from setuptools import setup


APP = ["app.py"]
DATA_FILES = ["index.html", "auto-bg.py", "auto-bg-hook.sh"]
OPTIONS = {
    "alias": False,
    "argv_emulation": False,
    "iconfile": "PromptRack.icns",
    "plist": {
        "CFBundleName": "Prompt Rack",
        "CFBundleDisplayName": "Prompt Rack",
        "CFBundleIdentifier": "com.promptrack.app",
        "CFBundleShortVersionString": "1.1",
        "CFBundleVersion": "2",
        "LSUIElement": True,
    },
}


setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
)
