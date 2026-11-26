r"""Headless self-test: game detection, WolvenKit download/check, body-shape extraction from the installed body mods,
then refit real outfit archives, run the safety check and list the output. Optionally compares the vertex positions
with refits you trust (e.g. ones made by hand).

  outfit_refitter.py --selftest [--home DIR] [--out DIR] [--game DIR] [--wolvenkit DIR]
                   [--body-archive TAG=FILE ...] [--case [SRC>]TAG=OUTFIT[=REFERENCE.archive[=exact]] ...] [--gui]

--case: refit OUTFIT from body SRC (default vanilla) to TAG; with a reference, print the vertex distance per mesh;
    'exact' = fail unless it is 0 (an unchanged refit).
--compare A=B: vertex distance between two archives (information).
--roundtrip TAG=OUTFIT[=MAX_MEAN_MM]: vanilla -> TAG -> vanilla, compared with the vanilla original.
--layers TAG=OUTFIT=STEM,STEM,...: garment layers (mesh stems inner -> outer): outer-layer vertices below an inner
    layer in 1.5 (recomputed) and in the output (must be 0).
--chest TAG=OUTFIT: garment vertices inside the body as the game shows it in clothes (must be 0).
    TAG:SHAPE=OUTFIT as if the outfit carried chest shape tag SHAPE; TAG:notop=OUTFIT a normal refit checked
    against the uncovered chest (every Angel / EVE refit clears both chest shapes).
--replace-own TAG=OUTFIT: an outfit with its own TAG version: left alone without the option, refit into a '!!!'
    archive with it.
--motion TAG=OUTFIT: 'Make clothes move with the body': the chest bones move 2 cm, nothing may end up inside.
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
--support DIR --support-outfit OUTFIT (3x: two zips with r6\tweaks, then one more): 'Report Issues' tests in the
    real window on two fake games made in DIR (with / without ArchiveXL etc.), incl. a restart (previous session).
--gui-extra DIR --gui-extra-pasties OUTFIT [--gui-extra-pair OUTFIT] [--gui-extra-lone ARCHIVE]: in the real window:
    every button once, the two options with real refits (unticked / 0 = no change, room -2.0 / +6.0 mm, pasties
    -30 / +30 mm), wrong inputs (text file, empty folder, an already refit archive), an outfit whose refit needs
    its .xl (pair OUTFIT -> EVE) and the incomplete result (lone ARCHIVE without its .xl -> EVE).
Exit code 0 = everything passed."""

import glob, os, re, shutil, struct, sys, tempfile, traceback, zipfile


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


def check_install_rules(core):
    """In a fake game folder: a newer refit takes out the older '!!!' refit of the same outfit; uninstall keeps a
    file that changed since it was installed and says when one is already gone; file errors read plainly."""
    t = tempfile.mkdtemp(prefix="outfitrefit_inst_ ä ")
    data = core.DATA
    try:
        core.DATA = os.path.join(t, "data")
        game = os.path.join(t, "game")
        os.makedirs(os.path.join(game, "bin", "x64"))
        open(os.path.join(game, "bin", "x64", "Cyberpunk2077.exe"), "w").close()
        out = os.path.join(t, "out")
        os.makedirs(out)

        def made(name, body="x"):
            p = os.path.join(out, name)
            open(p, "w").write(body)
            return p

        msgs = []
        core.install([made("!!!AngelRefit_X.archive")], game, msgs.append, "X")
        core.install([made("Other.archive")], game, msgs.append, "Y")
        core.install([made("AngelRefit_X.archive")], game, msgs.append, "X")
        md = core.mod_dir(game)
        assert not os.path.exists(os.path.join(md, "!!!AngelRefit_X.archive")), msgs
        assert sorted(e["file"] for e in core.installed()) == [
            "AngelRefit_X.archive",
            "Other.archive",
        ]
        # another body's normal refit stays (each body uses its own); the same body's under a new name replaces it
        core.install([made("EveRefit_X.archive")], game, msgs.append, "X")
        core.install([made("AngelRefit_X_1234567.archive")], game, msgs.append, "X")
        assert sorted(e["file"] for e in core.installed()) == [
            "AngelRefit_X_1234567.archive",
            "EveRefit_X.archive",
            "Other.archive",
        ], core.installed()
        # an updated Nexus download (new version / time in its name) replaces the older refit (1.9)
        core.install(
            [made("AngelRefit_N_1_0_1700000000.archive")],
            game,
            msgs.append,
            "N-123-1-0-1700000000",
        )
        core.install(
            [made("AngelRefit_N.archive")], game, msgs.append, "N-123-1-1-1800000000"
        )
        assert "AngelRefit_N_1_0_1700000000.archive" not in [
            e["file"] for e in core.installed()
        ], msgs
        assert core.outfit_name("N-123-1-1-1800000000.zip") == "N"
        # REDmod installs count as in the game (1.9)
        os.makedirs(os.path.join(game, "mods", "Some", "archives"))
        open(
            os.path.join(game, "mods", "Some", "archives", "RedOne.archive"), "w"
        ).close()
        assert "redone.archive" in core.archives_in_game(game)
        core.uninstall(
            [e for e in core.installed() if e["file"] != "Other.archive"], msgs.append
        )
        core.install([made("AngelRefit_X.archive")], game, msgs.append, "X")
        open(os.path.join(md, "Other.archive"), "a").write("changed by someone else")
        os.remove(os.path.join(md, "AngelRefit_X.archive"))
        msgs = []
        core.uninstall(core.installed(), msgs.append)
        assert os.path.exists(os.path.join(md, "Other.archive")), msgs
        assert any("Other.archive: it changed since" in m for m in msgs) and any(
            "Already gone" in m for m in msgs
        ), msgs
        assert core.installed() == []
        # a refit with an .xl (materials for new mesh paths): both are installed, a newer refit without one takes the
        # old .xl out and stays itself, and removing one of the pair removes the other
        a = made("EveRefit_P.archive")
        made("EveRefit_P.xl")
        assert [os.path.basename(f) for f in core.refit_files(a)] == [
            "EveRefit_P.xl",
            "EveRefit_P.archive",
        ]
        core.install(core.refit_files(a), game, msgs.append, "P")
        assert sorted(e["file"] for e in core.installed()) == [
            "EveRefit_P.archive",
            "EveRefit_P.xl",
        ], core.installed()
        os.remove(os.path.join(out, "EveRefit_P.xl"))
        core.install(core.refit_files(a), game, msgs.append, "P")
        assert [e["file"] for e in core.installed()] == ["EveRefit_P.archive"], msgs
        made("EveRefit_P.xl")
        core.install(core.refit_files(a), game, msgs.append, "P")
        core.uninstall(
            [e for e in core.installed() if e["file"].endswith(".xl")], msgs.append
        )
        assert core.installed() == [] and not os.path.exists(
            os.path.join(md, "EveRefit_P.archive")
        ), msgs
        # an .xl of that name which this tool did not put into the game holds back the whole refit: the .archive is
        # never installed without its .xl, and the other file is never replaced
        a = made("EveRefit_Q.archive")
        made("EveRefit_Q.xl")
        with open(os.path.join(md, "EveRefit_Q.xl"), "w") as f:
            f.write("someone else's")
        msgs = []
        core.install(core.refit_files(a), game, msgs.append, "Q")
        assert not os.path.exists(os.path.join(md, "EveRefit_Q.archive")), msgs
        assert open(os.path.join(md, "EveRefit_Q.xl")).read() == "someone else's"
        assert core.installed() == [] and len(msgs) == 2 and "together" in msgs[1], msgs
        os.remove(os.path.join(md, "EveRefit_Q.xl"))
        # the game folder moved (another Steam library): uninstall finds the file in the new place
        core.install([made("Move.archive")], game, msgs.append, "M")
        moved = game + " moved"
        os.rename(game, moved)
        msgs = []
        core.uninstall(core.installed(), msgs.append, game=moved)
        assert not os.path.exists(os.path.join(core.mod_dir(moved), "Move.archive")), (
            msgs
        )
        assert core.installed() == [], core.installed()
        assert "Windows did not let" in core.explain(PermissionError(13, "x", "f"))
        assert "disk is full" in core.explain(OSError(28, "x", "f"))
        assert core.explain(ValueError()) is None
    finally:
        core.DATA = data
        shutil.rmtree(t, ignore_errors=True)


def check_tar(core):
    """Without 7-Zip, .7z downloads are opened with Windows' tar (only the asked files when 'only' is given), and
    game_root() finds the game folder from a folder one level off."""
    t = tempfile.mkdtemp(prefix="outfitrefit_tar_ ä ")
    real = core.find_7z
    try:
        src = os.path.join(t, "src")
        os.makedirs(os.path.join(src, "Vanilla ö", "archive"))
        for rel in (os.path.join("Vanilla ö", "archive", "a.archive"), "readme.txt"):
            open(os.path.join(src, rel), "w").write("x")
        z = os.path.join(t, "dl.7z")
        core.run([core.find_tar(), "-c", "--format", "7zip", "-f", z, "-C", src, "."])
        core.find_7z = lambda: None
        out = core.unpack(z, os.path.join(t, "all"))
        got = sorted(
            os.path.relpath(os.path.join(dp, f), out)
            for dp, _, fs in os.walk(out)
            for f in fs
        )
        assert got == sorted(
            [os.path.join("Vanilla ö", "archive", "a.archive"), "readme.txt"]
        ), got
        out = core.unpack(z, os.path.join(t, "only"), "*.archive")
        got = [f for _, _, fs in os.walk(out) for f in fs]
        assert got == ["a.archive"], got
        game = core.find_game()
        if game:
            assert core.game_root(os.path.join(game, "bin", "x64")) == game
            assert core.game_root(os.path.dirname(game)) in (game, None)
        assert core.game_root(t) is None
    finally:
        core.find_7z = real
        shutil.rmtree(t, ignore_errors=True)


def check_dotnet(core):
    """WolvenKit and .NET: a leftover DOTNET_ROOT pointing to an empty folder is worked around (the installed .NET 10
    is found and used); with no .NET 10 anywhere the message says what was found. Needs .NET 10 installed."""
    t = tempfile.mkdtemp(prefix="outfitrefit_net_")
    keys = (
        "DOTNET_ROOT",
        "DOTNET_ROOT_X64",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "LOCALAPPDATA",
        "USERPROFILE",
    )
    old = {k: os.environ.get(k) for k in keys}
    seen = []
    core.ensure_wolvenkit(seen.append)  # a fresh data folder: download it first
    try:
        os.environ["DOTNET_ROOT"] = t
        os.environ.pop("DOTNET_ROOT_X64", None)
        core._ENV.clear()
        core._wk_ready(core.wk_exe(), seen.append)
        assert any("uses the .NET 10 in" in m for m in seen), seen
        for k in keys[2:]:
            os.environ[k] = t
        core._ENV.clear()
        try:
            core._wk_ready(core.wk_exe(), seen.append)
            raise AssertionError("no error without .NET 10")
        except core.ToolError as e:
            assert ".NET found on this PC: none" in str(
                e
            ) and "DOTNET_ROOT setting" in str(e), e
            assert "What WolvenKit said" in str(e), e
        # a WolvenKit crash whose report names .NET's folder is not 'missing .NET' (1.7 said so with 10.0.0 found)
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        real = core.run

        def crash(args, game=None, ok=None):
            raise RuntimeError(
                "WolvenKit.CLI.exe --version failed:\nUnhandled exception. System.DllNotFoundException\n   at "
                r"C:\Program Files\dotnet\shared\Microsoft.NETCore.App\10.0.0\System.Private.CoreLib.dll"
            )

        core.run = crash
        try:
            core._wk_ready(core.wk_exe(), seen.append)
            raise AssertionError("no error for a crash")
        except core.ToolError as e:
            assert "does not start" in str(e) and "DllNotFoundException" in str(e), e
        finally:
            core.run = real
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        core._ENV.clear()
        shutil.rmtree(t, ignore_errors=True)


def check_rigs(core):
    """Rig / breast physics mods are found; other mods are not (stub archives: judged by their name)."""
    t = tempfile.mkdtemp(prefix="outfitrefit_rigs_")
    try:
        md = os.path.join(t, "archive", "pc", "mod", "Sub Ordner ä")
        os.makedirs(md)
        for f in (
            "All The Rigs - Breasts.archive",
            "Some Outfit.archive",
            "body_physics_v.archive",
            "readme.txt",
        ):
            open(os.path.join(md, f), "wb").write(b"stub")
        got = [os.path.basename(x) for x in core.rig_mods(t)]
        assert got == ["All The Rigs - Breasts.archive", "body_physics_v.archive"], got
        assert core.rig_mods(os.path.join(t, "none")) == []
        # by content: an archive that replaces V's rig counts, unless it is a ragdoll mod (how she falls, not her shape)
        real = core.rdar.Rdar

        class FakeRig:
            def __init__(self, p):
                self.names = []

            def hashes(self):
                return {core.rdar.fnv(core.V_RIGS[0])}

        md2 = os.path.join(t, "rig", "archive", "pc", "mod")
        os.makedirs(md2)
        for f in ("CrT_RagdollProfiles_Base.archive", "My Rig Replacer.archive"):
            open(os.path.join(md2, f), "wb").write(b"stub")
        core.rdar.Rdar = FakeRig
        try:
            got = [os.path.basename(x) for x in core.rig_mods(os.path.join(t, "rig"))]
        finally:
            core.rdar.Rdar = real
        assert got == ["My Rig Replacer.archive"], got
    finally:
        shutil.rmtree(t, ignore_errors=True)


def check_rigid(refit):
    """copy_weights leaves rigid parts alone: a part led by at most 2 bones is rigid, cloth led by many is not."""
    import numpy as np

    one = (np.zeros((50, 8), np.int64), np.tile([1.0, 0, 0, 0, 0, 0, 0, 0], (50, 1)))
    two = (
        np.tile([3, 4, 0, 0, 0, 0, 0, 0], (50, 1)),
        np.tile([0.6, 0.4, 0, 0, 0, 0, 0, 0], (50, 1)),
    )
    cloth = (
        np.arange(50)[:, None] % 12 + np.zeros((50, 8), np.int64),
        np.tile([1.0, 0, 0, 0, 0, 0, 0, 0], (50, 1)),
    )
    assert refit.rigid(*one) and refit.rigid(*two) and not refit.rigid(*cloth)


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


def tiny_mesh(appearances, shape=True):
    """The objects part of a made-up .mesh: a CMesh with that many appearances, with or without a render blob."""
    names = [
        "",
        "CMesh",
        "rendRenderMeshBlob",
        "meshMeshAppearance",
        "appearances",
        "array:handle",
    ]
    strings, offs = b"", []
    for n in names:
        offs.append(len(strings))
        strings += n.encode() + b"\0"
    body = b"\0"
    if appearances:
        body += struct.pack("<HHII", 4, 5, 8 + 4 * appearances, appearances)
        body += b"".join(struct.pack("<I", i + 2) for i in range(appearances))
    body += b"\0\0"
    classes = [1] + [2] * shape + [3] * appearances
    s_at = 160
    n_at = s_at + len(strings)
    e_at = n_at + 8 * len(names)
    d_at = e_at + 24 * len(classes)
    tabs = [(0, 0, 0)] * 10
    tabs[0], tabs[1], tabs[4] = (
        (s_at, len(strings), 0),
        (n_at, len(names), 0),
        (e_at, len(classes), 0),
    )
    head = (
        b"CR2W" + bytes(36) + b"".join(struct.pack("<III", *t) for t in tabs) + strings
    )
    head += b"".join(struct.pack("<II", o, 0) for o in offs)
    head += b"".join(
        struct.pack(
            "<HHIIIII", c, 0, 0, len(body), d_at if c == 1 else d_at + len(body), 0, 0
        )
        for c in classes
    )
    return head + body + b"\0\0\0"


def check_xl_patch(refit):
    r"""Outfits whose meshes get their appearances from an ArchiveXL patch (resource: patch: in the .xl): a refit at a
    new path must be added to the same patch, or it has no appearances and the game crashes when the item is put on
    (1.9 - 1.9.2: Zenitex Stealthsuit main\eve.mesh). The .xl is read the way ArchiveXL reads it, and the final
    check must refuse a mesh without appearances that no patch fills in."""
    t = tempfile.mkdtemp(prefix="bodyrefit_xl_")
    try:
        arc = os.path.join(t, "a.archive")
        with open(os.path.join(t, "a.xl"), "w", encoding="utf-8") as f:
            f.write(
                "v: 2024-02-31\n"  # not a date: ArchiveXL reads every value as text
                "resource:\n"
                "  scope:\n"
                "    my\\all: [my\\legs_base_body.mesh, my\\more]\n"
                "    my\\more: my\\arms_base_body.mesh\n"
                "  patch:\n"
                "    my\\mat\\suit.mesh:\n"
                "      - my\\Main\\base_body.mesh\n"
                "    my\\mat\\legs.mesh: {props: [appearances], targets: [my\\all], order: 5}\n"
                "    my\\mat\\not.mesh: !exclude [my\\main\\base_body.mesh]\n"
                "    my\\mat\\suit.mesh: my//main/ebb.mesh\n"  # the same key again: both lists count
                "    my\\mat\\blob.mesh: {props: [renderResourceBlob], targets: [my\\main\\base_body.mesh]}\n"
                "    my\\mat\\all.mesh: {props: appearances, targets: [my\\main\\base_body.mesh]}\n"
                "  link:\n"
                "    my\\main\\ebb.mesh:\n"
                "      - my\\main\\angel.mesh\n"
                "    my\\main\\evb.mesh: my\\main\\vhd.mesh\n"  # one value: the key is the other name
                "  fix:\n"
                "    my\\main\\base_body.mesh: {names: {0010: 1e3, yes: no}}\n"
            )
        with open(
            os.path.join(t, "!!!EveRefit_a.xl"), "w"
        ) as f:  # an earlier refit's own file: not the outfit's
            f.write(
                "resource:\n  patch:\n    my\\mat\\suit.mesh: [my\\main\\eve.mesh]\n"
            )
        with open(
            os.path.join(t, "other.xl"), "w"
        ) as f:  # another mod's, in the same folder
            f.write(
                "resource:\n  patch:\n    their\\mat.mesh: [their\\x_base_body.mesh]\n"
            )
        with open(
            os.path.join(t, "extra.xl"), "wb"
        ) as f:  # Windows-1252, names one of the outfit's meshes
            f.write(
                "resource:\n  patch:\n    my\\mät.mesh: [MY/Hat_base_body.mesh]\n".encode(
                    "cp1252"
                )
            )
        base, eve = r"my\main\base_body.mesh", r"my\main\eve.mesh"
        hat = r"my\hat_base_body.mesh"
        assert len(refit.xl_files([arc])) == 3, (
            "every .xl of the folder but the tool's own"
        )
        files = refit.xl_files([arc], {base, hat})
        assert [os.path.basename(f) for f in files] == ["a.xl", "extra.xl"], files
        info = refit.xl_info(files)
        src = lambda p: sorted(refit.xl_sources(info, p))
        assert info["files"] == 2 and not info["unreadable"], info
        assert src(base) == [r"my\mat\all.mesh", r"my\mat\suit.mesh"], (
            "a patch that changes other things only gives no appearances; a 'props' that is no list is ignored",
            src(base),
        )
        assert src(r"my\main\ebb.mesh") == [r"my\mat\suit.mesh"], (
            "a key written twice counts twice"
        )
        assert (
            src(r"my\legs_base_body.mesh")
            == src(r"my\arms_base_body.mesh")
            == [r"my\mat\legs.mesh"]
        )
        assert src(hat) == ["my\\m\xe4t.mesh"] and not src(eve), (src(hat), src(eve))
        assert info["link"] == {
            r"my\main\angel.mesh": r"my\main\ebb.mesh",
            r"my\main\evb.mesh": r"my\main\vhd.mesh",
        }, info["link"]
        text, new = refit.xl_extra(
            info,
            [
                (base, eve),
                (r"my\legs_base_body.mesh", r"my\legs_eve.mesh"),
                (hat, r"my\hat_eve.mesh"),
                (r"my\x.mesh", r"my\x.mesh"),
            ],
        )
        assert "m\xe4t.mesh" in text, "letters are written as they are"
        again = os.path.join(t, "out")
        os.makedirs(again)
        with open(os.path.join(again, "b.xl"), "w", encoding="utf-8") as f:
            f.write(text)
        back = refit.xl_info(refit.xl_files([os.path.join(again, "b.archive")]))
        assert new == {eve, r"my\legs_eve.mesh", r"my\hat_eve.mesh"}, new
        assert sorted((s, sorted(tg), p, o) for s, tg, p, o in back["patch"]) == [
            (r"my\mat\all.mesh", [eve], None, None),
            (r"my\mat\blob.mesh", [eve], ("renderResourceBlob",), None),
            (r"my\mat\legs.mesh", [r"my\legs_eve.mesh"], ("appearances",), "5"),
            (r"my\mat\suit.mesh", [eve], None, None),
            ("my\\m\xe4t.mesh", [r"my\hat_eve.mesh"], None, None),
        ], back["patch"]
        assert back["fix"] == {eve: {"names": {"0010": "1e3", "yes": "no"}}}, back[
            "fix"
        ]
        assert refit.xl_extra(info, [(base, base)]) == (None, set()), (
            "a mesh that keeps its path needs nothing"
        )
        # a broken .xl is named and passed over, the others still count; no .xl at all is another reason
        with open(os.path.join(t, "a.archive.xl"), "w") as f:
            f.write("resource:\n\tpatch:\n\t\tmy\\mat\\suit.mesh: [x]\n")
        said = []
        info = refit.xl_info(refit.xl_files([arc], {base}), said.append)
        assert (
            info["unreadable"] == ["a.archive.xl"] and info["files"] == 1 and src(base)
        ), info
        assert any(
            "a.archive.xl" in m for m in said
        ) and "a.archive.xl" in refit.xl_blind(info), said
        none = refit.xl_info(
            refit.xl_files([os.path.join(again, "nothing.archive")], {base})
        )
        assert "not next to the .archive" in refit.xl_blind(none), refit.xl_blind(none)
        assert refit.xl_blind(back) is None
    finally:
        shutil.rmtree(t, ignore_errors=True)
    assert refit.norm_path('"A//B\\\\C/\xc4.Mesh"') == "a\\b\\c\\\xc4.mesh", (
        "as the game hashes paths: A-Z only"
    )
    bare, full, mats = (
        (tiny_mesh(0), []),
        (tiny_mesh(2), []),
        tiny_mesh(22, shape=False),
    )
    assert refit.mesh_facts(full[0])[1]["appearances"] == 2
    assert "rendRenderMeshBlob" not in refit.mesh_facts(mats)[0], (
        "a materials-only mesh has no shape"
    )
    why = refit.mesh_gate("eve.mesh", bare, bare, False)  # what 1.9.2 handed out
    assert why and "no appearances" in why, why
    assert refit.mesh_gate("eve.mesh", bare, bare, True) is None, (
        "in an .xl patch list: fine"
    )
    assert refit.mesh_gate("x.mesh", full, full, False) is None, "own appearances: fine"
    why = refit.mesh_gate("x.mesh", bare, full, True)
    assert why and "appearances 0 (original 2)" in why, why
    meshes = {
        r"a\m_base_body.mesh",
        r"a\w_base_body.mesh",
        r"a\vest_m.mesh",
        r"a\vest_w.mesh",
        r"a\m.mesh",
        r"a\t1_001_ma_jacket.mesh",
        r"a\t1_001_wa_jacket.mesh",
        r"a\dress_s.mesh",
        r"a\dress_m.mesh",
        r"a\dress_l.mesh",
        r"a\dress_w.mesh",
    }
    male = sorted(k for k in meshes if refit.is_male(k, meshes))
    assert male == [
        r"a\m_base_body.mesh",
        r"a\t1_001_ma_jacket.mesh",
        r"a\vest_m.mesh",
    ], male
    assert refit.is_male(r"b\m_gymfiend.mesh", set()) and refit.is_male(
        r"b\t0_000_pma_base.mesh", set()
    )
    assert not refit.is_male(r"b\arm_base_body.mesh", set()) and not refit.is_male(
        r"b\puma_top.mesh", set()
    )


def xl_outfit(wk, game, core, where):
    r"""A small made-up outfit of the kind that crashed: selftest\xl\suit_base_body.mesh has no appearances of its own
    (the game's left arm with its appearances taken out), selftest\xl\suit_materials.mesh holds them, and the .xl
    patches them in. selftest\xl\empty_materials.mesh is a patch file without appearances (for the .xl that points
    to the wrong file). -> the folder to refit (archive\pc\mod\selftest_xl.archive + .xl inside)."""
    import json

    arm = core.bodies()["meshes"][1]
    ub, js, pk = (os.path.join(where, x) for x in ("ub", "js", "pk"))
    for s in core.vanilla_sources(game):
        core.run([wk, "unbundle", s, "-o", ub, "-r", "^" + re.escape(arm) + "$"], game)
    mesh = os.path.join(ub, arm)
    assert os.path.isfile(mesh), "the game's arm mesh was not found"
    os.makedirs(js)
    core.run([wk, "convert", "serialize", mesh, "-o", js], game)
    jf = os.path.join(js, os.path.basename(mesh) + ".json")
    d = json.load(open(jf, encoding="utf-8"))
    assert d["Data"]["RootChunk"]["appearances"], "the arm mesh has appearances"
    d["Data"]["RootChunk"]["appearances"] = []
    json.dump(d, open(jf, "w", encoding="utf-8"))
    out = os.path.join(pk, "selftest", "xl")
    os.makedirs(out)
    core.run([wk, "convert", "deserialize", jf, "-o", js], game)
    bare = os.path.join(js, os.path.basename(mesh))
    shutil.copy(bare, os.path.join(out, "suit_base_body.mesh"))
    shutil.copy(bare, os.path.join(out, "empty_materials.mesh"))
    shutil.copy(mesh, os.path.join(out, "suit_materials.mesh"))
    mod = os.path.join(where, "outfit", "archive", "pc", "mod")
    os.makedirs(mod)
    core.run([wk, "pack", pk, "-o", mod], game, ok="Finished packing")
    made = glob.glob(os.path.join(mod, "*.archive"))
    assert len(made) == 1, made
    os.rename(made[0], os.path.join(mod, "selftest_xl.archive"))
    xl_outfit_xl(os.path.join(where, "outfit"), "suit_materials.mesh")
    return os.path.join(where, "outfit")


def xl_outfit_xl(root, patch_file):
    """(Re)write the made-up outfit's .xl: the suit gets its materials from selftest\\xl\\<patch_file>."""
    with open(os.path.join(root, "archive", "pc", "mod", "selftest_xl.xl"), "w") as f:
        f.write(
            "resource:\n  patch:\n    selftest\\xl\\%s:\n      - selftest\\xl\\suit_base_body.mesh\n"
            % patch_file
        )


def xl_case(core, rdar, wk, game, tags, barch, out, log, fails):
    r"""The whole way (refit_mod + the final check) on outfits whose meshes get their appearances from an .xl patch:
    a made-up one (xl_outfit), and the Zenitex Stealthsuit download when it is in Downloads and EVE is set up.
    The new mesh must come with an .xl that puts it into a patch whose file has appearances; the final check must
    have been asked about appearances, must count the new mesh as patched, and must refuse the same archive when
    nothing is patched; the same outfit as a lone .archive (its .xl not beside it) must leave that mesh out and say
    that the refit is incomplete, instead of handing out a mesh that crashes the game. With the made-up outfit also:
    a patch file without appearances gives none (mesh left out), and a patch file that is nowhere (another mod's)
    is named in the log while the mesh is still refit."""
    import refit

    def check(name, cond, detail=""):
        log(
            "  %s %s%s"
            % (
                "OK  " if cond else "FAIL",
                name,
                " (%s)" % (detail,) if detail and not cond else "",
            )
        )
        if not cond:
            fails.append("xl case: " + name)
        return cond

    work = tempfile.mkdtemp(prefix="xl_", dir=core._work_root())
    calls, real, seen = [], refit.buffer_gate, []
    say = lambda m: (seen.append(m), log("    | " + m))

    def gate(arch, src_of, patched=None):
        calls.append(
            (patched, real(arch, src_of, set()))
        )  # the same archive with nothing counted as patched
        return real(arch, src_of, patched)

    def refit_it(inp, tag, dest):
        seen[:], calls[:] = [], []
        r = core.run_batch(
            [inp],
            tag,
            game,
            os.path.join(out, dest),
            say,
            body_files=barch,
            src="vanilla",
        )[0]
        names = [n.lower() for n in rdar.Rdar(r[1]).names] if r[1] else []
        return r[1], r[2], names

    def whole(what, root, tag, new_end):
        log("\n### .xl patch case: %s -> %s" % (what, tag))
        orig = glob.glob(os.path.join(root, "**", "*.archive"), recursive=True)[0]
        arch, msg, names = refit_it(root, tag, "xl")
        if not check("refit made", bool(arch) and msg == "ok", msg):
            return
        new = [n for n in names if n.endswith(new_end)]
        check("the new mesh is in the refit", len(new) == 1, names)
        xl = core.xl_beside(arch)
        if check("the refit comes with its .xl", os.path.isfile(xl)) and new:
            srcs = refit.xl_sources(refit.xl_info([xl]), new[0])
            head = srcs and rdar.find_file([orig], srcs[0])[0]
            check(
                "its .xl puts the new mesh into a patch whose file has appearances",
                bool(head) and refit.mesh_facts(head)[1]["appearances"] > 0,
                srcs,
            )
        check(
            "the final check was asked about appearances",
            bool(calls) and calls[-1][0] is not None,
        )
        if calls and new:
            check(
                "the final check counts the new mesh as patched",
                new[0] in (calls[-1][0] or ()),
                calls[-1][0],
            )
            check(
                "the final check refuses this archive when nothing is patched",
                any("no appearances" in b for b in calls[-1][1]),
                calls[-1][1],
            )
        # the same outfit as a lone .archive: its .xl is not there, so the mesh can't be given its materials
        lone = os.path.join(work, "lone_" + tag, os.path.basename(orig))
        os.makedirs(os.path.dirname(lone))
        shutil.copy(orig, lone)
        arch, msg, names = refit_it(lone, tag, "xl_lone")
        check(
            "a lone .archive: the result says what is missing",
            msg.startswith(("incomplete: ", "nothing could be refit: "))
            and ".xl" in msg,
            msg,
        )
        check(
            "a lone .archive: the mesh that would crash is left out",
            not [n for n in names if n.endswith(new_end)],
            names,
        )
        check(
            "a lone .archive: no .xl is written",
            not arch or not os.path.isfile(core.xl_beside(arch)),
        )

    try:
        refit.buffer_gate = gate
        tag = tags[0]
        root = xl_outfit(wk, game, core, work)
        new_end = "suit_%s.mesh" % core.xl_name(tag)
        whole("made-up outfit", root, tag, new_end)
        log("\n### .xl patch case: the patch file holds no appearances")
        xl_outfit_xl(root, "empty_materials.mesh")
        arch, msg, names = refit_it(root, tag, "xl_empty")
        check(
            "the mesh is left out", not [n for n in names if n.endswith(new_end)], names
        )
        check("the log says why", any("holds none" in m for m in seen), msg)
        log(
            "\n### .xl patch case: the patch file is another mod's (nowhere to be found)"
        )
        xl_outfit_xl(root, "nowhere.mesh")
        arch, msg, names = refit_it(root, tag, "xl_other")
        check(
            "the mesh is refit",
            bool([n for n in names if n.endswith(new_end)]) and msg == "ok",
            msg,
        )
        check(
            "the log names the file that is needed",
            any("Note:" in m and r"selftest\xl\nowhere.mesh" in m for m in seen),
        )
        if arch and os.path.isfile(core.xl_beside(arch)):
            check(
                "its .xl names that patch file for the new mesh",
                refit.xl_sources(
                    refit.xl_info([core.xl_beside(arch)]), "selftest\\xl\\" + new_end
                )
                == [r"selftest\xl\nowhere.mesh"],
            )
        else:
            check("the refit comes with its .xl", False)
        zips = glob.glob(
            os.path.join(
                os.path.expanduser("~"), "Downloads", "Zenitex Stealthsuit-16172-*.zip"
            )
        )
        if zips and "eve" in tags:
            root = core.unpack(zips[0], os.path.join(work, "stealth"))
            whole(os.path.basename(zips[0]), root, "eve", r"\main\eve.mesh")
    finally:
        refit.buffer_gate = real
        shutil.rmtree(work, ignore_errors=True)


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
        """The refit's files the log says were installed: its .archive, and its .xl when it has one (a pair)."""
        return sorted(
            os.path.join("archive", "pc", "mod", m.split(": ", 1)[1]).lower()
            for m in lines
            if m.startswith("Installed into the game: ")
        )

    last = None
    for spec in opt_all(a, "--gui-install-outfit"):
        inp, _, want = spec.partition("=>")
        want = sorted(w.lower() for w in want.split(";") if w)
        name = core.shown_name(inp)
        last = (inp, want)
        log("\n## %s" % name)
        s0 = snap(game)
        # 3. Install unticked, original missing: nothing goes into the game, warning in the log and the summary
        st = gui.drive(
            inp,
            "angel",
            out,
            log,
            game=game,
            install=False,
            body_file=body,
            make_zip=True,
        )
        warn = core.MISSING_TEXT % name
        check("unticked: refit finished", st["ok"])
        # zip ticked: a zip with the refit at archive/pc/mod/ for Vortex / MO2, next to the refit
        zl = [m.split(": ", 1)[1] for m in st["log"] if m.startswith("Zip for Vortex")]
        zn = []
        if zl:
            with zipfile.ZipFile(os.path.join(out, zl[0])) as z:
                zn = z.namelist()
        made = (
            core.refit_files(os.path.join(out, zl[0][:-4] + ".archive")) if zl else []
        )
        check(
            "zip: has the refit (with its .xl when it has one) at archive/pc/mod/",
            bool(zl)
            and sorted(zn)
            == sorted("archive/pc/mod/" + os.path.basename(f) for f in made)
            and os.path.isfile(made[-1]),
            "%s %s" % (zl, zn),
        )
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
        # the refit in the game is the refit as it was made: the .archive, and its .xl when it has one
        check(
            "ticked: the refit went in whole (its .xl too when it has one)",
            [os.path.basename(r) for r in rr]
            == sorted(os.path.basename(f).lower() for f in made),
            "%s vs %s" % (rr, made),
        )
        check(
            "ticked: game got exactly the refit + the original",
            new_files(s0) == sorted(want + rr),
            "%s vs %s" % (new_files(s0), want + rr),
        )
        lst = core.installed()
        kinds = sorted((e["rel"].lower(), e["kind"]) for e in lst)
        check(
            "ticked: install list = refit + originals",
            kinds
            == sorted([(r, "refit") for r in rr] + [(w, "original") for w in want]),
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
        check(
            "present: only the refit was added",
            bool(rr) and new_files(s1) == rr,
            new_files(s1),
        )
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
    # 5c. 'Replace the outfit's own version' ticked in the window (an outfit with its own Angel version)
    if opt(a, "--gui-source-own"):
        ow = os.path.join(inp, os.path.basename(opt(a, "--gui-source-own")))
        shutil.copy2(opt(a, "--gui-source-own"), ow)
        b = set(os.listdir(out))
        st = gui.drive(
            ow,
            "angel",
            out,
            log,
            game=game,
            src="vanilla",
            body_files=barch,
            replace_own=True,
        )
        new = made("!!!AngelRefit_", b)
        check(
            "replace own version ticked: the outfit's own Angel parts refit into a '!!!' archive",
            st["ok"]
            and len(new) == 1
            and has(st, "replacing the outfit's own Hyst Angel version"),
            (st["errors"], new),
        )
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
    # 8. the 'Stop' button while refitting
    out3 = os.path.join(base, "Ausgabe Stopp ö")
    os.makedirs(out3, exist_ok=True)
    st = gui.drive(
        outfit,
        "vanilla",
        out3,
        log,
        game=game,
        src="angel",
        body_files=barch,
        stop_on="=== ",
    )
    check("Stop: the button said 'Stop' while refitting", st.get("stopped"))
    check(
        "Stop: the refit ended, the button says 'Refit' again",
        st.get("button") == "Refit",
        st.get("button"),
    )
    check("Stop: the log says it stopped", has(st, "Stopped"))
    check("Stop: no file left behind", os.listdir(out3) == [], os.listdir(out3))
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
    into a fake game in DIR, cause an error, click 'Report Issues' and check the report; then restart the
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
        find_button(root, "Report Issues").invoke()
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
            "clicked 'Report Issues'",
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
            "05_Hyst_Angel_BodyMod.archive (Hyst Angel, could not be read)" in game_a,
        )
        check(
            "report A: this PC (WolvenKit start test, antivirus, folders, unpacking, store)",
            # any version: the self-test also runs with a picked WolvenKit (10.0 beta)
            re.search(r"WolvenKit start test: \d+\.\d+\.\d+", A)
            and all(
                s in A
                for s in (
                    "Antivirus: ",
                    "Data folder: writable yes",
                    "Unpacking .7z / .rar: ",
                    "Game store files: ",
                    "Smart App Control: ",
                )
            ),
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


def stacked(pos, end):
    """All positions of the mesh in pos ({mesh path: [positions per primitive]}) whose name ends with end."""
    import numpy as np

    k = [x for x in pos if x.endswith(end)]
    assert len(k) == 1, (end, k)
    return np.vstack(pos[k[0]])


def layer_test(core, wk, game, spec, run_case, seen, barch, log, fails):
    """--layers TAG=OUTFIT=STEM,STEM,...: refit a vanilla outfit with garment layers (stems inner -> outer, e.g.
    casual_pt5_top,casual_pt5_top1,casual_pt5_jacket) and count outer-layer vertices below an inner layer: in the
    1.5 refit (move() alone, recomputed from the original) and in the output (expected 0). Also how far each
    garment moved compared with 1.5 (innermost: unchanged)."""
    import numpy as np
    import refit

    tag, inp, stems = spec.split("=")
    stems = stems.split(",")
    log("\n### garment layers, vanilla -> %s: %s" % (tag, os.path.basename(inp)))
    _, cages = core.setup(game, "vanilla", tag, log, body_files=barch)
    work = tempfile.mkdtemp(prefix="lay_", dir=core._work_root())
    try:
        arch = run_case("vanilla", tag, inp, os.path.join(work, "out"))
        if not arch:
            return
        ok = any(m.startswith("Garment layers (inner first): ") for m in seen)
        log("  %s layers found in the outfit's files" % ("OK  " if ok else "FAIL"))
        orig = core.prepare_input(inp, os.path.join(work, "in"), lambda m: None)
        src = {}
        for o in orig:
            src.update(positions(wk, game, o, os.path.join(work, "p_src")))
        dst = positions(wk, game, arch, os.path.join(work, "p_out"))
        xl = core.xl_name(tag)
        old = [
            refit.move(
                stacked(src, s + "_w_base_body.mesh"), cages["vanilla"], cages[tag]
            )[0]
            for s in stems
        ]
        new = [stacked(dst, s + "_w_%s.mesh" % xl) for s in stems]
        for s, b, n in zip(stems, old, new):
            d = np.linalg.norm(n - b, axis=1) * 1000
            h = lambda X: np.median(refit.skin_height(X, cages[tag])[0]) * 1000
            log(
                "  %-28s vs 1.5: moved max %.2f mm, mean %.3f mm, %d verts > 0.1 mm; median height over the skin "
                "%.1f -> %.1f mm" % (s, d.max(), d.mean(), (d > 0.1).sum(), h(b), h(n))
            )
        bad = 0
        for i in range(len(stems)):
            for o in range(i + 1, len(stems)):
                c0, b0 = refit.crossings(old[i], old[o], cages[tag])
                c1, b1 = refit.crossings(new[i], new[o], cages[tag])
                bad += b1
                log(
                    "  %s over %s: below the inner layer in 1.5 %d of %d (%.1f%%), in 1.6 %d of %d"
                    % (stems[o], stems[i], b0, c0, 100.0 * b0 / max(c0, 1), b1, c1)
                )
        if not ok or bad:
            log("FAIL: garment layers still cross (or were not found)")
            fails.append("layers " + os.path.basename(inp))
        else:
            log("  garment layers OK: no outer layer below an inner one")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def chest_test(core, wk, game, spec, run_case, barch, log, fails):
    """--chest TAG=OUTFIT: refit a vanilla outfit to TAG; no garment vertex may be inside the body as the game shows it
    in clothes (the full body mesh as it is; for Angel the same with every chest size), triangle-exact, within 6 cm of
    the skin. Prints the fit on the chest."""
    import numpy as np
    import refit

    # TAG:SHAPE (e.g. angel:hyst_boobs_squish): the outfit is treated as carrying that chest shape tag, so the body to
    # check against is the body with that chest add-on, and the log must say the shape is used
    tag, inp = spec.split("=", 1)
    tag, _, shape = tag.partition(":")
    no_top = (
        shape == "notop"
    )  # a normal refit checked against the body's uncovered chest: every refit must clear it too
    if no_top:
        shape = core.bodies()["bodies"][tag].get("uncovered", "")
    log(
        "\n### clothed chest, vanilla -> %s%s: %s"
        % (tag, " (%s)" % shape if shape else "", os.path.basename(inp))
    )
    _, cages = core.setup(game, "vanilla", tag, log, body_files=barch)
    body = core.shape_cage(cages[tag], tag, shape) if shape else cages[tag]
    if shape and body == cages[tag]:
        log("FAIL: %s has no %s shape" % (tag, shape))
        fails.append("chest shape " + shape)
        return
    work = tempfile.mkdtemp(prefix="chest_", dir=core._work_root())
    real = refit.mesh_shape
    try:
        if (
            shape and not no_top
        ):  # every mesh of the outfit as if its .app carried the tag
            refit.mesh_shape = lambda tags, rel, xl: shape
        seen = []
        arch = run_case("vanilla", tag, inp, os.path.join(work, "out"), seen)
        if no_top and not any(
            "it to both of" in m or "worn next to the skin on the chest" in m
            for m in seen
        ):
            log("FAIL: the log doesn't say both chest shapes are used")
            fails.append("chest both log")
        if (
            shape
            and not no_top
            and not any("switches the chest to its" in m for m in seen)
        ):
            log("FAIL: the log doesn't say the chest shape is used")
            fails.append("chest shape log " + shape)
        if not arch:
            fails.append("chest " + os.path.basename(inp))
            return
        pos = positions(wk, game, arch, os.path.join(work, "p"))
        bad = 0
        for name, parts in sorted(pos.items()):
            P = np.vstack(parts)
            h = refit.skin_height(P, body)[0]
            # feet left out: the body shape has flat feet, a shoe for raised feet overlaps them by design
            near = (np.abs(h) < 0.06) & (P[:, 1] >= refit.FEET_Y)
            band = near & (P[:, 1] > 1.22) & (P[:, 1] < 1.45)
            bad += int((h[near] < 0).sum())
            log(
                "  %-44s inside %d of %d near the body%s"
                % (
                    os.path.basename(name),
                    int((h[near] < 0).sum()),
                    int(near.sum()),
                    "; chest gap median %.1f mm, 90th pct %.1f mm"
                    % (np.median(h[band]) * 1000, np.percentile(h[band], 90) * 1000)
                    if band.sum() > 100
                    else "",
                )
            )
        # nipples: on Angel / EVE the clothing over the breast tips keeps the extra room (nipples poked through at 4 mm)
        room = refit.nipple_room(body)
        if room:
            from scipy.spatial import cKDTree

            allp = np.vstack([np.vstack(parts) for parts in pos.values()])
            hs, qs, _ = refit.skin_height(allp, body)
            tree = cKDTree(allp)
            for tip in refit.breast_tips(body):
                idx = np.array(
                    [i for i in tree.query_ball_point(tip, 0.03) if abs(hs[i]) < 0.03],
                    int,
                )
                if not len(idx):
                    continue
                dq = np.linalg.norm(qs[idx] - tip, axis=1)
                need = 0.004 + room * np.clip(1 - (dq / 0.03) ** 2, 0, 1)
                low = int((hs[idx] < need - 0.0005).sum())
                log(
                    "  around the breast tip: %d points, the closest %.1f mm over the skin; below the nipple room: %d"
                    % (len(idx), hs[idx][np.argmin(dq)] * 1000, low)
                )
                bad += low
        # the squashed shape (GarmentSupport morph) the game shows while a layer is worn over the garment
        for g in glob.glob(
            os.path.join(work, "p", "glb", "**", "*.glb"), recursive=True
        ):
            d, j, b = refit.load(g)
            for mm in j["meshes"]:
                names = (mm.get("extras") or {}).get("targetNames", [])
                if (
                    "LOD_1" not in (mm.get("name") or "")
                    or "GarmentSupport" not in names
                ):
                    continue
                for pr in mm["primitives"]:
                    if not pr.get("targets"):
                        continue
                    P = refit.read(d, j, b, pr["attributes"]["POSITION"])
                    D = refit.read(
                        d,
                        j,
                        b,
                        pr["targets"][names.index("GarmentSupport")]["POSITION"],
                    )
                    h = refit.skin_height(P + D, body)[0]
                    n_in = int(((np.abs(h) < 0.06) & (h < 0)).sum())
                    bad += n_in
                    log(
                        "  %-44s squashed shape (garment support): inside %d"
                        % (os.path.basename(g)[:44], n_in)
                    )
        if refit.ENGINE == 2:
            # fit 2.0 keeps the author's heights (no 4 mm floor, no radial nipple room) and leaves hidden parts
            # hidden, so the 1.9 counts above are for information. What must hold, measured here from the files and
            # not taken from the engine's own fit report: a vertex that was clearly above the source body (0.5 mm)
            # is not inside the body the game shows (0.1 mm), triangle-exact, within 2 cm of the skin. An inner
            # chest garment is checked against the normal body (the game shows that under a chest item).
            log("  (1.9 rule, for information: %d below its floors / inside the body mesh)" % bad)
            shown_body = cages[tag] if no_top and any("worn next to the skin" in m for m in seen) else body
            spos = {}
            for k, arc in enumerate(core.prepare_input(inp, os.path.join(work, "srcin"), lambda m: None)):
                spos.update(positions(wk, game, arc, os.path.join(work, "sp%d" % k)))
            sx, tx = core.xl_name("vanilla"), core.xl_name(tag)
            bad = cmp = meshes = 0
            for name, parts in sorted(pos.items()):
                sp = spos.get(name) or spos.get(name.replace("_%s.mesh" % tx, "_%s.mesh" % sx))
                if sp is None or [len(x) for x in sp] != [len(x) for x in parts]:
                    continue
                h0 = refit.skin_height(np.vstack(sp), cages["vanilla"])[0]
                h1 = refit.skin_height(np.vstack(parts), shown_body)[0]
                was = (h0 > 0.0005) & (h0 < 0.02) & (np.abs(h1) < 0.02)
                n_in = int((was & (h1 < -0.0001)).sum())
                meshes, cmp, bad = meshes + 1, cmp + int(was.sum()), bad + n_in
                if n_in:
                    log("    %-44s %d of %d vertices that were above the source skin are inside the shown body" % (os.path.basename(name)[:44], n_in, int(was.sum())))
            log("  own measurement: %d mesh(es), %d vertices near the skin compared, inside the shown body: %d" % (meshes, cmp, bad))
            if not meshes:
                # the source archive has no file names inside (old packing): its meshes can't be matched by name.
                # Then the engine's own fit report is all there is; the log says so.
                rx = re.compile(r"fit: ([0-9.]+) % inside")
                vals = [float(m.group(1)) for line in seen for m in [rx.search(line)] if m]
                bad = sum(v > 0.05 for v in vals) if vals else 1
                log("  (source meshes can't be matched by name: judged by the fit reports of %d mesh(es), most inside %.1f %%)" % (len(vals), max(vals, default=0)))
            elif bad <= cmp // 2000:  # up to 1 in 2,000: measurement noise at folds of the garment itself
                bad = 0
        if bad:
            log("FAIL: %d garment vertices inside the clothed body" % bad)
            fails.append("chest " + os.path.basename(inp))
        else:
            log("  clothed chest OK: nothing inside the body")
    finally:
        refit.mesh_shape = real
        shutil.rmtree(work, ignore_errors=True)


def own_test(core, rdar, wk, game, spec, barch, log, fails):
    """--replace-own TAG=OUTFIT (an outfit that ships its own TAG version of {body} parts): without the option those
    parts are left alone and the log says how to refit them anyway; with it they are refit into a '!!!' archive
    (loads before the outfit) that passes the safety check. Prints how far the refit is from the author's version."""
    import refit

    tag, inp = spec.split("=")
    xl = core.xl_name(tag)
    log("\n### replace the outfit's own %s version: %s" % (tag, os.path.basename(inp)))
    names = set(rdar.Rdar(inp).names)
    own = sorted(
        n
        for n in names
        if n.endswith("_%s.mesh" % xl) and n[: -len(xl) - 5] + "base_body.mesh" in names
    )
    log("  the outfit's own %s parts: %d" % (tag, len(own)))
    work = tempfile.mkdtemp(prefix="own_", dir=core._work_root())

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
            fails.append("replace own: " + name)

    try:
        for on in (False, True):
            lines = []
            res = core.run_batch(
                [inp],
                tag,
                game,
                os.path.join(work, str(on)),
                lines.append,
                body_files=barch,
                replace_own=on,
            )
            arch = res[0][1]
            got = set(rdar.Rdar(arch).names) if arch else set()
            if not on:
                check(
                    "option off: own parts left alone, log says how to refit them",
                    own
                    and not (got & set(own))
                    and any(
                        "tick 'Replace the outfit's own version'" in l for l in lines
                    ),
                    sorted(got & set(own)),
                )
                continue
            check(
                "option on: own parts refit into a '!!!' archive, safety check OK",
                arch
                and os.path.basename(arch).startswith("!!!")
                and set(own) <= got
                and not refit.buffer_gate(arch, {})
                and any("replacing the outfit's own" in l for l in lines),
                (arch, sorted(set(own) - got)),
            )
            if arch:
                show_compare(wk, game, arch, inp, log, "the author's own version in")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def motion_test(core, wk, game, spec, barch, log, fails):
    """--motion TAG=OUTFIT: 'Make clothes move with the body', offline. Refit OUTFIT with the option off and on, move
    the body's chest bones 2 cm outward (as breast physics / rig mods do) and move body and clothes by their skin
    weights (linear blend skinning, translations only). With the option on, no clothing vertex may end up inside the
    moved body (nearest body vertex along its normal, within 6 cm), and rigid parts (one or two bones) keep their
    weights."""
    import numpy as np
    import refit
    from scipy.spatial import cKDTree

    tag, inp = spec.split("=")
    log(
        "\n### clothes move with the body, vanilla -> %s: %s"
        % (tag, os.path.basename(inp))
    )
    _, cages = core.setup(game, "vanilla", tag, log, body_files=barch)
    P, N, _, _ = refit.surface(cages[tag])
    bnames, BJ, BW, _ = refit.body_skin(cages[tag])
    Tj = np.zeros((len(bnames), 3))
    for i, nm in enumerate(bnames):
        if "chest" in nm.lower():
            w = (BW * (BJ == i)).sum(1)
            if (w > 0.3).any():
                nn = (N[w > 0.3] * w[w > 0.3, None]).sum(0)
                Tj[i] = 0.02 * nn / np.linalg.norm(nn)
    log("  chest bones moved 2 cm: %d" % int((np.abs(Tj).sum(1) > 0).sum()))
    moved = P + (BW[..., None] * Tj[BJ]).sum(1)
    tree0, tree1 = cKDTree(P), cKDTree(moved)

    def inside(
        X, tree, B
    ):  # feet left out: shoes for raised feet overlap the flat feet by design
        dist, k = tree.query(X)
        return int(
            (
                (((X - B[k]) * N[k]).sum(1) < 0)
                & (dist < 0.06)
                & (X[:, 1] >= refit.FEET_Y)
            ).sum()
        )

    work = tempfile.mkdtemp(prefix="motion_", dir=core._work_root())
    res = {}
    try:
        for on in (False, True):
            lines = []
            r = core.run_batch(
                [inp],
                tag,
                game,
                os.path.join(work, str(on)),
                lines.append,
                body_files=barch,
                copy_moves=on,
            )
            if not r[0][1]:
                log("FAIL: no output (%s)" % r[0][2])
                fails.append("motion " + os.path.basename(inp))
                return
            src = os.path.join(work, "s%s" % on)
            core.run([wk, "unbundle", r[0][1], "-o", src], game)
            before = after = near = 0
            for m in glob.glob(os.path.join(src, "**", "*.mesh"), recursive=True):
                gd = os.path.join(work, "g%s" % on, os.path.basename(m))
                os.makedirs(gd)
                try:
                    core.run([wk, "export", m, "-o", gd, "-gp", game], game)
                except RuntimeError:
                    continue
                d, j, b = refit.load(glob.glob(os.path.join(gd, "*.glb"))[0])
                gn = (
                    [j["nodes"][ni].get("name") for ni in j["skins"][0]["joints"]]
                    if j.get("skins")
                    else []
                )
                gT = np.array(
                    [Tj[bnames.index(x)] if x in bnames else np.zeros(3) for x in gn]
                    or [np.zeros(3)]
                )
                for mm in j["meshes"]:
                    if "LOD_1" not in (mm.get("name") or "LOD_1"):
                        continue
                    for pr in mm["primitives"]:
                        sk = refit.prim_skin(d, j, b, pr)
                        if sk is None:
                            continue
                        X = refit.read(d, j, b, pr["attributes"]["POSITION"])
                        near += int((tree0.query(X)[0] < 0.06).sum())
                        before += inside(X, tree0, P)
                        after += inside(
                            X + (sk[1][..., None] * gT[sk[0]]).sum(1), tree1, moved
                        )
            res[on] = after
            log(
                "  option %s: %d clothing vertices near the body; inside before %d, after the chest moved %d"
                % ("ON " if on else "off", near, before, after)
            )
            if on:
                for l in lines:
                    if "now move with the body" in l or "movement can't" in l:
                        log("    | " + l.strip())
        # fit 2.0 leaves parts the author hid inside the body where they are, so they count as inside before the
        # chest moves too: what must hold is that the moving chest adds none
        # (up to 1 in 10,000 tolerated: without a 4 mm floor a few vertices sit 0.2-1 mm over the moving skin)
        if res[True] > (before + near // 10000 if refit.ENGINE == 2 else 0):
            log("FAIL: with the option, clothes still end up inside the moving chest")
            fails.append("motion " + os.path.basename(inp))
        else:
            log(
                "  clothes move with the body OK (%d inside without the option, 0 with it)"
                % res[False]
            )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def gui_extra_tests(core, gui, rdar, wk, game, a, barch, log, fails):
    r"""--gui-extra DIR --gui-extra-pasties OUTFIT [--gui-extra-pair OUTFIT] [--gui-extra-lone ARCHIVE]: in the real
    window: every button once (file dialogs answered by the test), the two options with real refits of a pasties
    outfit to Angel (unticked = no change, 0 = no change, -2.0 / +6.0 mm room, -30 / +30 mm pasties), wrong inputs
    (a text file, an empty folder, an already refit archive), an outfit whose refit needs its .xl (pair OUTFIT ->
    EVE: .archive + .xl) and the incomplete result (lone ARCHIVE of such an outfit without its .xl -> EVE).
    Everything is written below DIR (umlauts and spaces in the paths); the game folder is only read."""
    import numpy as np
    import refit, support
    from tkinter import filedialog, messagebox

    base = os.path.abspath(opt(a, "--gui-extra"))
    pasties = opt(a, "--gui-extra-pasties")
    os.makedirs(base, exist_ok=True)
    log("\n### Extra window tests (buttons, options, wrong inputs) in %s" % base)

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
            fails.append("extra: " + name)
        return cond

    def archives(d):
        return sorted(
            f
            for f in (os.listdir(d) if os.path.isdir(d) else [])
            if f.endswith(".archive")
        )

    # ---- 1. every button ----
    saved = core.load_settings()
    answers, boxes, opened = {}, [], []
    real = (
        filedialog.askdirectory,
        filedialog.askopenfilenames,
        filedialog.askopenfilename,
        messagebox.showerror,
        messagebox.showinfo,
        os.startfile,
        support.show_in_explorer,
    )
    filedialog.askdirectory = lambda **k: answers.get("dir", "")
    filedialog.askopenfilenames = lambda **k: answers.get("files", ())
    filedialog.askopenfilename = lambda **k: answers.get("file", "")
    messagebox.showerror = lambda t, m, **k: boxes.append(m)
    messagebox.showinfo = lambda t, m, **k: boxes.append(m)
    os.startfile = lambda *x, **k: opened.append(x[0])
    support.show_in_explorer = opened.append
    folder = os.path.join(base, "Ordner zum Hinzufügen ä")
    os.makedirs(folder, exist_ok=True)
    outdir = os.path.join(base, "Ausgabe Knöpfe ü")
    os.makedirs(outdir, exist_ok=True)
    want_buttons = [
        "Change...",
        "Body file...",
        "Body file...",
        "Change...",
        "Add outfit...",
        "Add folder...",
        "Remove",
        "Refit",
        "Open output folder",
        "Uninstall...",
        "Help",
        "Report Issues",
        "WolvenKit...",
    ]

    def buttons(w):
        import tkinter.ttk as ttk

        out = []
        for c in w.winfo_children():
            if isinstance(c, ttk.Button):
                out.append(c)
            elif c.winfo_class() != "Toplevel":
                out += buttons(c)
        return out

    def script(app, root):
        yield lambda: True
        bs = buttons(root)
        by = lambda text, n=0: [b for b in bs if b["text"] == text][n]
        check(
            "buttons: the window has exactly the expected buttons",
            sorted(b["text"] for b in bs) == sorted(want_buttons),
            [b["text"] for b in bs],
        )
        answers["files"] = (pasties,)
        by("Add outfit...").invoke()
        check(
            "button 'Add outfit...': the outfit is in the list",
            list(app.lst.get(0, "end")) == [os.path.normpath(pasties)],
        )
        answers["dir"] = folder
        by("Add folder...").invoke()
        check(
            "button 'Add folder...': the folder is in the list",
            app.lst.size() == 2 and app.lst.get(1) == os.path.normpath(folder),
        )
        app.lst.selection_set(1)
        by("Remove").invoke()
        check(
            "button 'Remove': the selected entry is gone",
            list(app.lst.get(0, "end")) == [os.path.normpath(pasties)],
        )
        del boxes[:]
        by("Change...", 0).invoke()  # game folder: a folder that is no game
        check(
            "button 'Change...' (game): a wrong folder is refused with a message",
            any("not the game folder" in m for m in boxes),
            boxes,
        )
        answers["dir"] = game
        by("Change...", 0).invoke()
        check(
            "button 'Change...' (game): the game folder is taken",
            os.path.normcase(app.game.get()) == os.path.normcase(game),
            app.game.get(),
        )
        answers["dir"] = outdir
        by("Change...", 1).invoke()
        check(
            "button 'Change...' (output): the folder is taken",
            app.out.get() == os.path.normpath(outdir),
            app.out.get(),
        )
        by("Open output folder").invoke()
        check(
            "button 'Open output folder': opens that folder",
            opened == [os.path.normpath(outdir)],
            opened,
        )
        del boxes[:]
        answers["dir"] = folder
        by("WolvenKit...").invoke()
        check(
            "button 'WolvenKit...': a folder without WolvenKit is refused with a message",
            len(boxes) == 1,
            boxes,
        )
        answers["dir"] = os.path.dirname(wk)
        by("WolvenKit...").invoke()
        check(
            "button 'WolvenKit...': a WolvenKit folder is taken",
            os.path.normcase(app.s.get("wk_existing", ""))
            == os.path.normcase(os.path.dirname(wk)),
            app.s.get("wk_existing"),
        )
        by("Help").invoke()
        yield lambda: True
        helps = [c for c in root.winfo_children() if c.winfo_class() == "Toplevel"]
        text = helps[0].winfo_children()[0].get("1.0", "end") if helps else ""
        check(
            "button 'Help': the guide opens with its steps", "Steps, in order" in text
        )
        check(
            "button 'Help': the guide explains both new options",
            "Adjust room at the chest" in text and "Move pasties up / down" in text,
        )
        for h in helps:
            [b for b in buttons(h) if b["text"] == "Close"][0].invoke()
        yield lambda: True
        check(
            "button 'Close' (Help): the guide is closed",
            not [c for c in root.winfo_children() if c.winfo_class() == "Toplevel"],
        )
        del boxes[:]
        core.uninstall(core.installed(), lambda m: None)
        by("Uninstall...").invoke()
        check(
            "button 'Uninstall...': with nothing installed it says so",
            any("not put anything" in m for m in boxes),
            boxes,
        )
        del boxes[:]
        by("Body file...", 0).invoke()  # 'Outfit made for' is vanilla: no file needed
        check(
            "button 'Body file...' (outfit made for vanilla): says none is needed",
            len(boxes) == 1,
            boxes,
        )
        answers["file"] = os.path.join(folder, "nichts.txt")
        open(answers["file"], "w").write("not a body")
        del boxes[:]
        app.body.set(core.body_name("angel"))
        app.show_body()
        by("Body file...", 1).invoke()
        check(
            "button 'Body file...' (your body mod): a text file is refused with a message",
            len(boxes) == 1,
            boxes,
        )
        del boxes[:], opened[:]
        by("Report Issues").invoke()
        yield lambda: True
        check(
            "button 'Report Issues': a report is made and shown",
            bool(opened) or bool(boxes),
            (opened, boxes),
        )
        check(
            "button 'Refit': enabled and labelled 'Refit' while idle",
            str(by("Refit")["state"]) != "disabled" and not app.busy,
        )
        app.s.clear()
        app.s.update(saved)
        app.save = lambda: None  # closing must not keep the test's values
        core.save_settings(saved)

    try:
        check("buttons: the script ran to its end", gui.drive_script(script, log))
    finally:
        (
            filedialog.askdirectory,
            filedialog.askopenfilenames,
            filedialog.askopenfilename,
            messagebox.showerror,
            messagebox.showinfo,
            os.startfile,
            support.show_in_explorer,
        ) = real
        core.save_settings(saved)

    # ---- 2. the two options, with real refits in the window ----
    _, cages = core.setup(game, "vanilla", "angel", log, body_files=barch)
    work = tempfile.mkdtemp(prefix="extra_", dir=core._work_root())
    runs = {}

    def run(key, **o):
        out = os.path.join(base, "Option %s ä" % key)
        shutil.rmtree(out, ignore_errors=True)
        # the window remembers its options from run to run (settings): every run starts from the same ones
        core.save_settings(o.pop("settings", saved))
        st = gui.drive(pasties, "angel", out, log, game=game, body_files=barch, **o)
        arcs = archives(out)
        if not (st["ok"] and len(arcs) == 1):
            check(
                "option run '%s': refit made" % key,
                False,
                (st["ok"], arcs, st.get("errors")),
            )
            return
        pos = positions(wk, game, os.path.join(out, arcs[0]), os.path.join(work, key))
        runs[key] = (
            np.vstack([p for k in sorted(pos) for p in pos[k]]),
            st["log"],
            os.path.join(out, arcs[0]),
        )

    try:
        run("aus")
        # boxes unticked, but the value lists hold +6.0 and +30 (as left from an earlier session)
        run(
            "aus mit Werten",
            settings=dict(
                saved,
                chest_room_on=False,
                chest_room=6.0,
                pasties_on=False,
                pasties_shift=30,
            ),
        )
        for v in ("-2.0", "+0.0", "+6.0"):
            run("Raum " + v, chest_room=v)
        for v in ("-30", "+0", "+30"):
            run("Pasties " + v, pasties_shift=v)
        core.save_settings(saved)
        if "aus" in runs:
            P0 = runs["aus"][0]
            h0 = refit.skin_height(P0, cages["angel"])[0]
            far = lambda k: (
                float(np.linalg.norm(runs[k][0] - P0, axis=1).max() * 1000)
                if k in runs and runs[k][0].shape == P0.shape
                else float("nan")
            )
            dh = lambda k: (
                float(
                    np.median(refit.skin_height(runs[k][0], cages["angel"])[0] - h0)
                    * 1000
                )
                if k in runs
                else float("nan")
            )
            for k, what in (
                ("aus mit Werten", "boxes unticked (values +6.0 / +30 in the lists)"),
                ("Raum +0.0", "room ticked, 0.0 mm"),
                ("Pasties +0", "pasties ticked, 0 mm"),
            ):
                check(
                    "options: %s = the same refit as without options" % what,
                    far(k) <= 0.0005,
                    "largest difference %.4f mm" % far(k),
                )
            log(
                "    room -2.0: height over the skin changes by %+.2f mm (median), +6.0: %+.2f mm; largest move %.2f / %.2f mm"
                % (dh("Raum -2.0"), dh("Raum +6.0"), far("Raum -2.0"), far("Raum +6.0"))
            )
            check(
                "options: room +6.0 mm lifts the piece about 6 mm",
                4.0 < dh("Raum +6.0") < 6.6,
                "%.2f mm" % dh("Raum +6.0"),
            )
            check(
                "options: room -2.0 mm brings it closer, at most 2 mm",
                -2.1 < dh("Raum -2.0") < -0.2,
                "%.2f mm" % dh("Raum -2.0"),
            )
            up = {}
            for k in ("Pasties -30", "Pasties +30"):
                if k in runs and runs[k][0].shape == P0.shape:
                    d = (runs[k][0].mean(0) - P0.mean(0)) * 1000
                    up[k] = (float(np.linalg.norm(d)), float(d[1]))  # glb: y is up
                    log(
                        "    %s: centre moved %.1f mm (up %+.1f mm), height over the skin %+.2f mm"
                        % (k, up[k][0], up[k][1], dh(k))
                    )
            check(
                "options: pasties +30 mm moves the piece up, about 30 mm",
                "Pasties +30" in up
                and 20 < up["Pasties +30"][0] < 35
                and up["Pasties +30"][1] > 5,
                up.get("Pasties +30"),
            )
            check(
                "options: pasties -30 mm moves the piece down, about 30 mm",
                "Pasties -30" in up
                and 20 < up["Pasties -30"][0] < 35
                and up["Pasties -30"][1] < -5,
                up.get("Pasties -30"),
            )
            check(
                "options: the log names the options",
                any("moved 30 mm up" in m for m in runs.get("Pasties +30", (0, []))[1])
                and any(
                    "moved 30 mm down" in m for m in runs.get("Pasties -30", (0, []))[1]
                ),
            )
        # ---- 3. wrong inputs ----
        txt = os.path.join(base, "kein Mod ö.txt")
        open(txt, "w").write("not an outfit")
        empty = os.path.join(base, "leerer Ordner ä")
        os.makedirs(empty, exist_ok=True)
        cases = [("a text file", txt, "vanilla"), ("an empty folder", empty, "vanilla")]
        if "aus" in runs:
            cases.append(
                ("an already refit archive (for the same body)", runs["aus"][2], None)
            )
        for what, inp, src in cases:
            out = os.path.join(base, "Falsch %d ü" % len(os.listdir(base)))
            core.save_settings(saved)
            st = gui.drive(inp, "angel", out, log, game=game, body_files=barch, src=src)
            said = [m for m in st["log"] + st.get("errors", []) if m.strip()]
            for m in said[-4:]:
                log("    | " + m.replace("\n", " ")[:300])
            check(
                "wrong input, %s: nothing is written" % what,
                not archives(out),
                archives(out),
            )
            check(
                "wrong input, %s: the window says why" % what,
                any(
                    re.search(
                        "Could not refit|nothing to refit|Nothing to refit|No clothing|are both|already fits|no \\.archive",
                        m,
                    )
                    for m in said
                ),
                said[-3:],
            )
        # ---- 4. an outfit whose refit needs its .xl, and the incomplete result ----
        if opt(a, "--gui-extra-pair"):
            out = os.path.join(base, "Paar ä ö")
            shutil.rmtree(out, ignore_errors=True)
            core.save_settings(saved)
            st = gui.drive(
                opt(a, "--gui-extra-pair"), "eve", out, log, game=game, body_files=barch
            )
            got = sorted(os.listdir(out)) if os.path.isdir(out) else []
            arcs = archives(out)
            check("pair: refit finished", st["ok"], st.get("errors"))
            check(
                "pair: the output is an .archive with its .xl of the same name",
                len(arcs) == 1 and arcs[0][:-8] + ".xl" in got,
                got,
            )
            check(
                "pair: the window names the .xl and says to copy it too",
                any("The two files belong together" in m for m in st["log"])
                and any(".xl files next to them" in m for m in st["log"]),
            )
            if arcs:
                check(
                    "pair: safety check of the output",
                    not refit.buffer_gate(os.path.join(out, arcs[0]), {}),
                )
        if opt(a, "--gui-extra-lone"):
            out = os.path.join(base, "Unvollständig ä")
            shutil.rmtree(out, ignore_errors=True)
            core.save_settings(saved)
            st = gui.drive(
                opt(a, "--gui-extra-lone"), "eve", out, log, game=game, body_files=barch
            )
            for m in [m for m in st["log"] if "INCOMPLETE" in m or "not complete" in m]:
                log("    | " + m[:300])
            check(
                "incomplete: the log says the refit is INCOMPLETE and why",
                any("This refit is INCOMPLETE" in m and ".xl" in m for m in st["log"]),
            )
            check(
                "incomplete: the summary names the outfit as not complete",
                any("is not complete:" in m for m in st["log"]),
            )
            got = sorted(os.listdir(out)) if os.path.isdir(out) else []
            check(
                "incomplete: an .archive is written, no .xl",
                len(archives(out)) == 1 and not [f for f in got if f.endswith(".xl")],
                got,
            )
    finally:
        core.save_settings(saved)
        shutil.rmtree(work, ignore_errors=True)


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
        check_rigid(__import__("refit"))
        log("rigid parts keep their movement OK")
    except AssertionError as e:
        log("FAIL rigid parts: %s" % (e,))
        fails.append("rigid")
    try:  # the two chest options: what is typed is no number, not finite or out of range -> a clean value, no error
        got = [core.chest_room_value(True, x) for x in ("+2.0", "-1,5", "abc", "", "nan", "inf", "-inf", "1e999", "99", "-99")]
        assert got == [2.0, -1.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 6.0, -2.0], got
        got = [core.pasties_value(True, x) for x in ("+10", "-7", "abc", "", "nan", "inf", "-inf", "1e999", "99", "-99", "2.4")]
        assert got == [10, -7, 0, 0, 0, 0, 0, 0, 30, -30, 2], got
        assert core.chest_room_value(False, "3") == 0.0 and core.pasties_value(False, "5") == 0
        log("chest option values (wrong / endless / out-of-range input) OK")
    except Exception as e:
        log("FAIL chest option values: %r" % (e,))
        fails.append("option values")
    try:
        check_rigs(core)
        log("rig / breast physics mod detection OK")
    except AssertionError as e:
        log("FAIL rig mod detection: %s" % (e,))
        fails.append("rigs")
    try:
        check_install_rules(core)
        log(
            "install / uninstall rules (older '!!!' refit, changed files, plain errors) OK"
        )
    except (AssertionError, OSError) as e:
        log("FAIL install / uninstall rules: %s" % (e,))
        fails.append("install rules")
    try:
        check_tar(core)
        log(".7z without 7-Zip (Windows tar), game folder one level off OK")
    except (AssertionError, core.ToolError, RuntimeError) as e:
        log("FAIL .7z without 7-Zip / game folder: %s" % (e,))
        fails.append("tar")
    try:
        check_dotnet(core)
        log("WolvenKit finds .NET 10 despite a wrong DOTNET_ROOT OK")
    except (AssertionError, core.ToolError) as e:
        log("FAIL WolvenKit / .NET: %s" % (e,))
        fails.append("dotnet")
    try:
        check_recover_names(rdar, __import__("refit"))
        log("names for archives without a name list OK")
    except AssertionError as e:
        log("FAIL names for archives without a name list: %s" % e)
        fails.append("recover names")
    try:
        check_xl_patch(__import__("refit"))
        log("ArchiveXL patches for new mesh paths, appearance check, male meshes OK")
    except AssertionError as e:
        log("FAIL ArchiveXL patches / appearance check: %s" % (e,))
        fails.append("xl patch")
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
    # 'WolvenKit...' with the folder above WolvenKit's own (1.9.2): found below it, not refused
    if (
        core.ensure_wolvenkit(log, lambda f: None, os.path.dirname(os.path.dirname(wk)))
        != wk
    ):
        log("FAIL: WolvenKit not found below the picked folder")
        fails.append("wolvenkit parent folder")
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
            continue
        # fit 2.0: one measurable check per fix, on garments made from the body shapes themselves
        import fitcheck

        for name, ok, txt in fitcheck.run(cages["vanilla"], cages[t]):
            log(
                "%s fit vanilla -> %s, %s: %s"
                % ("OK  " if ok else "FAIL", t, name, txt)
            )
            if not ok:
                fails.append("fit %s: %s" % (t, name))

    if tags:  # an outfit patched by its .xl, all the way through refit_mod and the final check
        try:
            xl_case(core, rdar, wk, game, tags, barch, out, log, fails)
        except Exception:
            log("FAIL: .xl patch case crashed:\n" + traceback.format_exc())
            fails.append("xl case")

    seen = []  # log lines of the last run_case

    def run_case(src, tag, inp, dest, seen_out=None, **opt):
        """One refit like the window does it, plus the safety check of the output. -> output archive or None.
        seen_out: a list that gets the refit's log lines."""
        log("\n### case %s: %s -> %s" % (os.path.basename(inp), src, tag))
        seen[:] = []
        res = core.run_batch(
            [inp],
            tag,
            game,
            dest,
            lambda m: (seen.append(m), log(m)),
            body_files=barch,
            src=src,
            **opt,
        )
        arch = res[0][1]
        if seen_out is not None:
            seen_out.extend(seen)
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
    for c in opt_all(a, "--layers"):
        try:
            layer_test(core, wk, game, c, run_case, seen, barch, log, fails)
        except Exception:
            log("FAIL: layer test crashed:\n" + traceback.format_exc())
            fails.append("layers " + c)
    for c in opt_all(a, "--chest"):
        try:
            chest_test(core, wk, game, c, run_case, barch, log, fails)
        except Exception:
            log("FAIL: clothed chest test crashed:\n" + traceback.format_exc())
            fails.append("chest " + c)
    for c in opt_all(a, "--replace-own"):
        try:
            own_test(core, rdar, wk, game, c, barch, log, fails)
        except Exception:
            log("FAIL: replace-own test crashed:\n" + traceback.format_exc())
            fails.append("replace own " + c)
    for c in opt_all(a, "--motion"):
        try:
            motion_test(core, wk, game, c, barch, log, fails)
        except Exception:
            log("FAIL: motion test crashed:\n" + traceback.format_exc())
            fails.append("motion " + c)
    if "--gui" in a:
        try:
            import gui

            gui.main(close_after_ms=2000)
            log("\nwindow opened and closed OK")
            # 'Adjust room at the chest' / 'Move pasties up / down': boxes, value lists, wrong input, saved settings
            if gui.room_test(log):
                fails.append("gui room / pasties controls")
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
    if opt(a, "--gui-extra"):
        try:
            import gui

            gui_extra_tests(core, gui, rdar, wk, game, a, barch, log, fails)
        except Exception:
            log("FAIL: extra window tests crashed:\n" + traceback.format_exc())
            fails.append("gui-extra")
    log("\nSELFTEST %s" % ("PASSED" if not fails else "FAILED: %s" % fails))
    return 1 if fails else 0
