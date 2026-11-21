r"""Headless self-test: game detection, WolvenKit download/check, body-shape extraction from the installed body mods,
then refit real outfit archives, run the safety check and list the output. Optionally compares the vertex positions
with refits you trust (e.g. ones made by hand).

  body_refitter.py --selftest [--home DIR] [--out DIR] [--game DIR] [--wolvenkit DIR]
                   [--body-archive TAG=FILE ...] [--case TAG=OUTFIT[=REFERENCE.archive] ...] [--gui]

--home: data folder to use instead of %LOCALAPPDATA%\BodyRefitter (fresh folder = real first run).
--gui: also open the window for two seconds and close it (proves it starts).
--gui-drop TAG=OUTFIT: open the real window, drop OUTFIT on it like Explorer does, press Refit, check the result.
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
            ]
        )
        assert got == want, got
    finally:
        shutil.rmtree(t, ignore_errors=True)


def main(a):
    if opt(a, "--home"):
        os.environ["BODYREFIT_HOME"] = os.path.abspath(opt(a, "--home"))
    import core, rdar

    log = lambda m: print(m, flush=True)
    fails = []
    log("Body Refitter %s self-test" % core.VERSION)
    try:
        check_variants(core)
        log("download-variant selection OK")
    except AssertionError as e:
        log("FAIL download-variant selection: %s" % e)
        fails.append("variants")
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
        tag, _, outfit = v.partition("=")
        log("\nGUI drag-and-drop test: %s -> %s" % (os.path.basename(outfit), tag))
        try:
            import gui

            out = tempfile.mkdtemp(prefix="rf_guidrop_")
            if gui.drive(outfit, tag, out, log) and any(
                f.lower().endswith(".archive") for f in os.listdir(out)
            ):
                log("  OK: window refit produced %s" % os.listdir(out))
            else:
                fails.append("gui-drop " + tag)
        except Exception:
            log("FAIL: gui-drop crashed:\n" + traceback.format_exc())
            fails.append("gui-drop " + tag)
    log("\nSELFTEST %s" % ("PASSED" if not fails else "FAILED: %s" % fails))
    return 1 if fails else 0
