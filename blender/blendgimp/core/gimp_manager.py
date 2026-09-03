import os
import re
import subprocess
import time


# ============================================================
# GIMP PROCESS STATE
# ============================================================

ENGINE_MODE_HEADLESS = "HEADLESS"
ENGINE_MODE_VISIBLE_DEBUG = "VISIBLE_DEBUG"

ENGINE_STATE_STOPPED = "STOPPED"
ENGINE_STATE_STARTING = "STARTING"
ENGINE_STATE_CONNECTING = "CONNECTING"
ENGINE_STATE_CONNECTED = "CONNECTED"
ENGINE_STATE_DISCONNECTED = "DISCONNECTED"
ENGINE_STATE_STOPPING = "STOPPING"
ENGINE_STATE_FAILED = "FAILED"

VALID_ENGINE_MODES = {
    ENGINE_MODE_HEADLESS,
    ENGINE_MODE_VISIBLE_DEBUG,
}

HEADLESS_ARGUMENTS = (
    "--no-interface",
    "--no-splash",
    "--console-messages",
    "--new-instance",
)

gimp_process = None

_engine_runtime = {
    "state": ENGINE_STATE_STOPPED,
    "mode": None,
    "pid": None,
    "launched_at": 0.0,
    "last_exit_code": None,
    "last_error": "",
    "launched_by_blendgimp": False,
}


# ============================================================
# GIMP DETECTION
# ============================================================

def find_gimp():
    """
    Search for a supported GIMP executable.

    The development portable GIMP path is currently checked
    first.

    Later this will be replaced with configurable and automatic
    GIMP discovery.
    """

    possible_paths = [

        # ----------------------------------------------------
        # BlendGimp development GIMP
        # ----------------------------------------------------

        r"C:\Users\hillary.fordiii\Downloads\BlendGimp\tools\gimp3\bin\gimp-3.2.exe",

        r"C:\Users\hillary.fordiii\Downloads\BlendGimp\tools\gimp3\bin\gimp.exe",

        r"C:\Users\hillary.fordiii\Downloads\BlendGimp\tools\gimp3\bin\gimp-3.exe",

        # ----------------------------------------------------
        # Possible standard Windows installations
        # ----------------------------------------------------

        r"C:\Program Files\GIMP 3\bin\gimp-3.2.exe",

        r"C:\Program Files\GIMP 3\bin\gimp.exe",

        r"C:\Program Files\GIMP 3.0\bin\gimp-3.2.exe",

        r"C:\Program Files\GIMP 3.0\bin\gimp.exe",
    ]

    for path in possible_paths:

        if os.path.isfile(path):

            print(
                f"BLENDGIMP: "
                f"Candidate GIMP executable found: {path}"
            )

            return path

    print(
        "BLENDGIMP: "
        "No supported GIMP executable found"
    )

    return None


# ============================================================
# GIMP VERSION
# ============================================================

def get_gimp_version(gimp_path):
    """
    Determine the GIMP version.

    Windows:
        Read the executable metadata.

    Linux/macOS:
        Use the --version command.

    Reading the Windows executable metadata avoids GIMP 3.2
    opening its own console window when --version is used.
    """

    if not gimp_path:
        return None

    # ========================================================
    # WINDOWS
    # ========================================================

    if os.name == "nt":

        try:

            safe_path = gimp_path.replace(
                "'",
                "''"
            )

            powershell_command = (
                f"$v = "
                f"(Get-Item -LiteralPath "
                f"'{safe_path}').VersionInfo; "
                f"if ($v.ProductVersion) "
                f"{{ $v.ProductVersion }} "
                f"elseif ($v.FileVersion) "
                f"{{ $v.FileVersion }}"
            )

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    powershell_command,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

            stdout = (
                result.stdout or ""
            ).strip()

            stderr = (
                result.stderr or ""
            ).strip()

            print(
                "BLENDGIMP: "
                f"PowerShell return code = "
                f"{result.returncode}"
            )

            print(
                "BLENDGIMP: "
                f"PowerShell stdout = "
                f"{repr(stdout)}"
            )

            print(
                "BLENDGIMP: "
                f"PowerShell stderr = "
                f"{repr(stderr)}"
            )

            if stdout:

                match = re.search(
                    r"(\d+\.\d+\.\d+)",
                    stdout
                )

                if match:

                    version = match.group(1)

                    print(
                        "BLENDGIMP: "
                        f"GIMP version detected = "
                        f"{version}"
                    )

                    return version

            print(
                "BLENDGIMP: "
                "No usable version found "
                "in EXE metadata"
            )

            return None

        except Exception as exc:

            print(
                "BLENDGIMP: "
                "Windows version detection "
                f"failed: {exc}"
            )

            return None

    # ========================================================
    # LINUX / MACOS
    # ========================================================

    try:

        result = subprocess.run(
            [
                gimp_path,
                "--version"
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )

        output = (
            (result.stdout or "").strip()
            or
            (result.stderr or "").strip()
        )

        print(
            "BLENDGIMP: "
            f"GIMP version output = "
            f"{repr(output)}"
        )

        match = re.search(
            r"(\d+\.\d+\.\d+)",
            output
        )

        if match:

            version = match.group(1)

            print(
                "BLENDGIMP: "
                f"GIMP version detected = "
                f"{version}"
            )

            return version

    except Exception as exc:

        print(
            "BLENDGIMP: "
            "Could not determine "
            f"GIMP version: {exc}"
        )

    return None


# ============================================================
# ENGINE MODE AND STATE HELPERS
# ============================================================

def normalize_engine_mode(engine_mode):
    mode = str(
        engine_mode
        or ENGINE_MODE_VISIBLE_DEBUG
    ).upper()

    if mode not in VALID_ENGINE_MODES:
        raise ValueError(
            f"Unsupported GIMP engine mode: {engine_mode}"
        )

    return mode


def build_launch_command(
    gimp_path,
    engine_mode=ENGINE_MODE_VISIBLE_DEBUG
):
    """Build the GIMP command without starting a process."""

    mode = normalize_engine_mode(
        engine_mode
    )

    command = [
        str(gimp_path)
    ]

    if mode == ENGINE_MODE_HEADLESS:
        command.extend(
            HEADLESS_ARGUMENTS
        )

    return command


def set_engine_state(
    state,
    error=None
):
    _engine_runtime[
        "state"
    ] = str(state)

    if error is not None:
        _engine_runtime[
            "last_error"
        ] = str(error)


def mark_engine_connecting():
    set_engine_state(
        ENGINE_STATE_CONNECTING
    )


def mark_engine_connected():
    set_engine_state(
        ENGINE_STATE_CONNECTED,
        error=""
    )


def mark_engine_disconnected(
    error=""
):
    state = (
        ENGINE_STATE_DISCONNECTED
        if is_gimp_running()
        else ENGINE_STATE_FAILED
    )

    set_engine_state(
        state,
        error=error
    )


def get_engine_snapshot():
    """Return a copy of lifecycle state safe for UI diagnostics."""

    snapshot = dict(
        _engine_runtime
    )

    snapshot[
        "running"
    ] = is_gimp_running()

    return snapshot


# ============================================================
# GIMP PROCESS STATUS
# ============================================================

def is_gimp_running():
    """
    Return True if the GIMP process launched by BlendGimp
    is still running, and record an unexpected process exit.
    """

    global gimp_process

    if gimp_process is None:
        return False

    try:
        exit_code = gimp_process.poll()

    except Exception as exc:
        set_engine_state(
            ENGINE_STATE_FAILED,
            error=(
                "Could not check GIMP process: "
                f"{exc}"
            )
        )

        print(
            "BLENDGIMP: "
            f"Could not check GIMP process: {exc}"
        )

        return False

    if exit_code is None:
        return True

    _engine_runtime[
        "last_exit_code"
    ] = int(exit_code)

    _engine_runtime[
        "pid"
    ] = None

    if _engine_runtime.get(
        "state"
    ) not in {
        ENGINE_STATE_STOPPED,
        ENGINE_STATE_STOPPING,
    }:
        set_engine_state(
            ENGINE_STATE_FAILED,
            error=(
                "GIMP engine exited unexpectedly "
                f"with code {exit_code}"
            )
        )

    return False


# ============================================================
# LAUNCH GIMP
# ============================================================

def launch_gimp(
    gimp_path,
    engine_mode=ENGINE_MODE_VISIBLE_DEBUG
):
    """
    Launch a persistent GIMP engine and store its process handle.

    Headless mode uses GIMP's supported ``--no-interface`` path while still
    loading brushes, patterns, plug-ins, and the automatic BlendGimp
    persistent procedure. Visible/Debug intentionally retains the original
    one-argument launch behavior.

    Returns ``(success, pid)``.
    """

    global gimp_process

    try:
        mode = normalize_engine_mode(
            engine_mode
        )

    except ValueError as exc:
        set_engine_state(
            ENGINE_STATE_FAILED,
            error=exc
        )
        return False, None

    if not gimp_path:
        set_engine_state(
            ENGINE_STATE_FAILED,
            error="GIMP executable path is empty"
        )
        return False, None

    if not os.path.isfile(gimp_path):
        message = (
            "Cannot launch GIMP. "
            "Executable does not exist."
        )

        set_engine_state(
            ENGINE_STATE_FAILED,
            error=message
        )

        print(
            "BLENDGIMP: "
            + message
        )

        return False, None

    # --------------------------------------------------------
    # Already running
    # --------------------------------------------------------

    if is_gimp_running():
        current_mode = _engine_runtime.get(
            "mode"
        )

        if current_mode != mode:
            _engine_runtime[
                "last_error"
            ] = (
                "Restart the engine to apply "
                f"{mode} mode"
            )

        print(
            "BLENDGIMP: "
            "GIMP engine is already running"
        )

        return (
            True,
            gimp_process.pid
        )

    # --------------------------------------------------------
    # Launch
    # --------------------------------------------------------

    command = build_launch_command(
        gimp_path,
        mode
    )

    popen_options = {
        "cwd": os.path.dirname(
            gimp_path
        ) or None,
    }

    if os.name == "nt" and mode == ENGINE_MODE_HEADLESS:
        # Do not replace the hidden GIMP UI with a console window on Windows.
        popen_options[
            "creationflags"
        ] = getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0x08000000
        )

    try:
        set_engine_state(
            ENGINE_STATE_STARTING,
            error=""
        )

        print(
            "BLENDGIMP: "
            f"Launching GIMP engine mode={mode} "
            f"from {gimp_path}"
        )

        print(
            "BLENDGIMP: "
            "Launch command = "
            + " ".join(command)
        )

        gimp_process = subprocess.Popen(
            command,
            **popen_options
        )

        _engine_runtime.update(
            {
                "state": ENGINE_STATE_STARTING,
                "mode": mode,
                "pid": int(gimp_process.pid),
                "launched_at": time.monotonic(),
                "last_exit_code": None,
                "last_error": "",
                "launched_by_blendgimp": True,
            }
        )

        print(
            "BLENDGIMP: "
            f"GIMP engine started "
            f"PID={gimp_process.pid} "
            f"mode={mode}"
        )

        return (
            True,
            gimp_process.pid
        )

    except Exception as exc:
        gimp_process = None

        _engine_runtime.update(
            {
                "state": ENGINE_STATE_FAILED,
                "pid": None,
                "last_error": str(exc),
                "launched_by_blendgimp": False,
            }
        )

        print(
            "BLENDGIMP: "
            f"Failed to launch GIMP: {exc}"
        )

        return False, None


# ============================================================
# STOP GIMP
# ============================================================

def stop_gimp(
    timeout=5.0,
    graceful_requested=False
):
    """
    Stop the GIMP process owned by BlendGimp.

    When the IPC layer has already asked GIMP to quit, wait for that clean
    application exit first. Platform termination is a bounded fallback for a
    failed or unavailable shutdown request, and kill is used only after the
    fallback timeout.

    Returns ``(success, exit_code, forced)``.
    """

    global gimp_process

    process = gimp_process

    if process is None:
        set_engine_state(
            ENGINE_STATE_STOPPED,
            error=""
        )
        _engine_runtime[
            "pid"
        ] = None
        return True, None, False

    if not is_gimp_running():
        exit_code = _engine_runtime.get(
            "last_exit_code"
        )
        gimp_process = None
        set_engine_state(
            ENGINE_STATE_STOPPED,
            error=""
        )
        return True, exit_code, False

    forced = False

    try:
        set_engine_state(
            ENGINE_STATE_STOPPING,
            error=""
        )

        print(
            "BLENDGIMP: "
            f"Stopping GIMP engine PID={process.pid}"
        )

        graceful_exit = False

        if graceful_requested:
            try:
                exit_code = process.wait(
                    timeout=max(
                        0.1,
                        float(timeout)
                    )
                )
                graceful_exit = True

            except subprocess.TimeoutExpired:
                print(
                    "BLENDGIMP: "
                    "Clean GIMP shutdown timed out; "
                    "using process termination fallback"
                )

        if not graceful_exit:
            process.terminate()

            try:
                exit_code = process.wait(
                    timeout=max(
                        0.1,
                        float(timeout)
                    )
                )

            except subprocess.TimeoutExpired:
                forced = True
                print(
                    "BLENDGIMP: "
                    "GIMP did not stop before the fallback timeout; "
                    "forcing process exit"
                )
                process.kill()
                exit_code = process.wait(
                    timeout=2.0
                )

        _engine_runtime.update(
            {
                "state": ENGINE_STATE_STOPPED,
                "pid": None,
                "last_exit_code": int(exit_code),
                "last_error": "",
                "launched_by_blendgimp": False,
            }
        )

        gimp_process = None

        print(
            "BLENDGIMP: "
            f"GIMP engine stopped exit={exit_code} "
            f"graceful={graceful_exit} "
            f"forced={forced}"
        )

        return True, int(exit_code), forced

    except Exception as exc:
        set_engine_state(
            ENGINE_STATE_FAILED,
            error=(
                "Could not stop GIMP engine: "
                f"{exc}"
            )
        )

        print(
            "BLENDGIMP: "
            f"Could not stop GIMP engine: {exc}"
        )

        return False, None, forced


# ============================================================
# CLEAR PROCESS REFERENCE
# ============================================================

def clear_process_reference(
    reset_state=True
):
    """
    Forget the GIMP process handle without terminating the process.

    This remains available for Visible/Debug sessions that the artist wants
    to leave open after disabling the Blender extension.
    """

    global gimp_process

    gimp_process = None

    _engine_runtime[
        "pid"
    ] = None

    _engine_runtime[
        "launched_by_blendgimp"
    ] = False

    if reset_state:
        set_engine_state(
            ENGINE_STATE_STOPPED,
            error=""
        )

    print(
        "BLENDGIMP: "
        "GIMP process reference cleared"
    )
