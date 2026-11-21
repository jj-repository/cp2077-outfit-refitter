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
                root.after(0, callback, files)
                return 0
            return u.CallWindowProcW(old, h, msg, wp, lp)

        root._drop_proc = WNDPROC(
            proc
        )  # keep a reference or Windows calls freed memory
        old = u.SetWindowLongPtrW(
            hwnd, -4, ctypes.cast(root._drop_proc, ctypes.c_void_p)
        )
        return True
    except Exception:
        return False


class App:
    def __init__(self, root, files):
        self.root, self.q, self.busy = root, queue.Queue(), False
        self.s = core.load_settings()
        self.tags = list(core.bodies()["bodies"])
        root.title("Body Refitter")
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
        self.inst = tk.BooleanVar(value=self.s.get("install", True))
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
        ttk.Button(a, text="WolvenKit...", command=self.pick_wk).pack(side="right")

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
                    "Body Refitter",
                    "That is not the game folder (no bin\\x64\\Cyberpunk2077.exe).",
                )
                return
            self.game.set(d)
            self.save()
            self.show_body()

    def pick_body(self):
        f = filedialog.askopenfilename(
            title="Choose the body mod's .archive", filetypes=[("Archive", "*.archive")]
        )
        if f:
            try:
                core.check_body_archive(f)
            except (core.ToolError, OSError, ValueError) as e:
                messagebox.showerror("Body Refitter", str(e))
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
                    "Body Refitter", "No WolvenKit.CLI.exe in that folder."
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
        return self.s.get("out") or core.DEFAULT_OUT

    def open_out(self):
        os.makedirs(self.out_dir(), exist_ok=True)
        os.startfile(self.out_dir())

    def uninstall(self):
        items = core.installed()
        if not items:
            messagebox.showinfo("Body Refitter", "No refits installed by this tool.")
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
            messagebox.showinfo("Body Refitter", "Add at least one outfit first.")
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
        threading.Thread(target=self.work, args=args, daemon=True).start()

    def work(self, files, tag, game, out, inst, body_file, wk_existing):
        put = lambda m: self.q.put(("log", m))
        try:
            res = core.run_batch(
                files,
                tag,
                game,
                out,
                put,
                lambda f: self.q.put(("prog", f)),
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
            self.q.put(("done", None))
        except core.ToolError as e:
            put("Stopped: %s" % e)
            self.q.put(("error", str(e)))
        except Exception:
            put("Unexpected error:\n" + traceback.format_exc())
            self.q.put(("error", "Unexpected error, see the log."))

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
                        messagebox.showerror("Body Refitter", v)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)


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
