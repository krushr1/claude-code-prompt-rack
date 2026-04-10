from setuptools import setup


APP = ["app.py"]
OPTIONS = {
    "alias": True,
    "argv_emulation": False,
    "iconfile": "PromptRack.icns",
    "plist": {
        "CFBundleName": "Prompt Rack",
        "CFBundleDisplayName": "Prompt Rack",
        "CFBundleIdentifier": "com.promptrack.app",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
    },
}


setup(
    app=APP,
    options={"py2app": OPTIONS},
)
