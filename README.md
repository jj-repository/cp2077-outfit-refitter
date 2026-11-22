# Outfit Refitter for Cyberpunk 2077

Make clothing mods fit the body mod you use, yourself, in a few clicks.

It supports the female body mods **Hyst Angel** and **Hyst EVE**.

## Quick start

1. Download **OutfitRefitter.zip** from the [Releases page](https://github.com/jj-repository/cp2077-outfit-refitter/releases).
2. Unzip it anywhere, for example on your Desktop.
3. Double-click **OutfitRefitter.exe** in the `OutfitRefitter` folder.

- **Windows SmartScreen** may say "Windows protected your PC", because the tool is new and not signed.
  Click **More info**, then **Run anyway**.
- If the tool says the **.NET 10 Runtime** is missing, install Microsoft's free
  [.NET 10 Runtime](https://dotnet.microsoft.com/download/dotnet/10.0) and start the tool again.
  WolvenKit needs it.
- **Use the version of the outfit made for the normal (vanilla) body** as input, not an Angel or EVE version.

WolvenKit (GPL-3.0) is downloaded from its official GitHub release on first run. It is not included in the download.

This repo contains only the tool. It contains **no body files, no clothing and no game files**.
Everything it needs is read from your own game and your own downloaded mods.

---

## What problem does this solve?

Body mods like Angel or EVE change the shape of V's body. Clothing has to be made for that exact
shape, or it doesn't fit: skin pokes through the shirt, or the clothes float around the body.

Most clothing mods are made for the normal (vanilla) body. Making a version for another body is
called a **refit**, and it is real work for the clothing author. There are many popular body mods,
so authors get asked again and again: "Can you make an Angel version? An EVE version? ..."
Many outfits never get one.

## What does this tool do?

You give it a clothing mod made for the normal body. It gives you back a small extra file that
reshapes that clothing to your body mod. That file goes into your game next to the original
outfit. If you let it, the tool puts both into your game for you. That's it.

- The original outfit is not changed. Delete the extra file and everything is back to normal.
- It works on the download as it comes: a zip, 7z or rar, an unpacked folder, or a single .archive file.
- If a download has several versions (for example "Vanilla", "EBB", "RB" folders), it uses the vanilla one.

It is automatic, so it is not as perfect as a refit made by hand. Most outfits look right; some
need a hand-made refit. Always look at the result in game.

## What you need

- Cyberpunk 2077 (Steam, GOG or Epic) on Windows.
- The body mod you use, installed normally: [Hyst Angel](https://www.nexusmods.com/cyberpunk2077/mods/14896)
  or [Hyst EVE](https://www.nexusmods.com/cyberpunk2077/mods/29810).
- [ArchiveXL](https://www.nexusmods.com/cyberpunk2077/mods/4198), which most clothing mods already need.
- An internet connection the first time (the tool downloads WolvenKit, see below).
- Microsoft's free [.NET 10 Runtime](https://dotnet.microsoft.com/download/dotnet/10.0). WolvenKit
  needs it. If it is missing, the tool tells you.

## How to use it

1. Install your body mod as usual. The clothing mod too, or let the tool install it (step 5).
2. Unzip Outfit Refitter anywhere (for example on your Desktop) and start **OutfitRefitter.exe**.
3. Check the top of the window:
   - **Game folder** is found automatically. If not, click **Change...** and pick your Cyberpunk 2077 folder.
   - **Your body mod**: choose Angel or EVE. It should say "found: ...". If it doesn't, the body mod is not
     installed in the game, or click **Body file...** and pick the body mod's `.archive` file.
   - **Output folder** is where the refits are saved. Click **Change...** to pick another folder.
4. Drag the clothing mod's download (zip / 7z / rar / folder / .archive) into the window,
   or click **Add outfit...**. You can add several. Use the outfit's **vanilla body** version.
5. Tick **Install into the game when done** if you want the tool to copy the result into your game.
   It also checks whether the original outfit is in your game. If it is not, the tool installs it too,
   from the same download. An original that is already installed is left as it is.
   The tick is off at first, so nothing goes into your game unless you ask for it.
6. Click **Refit**.
   - The **first time** it downloads WolvenKit (about 45 MB) and reads the body shapes from your game.
     This takes a minute or two and only happens once.
   - After that, each outfit takes from a few seconds to a minute.
7. Start the game and look at the outfit.

The results are saved in `Documents\Outfit Refitter` (or the folder you picked). Click **Open output folder** to see them.
They are named like `AngelRefit_<outfit>.archive` or `!!!AngelRefit_<outfit>.archive`.

If you did not let the tool install them: copy the files into `<game folder>\archive\pc\mod`.
The original outfit must be in your game as well. If it is missing, the log says so.

## Removing refits

Click **Uninstall...**. The list shows everything the tool put into your game:
**Refit** (the reshaped file) and **Original outfit** (outfits it installed because they were missing).
Select what you want to remove and click **Remove selected**.
The tool only removes files it installed itself.

## Having a problem?

Click **Support report...** and send the file it makes to the mod author.
It is a plain text file, saved in your output folder; the tool opens that folder for you.
It lists what you did in the tool, the log, any errors, your settings, and what the tool finds in your game folder
(game, ArchiveXL, TweakXL, Codeware, RED4ext, body mods, the outfits' files, the refits).
It contains no file contents, and your Windows user name is replaced. You can open it first to see what's inside.

## Good to know

- **Use the version of the outfit made for the normal (vanilla) body.** If you give it an outfit that was
  already made for Angel or EVE, it reshapes it a second time and it won't fit.
- **The refit needs the original outfit.** It only reshapes the original, so both must be in the game.
  With Install ticked, the tool looks for the original's `.archive` file(s) anywhere in `archive\pc\mod`
  (also in subfolders, where Vortex or MO2 may put them). If they are missing, it installs the original from
  your download: files that are already in an `archive\pc\mod`, `r6`, `red4ext` or `bin` folder keep that
  place, a loose `.archive` / `.xl` goes to `archive\pc\mod`. It never replaces a different file that is
  already there; the log says so instead. Without Install, the tool only tells you when the original is missing.
- **Some parts are skipped on purpose**, and the log says why: glasses, hats and other things that don't
  touch the body, male meshes, versions for other bodies (EBB, RB, ...), and parts the mod already ships
  an Angel/EVE version of.
- **Old outfits work too** (since 1.3): many outfits from before game 2.0 were packed without file names.
  The tool finds the names again from the outfit's other files and refits them like any other outfit. If it
  gives you a version made for another body (EBB, RB, ...), the log warns you; get the vanilla one if there is one.
  Since 1.4, meshes in an old file format that WolvenKit can't read are read and refit by the tool itself
  (the log says "read with the tool's own reader").
- **Some parts can't be refit**: a few meshes neither WolvenKit nor the tool's own reader can read, meshes
  WolvenKit can't write, and clothing in old outfits that no other file points to. The tool skips those parts, tells you, and refits the rest.
- **Safety check**: before writing a file, the tool checks every mesh in it. If anything is broken, it
  writes nothing and tells you, instead of giving you a file that could crash the game.
- **Files starting with `!!!`** replace the outfit's meshes. They load before most mods. If another mod
  also replaces the same clothing, whichever loads first wins.
- **Re-run after updates**: if the clothing mod or your body mod gets updated, refit the outfit again.
- **Gloves and tight hands** can look a little puffy, because the tool pushes clothing slightly out of the skin.

## Please respect the authors

- **Refits are for your own game.** Please do **not** upload refits of other people's outfits
  without the outfit author's permission. The outfit belongs to its author, also when it's refit.
- The body shapes are read from **your own** copy of the body mod. The tool contains nothing of the body
  mods and never uploads anything.

## Credits

- **Hyst** (and xBaebsae) for the Angel and EVE body mods. This tool only works because of their bodies.
  Thanks to Hyst for the permission to support Angel and EVE.
  Body mod pages: [Angel](https://www.nexusmods.com/cyberpunk2077/mods/14896),
  [EVE](https://www.nexusmods.com/cyberpunk2077/mods/29810),
  [refit resources](https://www.nexusmods.com/cyberpunk2077/mods/17544).
- **The WolvenKit team** for [WolvenKit](https://github.com/WolvenKit/WolvenKit) (GPL-3.0), which does all
  the reading and writing of game files. It is downloaded from its official GitHub release, not included here.
- **psiberx** for ArchiveXL.

This tool is a fan project. It is **not affiliated with** or endorsed by CD PROJEKT RED, Hyst, xBaebsae,
the WolvenKit team or Nexus Mods.

---

## Technical details

### How a refit is made

1. WolvenKit unpacks the outfit's `.archive`.
2. The tool reads the outfit's `.ent` / `.app` files to find ArchiveXL *dynamic* meshes
   (`*..._{body}.mesh`).
3. Every mesh is exported to glTF. Meshes with fewer than 20 vertices within 3 cm of the body (glasses,
   hats, props) are left alone. The source shape is always the **vanilla** body: Angel and EVE are too
   close to vanilla for a distance test to tell reliably which body a mesh was made for, so the tool
   picks the vanilla version of a download instead (folders/files named "Vanilla").
4. Each vertex is moved by the difference between the source body and your body: every body vertex
   gets the offset to the nearest vertex of the target body, smoothed over its neighbours, and each
   clothing vertex moves by the inverse-distance-weighted offset of its 8 nearest body vertices.
   Vertices that end up inside the new body are pushed out to 4 mm above the skin.
   Only positions change: UVs, skin weights, normals and morph targets stay the same.
5. WolvenKit imports the moved positions back into a copy of the original mesh.
6. WolvenKit 9.1.1 can write very big mesh buffers (over ~1.4 MB) in a way the game can't read.
   Those meshes are rebuilt directly (positions written into the original render buffer, compressed
   with the game's own Oodle library) and verified with a WolvenKit round trip. If that fails, the mesh is skipped.
7. A mesh WolvenKit can't read at all (seen with game 1.6-era meshes: WolvenKit 9.1.1 and 10.0 beta fail
   on their material list) is read by the tool itself: it parses only the CR2W tables, the bounding box
   and the render blob header (quantization, per-chunk vertex counts, offsets and strides), skips every
   other property by its stored size, and decodes the positions straight from the render buffer. The
   moved positions are written back into the same buffer (same size and layout), compressed with the
   game's Oodle library. Before writing, the unchanged positions must give back the same content byte for
   byte; the result may differ from the source only in position bytes and the patched quantization /
   bounding-box floats. WolvenKit is not used for that mesh's import.
8. WolvenKit packs the archive. The final check decodes every buffer of every mesh with the game's
   Oodle library, compares zero-filled tails against the source mesh, and checks the archive index
   checksum (a wrong one crashes the game at startup). Any problem = no file is written.

Output:
- Dynamic meshes become **new** files `<path>_angel.mesh` / `<path>_eve.mesh`, which ArchiveXL picks
  automatically when that body is installed: `AngelRefit_<outfit>.archive`.
- Static meshes (no `{body}` support in the mod) are replaced in place: `!!!AngelRefit_<outfit>.archive`.
  No `.xl` file is needed for either.

### Body shapes, WolvenKit and the Oodle DLL

- Body shapes are exported on first run from the user's own files into
  `%LOCALAPPDATA%\OutfitRefitter\cages\`: the vanilla female body + arms from the base game, and the
  same three meshes from the installed body mod's `.archive`. They are re-read when the source file
  changes. Nothing is shipped.
- WolvenKit.CLI **9.1.1** (`WolvenKit.Console-9.1.1.zip`) is downloaded from
  `github.com/WolvenKit/WolvenKit/releases`, checked against its SHA-256
  (`a14eae46931251635256e7b209cd4aa998ae5039a5fbd506fefaab671bf7f451`) and unpacked to
  `%LOCALAPPDATA%\OutfitRefitter\wolvenkit\`. Its `appsettings.json` is set to export meshes without
  materials and with all LODs. **WolvenKit...** in the window copies an existing WolvenKit install instead.
- The game's `oo2ext_7_win64.dll` is copied from `<game>\bin\x64` to `%LOCALAPPDATA%`, where WolvenKit
  9.1.1 looks for it. Without it, WolvenKit silently uses a fallback that breaks big buffers.
- Work runs at below-normal priority, so it can run while the game is open.

### Files

| File | What it does |
| --- | --- |
| `outfit_refitter.py` | Entry point: window, `--refit` command line, `--selftest` |
| `gui.py` | The window (Python's built-in tkinter) |
| `core.py` | Game detection, WolvenKit download, body shapes, unpacking downloads, install list |
| `refit.py` | The refit itself, the WolvenKit buffer repair, the own mesh reader (old meshes), the final safety check |
| `rdar.py` | Reading `.archive` / CR2W files, the game's Oodle library, buffer checks |
| `bodies.json` | Supported bodies |
| `support.py` | The session record and the support report |
| `selftest.py` | Headless test |
| `build_exe.bat` | Builds the Windows version |

### Running from source

```
python -m pip install numpy scipy
python outfit_refitter.py                                   (window)
python outfit_refitter.py --refit "C:\Downloads\Some Outfit.zip" --body angel [--install]
```

### Building the Windows version

```
build_exe.bat
```

This creates a local `.venv`, installs numpy, scipy and PyInstaller, and runs
`PyInstaller --onedir --windowed --name OutfitRefitter --add-data "bodies.json;." --add-data "README.md;." --add-data "LICENSE;." outfit_refitter.py`.
The result is `dist\OutfitRefitter\` (zip that folder for release).

### Self-test

```
python outfit_refitter.py --selftest --home <empty folder> --out <folder>
       [--body-archive eve=<path to 05_Hyst_EVE_BODY.archive>]
       [--case angel=<outfit>=<a refit you trust.archive>] [--case eve=<outfit>] ...
```

It checks the download-variant selection, detects the game, downloads and checks WolvenKit into `--home`
(a real first run), extracts the body shapes, refits each `--case`, runs the safety check, lists the
output archive and, if a reference refit is given, prints the vertex distance per mesh. `--gui` also opens
the window for two seconds. `--gui-install <folder> --gui-install-outfit <outfit>=><expected file>;...` runs the
Install tests in the real window on a fake game folder it makes in `<folder>` (original missing, original already
there, Install unticked, Uninstall, closing the window mid-refit); the real game folder is only read.
`--support <folder> --support-outfit <outfit>` (three times) tests **Support report...** in the real window on two
fake game folders (with and without ArchiveXL etc.), including a restart. `--own-reader <outfit>` checks the
own mesh reader on every mesh of an outfit: same positions as WolvenKit's export, byte-exact round trip, and a
displaced write that WolvenKit reads back as written. Exit code 0 = passed. In the windowed `.exe`, the text goes to
`%TEMP%\OutfitRefitter_console.log`.

### Adding a body

Add an entry to `bodies.json`: the key is the ArchiveXL body name (`{body}` → `_<key>.mesh`), plus a
display name, file-name patterns of the body's `.archive`, and its mod page. The body archive must replace
the vanilla female body meshes listed under `meshes`. **Only add bodies whose authors allow their
bodies to be used this way.**

### Data folder

`%LOCALAPPDATA%\OutfitRefitter\`: `settings.json` (game folder, body, output folder, install tick), `installed.json` (the uninstall list: refits and original outfits the tool installed), `wolvenkit\`,
`cages\`, `work\` (temporary), `last_session.txt` and `previous_session.txt` (what happened in the window in this
and the last run, for the support report). Delete the folder to reset the tool.
