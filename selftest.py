r"""Headless self-test: game detection, WolvenKit download/check, body-shape extraction from the installed body mods,
then refit real outfit archives, run the safety check and list the output. Optionally compares the vertex positions
with refits you trust (e.g. ones made by hand).

  outfit_refitter.py --selftest [--home DIR] [--out DIR] [--game DIR] [--wolvenkit DIR]
                   [--body-archive TAG=FILE ...] [--case TAG=OUTFIT[=REFERENCE.archive] ...] [--gui]

--home: data folder to use instead of %LOCALAPPDATA%\OutfitRefitter (fresh folder = real first run).
--gui: also open the window for two seconds and close it (proves it starts).
--gui-drop TAG=OUTFIT[=TEXT;TEXT]: open the real window, drop OUTFIT on it like Explorer does, press Refit, check the
    result (+ safety check; each TEXT must show up in the window log).
--gui-install DIR --gui-install-outfit OUTFIT=>REL;REL ...: 'Install into the game' tests in the real window on a fake
    game folder made in DIR (original missing / already there / Install unticked / Uninstall / closing the window).
    REL: the original outfit's files expected in the game, relative to the game folder. --no-wait: don't wait for
    the refit stopped by closing the window (the process exits while it ends).
--support DIR --support-outfit OUTFIT (3x: two zips with r6\tweaks, then one more): 'Support report...' tests in the
    real window on two fake games made in DIR (with / without ArchiveXL etc.), incl. a restart (previous session).
Exit code 0 = everything passed."""

import glob, os, shutil, sys, tempfile, traceback


def opt_all(a, k):
    return [a[i + 1] for i, x in enumerate(a) if x == k and i + 1 < len(a)]


def opt(a, k, d=None):
    v = opt_all(a, k)
    return v[-1] if v else d


def positions(wk, game, arch, tmp):
    """{mesh name: [positions per primitive]} of every mesh in an archive (WolvenKit export)."""
    import refit
    from core import run

    src, glbd = os.path.join(tmp, "src"), os.path.join(tmp, "glb")
    run([wk, "unbundle", arch, "-o", src], game)
    out = {}
    for m in glob.glob(os.path.join(src, "**", "*.mesh"), recursive=True):
        rel = os.path.relpath(m, src).lower()
        d = os.path.join(glbd, rel)
        os.makedirs(d, exist_ok=True)
        try:
            run([wk, "export", m, "-o", d, "-gp", game], game)
        except RuntimeError:
            continue
        g = glob.glob(os.path.join(d, "*.glb"))[0]
        dd, j, b = refit.load(g)
        out[rel] = [
            refit.read(dd, j, b, pr["attributes"]["POSITION"])
            for mm in j["meshes"]
            for pr in mm["primitives"]
        ]
    return out


def compare(wk, game, new, ref, work, log):
    """Max/mean vertex distance (mm) per mesh between our output and a reference refit. Meshes are matched by name;
    a static override (same path) and a switcher-style _<body> sibling of the same stem are treated as the same mesh."""
    import numpy as np

    a = positions(wk, game, new, os.path.join(work, "cmp_new"))
    b = positions(wk, game, ref, os.path.join(work, "cmp_ref"))
    norm = lambda n: n.replace("_eve.mesh", ".mesh").replace("_angel.mesh", ".mesh")
    bn = {norm(k): v for k, v in b.items()}
    worst = None
    for k, pa in sorted(a.items()):
        pb = b.get(k) or bn.get(norm(k))
        if pb is None:
            log("    %-60s no match in reference" % os.path.basename(k))
            continue
        if [len(x) for x in pa] != [len(x) for x in pb]:
            log(
                "    %-60s different vertex layout, cannot compare"
                % os.path.basename(k)
            )
            continue
        d = np.linalg.norm(np.vstack(pa) - np.vstack(pb), axis=1) * 1000
        worst = max(worst or 0.0, float(d.max()))
        log(
            "    %-60s max %.3f mm  mean %.4f mm  (%d verts)"
            % (os.path.basename(k), d.max(), d.mean(), len(d))
        )
    return worst


def check_variants(core):
    """Downloads with one folder/file per body must give only the vanilla archive."""
    t = tempfile.mkdtemp(prefix="bodyrefit_variants_")
    try:
        for p in (
            r"Vanilla\a.archive",
            r"EBB\a.archive",
            r"RB\a_rb.archive",
            r"Vanilla\b.archive",
            r"x\c_vanilla.archive",
            r"x\c_ebb.archive",
            r"shared.archive",
            r"EBB\e-VANILLA.archive",
            r"Vanilla\e-VANILLA.archive",
        ):
            os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True)
            open(os.path.join(t, p), "wb").close()
        got = sorted(
            os.path.relpath(x, t) for x in core.prepare_input(t, t, lambda m: None)
        )
        want = sorted(
            [
                r"Vanilla\a.archive",
                r"Vanilla\b.archive",
                r"x\c_vanilla.archive",
                r"shared.archive",
                r"Vanilla\e-VANILLA.archive",
            ]
        )
        assert got == want, got
        msgs = []
        core.prepare_input(t, t, msgs.append)
        assert not any("made for another body" in m for m in msgs), (
            msgs
        )  # had a vanilla version: no warning
        for name, warn in (
            ("Quiet Outfit_Hyst EBRB Refit-5194.zip", "(EBRB)"),
            ("Dress VTK Small.archive", "(VTK)"),
            ("Quiet Outfit and Hair-5194-1-0.zip", None),
        ):
            msgs = []
            core.variant_warning([name, r"archive\pc\mod\x.archive"], msgs.append)
            assert (warn in msgs[0]) if warn else not msgs, (name, msgs)
    finally:
        shutil.rmtree(t, ignore_errors=True)


def check_recover_names(rdar, refit):
    """Archives without a name list: <hash>.bin files get their names back from paths mentioned in other files,
    and a name pointing outside the folder is never used."""
    t = tempfile.mkdtemp(prefix="bodyrefit_names_")
    try:
        src = os.path.join(t, "src")
        os.makedirs(os.path.join(t, "archive", "pc", "mod"))
        os.makedirs(os.path.join(t, "r6", "tweaks"))
        os.makedirs(src)
        arc = os.path.join(t, "archive", "pc", "mod", "a.archive")
        for n, body in (
            (r"my\shirt.mesh", b"x"),
            (r"my\shirt.ent", b"CR2W..*my/shirt.mesh.."),
            (r"my\shirt.app", b"x"),
            (r"..\..\evil.mesh", b"x"),
            (r"my\unknown.xbm", b"x"),
        ):
            open(os.path.join(src, "%d.bin" % rdar.fnv(n)), "wb").write(body)
        with open(os.path.join(t, "r6", "tweaks", "a.yaml"), "w") as f:
            f.write("entityName: my\\shirt.ent\n")
        with open(arc[:-8] + ".xl", "w") as f:
            f.write("x: my/shirt.app ..\\..\\evil.mesh\n")
        found, left = refit.recover_names(src, [arc])
        assert (found, left) == (3, 2), (found, left)
        for n in (r"my\shirt.mesh", r"my\shirt.ent", r"my\shirt.app"):
            assert os.path.isfile(os.path.join(src, n)), n
        assert not os.path.exists(os.path.join(t, "evil.mesh"))
    finally:
        shutil.rmtree(t, ignore_errors=True)


def check_original_plan(core):
    """Which files of a download get installed where, and install/uninstall on a throw-away game folder."""
    t = tempfile.mkdtemp(prefix="outfitrefit_plan_")
    try:
        mk = lambda p, data=b"x": (
            os.makedirs(os.path.dirname(os.path.join(t, p)), exist_ok=True),
            open(os.path.join(t, p), "wb").write(data),
        )
        cases = {
            "loose": (
                [r"Outfit\a.archive", r"Outfit\a.xl", r"Outfit\readme.txt"],
                [r"archive\pc\mod\a.archive", r"archive\pc\mod\a.xl"],
            ),
            "tree": (
                [
                    r"archive\pc\mod\b.archive",
                    r"archive\pc\mod\b.archive.xl",
                    r"r6\tweaks\b.yaml",
                    r"bin\x64\plugins\b.txt",
                    r"red4ext\plugins\b\b.dll",
                    r"pics\b.png",
                ],
                [
                    r"archive\pc\mod\b.archive",
                    r"archive\pc\mod\b.archive.xl",
                    r"bin\x64\plugins\b.txt",
                    r"r6\tweaks\b.yaml",
                    r"red4ext\plugins\b\b.dll",
                ],
            ),
            "variants": (
                [
                    r"Vanilla\archive\pc\mod\Sub\c.archive",
                    r"Vanilla\archive\pc\mod\Sub\c.xl",
                    r"EBB\archive\pc\mod\Sub\c.archive",
                    r"EBB\archive\pc\mod\Sub\c.xl",
                ],
                [r"archive\pc\mod\Sub\c.archive", r"archive\pc\mod\Sub\c.xl"],
            ),
            "side by side": (
                [
                    r"x\d_vanilla.archive",
                    r"x\d_ebb.archive",
                    r"x\d_vanilla.xl",
                    r"x\d_ebb.xl",
                ],
                [r"archive\pc\mod\d_vanilla.archive", r"archive\pc\mod\d_vanilla.xl"],
            ),
        }
        for name, (files, want) in cases.items():
            root = os.path.join(t, name)
            for f in files:
                mk(os.path.join(name, f))
            arcs = core.prepare_input(root, root, lambda m: None)
            got = [rel for _, rel in core.original_plan(root, arcs)]
            assert got == want, (name, got)
        # install into a fake game: skips an archive that is in a subfolder, keeps a different file, stays inside
        game = os.path.join(t, "game")
        mk(r"game\archive\pc\mod\Vortex\b.archive", b"mine")
        mk(r"game\r6\tweaks\b.yaml", b"different")
        mk(r"game\archive\pc\mod\other.archive", b"other")
        root = os.path.join(t, "tree")
        msgs = []
        plan = core.original_plan(
            root, [os.path.join(root, r"archive\pc\mod\b.archive")]
        )
        plan.append((os.path.join(root, r"pics\b.png"), r"..\escape.png"))
        saved, core.DATA = core.DATA, os.path.join(t, "data")
        try:
            n = core.install_original(plan, game, msgs.append, "tree")
            assert n == 3, (n, msgs)  # b.archive.xl, b.txt, b.dll
            assert (
                open(os.path.join(game, r"r6\tweaks\b.yaml"), "rb").read()
                == b"different"
            )
            assert not os.path.exists(os.path.join(t, "escape.png"))
            assert any("outside the game folder" in m for m in msgs), msgs
            assert any("is different" in m for m in msgs), msgs
            assert not os.path.exists(os.path.join(game, r"archive\pc\mod\b.archive"))
            lst = core.installed()
            assert len(lst) == 3 and all(e["kind"] == "original" for e in lst), lst
            core.uninstall(lst, msgs.append)
            assert core.installed() == []
            left = sorted(
                os.path.relpath(os.path.join(dp, f), game)
                for dp, _, fs in os.walk(game)
                for f in fs
            )
            assert left == [
                r"archive\pc\mod\Vortex\b.archive",
                r"archive\pc\mod\other.archive",
                r"r6\tweaks\b.yaml",
            ], left
        finally:
            core.DATA = saved
    finally:
        shutil.rmtree(t, ignore_errors=True)


def snap(game):
    """{relative path: (size, mtime)} of every file in a (fake) game folder."""
    out = {}
    for dp, _, fs in os.walk(game):
        for f in fs:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, game).lower()] = (
                os.path.getsize(p),
                os.stat(p).st_mtime_ns,
            )
    return out


def make_fake_game(base, real_game):
    r"""A game folder the tool accepts (bin\x64\Cyberpunk2077.exe), with the game's Oodle DLL copied in (read only
    from the real game) and a few unrelated mod files that must survive everything."""
    g = os.path.join(base, "Cyberpunk 2077")
    for d in (r"bin\x64", r"archive\pc\content", r"archive\pc\mod", r"r6\tweaks"):
        os.makedirs(os.path.join(g, d), exist_ok=True)
    open(os.path.join(g, r"bin\x64\Cyberpunk2077.exe"), "wb").write(b"stub")
    shutil.copy2(
        os.path.join(real_game, r"bin\x64\oo2ext_7_win64.dll"),
        os.path.join(g, r"bin\x64"),
    )
    for rel in (
        r"archive\pc\mod\Fremder Mod ä.archive",
        r"archive\pc\mod\fremd.xl",
        r"r6\tweaks\fremd.yaml",
    ):
        open(os.path.join(g, rel), "wb").write(b"unrelated " + rel.encode("utf-8"))
    return g


def gui_install_tests(core, gui, a, log, fails):
    r"""--gui-install DIR: Install-into-the-game tests in the real window, on a fake game folder made in DIR.
    Outfits: --gui-install-outfit PATH=>rel1;rel2 (the original's files expected in the game, relative to it)."""
    import json

    base = os.path.abspath(opt(a, "--gui-install"))
    real = opt(a, "--game") or core.find_game()
    body = dict(x.split("=", 1) for x in opt_all(a, "--body-archive")).get(
        "angel"
    ) or core.find_body_archive(real, "angel")
    game = make_fake_game(base, real)
    van = os.path.join(core.DATA, "cages", "van")
    if os.path.isdir(
        van
    ):  # the fake game has no base-game archives: keep the vanilla shape read from the real game
        open(os.path.join(van, "source.txt"), "w").write(
            core._stamp(core.vanilla_sources(game))
        )
    out = os.path.join(base, "Ausgabe Ordner ü")
    log("\n### Install tests in the window, fake game: %s" % game)
    core.uninstall(core.installed(), log)  # start from an empty install list

    def check(name, cond, detail=""):
        log(
            "  %s %s%s"
            % (
                "OK  " if cond else "FAIL",
                name,
                (" -- %s" % detail) if detail and not cond else "",
            )
        )
        if not cond:
            fails.append(name)
        return cond

    def new_files(before):
        return sorted(set(snap(game)) - set(before))

    def refit_rel(lines):
        x = [
            m.split(": ", 1)[1]
            for m in lines
            if m.startswith("Installed into the game: ")
        ]
        return os.path.join("archive", "pc", "mod", x[0]).lower() if x else None

    last = None
    for spec in opt_all(a, "--gui-install-outfit"):
        inp, _, want = spec.partition("=>")
        want = sorted(w.lower() for w in want.split(";") if w)
        name = core.shown_name(inp)
        last = (inp, want)
        log("\n## %s" % name)
        s0 = snap(game)
        # 3. Install unticked, original missing: nothing goes into the game, warning in the log and the summary
        st = gui.drive(inp, "angel", out, log, game=game, install=False, body_file=body)
        warn = core.MISSING_TEXT % name
        check("unticked: refit finished", st["ok"])
        check("unticked: game folder unchanged", snap(game) == s0, new_files(s0))
        check(
            "unticked: warning in log and in summary",
            sum(m == warn for m in st["log"]) == 2,
            [m for m in st["log"] if "original" in m.lower()],
        )
        # 1. Install ticked, original missing: refit and original installed at the right paths, both in the list
        st = gui.drive(inp, "angel", out, log, game=game, install=True, body_file=body)
        check("ticked: refit finished", st["ok"])
        rr = refit_rel(st["log"])
        check(
            "ticked: game got exactly the refit + the original",
            new_files(s0) == sorted(want + [rr]),
            "%s vs %s" % (new_files(s0), want + [rr]),
        )
        lst = core.installed()
        kinds = sorted((e["rel"].lower(), e["kind"]) for e in lst)
        check(
            "ticked: install list = refit + originals",
            kinds == sorted([(rr, "refit")] + [(w, "original") for w in want]),
            kinds,
        )
        check(
            "ticked: log says original installed",
            "Original outfit was not installed — installed it now (%d files)"
            % len(want)
            in st["log"],
        )
        check("ticked: no missing-original warning", warn not in st["log"])
        # 5. Uninstall in the window removes refit + original and nothing else
        shown = gui.drive_uninstall(log)
        check(
            "uninstall list shows refit and original",
            any(x.startswith("Refit:") for x in shown)
            and any(x.startswith("Original outfit:") for x in shown),
        )
        check(
            "uninstall: game back to before (unrelated files kept)",
            snap(game) == s0,
            new_files(s0),
        )
        check("uninstall: list empty", core.installed() == [])
        # 2. Install ticked, original already there (same name, in a subfolder): left alone, only the refit installed
        pre = [w for w in want if w.endswith(".archive")][0]
        prep = os.path.join(
            game, "archive", "pc", "mod", "Vortex Ordner ö", os.path.basename(pre)
        )
        os.makedirs(os.path.dirname(prep), exist_ok=True)
        open(prep, "wb").write(b"installed by a mod manager")
        s1 = snap(game)
        st = gui.drive(inp, "angel", out, log, game=game, install=True, body_file=body)
        check("present: refit finished", st["ok"])
        rr = refit_rel(st["log"])
        check("present: only the refit was added", new_files(s1) == [rr], new_files(s1))
        check(
            "present: existing original untouched",
            open(prep, "rb").read() == b"installed by a mod manager",
        )
        check(
            "present: log says kept",
            "Original outfit already installed — kept as is" in st["log"],
        )
        gui.drive_uninstall(log)
        check(
            "present: uninstall removed only the refit", snap(game) == s1, new_files(s1)
        )
        shutil.rmtree(os.path.dirname(prep))
        check("clean between outfits", snap(game) == s0)
    if not last:
        return
    # 6. Close the window during a refit with Install ticked
    inp, want = last
    s0 = snap(game)
    log("\n## close the window while it is installing")
    copy = core._copy

    def slow_copy(src, dst):
        """Each copy takes a while, so the close really lands in the middle of installing."""
        import time

        time.sleep(1.5)
        copy(src, dst)

    core._copy = slow_copy
    try:
        st = gui.drive(
            inp,
            "angel",
            out,
            log,
            game=game,
            install=True,
            body_file=body,
            close_on="Installed into the game: ",
        )
    finally:
        core._copy = copy
    check(
        "close while installing: window closed while files were being copied",
        st["closed"],
    )
    lst = core.installed()
    parts = [f for f in snap(game) if f.endswith(".part")]
    check("close while installing: no half-copied files", not parts, parts)
    complete = all(os.path.isfile(os.path.join(e["folder"], e["file"])) for e in lst)
    check(
        "close while installing: everything in the list is in the game and nothing else",
        complete and sorted(e["rel"].lower() for e in lst) == new_files(s0),
        "%s vs %s" % ([e["rel"] for e in lst], new_files(s0)),
    )
    refits = [e for e in lst if e["kind"] == "refit"]
    check(
        "close while installing: refit in the game is complete",
        refits
        and all(
            open(os.path.join(e["folder"], e["file"]), "rb").read()
            == open(os.path.join(out, e["file"]), "rb").read()
            for e in refits
        ),
    )
    core.uninstall(lst, lambda m: None)
    check("close while installing: clean after uninstall", snap(game) == s0)
    log("\n## close the window as soon as the refit starts")
    st = gui.drive(
        inp, "angel", out, log, game=game, install=True, body_file=body, close_on="=== "
    )
    check("close during refit: window closed mid-refit", st["closed"])
    check(
        "close during refit: nothing written to the game",
        snap(game) == s0,
        new_files(s0),
    )
    check("close during refit: install list empty", core.installed() == [])
    if "--no-wait" in a:
        log("  (not waiting for the stopped refit; the process exits now)")
        return
    w = st["worker"]
    if w:
        w.join(300)
    check("close during refit: refit thread ended", not (w and w.is_alive()))
    check(
        "close during refit: still nothing written afterwards",
        snap(game) == s0 and core.installed() == [],
        new_files(s0),
    )


def check_scrub():
    """The user name and profile path never survive into a support report, in any spelling."""
    import support

    user = os.environ.get("USERNAME") or os.path.basename(os.path.expanduser("~"))
    prof = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    raw = "\n".join(
        [
            prof + r"\Downloads\x.zip",
            prof.lower() + r"\a",
            prof.upper().replace("\\", "/") + "/b",
            "Name " + user + " / " + user.lower() + " / " + user.upper(),
        ]
        + support._user_paths()
    )
    got = support.scrub(raw)
    assert user.lower() not in got.lower(), got
    assert r"%USERPROFILE%\Downloads\x.zip" in got and "%USERPROFILE%/b" in got, got
    assert "Name <user> / <user> / <user>" in got, got
    root, files = (
        "\\download",
        [
            r"\download\Vanilla\a.archive",
            r"\download\EBB\a.archive",
            r"\download\b.archive",
        ],
    )
    import core

    assert core.choose_archives(files, root, lambda m: None) == [
        r"\download\Vanilla\a.archive",
        r"\download\b.archive",
    ]


def find_button(w, text):
    import tkinter.ttk as ttk

    for c in w.winfo_children():
        if isinstance(c, ttk.Button) and c["text"] == text:
            return c
        b = find_button(c, text)
        if b:
            return b
    return None


def make_support_game(base, real, frameworks):
    r"""Fake game for the support-report test: archive\pc\content is a JUNCTION to the real one (read only, for the
    vanilla body shape), a stub body .archive, and with frameworks=True copies of the real ArchiveXL / TweakXL /
    Codeware / RED4ext DLLs (stubs if the real game has none). -> (game folder, junction)"""
    import _winapi

    g = os.path.join(base, "Cyberpunk 2077")
    for d in (r"bin\x64", r"archive\pc\mod", r"r6\tweaks"):
        os.makedirs(os.path.join(g, d), exist_ok=True)
    open(os.path.join(g, r"bin\x64\Cyberpunk2077.exe"), "wb").write(b"stub")
    shutil.copy2(
        os.path.join(real, r"bin\x64\oo2ext_7_win64.dll"), os.path.join(g, r"bin\x64")
    )
    j = os.path.join(g, r"archive\pc\content")
    if not os.path.exists(j):
        _winapi.CreateJunction(os.path.join(real, r"archive\pc\content"), j)
    if frameworks:
        open(
            os.path.join(g, r"archive\pc\mod\05_Hyst_Angel_BodyMod.archive"), "wb"
        ).write(b"stub, not a real archive")
        for rel in [r for _, r in __import__("support").FRAMEWORKS] + [
            r"red4ext\RED4ext.dll",
            r"bin\x64\winmm.dll",
        ]:
            os.makedirs(os.path.dirname(os.path.join(g, rel)), exist_ok=True)
            if os.path.isfile(os.path.join(real, rel)):
                shutil.copy2(os.path.join(real, rel), os.path.join(g, rel))
            else:
                open(os.path.join(g, rel), "wb").write(b"stub")
    return g, j


def remove_junction(j):
    """Never rmtree through a junction: rmdir removes only the link."""
    import subprocess

    if os.path.lexists(j):
        subprocess.run(["cmd", "/c", "rmdir", j], capture_output=True)
    if os.path.lexists(j):
        os.rmdir(j)


def support_tests(core, gui, a, log, fails):
    r"""--support DIR --support-outfit OUTFIT (3x): drop outfits on the real window, change body, tick Install, Refit
    into a fake game in DIR, cause an error, click 'Support report...' and check the report; then restart the
    window on a second fake game without ArchiveXL etc. and check again (previous session, missing flags)."""
    import support
    from tkinter import messagebox

    base = os.path.abspath(opt(a, "--support"))
    real = opt(a, "--game") or core.find_game()
    body = dict(x.split("=", 1) for x in opt_all(a, "--body-archive")).get(
        "angel"
    ) or core.find_body_archive(real, "angel")
    odas, outl, peach = opt_all(a, "--support-outfit")[:3]
    user = os.environ.get("USERNAME") or os.path.basename(os.path.expanduser("~"))
    gA, jA = make_support_game(os.path.join(base, "Spiel mit Mods ä ö"), real, True)
    gB, jB = make_support_game(os.path.join(base, "Spiel ohne Mods ü"), real, False)
    out = os.path.join(base, "Ausgabe ä ö")
    bogus = os.path.join(base, "kaputt ö.txt")
    open(bogus, "w").write("not an outfit")
    log("\n### Support report tests, fake games: %s / %s" % (gA, gB))
    core.uninstall(core.installed(), lambda m: None)
    boxes, opened, reports = [], [], []
    messagebox.showerror = lambda t, m, **k: boxes.append(m)
    messagebox.showinfo = lambda t, m, **k: boxes.append(m)
    support.show_in_explorer = opened.append
    os.startfile = lambda *x, **k: opened.append(x)

    def check(name, cond, detail=""):
        log(
            "  %s %s%s"
            % (
                "OK  " if cond else "FAIL",
                name,
                (" -- %s" % detail) if detail and not cond else "",
            )
        )
        if not cond:
            fails.append("support: " + name)

    def click_report(app, root):
        n = len(boxes)
        find_button(root, "Support report...").invoke()
        yield lambda: len(boxes) > n
        p = opened[-1] if opened else None
        check(
            "report saved in the output folder and shown in Explorer",
            p and os.path.dirname(p) == out and os.path.isfile(p),
            p,
        )
        check(
            "message after saving",
            boxes[-1].startswith("Saved. Send this file to the mod author."),
            boxes[-1],
        )
        try:
            reports.append(open(p, encoding="utf-8").read())  # strict UTF-8
        except Exception as e:
            check("report opens as UTF-8", False, e)

    def window_a(app, root):
        app.s.setdefault("body_archive", {})["angel"] = body
        app.game.set(gA)
        app.out.set(out)
        gui.post_drop(root, [odas, outl, bogus])
        yield lambda: app.lst.size() == 3
        app.body.set(core.body_name("eve"))
        app.body.set(core.body_name("angel"))
        app.inst.set(True)
        installed = core.installed
        core.installed = lambda: (
            1 / 0
        )  # a bug on purpose: 'Uninstall...' fails inside a Tk callback
        n = len(boxes)
        find_button(root, "Uninstall...").invoke()
        core.installed = installed
        check(
            "error on purpose shown as a message",
            len(boxes) == n + 1 and "went wrong" in boxes[-1],
            boxes[n:],
        )
        find_button(root, "Refit").invoke()
        yield lambda: not app.busy
        os.remove(
            os.path.join(gA, r"r6\tweaks\yellingintothevoid\void_Outlander.yaml")
        )  # a tweak gone missing
        dup = os.path.join(gA, r"archive\pc\mod\Vortex Ordner ö")
        os.makedirs(dup, exist_ok=True)
        open(os.path.join(dup, "void_Odas_Pants.archive"), "wb").write(b"second copy")
        gui.post_drop(root, [peach])
        yield lambda: app.lst.size() == 4
        app.lst.selection_clear(0, "end")
        app.lst.selection_set(2)  # the broken input
        find_button(root, "Remove").invoke()
        app.log(
            "Testzeile mit dem Namen: %s %s %s" % (user, user.lower(), user.upper())
        )
        yield from click_report(app, root)
        app.close()  # the real close: writes the session for the next start

    def window_b(app, root):
        app.game.set(gB)
        app.out.set(out)
        w = app.uninstall()
        items = core.installed()
        for i, e in enumerate(items):
            if e["kind"] == "refit" and "outlander" in e["file"].lower():
                w.lb.select_set(i)
        w.remove()
        yield from click_report(app, root)

    try:
        check("window A ran", gui.drive_script(window_a, log))
        check("window B (restart) ran", gui.drive_script(window_b, log))
        if len(reports) != 2:
            check("two reports written", False, len(reports))
            return
        A, B = reports
        for i, r in enumerate(reports):
            check(
                "report %s: no user name or profile path" % "AB"[i],
                user.lower() not in r.lower(),
            )
            check(
                "report %s: all sections" % "AB"[i],
                all(
                    s in r
                    for s in (
                        "1. Tool and system",
                        "2. Settings",
                        "3. This session",
                        "3b. Previous session",
                        "4. Game folder check",
                        "5. What this tool installed",
                        "Tool version: " + core.VERSION,
                    )
                ),
            )
        s = lambda p: support.scrub(p)
        for want in [
            "dropped outfit: " + s(odas),
            "dropped outfit: " + s(outl),
            "dropped outfit: " + s(bogus),
            "dropped outfit: " + s(peach),
            "body set to: Hyst EVE",
            "body set to: Hyst Angel",
            "install box ticked",
            "game folder set to: " + s(gA),
            "output folder set to: " + s(out),
            "clicked 'Uninstall...'",
            "clicked 'Refit'",
            "Refit started: 3 outfit(s)",
            "Refit finished",
            "removed outfit: " + s(bogus),
            "clicked 'Support report...'",
            "Testzeile mit dem Namen: <user> <user> <user>",
        ]:
            check("report A has: " + want, want in A)
        this_a = A.split("3b. Previous session")[0]
        check(
            "report A: error on purpose with its traceback",
            "[error] Unexpected error:" in this_a and "ZeroDivisionError" in this_a,
        )
        check(
            "report A: failed outfit as error",
            "[error] Could not refit this outfit: kaputt ö.txt is not a zip" in this_a,
        )
        check(
            "report A: message box text",
            "[message] Something went wrong in the window" in this_a,
        )
        game_a = A.split("4. Game folder check")[1].split(
            "5. What this tool installed"
        )[0]
        for name, rel in support.FRAMEWORKS + [("RED4ext", r"red4ext\RED4ext.dll")]:
            v = support.file_version(os.path.join(gA, rel))
            check(
                "report A: %s present%s" % (name, " with version %s" % v if v else ""),
                ("%s present" % rel if name == "RED4ext" else "%s: present" % name)
                in game_a
                and (not v or v in game_a),
            )
        check(
            "report A: RED4ext loader winmm.dll", "loader bin\\x64\\winmm.dll" in game_a
        )
        check(
            "report A: body mod found",
            "05_Hyst_Angel_BodyMod.archive (Hyst Angel)" in game_a,
        )
        check(
            "report A: duplicate flagged",
            any(
                l.startswith("  DUPLICATE:")
                and "Vortex Ordner ö\\void_Odas_Pants.archive" in l
                for l in game_a.splitlines()
            ),
        )

        def block(inp):
            i = game_a.find("Outfit: " + s(inp))
            return game_a[i : game_a.find("\n\n", i)] if i >= 0 else ""

        bo, bl, bp, bb = block(odas), block(outl), block(peach), block(bogus)
        check(
            "report A: Odas original/xl/tweak/refit present",
            "(refit this session: " in bo
            and "original void_Odas_Pants.archive: present" in bo
            and ".xl void_Odas_Pants.xl: present" in bo
            and r"tweak r6\tweaks\yellingintothevoid\void_Odas_Pants.yaml: present"
            in bo
            and "refit in the game: MISSING" not in bo
            and "refit in the output folder: none" not in bo,
            bo,
        )
        check(
            "report A: Outlander tweak missing, rest present",
            "original void_Outlander.archive: present" in bl
            and ".xl void_Outlander.xl: present" in bl
            and r"tweak r6\tweaks\yellingintothevoid\void_Outlander.yaml: MISSING" in bl
            and "refit in the game: MISSING" not in bl,
            bl,
        )
        check(
            "report A: Peachu (not refit) original + xl + refit missing",
            "original Peachu_SchoolShorts-VANILLA.archive: MISSING" in bp
            and ".xl Peachu_SchoolShorts-VANILLA.xl: MISSING" in bp
            and "refit in the game: MISSING (none found)" in bp
            and "refit this session" not in bp,
            bp,
        )
        check(
            "report A: broken input explained",
            "refit tried this session, no file made" in bb
            and "could not look inside the download" in bb,
            bb,
        )
        inst_a = A.split("5. What this tool installed")[1]
        check(
            "report A: install list has refits and originals",
            inst_a.count("Refit: ") == 2 and "Original outfit: " in inst_a,
            inst_a,
        )
        prev_b = B.split("3b. Previous session")[1].split("4. Game folder check")[0]
        check(
            "report B: previous session = window A",
            "dropped outfit: " + s(odas) in prev_b
            and "Refit finished" in prev_b
            and "ZeroDivisionError" in prev_b
            and "window closed" in prev_b,
        )
        check(
            "report B: this session has the uninstall pick",
            "uninstall picked: " in B.split("3b.")[0]
            and "Outlander" in B.split("3b.")[0],
        )
        game_b = B.split("4. Game folder check")[1].split(
            "5. What this tool installed"
        )[0]
        for name, _ in support.FRAMEWORKS:
            check("report B: %s missing" % name, "%s: MISSING" % name in game_b)
        check(
            "report B: RED4ext missing",
            r"red4ext\RED4ext.dll MISSING" in game_b
            and "winmm.dll / version.dll MISSING" in game_b,
        )
        check(
            "report B: no body mod",
            "Body mods found in archive\\pc\\mod: none" in game_b,
        )
        check(
            "report B: Outlander refit gone from the install list",
            "Refit: " in B.split("5. What this tool installed")[1]
            and "AngelRefit_void_Outlander"
            not in B.split("5. What this tool installed")[1].replace("!!!", ""),
        )
        log("  example report: %s" % opened[0])
    finally:
        core.uninstall(core.installed(), lambda m: None)
        remove_junction(jA)
        remove_junction(jB)
        check(
            "junctions removed, real content untouched",
            not os.path.lexists(jA)
            and not os.path.lexists(jB)
            and os.path.isdir(os.path.join(real, r"archive\pc\content")),
        )


def main(a):
    if opt(a, "--home"):
        os.environ["BODYREFIT_HOME"] = os.path.abspath(opt(a, "--home"))
    import core, rdar

    log = lambda m: print(m, flush=True)
    fails = []
    log("Outfit Refitter %s self-test" % core.VERSION)
    try:
        check_variants(core)
        log("download-variant selection OK")
    except AssertionError as e:
        log("FAIL download-variant selection: %s" % e)
        fails.append("variants")
    try:
        check_recover_names(rdar, __import__("refit"))
        log("names for archives without a name list OK")
    except AssertionError as e:
        log("FAIL names for archives without a name list: %s" % e)
        fails.append("recover names")
    try:
        check_original_plan(core)
        log("original-outfit install plan OK")
    except AssertionError as e:
        log("FAIL original-outfit install plan: %s" % (e,))
        fails.append("original plan")
    try:
        check_scrub()
        log("support report privacy scrub OK")
    except AssertionError as e:
        log("FAIL support report privacy scrub: %s" % e)
        fails.append("scrub")
    out = os.path.abspath(
        opt(a, "--out") or tempfile.mkdtemp(prefix="bodyrefit_selftest_")
    )
    log("data folder: %s\noutput: %s" % (core.DATA, out))
    game = opt(a, "--game") or core.find_game()
    log("game folder: %s" % game)
    if not core.is_game_dir(game):
        log("FAIL: game not found")
        return 1
    core.ensure_oodle(game)
    wk = core.ensure_wolvenkit(log, lambda f: None, opt(a, "--wolvenkit"))
    log("WolvenKit OK: %s" % wk)
    barch = dict(x.split("=", 1) for x in opt_all(a, "--body-archive"))
    tags = sorted(
        set(core.bodies()["bodies"])
        & (
            {t for t in barch}
            | {c.split("=")[0] for c in opt_all(a, "--case")}
            | {t for t in core.bodies()["bodies"] if core.find_body_archive(game, t)}
        )
    )
    for t in tags:
        try:
            _, cages = core.setup(game, t, log, body_archive=barch.get(t))
            log(
                "body shape %s OK: %s"
                % (t, ", ".join(os.path.basename(c) for c in cages[t].split(";")))
            )
        except core.ToolError as e:
            log("FAIL body %s: %s" % (t, e))
            fails.append(t)
    for c in opt_all(a, "--case"):
        parts = c.split("=")
        tag, inp, ref = parts[0], parts[1], (parts[2] if len(parts) > 2 else None)
        log("\n### case %s -> %s" % (os.path.basename(inp), tag))
        res = core.run_batch([inp], tag, game, out, log, body_archive=barch.get(tag))
        arch = res[0][1]
        if not arch:
            log("FAIL: no output (%s)" % res[0][2])
            fails.append(inp)
            continue
        bad = __import__("refit").buffer_gate(arch, {})
        r = rdar.Rdar(arch)
        log(
            "  output %s, %d bytes, index CRC %s, buffer check %s"
            % (
                os.path.basename(arch),
                os.path.getsize(arch),
                "OK" if r.crc_ok else "BAD",
                bad or "OK",
            )
        )
        for n in r.names:
            log("    " + n)
        if bad or not r.crc_ok:
            fails.append(inp)
        if ref:
            work = tempfile.mkdtemp(prefix="cmp_", dir=core._work_root())
            try:
                log("  compare with reference %s:" % os.path.basename(ref))
                w = compare(wk, game, arch, ref, work, log)
                log(
                    "  worst vertex difference: %s"
                    % ("%.3f mm" % w if w is not None else "nothing could be compared")
                )
            except (
                Exception
            ) as e:  # the comparison is information, not a pass/fail gate
                log("  comparison failed: %s" % e)
            finally:
                shutil.rmtree(work, ignore_errors=True)
    if "--gui" in a:
        try:
            import gui

            gui.main(close_after_ms=2000)
            log("\nwindow opened and closed OK")
        except Exception as e:
            log("FAIL: window did not start: %r" % e)
            fails.append("gui")
    for v in [x for i, x in enumerate(a) if i and a[i - 1] == "--gui-drop"]:
        tag, outfit, *expect = v.split("=")
        log("\nGUI drag-and-drop test: %s -> %s" % (os.path.basename(outfit), tag))
        try:
            import gui, refit

            out = tempfile.mkdtemp(prefix="rf_guidrop_")
            st = gui.drive(outfit, tag, out, log, game=game, body_file=barch.get(tag))
            for m in st["log"]:
                log("    | " + m)
            arcs = [f for f in os.listdir(out) if f.lower().endswith(".archive")]
            missing = [
                e
                for e in ";".join(expect).split(";")
                if e and not any(e in m for m in st["log"])
            ]
            bad = []
            for f in arcs:
                r = rdar.Rdar(os.path.join(out, f))
                bad += refit.buffer_gate(os.path.join(out, f), {})
                log(
                    "  %s: %d files, index CRC %s"
                    % (f, len(r.names), "OK" if r.crc_ok else "BAD")
                )
                for n in r.names:
                    log("    " + n)
            if st["ok"] and arcs and not missing and not bad:
                log("  OK: window refit produced %s, safety check OK" % arcs)
            else:
                log(
                    "  FAIL: ok=%s archives=%s missing log text=%s safety=%s"
                    % (st["ok"], arcs, missing, bad)
                )
                fails.append("gui-drop " + tag)
        except Exception:
            log("FAIL: gui-drop crashed:\n" + traceback.format_exc())
            fails.append("gui-drop " + tag)
    if opt(a, "--gui-install"):
        try:
            import gui

            gui_install_tests(core, gui, a, log, fails)
        except Exception:
            log("FAIL: install tests crashed:\n" + traceback.format_exc())
            fails.append("gui-install")
    if opt(a, "--support"):
        try:
            import gui

            support_tests(core, gui, a, log, fails)
        except Exception:
            log("FAIL: support report tests crashed:\n" + traceback.format_exc())
            fails.append("support")
    log("\nSELFTEST %s" % ("PASSED" if not fails else "FAILED: %s" % fails))
    return 1 if fails else 0
