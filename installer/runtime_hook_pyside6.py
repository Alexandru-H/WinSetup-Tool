"""PyInstaller runtime hook for PySide6 + shiboken6.

In onefile mode, PyInstaller extracts to a temp folder which
sometimes confuses PySide6's plugin loader. This hook tells Qt
where to find its plugins at runtime.

Required because PySide6's Qt.PluginsPath falls back to a wrong
default in frozen contexts.
"""

import os
import sys

if hasattr(sys, "_MEIPASS"):
    # Frozen mode (onefile or onedir) — Qt plugins live next to the bundled
    # PySide6 package.
    base = sys._MEIPASS
    plugins = os.path.join(base, "PySide6", "plugins")
    if os.path.isdir(plugins):
        os.environ["QT_PLUGIN_PATH"] = plugins

    # Same for QML if used (defensive)
    qml = os.path.join(base, "PySide6", "qml")
    if os.path.isdir(qml):
        os.environ["QML2_IMPORT_PATH"] = qml
