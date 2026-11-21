"""The small window. All real work is in core.run_batch (same code the --selftest uses)."""

import os, queue, threading, traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import core

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
Most clothing mods are made for the normal (vanilla) body. This tool reshapes an outfit so it fits your body mod (Hyst Angel or Hyst EVE). It makes one small extra file; the original outfit stays untouched.

Steps, in order
1. Game folder: check that the top line shows your Cyberpunk 2077 folder. If not, click 'Change...' next to it and pick the folder that contains 'bin' and 'archive'.

2. Your body mod: pick Hyst Angel or Hyst EVE. Below it you should see 'found: ...'. If it says 'not found', install that body mod first, or click 'Body file...' and pick its .archive.

3. Output folder: where the finished refits are saved. Click 'Change...' to pick another folder.

4. Add the outfit: drag its download (zip / 7z / rar), its unpacked folder or its .archive file into the big list. Or use 'Add outfit...' / 'Add folder...'. Use the outfit's VANILLA-body version. You can add several outfits at once.

5. Install: tick 'Install into the game when done' if the tool should put the refit straight into your game. Leave it unticked to only save the file in the output folder.

6. Click 'Refit'. The bar and the log below show the progress. The first run takes longer: the tool downloads WolvenKit (about 45 MB) and reads the body shape from your game once.

7. Done. If you didn't tick Install, click 'Open output folder' and copy the .archive file(s) into <game folder>\\archive\\pc\\mod. Keep the original outfit installed as well; the refit only reshapes it.

Undo
'Uninstall refits...' removes refits this tool installed. Or delete the AngelRefit_/EveRefit_ file from archive\\pc\\mod.

Good to know
- Refits are for your own game. Don't upload refits of other people's outfits without the outfit author's permission.
- If something is skipped (for example glasses or props), the log says so; the rest of the outfit is still refit.
- 'WolvenKit...' lets you use a WolvenKit you already have instead of downloading it.
"""


class App:
    def __init__(self, root, files):
        self.root, self.q, self.busy = root, queue.Queue(), False
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
        ttk.Button(f, text="Change...", command=self.pick_game).grid(row=0, column=2)
        ttk.Label(f, text="Your body mod:").grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        names = [core.body_name(t) for t in self.tags]
        self.body = tk.StringVar(value=core.body_name(self.s.get("body", self.tags[0])))
        cb = ttk.Combobox(
            f, textvariable=self.body, values=names, state="readonly", width=18
        )
        cb.grid(row=1, column=1, sticky="w", padx=4, pady=(6, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.show_body())
        ttk.Button(f, text="Body file...", command=self.pick_body).grid(
            row=1, column=2, pady=(6, 0)
        )
        self.body_info = ttk.Label(f, text="", foreground="#555")
        self.body_info.grid(row=2, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Label(f, text="Output folder:").grid(
            row=3, column=0, sticky="w", pady=(6, 0)
        )
        self.out = tk.StringVar(value=self.s.get("out") or core.DEFAULT_OUT)
        ttk.Entry(f, textvariable=self.out, state="readonly").grid(
            row=3, column=1, sticky="ew", padx=4, pady=(6, 0)
        )
        ttk.Button(f, text="Change...", command=self.pick_out).grid(
            row=3, column=2, pady=(6, 0)
        )
        f.columnconfigure(1, weight=1)

        box = ttk.LabelFrame(
            root, text="Outfits to refit: drop zip / 7z / rar / folder / .archive here"
        )
        box.pack(fill="both", expand=True, **pad)
        self.lst = tk.Listbox(box, height=6, selectmode="extended", activestyle="none")
        self.lst.pack(fill="both", expand=True, padx=6, pady=4)
        b = ttk.Frame(box)
        b.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(b, text="Add outfit...", command=self.add_files).pack(side="left")
        ttk.Button(b, text="Add folder...", command=self.add_folder).pack(
            side="left", padx=4
        )
        ttk.Button(b, text="Remove", command=self.remove_sel).pack(side="left")

        o = ttk.Frame(root)
        o.pack(fill="x", **pad)
        self.inst = tk.BooleanVar(value=self.s.get("install", False))
        ttk.Checkbutton(
            o, text="Install into the game when done", variable=self.inst
        ).pack(side="left")

        a = ttk.Frame(root)
        a.pack(fill="x", **pad)
        self.go = ttk.Button(a, text="Refit", command=self.start)
        self.go.pack(side="left")
        ttk.Button(a, text="Open output folder", command=self.open_out).pack(
            side="left", padx=4
        )
        ttk.Button(a, text="Uninstall refits...", command=self.uninstall).pack(
            side="left"
        )
        ttk.Button(a, text="Help", command=self.show_help).pack(side="right")
        ttk.Button(a, text="WolvenKit...", command=self.pick_wk).pack(
            side="right", padx=4
        )

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

        if not enable_drop(root, self.add):
            box["text"] = "Outfits to refit (zip / 7z / rar / folder / .archive)"
        self.add(files)
        self.show_body()
        if not core.is_game_dir(self.game.get()):
            self.log(
                "Game folder not found automatically. Click 'Change...' and pick your Cyberpunk 2077 folder."
            )
        else:
            self.log("Ready. Add outfits, check the body, then press Refit.")
        root.after(100, self.poll)

    # ---- helpers ----
    def tag(self):
        return self.tags[[core.body_name(t) for t in self.tags].index(self.body.get())]

    def log(self, msg):
        self.log_box["state"] = "normal"
        self.log_box.insert("end", msg + "\n")
        self.log_box.see("end")
        self.log_box["state"] = "disabled"

    def save(self):
        self.s.update(game=self.game.get(), body=self.tag(), install=self.inst.get())
        core.save_settings(self.s)

    def body_file(self):
        return self.s.get("body_archive", {}).get(self.tag())

    def show_body(self):
        g, t = self.game.get(), self.tag()
        p = (
            self.body_file()
            if self.body_file() and os.path.isfile(self.body_file())
            else (core.find_body_archive(g, t) if core.is_game_dir(g) else None)
        )
        self.body_info["text"] = (
            ("found: " + os.path.basename(p))
            if p
            else "not found in the game - install %s or use 'Body file...'"
            % core.body_name(t)
        )

    def add(self, files):
        have = set(self.lst.get(0, "end"))
        for f in files:
            if f not in have:
                self.lst.insert("end", f)

    # ---- buttons ----
    def pick_game(self):
        d = filedialog.askdirectory(title="Choose your Cyberpunk 2077 folder")
        if d:
            d = os.path.normpath(d)
            if not core.is_game_dir(d):
                messagebox.showerror(
                    "Outfit Refitter",
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

    def pick_body(self):
        f = filedialog.askopenfilename(
            title="Choose the body mod's .archive", filetypes=[("Archive", "*.archive")]
        )
        if f:
            try:
                core.check_body_archive(f)
            except (core.ToolError, OSError, ValueError) as e:
                messagebox.showerror("Outfit Refitter", str(e))
                return
            self.s.setdefault("body_archive", {})[self.tag()] = os.path.normpath(f)
            self.save()
            self.show_body()

    def pick_wk(self):
        d = filedialog.askdirectory(
            title="Choose a folder with WolvenKit.CLI.exe (optional, otherwise it is downloaded)"
        )
        if d:
            if not os.path.isfile(os.path.join(d, "WolvenKit.CLI.exe")):
                messagebox.showerror(
                    "Outfit Refitter", "No WolvenKit.CLI.exe in that folder."
                )
                return
            self.s["wk_existing"] = os.path.normpath(d)
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
        t.pack(fill="both", expand=True)
        t.insert("end", HELP_TEXT)
        t["state"] = "disabled"
        ttk.Button(w, text="Close", command=w.destroy).pack(pady=6)
        return w

    def open_out(self):
        os.makedirs(self.out_dir(), exist_ok=True)
        os.startfile(self.out_dir())

    def uninstall(self):
        items = core.installed()
        if not items:
            messagebox.showinfo("Outfit Refitter", "No refits installed by this tool.")
            return
        w = tk.Toplevel(self.root)
        w.title("Installed refits")
        ttk.Label(w, text="Select refits to remove from the game:").pack(
            anchor="w", padx=8, pady=4
        )
        lb = tk.Listbox(w, selectmode="extended", width=70, height=12)
        for e in items:
            lb.insert("end", "%s   (%s)" % (e["file"], e["date"]))
        lb.pack(fill="both", expand=True, padx=8)

        def rm():
            names = [items[i]["file"] for i in lb.curselection()]
            if names:
                core.uninstall(names, self.log)
            w.destroy()

        bf = ttk.Frame(w)
        bf.pack(fill="x", padx=8, pady=6)
        ttk.Button(bf, text="Remove selected", command=rm).pack(side="left")
        ttk.Button(bf, text="Select all", command=lambda: lb.select_set(0, "end")).pack(
            side="left", padx=4
        )
        ttk.Button(bf, text="Close", command=w.destroy).pack(side="right")

    def start(self):
        if self.busy:
            return
        files = list(self.lst.get(0, "end"))
        if not files:
            messagebox.showinfo("Outfit Refitter", "Add at least one outfit first.")
            return
        self.save()
        self.busy = True
        self.go["state"] = "disabled"
        self.prog["value"] = 0
        args = (
            files,
            self.tag(),
            self.game.get(),
            self.out_dir(),
            self.inst.get(),
            self.body_file(),
            self.s.pop("wk_existing", None),
        )
        # the worker gets only the queue, never the window objects (Tk is not thread-safe)
        threading.Thread(target=App.work, args=(self.q,) + args, daemon=True).start()

    @staticmethod
    def work(q, files, tag, game, out, inst, body_file, wk_existing):
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
                body_file,
                wk_existing,
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
            if ok:
                put(
                    "Refits are for your own game only. Please don't upload refits of other people's outfits "
                    "without the outfit author's permission."
                )
            q.put(("done", None))
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
                    try:
                        open(
                            os.path.join(core.DATA, "last_log.txt"),
                            "w",
                            encoding="utf-8",
                        ).write(self.log_box.get("1.0", "end"))
                    except OSError:
                        pass
                    if kind == "error":
                        messagebox.showerror("Outfit Refitter", v)
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


def drive(outfit, tag, out, log, timeout_s=900):
    """Self-test: open the real window, drop an outfit on it, pick the body, press Refit,
    wait for the result. Returns True when the refit finished without error."""
    import gc, time

    gc.collect()  # drop Tk objects of an earlier window while we are still on the main thread
    root = tk.Tk()
    app = App(root, [])
    errors = []
    messagebox.showerror = lambda title, msg, **kw: errors.append(
        msg
    )  # no modal dialogs in a test
    messagebox.showinfo = lambda title, msg, **kw: errors.append(msg)
    app.out.set(out)
    app.inst.set(False)
    app.body.set(core.body_name(tag))
    t0 = time.time()
    state = {"step": "drop", "ok": False}

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
                app.start()
                state["step"] = "wait_done"
            elif time.time() - t0 > 10:
                log("  FAIL: dropped file never showed up in the list")
                root.destroy()
                return
        elif state["step"] == "wait_done":
            if not app.busy:
                state["ok"] = not errors
                if errors:
                    log("  FAIL: %s" % errors)
                root.after(300, root.destroy)
                return
            if time.time() - t0 > timeout_s:
                log("  FAIL: refit did not finish in time")
                root.destroy()
                return
        root.after(200, tick)

    root.after(500, tick)
    root.mainloop()
    del app, root
    gc.collect()
    return state["ok"]


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
