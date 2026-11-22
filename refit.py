r"""Refit one clothing mod to another body and pack the result as its own .archive.

Flow: WolvenKit unbundle -> find ArchiveXL "{body}" mesh families in .ent/.app -> per mesh: WolvenKit export glb ->
move the vertices with the source->target body displacement field (only POSITION bytes change) -> WolvenKit import
-> rebuild any mesh whose big buffers WolvenKit wrote badly -> WolvenKit pack -> final buffer + index-CRC check.
A mesh WolvenKit can't read at all (old file format) goes through OwnMesh instead: positions decoded from and
written back into its own render buffer, no WolvenKit import.

Source and target are any two bodies of bodies.json (vanilla, Angel, EVE); the field goes straight from one to the
other (no step over vanilla, which would add the error and the push-out twice).
Layered outfits (top < shirt < jacket, from the outfit's ArchiveXL outfit slots): garments are refit inner first and
each outer layer is kept 1 mm above the refit inner ones (layer_push).
- Dynamic meshes ("*path_{body}.mesh" in .ent/.app): <path>_<source xl>.mesh is refit and written as NEW
  <path>_<target xl>.mesh (xl name: vanilla = base_body, Angel = angel). Nothing of the original mod is overridden;
  ArchiveXL picks it when that body is installed (base_body = no body mod).
- Static meshes (no {body} family): refit in place, same depot path; the output is named "!!!<Body>Refit_*.archive"
  so it loads before the mod's own archive and wins.
Meshes that can't be read, are not worn on the body (glasses, props), are male, or already have a version for the
target body are skipped and listed. A mesh WolvenKit can't re-import is dropped; the rest of the outfit still gets
refit.
"""

import base64, glob, json, os, re, shutil, struct

import numpy as np
from scipy.spatial import cKDTree

import rdar
from core import ToolError, body_name, run, xl_name

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


_SHAPE = {}


def shape(paths):
    """(kd-tree, positions, unit normals) of a body shape (glb paths joined with ';'), cached."""
    if paths not in _SHAPE:
        T, TN = body_points(paths)
        _SHAPE[paths] = (cKDTree(T), T, TN / np.linalg.norm(TN, axis=1, keepdims=True))
    return _SHAPE[paths]


_FIELD = {}


def field(src, tgt):
    """Displacement field src body -> tgt body (cached): every source vertex moves to the nearest target vertex,
    smoothed twice over 6 neighbours. ponytail: nearest-vertex projection, fine for ~90k-vertex bodies."""
    if (src, tgt) not in _FIELD:
        S, _ = body_points(src)
        ttree, T, TN = shape(tgt)
        disp = T[ttree.query(S)[1]] - S
        stree = cKDTree(S)
        for _ in range(2):
            disp = disp[stree.query(S, k=6)[1]].mean(1)
        _FIELD[(src, tgt)] = (stree, disp, ttree, T, TN)
    return _FIELD[(src, tgt)]


def push_out(Q, shp, off):
    """Push vertices of Q (in place) that are inside body shape shp = (tree, T, TN), or closer than `off` m to its
    skin, out to `off` m above it (only within 6 cm of it). -> mask of the pushed vertices."""
    tree, T, TN = shp
    _, k = tree.query(Q)
    depth = ((Q - T[k]) * TN[k]).sum(1)
    bad = (depth < off) & (np.linalg.norm(Q - T[k], axis=1) < 0.06)
    Q[bad] += TN[k[bad]] * (off - depth[bad])[:, None]
    return bad


def move(P, src, tgt, off=0.004):
    """Move every garment vertex (glb space) by the inverse-distance-weighted displacement of its 8 nearest
    source-body vertices, then push vertices that ended up inside the target body out to `off` m above the skin
    (only within 6 cm of it). -> (new positions, number pushed out)."""
    stree, disp, ttree, T, TN = field(src, tgt)
    dist, nb = stree.query(P, k=8)
    w = 1.0 / np.maximum(dist, 1e-4) ** 2
    w /= w.sum(1, keepdims=True)
    Q = P + (disp[nb] * w[..., None]).sum(1)
    bad = push_out(Q, (ttree, T, TN), off)
    # the nearest body vertex's normal misses curved spots (EVE's crotch: up to 2.6 mm inside after the push above),
    # so vertices close to the skin are checked again against the exact surface and lifted to `off` above it
    for _ in range(2):
        _, k = ttree.query(Q)
        near = np.where(((Q - T[k]) * TN[k]).sum(1) < 0.01)[0]
        if not len(near):
            break
        h, q, n = skin_height(Q[near], tgt)
        # ponytail: only shallow misses (> -1.5 cm); deeper ones are the outfit's own hidden parts, left alone
        low = (h < off * 0.5) & (h > -0.015)
        if not low.any():
            break
        Q[near[low]] = q[low] + n[low] * off
        bad[near[low]] = True
    return Q, int(bad.sum())


def layer_push(Q, inner, tgt, gap=0.001, reach=0.006):
    """Push garment vertices Q (in place) of an outer layer out of the inner layers' vertices `inner` (both already
    refit, glb space): height = distance to the target body's surface (skin_height); where at least 3 inner vertices
    sit over the same skin spot (closest skin points within `reach` m), an outer vertex lower than the highest of them
    + gap is lifted to that height, straight away from the skin. Only within 6 cm of the skin. -> vertices lifted.
    ponytail: max height over a 6 mm spot, up to 4 passes; measured on Maniekk Pt5 (0 crossings, fit ~unchanged). The
    exact surface matters: heights along the nearest body vertex's normal left 594 jacket vertices under the shirt."""
    if inner is None or not len(inner):
        return 0
    hi, fi, _ = skin_height(inner, tgt)
    keep = np.abs(hi) < 0.06
    if not keep.any():
        return 0
    hi, ftree = hi[keep], cKDTree(fi[keep])
    lifted = np.zeros(len(Q), bool)
    for _ in range(
        4
    ):  # a lifted vertex can land over another skin spot: again until nothing moves
        ho, fo, n = skin_height(Q, tgt)
        need = np.zeros(len(Q))
        for i, L in enumerate(ftree.query_ball_point(fo, reach)):
            if len(L) >= 3 and abs(ho[i]) < 0.06:
                need[i] = hi[L].max() + gap - ho[i]
        m = need > 1e-6
        if not m.any():
            break
        Q[m] += n[m] * need[m][:, None]
        lifted |= m
    return int(lifted.sum())


def closest_on_triangles(X, A, B, C):
    """Barycentric (u, v, w) of the point of each triangle (A, B, C) closest to X (Ericson, Real-Time Collision
    Detection 5.1.5; vectorised over any leading shape)."""
    ab, ac, ap = B - A, C - A, X - A
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp, cp = X - B, X - C
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-30, 1e-30, den)
    v, w = vb / den, vc / den
    U = np.stack([1 - v - w, v, w], -1)
    z, one = np.zeros_like(d1), np.ones_like(d1)
    safe = lambda x: np.where(x == 0, 1e-30, x)
    for (
        m,
        val,
    ) in (  # edges, then corners (later ones win, as in the book's early returns)
        ((vc <= 0) & (d1 >= 0) & (d3 <= 0), (d1 / safe(d1 - d3), 0)),
        ((vb <= 0) & (d2 >= 0) & (d6 <= 0), (d2 / safe(d2 - d6), 1)),
        (
            (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0),
            ((d4 - d3) / safe(d4 - d3 + d5 - d6), 2),
        ),
    ):
        t, e = val
        cand = [
            np.stack([1 - t, t, z], -1),
            np.stack([1 - t, z, t], -1),
            np.stack([z, 1 - t, t], -1),
        ][e]
        U[m] = cand[m]
    for m, c in (
        ((d1 <= 0) & (d2 <= 0), 0),
        ((d3 >= 0) & (d4 <= d3), 1),
        ((d6 >= 0) & (d5 <= d6), 2),
    ):
        U[m] = np.stack(
            [one, z, z] if c == 0 else [z, one, z] if c == 1 else [z, z, one], -1
        )[m]
    return U


_SURF = {}


def surface(paths):
    """(positions, unit normals, triangles, kd-tree of triangle centres) of a body's LOD 1 surface, cached."""
    if paths not in _SURF:
        Ps, Ns, Fs, o = [], [], [], 0
        for p in paths.split(";"):
            d, j, b = load(p)
            for m in j["meshes"]:
                if "LOD_1" not in (m.get("name") or "LOD_1"):
                    continue
                for pr in m["primitives"]:
                    P = read(d, j, b, pr["attributes"]["POSITION"])
                    Ps.append(P)
                    Ns.append(read(d, j, b, pr["attributes"]["NORMAL"]))
                    Fs.append(read_index(d, j, b, pr["indices"]).reshape(-1, 3) + o)
                    o += len(P)
        P, N, F = np.vstack(Ps), np.vstack(Ns), np.vstack(Fs)
        N /= np.linalg.norm(N, axis=1, keepdims=True)
        _SURF[paths] = (P, N, F, cKDTree(P[F].mean(1)))
    return _SURF[paths]


def read_index(d, j, b, ai):
    a = j["accessors"][ai]
    bv = j["bufferViews"][a["bufferView"]]
    dt = {5121: "<u1", 5123: "<u2", 5125: "<u4"}[a["componentType"]]
    start = b + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.frombuffer(bytes(d), dt, a["count"], start).astype(np.int64)


def skin_height(X, tgt, k=24, faces=False):
    """Signed distance of points X to body tgt's surface (closest point on its triangles, among the k nearest by
    centre; sign from the interpolated normal) -> (height, closest skin point, unit direction away from the skin);
    faces=True: also (the closest triangle's vertex indices into surface(tgt), barycentric weights of the point)."""
    P, N, F, tree = surface(tgt)
    h, q, n = np.zeros(len(X)), np.zeros_like(X), np.zeros_like(X)
    fv, bu = np.zeros((len(X), 3), np.int64), np.zeros((len(X), 3))
    for s in range(
        0, len(X), 20000
    ):  # in blocks: the k candidate triangles per point take memory
        x = X[s : s + 20000]
        ti = tree.query(x, k=k)[1]
        f = F[ti]
        U = closest_on_triangles(x[:, None], P[f[..., 0]], P[f[..., 1]], P[f[..., 2]])
        c = (U[..., None] * P[f]).sum(-2)
        dd = np.linalg.norm(x[:, None] - c, axis=-1)
        best = dd.argmin(1)
        r = np.arange(len(x))
        cq, u, fb = c[r, best], U[r, best], f[r, best]
        nn = (u[:, :, None] * N[fb]).sum(1)
        sg = np.sign(((x - cq) * nn).sum(1))
        sg[sg == 0] = 1
        d = x - cq
        ln = np.linalg.norm(d, axis=1)
        dirn = np.where(
            (ln > 1e-7)[:, None], d / np.maximum(ln, 1e-12)[:, None] * sg[:, None], nn
        )
        h[s : s + 20000], q[s : s + 20000] = dd[r, best] * sg, cq
        n[s : s + 20000] = dirn / np.linalg.norm(dirn, axis=1, keepdims=True)
        fv[s : s + 20000], bu[s : s + 20000] = fb, u
    return (h, q, n, fv, bu) if faces else (h, q, n)


def crossings(inner, outer, tgt, reach=0.006):
    """Outer-layer vertices lower than the inner layer over the same skin spot (at least 3 inner vertices with their
    foot within `reach` m, below their mean height), within 6 cm of the skin. -> (compared, below)."""
    hi, fi, _ = skin_height(inner, tgt)
    keep = np.abs(hi) < 0.06
    hi, ftree = hi[keep], cKDTree(fi[keep])
    ho, fo, _ = skin_height(outer, tgt)
    cmp = below = 0
    for i, L in enumerate(ftree.query_ball_point(fo, reach)):
        if len(L) >= 3 and abs(ho[i]) < 0.06:
            cmp += 1
            below += bool(ho[i] < hi[L].mean())
    return cmp, below


def _acc_layout(d, j, b, ai):
    a = j["accessors"][ai]
    bv = j["bufferViews"][a["bufferView"]]
    dt = np.dtype(
        {5121: "<u1", 5123: "<u2", 5125: "<u4", 5126: "<f4"}[a["componentType"]]
    )
    n = ACC_N[a["type"]]
    start = b + bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride", dt.itemsize * n)
    idx = (
        start
        + np.arange(a["count"])[:, None] * stride
        + np.arange(dt.itemsize * n)[None, :]
    )
    return a, dt, n, idx


def read_vals(d, j, b, ai):
    """Any accessor as float64 (normalized integers as 0-1, joint indices as they are)."""
    a, dt, n, idx = _acc_layout(d, j, b, ai)
    raw = np.frombuffer(bytes(d), np.uint8)
    X = (
        raw[idx]
        .reshape(a["count"], -1)
        .view(dt)
        .reshape(a["count"], n)
        .astype(np.float64)
    )
    return X / np.iinfo(dt).max if a.get("normalized") and dt.kind == "u" else X


def write_vals(d, j, b, ai, X):
    """Write X into accessor ai of glb bytes d (in place), in the accessor's own number type."""
    a, dt, n, idx = _acc_layout(d, j, b, ai)
    if a.get("normalized") and dt.kind == "u":
        X = np.round(np.clip(X, 0, 1) * np.iinfo(dt).max)
    raw = np.frombuffer(d, np.uint8)  # d is a bytearray: writes go straight into it
    raw[idx] = np.ascontiguousarray(X.astype(dt)).view(np.uint8).reshape(idx.shape)


def prim_skin(d, j, b, pr):
    """(joint indices, weights) of a primitive, all JOINTS_n / WEIGHTS_n sets side by side, or None."""
    at = pr["attributes"]
    sets = [i for i in range(4) if "JOINTS_%d" % i in at and "WEIGHTS_%d" % i in at]
    if not sets:
        return None
    J = np.hstack([read_vals(d, j, b, at["JOINTS_%d" % i]) for i in sets]).astype(
        np.int64
    )
    W = np.hstack([read_vals(d, j, b, at["WEIGHTS_%d" % i]) for i in sets])
    return J, W, sets


_BSKIN = {}


def body_skin(paths):
    """Skin weights of a body (glb paths joined with ';'), in surface()'s vertex order: -> (joint names, J (V, K)
    indices into the names, W (V, K), {joint name: parent joint name}). Cached."""
    if paths not in _BSKIN:
        names, idx, Js, Ws, parent = [], {}, [], [], {}
        for p in paths.split(";"):
            d, j, b = load(p)
            local = []
            for ni in j["skins"][0]["joints"] if j.get("skins") else []:
                nm = j["nodes"][ni].get("name")
                idx.setdefault(nm, len(names))
                if idx[nm] == len(names):
                    names.append(nm)
                local.append(idx[nm])
            for nd in j["nodes"]:
                for c in nd.get("children", []):
                    parent.setdefault(j["nodes"][c].get("name"), nd.get("name"))
            local = np.array(local or [0])
            for m in j["meshes"]:
                if "LOD_1" not in (m.get("name") or "LOD_1"):
                    continue
                for pr in m["primitives"]:
                    sk = prim_skin(d, j, b, pr)
                    n = j["accessors"][pr["attributes"]["POSITION"]]["count"]
                    if sk is None:  # keep the vertex order of surface()
                        Js.append(np.zeros((n, 1), np.int64))
                        Ws.append(np.zeros((n, 1)))
                    else:
                        Js.append(local[sk[0]])
                        Ws.append(sk[1])
        K = max(x.shape[1] for x in Js)
        pad = lambda X: np.hstack([X, np.zeros((len(X), K - X.shape[1]), X.dtype)])
        _BSKIN[paths] = (
            names,
            np.vstack([pad(x) for x in Js]),
            np.vstack([pad(x) for x in Ws]),
            parent,
        )
    return _BSKIN[paths]


def rigid(J, W):
    """True for a rigid part (prop, buckle, scabbard, bag): every vertex is led by one of at most 2 bones (cloth on
    the body is led by many: a top 12, trousers 28). ponytail: a naive heuristic; a part led by 3+ bones counts as
    cloth."""
    top = J[np.arange(len(J)), W.argmax(1)]
    return len(np.unique(top)) <= 2


def copy_weights(d, j, b, pr, Q, tgt, near=0.015, far=0.04):
    """Give the garment vertices Q (refit positions of primitive pr, glb space) the skin weights of body tgt where they
    sit close to it, so they move with the body (also with rig / breast physics mods that move the body's bones):
    the weights at the closest point of the body's skin, fully within `near` m, fading to the garment's own weights
    at `far` m; vertices further out (a hanging skirt, cloth bones) keep theirs. A body bone the garment's skin lacks
    falls back to its nearest parent that the garment has. Writes the JOINTS_n / WEIGHTS_n accessors of glb bytes d
    in place. -> vertices changed.
    ponytail: the K strongest influences are kept (K = the garment's own count, usually 8)."""
    sk = prim_skin(d, j, b, pr)
    if sk is None or not j.get("skins"):
        return 0
    GJ, GW, sets = sk
    if rigid(GJ, GW):  # a knife, a buckle, a bag: it must not bend like skin
        return 0
    gnames = [j["nodes"][ni].get("name") for ni in j["skins"][0]["joints"]]
    gidx = {nm: i for i, nm in enumerate(gnames)}
    bnames, BJ, BW, parent = body_skin(tgt)
    gmap = np.full(len(bnames), -1)
    for i, nm in enumerate(bnames):
        x, hops = nm, 0
        while x is not None and x not in gidx and hops < 64:
            x, hops = parent.get(x), hops + 1
        if x in gidx:
            gmap[i] = gidx[x]
    h, _, _, fv, bu = skin_height(Q, tgt, faces=True)
    alpha = np.clip((far - np.abs(h)) / (far - near), 0, 1)
    sel = np.nonzero(alpha > 0)[0]
    if not len(sel):
        return 0
    n, K, G = len(sel), GJ.shape[1], len(gnames) + 1
    cj = gmap[BJ[fv[sel]]].reshape(
        n, -1
    )  # 3 corners x their influences, as garment joints
    cw = (BW[fv[sel]] * bu[sel][:, :, None]).reshape(n, -1)
    cw[cj < 0], cj[cj < 0] = 0, 0
    a = alpha[sel][:, None]
    ids = np.hstack([cj, GJ[sel]])
    ws = np.hstack([cw * a, GW[sel] * (1 - a)])
    # sum per (vertex, joint), keep the K biggest per vertex, normalize
    key = np.repeat(np.arange(n), ids.shape[1]) * G + ids.ravel()
    uk, inv = np.unique(key, return_inverse=True)
    sw = np.bincount(inv, ws.ravel())
    ur, uj = uk // G, uk % G
    o = np.lexsort((-sw, ur))
    ur, uj, sw = ur[o], uj[o], sw[o]
    rank = np.arange(len(ur)) - np.searchsorted(ur, np.arange(n))[ur]
    k = (rank < K) & (sw > 1e-6)
    NJ, NW = np.zeros((n, K), np.int64), np.zeros((n, K))
    NJ[ur[k], rank[k]], NW[ur[k], rank[k]] = uj[k], sw[k]
    tot = NW.sum(1)
    ok = (
        tot > 1e-6
    )  # nothing left (no body bone known to the garment): keep the garment's own weights
    NW[ok] /= tot[ok][:, None]
    GJ[sel[ok]], GW[sel[ok]] = NJ[ok], NW[ok]
    at = pr["attributes"]
    for c, i in enumerate(sets):
        write_vals(d, j, b, at["JOINTS_%d" % i], GJ[:, 4 * c : 4 * c + 4])
        write_vals(d, j, b, at["WEIGHTS_%d" % i], GW[:, 4 * c : 4 * c + 4])
    return int(ok.sum())


def refit_parts(parts, src, tgt, inner=None):
    """move() + layer_push() for the parts of one mesh: parts = [(LOD key, positions)], inner = {LOD key: inner layer
    positions} (a LOD the inner layers lack uses their LOD 1). -> ([new positions], pushed out of the skin, lifted
    over the inner layers)."""
    out, pushed, lifted = [], 0, 0
    for lod, P in parts:
        Q, n = move(P, src, tgt)
        if inner:
            lifted += layer_push(Q, inner.get(lod, inner.get(1)), tgt)
        out.append(Q)
        pushed += n
    return out, pushed, lifted


def lod_of(name):
    """LOD number of a WolvenKit glb mesh name ('submesh_00_LOD_1' -> 1)."""
    m = re.search(r"LOD_(\d+)", name or "")
    return int(m.group(1)) if m else 1


def refit_glb(gar, out, src, tgt, inner=None, weights=False):
    """refit_parts() applied to a WolvenKit glb export. Only POSITION bytes change (and with weights=True the skin
    weights near the body, copy_weights): indices, UVs, normals and morph targets stay identical.
    -> (vertices, pushed out of the skin, lifted over inner layers, [(LOD, new positions)], vertices that got the
    body's weights)."""
    d, j, b = load(gar)
    prims = [(lod_of(m.get("name")), pr) for m in j["meshes"] for pr in m["primitives"]]
    acc = [(lod, pr["attributes"]["POSITION"]) for lod, pr in prims]
    parts = [(lod, read(d, j, b, ai)) for lod, ai in acc]
    Qs, inside, lifted = refit_parts(parts, src, tgt, inner)
    copied = 0
    for (_, ai), (_, pr), Q in zip(acc, prims, Qs):
        write(d, j, b, ai, Q)
        if weights:
            copied += copy_weights(d, j, b, pr, Q, tgt)
    jb = json.dumps(j, separators=(",", ":")).encode()
    jb += b" " * ((4 - len(jb) % 4) % 4)
    rest = d[20 + struct.unpack_from("<I", d, 12)[0] :]
    body = struct.pack("<II", len(jb), 0x4E4F534A) + jb + rest
    open(out, "wb").write(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return (
        sum(len(P) for _, P in parts),
        inside,
        lifted,
        [(lod, Q) for (lod, _), Q in zip(parts, Qs)],
        copied,
    )


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


def far_from_body(pts, cages):
    """True for glasses, hats, props: fewer than 20 vertices within 3 cm of the source or target body. They look
    the same on every body, so they are left alone.
    ponytail: no 'which body was this made for' guess from the geometry. Angel and EVE are too close to vanilla for a
    distance test to tell them apart reliably (tested 2026-11); the user picks the source body (pre-filled from the
    file name)."""
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


def to_glb(P):  # mesh space -> WolvenKit glb space (x, z, -y)
    return np.stack([P[:, 0], P[:, 2], -P[:, 1]], 1)


def from_glb_space(G):
    return np.stack([G[:, 0], -G[:, 2], G[:, 1]], 1)


def glb_pos(p):
    """Positions per primitive in mesh space (WolvenKit glb = (x, z, -y))."""
    d, j, b = load(p)
    g = [
        read(d, j, b, pr["attributes"]["POSITION"])
        for m in j["meshes"]
        for pr in m["primitives"]
    ]
    return [from_glb_space(x) for x in g]


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


def repair_buffers(done, meshes, pack, glbs, work, wk, game, log, skipped, moves=False):
    """WolvenKit 9.1.1 import can write big buffers (> ~1.4 MB) the game can't decode. Rebuild only those meshes;
    a mesh that can't be rebuilt is dropped (listed as skipped). The rebuild writes positions only: moves=True
    (copied skin weights) says so in the log."""
    import tempfile

    for s, t in list(done):
        p = os.path.join(pack, t)
        if t not in glbs or not rdar.bad_buffers(*rdar.loose_mesh(p), meshes[s]):
            continue
        log(
            "  WolvenKit wrote a broken big buffer in %s; rebuilding it directly%s ..."
            % (os.path.basename(t), " (its movement can't be copied)" if moves else "")
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


# ---------------- meshes WolvenKit can't read: own reader ----------------


class OwnMesh:
    """A .mesh read without WolvenKit, for files it can't read (e.g. game 1.6-era meshes whose material list it trips
    over). Only what the refit needs is parsed: the CR2W name/export tables, CMesh.boundingBox and the render blob
    header (quantization, chunk vertex counts/offsets/strides). Every other property, the material list included, is
    skipped by its stored size. Positions (Short4N) are decoded in mesh space; with_positions() writes new ones into a
    copy of the same render buffer (same size, same layout) and patches header floats in place, so nothing but the
    position xyz shorts, those floats and the buffer table can change (checked in own_refit)."""

    def __init__(self, data):
        d = self.d = bytes(data)
        assert d[:4] == b"CR2W", "not CR2W"
        tabs = [struct.unpack_from("<III", d, 40 + 12 * i) for i in range(10)]
        self.names = []
        for i in range(tabs[1][1]):
            o = tabs[0][0] + struct.unpack_from("<I", d, tabs[1][0] + 8 * i)[0]
            self.names.append(d[o : d.index(b"\0", o)].decode("latin-1"))
        ex = {}
        for i in range(tabs[4][1]):
            e = struct.unpack_from("<HHIIIII", d, tabs[4][0] + 24 * i)
            ex.setdefault(self.names[e[0]], []).append(e[4])
        assert len(ex.get("CMesh", ())) == len(ex.get("rendRenderMeshBlob", ())) == 1, (
            "not a plain mesh"
        )
        mesh = self.struct(ex["CMesh"][0])[0]
        blob = self.struct(ex["rendRenderMeshBlob"][0])[0]
        h = self.struct(blob["header"][1])[0]
        self.qs, self.qs_at = self.vec(h.get("quantizationScale"))
        self.qo, _ = self.vec(h.get("quantizationOffset"))
        bb = self.struct(mesh["boundingBox"][1])[0] if "boundingBox" in mesh else {}
        self.bmin, self.bmin_at = self.vec(bb.get("Min"))
        self.bmax, self.bmax_at = self.vec(bb.get("Max"))
        self.spans = []  # (byte offset of the first position, stride, vertex count) per render chunk
        self.lods = []  # LOD number per render chunk
        for c in self.array(h["renderChunkInfos"]):
            cv = self.struct(c["chunkVertices"][1])[0]
            vl = self.struct(cv["vertexLayout"][1])[0]
            el = self.array(vl["elements"])[0]
            use, typ = (self.names[self.num(el, k, "H")] for k in ("usage", "type"))
            assert (use, typ, self.num(el, "streamIndex", "B")) == (
                "PS_Position",
                "PT_Short4N",
                0,
            ), "positions stored as %s %s" % (use, typ)
            off = self.nums(cv.get("byteOffsets"), "I")
            stride = self.nums(vl.get("slotStrides"), "B")
            self.spans.append(
                (off[0] if off else 0, stride[0], self.num(c, "numVertices", "H"))
            )
            lm = self.num(c, "lodMask", "B")  # lowest LOD bit = WolvenKit's LOD_<n>
            self.lods.append((lm & -lm).bit_length() or 1)
        assert self.spans and (self.qs > 0).all(), "no render chunks"
        self.bidx = (
            struct.unpack_from("<I", d, blob["renderBuffer"][1])[0] & 0xFFFF
        ) - 1
        e = rdar.buf_table(d)[self.bidx]
        self.buf = rdar.seg_raw(d[e[2] : e[2] + e[3]], e[4])
        assert all(
            o + (n - 1) * s + 6 <= len(self.buf) for o, s, n in self.spans if n
        ), "chunk outside the buffer"

    # CR2W value encoding: a struct is 0x00, then (u16 name, u16 type, u32 size incl. itself, value)*, then u16 0;
    # properties at their default value are not stored. Arrays and static arrays: u32 count + elements.
    def struct(self, o):
        assert self.d[o] == 0, "struct expected at %d" % o
        o, out = o + 1, {}
        while True:
            n, t = struct.unpack_from("<HH", self.d, o)
            if n == 0:
                return out, o + 2
            sz = struct.unpack_from("<I", self.d, o + 4)[0]
            out[self.names[n]] = (self.names[t], o + 8, sz - 4)
            o += 4 + sz

    def array(self, p):
        o, out = p[1] + 4, []
        for _ in range(struct.unpack_from("<I", self.d, p[1])[0]):
            s, o = self.struct(o)
            out.append(s)
        return out

    def num(self, s, k, f):
        return struct.unpack_from("<" + f, self.d, s[k][1])[0] if k in s else 0

    def nums(self, p, f):
        if not p:
            return []
        n = struct.unpack_from("<I", self.d, p[1])[0]
        return list(struct.unpack_from("<%d%s" % (n, f), self.d, p[1] + 4))

    def vec(
        self, p
    ):  # Vector4 -> (xyz, file offset of each float, None when stored as default 0)
        s = self.struct(p[1])[0] if p else {}
        at = [s[k][1] if k in s else None for k in "XYZ"]
        return np.array(
            [struct.unpack_from("<f", self.d, a)[0] if a else 0.0 for a in at]
        ), at

    def positions(self):
        """Mesh-space positions of all render chunks, stacked in chunk order (= WolvenKit's glb primitive order)."""
        return get_pos(self.buf, self.spans, self.qs, self.qo)

    def pos_bytes(self):
        """Indexes into the render buffer of every position x/y/z byte, in vertex order."""
        return np.concatenate(
            [
                (o + np.arange(n)[:, None] * s + np.arange(6)[None, :]).ravel()
                for o, s, n in self.spans
            ]
        )

    def with_positions(self, Q):
        """CR2W bytes with mesh-space positions Q written into the render buffer (Kraken-compressed with the game's
        Oodle). The quantization offset never changes; a scale axis grows only when Q leaves its range. The bounding
        box only grows, and only where Q goes beyond both the stored box and the source vertices (some old files
        have a box that doesn't hold their own vertices; it stays as it was).
        ponytail: a bounding-box axis stored as default 0 is left as it is (culling margin only)."""
        d = bytearray(self.d)
        qs = self.qs.copy()
        for i in np.nonzero(np.abs(Q - self.qo).max(0) > qs * (1 + 1e-6))[0]:
            qs[i] = float(np.float32(np.abs(Q[:, i] - self.qo[i]).max() * 1.001))
            struct.pack_into("<f", d, self.qs_at[i], qs[i])
        P = self.positions()
        lo, hi = Q.min(0), Q.max(0)
        blo, bhi = np.minimum(self.bmin, P.min(0)), np.maximum(self.bmax, P.max(0))
        for i in range(3):
            if lo[i] < blo[i] and self.bmin_at[i]:
                struct.pack_into("<f", d, self.bmin_at[i], lo[i])
            if hi[i] > bhi[i] and self.bmax_at[i]:
                struct.pack_into("<f", d, self.bmax_at[i], hi[i])
        qi = np.clip(np.round((Q - self.qo) / qs * 32767.0), -32767, 32767).astype(
            "<i2"
        )
        buf = np.frombuffer(bytearray(self.buf), np.uint8)
        buf[self.pos_bytes()] = qi.view(np.uint8).ravel()
        return splice(bytes(d), bytes(d), {self.bidx: buf.tobytes()})


def cr2w_content(d):
    """(objects part with the header CRC and the buffer table's offsets/stored sizes/CRCs zeroed, [decoded buffers]):
    what must stay equal when only the compression of a buffer changes."""
    oend, toff, ents = rdar.cr2w_bufs(d)
    h = bytearray(d[:oend])
    h[28:36] = bytes(8)
    h[40 + 5 * 12 + 8 : 40 + 5 * 12 + 12] = bytes(4)
    for i in range(len(ents)):
        h[toff + 24 * i + 8 : toff + 24 * i + 16] = bytes(8)
        h[toff + 24 * i + 20 : toff + 24 * i + 24] = bytes(4)
    return bytes(h), [rdar.seg_raw(d[e[2] : e[2] + e[3]], e[4]) for e in ents]


def own_refit(m, Q):
    """m.with_positions(Q) after proving the writer on this exact file: the unchanged positions must give back the
    same content byte for byte (save(load(x)) == x). The result is re-read: it may differ from the source only in
    the position bytes and the header floats that were patched, and every position is within half a quantization
    step of Q."""
    assert cr2w_content(m.with_positions(m.positions())) == cr2w_content(m.d), (
        "round trip not byte-exact"
    )
    new = m.with_positions(Q)
    (h0, b0), (h1, b1) = cr2w_content(m.d), cr2w_content(new)
    floats = {a + k for a in m.qs_at + m.bmin_at + m.bmax_at if a for k in range(4)}
    assert len(h0) == len(h1) and all(
        i in floats for i in range(len(h0)) if h0[i] != h1[i]
    ), "header changed"
    assert len(b0) == len(b1) and all(
        x == y for i, (x, y) in enumerate(zip(b0, b1)) if i != m.bidx
    )
    r0, r1 = np.frombuffer(b0[m.bidx], np.uint8), np.frombuffer(b1[m.bidx], np.uint8)
    keep = np.ones(len(r0), bool)
    keep[m.pos_bytes()] = False
    assert len(r0) == len(r1) and (r0[keep] == r1[keep]).all(), (
        "render buffer changed outside the positions"
    )
    m2 = OwnMesh(new)
    assert (np.abs(m2.positions() - Q) <= m2.qs / 32767.0 * 0.5 + 1e-6).all(), (
        "positions not written"
    )
    return new


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


# ---------------- garment layers: which garment of an outfit is worn over which ----------------

# ArchiveXL / Equipment-EX outfit slots that are clothing layers: (group, garmentOffset = the game's layer order,
# from Equipment-EX's slot list). Torso garments are layered among themselves, legs among themselves (a top can be
# tucked in or worn over the trousers, so torso vs legs is left alone), full-body garments with both.
LAYER_SLOTS = {
    "TorsoUnder": ("torso", 120000),
    "TorsoInner": ("torso", 150000),
    "TorsoMiddle": ("torso", 180000),
    "TorsoOuter": ("torso", 210000),
    "TorsoAux": ("torso", 240000),
    "LegsInner": ("legs", 130000),
    "LegsMiddle": ("legs", 160000),
    "LegsOuter": ("legs", 190000),
    "BodyUnder": ("body", 110000),
    "BodyInner": ("body", 140000),
    "BodyMiddle": ("body", 170000),
    "BodyOuter": ("body", 300000),
}
# Items without placementSlots: the slot Equipment-EX gives their base item's equipment area
BASE_SLOT = {
    "GenericInnerChestClothing": "TorsoInner",
    "GenericOuterChestClothing": "TorsoOuter",
    "GenericLegClothing": "LegsMiddle",
}
RECORD = re.compile(r"^(?=\S)[^#\n]*:[ \t]*$", re.M)  # a top-level TweakXL record
DEPOT_REF = re.compile(r'"\$value":\s*"([^"]+\.(?:ent|app|mesh))"', re.I)


def norm_path(p):
    return p.replace("\\\\", "\\").lstrip("*").replace("/", "\\").lower()


def tweak_items(paths):
    r"""(entityName pattern, outfit slot) of every clothing record with a layer slot in the .yaml files that come with
    the outfit: r6\tweaks next to the archive\pc\mod folder the files are in; for loose files, .yaml and r6\tweaks
    beside them or one folder up."""
    files = set()
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        parts = d.split(os.sep)
        low = [x.lower() for x in parts]
        i = next(
            (
                k
                for k in range(len(low) - 2)
                if low[k : k + 3] == ["archive", "pc", "mod"]
            ),
            None,
        )
        roots = (
            [d, os.path.dirname(d)] if i is None else [os.sep.join(parts[:i]) + os.sep]
        )
        for r in roots:
            if i is None:
                files.update(glob.glob(os.path.join(r, "*.y*ml")))
            tw = os.path.join(r, "r6", "tweaks", "**", "*.y*ml")
            files.update(glob.glob(tw, recursive=True))
    items = []
    for f in sorted(files):
        try:
            t = open(f, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        starts = [m.start() for m in RECORD.finditer(t)] + [len(t)]
        for a, b in zip(starts, starts[1:]):
            blk = t[a:b]
            en = re.search(r"^\s+entityName:\s*['\"]?([^'\"\s#]+)", blk, re.M)
            if not en:
                continue
            sl = re.search(r"OutfitSlots\.(\w+)", blk)
            base = re.search(r"\$base:\s*['\"]?Items\.(\w+)", blk)
            slot = sl.group(1) if sl else BASE_SLOT.get(base.group(1)) if base else None
            if slot in LAYER_SLOTS:
                pat = ".*".join(map(re.escape, re.split(r"\$\{[^}]*\}", en.group(1))))
                items.append((re.compile(pat, re.I), slot))
    return items


def garment_slots(src, js, items, sx, wk, game):
    r"""{mesh path (as in src, source body's member for {body} meshes): outfit slot} for the meshes of the outfit's
    clothing records `items`: record entityName -> the ArchiveXL factory .csv's .ent -> every .ent / .app it refers to
    (serialized JSON in js, mirroring src) -> meshes. A mesh claimed by records with different slots gets none.
    ponytail: one .app shared by several items gives all of them all its meshes (-> no slot, no layer push); add
    appearance-name matching if such outfits need it."""
    if not items:
        return {}
    names = {}
    csvd = os.path.join(js, "_csv")
    os.makedirs(csvd, exist_ok=True)
    for c in glob.glob(os.path.join(src, "**", "*.csv"), recursive=True):
        try:
            run([wk, "convert", "serialize", c, "-o", csvd], game)
            rows = (
                json.load(
                    open(
                        os.path.join(csvd, os.path.basename(c) + ".json"),
                        encoding="utf-8-sig",
                    )
                )["Data"]["RootChunk"].get("compiledData")
                or []
            )
        except (RuntimeError, OSError, ValueError, KeyError, TypeError):
            continue
        for r in rows:
            if len(r) >= 2 and str(r[1]).lower().endswith(".ent"):
                names[str(r[0]).lower()] = norm_path(str(r[1]))
    refs = {}
    for f in glob.glob(os.path.join(js, "**", "*.json"), recursive=True):
        rel = os.path.relpath(f, js)
        if not rel.lower().startswith("_csv"):
            text = open(f, encoding="utf-8", errors="ignore").read()
            refs[rel[:-5].lower()] = {norm_path(m) for m in DEPOT_REF.findall(text)}
    claims = {}
    for rx, slot in items:
        for name, root in names.items():
            if not rx.fullmatch(name):
                continue
            seen, todo = set(), [root]
            while todo:
                n = todo.pop()
                if n in seen:
                    continue
                seen.add(n)
                for r in refs.get(n, ()):
                    if r.endswith(".mesh"):
                        m = r.replace("{gender}", "w").replace("{body}", sx)
                        claims.setdefault(m, set()).add(slot)
                    else:
                        todo.append(r)
    return {m: s.pop() for m, s in claims.items() if len(s) == 1}


def under(a, b):
    """True when a garment in slot a is worn under one in slot b (same group or a full-body garment, lower layer)."""
    (ga, ra), (gb, rb) = LAYER_SLOTS[a], LAYER_SLOTS[b]
    return ra < rb and (ga == gb or "body" in (ga, gb))


def inner_layers(slot, worn):
    """{LOD: positions} of the refit garments in `worn` [(slot, [(LOD, positions)])] worn under a garment in `slot`,
    None when there are none."""
    acc = {}
    for s2, parts in worn if slot in LAYER_SLOTS else ():
        if under(s2, slot):
            for lod, Q in parts:
                acc.setdefault(lod, []).append(Q)
    return {k: np.vstack(v) for k, v in acc.items()} or None


# ---------------- one outfit ----------------


def refit_mod(
    arcs,
    name,
    work,
    out,
    src_body,
    tgt,
    cages,
    wk,
    game,
    log,
    progress=lambda f: None,
    origin=None,
    replace_own=False,
    copy_moves=False,
):
    """arcs: the outfit's .archive files, made for body src_body; refit to body tgt (body tags; cages: {tag: glbs}).
    origin: files whose surroundings hold the outfit's r6\\tweaks .yaml (default arcs), for the garment layers.
    replace_own: also refit {body} parts the mod already has a target-body version of, and replace that version
    (the output then loads first: '!!!').
    copy_moves: clothing close to the body gets the body's skin weights (copy_weights), so it moves with the body,
    also with rig / breast physics mods. Meshes written without WolvenKit's import (own reader, rebuilt big
    buffers) keep their own weights; the log says so.
    -> path of the written refit archive, or None if nothing needed a refit.
    Raises ToolError when nothing could be refit or the final safety check fails."""
    sx, tx = xl_name(src_body), xl_name(tgt)
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
        od = os.path.join(js, os.path.dirname(os.path.relpath(f, src)))
        os.makedirs(
            od, exist_ok=True
        )  # mirrored: two .app files of the same name don't overwrite each other
        try:
            run([wk, "convert", "serialize", f, "-o", od], game)
        except RuntimeError:
            log(
                "  (could not read %s, an old file format; its meshes are treated as fixed)"
                % os.path.basename(f)
            )
    for f in glob.glob(os.path.join(js, "**", "*.json"), recursive=True):
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
        s, t = pat.replace("{body}", sx), pat.replace("{body}", tx)
        fam = re.escape(pat).replace(r"\{body\}", r"[a-z0-9_]+")
        covered |= {k for k in meshes if re.fullmatch(fam, k)}
        if t in meshes and not (replace_own and s in meshes):
            skipped.append(
                (
                    t,
                    "the mod already has a %s version%s"
                    % (
                        body_name(tgt),
                        ""
                        if replace_own
                        else " (tick 'Replace the outfit's own version' to refit it anyway)",
                    ),
                )
            )
        elif s in meshes:
            jobs.append((s, t))
        else:
            skipped.append((pat, "no %s mesh to start from" % sx))
    for k in meshes:
        stem = os.path.splitext(os.path.basename(k))[0]
        if k in covered or HELPER.search(k):
            continue
        if MALE.search(k):
            skipped.append((k, "male mesh"))
            continue
        if stem.endswith(
            "_" + sx
        ):  # family whose {body} path is only stored hashed: add the sibling
            t = k[: -len(sx) - 5] + tx + ".mesh"
            if t not in meshes or replace_own:
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
    log(
        "Refitting %d mesh(es) from %s to %s ..."
        % (len(jobs), body_name(src_body), body_name(tgt))
    )
    replaced = [t for s, t in jobs if s != t and t in meshes]
    if replaced:
        log(
            "  replacing the outfit's own %s version of: %s"
            % (body_name(tgt), ", ".join(os.path.basename(t) for t in replaced))
        )
    slots = {}
    if len(jobs) > 1:
        slots = garment_slots(src, js, tweak_items(origin or arcs), sx, wk, game)
        slots = {s: slots[s] for s, _ in jobs if s in slots}
        rank = lambda s: LAYER_SLOTS[slots[s]][1] if s in slots else 0
        jobs.sort(key=lambda st: rank(st[0]))  # inner layers first
        if any(under(a, b) for a in slots.values() for b in slots.values()):
            log(
                "Garment layers (inner first): %s. Each layer is kept 1 mm above the ones under it."
                % ", ".join(
                    "%s (%s)" % (os.path.splitext(os.path.basename(s))[0], slots[s])
                    for s, _ in jobs
                    if s in slots
                )
            )
    worn = []  # (slot, [(LOD, refit positions)]) of the refit garments with a layer slot
    done, glbs = [], {}
    for n, (s, t) in enumerate(jobs):
        progress(0.8 * n / len(jobs))
        own = None
        try:
            run([wk, "export", meshes[s], "-o", glbd, "-gp", game], game)
            g = os.path.join(glbd, os.path.splitext(os.path.basename(s))[0] + ".glb")
            pts = glb_points(g)
        except RuntimeError:
            try:  # e.g. game 1.6-era meshes: the own reader needs only the vertex data
                own = OwnMesh(open(meshes[s], "rb").read())
                pts = to_glb(own.positions())
            except Exception:
                skipped.append((s, "WolvenKit can't read this mesh"))
                continue
        stem = os.path.splitext(os.path.basename(s))[0]
        if not BODY_SUFFIX.search(stem) and far_from_body(
            pts, (cages[src_body], cages[tgt])
        ):
            skipped.append((s, "not worn on the body (same on every body)"))
            continue
        tdir = os.path.dirname(t)
        os.makedirs(os.path.join(raw, tdir), exist_ok=True)
        os.makedirs(os.path.join(pack, tdir), exist_ok=True)
        inner = inner_layers(slots.get(s), worn)
        lift = lambda k: ", %d lifted over the layer under it" % k if k else ""
        if own:  # split by render chunk: each chunk's LOD meets the same LOD of the inner layers
            cut = np.cumsum([n for _, _, n in own.spans])[:-1]
            parts = list(zip(own.lods, np.split(pts, cut)))
            Qs, pushed, lifted = refit_parts(parts, cages[src_body], cages[tgt], inner)
            try:
                data = own_refit(own, from_glb_space(np.vstack(Qs)))
            except Exception as e:
                skipped.append((s, "could not be written safely (%s)" % str(e)[:200]))
                continue
            open(os.path.join(pack, t), "wb").write(data)
            log(
                "  %s: %d vertices moved, %d pushed out of the skin%s (read with the tool's own reader, old file "
                "format%s)"
                % (
                    os.path.basename(t),
                    len(pts),
                    pushed,
                    lift(lifted),
                    "; its movement can't be copied" if copy_moves else "",
                )
            )
            done.append((s, t))
            if s in slots:
                worn.append((slots[s], [(lod, Q) for (lod, _), Q in zip(parts, Qs)]))
            continue
        tglb = os.path.join(raw, os.path.splitext(t)[0] + ".glb")
        g2 = os.path.join(
            work, "srcglb", t[:-5] + ".glb"
        )  # own copy: glbd is keyed by basename
        os.makedirs(os.path.dirname(g2), exist_ok=True)
        shutil.copy(g, g2)
        glbs[t] = (g2, tglb)
        nv, pushed, lifted, parts, copied = refit_glb(
            g, tglb, cages[src_body], cages[tgt], inner, copy_moves
        )
        log(
            "  %s: %d vertices moved, %d pushed out of the skin%s%s"
            % (
                os.path.basename(t),
                nv,
                pushed,
                lift(lifted),
                ", %d now move with the body" % copied if copied else "",
            )
        )
        shutil.copy(meshes[s], os.path.join(pack, t))
        done.append((s, t))
        if s in slots:
            worn.append((slots[s], parts))
    if glbs:
        imp = run([wk, "import", raw, "-o", pack, "-k"], game, ok="Imported")
        m = re.search(r"Imported (\d+)/(\d+)", imp)
        if not m:
            raise ToolError(
                "WolvenKit could not import the refit meshes:\n" + imp[-500:]
            )
        for s, t in list(
            done
        ):  # a mesh WolvenKit could not re-import is still the unrefit copy -> drop it
            if (
                t in glbs
                and open(os.path.join(pack, t), "rb").read()
                == open(meshes[s], "rb").read()
            ):
                os.remove(os.path.join(pack, t))
                done.remove((s, t))
                skipped.append((s, "WolvenKit can't write this mesh back"))
        progress(0.85)
        repair_buffers(
            done, meshes, pack, glbs, work, wk, game, log, skipped, copy_moves
        )
    for s, why in skipped:
        log("  skipped %s: %s" % (os.path.basename(s), why))
    if not done:
        if any("WolvenKit" in w or "rebuilt" in w or "safely" in w for _, w in skipped):
            raise ToolError("none of the meshes could be refit (see the list above).")
        log("Nothing needed a refit.")
        return None
    static = any(
        s == t or t in meshes for s, t in done
    )  # an existing path is replaced: load first
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
