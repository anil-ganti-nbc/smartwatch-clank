# Windows symmetry

Windows packaging must launch the same canonical Smartwatch core and loopback dashboard. It must honour `SMARTWATCH_CLANK_DATA_DIR`, defaulting to `%LOCALAPPDATA%\Smartwatch Clank`, keep mutable SQLite state outside the bundle, and never bundle secrets or production state. No Windows executable is introduced in macOS Stage A.
