# Outfit Refitter for Cyberpunk 2077

Make clothing mods fit the body mod you use, yourself, in a few clicks.

It supports the female body mods **Hyst Angel** and **Hyst EVE** and the normal (vanilla) body, in every direction:
vanilla → Angel / EVE, Angel / EVE → vanilla, and Angel ↔ EVE.

## Quick start

1. Download **OutfitRefitter.zip** from the [Releases page](https://github.com/jj-repository/cp2077-outfit-refitter/releases).
2. Unzip it anywhere, for example on your Desktop.
3. Double-click **OutfitRefitter.exe** in the `OutfitRefitter` folder.

- **Windows SmartScreen** may say "Windows protected your PC", because the tool is new and not signed.
  Click **More info**, then **Run anyway**.
- If the tool says the **.NET 10 Runtime** is missing, install Microsoft's free
  [.NET 10 Runtime](https://dotnet.microsoft.com/download/dotnet/10.0) and start the tool again.
  WolvenKit needs it.
- Tell the tool which body the outfit is made for (**Outfit made for**, usually *Vanilla*). It fills this in from the
  outfit's name when the name says Angel, EVE or Vanilla.

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

You give it a clothing mod and say which body it is made for. It gives you back a small extra file that
reshapes that clothing to your body. That file goes into your game next to the original
outfit. If you let it, the tool puts both into your game for you. That's it.

- Most outfits are made for the normal body: refit them to Angel or EVE.
- An outfit made only for Angel or EVE can be refit to the normal body (if you play without a body mod) or
  to the other Hyst body.
- The original outfit is not changed. Delete the extra file and everything is back to normal.
- It works on the download as it comes: a zip, 7z or rar, an unpacked folder, or a single .archive file.
- If a download has several versions (for example "Vanilla", "Angel", "EBB" folders), it uses the one for the body
  you picked under **Outfit made for**, else the vanilla one.

It is automatic, so it is not as perfect as a refit made by hand. Most outfits look right; some
need a hand-made refit. Always look at the result in game.

## What you need

- Cyberpunk 2077 (Steam, GOG or Epic) on Windows.
- The body mod you use, installed normally: [Hyst Angel](https://www.nexusmods.com/cyberpunk2077/mods/14896)
  or [Hyst EVE](https://www.nexusmods.com/cyberpunk2077/mods/29810). (Nothing, if you refit to the normal body.)
- For an outfit made for a body you don't have installed (for example an Angel outfit while you use EVE): that
  body mod's download (zip / 7z / rar) or its `.archive`. The tool only reads the body shape from it; it installs nothing.
- [ArchiveXL](https://www.nexusmods.com/cyberpunk2077/mods/4198), which most clothing mods already need.
- An internet connection the first time (the tool downloads WolvenKit, see below).
- Microsoft's free [.NET 10 Runtime](https://dotnet.microsoft.com/download/dotnet/10.0). WolvenKit
  needs it. If it is missing, the tool tells you.

## How to use it

1. Install your body mod as usual. The clothing mod too, or let the tool install it (step 5).
2. Unzip Outfit Refitter anywhere (for example on your Desktop) and start **OutfitRefitter.exe**.
3. Check the top of the window:
   - **Game folder** is found automatically. If not, click **Change...** and pick your Cyberpunk 2077 folder.
   - **Outfit made for**: the body the outfit was made for. Leave it on *Vanilla (normal body)* for normal outfits.
     When you add an outfit whose name says `ANGEL`, `EVE` or `Vanilla`, the tool sets it for you (the log says so);
     change it if that's wrong. If the outfit's body mod is not installed in your game, click **Body file...** next
     to it and pick that body mod's download (zip / 7z / rar) or its `.archive`. It is read once and kept as a body
     shape; nothing is installed.
   - **Your body mod**: the body you use: Angel, EVE, or *Vanilla (normal body)* if you play without a body mod.
     It should say "found: ...". If it doesn't, the body mod is not installed in the game; install it, or click
     **Body file...** and pick its download or `.archive`.
   - **Output folder** is where the refits are saved. Click **Change...** to pick another folder.
4. Drag the clothing mod's download (zip / 7z / rar / folder / .archive) into the window,
   or click **Add outfit...**. You can add several, but **Outfit made for** counts for all of them:
   add outfits for one body at a time.
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
They are named after the body they fit, like `AngelRefit_<outfit>.archive`, `!!!EveRefit_<outfit>.archive` or
`!!!VanillaRefit_<outfit>.archive`.

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

- **Pick the right "Outfit made for".** If an outfit made for Angel is refit as if it were made for the normal
  body, it is reshaped twice and won't fit. When the outfit's name says another body than the one you picked, the
  log warns you (the refit still runs, in case the name is wrong). Outfits for other body mods (EBB, RB, VTK, ...)
  can't be used as the source yet; use their vanilla version.
- **Refitting from Angel or EVE** works best when the outfit was really made on that body. Each direction is a
  single step (for example Angel → EVE goes straight from one body shape to the other, not over vanilla).
- **The refit needs the original outfit.** It only reshapes the original, so both must be in the game.
  With Install ticked, the tool looks for the original's `.archive` file(s) anywhere in `archive\pc\mod`
  (also in subfolders, where Vortex or MO2 may put them). If they are missing, it installs the original from
  your download: files that are already in an `archive\pc\mod`, `r6`, `red4ext` or `bin` folder keep that
  place, a loose `.archive` / `.xl` goes to `archive\pc\mod`. It never replaces a different file that is
  already there; the log says so instead. Without Install, the tool only tells you when the original is missing.
- **Some parts are skipped on purpose**, and the log says why: glasses, hats and other things that don't
  touch the body, male meshes, versions for other bodies (EBB, RB, ...), and parts the mod already ships
  a version for your body of (for example a normal-body version when you refit to vanilla).
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
- **Layered outfits** (since 1.6): when an outfit has several layers, for example a top, a shirt over it and a
  jacket, the tool keeps each layer 1 mm above the ones under it, so inner layers don't poke through. It reads the
  order from the outfit's own files (ArchiveXL outfit slots like TorsoUnder / TorsoMiddle / TorsoOuter in its
  `r6\tweaks` file). Add the whole download (or the `.archive` from your game folder), not a loose `.archive`
  without its tweak file, so the tool can find them. Outfits without that information are refit as before.
- **Chest size**: Hyst tells you to set V's chest size (*Breast*) to *Big* for Angel. That setting changes how V looks
  nude; in clothes her chest is the same with every setting, so refits fit all of them.
- **Rig mods that change the breast area** (for example "All the Rigs"): Hyst says they make some clothes clip with
  these bodies. The tool can't fix that.

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
3. Every mesh is exported to glTF. Meshes with fewer than 20 vertices within 3 cm of the source or target body
   (glasses, hats, props) are left alone. The source body is what the user picks under **Outfit made for**
   (default vanilla, pre-filled from the outfit's file name: `ANGEL`, `EVE`, `Vanilla`, or this tool's own
   `<Body>Refit_` names). It is not guessed from the geometry: Angel and EVE are too close to vanilla for a
   distance test to tell reliably which body a mesh was made for. In a download with several versions, the
   version for the source body is used (folders/files named after it), else the vanilla one. When the chosen
   files' names say another body than the source, the log warns (never blocks).
4. Each vertex is moved by the difference between the source body and the target body: every source body vertex
   gets the offset to the nearest vertex of the target body, smoothed over its neighbours, and each
   clothing vertex moves by the inverse-distance-weighted offset of its 8 nearest source body vertices.
   Vertices that end up inside the target body are pushed out to 4 mm above the skin.
   Only positions change: UVs, skin weights, normals and morph targets stay the same.
   Every pair is one direct step (Angel → EVE does not go over vanilla, which would add the error and the
   push-out twice).
   - **Garment layers** (1.6): the outfit's TweakXL records (`r6\tweaks\*.yaml` of the download, or of the game
     folder for an installed `.archive`) give each item its ArchiveXL / Equipment-EX outfit slot (`placementSlots`,
     else the slot Equipment-EX gives its base item: inner chest → TorsoInner, outer chest → TorsoOuter, legs →
     LegsMiddle). Item → meshes: `entityName` → the ArchiveXL factory `.csv` → `.ent` → every `.ent` / `.app` it
     refers to → meshes (`{gender}` = `w`, `{body}` = the source body). Layer order = Equipment-EX's
     `garmentOffset`. Torso slots are layered among themselves, legs among themselves (a top can be tucked in or
     worn over the trousers, so torso vs legs is left alone), Body slots with both. Garments are refit inner first;
     each outer vertex is lifted along the target body's normal to 1 mm above the highest inner-layer vertex over
     the same skin spot (at least 3 inner vertices whose foot point on the skin is within 6 mm; up to 4 passes; same
     LOD). Meshes without a known slot, or claimed by items with different slots (one `.app` shared by several
     items), are not layered. Works the same for static and `{body}` outfits and for the own mesh reader
     (old-format meshes; LOD from each render chunk's `lodMask`).
   - **Chest size** (checked for 1.6). Target: the body mesh as it is = V's clothed chest, with every chest size
     setting; same as 1.0-1.5. Evidence (2026-11, from files; not checked in game): hand-made Angel garments clear
     the full mesh as it is and not the morphed chest. Over 309,000 chest-band vertices of Peachu's ANGEL tops, the
     JoyToy minidress and xBaebsae's (Angel co-author) vanilla-clothing refits, 2.1 % are deeper than 1 mm inside the
     full mesh (Peachu's nine current tops: 0.0 % each), 4.3 % inside full + the character creator's `breast_big`
     morph, 6.2 % inside full + 2 x it; Hyst's own Angel garments: 0.0 % vs 2.0-6.3 %. Their authors use Hyst's
     recommended chest size *Big*, so with *Big* the clothed chest is the full mesh; with *Default* (morph weight 0)
     the nude chest is the full mesh, and morph weights can't go below 0, so the clothed chest is the full mesh too
     (full minus the Big morph, tried as a 'Default' target, folds the under-bust crease: 62 inverted triangles).
     *Big* only changes V nude (full + Big has the same chest as Angel's `femv_boobs_natural` mesh), which is the
     "toggleable chest" Hyst asks for *Big* for. Angel's body mesh has a `GarmentSupport` shape key equal to
     `breast_big` (max difference 0.04 mm); vanilla's points the other way (about minus `breast_big` on the chest).
     Angel's other chest meshes (`femv_boobs_squish` / `femv_boobs_natural`) replace the chest only under garments
     that carry the visual tags `hyst_boobs_*` (`ANGEL.xl`), which vanilla clothing doesn't. EVE: no GarmentSupport
     shape key, `breast_big` at most 6.8 mm (median 1.1 mm).
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

Output (`<Body>` = the target: `Angel`, `Eve`, `Vanilla`):
- Dynamic meshes (`*_{body}.mesh`): the source body's member (`_base_body` for vanilla, `_angel`, `_eve`) is refit
  and written as a **new** file for the target body, which ArchiveXL picks automatically when that body is
  installed: `<path>_angel.mesh` / `<path>_eve.mesh`, or `<path>_base_body.mesh` for vanilla (ArchiveXL's name for
  "no body mod"). Archive: `<Body>Refit_<outfit>.archive`. A member the mod already has is never replaced; most
  `{body}` outfits already ship `_base_body`, so refitting those to vanilla has nothing to do (the log says so).
- Static meshes (no `{body}` support in the mod) are replaced in place: `!!!<Body>Refit_<outfit>.archive`. For
  vanilla this is how an Angel-only or EVE-only outfit fits the normal body.
  No `.xl` file is needed for either.

### Body shapes, WolvenKit and the Oodle DLL

- Body shapes are exported when first needed from the user's own files into
  `%LOCALAPPDATA%\OutfitRefitter\cages\<body>\` (`van` for vanilla): the vanilla female body + arms from the base
  game, and the same three meshes from a body mod's `.archive`, either installed in `archive\pc\mod` or picked with
  **Body file...** (an `.archive`, or the body mod's download: zip / 7z / rar / folder; only its `.archive` files are
  extracted, into a temporary folder, and the one holding the three body meshes is used). They are re-read when the
  source file changes. Nothing is shipped, and a picked body file is never installed.
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
| `refit.py` | The refit itself (incl. garment layers), the WolvenKit buffer repair, the own mesh reader (old meshes), the final safety check |
| `rdar.py` | Reading `.archive` / CR2W files, the game's Oodle library, buffer checks |
| `bodies.json` | Supported bodies (source and target): names, ArchiveXL names, body file patterns, name hints |
| `support.py` | The session record and the support report |
| `selftest.py` | Headless test |
| `build_exe.bat` | Builds the Windows version |

### Running from source

```
python -m pip install numpy scipy
python outfit_refitter.py                                   (window)
python outfit_refitter.py --refit "C:\Downloads\Some Outfit.zip" --body angel [--from vanilla] [--install]
python outfit_refitter.py --refit "Some Outfit-ANGEL.archive" --from angel --body eve --body-file eve="EVE BODY.zip"
```

`--body`: the body to refit to; `--from`: the body the outfit is made for (`vanilla`, `angel`, `eve`; default
`vanilla`); `--body-file TAG=FILE`: a body's `.archive` or download when it is not installed in the game.

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
       [--case angel=<outfit>=<a refit you trust.archive>[=exact]] [--case angel>vanilla=<Angel outfit>[=<reference>]] ...
       [--compare <a.archive>=<b.archive>] [--roundtrip angel=<vanilla outfit>[=<max mean mm>]]
       [--layers angel=<vanilla outfit>=<inner stem>,<stem>,<outer stem>] [--chest angel=<vanilla outfit>]
```

`--layers` refits a layered outfit and counts outer-layer vertices below an inner layer, in the 1.5 result
(recomputed) and in the output (must be 0); `--chest` refits an outfit and counts garment vertices inside the
body as the game shows it in clothes (must be 0).

It checks the download-variant selection and the source-body logic (name guess, wrong-body warning, picking a
download's version, body file checks), detects the game, downloads and checks WolvenKit into `--home`
(a real first run), extracts the body shapes, refits each `--case` (`SRC>TGT`, source default vanilla), runs the
safety check, lists the output archive and, if a reference refit is given, prints the vertex distance per mesh
(`exact`: must be 0). `--compare` prints the distance between two archives; `--roundtrip` refits vanilla → body →
vanilla and compares with the original. `--gui-source <folder> --gui-source-outfit <Angel outfit>
[--gui-source-body eve=<EVE download>]` tests **Outfit made for** in the real window (pre-fill, wrong source,
same body twice, missing source body, body from a download, **Body file...**, closing mid-refit). `--gui` also opens
the window for two seconds. `--gui-install <folder> --gui-install-outfit <outfit>=><expected file>;...` runs the
Install tests in the real window on a fake game folder it makes in `<folder>` (original missing, original already
there, Install unticked, Uninstall, closing the window mid-refit); the real game folder is only read.
`--support <folder> --support-outfit <outfit>` (three times) tests **Support report...** in the real window on two
fake game folders (with and without ArchiveXL etc.), including a restart. `--own-reader <outfit>` checks the
own mesh reader on every mesh of an outfit: same positions as WolvenKit's export, byte-exact round trip, and a
displaced write that WolvenKit reads back as written. Exit code 0 = passed. In the windowed `.exe`, the text goes to
`%TEMP%\OutfitRefitter_console.log`.

### Adding a body

Add one entry to `bodies.json`; it then works as source and as target. Key: the tool's name for the body (also the
output name, `<Key>Refit_`); `name`: shown in the window; `xl`: the ArchiveXL body name (`{body}` →
`_<xl>.mesh`, default = the key); `archives`: file-name patterns of the body's `.archive`; `page`: its mod page;
`hints`: words in an outfit's file name that mean "made for this body" (lower case = any case, with capitals =
exactly). The body archive must replace the vanilla female body meshes listed under `meshes` and keep the vanilla
topology. **Only add bodies whose authors allow their bodies to be used this way.**

### Data folder

`%LOCALAPPDATA%\OutfitRefitter\`: `settings.json` (game folder, body, picked body files, output folder, install tick; "Outfit made for" is not saved, it starts on Vanilla), `installed.json` (the uninstall list: refits and original outfits the tool installed), `wolvenkit\`,
`cages\`, `work\` (temporary), `last_session.txt` and `previous_session.txt` (what happened in the window in this
and the last run, for the support report). Delete the folder to reset the tool.
