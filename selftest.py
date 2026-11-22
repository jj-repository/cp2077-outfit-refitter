r"""Headless self-test: game detection, WolvenKit download/check, body-shape extraction from the installed body mods,
then refit real outfit archives, run the safety check and list the output. Optionally compares the vertex positions
with refits you trust (e.g. ones made by hand).

  outfit_refitter.py --selftest [--home DIR] [--out DIR] [--game DIR] [--wolvenkit DIR]
                   [--body-archive TAG=FILE ...] [--case [SRC>]TAG=OUTFIT[=REFERENCE.archive[=exact]] ...] [--gui]

--case: refit OUTFIT from body SRC (default vanilla) to TAG; with a reference, print the vertex distance per mesh;
    'exact' = fail unless it is 0 (an unchanged refit).
--compare A=B: vertex distance between two archives (information).
--roundtrip TAG=OUTFIT[=MAX_MEAN_MM]: vanilla -> TAG -> vanilla, compared with the vanilla original.
--gui-source DIR --gui-source-outfit ANGEL_OUTFIT [--gui-source-body eve=DOWNLOAD]: 'Outfit made for' tests in the
    real window (pre-fill, wrong source, same body twice, missing source body, body from a download, 'Body file...',
    closing mid-refit).

--home: data folder to use instead of %LOCALAPPDATA%\OutfitRefitter (fresh folder = real first run).
--gui: also open the window for two seconds and close it (proves it starts).
--gui-drop TAG=OUTFIT[=TEXT;TEXT]: open the real window, drop OUTFIT on it like Explorer does, press Refit, check the
    result (+ safety check; each TEXT must show up in the window log).
--gui-install DIR --gui-install-outfit OUTFIT=>REL;REL ...: 'Install into the game' tests in the real window on a fake
    game folder made in DIR (original missing / already there / Install unticked / Uninstall / closing the window).
    REL: the original outfit's files expected in the game, relative to the game folder. --no-wait: don't wait for
    the refit stopped by closing the window (the process exits while it ends).
--own-reader OUTFIT: check the own mesh reader (used for meshes WolvenKit can't read) on every mesh of OUTFIT:
    same positions as WolvenKit's export, byte-exact round trip, displaced write read back by WolvenKit.
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
    a static override (same path) and a switcher-style _<body> sibling of the same stem are treated as the same mesh.
    -> (worst max over all meshes, mean over all compared vertices), (None, None) when nothing could be compared."""
    import numpy as np

    a = positions(wk, game, new, os.path.join(work, "cmp_new"))
    b = positions(wk, game, ref, os.path.join(work, "cmp_ref"))
    norm = lambda n: (
        n.replace("_eve.mesh", ".mesh")
        .replace("_angel.mesh", ".mesh")
        .replace("_base_body.mesh", ".mesh")
    )
    bn = {norm(k): v for k, v in b.items()}
    worst, alld = None, []
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
        alld.append(d)
        log(
            "    %-60s max %.3f mm  mean %.4f mm  (%d verts)"
            % (os.path.basename(k), d.max(), d.mean(), len(d))
        )
    return worst, (float(np.concatenate(alld).mean()) if alld else None)


def show_compare(wk, game, new, ref, log, what="reference"):
    """compare() with its own work folder; information, never a pass/fail gate. -> (worst, mean) or (None, None)."""
    import core

    work = tempfile.mkdtemp(prefix="cmp_", dir=core._work_root())
    try:
        log("  compare with %s %s:" % (what, os.path.basename(ref)))
        w, m = compare(wk, game, new, ref, work, log)
        log(
            "  worst vertex difference: %s"
            % (
                "%.3f mm (mean %.3f mm)" % (w, m)
                if w is not None
                else "nothing could be compared"
            )
        )
        return w, m
    except Exception as e:
        log("  comparison failed: %s" % e)
        return None, None
    finally:
        shutil.rmtree(work, ignore_errors=True)


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


def check_source(core):
    """Source body ('Outfit made for'): name guess, wrong-input warning, picking a download's version for the source,
    same source and target refused, body file checks."""
    for name, want in (
        ("Peachu_CasualDress-ANGEL", "angel"),
        ("!!!AngelRefit_Peachu_SchoolShorts", "angel"),
        ("EveRefit_x", "eve"),
        ("EVE - Ocean String - Archive XL", "eve"),
        ("EVE Body - Full Vanilla Refit", "eve"),  # first body word wins
        ("Peachu_FemV_Evelyn_Vanilla", "vanilla"),  # 'Evelyn' is not EVE
        ("Stellar_Blade_Eve_Bodysuit_ArchiveXL", None),  # 'Eve' = a character's name
        ("Test outfit - Peachu School Shorts", None),
    ):
        g = core.guess_body([name])
        assert (g[1] if g else None) == want, (name, g)
    assert core.guess_body(["Quiet Outfit_Hyst EBRB Refit-5194"]) == ("EBRB", None)
    assert core.xl_name("vanilla") == "base_body" and core.xl_name("angel") == "angel"
    for src, texts, warn in (
        ("vanilla", ["Peachu_CasualDress-ANGEL"], "looks made for Hyst Angel"),
        ("angel", ["Peachu_CasualDress-ANGEL"], None),
        (
            "eve",
            ["Peachu_CasualDress-ANGEL"],
            "but 'Outfit made for' is set to Hyst EVE",
        ),
        (
            "angel",
            [r"Vanilla\archive\pc\mod\x.archive", "Outfit"],
            "looks made for Vanilla",
        ),
        (
            "angel",
            ["Dress VTK Small"],
            "made for another body (VTK), not for Hyst Angel",
        ),
        ("vanilla", ["Some Outfit"], None),
    ):
        msgs = []
        core.variant_warning(texts, msgs.append, src)
        assert (len(msgs) == 1 and warn in msgs[0]) if warn else not msgs, (
            src,
            texts,
            msgs,
        )
    arcs = [
        r"r\Vanilla\a.archive",
        r"r\Angel\a.archive",
        r"r\EBB\a.archive",
        r"r\x\d_vanilla.archive",
        r"r\x\d_angel.archive",
        r"r\x\d_ebb.archive",
        r"r\s.archive",
    ]
    for src, want in (
        ("vanilla", [r"r\Vanilla\a.archive", r"r\s.archive", r"r\x\d_vanilla.archive"]),
        ("angel", [r"r\Angel\a.archive", r"r\s.archive", r"r\x\d_angel.archive"]),
        ("eve", [r"r\Vanilla\a.archive", r"r\s.archive", r"r\x\d_vanilla.archive"]),
    ):
        got = core.choose_archives(arcs, "r", lambda m: None, src)
        assert got == want, (src, got)
    t = tempfile.mkdtemp(prefix="outfitrefit_src_")
    try:
        game = os.path.join(t, "Spiel ä")
        os.makedirs(os.path.join(game, "bin", "x64"))
        open(os.path.join(game, "bin", "x64", "Cyberpunk2077.exe"), "wb").close()
        try:
            core.setup(game, "angel", "angel", lambda m: None)
            assert False, "same source and target accepted"
        except core.ToolError as e:
            assert "both Hyst Angel" in str(e), e
        import zipfile

        empty = os.path.join(t, "kein Körper ö.zip")
        with zipfile.ZipFile(empty, "w") as z:
            z.writestr("readme.txt", "x")
        for bad in (empty, os.path.join(t, "x.txt")):
            try:
                core.check_body_file(bad, "eve")
                assert False, bad
            except core.ToolError:
                pass
        assert core.body_source(game, "vanilla") == core.vanilla_sources(game)
        assert core.body_source(game, "eve", {"eve": empty}) == [empty]
        assert (
            core.body_source(game, "eve", {"eve": os.path.join(t, "gone.zip")}) is None
        )
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


def check_own_reader(wk, game, outfit, work, log):
    """The own mesh reader (fallback for meshes WolvenKit can't read) on every mesh of an outfit: positions must equal
    WolvenKit's glb export within one quantization step where WolvenKit can read the mesh; writing the unchanged
    positions must give back the same content byte for byte, and a real displacement (incl. a grown bounding range)
    must pass own_refit's checks and, where WolvenKit can read the mesh, come back from WolvenKit as written.
    -> list of problems."""
    import numpy as np
    import core, rdar, refit

    src = os.path.join(work, "src")
    arcs = core.prepare_input(outfit, os.path.join(work, "in"), lambda m: None)
    for a in arcs:
        core.run([wk, "unbundle", a, "-o", src], game)
    if not all(rdar.Rdar(a).names for a in arcs):
        refit.recover_names(src, arcs)
    bad, n_own, n_wk = [], 0, 0
    for m in sorted(glob.glob(os.path.join(src, "**", "*.mesh"), recursive=True)):
        name = os.path.basename(m)
        try:
            own = refit.OwnMesh(open(m, "rb").read())
        except Exception as e:
            log("    %-40s own reader: can't read (%s)" % (name, str(e)[:80]))
            continue
        n_own += 1
        P = own.positions()
        step = own.qs.max() / 32767.0
        wkd = os.path.join(work, "glb", os.path.relpath(m, src))
        os.makedirs(wkd, exist_ok=True)
        try:
            core.run([wk, "export", m, "-o", wkd, "-gp", game], game)
            G = refit.from_glb_space(
                refit.glb_points(glob.glob(os.path.join(wkd, "*.glb"))[0])
            )
        except RuntimeError:
            G = None
        line = "    %-40s %6d verts" % (name, len(P))
        if G is not None:
            n_wk += 1
            dv = np.abs(G - P).max() if G.shape == P.shape else float("inf")
            line += ", vs WolvenKit max %.4f mm (step %.4f mm)" % (
                dv * 1000,
                step * 1000,
            )
            if dv > step + 1e-5:
                bad.append(name + ": positions differ from WolvenKit")
        else:
            line += ", WolvenKit can't read it"
        lo, hi = P.min(0), P.max(0)
        Q = P + 0.01 * np.sin(
            (P - lo) / np.maximum(hi - lo, 1e-6) * 3.0
        )  # smooth, up to 1 cm, grows the range
        try:
            new = refit.own_refit(own, Q)  # includes the byte-exact round trip
            line += ", round trip byte-exact, displaced write OK"
        except Exception as e:
            bad.append("%s: %s" % (name, e))
            log(line + ", FAILED: %s" % e)
            continue
        if G is not None:
            chk = os.path.join(work, "chk", os.path.relpath(m, src))
            os.makedirs(chk, exist_ok=True)
            open(os.path.join(chk, name), "wb").write(new)
            core.run(
                [wk, "export", os.path.join(chk, name), "-o", chk, "-gp", game], game
            )
            G2 = refit.from_glb_space(
                refit.glb_points(glob.glob(os.path.join(chk, "*.glb"))[0])
            )
            dq = np.abs(G2 - Q).max()
            line += ", WolvenKit reads it back within %.4f mm" % (dq * 1000)
            if dq > refit.OwnMesh(new).qs.max() / 32767.0 + 1e-5:
                bad.append(name + ": WolvenKit reads other positions back")
        log(line)
    log("    own reader: %d meshes read, %d compared with WolvenKit" % (n_own, n_wk))
    if not n_own:
        bad.append("own reader read no mesh")
    return bad


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
    van = core.cage_dir("vanilla")
    if os.path.isdir(
        van
    ):  # the fake game has no base-game archives: keep the vanilla shape read from the real game
        open(os.path.join(van, "source.txt"), "w", encoding="utf-8").write(
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


def gui_source_tests(core, gui, a, log, fails):
    r"""--gui-source DIR --gui-source-outfit ANGEL_OUTFIT [--gui-source-body eve=DOWNLOAD]: 'Outfit made for' in the
    real window. Name pre-fill + Angel -> vanilla, a wrong source (warning, the refit still runs), source = target
    (refused), a missing source body (clear stop), the source body read from a download (shape read fresh),
    'Body file...' checks, closing the window mid-refit. Inputs and output live in DIR (umlaut paths); the real game
    folder is only read (Install stays unticked)."""
    import refit, zipfile
    from tkinter import messagebox

    base = os.path.abspath(opt(a, "--gui-source"))
    game = opt(a, "--game") or core.find_game()
    barch = dict(x.split("=", 1) for x in opt_all(a, "--body-archive"))
    dls = dict(x.split("=", 1) for x in opt_all(a, "--gui-source-body"))
    inp = os.path.join(base, "Eingabe ä ö")
    os.makedirs(inp, exist_ok=True)
    outfit = os.path.join(inp, os.path.basename(opt(a, "--gui-source-outfit")))
    shutil.copy2(
        opt(a, "--gui-source-outfit"), outfit
    )  # a copy: never touch the installed mod
    out = os.path.join(base, "Ausgabe ü")
    os.makedirs(out, exist_ok=True)
    md = core.mod_dir(game)
    mods0 = sorted(
        (os.path.relpath(os.path.join(dp, f), md), os.path.getsize(os.path.join(dp, f)))
        for dp, _, fs in os.walk(md)
        for f in fs
    )
    log("\n### 'Outfit made for' tests in the window: %s" % os.path.basename(outfit))

    def check(name, cond, detail=""):
        log(
            "  %s %s%s"
            % (
                "OK  " if cond else "FAIL",
                name,
                (" -- %s" % (detail,)) if detail and not cond else "",
            )
        )
        if not cond:
            fails.append("source: " + name)

    def made(prefix, before):
        new = sorted(set(os.listdir(out)) - before)
        return [f for f in new if f.startswith(prefix)]

    has = lambda st, text: any(text in m for m in st["log"])

    # 1. the name says ANGEL: 'Outfit made for' is pre-filled, Angel -> vanilla
    b = set(os.listdir(out))
    st = gui.drive(outfit, "vanilla", out, log, game=game, src=None, body_files=barch)
    check(
        "pre-fill: 'Outfit made for' set to Hyst Angel from the name",
        st.get("src") == "angel" and has(st, "'Outfit made for' set to Hyst Angel"),
        st.get("src"),
    )
    check("Angel -> vanilla: finished without error", st["ok"], st["errors"])
    van = made("!!!VanillaRefit_", b)
    check(
        "Angel -> vanilla: !!!VanillaRefit_ archive, safety check OK",
        len(van) == 1 and not refit.buffer_gate(os.path.join(out, van[0]), {}),
        van,
    )
    check("Angel -> vanilla: no wrong-body warning", not has(st, "looks made for"))
    # 2. wrong source: the name says ANGEL, the user leaves 'Vanilla' -> warning, refit still made
    b = set(os.listdir(out))
    st = gui.drive(outfit, "eve", out, log, game=game, src="vanilla", body_files=barch)
    check("wrong source: warning in the log", has(st, "looks made for Hyst Angel"))
    eve = made("!!!EveRefit_", b)
    check(
        "wrong source: refit still made (only a warning)",
        st["ok"] and len(eve) == 1,
        eve,
    )
    # 3. same body on both sides
    st = gui.drive(outfit, "angel", out, log, game=game, src="angel", body_files=barch)
    check(
        "same source and target: refused with a message, nothing started",
        not st["ok"]
        and st["worker"] is None
        and any("nothing to refit" in e for e in st["errors"]),
        st["errors"],
    )
    # 4. source body not installed and no file picked
    if core.find_body_archive(game, "eve"):
        log("  (skipped the missing-body test: Hyst EVE is installed in this game)")
    else:
        b = set(os.listdir(out))
        st = gui.drive(
            outfit,
            "vanilla",
            out,
            log,
            game=game,
            src="eve",
            body_files={k: v for k, v in barch.items() if k != "eve"},
        )
        check(
            "missing source body: clear stop with what to do",
            has(st, "Stopped: The outfit is made for Hyst EVE")
            and any(
                "'Body file...' next to 'Outfit made for'" in e for e in st["errors"]
            ),
            st["errors"],
        )
        check("missing source body: no file written", made("", b) == [], made("", b))
    # 5. source body from a download (zip in a folder with umlauts), shape read fresh
    zp = None
    if "eve" in dls and eve:
        zp = os.path.join(base, "Körper Download ä", os.path.basename(dls["eve"]))
        os.makedirs(os.path.dirname(zp), exist_ok=True)
        if not os.path.isfile(zp):
            shutil.copy2(dls["eve"], zp)
        shutil.rmtree(core.cage_dir("eve"), ignore_errors=True)
        eo = os.path.join(inp, eve[0])
        shutil.copy2(os.path.join(out, eve[0]), eo)
        b = set(os.listdir(out))
        st = gui.drive(
            eo, "vanilla", out, log, game=game, src=None, body_files=dict(barch, eve=zp)
        )
        check(
            "body download: 'Outfit made for' pre-filled EVE (EveRefit_ name)",
            st.get("src") == "eve",
        )
        check(
            "body download: EVE shape read from the zip, refit made",
            st["ok"]
            and has(st, "Reading the Hyst EVE body shape")
            and len(made("!!!VanillaRefit_", b)) == 1,
            st["errors"],
        )
        stamp = open(
            os.path.join(core.cage_dir("eve"), "source.txt"), encoding="utf-8"
        ).read()
        check(
            "body download: cache remembers the zip",
            os.path.basename(zp) in stamp,
            stamp,
        )
        shutil.rmtree(
            core.cage_dir("eve"), ignore_errors=True
        )  # later runs re-read it from their own file
    # 6. 'Body file...' (the part after the file dialog), help text
    boxes = []
    messagebox.showerror = lambda t, m, **k: boxes.append(m)
    messagebox.showinfo = lambda t, m, **k: boxes.append(m)
    bogus = os.path.join(inp, "kaputt ö.txt")
    open(bogus, "w").write("not a body")
    empty = os.path.join(inp, "leer ä.zip")
    with zipfile.ZipFile(empty, "w") as z:
        z.writestr("readme.txt", "no archive in here")
    good = zp or barch.get("eve")
    saved = core.load_settings()

    def script(app, root):
        n = len(boxes)
        r1, r2 = app.use_body_file("eve", bogus), app.use_body_file("eve", empty)
        check(
            "Body file...: a text file and a zip without .archive are refused",
            r1 is False and r2 is False and len(boxes) == n + 2,
            boxes[n:],
        )
        app.src.set(core.body_name("eve"))
        app.show_body()
        if good:
            check(
                "Body file...: a body download / .archive is taken and shown",
                app.use_body_file("eve", good)
                and app.src_info["text"] == "found: " + os.path.basename(good),
                app.src_info["text"],
            )
        app.src.set(core.body_name("vanilla"))
        app.pick_body("src")  # vanilla: no file dialog, a message instead
        check(
            "Body file... for vanilla: says none is needed",
            "needs no body file" in boxes[-1],
            boxes[-1:],
        )
        h = app.show_help()
        root.update()
        text = h.winfo_children()[0].get("1.0", "end")
        h.destroy()
        check(
            "help explains 'Outfit made for'",
            "Outfit made for" in text and "Body file..." in text,
        )
        yield lambda: True

    check("Body file... script ran", gui.drive_script(script, log))
    core.save_settings(saved)
    # 7. close the window while refitting from Angel
    out2 = os.path.join(base, "Ausgabe Abbruch ö")
    os.makedirs(out2, exist_ok=True)
    st = gui.drive(
        outfit,
        "vanilla",
        out2,
        log,
        game=game,
        src="angel",
        body_files=barch,
        close_on="=== ",
    )
    check("close mid-refit: window closed", st["closed"])
    if st["worker"]:
        st["worker"].join(300)
    check(
        "close mid-refit: refit thread ended",
        not (st["worker"] and st["worker"].is_alive()),
    )
    check(
        "close mid-refit: no file left behind", os.listdir(out2) == [], os.listdir(out2)
    )
    mods1 = sorted(
        (os.path.relpath(os.path.join(dp, f), md), os.path.getsize(os.path.join(dp, f)))
        for dp, _, fs in os.walk(md)
        for f in fs
    )
    check("game mod folder unchanged", mods0 == mods1)


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
        check_source(core)
        log("source body (name guess, warning, version pick, body files) OK")
    except AssertionError as e:
        log("FAIL source body: %s" % (e,))
        fails.append("source body")
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
    for o in opt_all(a, "--own-reader"):
        log("\n### own mesh reader on %s" % os.path.basename(o))
        work = tempfile.mkdtemp(prefix="own_", dir=core._work_root())
        try:
            bad = check_own_reader(wk, game, o, work, log)
        except Exception:
            bad = ["crashed:\n" + traceback.format_exc()]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        log("  own reader %s" % ("OK" if not bad else "FAIL: %s" % bad))
        if bad:
            fails.append("own reader " + os.path.basename(o))
    barch = dict(x.split("=", 1) for x in opt_all(a, "--body-archive"))
    pair = lambda spec: spec.split(">") if ">" in spec else ["vanilla", spec]
    tags = sorted(
        (
            set(core.bodies()["bodies"])
            & (
                {t for t in barch}
                | {t for c in opt_all(a, "--case") for t in pair(c.split("=")[0])}
                | {
                    t
                    for t in core.bodies()["bodies"]
                    if core.bodies()["bodies"][t].get("archives")
                    and core.find_body_archive(game, t)
                }
            )
        )
        - {"vanilla"}
    )
    for t in tags:
        try:
            _, cages = core.setup(game, "vanilla", t, log, body_files=barch)
            log(
                "body shape %s OK: %s"
                % (t, ", ".join(os.path.basename(c) for c in cages[t].split(";")))
            )
        except core.ToolError as e:
            log("FAIL body %s: %s" % (t, e))
            fails.append(t)

    def run_case(src, tag, inp, dest):
        """One refit like the window does it, plus the safety check of the output. -> output archive or None."""
        log("\n### case %s: %s -> %s" % (os.path.basename(inp), src, tag))
        res = core.run_batch([inp], tag, game, dest, log, body_files=barch, src=src)
        arch = res[0][1]
        if not arch:
            log("FAIL: no output (%s)" % res[0][2])
            fails.append(inp)
            return None
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
        return arch

    # --case [SRC>]TGT=OUTFIT[=REFERENCE[=exact]]: exact = must give the reference's positions (within 0.0005 mm)
    for c in opt_all(a, "--case"):
        parts = c.split("=")
        (src, tag), inp = pair(parts[0]), parts[1]
        ref = parts[2] if len(parts) > 2 else None
        arch = run_case(src, tag, inp, out)
        if arch and ref:
            w, _ = show_compare(wk, game, arch, ref, log)
            if parts[3:] == ["exact"] and (w is None or w > 0.0005):
                log("FAIL: not the same as the reference")
                fails.append("exact " + os.path.basename(inp))
    # --compare A=B: how far apart two archives are (information, e.g. an author's Angel and vanilla versions)
    for c in opt_all(a, "--compare"):
        x, y = c.split("=", 1)
        log("\n### compare %s with %s" % (os.path.basename(x), os.path.basename(y)))
        show_compare(wk, game, x, y, log, "")
    # --roundtrip TAG=OUTFIT[=MAX_MEAN_MM]: vanilla -> TAG -> vanilla must come back close to the vanilla original
    for c in opt_all(a, "--roundtrip"):
        parts = c.split("=")
        tag, inp = parts[0], parts[1]
        log(
            "\n### round trip vanilla -> %s -> vanilla: %s"
            % (tag, os.path.basename(inp))
        )
        rt = tempfile.mkdtemp(prefix="rt_", dir=core._work_root())
        try:
            orig = core.prepare_input(inp, os.path.join(rt, "orig"), lambda m: None)
            one = run_case("vanilla", tag, inp, os.path.join(rt, "1"))
            two = one and run_case(tag, "vanilla", one, os.path.join(rt, "2"))
            if not two or len(orig) != 1:
                log("FAIL: round trip incomplete (%d original archives)" % len(orig))
                fails.append("roundtrip " + os.path.basename(inp))
                continue
            w, m = show_compare(wk, game, two, orig[0], log, "the vanilla original")
            log(
                "  round trip error: max %s mm, mean %s mm"
                % (
                    "%.3f" % w if w is not None else "?",
                    "%.3f" % m if m is not None else "?",
                )
            )
            if m is None or (len(parts) > 2 and m > float(parts[2])):
                log("FAIL: round trip error too big or not measurable")
                fails.append("roundtrip " + os.path.basename(inp))
        finally:
            shutil.rmtree(rt, ignore_errors=True)
    if "--gui" in a:
        try:
            import gui

            gui.main(close_after_ms=2000)
            log("\nwindow opened and closed OK")
        except Exception as e:
            log("FAIL: window did not start: %r" % e)
            fails.append("gui")
    for v in [x for i, x in enumerate(a) if i and a[i - 1] == "--gui-drop"]:
        spec, outfit, *expect = v.split("=")
        src, tag = pair(spec)
        log(
            "\nGUI drag-and-drop test: %s, %s -> %s"
            % (os.path.basename(outfit), src, tag)
        )
        try:
            import gui, refit

            out = tempfile.mkdtemp(prefix="rf_guidrop_")
            st = gui.drive(outfit, tag, out, log, game=game, body_files=barch, src=src)
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
    if opt(a, "--gui-source"):
        try:
            import gui

            gui_source_tests(core, gui, a, log, fails)
        except Exception:
            log("FAIL: 'Outfit made for' tests crashed:\n" + traceback.format_exc())
            fails.append("gui-source")
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
