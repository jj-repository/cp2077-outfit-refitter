r"""Everything around the refit itself: settings, finding the game, getting WolvenKit, extracting the body shapes
from the user's own installed files, unpacking outfit downloads, installing/uninstalling refits.
No widgets here: the GUI and the --selftest both call run_batch()."""

import fnmatch, glob, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, zipfile

import rdar

APP_DIR = getattr(
    sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))
)  # PyInstaller unpack dir or source
DATA = os.environ.get("BODYREFIT_HOME") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "BodyRefitter"
)
DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Documents", "Body Refitter")

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


def bodies():
    return json.load(open(os.path.join(APP_DIR, "bodies.json"), encoding="utf-8"))


def body_name(tag):
    return bodies()["bodies"][tag]["name"]


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
    json.dump(s, open(_settings_path(), "w", encoding="utf-8"), indent=1)


# ---------------- game folder ----------------


def is_game_dir(p):
    return bool(p) and os.path.isfile(
        os.path.join(p, "bin", "x64", "Cyberpunk2077.exe")
    )


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
        ):
            cands.append(os.path.join(drive + ":\\", sub, "Cyberpunk 2077"))
    return next((c for c in cands if is_game_dir(c)), None)


def mod_dir(game):
    return os.path.join(game, "archive", "pc", "mod")


# ---------------- running tools quietly ----------------

_FLAGS = (
    0x08000000 | 0x00004000
)  # CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS (keeps a running game smooth)


def run(args, game=None, ok=None):
    """Run a tool hidden at low priority. ok: text that marks success even on a non-zero exit (WolvenKit import)."""
    env = dict(os.environ)
    if game:
        env["CP77_DIR"] = game
    r = subprocess.run(
        [str(a) for a in args],
        capture_output=True,
        text=True,
        errors="replace",
        env=env,
        creationflags=_FLAGS,
        stdin=subprocess.DEVNULL,
    )
    if r.returncode and not (ok and ok in r.stdout):
        raise RuntimeError(
            "%s %s failed:\n%s%s"
            % (
                os.path.basename(str(args[0])),
                args[1],
                r.stdout[-1500:],
                r.stderr[-1500:],
            )
        )
    return r.stdout


# ---------------- WolvenKit ----------------


def wk_exe():
    return os.path.join(DATA, "wolvenkit", "WolvenKit.CLI.exe")


def _wk_ready(exe, log):
    try:
        v = run([exe, "--version"]).strip()
    except (RuntimeError, OSError) as e:
        if "NET" in str(e) or "framework" in str(e).lower():
            raise ToolError(
                "WolvenKit needs the free Microsoft .NET 10 Runtime. Install it from %s "
                "(pick '.NET Runtime 10', x64), then press Refit again." % DOTNET_URL
            )
        raise ToolError("WolvenKit does not start:\n%s" % e)
    if not v.startswith(WK_VERSION):
        log(
            "Note: WolvenKit %s found; this tool is tested with %s."
            % (v.splitlines()[0][:20], WK_VERSION)
        )
    return exe


def ensure_wolvenkit(log, progress=lambda f: None, existing=None):
    """WolvenKit.CLI in our data folder. existing: a WolvenKit folder the user already has (copied, never changed).
    Otherwise the official release zip is downloaded once and checked against its SHA-256."""
    exe = wk_exe()
    if existing:
        src = (
            existing
            if existing.lower().endswith(".exe")
            else os.path.join(existing, "WolvenKit.CLI.exe")
        )
        if not os.path.isfile(src):
            raise ToolError("No WolvenKit.CLI.exe in %s." % existing)
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
                        WK_URL, headers={"User-Agent": "BodyRefitter"}
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
        except OSError as e:
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
    return _wk_ready(exe, log)


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
            for p in cfg["bodies"][tag]["archives"]
        ):
            p = os.path.join(md, f)
            try:
                if want in rdar.Rdar(p).hashes():
                    hits.append(p)
            except (OSError, ValueError):
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


def _stamp(paths):
    """Changes when a source file changes (a folder counts as its .archive files)."""
    files = []
    for p in paths:
        files += (
            sorted(glob.glob(os.path.join(p, "*.archive"))) if os.path.isdir(p) else [p]
        )
    return ";".join(
        "%s|%d|%d" % (os.path.basename(f), os.path.getsize(f), int(os.path.getmtime(f)))
        for f in files
    )


def ensure_cages(key, sources, game, wk, log):
    """Body shape glbs for key ('van' or a body tag) in DATA\\cages\\<key>, extracted with WolvenKit from sources
    (the base game's content folder, or the user's body mod archive). Re-extracted when the source file changes."""
    d = os.path.join(DATA, "cages", key)
    stamp = _stamp(sources)
    glbs = sorted(glob.glob(os.path.join(d, "*.glb")))
    try:
        if len(glbs) == 3 and open(os.path.join(d, "source.txt")).read() == stamp:
            return ";".join(glbs)
    except OSError:
        pass
    log(
        "Reading the %s body shape from your installed files (one time, about a minute) ..."
        % ("vanilla" if key == "van" else body_name(key))
    )
    shutil.rmtree(d, ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="cage_", dir=_work_root())
    try:
        meshes = bodies()["meshes"]
        rx = "^(" + "|".join(re.escape(m) for m in meshes) + ")$"
        for s in sources:
            run([wk, "unbundle", s, "-o", tmp, "-r", rx], game)
        os.makedirs(d)
        for m in meshes:
            mp = os.path.join(tmp, m)
            if not os.path.isfile(mp):
                raise ToolError(
                    "Could not find %s in %s."
                    % (os.path.basename(m), ", ".join(map(os.path.basename, sources)))
                )
            run([wk, "export", mp, "-o", d, "-gp", game], game)
        glbs = sorted(glob.glob(os.path.join(d, "*.glb")))
        if len(glbs) != 3:
            raise ToolError("WolvenKit could not export the body shape.")
        open(os.path.join(d, "source.txt"), "w").write(stamp)
        return ";".join(glbs)
    except Exception:
        shutil.rmtree(d, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def vanilla_sources(game):
    return [os.path.join(game, "archive", "pc", "content")]


# ---------------- outfit input ----------------


def _work_root():
    d = os.path.join(DATA, "work")
    os.makedirs(d, exist_ok=True)
    return d


def find_7z():
    for p in (
        shutil.which("7z"),
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ):
        if p and os.path.isfile(p):
            return p
    return None


VARIANT_BODY = re.compile(
    r"(^|[^a-z])(ebb|ebbp|ebbrb|ebbprb|rb|evb|angel|eve|lush|solo|spawn0|songbird|gymfiend|"
    r"vtk|flat|big ?boobs|small ?boobs|freyja|elegy)([^a-z]|$)",
    re.I,
)


def outfit_name(path):
    n = os.path.basename(os.path.normpath(path))
    n = re.sub(r"\.(zip|7z|rar|archive)$", "", n, flags=re.I)
    return re.sub(r"\W+", "_", n).strip("_")[:60] or "outfit"


def prepare_input(path, dest, log):
    """zip/7z/rar/folder/.archive -> list of the .archive files to refit (copied/extracted under dest).
    Downloads with several body versions (Vanilla / EBB / RB folders with the same files) -> the Vanilla one."""
    os.makedirs(dest, exist_ok=True)
    low = path.lower()
    if os.path.isdir(path):
        root = path
    elif low.endswith(".archive"):
        shutil.copy(path, dest)
        return [os.path.join(dest, os.path.basename(path))]
    elif low.endswith(".zip"):
        zipfile.ZipFile(path).extractall(dest)
        root = dest
    elif low.endswith((".7z", ".rar")):
        z = find_7z()
        if not z:
            raise ToolError(
                "To open .7z or .rar files, install 7-Zip (7-zip.org), or unpack the download yourself "
                "and add the folder instead."
            )
        run([z, "x", "-y", "-o" + dest, path])
        root = dest
    else:
        raise ToolError(
            "%s is not a zip, 7z, rar, folder or .archive." % os.path.basename(path)
        )
    arcs = sorted(glob.glob(os.path.join(root, "**", "*.archive"), recursive=True))
    if not arcs:
        raise ToolError("No .archive file found in %s." % os.path.basename(path))
    groups = {}
    for a in arcs:
        groups.setdefault(os.path.basename(a).lower(), []).append(a)
    chosen = []
    for same in groups.values():
        if len(same) == 1:
            chosen.append(same[0])
            continue
        rel = lambda a: os.path.relpath(a, root)
        van = (
            [a for a in same if re.search("vanilla", rel(a), re.I)]
            or [a for a in same if not VARIANT_BODY.search(rel(a))]
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
    if any(re.search("vanilla", r, re.I) for r in rels.values()):
        drop = [
            a
            for a, r in rels.items()
            if VARIANT_BODY.search(r) and not re.search("vanilla", r, re.I)
        ]
        for a in drop:
            log(
                "Skipping %s (a version for another body; the vanilla one is used)."
                % rels[a]
            )
        chosen = [a for a in chosen if a not in drop]
    return chosen


# ---------------- install / uninstall ----------------


def _installed_path():
    return os.path.join(DATA, "installed.json")


def installed():
    try:
        return json.load(open(_installed_path(), encoding="utf-8"))
    except (OSError, ValueError):
        return []


def install(files, game, log):
    lst = installed()
    mine = {e["file"].lower() for e in lst}
    md = mod_dir(game)
    for f in files:
        dst = os.path.join(md, os.path.basename(f))
        if os.path.exists(dst) and os.path.basename(f).lower() not in mine:
            log(
                "Not installed: %s already exists in the game and was not made by this tool."
                % os.path.basename(f)
            )
            continue
        shutil.copy2(f, dst)
        if os.path.basename(f).lower() not in mine:
            lst.append(
                {
                    "file": os.path.basename(f),
                    "folder": md,
                    "date": time.strftime("%Y-%m-%d %H:%M"),
                }
            )
        log("Installed into the game: %s" % os.path.basename(f))
    json.dump(lst, open(_installed_path(), "w", encoding="utf-8"), indent=1)


def uninstall(names, log):
    lst = installed()
    keep = []
    for e in lst:
        if e["file"] in names:
            p = os.path.join(e["folder"], e["file"])
            if os.path.exists(p):
                os.remove(p)
            log("Removed from the game: %s" % e["file"])
        else:
            keep.append(e)
    json.dump(keep, open(_installed_path(), "w", encoding="utf-8"), indent=1)


# ---------------- the whole job ----------------


def setup(game, tag, log, progress=lambda f: None, body_archive=None, wk_existing=None):
    """Everything needed before refitting: -> (wolvenkit exe, {cage key: glb list})."""
    if not is_game_dir(game):
        raise ToolError(
            "Please choose your Cyberpunk 2077 game folder (the one with the 'bin' and 'archive' folders)."
        )
    ensure_oodle(game)
    wk = ensure_wolvenkit(log, progress, wk_existing)
    body_archive = body_archive or find_body_archive(game, tag)
    if not body_archive:
        raise ToolError(
            "%s is not installed in the game (archive\\pc\\mod). Install the body mod first (%s), "
            "or pick its .archive file with 'Body file...'."
            % (body_name(tag), bodies()["bodies"][tag]["page"])
        )
    check_body_archive(body_archive)
    cages = {
        "van": ensure_cages("van", vanilla_sources(game), game, wk, log),
        tag: ensure_cages(tag, [body_archive], game, wk, log),
    }
    return wk, cages


def run_batch(
    inputs,
    tag,
    game,
    out_dir,
    log,
    progress=lambda f: None,
    do_install=False,
    body_archive=None,
    wk_existing=None,
):
    """Refit every input for body tag. -> list of (input, output archive or None, message)."""
    import refit

    wk, cages = setup(game, tag, log, progress, body_archive, wk_existing)
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for i, inp in enumerate(inputs):
        name = outfit_name(inp)
        log("")
        log("=== %s ===" % os.path.basename(inp))
        work = tempfile.mkdtemp(prefix="job_", dir=_work_root())
        try:
            arcs = prepare_input(inp, os.path.join(work, "in"), log)
            res = refit.refit_mod(
                arcs,
                name,
                work,
                out_dir,
                tag,
                cages,
                wk,
                game,
                log,
                lambda f: progress((i + f) / len(inputs)),
            )
            if res and do_install:
                install([res], game, log)
            results.append((inp, res, "ok" if res else "nothing to refit"))
        except ToolError as e:
            log("Could not refit this outfit: %s" % e)
            results.append((inp, None, str(e)))
        except (
            Exception
        ) as e:  # keep going with the next outfit; full detail goes to the log
            log("Could not refit this outfit (unexpected error): %r" % e)
            results.append((inp, None, repr(e)))
        finally:
            if not os.environ.get("BODYREFIT_KEEP_WORK"):
                shutil.rmtree(work, ignore_errors=True)
        progress((i + 1) / len(inputs))
    return results
