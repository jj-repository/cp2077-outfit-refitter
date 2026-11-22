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

7. Click 'Refit'. The bar and the log below show the progress. The first run takes longer: the tool downloads WolvenKit (about 45 MB) and reads the body shapes once.

8. Done. If you didn't tick Install, click 'Open output folder' and copy the .archive file(s) into <game folder>\\archive\\pc\\mod. The original outfit must be in your game as well; the refit only reshapes it. The log tells you if it is missing.

Undo
'Uninstall...' lists what this tool put into your game: refits, and original outfits it installed because they were missing. Select what to remove. Or delete the AngelRefit_ / EveRefit_ / VanillaRefit_ file from archive\\pc\\mod yourself.

Good to know
- Pick the right 'Outfit made for'. If an Angel outfit is refit as if it were vanilla, it gets reshaped twice and won't fit. The log warns you when the outfit's name says another body than the one you picked.
- The refit only works together with the original outfit. With Install ticked, the tool checks your game and installs the original if it is missing. An original that is already installed is left as it is.
- Refits are for your own game. Don't upload refits of other people's outfits without the outfit author's permission.
- If something is skipped (for example glasses or props), the log says so; the rest of the outfit is still refit.
- 'WolvenKit...' lets you use a WolvenKit you already have instead of downloading it.

Having a problem?
Click 'Support report...' and send the file it makes to the mod author. It lists what you did in the tool, the log, and what the tool finds in your game folder. Your Windows user name is left out, and no file contents are included. You can open the file first to see what's inside.
"""


# log lines that are errors in the support report
ERROR_LINES = (
    "Unexpected error",
    "Could not refit",
    "Stopped",
    "Not installed",
    "Not copied",
    "The original outfit could not",
)


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
        root.minsize(560, 520)
        pad = dict(padx=8, pady=3)

        f = ttk.Frame(root)
        f.pack(fill="x", **pad)
        ttk.Label(f, text="Game folder:").grid(row=0, column=0, sticky="w")
        self.game = tk.StringVar(value=self.s.get("game") or core.find_game() or "")
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

        a = ttk.Frame(root)
        a.pack(fill="x", **pad)
        self.go = self.button(a, "Refit", self.start)
        self.go.pack(side="left")
        self.button(a, "Open output folder", self.open_out).pack(side="left", padx=4)
        self.button(a, "Uninstall...", self.uninstall).pack(side="left")
        self.button(a, "Help", self.show_help).pack(side="right")
        self.button(a, "Support report...", self.support_report).pack(
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
        traces.append(
            (
                self.inst,
                self.inst.trace_add(
                    "write",
                    lambda *x: self.rec(
                        "action",
                        "install box " + ("ticked" if self.inst.get() else "unticked"),
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
        root.minsize(max(560, a.winfo_reqwidth() + 16), 520)
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
        self.s.update(game=self.game.get(), body=self.tag(), install=self.inst.get())
        core.save_settings(self.s)

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
            d = os.path.normpath(d)
            if not core.is_game_dir(d):
                self.msg(
                    "error",
                    "That is not the game folder (no bin\\x64\\Cyberpunk2077.exe).",
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
            if not os.path.isfile(os.path.join(d, "WolvenKit.CLI.exe")):
                self.msg("error", "No WolvenKit.CLI.exe in that folder.")
                return
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
                core.uninstall(sel, self.log)
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
        if self.busy:
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
            "Refit started: %d outfit(s), made for %s, body %s, install %s, game %s, output %s"
            % (
                len(files),
                self.src.get(),
                self.body.get(),
                "ticked" if self.inst.get() else "unticked",
                self.game.get(),
                self.out_dir(),
            ),
        )
        self.busy = True
        self.go["state"] = "disabled"
        self.prog["value"] = 0
        self.cancel = threading.Event()
        args = (
            files,
            self.tag(),
            self.game.get(),
            self.out_dir(),
            self.inst.get(),
            self.body_files(),
            self.s.pop("wk_existing", None),
            self.cancel,
            self.src_tag(),
        )
        # the worker gets only the queue and plain values, never the window objects (Tk is not thread-safe)
        self.worker = threading.Thread(
            target=App.work, args=(self.q,) + args, daemon=True
        )
        self.worker.start()

    @staticmethod
    def work(q, files, tag, game, out, inst, body_files, wk_existing, cancel, src):
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
            )
            ok = [r for r in res if r[1]]
            put("")
            put(
                "Finished: %d of %d outfit(s) refit. Files are in: %s"
                % (len(ok), len(res), out)
            )
            if ok and not inst:
                put(
                    "To use them, copy the .archive files into <game>\\archive\\pc\\mod (or tick 'Install')."
                )
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
        except Exception:
            put("Unexpected error:\n" + traceback.format_exc())
            q.put(("error", "Unexpected error, see the log."))

    def poll(self):
        try:
            while True:
                kind, v = self.q.get_nowait()
                if kind == "log":
                    self.log(v)
                elif kind == "prog":
                    self.prog["value"] = v
                else:
                    self.busy = False
                    self.go["state"] = "normal"
                    self.prog["value"] = 1.0 if kind == "done" else self.prog["value"]
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
):
    """Self-test: open the real window, drop an outfit on it, pick the body (and game folder, body file, Install
    tick), press Refit, wait for the result. close_on: text; when it shows up in the log, the window gets a real
    WM_CLOSE (the user clicking X) in the middle of the refit.
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
            if close_on and app.busy and any(close_on in m for m in lines):
                log("  closing the window during the refit (WM_CLOSE)")
                ctypes.windll.user32.PostMessageW(int(root.wm_frame(), 16), 0x10, 0, 0)
                state["step"] = "closing"
                state["closed"] = True
            elif not app.busy:
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
