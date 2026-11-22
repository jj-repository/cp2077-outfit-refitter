r"""Support report: the window records what happens (in memory, always on), and 'Support report...' writes one plain
text file with that record, the settings and a check of the game folder. No file contents, and the Windows user
name / profile path is replaced everywhere. Nothing here may crash the window: every check that fails writes its
reason into the report instead."""

import collections, fnmatch, os, platform, re, subprocess, sys, threading, time, traceback, zipfile

import core

MAX_ENTRIES = 5000  # per session; older entries are dropped
LAST, PREVIOUS = "last_session.txt", "previous_session.txt"


class Session:
    """What happened in this window: actions, log lines, errors, message boxes. Thread-safe (a lock), although the
    window only records from the Tk thread (the worker's lines arrive through the queue)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.entries = collections.deque(maxlen=MAX_ENTRIES)
        self.started = time.strftime("%Y-%m-%d %H:%M:%S")
        self.actions = 0

    def add(self, kind, text):
        with self.lock:
            self.entries.append((time.strftime("%H:%M:%S"), kind, str(text)[:8000]))
            self.actions += kind == "action"

    def text(self, kinds=None):
        with self.lock:
            entries = list(self.entries)
        out = []
        for t, kind, text in entries:
            if kinds and kind not in kinds:
                continue
            lines = text.splitlines() or [""]
            out.append("%s [%s] %s" % (t, kind, lines[0]))
            out += ["           " + l for l in lines[1:]]
        return "\n".join(out)

    def save(self):
        """Write this session to the data folder (after each refit and when the window closes)."""
        try:
            os.makedirs(core.DATA, exist_ok=True)
            p = os.path.join(core.DATA, LAST)
            with open(p + ".part", "w", encoding="utf-8") as f:
                f.write("Session started %s\n%s\n" % (self.started, self.text()))
            os.replace(p + ".part", p)
        except OSError:
            pass


def rotate_sessions():
    """New window: the session file of the last run becomes the previous one (only current + previous are kept)."""
    try:
        p = os.path.join(core.DATA, LAST)
        if os.path.isfile(p):
            os.replace(p, os.path.join(core.DATA, PREVIOUS))
    except OSError:
        pass


# ---------------- privacy ----------------


def _user_paths():
    """The profile folder in every spelling it can show up in (long, 8.3 short, either slash)."""
    paths = {os.environ.get("USERPROFILE") or "", os.path.expanduser("~")}
    for p in list(paths):
        try:
            import ctypes

            b = ctypes.create_unicode_buffer(1024)
            if p and ctypes.windll.kernel32.GetShortPathNameW(p, b, 1024):
                paths.add(b.value)
        except Exception:
            pass
    return sorted(
        (p.rstrip("\\/") for p in paths if len(p.rstrip("\\/")) > 3),
        key=len,
        reverse=True,
    )


def scrub(text):
    r"""C:\Users\<name>\... -> %USERPROFILE%\..., and the user name itself -> <user>."""
    for p in _user_paths():
        rx = r"[\\/]".join(re.escape(x) for x in re.split(r"[\\/]", p))
        text = re.sub(rx, "%USERPROFILE%", text, flags=re.I)
    names = {os.environ.get("USERNAME") or ""}
    names |= {os.path.basename(p) for p in _user_paths()}
    for n in sorted((n for n in names if n), key=len, reverse=True):
        # ponytail: a 1-2 letter name is only replaced as a whole word, or every 'a' in the report would go
        rx = (
            re.escape(n)
            if len(n) >= 3
            else r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(n)
        )
        text = re.sub(rx, "<user>", text, flags=re.I)
    return text


# ---------------- game folder check ----------------


def file_version(path):
    """'1.28.8.0' from a DLL/EXE's version resource, or None."""
    try:
        import ctypes
        from ctypes import wintypes

        v = ctypes.windll.version
        n = v.GetFileVersionInfoSizeW(path, None)
        if not n:
            return None
        buf = ctypes.create_string_buffer(n)
        if not v.GetFileVersionInfoW(path, 0, n, buf):
            return None
        p, ln = ctypes.c_void_p(), wintypes.UINT()
        if (
            not v.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(ln))
            or ln.value < 52
        ):
            return None
        ms, ls = (ctypes.c_uint32 * 4).from_address(p.value)[2:4]
        return "%d.%d.%d" % (
            ms >> 16,
            ms & 0xFFFF,
            ls >> 16,
        )  # the 4th part is often a cut-off build date
    except Exception:
        return None


def _have(game, rel):
    p = os.path.join(game, rel)
    if not os.path.isfile(p):
        return "MISSING"
    v = file_version(p)
    return "present" + (" (version %s)" % v if v else "")


FRAMEWORKS = [
    ("ArchiveXL", r"red4ext\plugins\ArchiveXL\ArchiveXL.dll"),
    ("TweakXL", r"red4ext\plugins\TweakXL\TweakXL.dll"),
    ("Codeware", r"red4ext\plugins\Codeware\Codeware.dll"),
]


def input_files(path):
    """(root, [file paths under root]) of an outfit download, without unpacking it."""
    low = path.lower()
    if os.path.isdir(path):
        return path, [os.path.join(dp, f) for dp, _, fs in os.walk(path) for f in fs]
    if low.endswith(".archive"):
        return os.path.dirname(path), [path]
    root = "\\download"  # the names inside a zip / 7z are placed under a made-up root (never touched on disk)
    if low.endswith(".zip"):
        names = [i.filename for i in zipfile.ZipFile(path).infolist() if not i.is_dir()]
    elif low.endswith((".7z", ".rar")):
        z = core.find_7z()
        if not z:
            raise core.ToolError("7-Zip is not installed, cannot look inside")
        out = core.run([z, "l", "-slt", "-ba", path])
        names, cur = [], {}
        for line in out.splitlines() + [""]:
            if not line.strip():
                if cur.get("Path") and "D" not in cur.get("Attributes", ""):
                    names.append(cur["Path"])
                cur = {}
            elif " = " in line:
                k, _, v = line.partition(" = ")
                cur[k.strip()] = v
    else:
        raise core.ToolError("not a zip, 7z, rar, folder or .archive")
    return root, [os.path.join(root, os.path.normpath(n)) for n in names]


def outfit_parts(path):
    """-> (original .archive names, .xl names, r6\\tweaks paths relative to the game) the download would install."""
    root, files = input_files(path)
    arcs = core.choose_archives(
        [f for f in files if f.lower().endswith(".archive")], root, lambda m: None
    )
    dirs = {os.path.normcase(os.path.dirname(a)) for a in arcs}
    xls = sorted(
        os.path.basename(f)
        for f in files
        if f.lower().endswith(".xl") and os.path.normcase(os.path.dirname(f)) in dirs
    )
    if any(
        "vanilla" in x.lower() for x in xls
    ):  # e.g. a_vanilla.xl beside a_ebb.xl: only the vanilla one counts
        xls = [
            x for x in xls if "vanilla" in x.lower() or not core.VARIANT_BODY.search(x)
        ]
    tweaks = []
    for f in files:
        parts = os.path.relpath(f, root).split(os.sep)
        low = [x.lower() for x in parts]
        i = next(
            (k for k in range(len(low) - 2) if low[k : k + 2] == ["r6", "tweaks"]), None
        )
        if i is not None and low[-1].endswith((".yaml", ".yml")):
            tweaks.append(os.path.join(*parts[i:]))
    return [os.path.basename(a) for a in arcs], xls, sorted(tweaks)


def mod_files(game):
    r"""{lower-case file name: [paths relative to archive\pc\mod]} for every file under archive\pc\mod."""
    md, found = core.mod_dir(game), {}
    for dp, _, fs in os.walk(md):
        for f in fs:
            found.setdefault(f.lower(), []).append(
                os.path.relpath(os.path.join(dp, f), md)
            )
    return found


def _where(found, name):
    hits = found.get(name.lower())
    return ("present: " + ", ".join(hits)) if hits else "MISSING"


def check_game(game, out_dir, outfits, refits, lines):
    """Append the game folder check to lines. outfits: inputs in the list; refits: [(input, output archive)]."""
    if not core.is_game_dir(game):
        lines.append(
            "Game found: NO (no bin\\x64\\Cyberpunk2077.exe in %s)"
            % (game or "(no folder set)")
        )
        return
    v = file_version(os.path.join(game, "bin", "x64", "Cyberpunk2077.exe"))
    lines.append(
        "Game found: yes (Cyberpunk2077.exe%s)" % (", version %s" % v if v else "")
    )
    loader = [
        x
        for x in ("winmm.dll", "version.dll")
        if os.path.isfile(os.path.join(game, "bin", "x64", x))
    ]
    lines.append(
        "RED4ext: red4ext\\RED4ext.dll %s, loader bin\\x64\\%s"
        % (
            _have(game, r"red4ext\RED4ext.dll"),
            " + ".join(loader) if loader else "winmm.dll / version.dll MISSING",
        )
    )
    for name, rel in FRAMEWORKS:
        lines.append("%s: %s" % (name, _have(game, rel)))
    found = mod_files(game)
    pats = [
        (t, p)
        for t, b in core.bodies()["bodies"].items()
        for p in b.get("archives", [])
    ]
    bodies = sorted(
        {
            "%s (%s)" % (r, core.body_name(t))
            for n, rels in found.items()
            for t, p in pats
            if fnmatch.fnmatch(n, p.lower())
            for r in rels
        }
    )
    lines.append(
        "Body mods found in archive\\pc\\mod: %s"
        % ("; ".join(bodies) if bodies else "none")
    )
    dup = {n: r for n, r in found.items() if n.endswith(".archive") and len(r) > 1}
    lines.append(
        "Same .archive name in more than one folder: %s"
        % ("none" if not dup else "yes, see below")
    )
    for n in sorted(dup):
        lines.append("  DUPLICATE: " + ", ".join(dup[n]))
    done = {os.path.normcase(i): o for i, o in refits}
    for inp in list(dict.fromkeys(list(outfits) + [i for i, _ in refits])):
        lines.append("")
        key = os.path.normcase(inp)
        lines.append(
            "Outfit: %s%s"
            % (
                inp,
                ""
                if key not in done
                else "  (refit this session: %s)" % os.path.basename(done[key])
                if done[key]
                else "  (refit tried this session, no file made)",
            )
        )
        try:
            arcs, xls, tweaks = outfit_parts(inp)
            for a in arcs:
                lines.append("  original %s: %s" % (a, _where(found, a)))
            for x in xls:
                lines.append("  .xl %s: %s" % (x, _where(found, x)))
            for t in tweaks:
                exact = os.path.isfile(os.path.join(game, t))
                lines.append("  tweak %s: %s" % (t, "present" if exact else "MISSING"))
            if not (xls or tweaks):
                lines.append("  (no .xl or r6\\tweaks files in this download)")
        except Exception as e:
            lines.append("  could not look inside the download: %s" % (e,))
        out = done.get(os.path.normcase(inp))
        name = core.outfit_name(inp)
        names = (
            [os.path.basename(out)]
            if out
            else [
                pre + t.capitalize() + "Refit_" + name + ".archive"
                for t in core.bodies()["bodies"]
                for pre in ("", "!!!")
            ]
        )
        hits = [n for n in names if n.lower() in found]
        if hits:
            lines.append(
                "  refit in the game: %s"
                % "; ".join(", ".join(found[n.lower()]) for n in hits)
            )
        else:
            lines.append(
                "  refit in the game: MISSING (%s)"
                % (names[0] if out else "none found")
            )
        mine = [n for n in names if os.path.isfile(os.path.join(out_dir, n))]
        lines.append("  refit in the output folder: %s" % ("; ".join(mine) or "none"))


# ---------------- the report ----------------


def build(session, settings, game, out, outfits, refits):
    """The report text (already scrubbed). Every part that fails says so and the rest still comes."""
    L = []

    def part(title, fn):
        L.append("")
        L.append(title)
        L.append("-" * len(title))
        try:
            fn()
        except Exception as e:
            L.append(
                "could not check this: %s"
                % "".join(traceback.format_exception_only(type(e), e)).strip()
            )

    L.append("Outfit Refitter support report")
    L.append(
        "Send this file to the mod author. It holds no file contents; your Windows user name is replaced."
    )

    def system():
        L.append("Tool version: %s" % core.VERSION)
        L.append("Windows: %s" % platform.platform())
        L.append(
            "Python: %s, %s"
            % (
                platform.python_version(),
                "exe build" if getattr(sys, "frozen", False) else "from source",
            )
        )
        L.append("Report made: %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
        L.append("Data folder: %s" % core.DATA)

    def setting():
        known = core.bodies()["bodies"]
        for label, key in (("Outfit made for", "from"), ("Body", "body")):
            tag = settings.get(key)
            if tag:
                L.append(
                    "%s: %s" % (label, core.body_name(tag) if tag in known else tag)
                )
        for t, p in sorted(settings.get("body_archive", {}).items()):
            L.append(
                "Body file picked for %s: %s (%s)"
                % (t, p, "exists" if os.path.exists(p) else "MISSING")
            )
        L.append(
            "Install into the game: %s"
            % ("ticked" if settings.get("install") else "not ticked")
        )
        L.append("Output folder: %s" % out)
        L.append("Game folder: %s" % game)
        wk = core.wk_exe()
        L.append(
            "WolvenKit: %s"
            % (
                "%s (version %s)" % (wk, file_version(wk) or "unknown")
                if os.path.isfile(wk)
                else "not downloaded yet"
            )
        )
        if settings.get("wk_existing"):
            L.append(
                "WolvenKit to copy on the next refit: %s" % settings["wk_existing"]
            )

    def this():
        L.append("Started: %s" % session.started)
        L.append("Errors and message boxes:")
        L.append(session.text({"error", "message"}) or "  none")
        L.append("")
        L.append("Everything, in order:")
        L.append(session.text())

    def previous():
        p = os.path.join(core.DATA, PREVIOUS)
        if not os.path.isfile(p):
            L.append("none recorded")
            return
        L.extend(
            open(p, encoding="utf-8", errors="replace")
            .read()
            .splitlines()[-MAX_ENTRIES:]
        )

    def install_list():
        lst = core.installed()
        if not lst:
            L.append("nothing")
        for e in lst:
            L.append(
                (
                    "%s: %s  (%s, %s) %s"
                    % (
                        "Original outfit" if e.get("kind") == "original" else "Refit",
                        os.path.join(e.get("folder", ""), e.get("file", "")),
                        e.get("outfit", ""),
                        e.get("date", ""),
                        ""
                        if os.path.isfile(
                            os.path.join(e.get("folder", ""), e.get("file", ""))
                        )
                        else "-- file is GONE",
                    )
                ).rstrip()
            )

    part("1. Tool and system", system)
    part("2. Settings", setting)
    part("3. This session", this)
    part("3b. Previous session", previous)
    part("4. Game folder check", lambda: check_game(game, out, outfits, refits, L))
    part("5. What this tool installed", install_list)
    return scrub("\n".join(L) + "\n")


def write(folder, text):
    """Save the report in folder (the data folder if that fails). -> path."""
    name = "OutfitRefitter_support_%s.txt" % time.strftime("%Y-%m-%d_%H%M%S")
    for d in (folder, core.DATA):
        try:
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, name)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            return p
        except OSError:
            continue
    raise OSError("could not write the report to %s or %s" % (folder, core.DATA))


def show_in_explorer(path):
    """Open the file's folder with the file selected."""
    subprocess.Popen('explorer /select,"%s"' % path)
