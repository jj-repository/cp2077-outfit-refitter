r"""Everything around the refit itself: settings, finding the game, getting WolvenKit, extracting the body shapes
from the user's own installed files, unpacking outfit downloads, installing/uninstalling refits.
No widgets here: the GUI and the --selftest both call run_batch()."""

import contextlib, filecmp, fnmatch, glob, hashlib, http.client, json, os, re, shutil, subprocess, sys, tempfile, threading, time, urllib.request, zipfile

import rdar

APP_DIR = getattr(
    sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))
)  # PyInstaller unpack dir or source
DATA = os.environ.get("BODYREFIT_HOME") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "OutfitRefitter"
)
_OLD_DATA = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BodyRefitter"
)
if (
    not os.environ.get("BODYREFIT_HOME")
    and not os.path.exists(DATA)
    and os.path.isdir(_OLD_DATA)
):
    try:  # the tool was called Body Refitter in 1.0: move its data folder over once
        os.rename(_OLD_DATA, DATA)
    except OSError:
        DATA = _OLD_DATA

VERSION = "2.0"  # also in version_info.txt (the exe's properties)


def pasties_value(on, text):
    """'Move pasties up / down' as whole mm: 0 when the box is unticked or the text is no number; else the number
    rounded and kept within -30 .. +30."""
    if not on:
        return 0
    try:
        v = float(str(text).replace(",", ".").replace("mm", "").strip())
    except (ValueError, OverflowError):
        return 0
    if v != v or v in (float("inf"), float("-inf")):  # nan, inf: no number
        return 0
    return int(min(30, max(-30, round(v))))


def chest_room_value(on, text):
    """'Adjust room at the chest' as mm: 0.0 when the box is unticked or the text is no number; else the number
    (decimal comma allowed) rounded to 0.5 and kept within -2 .. +6."""
    if not on:
        return 0.0
    try:
        v = float(str(text).replace(",", ".").replace("mm", "").strip())
    except (ValueError, OverflowError):
        return 0.0
    if v != v or v in (float("inf"), float("-inf")):  # nan, inf: no number
        return 0.0
    return min(6.0, max(-2.0, round(v * 2) / 2))


def documents_dir():
    r"""The real Documents folder, also when OneDrive (or the user) moved it. Falls back to %USERPROFILE%\Documents,
    then to the folder the tool runs from."""
    try:
        import ctypes, uuid
        from ctypes import wintypes

        fid = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")  # FOLDERID_Documents
        guid = (ctypes.c_byte * 16).from_buffer_copy(fid.bytes_le)
        p = ctypes.c_wchar_p()
        sh = ctypes.windll.shell32
        sh.SHGetKnownFolderPath.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        if sh.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p)) == 0:
            d = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            if d and os.path.isdir(d):
                return d
    except Exception:
        pass
    d = os.path.join(
        os.environ.get("USERPROFILE") or os.path.expanduser("~"), "Documents"
    )
    if os.path.isdir(d):
        return d
    return os.path.dirname(
        sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    )


DEFAULT_OUT = os.path.join(documents_dir(), "Outfit Refitter")

WK_VERSION = "9.1.1"
WK_URL = (
    "https://github.com/WolvenKit/WolvenKit/releases/download/%s/WolvenKit.Console-%s.zip"
    % ((WK_VERSION,) * 2)
)
WK_SHA256 = "a14eae46931251635256e7b209cd4aa998ae5039a5fbd506fefaab671bf7f451"  # from the GitHub release page
# MeshExportArgs our refit is proven with: no materials, every LOD exported (the body shape uses LOD 1)
WK_APPSETTINGS = {
    "Logging": {"LogLevel": {"Default": "Information", "Microsoft": "Warning"}},
    "XbmExportArgs": {"UncookExtension": "dds"},
    "MeshExportArgs": {"withMaterials": False, "isGLBinary": True, "lodFilter": False},
}
DOTNET_URL = "https://dotnet.microsoft.com/download/dotnet/10.0"


class ToolError(Exception):
    """A problem the user can understand and maybe fix; the message is shown as-is."""


def explain(e):
    """Plain words for Windows file errors a user can fix (access denied, disk full, path too long). -> text or
    None for anything else."""
    if isinstance(e, ToolError):
        return str(e)
    if not isinstance(e, OSError):
        return None
    f = e.filename or e.filename2 or ""
    win = getattr(e, "winerror", None)
    if isinstance(e, PermissionError) or win in (5, 32, 33):
        return (
            "Windows did not let the tool use %s. Close the game and any program that may have it open (a mod "
            "manager, WolvenKit, an archive tool, a file window) and try again. If it is in a protected folder "
            "(like Program Files), pick another output folder or install the refit yourself; an antivirus can "
            "block it too." % (f or "a file")
        )
    if e.errno == 28 or win in (39, 112):
        return "The disk is full (%s). Free some space and try again." % (
            f or "no file name"
        )
    if isinstance(e, FileNotFoundError) and len(f) >= 240 or win == 206:
        return (
            "A file path got too long for Windows (%d characters). Move the download to a short folder like "
            "C:\\Mods and try again." % len(f)
        )
    return None


def bodies():
    return json.load(open(os.path.join(APP_DIR, "bodies.json"), encoding="utf-8"))


def body_name(tag):
    return bodies()["bodies"][tag]["name"]


def xl_name(tag):
    """The ArchiveXL {body} name of a body: vanilla = base_body, Angel = angel, ..."""
    return bodies()["bodies"][tag].get("xl", tag)


def hint_rx(tag):
    """Words in a file name that mean 'made for this body'. A hint in lower case matches any case, one with capitals
    only exactly ('EVE' = the body, 'Eve' = often just a character's name). ponytail: 'vanilla' matches anywhere
    (as in 1.0-1.4, e.g. 'VanillaButt'); other hints only as a whole word, so 'eve' never matches 'Evelyn'."""
    words = "|".join(
        "(?i:%s)" % re.escape(w) if w.islower() else re.escape(w)
        for w in bodies()["bodies"][tag].get("hints", [tag])
    )
    if tag == "vanilla":
        return re.compile(words)
    return re.compile(r"(^|[^a-zA-Z])(%s)([^a-zA-Z]|$)" % words)


# ---------------- settings ----------------


def _settings_path():
    return os.path.join(DATA, "settings.json")


def load_settings():
    try:
        return json.load(open(_settings_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(s):
    os.makedirs(DATA, exist_ok=True)
    tmp = _settings_path() + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)
    os.replace(tmp, _settings_path())  # an interrupted save never loses the settings


# ---------------- game folder ----------------


def is_game_dir(p):
    return bool(p) and os.path.isfile(
        os.path.join(p, "bin", "x64", "Cyberpunk2077.exe")
    )


def game_root(p):
    r"""The game folder for a folder the user picked one level off: 'bin\x64' or 'bin' inside it, or the Xbox app's
    outer folder (the game is in its 'Content'). -> path or None."""
    if not p:
        return None
    p = os.path.normpath(p)
    for c in (
        p,
        os.path.join(p, "Content"),
        os.path.join(p, "Cyberpunk 2077"),
        os.path.join(p, "Cyberpunk 2077", "Content"),
        os.path.dirname(p),
        os.path.dirname(os.path.dirname(p)),
    ):
        if is_game_dir(c):
            return c
    return None


def _reg(root, key, value):
    try:
        import winreg

        with winreg.OpenKey(root, key) as k:
            return winreg.QueryValueEx(k, value)[0]
    except OSError:
        return None


def find_game():
    """Steam libraries, GOG and Epic installs, then common folders. -> path or None."""
    import winreg

    cands = []
    steam = _reg(
        winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"
    ) or _reg(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"
    )
    if steam:
        libs = [steam]
        try:
            vdf = open(
                os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8"
            ).read()
            libs += [
                p.replace("\\\\", "\\") for p in re.findall(r'"path"\s+"([^"]+)"', vdf)
            ]
        except OSError:
            pass
        cands += [
            os.path.join(l, "steamapps", "common", "Cyberpunk 2077") for l in libs
        ]
    for key in (
        r"SOFTWARE\WOW6432Node\GOG.com\Games\1423049311",
        r"SOFTWARE\GOG.com\Games\1423049311",
    ):
        cands.append(_reg(winreg.HKEY_LOCAL_MACHINE, key, "path"))
    for m in glob.glob(r"C:\ProgramData\Epic\EpicGamesLauncher\Data\Manifests\*.item"):
        try:
            j = json.load(open(m, encoding="utf-8"))
            if "cyberpunk" in j.get("DisplayName", "").lower():
                cands.append(j.get("InstallLocation"))
        except (OSError, ValueError):
            pass
    for drive in "CDEFGH":
        for sub in (
            r"Program Files (x86)\Steam\steamapps\common",
            r"SteamLibrary\steamapps\common",
            r"GOG Galaxy\Games",
            r"Program Files\Epic Games",
            r"Games",
            r"XboxGames",  # Xbox app / Game Pass: the game is in its Content folder
        ):
            cands.append(os.path.join(drive + ":\\", sub, "Cyberpunk 2077"))
    return next((game_root(c) for c in cands if game_root(c)), None)


def mod_dir(game):
    return os.path.join(game, "archive", "pc", "mod")


# ---------------- running tools quietly ----------------

_FLAGS = (
    0x08000000 | 0x00004000
)  # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS (keeps a running game smooth)


def run(args, game=None, ok=None):
    """Run a tool hidden at low priority. ok: text that marks success even on a non-zero exit (WolvenKit import)."""
    if getattr(_JOB, "cancel", None) and _JOB.cancel.is_set():
        raise ToolError("Stopped.")
    env = dict(os.environ, **_ENV)
    if game:
        env["CP77_DIR"] = game
    p = subprocess.Popen(
        [str(a) for a in args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        env=env,
        creationflags=_FLAGS,
        stdin=subprocess.DEVNULL,
    )
    _RUNNING.add(p)
    try:
        out, err = p.communicate(timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        raise ToolError(
            "%s did not finish within %d minutes and was stopped. Try again; if it happens again, use "
            "'Report Issues'." % (os.path.basename(str(args[0])), TOOL_TIMEOUT // 60)
        )
    finally:
        _RUNNING.discard(p)
    if p.returncode and not (ok and ok in out):
        raise RuntimeError(
            "%s %s failed:\n%s%s"
            % (os.path.basename(str(args[0])), args[1], out[-1500:], err[-1500:])
        )
    return out


_RUNNING = set()  # tools started by run() that are still working
_JOB = threading.local()  # .cancel: the Event of the refit running in this thread (run() starts nothing once set)
TOOL_TIMEOUT = (
    30 * 60
)  # ponytail: one WolvenKit / 7-Zip call; the biggest meshes seen take a few minutes


def stop_tools():
    """Stop running WolvenKit / 7-Zip processes (the window is closing); the refit they belong to then fails."""
    for p in list(_RUNNING):
        try:
            p.kill()
        except OSError:
            pass


# ---------------- WolvenKit ----------------


def wk_exe():
    return os.path.join(DATA, "wolvenkit", "WolvenKit.CLI.exe")


_ENV = {}  # extra environment for the tools (a working DOTNET_ROOT, see _wk_ready)
# .NET's own "no fitting .NET" message always starts like this. ponytail: not "Microsoft.NETCore.App" alone: a
# WolvenKit crash report names .NET's folder too, and 1.7 then blamed .NET for any crash (reported with 10.0.0 found)
_NO_NET = (
    "You must install .NET",
    "You must install or update .NET",
)


def net_runtimes():
    """[(version, .NET folder, 64-bit)] of the .NET runtimes in the usual places (the installer's, a user install,
    and any DOTNET_ROOT that is set)."""
    roots = [
        os.environ.get(k, "")
        for k in ("DOTNET_ROOT_X64", "DOTNET_ROOT", "DOTNET_ROOT(x86)")
    ]
    roots += [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "dotnet"),
        os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "dotnet"
        ),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "dotnet"),
        os.path.join(os.path.expanduser("~"), ".dotnet"),
    ]
    out, seen = [], set()
    for r in roots:
        if not r or os.path.normcase(os.path.abspath(r)) in seen:
            continue
        seen.add(os.path.normcase(os.path.abspath(r)))
        for v in sorted(
            glob.glob(os.path.join(r, "shared", "Microsoft.NETCore.App", "*"))
        ):
            out.append((os.path.basename(v), r, "(x86)" not in r))
    return out


def _wk_ready(exe, log):
    try:
        v = run([exe, "--version"]).strip()
    except (RuntimeError, OSError) as e:
        if not any(m in str(e) for m in _NO_NET):
            raise ToolError(
                "WolvenKit does not start. An antivirus may have blocked or removed one of its files: allow the "
                "folder %s in your antivirus and press Refit again.\n\n%s"
                % (os.path.dirname(exe), str(e)[-1500:])
            )
        # WolvenKit can't find .NET 10. Often it is installed but a leftover DOTNET_ROOT setting (from developer
        # tools or game engines) sends WolvenKit to another folder: try every 64-bit .NET 10 found ourselves.
        rts = net_runtimes()
        for root in dict.fromkeys(
            r for ver, r, x64 in rts if x64 and re.match(r"10\.\d+\.\d+$", ver)
        ):
            _ENV.update(DOTNET_ROOT=root, DOTNET_ROOT_X64=root)
            try:
                v = run([exe, "--version"]).strip()
                log("Note: WolvenKit uses the .NET 10 in %s." % root)
                break
            except (RuntimeError, OSError):
                _ENV.clear()
        else:
            found = ", ".join(
                "%s%s in %s" % (ver, "" if x64 else " (32-bit)", r)
                for ver, r, x64 in rts
            )
            raise ToolError(
                "WolvenKit needs the free Microsoft .NET 10 Runtime (64-bit / x64). Install it from %s "
                "(pick '.NET Runtime 10', x64), then press Refit again.\n\n.NET found on this PC: %s.%s"
                "\n\nWhat WolvenKit said:\n%s"
                % (
                    DOTNET_URL,
                    found or "none",
                    "\nA DOTNET_ROOT setting is set on this PC (%s); WolvenKit looks only there."
                    % (
                        os.environ.get("DOTNET_ROOT_X64")
                        or os.environ.get("DOTNET_ROOT")
                    )
                    if os.environ.get("DOTNET_ROOT_X64")
                    or os.environ.get("DOTNET_ROOT")
                    else "",
                    str(e)[-600:],
                )
            )
    if not v.startswith(WK_VERSION):
        log(
            "Note: WolvenKit %s found; this tool is tested with %s."
            % ((v.splitlines() or ["?"])[0][:20], WK_VERSION)
        )
    return exe


def find_wk_cli(path):
    """WolvenKit.CLI.exe in a picked folder or a few levels below it (people often pick the folder above the
    unzipped one), or the picked .exe itself; None if there is none."""
    if path.lower().endswith(".exe"):
        return path if os.path.isfile(path) else None
    root = os.path.abspath(path)
    for d, subs, files in os.walk(root):
        if "WolvenKit.CLI.exe" in files:
            return os.path.join(d, "WolvenKit.CLI.exe")
        if d[len(root) :].count(os.sep) >= 3:
            subs[
                :
            ] = []  # ponytail: 3 levels deep is enough for 'Downloads\WolvenKit\WolvenKit.Console-x\'
    return None


WK_NOT_FOUND = (
    "No WolvenKit.CLI.exe in %s.\n\nThe tool needs WolvenKit's console version (the file "
    "WolvenKit.Console-<version>.zip, unzipped). The download of the WolvenKit app may not have it. You can also leave "
    "this empty: the tool then downloads WolvenKit %s by itself.\n\n" + WK_URL
)


def ensure_wolvenkit(log, progress=lambda f: None, existing=None, retry=True):
    """WolvenKit.CLI in our data folder. existing: a WolvenKit folder the user already has (copied, never changed).
    Otherwise the official release zip is downloaded once and checked against its SHA-256."""
    exe = wk_exe()
    if existing:
        src = find_wk_cli(existing)
        if not src:
            raise ToolError(WK_NOT_FOUND % (existing, WK_VERSION))
        if os.path.normcase(os.path.abspath(src)) != os.path.normcase(exe):
            log("Copying your WolvenKit from %s ..." % os.path.dirname(src))
            shutil.rmtree(os.path.dirname(exe), ignore_errors=True)
            shutil.copytree(os.path.dirname(src), os.path.dirname(exe))
    if not os.path.isfile(exe):
        os.makedirs(DATA, exist_ok=True)
        zp = os.path.join(DATA, "WolvenKit.Console-%s.zip" % WK_VERSION)
        log(
            "First run: downloading WolvenKit %s from its official GitHub page (about 45 MB) ..."
            % WK_VERSION
        )
        try:
            with (
                urllib.request.urlopen(
                    urllib.request.Request(
                        WK_URL, headers={"User-Agent": "OutfitRefitter"}
                    ),
                    timeout=60,
                ) as r,
                open(zp + ".part", "wb") as f,
            ):
                total, got = int(r.headers.get("Content-Length") or 0), 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    got += len(b)
                    if total:
                        progress(got / total)
        except (
            OSError,
            http.client.HTTPException,
        ) as e:  # also a connection cut mid-download
            raise ToolError(
                "Could not download WolvenKit (%s). Check your internet connection, or download\n%s\n"
                "yourself, unzip it, and choose that folder under 'WolvenKit...'."
                % (e, WK_URL)
            )
        h = hashlib.sha256(open(zp + ".part", "rb").read()).hexdigest()
        if h != WK_SHA256:
            os.remove(zp + ".part")
            raise ToolError(
                "The WolvenKit download is damaged (checksum mismatch). Please try again."
            )
        os.replace(zp + ".part", zp)
        tmp = os.path.join(DATA, "wolvenkit_tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        zipfile.ZipFile(zp).extractall(tmp)
        found = glob.glob(os.path.join(tmp, "**", "WolvenKit.CLI.exe"), recursive=True)
        if not found:
            raise ToolError("The WolvenKit download has no WolvenKit.CLI.exe.")
        shutil.rmtree(os.path.dirname(exe), ignore_errors=True)
        shutil.move(os.path.dirname(found[0]), os.path.dirname(exe))
        shutil.rmtree(tmp, ignore_errors=True)
        os.remove(zp)
        log("WolvenKit installed (checksum OK).")
    json.dump(
        WK_APPSETTINGS,
        open(os.path.join(os.path.dirname(exe), "appsettings.json"), "w"),
        indent=1,
    )
    try:
        return _wk_ready(exe, log)
    except ToolError as e:
        # a broken copy of our own download (a file removed by an antivirus, a cut-off unzip): get it fresh once
        if existing or "does not start" not in str(e) or not retry:
            raise
        log("WolvenKit does not start; downloading it again ...")
        shutil.rmtree(os.path.dirname(exe), ignore_errors=True)
        return ensure_wolvenkit(log, progress, retry=False)


def ensure_oodle(game):
    """WolvenKit 9.1.1 loads the game's Oodle DLL only from %LOCALAPPDATA%. Without it, it silently falls back to a
    compressor that breaks big mesh buffers. Copy it from the user's own game (never shipped with this tool)."""
    src = os.path.join(game, "bin", "x64", "oo2ext_7_win64.dll")
    if not os.path.isfile(src):
        raise ToolError(
            "oo2ext_7_win64.dll is missing in your game folder (bin\\x64). Verify the game files."
        )
    dst = os.path.join(os.environ.get("LOCALAPPDATA", ""), "oo2ext_7_win64.dll")
    if not os.path.isfile(dst) or os.path.getsize(dst) != os.path.getsize(src):
        shutil.copy2(src, dst)
    rdar.init_oodle(game)


# ---------------- body shapes ("cages") ----------------


def find_body_archive(game, tag):
    """The installed body mod's .archive that replaces the vanilla female body meshes."""
    cfg = bodies()
    want = rdar.fnv(cfg["meshes"][0])
    md = mod_dir(game)
    hits = []
    for f in sorted(os.listdir(md)) if os.path.isdir(md) else []:
        if any(
            fnmatch.fnmatch(f.lower(), p.lower())
            for p in cfg["bodies"][tag].get("archives", [])
        ):
            p = os.path.join(md, f)
            try:
                if want in rdar.Rdar(p).hashes():
                    hits.append(p)
            except (
                Exception
            ):  # a broken or cut-off archive (struct.error too) is just not the body
                pass
    return max(hits, key=os.path.getsize) if hits else None


def check_body_archive(path):
    cfg = bodies()
    hs = rdar.Rdar(path).hashes()
    if not all(rdar.fnv(m) in hs for m in cfg["meshes"]):
        raise ToolError(
            "%s does not contain the body meshes this tool needs. Pick the body mod's main .archive."
            % os.path.basename(path)
        )


def check_body_file(path, tag):
    """Quick check of a body file the user picked (the window waits for it). An .archive must hold the body meshes;
    a download (zip / 7z / rar / folder) is only looked into on Refit, see body_archive_in()."""
    low = path.lower()
    if os.path.isdir(path):
        return
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            if not any(n.lower().endswith(".archive") for n in z.namelist()):
                raise ToolError(
                    "%s has no .archive file inside. Pick the %s download or its main .archive."
                    % (os.path.basename(path), body_name(tag))
                )
    elif low.endswith((".7z", ".rar")):
        if not (find_7z() or find_tar()):
            raise ToolError(
                "To read a .7z or .rar, install 7-Zip (7-zip.org), or unpack the download yourself and pick "
                "the body mod's .archive instead."
            )
    elif low.endswith(".archive"):
        check_body_archive(path)
    else:
        raise ToolError(
            "%s is not a zip, 7z, rar or .archive." % os.path.basename(path)
        )


def body_archive_in(path, dest, tag):
    """The body mod's .archive: path itself, or the one inside a body mod download (zip / 7z / rar / folder; only its
    .archive files are extracted, into dest). Never installed, only read for the body shape."""
    if not os.path.isdir(path) and path.lower().endswith(".archive"):
        return path
    root = unpack(path, dest, "*.archive")
    want = {rdar.fnv(m) for m in bodies()["meshes"]}
    found = []
    for a in sorted(glob.glob(os.path.join(root, "**", "*.archive"), recursive=True)):
        try:
            if want <= set(rdar.Rdar(a).hashes()):
                found.append(a)
        except Exception:  # a broken archive is just not the body
            pass
    pats = bodies()["bodies"][tag].get("archives", [])
    named = [
        a
        for a in found
        if any(fnmatch.fnmatch(os.path.basename(a).lower(), p.lower()) for p in pats)
    ] or found
    if not named:
        raise ToolError(
            "No %s body file found in %s. Pick the body mod's download or its main .archive."
            % (body_name(tag), os.path.basename(path))
        )
    kinds = sorted({os.path.basename(a) for a in named}, key=str.lower)
    if len(kinds) > 1:
        raise ToolError(
            "%s has several body files (%s). Unpack it and pick the one you use with 'Body file...'."
            % (os.path.basename(path), ", ".join(kinds))
        )
    return max(named, key=os.path.getsize)


def _stamp(paths):
    """Changes when a source file changes (a folder counts as its .archive files: the top level, or every
    subfolder when the top level has none, as in an unpacked body mod download)."""
    files = []
    for p in paths:
        files += (
            (
                sorted(glob.glob(os.path.join(p, "*.archive")))
                or sorted(glob.glob(os.path.join(p, "**", "*.archive"), recursive=True))
            )
            if os.path.isdir(p)
            else [p]
        )
    return ";".join(
        "%s|%d|%d" % (os.path.basename(f), os.path.getsize(f), int(os.path.getmtime(f)))
        for f in files
    )


def cage_dir(tag):
    """DATA\\cages\\<tag>; the vanilla shape keeps its 1.0-1.4 folder name 'van' (no re-read after the update)."""
    return os.path.join(DATA, "cages", "van" if tag == "vanilla" else tag)


def ensure_nipples(d, game, wk):
    """The base game's nipple mesh as a glb in the 'nipples' folder of cage folder d (bodies.json 'nipples'), read
    once from the game's own archives. Without it (not found, WolvenKit fails) the fit goes on without that
    surface."""
    m = bodies().get("nipples")
    od = os.path.join(d, "nipples")
    if not m or glob.glob(os.path.join(od, "*.glb")):
        return
    tmp = tempfile.mkdtemp(prefix="nip_", dir=_work_root())
    try:
        for s in vanilla_sources(game):
            run([wk, "unbundle", s, "-o", tmp, "-r", "^" + re.escape(m) + "$"], game)
        os.makedirs(od, exist_ok=True)
        run([wk, "export", os.path.join(tmp, m), "-o", od, "-gp", game], game)
    except (RuntimeError, OSError):
        shutil.rmtree(od, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def ensure_cages(key, sources, game, wk, log):
    """Body shape glbs for body tag key in cage_dir(key), extracted with WolvenKit from sources (the base game's
    content folder, or the user's body mod: an installed .archive or a picked download). Re-extracted when the source
    file changes."""
    d = cage_dir(key)
    stamp = _stamp(sources)
    glbs = sorted(glob.glob(os.path.join(d, "*.glb")))
    shapes = bodies()["bodies"][key].get("shapes", {})
    try:
        if (
            len(glbs) == 3
            and open(os.path.join(d, "source.txt"), encoding="utf-8").read() == stamp
            and (not shapes or os.path.isfile(os.path.join(d, "shapes.txt")))
        ):
            ensure_nipples(d, game, wk)
            return ";".join(glbs)
    except (OSError, ValueError):
        pass
    log(
        "Reading the %s body shape from your files (one time, about a minute) ..."
        % ("vanilla" if key == "vanilla" else body_name(key))
    )
    shutil.rmtree(d, ignore_errors=True)
    import refit

    refit.forget_bodies()  # the same file names get the new shape: drop what was cached from the old one
    tmp = tempfile.mkdtemp(prefix="cage_", dir=_work_root())
    try:
        meshes = bodies()["meshes"]
        rx = (
            "^(" + "|".join(re.escape(m) for m in meshes + list(shapes.values())) + ")$"
        )
        ub = os.path.join(tmp, "x")
        for s in sources:
            if not bodies()["bodies"][key].get("game"):
                s = body_archive_in(s, os.path.join(tmp, "dl"), key)
                check_body_archive(s)
            run([wk, "unbundle", s, "-o", ub, "-r", rx], game)
        os.makedirs(d)
        for m in meshes:
            mp = os.path.join(ub, m)
            if not os.path.isfile(mp):
                raise ToolError(
                    "Could not find %s in %s."
                    % (os.path.basename(m), ", ".join(map(os.path.basename, sources)))
                )
            run([wk, "export", mp, "-o", d, "-gp", game], game)
        glbs = sorted(glob.glob(os.path.join(d, "*.glb")))
        if len(glbs) != 3:
            raise ToolError("WolvenKit could not export the body shape.")
        got = []
        for (
            tag,
            m,
        ) in (
            shapes.items()
        ):  # chest shape add-ons; an older body version may not have them
            mp = os.path.join(ub, m)
            if os.path.isfile(mp):
                try:
                    od = os.path.join(d, "shapes", tag)
                    os.makedirs(od)
                    run([wk, "export", mp, "-o", od, "-gp", game], game)
                    got.append(tag)
                except RuntimeError:
                    pass
        if shapes:
            open(os.path.join(d, "shapes.txt"), "w", encoding="utf-8").write(
                " ".join(got)
            )
        open(os.path.join(d, "source.txt"), "w", encoding="utf-8").write(stamp)
        ensure_nipples(d, game, wk)
        return ";".join(glbs)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def shape_cage(cage, key, tag):
    """The body shape of body key while an outfit with visual tag `tag` is worn: the chest add-on mesh replaces the
    full body's chest submeshes (hide=...), as the body mod's .xl does. The normal shape when the body has no such
    add-on (or tag is None)."""
    b = bodies()["bodies"][key]
    g = (
        glob.glob(
            os.path.join(os.path.dirname(cage.split(";")[0]), "shapes", tag, "*.glb")
        )
        if tag in b.get("shapes", {})
        else []
    )
    if not g:
        return cage
    hide = "|hide=" + ",".join(map(str, b["shape_hide"]))
    parts = [
        p + hide if os.path.basename(p).lower() == "t0_000_pwa_base__full.glb" else p
        for p in cage.split(";")
    ]
    return ";".join(parts + [g[0]])


_WB = "base\\characters\\base_entities\\woman_base\\"
V_RIGS = (  # V's body rigs; a mod that replaces one changes how her body (and chest) moves
    _WB + "woman_base.rig",
    _WB + "deformations_rigs\\woman_base_deformations.rig",
    _WB + "deformations_rigs\\woman_base_breasts.rig",
)
RIG_NAME = re.compile(
    r"all.?the.?rigs|(breast|boob|body|chest).?(physic|jiggle|bounce|rig)", re.I
)
RIG_PATH = re.compile(r"(physic|jiggle|bounce)", re.I)


def rig_mods(game):
    """Mods in archive\\pc\\mod (subfolders too) that change how V's body moves: they replace one of V's body rigs,
    hold breast / body physics files, or are named like a rig / physics mod. -> sorted relative paths.
    ponytail: by content where the archive says (rig hashes, file names), by its name otherwise; a few seconds for
    hundreds of mods."""
    want = {rdar.fnv(p) for p in V_RIGS}
    md, hits = mod_dir(game), set()
    for dp, _, fs in os.walk(md):
        for f in fs:
            if not f.lower().endswith(".archive"):
                continue
            p = os.path.join(dp, f)
            try:
                r = rdar.Rdar(p)
                names = r.names
                hit = bool(want & set(r.hashes())) or (
                    any(
                        RIG_PATH.search(n)
                        and re.search(r"breast|boob|body|chest", n, re.I)
                        for n in names
                    )
                    if names  # the files say what it is (an NPC physics mod is not one)
                    else bool(RIG_NAME.search(f))
                )
            except Exception:  # a broken archive: only its name can tell
                hit = bool(RIG_NAME.search(f))
            # ragdoll mods replace V's rig only for how she falls, not her shape (reported: CrT_RagdollProfiles)
            if hit and re.search(r"ragdoll", f, re.I) and not RIG_NAME.search(f):
                hit = False
            if hit:
                hits.add(os.path.relpath(p, md))
    return sorted(hits, key=str.lower)


MOVES_HINT = "Tip: tick 'Make clothes move with the body' so tight clothes follow those mods' movements better."
RIG_NOTE = (
    "Note: %s change%s how V's body moves (rig / breast physics). Hyst says such mods make some clothes clip with "
    "Angel and EVE, mostly when V moves."
)


def vanilla_sources(game):
    return [os.path.join(game, "archive", "pc", "content")]


# ---------------- outfit input ----------------


def _work_root():
    d = os.path.join(DATA, "work")
    os.makedirs(d, exist_ok=True)
    return d


def clean_old_work():
    """Remove temporary folders left behind when the tool was closed during a refit (older than 12 hours,
    so a second running copy of the tool keeps its own)."""
    for p in glob.glob(os.path.join(_work_root(), "*_*")):
        try:
            if time.time() - os.path.getmtime(p) > 12 * 3600:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass


def find_7z():
    """7-Zip's 7z.exe: on PATH, in its usual folders, or where its installer says (any folder)."""
    import winreg

    reg = [
        _reg(root, r"SOFTWARE\7-Zip", v)
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
        for v in ("Path64", "Path")
    ]
    for p in (
        [shutil.which("7z")]
        + [os.path.join(r, "7z.exe") for r in reg if r]
        + [
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ]
    ):
        if p and os.path.isfile(p):
            return p
    return None


def find_tar():
    """Windows' own tar.exe (libarchive). Windows 11 builds read .7z and .rar too, so most people need no 7-Zip."""
    p = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
    return p if os.path.isfile(p) else None


NO_7Z = (
    "To open .7z or .rar files, install 7-Zip (free, 7-zip.org), or unpack the download yourself "
    "and add the folder instead."
)


VARIANT_BODY = re.compile(
    r"(^|[^a-z])(ebb|ebbp|ebbrb|ebbprb|ebrb|rb|evb|angel|eve|lush|solo|spawn0|songbird|gymfiend|"
    r"vtk|flat|big ?boobs|small ?boobs|freyja|elegy)([^a-z]|$)",
    re.I,
)


def shown_name(path):
    """The outfit's name as the user sees it: the download's name without .zip / .7z / .rar / .archive."""
    n = os.path.basename(os.path.normpath(path))
    return re.sub(r"\.(zip|7z|rar|archive)$", "", n, flags=re.I)


NEXUS_TAIL = re.compile(
    r"-\d+(?:-[0-9a-z]+)*-\d{9,11}$", re.I
)  # Nexus download: Name-<mod id>-<version>-<time>


def outfit_key(shown):
    """The outfit's name without the Nexus mod id / version / time, so an updated outfit replaces the old refit."""
    return NEXUS_TAIL.sub("", shown or "") or shown


def outfit_name(path):
    n = re.sub(r"\W+", "_", outfit_key(shown_name(path))).strip("_") or "outfit"
    # cut long names, with a short checksum so two downloads with the same start (main + optional file) differ
    return (
        n if len(n) <= 60 else n[:52] + "_" + hashlib.sha1(n.encode()).hexdigest()[:7]
    )


def unpack(path, dest, only=None):
    """zip / 7z / rar -> extracted into dest; a folder is used as it is. only: e.g. '*.archive' to extract just those
    files. -> the folder to look in."""
    if os.path.isdir(path):
        return path
    low = path.lower()
    os.makedirs(dest, exist_ok=True)
    if low.endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            z.extractall(
                dest,
                [
                    n
                    for n in z.namelist()
                    if not only or fnmatch.fnmatch(os.path.basename(n).lower(), only)
                ],
            )
    elif low.endswith((".7z", ".rar")):
        z = find_7z()
        if z:
            run([z, "x", "-y", "-o" + dest, path] + ([only, "-r"] if only else []))
        elif find_tar():
            try:  # tar matches --include against the whole path inside the download
                run(
                    [find_tar(), "-x", "-f", path, "-C", dest]
                    + (["--include", "*" + only] if only else [])
                )
            except RuntimeError as e:
                raise ToolError(
                    "%s\n(Windows could not open it: %s)" % (NO_7Z, str(e)[-300:])
                )
        else:
            raise ToolError(NO_7Z)
    else:
        raise ToolError(
            "%s is not a zip, 7z, rar, folder or .archive." % os.path.basename(path)
        )
    return dest


def pick_variants(arcs, game, log):
    """Archives holding the same meshes are alternatives (colour / FOMOD options); the game loads only one of them.
    -> arcs with one per such group: the one in the game (the first in load order if several), else the first by
    name. Refitting every colour is pointless: their refits replace the same files, so again only one would show."""

    def mesh_ids(a):
        try:
            r = rdar.Rdar(a)
            ids = {rdar.fnv(n) for n in r.names if n.lower().endswith(".mesh")}
            return ids or r.hashes()  # no name list (old packing): every file
        except Exception:
            return set()

    ids = {a: mesh_ids(a) for a in arcs}
    groups = []
    for a in arcs:
        g = next((g for g in groups if any(ids[a] & ids[b] for b in g)), None)
        g.append(a) if g else groups.append([a])
    if all(len(g) == 1 for g in groups):
        return arcs
    have = archives_in_game(game) if game else set()
    name = lambda a: os.path.basename(a).lower()
    keep = set()
    for g in groups:
        inst = [a for a in g if name(a) in have]
        pick = min(
            inst or g, key=name
        )  # archives load by name; the first one loaded wins
        keep.add(pick)
        if len(g) > 1:
            log(
                "This download has %d versions of the same clothes (%s); the game shows only one. Refitting %s, %s."
                % (
                    len(g),
                    ", ".join(sorted(os.path.basename(a) for a in g)),
                    os.path.basename(pick),
                    "the one in your game"
                    if inst
                    else "the first one. For another one, install that one first, or pick its .archive file",
                )
            )
    return [a for a in arcs if a in keep]


def prepare_input(path, dest, log, src="vanilla", game=None):
    """zip/7z/rar/folder/.archive -> list of the .archive files to refit (copied/extracted under dest).
    Downloads with several body versions (Vanilla / EBB / Angel folders with the same files) -> the one for src
    (the body the outfit is made for), else the vanilla one. Warns when the chosen files look made for another body.
    Several colour versions of the same clothes -> one (pick_variants)."""
    os.makedirs(dest, exist_ok=True)
    if not os.path.isdir(path) and path.lower().endswith(".archive"):
        shutil.copy(path, dest)
        variant_warning([shown_name(path)], log, src)
        return [os.path.join(dest, os.path.basename(path))]
    root = unpack(path, dest)
    arcs = sorted(glob.glob(os.path.join(root, "**", "*.archive"), recursive=True))
    if not arcs:
        raise ToolError("No .archive file found in %s." % os.path.basename(path))
    chosen = pick_variants(choose_archives(arcs, root, log, src), game, log)
    # the chosen files' own paths say more than the download's name, so they are looked at first
    variant_warning(
        [os.path.relpath(a, root) for a in chosen] + [shown_name(path)], log, src
    )
    return chosen


REFIT_NAME = re.compile(
    r"^!*([a-z]+)Refit_", re.I
)  # this tool's own output: AngelRefit_x, !!!EveRefit_x


def guess_body(texts):
    """The body an outfit looks made for, from file / folder names: -> (word, body tag) for a body this tool knows,
    (word, None) for another body (EBB, RB, ...), None when the names don't say. Earlier texts win."""
    tags = bodies()["bodies"]
    for t in texts:
        m = REFIT_NAME.match(os.path.basename(t))
        if m and m.group(1).lower() in tags:
            return m.group(1), m.group(1).lower()
    for t in texts:  # the body word that comes first in a name wins ('EVE Body - Full Vanilla Refit' = EVE)
        hits = [
            (m.start(2 if m.re.groups else 0), m, tag)
            for tag in tags
            for m in [hint_rx(tag).search(t)]
            if m
        ]
        if hits:
            _, m, tag = min(hits, key=lambda h: h[0])
            return m.group(2 if m.re.groups else 0), tag
    for t in texts:  # another body; 'Eve' / 'angel' that aren't hints above (e.g. a character's name) don't count
        for m in VARIANT_BODY.finditer(t):
            if m.group(2).lower() not in tags:
                return m.group(2), None
    return None


def variant_warning(texts, log, src="vanilla"):
    """Warn (never block) when the outfit's name or folders say it was made for another body than src."""
    g = guess_body(texts)
    if not g or g[1] == src:
        return
    word, tag = g
    if tag is None and src == "vanilla":
        log(
            "This looks like a version made for another body (%s). Refits work best from the outfit's normal "
            "(vanilla) version — download that one if the outfit has it." % word
        )
    elif tag is None:
        log(
            "This looks like a version made for another body (%s), not for %s. This tool can't refit from %s "
            "yet; use the outfit's vanilla or %s version if it has one."
            % (word, body_name(src), word, body_name(src))
        )
    else:
        log(
            "Warning: this outfit looks made for %s ('%s' in its name), but 'Outfit made for' is set to %s. "
            "If the name is right, set 'Outfit made for' to %s and refit again — otherwise the outfit is reshaped "
            "from the wrong body and won't fit."
            % (body_name(tag), word, body_name(src), body_name(tag))
        )


def choose_archives(arcs, root, log, src="vanilla"):
    """The .archive files to use out of a download's (paths under root, sorted). Several body versions -> the one
    for src, else the vanilla one. Works on names only (the support report uses it on a zip's file list)."""
    arcs = sorted(arcs)
    want = hint_rx(src)
    others = [hint_rx(t) for t in bodies()["bodies"] if t != src]
    other_body = lambda s: VARIANT_BODY.search(s) or any(r.search(s) for r in others)
    label = "vanilla" if src == "vanilla" else body_name(src)
    groups = {}
    for a in arcs:
        groups.setdefault(os.path.basename(a).lower(), []).append(a)
    chosen = []
    for same in groups.values():
        if len(same) == 1:
            chosen.append(same[0])
            continue
        rel = lambda a: os.path.relpath(a, root)
        folder = lambda a: os.path.dirname(
            rel(a)
        )  # same file name: only the folder tells the versions apart
        van = (
            [a for a in same if want.search(folder(a))]
            or [a for a in same if re.search("vanilla", folder(a), re.I)]
            or [a for a in same if not other_body(folder(a))]
            or same
        )
        chosen.append(van[0])
        log(
            "This download has %d versions of %s; using %s."
            % (
                len(same),
                os.path.basename(van[0]),
                os.path.dirname(rel(van[0])) or "the main one",
            )
        )
    # different names per body (Vanilla\a.archive + EBB\a_ebb.archive, or a_vanilla / a_rb side by side)
    rels = {a: os.path.relpath(a, root) for a in chosen}
    if not any(
        want.search(r) for r in rels.values()
    ):  # no version for src: fall back to the vanilla one
        want, label = hint_rx("vanilla"), "vanilla"
        other_body = lambda s: (
            VARIANT_BODY.search(s)
            or any(hint_rx(t).search(s) for t in bodies()["bodies"] if t != "vanilla")
        )
    if any(want.search(r) for r in rels.values()):
        drop = [a for a, r in rels.items() if other_body(r) and not want.search(r)]
        for a in drop:
            log(
                "Skipping %s (a version for another body; the %s one is used)."
                % (rels[a], label)
            )
        chosen = [a for a in chosen if a not in drop]
    return chosen


# ---------------- install / uninstall ----------------

# Held while files are copied into the game. The window takes it before closing, so closing never cuts a copy in half.
INSTALL_LOCK = threading.Lock()
GAME_TOP = (
    "r6",
    "red4ext",
    "bin",
)  # folders of a download that go to the same place in the game folder


def _installed_path():
    return os.path.join(DATA, "installed.json")


def installed():
    try:
        return json.load(open(_installed_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_installed(lst):
    os.makedirs(DATA, exist_ok=True)
    tmp = _installed_path() + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(lst, f, indent=1)
    os.replace(tmp, _installed_path())


def _copy(src, dst):
    """Copy under a temporary name, then rename: a file in the game is either complete or not there."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        shutil.copy2(src, dst + ".part")
        os.replace(dst + ".part", dst)
    except OSError:  # e.g. the game holds the old file open, or the disk is full: leave nothing half-done behind
        if os.path.exists(dst + ".part"):
            os.remove(dst + ".part")
        raise


def _inside(path, folder):
    a, b = (os.path.normcase(os.path.abspath(x)) for x in (path, folder))
    return a != b and os.path.commonpath([a, b]) == b


def _entry(dst, game, kind, outfit):
    return {
        "file": os.path.basename(dst),
        "folder": os.path.dirname(dst),
        "rel": os.path.relpath(dst, game),
        "kind": kind,
        "outfit": outfit,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "stamp": _stamp_of(dst),
    }


def _stamp_of(p):
    """Size and change time of an installed file: uninstall only removes it while it is still the tool's copy."""
    st = os.stat(p)
    return [st.st_size, int(st.st_mtime)]


def _key(e):
    return os.path.normcase(os.path.join(e["folder"], e["file"]))


@contextlib.contextmanager
def _install_errors(log, res):
    """with-block around installing a finished refit: a Windows file error there doesn't lose the refit, it is
    reported in plain words and the outfit still counts as refit (the file is in the output folder)."""
    try:
        yield
    except OSError as e:
        log(
            "The refit is made but could not be installed: %s\nIt is in %s; copy it into "
            "<game>\\archive\\pc\\mod yourself."
            % (explain(e) or e, os.path.dirname(res))
        )


def _refit_body(name):
    """'eve' for !!!EveRefit_x.archive, None for a file that isn't this tool's refit."""
    m = REFIT_NAME.match(os.path.basename(name))
    return m.group(1).lower() if m else None


def xl_beside(archive):
    """The .xl file a refit .archive can come with (refit.xl_extra), whether it is there or not. It gives new mesh
    paths the outfit's materials; without it those meshes crash the game, so the two always travel together."""
    return archive[: -len(".archive")] + ".xl"


def refit_files(archive):
    """A finished refit as the files to hand out: its .xl first (when it has one), then the .archive."""
    x = xl_beside(archive)
    return ([x] if os.path.isfile(x) else []) + [archive]


def install(files, game, log, outfit=""):
    r"""Copy refits into archive\pc\mod and add them to the uninstall list."""
    lst = installed()
    mine = {_key(e) for e in lst}
    now = {
        os.path.normcase(os.path.join(mod_dir(game), os.path.basename(f)))
        for f in files
    }
    # the files of one call are one refit (.xl + .archive): all of them are installed or none. A file of the same name
    # that this tool did not put there is never replaced, so it holds back its partner as well (an .archive without
    # its .xl crashes the game)
    foreign = [
        os.path.basename(f)
        for f in files
        if os.path.exists(os.path.join(mod_dir(game), os.path.basename(f)))
        and os.path.normcase(os.path.join(mod_dir(game), os.path.basename(f)))
        not in mine
        and os.path.normcase(os.path.abspath(f))
        != os.path.normcase(os.path.join(mod_dir(game), os.path.basename(f)))
    ]
    for f in files if foreign else ():
        name = os.path.basename(f)
        log(
            "Not installed: %s already exists in the game and was not made by this tool."
            % name
            if name in foreign
            else "Not installed: %s (it only works together with %s)."
            % (name, ", ".join(foreign))
        )
    if foreign:
        return
    for f in files:
        dst = os.path.join(mod_dir(game), os.path.basename(f))
        k = os.path.normcase(dst)
        same = (
            os.path.normcase(os.path.abspath(f)) == k
        )  # output folder = the game's mod folder itself
        if not same:
            _copy(f, dst)
        lst = [e for e in lst if _key(e) != k] + [_entry(dst, game, "refit", outfit)]
        mine.add(k)
        _save_installed(lst)
        log("Installed into the game: %s" % os.path.basename(f))
        # an older refit of this outfit that replaces the game's files ('!!!', e.g. made for the other body or with
        # 'Replace the outfit's own version') would win over this one, and one for the same body (1.7 named long
        # outfits differently) is the same thing twice: take it out
        old = [
            e
            for e in lst
            if e.get("kind") == "refit"
            and outfit
            and outfit_key(e.get("outfit")) == outfit_key(outfit)
            and _key(e) not in now
            and (
                e["file"].startswith("!")
                or (_refit_body(f) and _refit_body(e["file"]) == _refit_body(f))
            )
        ]
        if old:
            log(
                "Taking out the older refit of this outfit, it would win over the new one:"
            )
            uninstall(old, log, locked=True)
            lst = installed()


def archives_in_game(game):
    r"""Lower-case names of every .archive under archive\pc\mod, subfolders included (Vortex / MO2 installs), and
    under mods\<name>\archives (REDmod installs)."""
    names = set()
    for top in (mod_dir(game), os.path.join(game, "mods")):
        for dp, _, fs in os.walk(top):
            names.update(f.lower() for f in fs if f.lower().endswith(".archive"))
    return names


def original_missing(arcs, game):
    """True when one of the outfit's .archive files is nowhere in the game's mod folder."""
    have = archives_in_game(game)
    return any(os.path.basename(a).lower() not in have for a in arcs)


def original_plan(root, arcs):
    r"""How to install the original outfit: -> [(source file, path relative to the game folder)].
    arcs: the .archive files prepare_input picked (the vanilla version), under root. Files already under
    archive\pc\mod\... keep that path; a loose .archive and the .xl files next to it go to archive\pc\mod;
    r6, red4ext and bin folders beside them keep their path."""
    every = glob.glob(os.path.join(root, "**", "*.archive"), recursive=True)
    picked = {os.path.normcase(a) for a in arcs}
    mixed = {
        os.path.normcase(os.path.dirname(a))
        for a in every
        if os.path.normcase(a) not in picked
    }
    plan, roots = {}, set()
    for a in arcs:
        parts = os.path.relpath(a, root).split(os.sep)
        low = [p.lower() for p in parts]
        i = next(
            (
                k
                for k in range(len(low) - 3)
                if low[k : k + 3] == ["archive", "pc", "mod"]
            ),
            None,
        )
        if i is None:  # loose in the download
            dest, groot = os.path.join("archive", "pc", "mod"), os.path.dirname(a)
        else:
            dest, groot = os.path.join(*parts[i:-1]), os.path.join(root, *parts[:i])
        roots.add(groot)
        d = os.path.dirname(a)
        for f in os.listdir(d):
            p, fl = os.path.join(d, f), f.lower()
            other_body = (  # e.g. outfit_ebb.xl beside outfit_vanilla.xl
                os.path.normcase(d) in mixed
                and VARIANT_BODY.search(f)
                and "vanilla" not in fl
            )
            if os.path.normcase(p) == os.path.normcase(a) or (
                fl.endswith(".xl") and os.path.isfile(p) and not other_body
            ):
                plan[os.path.normcase(os.path.join(dest, f))] = (
                    p,
                    os.path.join(dest, f),
                )
    for g in roots:
        for top in os.listdir(g):
            if top.lower() in GAME_TOP and os.path.isdir(os.path.join(g, top)):
                for dp, _, fs in os.walk(os.path.join(g, top)):
                    for f in fs:
                        rel = os.path.relpath(os.path.join(dp, f), g)
                        plan[os.path.normcase(rel)] = (os.path.join(dp, f), rel)
    return sorted(plan.values(), key=lambda x: x[1].lower())


def install_original(plan, game, log, outfit=""):
    """Copy the original outfit into the game and add it to the uninstall list. Skips archives that are already
    somewhere in the mod folder and never overwrites a different file. -> number of files copied."""
    have = archives_in_game(game)
    lst = installed()
    n = 0
    for src, rel in plan:
        dst = os.path.normpath(os.path.join(game, rel))
        if not _inside(dst, game):
            log("Not copied: %s would land outside the game folder." % rel)
            continue
        if rel.lower().endswith(".archive") and os.path.basename(rel).lower() in have:
            continue
        if os.path.exists(dst):
            if not filecmp.cmp(src, dst, shallow=False):
                log(
                    "Not copied: %s is already in the game and is different. Kept the one in the game."
                    % rel
                )
            continue
        _copy(src, dst)
        lst.append(_entry(dst, game, "original", outfit))
        _save_installed(lst)
        n += 1
    return n


def uninstall(entries, log, locked=False, game=None):
    """Remove the given install-list entries (refits, or originals this tool installed) from the game. A file that
    changed since (e.g. the same mod installed again by a mod manager) is kept. locked: INSTALL_LOCK already held."""
    if not locked:
        with (
            INSTALL_LOCK
        ):  # never at the same time as a refit being installed (both write the list)
            # a refit's .archive and its .xl go together: the .archive alone would crash the game
            swap = lambda k: (
                k[:-8] + ".xl" if k.endswith(".archive") else k[:-3] + ".archive"
            )
            pair = {swap(_key(e)) for e in entries if e.get("kind") == "refit"}
            more = [e for e in installed() if _key(e) in pair and e not in entries]
            return uninstall(entries + more, log, True, game)
    gone = {_key(e) for e in entries}
    keep = []
    for e in installed():
        if _key(e) not in gone:
            keep.append(e)
            continue
        p, name = os.path.join(e["folder"], e["file"]), e.get("rel", e["file"])
        if (
            not os.path.exists(p) and game and e.get("rel")
        ):  # the game folder moved (e.g. another Steam library)
            p = os.path.join(game, e["rel"])
        try:
            if not os.path.exists(p):
                log("Already gone from the game: %s" % name)
            elif e.get("stamp") and _stamp_of(p) != e["stamp"]:
                log(
                    "Kept %s: it changed since this tool installed it (another install?)."
                    % name
                )
            else:
                os.remove(p)
                log("Removed from the game: %s" % name)
        except (
            OSError
        ) as x:  # e.g. the game is running: keep it on the list, go on with the rest
            log("Not removed: %s" % (explain(x) or x))
            keep.append(e)
    _save_installed(keep)


# ---------------- the whole job ----------------


def body_source(game, tag, body_files=None):
    """Where a body's shape is read from: [the game's content folder] for vanilla, else [the body file the user picked
    (an .archive or a download), or the body mod's .archive installed in the game]. None = not found."""
    if bodies()["bodies"][tag].get("game"):
        return vanilla_sources(game)
    p = (body_files or {}).get(tag)
    if p and os.path.exists(p):
        return [p]
    p = find_body_archive(game, tag)
    return [p] if p else None


def setup(
    game, src, tgt, log, progress=lambda f: None, body_files=None, wk_existing=None
):
    """Everything needed before refitting from body src to body tgt: -> (wolvenkit exe, {body tag: glb list}).
    body_files: {body tag: file the user picked}."""
    if not is_game_dir(game):
        raise ToolError(
            "Please choose your Cyberpunk 2077 game folder (the one with the 'bin' and 'archive' folders)."
        )
    if src == tgt:
        raise ToolError(
            "'Outfit made for' and 'Your body mod' are both %s, so there is nothing to refit. Set 'Outfit made for' "
            "to the body the outfit was made for." % body_name(src)
        )
    ensure_oodle(game)
    wk = ensure_wolvenkit(log, progress, wk_existing)
    found = {t: body_source(game, t, body_files) for t in (src, tgt)}
    if not found[tgt]:
        raise ToolError(
            "%s is not installed in the game (archive\\pc\\mod). Install the body mod first (%s), "
            "or pick its download or .archive file with 'Body file...' next to 'Your body mod'. (With Mod "
            "Organizer 2 your mods are not in the game folder, so always pick the body file that way.)"
            % (body_name(tgt), bodies()["bodies"][tgt].get("page", ""))
        )
    if not found[src]:
        raise ToolError(
            "The outfit is made for %s, so the tool needs the %s body shape, but %s is not installed in the game. "
            "Click 'Body file...' next to 'Outfit made for' and pick the %s download (zip / 7z / rar) or its "
            ".archive%s. It is only read, never installed."
            % (
                body_name(src),
                body_name(src),
                body_name(src),
                body_name(src),
                " (%s)" % bodies()["bodies"][src]["page"]
                if bodies()["bodies"][src].get("page")
                else "",
            )
        )
    cages = {}
    for t in (src, tgt):
        if found[t][0].lower().endswith(".archive"):
            check_body_archive(found[t][0])
        cages[t] = ensure_cages(t, found[t], game, wk, log)
    return wk, cages


MISSING_TEXT = "The original outfit '%s' is not in your game yet. Install it too — the refit only works together with it."


def run_batch(
    inputs,
    tag,
    game,
    out_dir,
    log,
    progress=lambda f: None,
    do_install=False,
    body_files=None,
    wk_existing=None,
    cancel=None,
    src="vanilla",
    replace_own=False,
    copy_moves=False,
    make_zip=False,
    chest_room=0.0,
    pasties_shift=0,
):
    """Refit every input from body src (the body the outfits are made for) to body tag. -> list of (input, output
    archive or None, message, original missing).
    original missing: True when the original outfit is not in the game (and was not installed now).
    body_files: {body tag: body file the user picked}.
    cancel: a threading.Event; once set, nothing more is installed and no further outfit is started.
    replace_own: also refit parts the outfit already ships a version for body tag of, replacing that version.
    copy_moves: clothing close to the body gets the body's movement (skin weights), see refit.copy_weights.
    make_zip: also pack each refit into its own zip (archive/pc/mod/...) for Vortex / Mod Organizer 2.
    chest_room: 'Adjust room at the chest' in mm (-2 .. +6, 0 = the normal fit), see refit.refit_mod.
    pasties_shift: 'Move pasties up / down' in mm (-30 .. +30, 0 = the normal place), see refit.refit_mod."""
    import refit

    cancel = cancel or threading.Event()
    _JOB.cancel = cancel
    clean_old_work()
    wk, cages = setup(game, src, tag, log, progress, body_files, wk_existing)
    log("Refitting from %s to %s." % (body_name(src), body_name(tag)))
    if not bodies()["bodies"][tag].get("game"):
        rigs = rig_mods(game)
        if rigs:
            log(RIG_NOTE % (", ".join(rigs), "s" if len(rigs) == 1 else ""))
            if not copy_moves:
                log(MOVES_HINT)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for i, inp in enumerate(inputs):
        if cancel.is_set():
            break
        name = outfit_name(inp)
        shown = shown_name(inp)
        log("")
        log("=== %s ===" % os.path.basename(inp))
        work = tempfile.mkdtemp(prefix="job_", dir=_work_root())
        try:
            arcs = prepare_input(inp, os.path.join(work, "in"), log, src, game)
            left_out = []  # parts that belong to the refit but could not be made (refit_mod's 'incomplete')
            res = refit.refit_mod(
                arcs,
                name,
                work,
                out_dir,
                src,
                tag,
                cages,
                wk,
                game,
                log,
                lambda f: progress((i + f) / len(inputs)),
                # a single .archive is copied: its yaml (garment layers) is beside the original
                [inp]
                if os.path.isfile(inp) and inp.lower().endswith(".archive")
                else arcs,
                replace_own,
                copy_moves,
                chest_room,
                pasties_shift,
                left_out,
            )
            if left_out:
                log(
                    "%s: %s"
                    % (
                        "This refit is INCOMPLETE" if res else "Nothing could be refit",
                        "; ".join(left_out),
                    )
                )
            missing = bool(res) and original_missing(arcs, game)
            if res and make_zip:
                zp = os.path.splitext(res)[0] + ".zip"
                with zipfile.ZipFile(zp + ".part", "w", zipfile.ZIP_DEFLATED) as z:
                    for f in refit_files(res):
                        z.write(f, "archive/pc/mod/" + os.path.basename(f))
                os.replace(zp + ".part", zp)
                log("Zip for Vortex / Mod Organizer 2: %s" % os.path.basename(zp))
            if res and do_install:
                with INSTALL_LOCK, _install_errors(log, res):
                    if cancel.is_set():
                        log("Stopped before installing.")
                        break
                    install(refit_files(res), game, log, shown)
                    if missing:
                        root = inp if os.path.isdir(inp) else os.path.join(work, "in")
                        n = install_original(
                            original_plan(root, arcs), game, log, shown
                        )
                        missing = original_missing(arcs, game)
                        if n and not missing:
                            log(
                                "Original outfit was not installed — installed it now (%d files)"
                                % n
                            )
                        else:
                            log(
                                "The original outfit could not be installed completely (see above). "
                                "Please install it yourself."
                            )
                    else:
                        log("Original outfit already installed — kept as is")
            elif missing:
                log(MISSING_TEXT % shown)
            msg = "ok" if res else "nothing to refit"
            if left_out:
                msg = ("incomplete: " if res else "nothing could be refit: ") + "; ".join(left_out)
            results.append((inp, res, msg, missing))
        except (
            Exception
        ) as e:  # keep going with the next outfit; full detail goes to the log
            if cancel.is_set():  # 'Stop' clicked or the window closed: a killed tool's error is just that
                log("Stopped. Nothing was kept from this outfit.")
                results.append((inp, None, "stopped", False))
                break
            why = explain(
                e
            )  # a ToolError's own text, or plain words for Windows file errors
            log(
                "Could not refit this outfit: %s" % why
                if why
                else "Could not refit this outfit (unexpected error): %r" % e
            )
            results.append((inp, None, why or repr(e), False))
        finally:
            if not os.environ.get("BODYREFIT_KEEP_WORK"):
                shutil.rmtree(work, ignore_errors=True)
        progress((i + 1) / len(inputs))
    return results
