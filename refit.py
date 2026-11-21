r"""Refit one clothing mod to another body and pack the result as its own .archive.

Flow: WolvenKit unbundle -> find ArchiveXL "{body}" mesh families in .ent/.app -> per mesh: WolvenKit export glb ->
move the vertices with the vanilla->target body displacement field (only POSITION bytes change) -> WolvenKit import
-> rebuild any mesh whose big buffers WolvenKit wrote badly -> WolvenKit pack -> final buffer + index-CRC check.

- Dynamic meshes ("*path_{body}.mesh" in .ent/.app): <path>_base_body.mesh is refit and written as NEW
  <path>_<body>.mesh. Nothing of the original mod is overridden; ArchiveXL picks it when that body is installed.
- Static meshes (no {body} family): refit in place, same depot path; the output is named "!!!<Body>Refit_*.archive"
  so it loads before the mod's own archive and wins.
The source shape is always the vanilla body. Meshes that can't be read, are not worn on the body (glasses, props),
are male, or already have a version for the target body are
skipped and listed. A mesh WolvenKit can't re-import is dropped; the rest of the outfit still gets refit.
"""

import base64, glob, json, os, re, shutil, struct, sys

import numpy as np
from scipy.spatial import cKDTree

import rdar
from core import ToolError, body_name, run

HELPER = re.compile(
    r"(save_material|material_mesh|_materials?\.mesh$|_shadow\.mesh$|[\\/_]props?[\\/_])",
    re.I,
)
MALE = re.compile(
    r"(^|[\\/_])pma([_\\/.]|$)|_m_base_body|_male[_.]|_m_(adonis|aeiou|atlas|gymfiend|lush|big|vanilla)\b"
    r"|_mbb",
    re.I,
)
BODY_SUFFIX = re.compile(
    r"_(base_body|eve|ebb|ebbp|ebbrb|ebbprb|rb|evb|lush|flat|small|solo\w*|angel|vanilla)$",
    re.I,
)

# ---------------- glb geometry (WolvenKit glb export) ----------------

ACC_N = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def load(p):
    d = bytearray(open(p, "rb").read())
    jl = struct.unpack_from("<I", d, 12)[0]
    return d, json.loads(d[20 : 20 + jl]), 20 + jl + 8


def acc_view(d, j, bin_off, ai):
    a = j["accessors"][ai]
    bv = j["bufferViews"][a["bufferView"]]
    assert a["componentType"] == 5126, "float accessors only"
    n = ACC_N[a["type"]]
    start = bin_off + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    return start, bv.get("byteStride", 4 * n), a["count"], n


def read(d, j, bin_off, ai):
    start, stride, cnt, n = acc_view(d, j, bin_off, ai)
    raw = np.frombuffer(bytes(d), dtype=np.uint8)
    idx = start + np.arange(cnt)[:, None] * stride + np.arange(n * 4)[None, :]
    return (
        raw[idx].reshape(cnt, n * 4).view(np.float32).reshape(cnt, n).astype(np.float64)
    )


def write(d, j, bin_off, ai, arr):
    start, stride, cnt, n = acc_view(d, j, bin_off, ai)
    f = arr.astype(np.float32)
    for i in range(cnt):
        d[start + i * stride : start + i * stride + 4 * n] = f[i].tobytes()
    a = j["accessors"][ai]
    a["min"], a["max"] = f.min(0).tolist(), f.max(0).tolist()


def body_points(paths):
    """LOD 1 positions + normals of a body shape (one glb or several joined with ';')."""
    pts, nrm = [], []
    for p in paths.split(";"):
        d, j, b = load(p)
        for m in j["meshes"]:
            if "LOD_1" not in (m.get("name") or "LOD_1"):
                continue
            for pr in m["primitives"]:
                pts.append(read(d, j, b, pr["attributes"]["POSITION"]))
                if "NORMAL" in pr["attributes"]:
                    nrm.append(read(d, j, b, pr["attributes"]["NORMAL"]))
    return np.vstack(pts), (np.vstack(nrm) if nrm else None)


_FIELD = {}


def field(src, tgt):
    """Displacement field src body -> tgt body (cached): every source vertex moves to the nearest target vertex,
    smoothed twice over 6 neighbours. ponytail: nearest-vertex projection, fine for ~90k-vertex bodies."""
    if (src, tgt) not in _FIELD:
        S, _ = body_points(src)
        T, TN = body_points(tgt)
        TN = TN / np.linalg.norm(TN, axis=1, keepdims=True)
        ttree = cKDTree(T)
        disp = T[ttree.query(S)[1]] - S
        stree = cKDTree(S)
        for _ in range(2):
            disp = disp[stree.query(S, k=6)[1]].mean(1)
        _FIELD[(src, tgt)] = (stree, disp, ttree, T, TN)
    return _FIELD[(src, tgt)]


def refit_glb(gar, out, src, tgt, off=0.004):
    """Move every garment vertex by the inverse-distance-weighted displacement of its 8 nearest source-body vertices,
    then push vertices that ended up inside the target body out to `off` m above the skin (only within 6 cm of it).
    Only POSITION bytes change: indices, UVs, weights, normals and morph targets stay identical."""
    stree, disp, ttree, T, TN = field(src, tgt)
    d, j, b = load(gar)
    inside = total = 0
    for m in j["meshes"]:
        for pr in m["primitives"]:
            ai = pr["attributes"]["POSITION"]
            P = read(d, j, b, ai)
            dist, nb = stree.query(P, k=8)
            w = 1.0 / np.maximum(dist, 1e-4) ** 2
            w /= w.sum(1, keepdims=True)
            Q = P + (disp[nb] * w[..., None]).sum(1)
            _, k = ttree.query(Q)
            depth = ((Q - T[k]) * TN[k]).sum(1)
            bad = (depth < off) & (np.linalg.norm(Q - T[k], axis=1) < 0.06)
            Q[bad] += TN[k[bad]] * (off - depth[bad])[:, None]
            write(d, j, b, ai, Q)
            inside += int(bad.sum())
            total += len(P)
    jb = json.dumps(j, separators=(",", ":")).encode()
    jb += b" " * ((4 - len(jb) % 4) % 4)
    rest = d[20 + struct.unpack_from("<I", d, 12)[0] :]
    body = struct.pack("<II", len(jb), 0x4E4F534A) + jb + rest
    open(out, "wb").write(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return total, inside


def glb_points(glb):
    d, j, b = load(glb)
    return np.vstack(
        [
            read(d, j, b, pr["attributes"]["POSITION"])
            for m in j["meshes"]
            for pr in m["primitives"]
        ]
    )


_TREES = {}


def far_from_body(glb, cages):
    """True for glasses, hats, props: fewer than 20 vertices within 3 cm of the vanilla or target body. They look
    the same on every body, so they are left alone.
    ponytail: no 'which body was this made for' guess. Angel and EVE are too close to vanilla for a distance test to
    tell them apart reliably (tested 2026-11), so the source is always the vanilla body; users add vanilla downloads."""
    pts = glb_points(glb)
    near = np.zeros(len(pts), bool)
    for c in cages:
        if c not in _TREES:
            _TREES[c] = cKDTree(body_points(c)[0])
        near |= _TREES[c].query(pts)[0] < 0.03
    return near.sum() < 20


# ---------------- rebuilding meshes WolvenKit wrote badly ----------------


def spans(h):
    out = []
    for c in h["renderChunkInfos"]:
        cv = c["chunkVertices"]
        el = cv["vertexLayout"]["elements"]["Elements"][0]
        assert (
            el["usage"] == "PS_Position"
            and el["type"] == "PT_Short4N"
            and el["streamIndex"] == 0
        )
        out.append(
            (
                cv["byteOffsets"]["Elements"][0],
                cv["vertexLayout"]["slotStrides"]["Elements"][0],
                c["numVertices"],
            )
        )
    return out


def get_pos(buf, sp, qs, qo):
    raw = np.frombuffer(bytes(buf), np.uint8)
    out = []
    for o, s, n in sp:
        idx = o + np.arange(n)[:, None] * s + np.arange(6)[None, :]
        out.append(raw[idx].reshape(n, 6).view("<i2").astype(np.float64))
    return np.vstack(out) / 32767.0 * qs + qo


def glb_pos(p):
    """Positions per primitive in mesh space (WolvenKit glb = (x, z, -y))."""
    d, j, b = load(p)
    g = [
        read(d, j, b, pr["attributes"]["POSITION"])
        for m in j["meshes"]
        for pr in m["primitives"]
    ]
    return [np.stack([x[:, 0], -x[:, 2], x[:, 1]], 1) for x in g]


def splice(new, orig, override):
    """new CR2W with buffer i := oo2ext(override[i]); undecodable others := orig's buffer i (same memSize)."""
    import zlib

    oend, toff, ents = rdar.cr2w_bufs(new)
    _, _, oents = rdar.cr2w_bufs(orig)
    out = bytearray(new[:oend])
    for i, e in enumerate(ents):
        comp = new[e[2] : e[2] + e[3]]
        if i in override:
            raw = override[i]
            assert e[4] == len(raw), "buffer %d size %d != %d" % (i, e[4], len(raw))
            z = b"KARK" + struct.pack("<I", len(raw)) + rdar.oodle_kraken(raw)
            comp = z if len(z) < len(raw) else raw
        elif not (len(comp) == e[4] or rdar.game_ok(comp, e[4])):
            o = oents[i]
            assert o[4] == e[4], "buffer %d size changed, cannot transplant" % i
            comp = orig[o[2] : o[2] + o[3]]
        e[2], e[3], e[5] = len(out), len(comp), zlib.crc32(comp)
        out += comp
    tb = b"".join(struct.pack("<IIIIII", *e) for e in ents)
    out[toff : toff + len(tb)] = tb
    struct.pack_into("<I", out, 40 + 5 * 12 + 8, zlib.crc32(tb))
    struct.pack_into("<I", out, 28, len(out))
    struct.pack_into("<I", out, 32, 0xDEADBEEF)
    struct.pack_into("<I", out, 32, zlib.crc32(bytes(out[:160])))
    return bytes(out)


def strip(o):  # JSON compare: no HandleIds, floats at float32 precision
    if isinstance(o, float):
        return float(np.float32(o))
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k != "HandleId"}
    return [strip(v) for v in o] if isinstance(o, list) else o


def from_glb(sm, g, tg, tmp, wk, game):
    """Refit CR2W bytes for source mesh file sm: the positions of refit glb tg (g = WolvenKit export of sm) written
    straight into sm's render buffer and compressed with the game's own Oodle. Verified by a WolvenKit round trip."""
    for x in ("json", "new", "chk"):
        os.makedirs(os.path.join(tmp, x), exist_ok=True)
    jd = os.path.join(tmp, "json")
    run([wk, "convert", "serialize", sm, "-o", jd], game)
    jp = os.path.join(jd, os.path.basename(sm) + ".json")
    js = json.load(open(jp, encoding="utf-8-sig"))
    rc = js["Data"]["RootChunk"]
    rb = rc["renderResourceBlob"]["Data"]
    h = rb["header"]
    buf0 = base64.b64decode(rb["renderBuffer"]["Bytes"])
    buf = bytearray(buf0)
    sp = spans(h)
    qs = np.array([h["quantizationScale"][k] for k in "XYZ"])
    qo = np.array([h["quantizationOffset"][k] for k in "XYZ"])
    P0, G0, G1 = get_pos(buf, sp, qs, qo), glb_pos(g), glb_pos(tg)
    assert [len(x) for x in G0] == [n for _, _, n in sp], (
        "glb primitives != render chunks"
    )
    assert np.abs(np.vstack(G0) - P0).max() <= qs.max() / 32767 * 2 + 1e-5, (
        "glb vertex order != buffer"
    )
    Q = np.vstack(G1)
    lo, hi = Q.min(0), Q.max(0)
    if (np.abs((Q - qo) / qs) > 1.0).any():  # bounds grew: new quantization
        qo = ((lo + hi) / 2).astype(np.float32).astype(np.float64)
        qs = (
            (np.maximum((hi - lo) / 2, 1e-6) * 1.001)
            .astype(np.float32)
            .astype(np.float64)
        )
        for i, k in enumerate("XYZ"):
            h["quantizationOffset"][k], h["quantizationScale"][k] = (
                float(qo[i]),
                float(qs[i]),
            )
    qi = np.clip(np.round((Q - qo) / qs * 32767.0), -32767, 32767).astype("<i2")
    i = 0
    for o, s, n in sp:
        for v in range(n):
            struct.pack_into("<3h", buf, o + v * s, *qi[i])
            i += 1
    rb["renderBuffer"]["Bytes"] = base64.b64encode(bytes(buf)).decode()
    bb = rc["boundingBox"]
    for ix, k in enumerate("XYZ"):
        bb["Min"][k] = float(min(bb["Min"][k], lo[ix]))
        bb["Max"][k] = float(max(bb["Max"][k], hi[ix]))
    json.dump(js, open(jp, "w", encoding="utf-8"), indent=1)
    nd = os.path.join(tmp, "new")
    run([wk, "convert", "deserialize", jp, "-o", nd], game)
    orig = open(sm, "rb").read()
    _, _, oents = rdar.cr2w_bufs(orig)
    ridx = [
        k
        for k, e in enumerate(oents)
        if e[4] == len(buf0) and rdar.seg_raw(orig[e[2] : e[2] + e[3]], e[4]) == buf0
    ]
    assert len(ridx) == 1, "render buffer not found in source"
    fixed = splice(
        open(os.path.join(nd, os.path.basename(sm)), "rb").read(),
        orig,
        {ridx[0]: bytes(buf)},
    )
    chk = os.path.join(tmp, "chk")
    open(os.path.join(chk, os.path.basename(sm)), "wb").write(fixed)
    run(
        [
            wk,
            "convert",
            "serialize",
            os.path.join(chk, os.path.basename(sm)),
            "-o",
            chk,
        ],
        game,
    )
    back = json.load(
        open(os.path.join(chk, os.path.basename(sm) + ".json"), encoding="utf-8-sig")
    )
    assert strip(back["Data"]) == strip(js["Data"]), "round trip differs"
    return fixed


def repair_buffers(done, meshes, pack, glbs, work, wk, game, log, skipped):
    """WolvenKit 9.1.1 import can write big buffers (> ~1.4 MB) the game can't decode. Rebuild only those meshes;
    a mesh that can't be rebuilt is dropped (listed as skipped)."""
    import tempfile

    for s, t in list(done):
        p = os.path.join(pack, t)
        if not rdar.bad_buffers(*rdar.loose_mesh(p), meshes[s]):
            continue
        log(
            "  WolvenKit wrote a broken big buffer in %s; rebuilding it directly ..."
            % os.path.basename(t)
        )
        tmp = tempfile.mkdtemp(prefix="fix_", dir=work)
        try:
            open(p, "wb").write(from_glb(meshes[s], *glbs[t], tmp, wk, game))
            assert not rdar.bad_buffers(*rdar.loose_mesh(p), meshes[s])
        except Exception as e:
            os.remove(p)
            done.remove((s, t))
            skipped.append((s, "could not be rebuilt safely (%s)" % str(e)[:200]))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def buffer_gate(arch, src_of):
    """Final check of the packed archive -> list of problems (empty = safe to use)."""
    r = rdar.Rdar(arch)
    bad = [] if r.crc_ok else ["archive index checksum wrong"]
    for name, head, segs in rdar.archive_meshes(arch):
        sm = src_of.get(name.lower())
        if head is None:
            bad.append(name + ": header can't be decoded")
        elif sm and rdar.bad_buffers(head, segs, sm):
            bad.append(
                "%s: buffers %s broken" % (name, rdar.bad_buffers(head, segs, sm))
            )
        elif not sm and not all(ok for _, _, ok, _, _ in rdar.stats(head, segs)):
            bad.append(name + ": buffer can't be decoded")
    return bad


# ---------------- archives packed without a file-name list ----------------

DEPOT_PATH = re.compile(rb"[A-Za-z0-9_\-\\/.{}]{4,}\.[a-z0-9_]{2,12}")


def recover_names(src, arcs):
    r"""Old archives (game 1.6 era) have no file-name list, so WolvenKit unbundles their own files as <hash>.bin
    (vanilla paths it knows are already named). Every depot path mentioned inside the extracted files, the .xl next
    to the archive and the r6\tweaks .yaml is hashed and matched; matched files move to their real path.
    One pass is enough: strings are read from every file, named or not. -> (named, still unnamed)."""
    bins = {}
    for p in glob.glob(os.path.join(src, "*.bin")):
        stem = os.path.splitext(os.path.basename(p))[0]
        if stem.isdigit():
            bins[int(stem)] = p
    texts = []
    for a in arcs:
        d = os.path.dirname(a)
        texts += glob.glob(os.path.join(d, "*.xl"))
        if [x.lower() for x in os.path.normpath(d).split(os.sep)[-3:]] == [
            "archive",
            "pc",
            "mod",
        ]:
            tw = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(d))), "r6", "tweaks"
            )
            texts += glob.glob(os.path.join(tw, "**", "*.y*ml"), recursive=True)
    texts += glob.glob(os.path.join(src, "**", "*"), recursive=True)
    names = {}
    for t in texts:
        if not bins or not os.path.isfile(t):
            continue
        for m in DEPOT_PATH.finditer(open(t, "rb").read()):
            n = m.group().decode("latin-1").lstrip("*").replace("/", "\\").lower()
            h = rdar.fnv(n)
            if h in bins and h not in names:
                names[h] = n
    found = 0
    for h, n in names.items():
        dst = os.path.normpath(os.path.join(src, n))
        if (  # names come from the download: never let one point outside the folder
            os.path.commonpath([dst, os.path.normpath(src)]) != os.path.normpath(src)
            or ":" in n
            or os.path.exists(dst)
        ):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(bins[h], dst)
        found += 1
    return found, len(bins) - found


# ---------------- one outfit ----------------


def refit_mod(
    arcs, name, work, out, tgt, cages, wk, game, log, progress=lambda f: None
):
    """arcs: the outfit's .archive files. -> path of the written refit archive, or None if nothing needed a refit.
    Raises ToolError when nothing could be refit or the final safety check fails."""
    src, glbd, raw, pack = (
        os.path.join(work, x) for x in ("src", "glb", "raw", "pack")
    )
    for d in (src, glbd, raw, pack, out):
        os.makedirs(d, exist_ok=True)
    nameless = [os.path.basename(a) for a in arcs if not rdar.Rdar(a).names]
    for a in arcs:
        run([wk, "unbundle", a, "-o", src], game)
    if nameless:
        found, left = recover_names(src, arcs)
        log(
            "%s has no file names inside (an older way of packing); found %d of them from the other files%s."
            % (
                ", ".join(nameless),
                found,
                ", %d stay unnamed (not needed for the refit)" % left if left else "",
            )
        )
    meshes = {
        os.path.relpath(p, src).replace("/", "\\").lower(): p
        for p in glob.glob(os.path.join(src, "**", "*.mesh"), recursive=True)
    }
    if not meshes:
        if nameless:
            raise ToolError(
                "Couldn't find the clothing inside %s (it's packed without file names and no other file "
                "points to the clothing). This outfit can't be refit."
                % ", ".join(nameless)
            )
        log("No clothing meshes in this mod.")
        return None
    # ArchiveXL dynamic families: paths are hashed in the binary files, WolvenKit resolves them when serializing
    dyn = set()
    js = os.path.join(work, "json")
    os.makedirs(js, exist_ok=True)
    for f in glob.glob(os.path.join(src, "**", "*.ent"), recursive=True) + glob.glob(
        os.path.join(src, "**", "*.app"), recursive=True
    ):
        try:
            run([wk, "convert", "serialize", f, "-o", js], game)
        except RuntimeError:
            log(
                "  (could not read %s, an old file format; its meshes are treated as fixed)"
                % os.path.basename(f)
            )
    for f in glob.glob(os.path.join(js, "*.json")):
        for m in re.finditer(
            r"\*?[A-Za-z0-9_\\/.\-{}]+\{body\}[A-Za-z0-9_\\/.\-{}]*\.mesh",
            open(f, encoding="utf-8", errors="ignore").read(),
        ):
            dyn.add(
                m.group().replace("\\\\", "\\").lstrip("*").replace("/", "\\").lower()
            )
    jobs, covered, skipped = [], set(), []  # jobs: (source rel path, target rel path)
    for pat in sorted(dyn):
        pat = pat.replace("{gender}", "w")  # ArchiveXL: female V -> "w"
        if re.search(r"\{(?!body\})", pat):
            skipped.append((pat, "uses other ArchiveXL tokens"))
            continue
        s, t = pat.replace("{body}", "base_body"), pat.replace("{body}", tgt)
        fam = re.escape(pat).replace(r"\{body\}", r"[a-z0-9_]+")
        covered |= {k for k in meshes if re.fullmatch(fam, k)}
        if t in meshes:
            skipped.append((t, "the mod already has a %s version" % body_name(tgt)))
        elif s in meshes:
            jobs.append((s, t))
        else:
            skipped.append((pat, "no base_body mesh to start from"))
    for k in meshes:
        stem = os.path.splitext(os.path.basename(k))[0]
        if k in covered or HELPER.search(k):
            continue
        if MALE.search(k):
            skipped.append((k, "male mesh"))
            continue
        if stem.endswith(
            "_base_body"
        ):  # family whose {body} path is only stored hashed: add the sibling
            t = k[: -len("base_body") - 5] + tgt + ".mesh"
            if t not in meshes:
                jobs.append((k, t))
            continue
        if BODY_SUFFIX.search(stem):
            continue
        jobs.append((k, k))  # static: override in place
    if not jobs:
        for s, why in skipped:
            log("  skipped %s: %s" % (os.path.basename(s), why))
        log("Nothing to refit in this mod.")
        return None
    log("Refitting %d mesh(es) to %s ..." % (len(jobs), body_name(tgt)))
    done, glbs = [], {}
    for n, (s, t) in enumerate(jobs):
        progress(0.8 * n / len(jobs))
        try:
            run([wk, "export", meshes[s], "-o", glbd, "-gp", game], game)
        except RuntimeError:
            skipped.append((s, "WolvenKit can't read this mesh"))
            continue
        g = os.path.join(glbd, os.path.splitext(os.path.basename(s))[0] + ".glb")
        stem = os.path.splitext(os.path.basename(s))[0]
        if not BODY_SUFFIX.search(stem) and far_from_body(
            g, (cages["van"], cages[tgt])
        ):
            skipped.append((s, "not worn on the body (same on every body)"))
            continue
        tdir = os.path.dirname(t)
        os.makedirs(os.path.join(raw, tdir), exist_ok=True)
        os.makedirs(os.path.join(pack, tdir), exist_ok=True)
        tglb = os.path.join(raw, os.path.splitext(t)[0] + ".glb")
        g2 = os.path.join(
            work, "srcglb", t[:-5] + ".glb"
        )  # own copy: glbd is keyed by basename
        os.makedirs(os.path.dirname(g2), exist_ok=True)
        shutil.copy(g, g2)
        glbs[t] = (g2, tglb)
        nv, pushed = refit_glb(g, tglb, cages["van"], cages[tgt])
        log(
            "  %s: %d vertices moved, %d pushed out of the skin"
            % (os.path.basename(t), nv, pushed)
        )
        shutil.copy(meshes[s], os.path.join(pack, t))
        done.append((s, t))
    if done:
        imp = run([wk, "import", raw, "-o", pack, "-k"], game, ok="Imported")
        m = re.search(r"Imported (\d+)/(\d+)", imp)
        if not m:
            raise ToolError(
                "WolvenKit could not import the refit meshes:\n" + imp[-500:]
            )
        for s, t in list(
            done
        ):  # a mesh WolvenKit could not re-import is still the unrefit copy -> drop it
            if open(os.path.join(pack, t), "rb").read() == open(meshes[s], "rb").read():
                os.remove(os.path.join(pack, t))
                done.remove((s, t))
                skipped.append((s, "WolvenKit can't write this mesh back"))
        progress(0.85)
        repair_buffers(done, meshes, pack, glbs, work, wk, game, log, skipped)
    for s, why in skipped:
        log("  skipped %s: %s" % (os.path.basename(s), why))
    if not done:
        if any("WolvenKit" in w or "rebuilt" in w for _, w in skipped):
            raise ToolError("none of the meshes could be refit (see the list above).")
        log("Nothing needed a refit.")
        return None
    static = any(s == t for s, t in done)
    tmp = os.path.join(work, "packout")
    os.makedirs(tmp, exist_ok=True)
    run([wk, "pack", pack, "-o", tmp], game, ok="Finished packing")
    arch = glob.glob(os.path.join(tmp, "*.archive"))[0]
    progress(0.95)
    bad = buffer_gate(arch, {t: meshes[s] for s, t in done})
    if bad:  # never hand out an archive with broken buffers
        raise ToolError(
            "the safety check found broken data, so no file was written: "
            + "; ".join(bad)
        )
    final = os.path.join(
        out, ("!!!" if static else "") + tgt.capitalize() + "Refit_" + name + ".archive"
    )
    shutil.move(arch, final)
    log(
        "Done: %s (%d mesh(es) refit%s)"
        % (
            os.path.basename(final),
            len(done),
            ", replaces the original meshes" if static else "",
        )
    )
    return final
