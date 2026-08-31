import os
import re
import subprocess


# ============================================================
# GIMP PROCESS STATE
# ============================================================

gimp_process = None


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
# GIMP PROCESS STATUS
# ============================================================

def is_gimp_running():
    """
    Return True if the GIMP process launched by BlendGimp
    is still running.
    """

    global gimp_process

    if gimp_process is None:
        return False

    try:

        return (
            gimp_process.poll() is None
        )

    except Exception as exc:

        print(
            "BLENDGIMP: "
            f"Could not check GIMP process: {exc}"
        )

        return False


# ============================================================
# LAUNCH GIMP
# ============================================================

def launch_gimp(gimp_path):
    """
    Launch GIMP and store the process handle.

    Returns:

        True, PID

    on success.

    Returns:

        False, None

    on failure.
    """

    global gimp_process

    if not gimp_path:
        return False, None

    if not os.path.isfile(gimp_path):

        print(
            "BLENDGIMP: "
            "Cannot launch GIMP. "
            "Executable does not exist."
        )

        return False, None

    # --------------------------------------------------------
    # Already running
    # --------------------------------------------------------

    if is_gimp_running():

        print(
            "BLENDGIMP: "
            "GIMP is already running"
        )

        return (
            True,
            gimp_process.pid
        )

    # --------------------------------------------------------
    # Launch
    # --------------------------------------------------------

    try:

        print(
            "BLENDGIMP: "
            f"Launching GIMP from {gimp_path}"
        )

        gimp_process = subprocess.Popen(
            [gimp_path],
            cwd=os.path.dirname(
                gimp_path
            )
        )

        print(
            "BLENDGIMP: "
            f"GIMP process started "
            f"PID={gimp_process.pid}"
        )

        return (
            True,
            gimp_process.pid
        )

    except Exception as exc:

        gimp_process = None

        print(
            "BLENDGIMP: "
            f"Failed to launch GIMP: {exc}"
        )

        return False, None


# ============================================================
# CLEAR PROCESS REFERENCE
# ============================================================

def clear_process_reference():
    """
    Forget the GIMP process handle.

    This intentionally DOES NOT terminate GIMP.
    """

    global gimp_process

    gimp_process = None

    print(
        "BLENDGIMP: "
        "GIMP process reference cleared"
    )