from setuptools import setup

APP = ['sp.py'] # Bu yerga asosiy kodingiz nomini yozing
DATA_FILES = []
OPTIONS = {
    'argv_emulation': True,
    'packages': ['moviepy', 'tkinter'], # Kerakli kutubxonalar
    'iconfile': 'app_icon.icns', # Agar ikonkangiz bo'lsa (ixtiyoriy)
    'plist': {
        'CFBundleName': "Kino TV Resizer",
        'CFBundleDisplayName': "Kino TV Resizer",
        'CFBundleGetInfoString': "Videoni 720p ga tushirish",
        'CFBundleIdentifier': "com.kinotv.resizer",
        'CFBundleVersion': "1.0.0",
    }
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)