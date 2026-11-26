"""The small window. All real work is in core.run_batch (same code the --selftest uses)."""

import os, queue, threading, traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import core, support

OUTFIT_TYPES = [
    ("Outfit downloads", "*.zip *.7z *.rar *.archive"),
    ("All files", "*.*"),
]


def enable_drop(root, callback):
    """Let files be dropped from Explorer onto the window (WM_DROPFILES; stdlib tkinter has no drag and drop).
    Fails silently: 'Add outfit...' still works."""
    try:
        import ctypes
        from ctypes import wintypes

        u, sh = ctypes.windll.user32, ctypes.windll.shell32
        LRESULT = ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )
        u.SetWindowLongPtrW.restype = ctypes.c_void_p
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        u.CallWindowProcW.restype = LRESULT
        u.CallWindowProcW.argtypes = [
            ctypes.c_void_p,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        sh.DragQueryFileW.argtypes = [
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.c_wchar_p,
            wintypes.UINT,
        ]
        sh.DragFinish.argtypes = [ctypes.c_void_p]
        root.update_idletasks()
        hwnd = int(root.wm_frame(), 16)
        for msg in (
            0x233,
            0x4A,
            0x49,
        ):  # allow drops from a non-admin Explorer if we run as admin
            u.ChangeWindowMessageFilterEx(hwnd, msg, 1, None)
        sh.DragAcceptFiles(hwnd, True)

        def proc(h, msg, wp, lp):
            if msg == 0x233:  # WM_DROPFILES
                files = []
                for i in range(sh.DragQueryFileW(wp, 0xFFFFFFFF, None, 0)):
                    n = sh.DragQueryFileW(wp, i, None, 0) + 1
                    b = ctypes.create_unicode_buffer(n)
                    sh.DragQueryFileW(wp, i, b, n)
                    files.append(b.value)
                sh.DragFinish(wp)
                # Never call Tk from inside this Windows callback: Tcl runs it with the GIL
                # released and any tkinter call here aborts the process (0xc0000409).
                # Hand the files over and let a Tk timer pick them up.
                pending.append(files)
                return 0
            return u.CallWindowProcW(old, h, msg, wp, lp)

        pending = []

        def poll():
            while pending:
                callback(pending.pop(0))
            root.after(150, poll)

        root._drop_proc = WNDPROC(
            proc
        )  # keep a reference or Windows calls freed memory
        old = u.SetWindowLongPtrW(
            hwnd, -4, ctypes.cast(root._drop_proc, ctypes.c_void_p)
        )
        poll()
        return True
    except Exception:
        return False


HELP_TEXT = """What this tool does
An outfit only fits the body it was made for. This tool reshapes an outfit from one body to another: from the normal (vanilla) body to Hyst Angel or Hyst EVE, from Angel or EVE back to vanilla, or between Angel and EVE. It makes one small extra file; the original outfit stays untouched.

Steps, in order
1. Game folder: check that the top line shows your Cyberpunk 2077 folder. If not, click 'Change...' next to it and pick the folder that contains 'bin' and 'archive'.

2. Outfit made for: the body the outfit was made for. Most outfits are made for the normal body: leave it on 'Vanilla (normal body)'. When you add an outfit whose name says 'Angel', 'EVE' or 'Vanilla', the tool sets this for you; check it and change it if it's wrong.
   If the outfit is made for Angel or EVE and that body mod is NOT in your game (for example you use EVE and the outfit is for Angel), click 'Body file...' next to it and pick that body mod's download (zip / 7z / rar) or its .archive. The tool only reads the body shape from it, once. It installs nothing.

3. Your body mod: the body you use and want the outfit to fit: Hyst Angel, Hyst EVE, or 'Vanilla (normal body)' if you play without a body mod. Below it you should see 'found: ...'. If it says 'not found', install that body mod first, or click 'Body file...' and pick its download or .archive.

4. Output folder: where the finished refits are saved. Click 'Change...' to pick another folder.

5. Add the outfit: drag its download (zip / 7z / rar), its unpacked folder or its .archive file into the big list. Or use 'Add outfit...' / 'Add folder...'. You can add several outfits at once, but 'Outfit made for' counts for all of them, so add outfits for one body at a time. If a download has versions for several bodies, the tool uses the one for 'Outfit made for' (or the vanilla one).

6. Install: tick 'Install into the game when done' if the tool should put the refit straight into your game. If the original outfit is not in your game yet, the tool installs it too, from the same download. Leave it unticked to only save the file in the output folder; then nothing goes into your game.
   Some outfit downloads already contain a ready-made version for the body you picked under 'Your body mod' (for example an Angel version made by the outfit's author). The tool leaves that version alone and only refits the parts that have none. If the author's version doesn't fit well (for example it clips), tick 'Replace the outfit's own version': the tool then refits those parts too, and its version is used instead. Uninstall the refit to get the outfit's own version back.
   'Make clothes move with the body': tight clothes then follow the body exactly when V moves, also with rig or breast physics mods (the log tells you if you have such mods). Loose parts like a hanging skirt keep their own movement. It's on by default; untick it only if a part moves wrong (for example a skirt sticks to the legs).
   'Adjust room at the chest': only for when the chest still pokes through after a refit, or a top sits too loose there. Tick it, pick a value from -2.0 to +6.0 mm and refit the outfit again. Plus makes the clothing looser over the chest, minus makes it tighter than the tool's normal fit; nothing else changes. Tighter can clip when V moves. Unticked, or 0.0, is the normal fit.
   'Move pasties up / down': only for pasties and other small pieces that sit on the breast. If they sit too high or too low after a refit, tick it, pick how far to move them (from -30 to +30 mm, plus is up) and refit again. They slide along the skin and stay as close to it as before; tops, bras and everything else are not changed. The log says which pieces were moved. Unticked, or 0, is the normal place.
   'Also pack each refit into its own zip': for Vortex or Mod Organizer 2. The zip is saved next to the refit in the output folder; add it to your mod manager like any other mod, then you can turn the refit on and off there. Leave 'Install into the game' unticked then.
   Not sure what an option does? Move the mouse over the small '?' next to it.

7. Click 'Refit'. The bar and the log below show the progress. The first run takes longer: the tool downloads WolvenKit (about 45 MB) and reads the body shapes once. While it runs, the button says 'Stop': click it to stop; nothing is kept from the outfit it was working on.

8. Done. If you didn't tick Install, click 'Open output folder' and copy the .archive file(s), and the .xl file of the same name when there is one, into <game folder>\\archive\\pc\\mod. The original outfit must be in your game as well; the refit only reshapes it. The log tells you if it is missing.

Undo
'Uninstall...' lists what this tool put into your game: refits, and original outfits it installed because they were missing. Select what to remove. Or delete the AngelRefit_ / EveRefit_ / VanillaRefit_ file from archive\\pc\\mod yourself. If a file was changed since the tool installed it (for example you installed the same mod again with a mod manager), the tool leaves it alone and tells you.
When you refit an outfit again, for example for the other body, the new refit replaces the tool's older refit of that outfit in your game, so the old one can't win over it.

Good to know
- Pick the right 'Outfit made for'. If an Angel outfit is refit as if it were vanilla, it gets reshaped twice and won't fit. The log warns you when the outfit's name says another body than the one you picked.
- The refit only works together with the original outfit. With Install ticked, the tool checks your game and installs the original if it is missing. An original that is already installed is left as it is.
- Refits are for your own game. Don't upload refits of other people's outfits without the outfit author's permission.
- If something is skipped (for example glasses or props), the log says so; the rest of the outfit is still refit.
- Outfits with several layers (for example a top, a shirt and a jacket): the tool keeps each layer 1 mm above the one under it, so the shirt doesn't poke through the jacket. It knows the order from the outfit's own files (when the outfit uses ArchiveXL outfit slots such as TorsoUnder / TorsoMiddle / TorsoOuter). Add the outfit's download, not only its .archive, so the tool can read them.
- Chest size: Hyst tells you to set V's chest size ('Breast') to 'Big' for Angel and EVE. With 'Big', the game shows the bare chest shape also under clothes, together with the normal one. Refits fit both shapes and leave a little more room under the breasts, so nothing pokes through, also when V moves. With another chest size, tops can sit a little looser under the breasts.
- Some outfits switch Angel's or EVE's chest to a different shape while they're worn ('natural' or 'squish', Hyst's shape add-ons). The tool sees that and fits those outfits to that shape; the log says so.
- A download with several colours of the same clothes: the tool refits the colour installed in your game (or the first one, and says so).
- Parts with cloth physics (some skirts and coats) stay unchanged, because changing them can crash the game. They may clip a little.
- Layered outfits: when something is worn over a piece, the game squashes that piece toward the body. The tool fits that squashed shape to your body too.
- Angel and EVE show nipples also under clothes. Refits leave a little more room over the breast tips so they don't poke through.
- Hyst says that rig mods which change the breast area (for example "All the Rigs") and breast physics mods make some clothes clip with Angel and EVE, mostly when V moves. The tool tells you in the log if you have such mods; keep 'Make clothes move with the body' ticked so tight clothes follow those movements.
- 'WolvenKit...' lets you use a WolvenKit you already have instead of downloading it. Pick the folder of WolvenKit's console version (WolvenKit.Console-<version>.zip, unzipped) or a folder above it; the tool finds WolvenKit.CLI.exe inside. It's copied on the next refit.

Common problems
- The game folder isn't found: click 'Change...' and pick your 'Cyberpunk 2077' folder. For the Xbox app it's XboxGames\\Cyberpunk 2077\\Content.
- 'WolvenKit needs .NET 10': install Microsoft's free '.NET Runtime 10' (64-bit / x64) from the link in the message. The tool also finds a .NET 10 that WolvenKit misses because of an old Windows setting, and lists what it found.
- 'Windows did not let the tool use ...': close the game and anything that might have the file open (a mod manager, an archive tool), then try again. If your game is in a protected folder like Program Files, untick Install and copy the refit yourself.
- 'WolvenKit does not start': an antivirus may have blocked it. The tool downloads WolvenKit again once by itself; if that doesn't help, allow the folder named in the message in your antivirus.
- .7z and .rar downloads: Windows 11 opens them by itself. If yours can't, install 7-Zip (free), or unpack the download and add the folder.
- Mod Organizer 2: your mods are not in the game folder, so the tool can't see your body mod there. Pick the body mod's file with 'Body file...'.

Having a problem?
Click 'Report Issues' and send the file it makes to the mod author: open a new issue at https://github.com/jj-repository/cp2077-outfit-refitter/issues and attach the file there, or upload it to pastebin.com and post the link in the comments on the Nexus page. It lists what you did in the tool, the log, what the tool finds in your game folder, and things on your PC that can get in the way (Windows version, free space, your antivirus' name, whether WolvenKit starts). Your Windows user name and your PC's name are left out, and no file contents are included. You can open the file first to see what's inside.
"""


# log lines that are errors in the support report
ERROR_LINES = (
    "Unexpected error",
    "Could not refit",
    "Stopped",
    "Not installed",
    "Not copied",
    "The original outfit could not",
    "could not be installed",
    "Not removed",
    "Could not save the settings",
)


def tip_icon(parent, text):
    """A small '?' after an option; hovering over it shows text."""
    q = ttk.Label(parent, text=" ? ", foreground="#1a5fb4", cursor="question_arrow")
    q.pack(side="left")
    pop = []

    def show(e):
        t = tk.Toplevel(q)
        t.wm_overrideredirect(True)
        t.wm_geometry("+%d+%d" % (e.x_root + 12, e.y_root + 12))
        tk.Label(
            t,
            text=text,
            wraplength=360,
            justify="left",
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
        ).pack()
        pop.append(t)

    def hide(e):
        while pop:
            pop.pop().destroy()

    q.bind("<Enter>", show)
    q.bind("<Leave>", hide)
    return q


class App:
    def __init__(self, root, files):
        support.rotate_sessions()
        self.session, self.results = support.Session(), []
        self.root, self.q, self.busy = root, queue.Queue(), False
        self.cancel, self.worker = threading.Event(), None
        root.report_callback_exception = self.on_error
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.s = core.load_settings()
        self.tags = list(core.bodies()["bodies"])
        root.title("Outfit Refitter %s" % core.VERSION)
        root.minsize(560, 600)
        pad = dict(padx=8, pady=3)

        f = ttk.Frame(root)
        f.pack(fill="x", **pad)
        ttk.Label(f, text="Game folder:").grid(row=0, column=0, sticky="w")
        # a saved folder the game was moved away from is looked for again
        self.game = tk.StringVar(
            value=core.game_root(self.s.get("game")) or core.find_game() or ""
        )
        ttk.Entry(f, textvariable=self.game, state="readonly").grid(
            row=0, column=1, sticky="ew", padx=4
        )
        self.button(f, "Change...", self.pick_game).grid(row=0, column=2)
        names = [core.body_name(t) for t in self.tags]
        body = self.s.get("body", "angel")
        self.src = tk.StringVar(value=core.body_name("vanilla"))
        self.body = tk.StringVar(
            value=core.body_name(body if body in self.tags else "angel")
        )
        self.src_info, self.body_info = (
            ttk.Label(f, text="", foreground="#555") for _ in range(2)
        )
        for row, label, var, info, role in (
            (1, "Outfit made for:", self.src, self.src_info, "src"),
            (3, "Your body mod:", self.body, self.body_info, "body"),
        ):
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=(6, 0))
            cb = ttk.Combobox(
                f, textvariable=var, values=names, state="readonly", width=22
            )
            cb.grid(row=row, column=1, sticky="w", padx=4, pady=(6, 0))
            cb.bind("<<ComboboxSelected>>", lambda e: self.show_body())
            self.button(f, "Body file...", lambda r=role: self.pick_body(r)).grid(
                row=row, column=2, pady=(6, 0)
            )
            info.grid(row=row + 1, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Label(f, text="Output folder:").grid(
            row=5, column=0, sticky="w", pady=(6, 0)
        )
        self.out = tk.StringVar(value=self.s.get("out") or core.DEFAULT_OUT)
        ttk.Entry(f, textvariable=self.out, state="readonly").grid(
            row=5, column=1, sticky="ew", padx=4, pady=(6, 0)
        )
        self.button(f, "Change...", self.pick_out).grid(row=5, column=2, pady=(6, 0))
        f.columnconfigure(1, weight=1)
        self.guesses = {}  # outfit in the list -> body tag its name says it is made for

        box = ttk.LabelFrame(
            root, text="Outfits to refit: drop zip / 7z / rar / folder / .archive here"
        )
        box.pack(fill="both", expand=True, **pad)
        self.lst = tk.Listbox(box, height=6, selectmode="extended", activestyle="none")
        self.lst.pack(fill="both", expand=True, padx=6, pady=4)
        b = ttk.Frame(box)
        b.pack(fill="x", padx=6, pady=(0, 6))
        self.button(b, "Add outfit...", self.add_files).pack(side="left")
        self.button(b, "Add folder...", self.add_folder).pack(side="left", padx=4)
        self.button(b, "Remove", self.remove_sel).pack(side="left")

        o = ttk.Frame(root)
        o.pack(fill="x", **pad)
        self.inst = tk.BooleanVar(value=self.s.get("install", False))
        ttk.Checkbutton(
            o, text="Install into the game when done", variable=self.inst
        ).pack(side="left")
        o2 = ttk.Frame(root)
        o2.pack(fill="x", **pad)
        # off by default: parts the outfit already has a version for your body of are left alone
        self.own = tk.BooleanVar(value=self.s.get("replace_own", False))
        ttk.Checkbutton(
            o2,
            text="Replace the outfit's own version (if it already comes with one for 'Your body mod')",
            variable=self.own,
        ).pack(side="left")
        o3 = ttk.Frame(root)
        o3.pack(fill="x", **pad)
        # on by default since 1.9; new key so 1.7/1.8's saved "off" (the old default) doesn't stick
        self.moves = tk.BooleanVar(value=self.s.get("move_with_body", True))
        ttk.Checkbutton(
            o3,
            text="Make clothes move with the body (helps with rig / breast physics mods)",
            variable=self.moves,
        ).pack(side="left")
        o4 = ttk.Frame(root)
        o4.pack(fill="x", **pad)
        self.zip = tk.BooleanVar(value=self.s.get("make_zip", False))
        ttk.Checkbutton(
            o4,
            text="Also pack each refit into its own zip (for Vortex / Mod Organizer 2)",
            variable=self.zip,
        ).pack(side="left")
        # 'Adjust room at the chest': a tick box and a list of values; the list is only active when ticked
        # (its state follows the box in poll(), never from inside a Tk callback)
        o5 = ttk.Frame(root)
        o5.pack(fill="x", **pad)
        self.room_on = tk.BooleanVar(value=bool(self.s.get("chest_room_on", False)))
        ttk.Checkbutton(o5, text="Adjust room at the chest", variable=self.room_on).pack(side="left")
        self.room = tk.StringVar(value="%+.1f" % core.chest_room_value(True, self.s.get("chest_room", 0.0)))
        self.room_box = ttk.Combobox(
            o5, textvariable=self.room, values=["-2.0", "-1.5", "-1.0", "-0.5", "+0.0", "+0.5", "+1.0", "+1.5", "+2.0", "+2.5", "+3.0", "+3.5", "+4.0", "+4.5", "+5.0", "+5.5", "+6.0"], width=6, state="disabled"
        )
        self.room_box.pack(side="left", padx=(6, 2))
        ttk.Label(o5, text="mm").pack(side="left")
        self.room_state = None
        # 'Move pasties up / down': the same pattern
        o6 = ttk.Frame(root)
        o6.pack(fill="x", **pad)
        self.pasties_on = tk.BooleanVar(value=bool(self.s.get("pasties_on", False)))
        ttk.Checkbutton(o6, text="Move pasties up / down", variable=self.pasties_on).pack(side="left")
        self.pasties = tk.StringVar(value="%+d" % core.pasties_value(True, self.s.get("pasties_shift", 0)))
        self.pasties_box = ttk.Combobox(
            o6, textvariable=self.pasties, values=["%+d" % v for v in range(-30, 31)], width=6, state="disabled"
        )
        self.pasties_box.pack(side="left", padx=(6, 2))
        ttk.Label(o6, text="mm (+ is up)").pack(side="left")
        self.pasties_state = None
        for fr, txt in (
            (
                o,
                "Puts the finished outfit into your game for you. If the original outfit is missing, "
                "it's added too. You can take it out again with 'Uninstall'.",
            ),
            (
                o2,
                "Some outfit downloads already contain a ready-made version for the body you picked under "
                "'Your body mod' (for example an Angel version made by the outfit's author). "
                "Normally the tool doesn't touch that version and only refits the parts that have none. "
                "Tick this if the author's version doesn't fit well: the tool then refits those parts too, "
                "and its version is used instead of the author's.",
            ),
            (
                o3,
                "Clothes close to the body move along with it, like skin. Keep this ticked. "
                "Only untick it if something moves wrong, for example a skirt sticks to the legs.",
            ),
            (
                o4,
                "Puts a zip next to each refit in the output folder. Add that zip to Vortex or Mod Organizer 2 "
                "like any other mod: then you can turn the refit on and off there. Leave 'Install into the game' "
                "unticked if you use a mod manager.",
            ),
            (
                o5,
                "Only if the chest still pokes through (or a top sits too loose) after a refit: tick it, pick a "
                "value and refit again. Plus = looser (more room over the chest), minus = tighter than the tool's "
                "normal fit. Only the chest area changes. Tighter can clip when V moves; the clothing never ends "
                "up inside the skin of the standing body. Unticked or 0.0 = the normal fit.",
            ),
            (
                o6,
                "Only for pasties and other small pieces that sit on the breast: if they sit too high or too low "
                "after a refit, tick it, pick how far to move them (plus = up, minus = down) and refit again. "
                "They slide along the skin and stay as close to it as before. Tops, bras and everything else "
                "are not changed; the log says which pieces were moved. Unticked or 0 = the normal place.",
            ),
        ):
            tip_icon(fr, txt)

        a = ttk.Frame(root)
        a.pack(fill="x", **pad)
        self.go = self.button(a, "Refit", self.start)
        self.go.pack(side="left")
        self.button(a, "Open output folder", self.open_out).pack(side="left", padx=4)
        self.button(a, "Uninstall...", self.uninstall).pack(side="left")
        self.button(a, "Help", self.show_help).pack(side="right")
        self.button(a, "Report Issues", self.support_report).pack(
            side="right", padx=(4, 0)
        )
        self.button(a, "WolvenKit...", self.pick_wk).pack(side="right", padx=4)

        self.prog = ttk.Progressbar(root, maximum=1.0)
        self.prog.pack(fill="x", **pad)
        lf = ttk.Frame(root)
        lf.pack(fill="both", expand=True, **pad)
        self.log_box = tk.Text(
            lf, height=12, wrap="word", state="disabled", font=("Segoe UI", 9)
        )
        sb = ttk.Scrollbar(lf, command=self.log_box.yview)
        self.log_box["yscrollcommand"] = sb.set
        sb.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True)

        # record every change of these, also from the self-test; the traces are removed with the window, or
        # Tcl keeps them (and this whole window) alive and fires its old timers into the next window
        traces = [
            (
                var,
                var.trace_add(
                    "write",
                    lambda *x, n=name, v=var: self.rec(
                        "action", "%s set to: %s" % (n, v.get())
                    ),
                ),
            )
            for name, var in (
                ("game folder", self.game),
                ("outfit made for", self.src),
                ("body", self.body),
                ("output folder", self.out),
            )
        ]
        for var, what in (
            (self.inst, "install box "),
            (self.own, "replace own version box "),
            (self.moves, "move with the body box "),
            (self.zip, "zip box "),
            (self.room_on, "room at the chest box "),
            (self.pasties_on, "move pasties box "),
        ):
            traces.append(
                (
                    var,
                    var.trace_add(
                        "write",
                        lambda *x, v=var, w=what: self.rec(
                            "action", w + ("ticked" if v.get() else "unticked")
                        ),
                    ),
                )
            )
        root.bind(
            "<Destroy>",
            lambda e: (
                [v.trace_remove("write", t) for v, t in traces]
                if e.widget is root
                else None
            ),
            add="+",
        )
        if not enable_drop(root, lambda f: self.add(f, "dropped")):
            box["text"] = "Outfits to refit (zip / 7z / rar / folder / .archive)"
        self.add(files, "added from the command line")
        self.show_body()
        if not core.is_game_dir(self.game.get()):
            self.log(
                "Game folder not found automatically. Click 'Change...' and pick your Cyberpunk 2077 folder."
            )
        else:
            self.log(
                "Ready. Add outfits, check 'Outfit made for' and your body, then press Refit."
            )
        root.update_idletasks()  # never shrink the window so far that buttons get cut off
        root.minsize(max(560, a.winfo_reqwidth() + 16), 600)
        root.after(100, self.poll)

    # ---- helpers ----
    def tag(self):
        """The body to refit to ('Your body mod')."""
        return self.tags[[core.body_name(t) for t in self.tags].index(self.body.get())]

    def src_tag(self):
        """The body the outfits are made for ('Outfit made for')."""
        return self.tags[[core.body_name(t) for t in self.tags].index(self.src.get())]

    def rec(self, kind, text):
        self.session.add(kind, text)

    def button(self, parent, text, cmd):
        """A button whose clicks go into the session record."""
        return ttk.Button(
            parent,
            text=text,
            command=lambda: (self.rec("action", "clicked '%s'" % text), cmd()),
        )

    def msg(self, kind, text):
        """Message box (kind: error / info), recorded."""
        self.rec("message", text)
        (messagebox.showerror if kind == "error" else messagebox.showinfo)(
            "Outfit Refitter", text
        )

    def log(self, msg):
        self.rec("error" if msg.startswith(ERROR_LINES) else "log", msg)
        self.log_box["state"] = "normal"
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box["state"] = "disabled"

    def save(self):
        self.s.update(
            game=self.game.get(),
            body=self.tag(),
            install=self.inst.get(),
            replace_own=self.own.get(),
            move_with_body=self.moves.get(),
            make_zip=self.zip.get(),
            chest_room_on=self.room_on.get(),
            chest_room=core.chest_room_value(True, self.room.get()),
            pasties_on=self.pasties_on.get(),
            pasties_shift=core.pasties_value(True, self.pasties.get()),
        )
        try:
            core.save_settings(self.s)
        except OSError as e:  # settings are a convenience: never stop the tool for them
            self.log("Could not save the settings: %s" % (core.explain(e) or e))

    def body_files(self):
        """{body tag: body file the user picked} (plain copy, safe to hand to the worker)."""
        return dict(self.s.get("body_archive", {}))

    def body_file(self):
        return self.body_files().get(self.tag())

    def show_body(self):
        g = self.game.get()
        for info, t, src in (
            (self.src_info, self.src_tag(), True),
            (self.body_info, self.tag(), False),
        ):
            if core.bodies()["bodies"][t].get("game"):
                info["text"] = "comes from your game, nothing to install"
                continue
            p = self.body_files().get(t)
            if not (p and os.path.exists(p)):
                p = core.find_body_archive(g, t) if core.is_game_dir(g) else None
            info["text"] = (
                ("found: " + os.path.basename(p))
                if p
                else "not in the game - pick its download or .archive with 'Body file...' (only read)"
                if src
                else "not found in the game - install %s or use 'Body file...'"
                % core.body_name(t)
            )

    def add(self, files, how="added"):
        have = set(self.lst.get(0, "end"))
        for f in files:
            if f not in have:
                self.lst.insert("end", f)
                have.add(f)
                self.rec("action", "%s outfit: %s" % (how, f))
                self.guess_source(f)

    def guess_source(self, f):
        """Pre-fill 'Outfit made for' from an added outfit's name (the user can change it). Names that say nothing
        leave it as it is."""
        g = core.guess_body([core.shown_name(f)])
        if not g:
            return
        word, t = g
        if t is None:
            self.log(
                "%s looks made for %s, which this tool can't refit from yet. Use the outfit's vanilla version if "
                "it has one." % (core.shown_name(f), word)
            )
            return
        self.guesses[f] = t
        if t != self.src_tag():
            self.src.set(core.body_name(t))
            self.show_body()
            self.log(
                "'Outfit made for' set to %s ('%s' in the name of %s). Change it if that's wrong."
                % (core.body_name(t), word, core.shown_name(f))
            )
        if t == self.tag():
            self.log(
                "That is also 'Your body mod': this outfit already fits %s. Pick another body under "
                "'Your body mod' to refit it." % core.body_name(t)
            )
        kinds = {self.guesses[x] for x in self.lst.get(0, "end") if x in self.guesses}
        if len(kinds) > 1:
            self.log(
                "Note: the outfits in the list look made for different bodies (%s). 'Outfit made for' counts for "
                "all of them, so refit them in groups."
                % ", ".join(core.body_name(k) for k in sorted(kinds))
            )

    # ---- buttons ----
    def pick_game(self):
        d = filedialog.askdirectory(title="Choose your Cyberpunk 2077 folder")
        if d:
            d = core.game_root(d)
            if not d:
                self.msg(
                    "error",
                    "That is not the game folder (no bin\\x64\\Cyberpunk2077.exe). Pick the 'Cyberpunk 2077' "
                    "folder (for the Xbox app: XboxGames\\Cyberpunk 2077\\Content).",
                )
                return
            self.game.set(d)
            self.save()
            self.show_body()

    def pick_out(self):
        d = filedialog.askdirectory(
            title="Choose where to save the refits", initialdir=self.out_dir()
        )
        if d:
            self.out.set(os.path.normpath(d))
            self.s["out"] = self.out.get()
            self.save()

    def pick_body(self, role="body"):
        """'Body file...' next to 'Outfit made for' (role src) or 'Your body mod' (role body)."""
        t = self.src_tag() if role == "src" else self.tag()
        if core.bodies()["bodies"][t].get("game"):
            self.msg(
                "info",
                "%s needs no body file: its shape comes from your game."
                % core.body_name(t),
            )
            return
        f = filedialog.askopenfilename(
            title="Choose the %s download or its .archive" % core.body_name(t),
            filetypes=[
                ("Body mod download or .archive", "*.zip *.7z *.rar *.archive"),
                ("All files", "*.*"),
            ],
        )
        if f:
            self.use_body_file(t, f)

    def use_body_file(self, t, f):
        """Check and remember a body file for body t (the part of 'Body file...' after the file dialog)."""
        f = os.path.normpath(f)
        self.rec("action", "body file picked for %s: %s" % (t, f))
        try:
            core.check_body_file(f, t)
        except core.ToolError as e:
            self.msg("error", str(e))
            return False
        except Exception as e:  # a broken zip or archive
            self.msg(
                "error",
                "Could not read %s (%s). Pick the body mod's download or its main .archive."
                % (os.path.basename(f), e),
            )
            return False
        self.s.setdefault("body_archive", {})[t] = f
        self.save()
        self.show_body()
        return True

    def pick_wk(self):
        d = filedialog.askdirectory(
            title="Choose a folder with WolvenKit.CLI.exe (optional, otherwise it is downloaded)"
        )
        if d:
            exe = core.find_wk_cli(d)
            if not exe:
                self.msg("error", core.WK_NOT_FOUND % (d, core.WK_VERSION))
                return
            d = os.path.dirname(exe)
            self.s["wk_existing"] = os.path.normpath(d)
            self.rec("action", "WolvenKit folder picked: %s" % d)
            self.save()
            self.log("WolvenKit will be copied from %s on the next refit." % d)

    def add_files(self):
        self.add(
            [
                os.path.normpath(f)
                for f in filedialog.askopenfilenames(filetypes=OUTFIT_TYPES)
            ]
        )

    def add_folder(self):
        d = filedialog.askdirectory(title="Choose an unpacked outfit folder")
        if d:
            self.add([os.path.normpath(d)])

    def remove_sel(self):
        for i in reversed(self.lst.curselection()):
            self.rec("action", "removed outfit: %s" % self.lst.get(i))
            self.lst.delete(i)

    def out_dir(self):
        return self.out.get()

    def show_help(self):
        w = tk.Toplevel(self.root)
        w.title("Outfit Refitter - Help")
        w.transient(self.root)
        t = tk.Text(
            w, width=74, height=30, wrap="word", font=("Segoe UI", 10), padx=10, pady=8
        )
        sb = ttk.Scrollbar(w, command=t.yview)
        t["yscrollcommand"] = sb.set
        ttk.Button(w, text="Close", command=w.destroy).pack(side="bottom", pady=6)
        sb.pack(side="right", fill="y")
        t.pack(fill="both", expand=True)
        t.insert("end", HELP_TEXT)
        t["state"] = "disabled"
        return w

    def support_report(self):
        """Write the support report into the output folder, show it in Explorer. Never raises."""
        try:
            settings = dict(
                self.s,
                body=self.tag(),
                install=self.inst.get(),
                replace_own=self.own.get(),
                copy_moves=self.moves.get(),
                make_zip=self.zip.get(),
                chest_room=self.room_mm(),
                pasties_shift=self.pasties_mm(),
                **{"from": self.src_tag()},
            )
            text = support.build(
                self.session,
                settings,
                self.game.get(),
                self.out_dir(),
                list(self.lst.get(0, "end")),
                list(self.results),
            )
            p = support.write(self.out_dir(), text)
        except Exception as e:
            self.rec("error", "support report failed:\n" + traceback.format_exc())
            self.msg("error", "Could not write the support report: %s" % e)
            return None
        self.rec("action", "support report saved: %s" % p)
        try:
            support.show_in_explorer(p)
        except Exception as e:
            self.rec("error", "could not open Explorer: %r" % e)
        self.msg(
            "info",
            "Saved. Send this file to the mod author. You can open it first to see what's inside.\n\n%s"
            % p,
        )
        return p

    def open_out(self):
        os.makedirs(self.out_dir(), exist_ok=True)
        os.startfile(self.out_dir())

    def uninstall(self):
        items = core.installed()
        if not items:
            self.msg("info", "This tool has not put anything into your game.")
            return None
        w = tk.Toplevel(self.root)
        w.title("Installed by Outfit Refitter")
        ttk.Label(
            w,
            text="Select what to remove from the game.\n"
            "Refit = the reshaped file this tool made.\n"
            "Original outfit = the outfit itself; this tool installed it because it was missing.",
            justify="left",
        ).pack(anchor="w", padx=8, pady=4)
        lb = tk.Listbox(w, selectmode="extended", width=90, height=12)
        for e in items:
            lb.insert(
                "end",
                "%s:  %s   (%s%s)"
                % (
                    "Original outfit" if e.get("kind") == "original" else "Refit",
                    e.get("rel", e["file"]),
                    e["outfit"] + ", " if e.get("outfit") else "",
                    e["date"],
                ),
            )
        lb.pack(fill="both", expand=True, padx=8)

        def rm():
            sel = [items[i] for i in lb.curselection()]
            self.rec(
                "action",
                "uninstall picked: %s"
                % ("; ".join(e.get("rel", e["file"]) for e in sel) or "nothing"),
            )
            if sel:
                core.uninstall(sel, self.log, game=self.game.get())
            w.destroy()

        bf = ttk.Frame(w)
        bf.pack(fill="x", padx=8, pady=6)
        ttk.Button(bf, text="Remove selected", command=rm).pack(side="left")
        ttk.Button(bf, text="Select all", command=lambda: lb.select_set(0, "end")).pack(
            side="left", padx=4
        )
        ttk.Button(bf, text="Close", command=w.destroy).pack(side="right")
        w.lb, w.remove = lb, rm  # for the self-test
        return w

    def on_error(self, exc, val, tb):
        """A bug in a button or timer: write it to the log and say so plainly instead of failing silently."""
        try:
            self.log(
                "Unexpected error:\n"
                + "".join(traceback.format_exception(exc, val, tb))
            )
            self.msg(
                "error",
                "Something went wrong in the window. The details are in the log at the bottom.",
            )
        except Exception:
            pass

    def close(self):
        """Window closed: stop the refit, but let a copy into the game finish first, so the game never keeps a
        half-copied file (the uninstall list is saved after every file)."""
        self.cancel.set()
        if (
            self.session.actions
        ):  # an empty run must not push the last real session out of 'previous'
            self.rec("action", "window closed")
            self.session.save()
        core.stop_tools()
        got = core.INSTALL_LOCK.acquire(timeout=120)
        try:
            for i in self.root.tk.call(
                "after", "info"
            ):  # no timer may fire once the window is gone
                self.root.after_cancel(i)
            self.root.destroy()
        finally:
            if got:
                core.INSTALL_LOCK.release()

    def start(self):
        if self.busy:  # the button says 'Stop' while a refit runs
            self.rec("action", "Stop clicked")
            self.log("Stopping ...")
            self.cancel.set()
            core.stop_tools()
            self.go["state"] = "disabled"
            return
        files = list(self.lst.get(0, "end"))
        if not files:
            self.msg("info", "Add at least one outfit first.")
            return
        if self.src_tag() == self.tag():
            self.msg(
                "info",
                "'Outfit made for' and 'Your body mod' are both %s, so there is nothing to refit.\n\n"
                "Set 'Outfit made for' to the body the outfit was made for."
                % self.body.get(),
            )
            return
        self.save()
        self.rec(
            "action",
            "Refit started: %d outfit(s), made for %s, body %s, install %s, replace own version %s, move with the body %s, zip %s, room at the chest %s, move pasties %s, game %s, "
            "output %s"
            % (
                len(files),
                self.src.get(),
                self.body.get(),
                "ticked" if self.inst.get() else "unticked",
                "ticked" if self.own.get() else "unticked",
                "ticked" if self.moves.get() else "unticked",
                "ticked" if self.zip.get() else "unticked",
                "%+.1f mm" % self.room_mm() if self.room_mm() else "not adjusted",
                "%+d mm" % self.pasties_mm() if self.pasties_mm() else "no",
                self.game.get(),
                self.out_dir(),
            ),
        )
        self.busy = True
        self.go.config(text="Stop")
        self.prog["value"] = 0
        self.cancel = threading.Event()
        args = (
            files,
            self.tag(),
            self.game.get(),
            self.out_dir(),
            self.inst.get(),
            self.body_files(),
            self.s.get("wk_existing"),  # forgotten only once a refit worked with it
            self.cancel,
            self.src_tag(),
            self.own.get(),
            self.moves.get(),
            self.zip.get(),
            self.room_mm(),
            self.pasties_mm(),
        )
        # the worker gets only the queue and plain values, never the window objects (Tk is not thread-safe)
        self.worker = threading.Thread(
            target=App.work, args=(self.q,) + args, daemon=True
        )
        self.worker.start()

    @staticmethod
    def work(
        q,
        files,
        tag,
        game,
        out,
        inst,
        body_files,
        wk_existing,
        cancel,
        src,
        own,
        moves,
        make_zip,
        chest_room=0.0,
        pasties_shift=0,
    ):
        put = lambda m: q.put(("log", m))
        try:
            res = core.run_batch(
                files,
                tag,
                game,
                out,
                put,
                lambda f: q.put(("prog", f)),
                inst,
                body_files,
                wk_existing,
                cancel,
                src,
                own,
                moves,
                make_zip,
                chest_room,
                pasties_shift,
            )
            ok = [r for r in res if r[1]]
            put("")
            put(
                "Finished: %d of %d outfit(s) refit. Files are in: %s"
                % (len(ok), len(res), out)
            )
            if ok and not inst:
                put(
                    "To use them, copy the .archive files (and the .xl files next to them) into <game>\\archive\\pc\\mod (or tick 'Install')."
                )
            for r in res:
                if r[2].startswith(("incomplete: ", "nothing could be refit: ")):
                    put("%s is not complete: %s" % (core.shown_name(r[0]), r[2].split(": ", 1)[1]))
            for r in res:
                if r[3]:
                    put(core.MISSING_TEXT % core.shown_name(r[0]))
            if ok:
                put(
                    "Refits are for your own game only. Please don't upload refits of other people's outfits "
                    "without the outfit author's permission."
                )
            q.put(("done", [(r[0], r[1]) for r in res]))
        except core.ToolError as e:
            put("Stopped: %s" % e)
            q.put(("error", str(e)))
        except Exception as e:
            why = core.explain(e)
            put(
                ("Stopped: %s\n" % why if why else "Unexpected error:\n")
                + traceback.format_exc()
            )
            q.put(("error", why or "Unexpected error, see the log."))

    def room_mm(self):
        """'Adjust room at the chest' in mm: 0.0 when unticked or the text is no number."""
        return core.chest_room_value(self.room_on.get(), self.room.get())

    def pasties_mm(self):
        """'Move pasties up / down' in mm: 0 when unticked or the text is no number."""
        return core.pasties_value(self.pasties_on.get(), self.pasties.get())

    def poll(self):
        want = "readonly" if self.pasties_on.get() else "disabled"
        if want != self.pasties_state:
            self.pasties_state = want
            self.pasties_box.config(state=want)
        want = "readonly" if self.room_on.get() else "disabled"
        if want != self.room_state:  # the value list is only active while the box is ticked
            self.room_state = want
            self.room_box.config(state=want)
        try:
            while True:
                kind, v = self.q.get_nowait()
                if kind == "log":
                    self.log(v)
                elif kind == "prog":
                    self.prog["value"] = v
                else:
                    self.busy = False
                    self.go.config(text="Refit", state="normal")
                    self.prog["value"] = 1.0 if kind == "done" else self.prog["value"]
                    if (
                        kind == "done"
                    ):  # the picked WolvenKit is copied now; later runs use the copy
                        self.s.pop("wk_existing", None)
                    self.save()
                    if kind == "done":
                        self.results += v
                    self.rec(
                        "action",
                        "Refit finished" if kind == "done" else "Refit stopped: %s" % v,
                    )
                    self.session.save()
                    if kind == "error":
                        self.msg("error", v)
        except queue.Empty:
            pass
        finally:  # whatever fails above (shown by on_error), the window keeps listening to the worker
            self.root.after(100, self.poll)


def post_drop(root, paths):
    """Self-test helper: send a real WM_DROPFILES to the window, exactly what Explorer sends."""
    import ctypes
    from ctypes import wintypes

    k, u = ctypes.windll.kernel32, ctypes.windll.user32
    k.GlobalAlloc.restype = ctypes.c_void_p
    k.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]
    k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u.PostMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    data = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    h = k.GlobalAlloc(0x42, 20 + len(data))  # DROPFILES header + wide file list
    p = k.GlobalLock(h)
    ctypes.memmove(p, (ctypes.c_uint32 * 5)(20, 0, 0, 0, 1), 20)
    ctypes.memmove(p + 20, data, len(data))
    k.GlobalUnlock(h)
    u.PostMessageW(int(root.wm_frame(), 16), 0x233, h, 0)


def drive(
    outfit,
    tag,
    out,
    log,
    timeout_s=900,
    game=None,
    install=False,
    body_file=None,
    close_on=None,
    src="vanilla",
    body_files=None,
    replace_own=False,
    copy_moves=False,
    stop_on=None,
    make_zip=False,
    chest_room=None,
    pasties_shift=None,
):
    """Self-test: open the real window, drop an outfit on it, pick the body (and game folder, body file, Install
    tick, 'Replace the outfit's own version' tick), press Refit, wait for the result. close_on: text; when it shows up in the log, the window gets a real
    WM_CLOSE (the user clicking X) in the middle of the refit. stop_on: text; when it shows up, the 'Stop' button is
    clicked (state: "stopped", and "button": the button's text after the refit ended).
    -> {"ok": finished without error, "log": [window log lines], "worker": the refit thread}"""
    import ctypes, gc, time

    gc.collect()  # drop Tk objects of an earlier window while we are still on the main thread
    root = tk.Tk()
    app = App(root, [])
    errors, lines = [], []
    messagebox.showerror = lambda title, msg, **kw: errors.append(
        msg
    )  # no modal dialogs in a test
    messagebox.showinfo = lambda title, msg, **kw: errors.append(msg)
    show = app.log
    app.log = lambda m: (lines.append(m), show(m))
    if game:
        app.game.set(game)
    if body_files is not None:
        app.s["body_archive"] = dict(body_files)
    if body_file:
        app.s.setdefault("body_archive", {})[tag] = body_file
    app.out.set(out)
    app.inst.set(install)
    app.own.set(replace_own)
    app.moves.set(copy_moves)
    app.zip.set(make_zip)
    if chest_room is not None:  # the value as text, as a user would pick or type it
        app.room_on.set(True)
        app.room.set(str(chest_room))
    if pasties_shift is not None:
        app.pasties_on.set(True)
        app.pasties.set(str(pasties_shift))
    app.body.set(core.body_name(tag))
    app.show_body()
    t0 = time.time()
    state = {"step": "drop", "ok": False, "log": lines, "worker": None, "closed": False}
    state["errors"] = errors

    def tick():
        if state["step"] == "drop":
            h = app.show_help()
            root.update()
            ok_help = "Steps, in order" in h.winfo_children()[0].get("1.0", "end")
            h.destroy()
            log("  help window %s" % ("OK" if ok_help else "EMPTY"))
            if not ok_help:
                errors.append("help window empty")
            post_drop(root, [outfit])
            state["step"] = "wait_list"
        elif state["step"] == "wait_list":
            if app.lst.size():
                log("  drop arrived in the list: %s" % app.lst.get(0))
                if src:  # None: keep what the window pre-filled from the outfit's name
                    app.src.set(core.body_name(src))
                state["src"] = app.src_tag()
                app.start()
                state["worker"] = app.worker
                state["step"] = "wait_done"
            elif time.time() - t0 > 10:
                log("  FAIL: dropped file never showed up in the list")
                root.destroy()
                return
        elif state["step"] == "wait_done":
            if (
                stop_on
                and app.busy
                and not state.get("stopped")
                and any(stop_on in m for m in lines)
            ):
                log("  clicking 'Stop' during the refit")
                state["stopped"] = app.go["text"] == "Stop"
                app.go.invoke()
            elif close_on and app.busy and any(close_on in m for m in lines):
                log("  closing the window during the refit (WM_CLOSE)")
                ctypes.windll.user32.PostMessageW(int(root.wm_frame(), 16), 0x10, 0, 0)
                state["step"] = "closing"
                state["closed"] = True
            elif not app.busy:
                state["button"] = app.go["text"]
                state["ok"] = not errors
                if errors:
                    log("  FAIL: %s" % errors)
                root.after(300, root.destroy)
                return
            elif time.time() - t0 > timeout_s:
                log("  FAIL: refit did not finish in time")
                root.destroy()
                return
        elif state["step"] == "closing":
            if time.time() - t0 > timeout_s:
                log("  FAIL: the window did not close")
                root.destroy()
                return
        root.after(200, tick)

    root.after(500, tick)
    root.mainloop()
    if state["step"] == "closing":
        state["ok"] = not errors
    del app, root
    gc.collect()
    return state


def drive_script(script, log, timeout_s=900):
    """Self-test: open the real window and run script(app, root), a generator. Each yield gives a function; the
    script goes on once it returns True, while the window keeps running. -> True when the script got to its end."""
    import gc, time

    gc.collect()
    root = tk.Tk()
    app = App(root, [])
    gen, t0, state = script(app, root), time.time(), {"ok": False}

    def stop(delay=0):
        try:
            root.after(delay, root.destroy)
        except tk.TclError:  # the script closed the window itself
            pass

    def step(wait):
        try:
            if wait is None or wait():
                wait = next(gen)
        except StopIteration:
            state["ok"] = True
            return stop(300)
        except Exception:
            log("  FAIL: test script crashed:\n" + traceback.format_exc())
            return stop()
        if time.time() - t0 > timeout_s:
            log("  FAIL: test script timed out")
            return stop()
        root.after(200, step, wait)

    root.after(500, step, None)
    root.mainloop()
    del app, root, gen
    gc.collect()
    return state["ok"]


def drive_uninstall(log, pick=lambda entry: True):
    """Self-test: open the real window, click 'Uninstall...', select the entries pick() accepts, click
    'Remove selected'. -> list of the dialog's lines."""
    import gc

    gc.collect()
    root = tk.Tk()
    app = App(root, [])
    shown = []
    messagebox.showinfo = lambda title, msg, **kw: log("    message: " + msg)

    def go():
        try:
            w = app.uninstall()
            if w is not None:
                items = core.installed()
                shown.extend(w.lb.get(0, "end"))
                for i, e in enumerate(items):
                    if pick(e):
                        w.lb.select_set(i)
                w.remove()
        finally:
            root.after(200, root.destroy)

    root.after(300, go)
    root.mainloop()
    del app, root
    gc.collect()
    for x in shown:
        log("    list: " + x)
    return shown


def room_test(log):
    """Self-test of 'Adjust room at the chest' in the real window: the box is unticked and the value list inactive
    at first; ticking makes it active, unticking inactive again (through poll(), no Tk call from a callback);
    picked values, typed wrong input and values out of range give the right number; the settings are saved.
    -> [problems]"""
    import gc

    gc.collect()
    root = tk.Tk()
    app = App(root, [])
    bad, seen = [], {}
    saved = dict(app.s)
    steps = [
        (lambda: (app.room_on.set(False), app.room.set("+0.0")), None),
        (lambda: seen.update(off=str(app.room_box.cget("state")), v0=app.room_mm()), None),
        (lambda: app.room_on.set(True), None),
        (lambda: seen.update(on=str(app.room_box.cget("state")), n=len(app.room_box.cget("values"))), None),
        (lambda: app.room.set("+2.0"), None),
        (lambda: seen.update(v2=app.room_mm()), None),
        (lambda: app.room.set("abc"), None),
        (lambda: seen.update(vbad=app.room_mm()), None),
        (lambda: app.room.set("99"), None),
        (lambda: seen.update(vhigh=app.room_mm()), None),
        (lambda: app.room.set("-1,5"), None),
        (lambda: (seen.update(vneg=app.room_mm()), app.save(), seen.update(s_on=app.s.get("chest_room_on"), s_v=app.s.get("chest_room"))), None),
        (lambda: (app.pasties_on.set(False), app.pasties.set("+0")), None),
        (lambda: seen.update(p_off=str(app.pasties_box.cget("state")), p0=app.pasties_mm()), None),
        (lambda: (app.pasties_on.set(True), app.pasties.set("+10")), None),
        (lambda: seen.update(p_on=str(app.pasties_box.cget("state")), p_n=len(app.pasties_box.cget("values")), p10=app.pasties_mm()), None),
        (lambda: app.pasties.set("xyz"), None),
        (lambda: seen.update(p_bad=app.pasties_mm()), None),
        (lambda: app.pasties.set("-99"), None),
        (lambda: (seen.update(p_low=app.pasties_mm()), app.pasties.set("-7")), None),
        (lambda: (app.save(), seen.update(ps_on=app.s.get("pasties_on"), ps_v=app.s.get("pasties_shift")), app.pasties_on.set(False)), None),
        (lambda: seen.update(p_off2=str(app.pasties_box.cget("state")), p_voff=app.pasties_mm()), None),
        (lambda: app.room_on.set(False), None),
        (lambda: seen.update(off2=str(app.room_box.cget("state")), voff=app.room_mm()), None),
    ]

    def step(i):
        if i < len(steps):
            steps[i][0]()
            root.after(350, step, i + 1)  # poll() runs every 100 ms in between
        else:
            app.s.clear()
            app.s.update(saved)
            app.save = lambda: None  # closing must not keep the test's values
            core.save_settings(saved)
            root.destroy()

    root.after(400, step, 0)
    root.mainloop()
    del root
    gc.collect()
    want = dict(off="disabled", v0=0.0, on="readonly", n=17, v2=2.0, vbad=0.0, vhigh=6.0, vneg=-1.5, s_on=True, s_v=-1.5, off2="disabled", voff=0.0,
                p_off="disabled", p0=0, p_on="readonly", p_n=61, p10=10, p_bad=0, p_low=-30, ps_on=True, ps_v=-7, p_off2="disabled", p_voff=0)
    for k, v in want.items():
        if seen.get(k) != v:
            bad.append("%s: %r, expected %r" % (k, seen.get(k), v))
    log("  room at the chest / move pasties controls: %s" % ("OK" if not bad else "; ".join(bad)))
    return bad


def main(files=(), close_after_ms=None):
    try:  # sharp text on high-DPI screens
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root, [os.path.abspath(f) for f in files if os.path.exists(f)])
    if close_after_ms:  # used by the self-test to prove the window starts
        root.after(close_after_ms, root.destroy)
    root.mainloop()
    del root
    import gc

    gc.collect()  # free Tk objects here on the main thread, not later in a worker thread
