r"""Outfit Refitter: refit Cyberpunk 2077 clothing mods to the body mod you use.

outfit_refitter.py [outfit ...]                      open the window (files dropped on the exe are pre-added)
outfit_refitter.py --refit OUTFIT... --body angel [--install] [--out DIR] [--game DIR]
outfit_refitter.py --selftest ...                    see selftest.py
outfit_refitter.py --version
In the windowed .exe, --refit / --selftest / --version output goes to %TEMP%\OutfitRefitter_console.log.
"""

import os, sys


def cli_refit(a):
    import core
    from selftest import opt

    s = core.load_settings()
    game = opt(a, "--game") or s.get("game") or core.find_game()
    tag = opt(a, "--body", "angel")
    files = [
        x
        for i, x in enumerate(a)
        if not x.startswith("--")
        and (i == 0 or a[i - 1] not in ("--game", "--body", "--out"))
    ]
    res = core.run_batch(
        files,
        tag,
        game,
        opt(a, "--out") or core.DEFAULT_OUT,
        print,
        do_install="--install" in a,
    )
    return 0 if all(r[1] or r[2] == "nothing to refit" for r in res) else 1


def main():
    a = sys.argv[1:]
    import ctypes  # below-normal priority: refitting while the game runs must not make it stutter

    ctypes.windll.kernel32.SetPriorityClass(
        ctypes.windll.kernel32.GetCurrentProcess(), 0x4000
    )
    if (sys.stdout is None or sys.stderr is None) and (
        "--selftest" in a or "--refit" in a or "--version" in a
    ):
        # the windowed exe has no console: write the text to %TEMP%\OutfitRefitter_console.log instead
        import tempfile

        sys.stdout = sys.stderr = open(
            os.path.join(tempfile.gettempdir(), "OutfitRefitter_console.log"),
            "w",
            encoding="utf-8",
            buffering=1,
        )
    if "--version" in a:
        import core

        print("Outfit Refitter", core.VERSION)
        return 0
    try:
        if "--selftest" in a:
            import selftest

            return selftest.main(a)
        if "--refit" in a:
            return cli_refit([x for x in a if x != "--refit"])
    except Exception as e:  # ToolError messages are meant for people
        print("ERROR:", e)
        return 1
    import gui

    gui.main(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
