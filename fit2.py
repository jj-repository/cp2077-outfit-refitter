"""Fit 2.0: the geometry of a refit.

1. Body-to-body correspondence by UV (corr, carry): the bodies share their skin textures, so the same UV in the same
   body part is the same anatomical spot. Nearest-3D only where the UV layouts differ (vanilla's lower legs / feet).
2. Height-preserving carry: a garment vertex near the skin goes to the same anatomical spot of the target body, at its
   own original height over the skin (bind -> carry).
3. Mesh-aware solve (solve): the pointwise carry is a constraint only where it is reliable (close to the skin, one
   clear closest skin point); the rest of the garment follows on its own connectivity (Laplacian of the displacement,
   coincident split vertices welded), so loose and bridging parts keep their shape.
4. Rigid parts (same skin weights on every vertex of a connected part) move by one best-fit rigid transform.
5. Collision safety net against the body as the game shows it, offsets smoothed over the mesh, the order of stacked
   layers kept (clear).
6. Per-vertex rotation of the deformation, for normals and tangents (rotations).
"""

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spl
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

import refit as R

UV_TOL = 0.003  # UV distance up to which two bodies' UVs count as the same spot
NEAR, FAR = 0.005, 0.020  # m over the skin: full pointwise constraint up to NEAR, none from FAR
MINVIS = 0.001  # m: the least clearance of a visible vertex of a garment that relied on the body being crushed
MINOWN = 0.0002  # m: the same for a garment that never did (BV's own Angel pasties sit 0.3 mm over the skin)
KEEP = 0.002  # m: a vertex the author had at least this high never ends lower than this
PRIOR = 1e-3  # weight of the body displacement field for unconstrained vertices (vs 1 per mesh edge)
SLIDE_ROUNDS, SLIDE_ANCHOR, SLIDE_PLANE = 4, 0.05, 10  # sliding solve: rounds, pull to the anatomical place, hold to the height (vs 1 per edge)
SLIDE_MAX = 150000  # welded vertices up to which the sliding solve is run (memory / time)
RIGID_SIZE, RIGID_RMS = 0.06, 0.002  # m: a stiff part is at most this big, or its soft fit is this close to rigid
SQ_SPREAD = (12, 0.85)  # squashed shape: rings / share per ring for spreading its lift over the crushed body
HARD = 30.0  # weight of a fully reliable pointwise constraint


def base(spec):
    """The body spec without its 'extra' geometry: the surface the correspondence works on."""
    return ";".join(p for p in spec.split(";") if not R.spec_opts(p)[3])


def _unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


# ---------------- 1. correspondence ----------------


def _locator(tgt, keys):
    """UV triangles of target parts `keys` (part numbers): (triangle numbers, their UVs, kd-tree of UV centres)."""
    ck = ("loc", tgt, keys)
    if ck not in R._CORR:
        F = R.surface(tgt)[2]
        UV, part, _ = R.surface_uv(tgt)
        fsel = np.nonzero(np.isin(part[F[:, 0]], keys))[0]
        tri = UV[F[fsel]]
        R._CORR[ck] = (fsel, tri, cKDTree(tri.mean(1)))
    return R._CORR[ck]


def _part_map(src, tgt):
    """{source part number: (target part numbers)}: the same file + submesh when the target has it, else the target
    parts no source part matches (a chest add-on mesh stands in for the hidden chest submeshes)."""
    ks, kt = R.surface_uv(src)[2], R.surface_uv(tgt)[2]
    rest = tuple(i for i, k in enumerate(kt) if k not in ks)
    return {i: ((kt.index(k),) if k in kt else rest) for i, k in enumerate(ks)}


def uv_locate(uv, part, X, src, tgt):
    """Where UVs `uv` of source parts `part` lie on target body tgt: -> (triangle corners (n, 3) into surface(tgt),
    barycentric weights, UV miss). Among triangles that hold the UV (mirrored halves share UVs) the one nearest in
    3D to X wins. miss = inf where the target has no such part."""
    Pt, _, Ft, t3d = R.surface(tgt)
    UVt, partt, _ = R.surface_uv(tgt)
    n = len(uv)
    f, bu, miss = np.zeros((n, 3), np.int64), np.zeros((n, 3)), np.full(n, np.inf)
    pm = _part_map(src, tgt)
    z = np.zeros((1, 1, 1))
    for p in np.unique(part):
        keys = pm.get(int(p), ())
        sel = np.nonzero(part == p)[0]
        if p < 0 or not keys:
            continue
        fsel, tri, tree = _locator(tgt, keys)
        if not len(fsel):
            continue
        k = min(16, len(fsel))
        for s in range(0, len(sel), 20000):
            i = sel[s : s + 20000]
            # candidates: the k nearest triangles in UV, and the k nearest in 3D (toes and fingers stack many UV
            # charts on each other; the bodies are close enough there for the 3D neighbours to hold the right one)
            tg = fsel[tree.query(uv[i], k=k)[1].reshape(len(i), k)]
            t3 = t3d.query(X[i], k=k)[1].reshape(len(i), k)
            tg = np.concatenate([tg, t3], 1)
            T = np.concatenate([UVt[Ft[tg]], np.zeros(tg.shape + (3, 1))], -1)
            x = np.concatenate([uv[i], np.zeros((len(i), 1))], 1)[:, None]
            U = R.closest_on_triangles(x, T[..., 0, :], T[..., 1, :], T[..., 2, :])
            dd = np.linalg.norm(x - (U[..., None] * T).sum(-2), axis=-1)
            dd[~np.isin(partt[Ft[tg][..., 0]], keys)] = np.inf
            fc = Ft[tg]
            d3 = np.linalg.norm(
                (U[..., None] * Pt[fc]).sum(-2) - X[i][:, None], axis=-1
            )
            hitc = dd < 1e-4  # triangles that hold the UV: the nearest in 3D; none: the nearest in UV
            best = np.where(hitc.any(1), np.where(hitc, d3, np.inf).argmin(1), (dd + 0.01 * d3).argmin(1))
            r = np.arange(len(i))
            f[i], bu[i], miss[i] = fc[r, best], U[r, best], dd[r, best]
    return f, bu, miss


def skeleton_vote(src, tgt, f, bu, Ps):
    """Per source vertex, 1 where the UV mapping is the anatomical one and 0 where the nearest point of the target's
    skin is: the bodies share one skeleton, so the same spot of the body carries the same bone weights. Compared are
    the vertex's own weights with those at its UV target and at its nearest target point (L1 distance), smoothed
    over the surface. On the chest the UV mapping wins by far (vanilla -> Angel 0.02 vs 0.23); on EVE's thighs the
    UVs have slid 8 mm down the leg against the weights and the nearest point wins. All 1 when a body has no skin."""
    try:
        na, Ja, Wa, _ = R.body_skin(src)
        nb, Jb, Wb, _ = R.body_skin(tgt)
    except (KeyError, IndexError, ValueError):
        return np.ones(len(Ps))
    if len(Ja) != len(Ps) or not Wa.any() or not Wb.any():
        return np.ones(len(Ps))
    names = sorted(set(na) | set(nb))

    def dense(nm, J, W):
        col = np.array([names.index(x) for x in nm])
        D = np.zeros((len(J), len(names)), np.float32)
        np.add.at(D, (np.repeat(np.arange(len(J)), J.shape[1]), col[J].ravel()), W.ravel())
        return D

    A, B = dense(na, Ja, Wa), dense(nb, Jb, Wb)
    fv, bn = R.skin_height(Ps, tgt, faces=True)[3:]
    euv = np.abs((bu[..., None] * B[f]).sum(1) - A).sum(1)
    enn = np.abs((bn[..., None] * B[fv]).sum(1) - A).sum(1)
    pref = np.clip(0.5 + (enn - euv) / 0.05, 0, 1)
    nbr = cKDTree(Ps).query(Ps, k=min(24, len(Ps)))[1]
    for _ in range(3):
        pref = pref[nbr].mean(1)
    return np.clip((pref - 0.3) / 0.3, 0, 1)


def corr(src, tgt):
    """Correspondence src body -> tgt body per source vertex (cached): dict with
    T: target point (same UV in the same part; nearest target vertex, smoothed, where the UVs don't match),
    ok: UV match, w: 1 where the UV mapping is used, fading to 0 within 2 cm of a vertex without a match,
    D: displacement field (w * UV + (1 - w) * nearest-3D), Dnn: the nearest-3D field alone, tree: kd-tree of the
    source vertices."""
    ck = ("corr", src, tgt)
    if ck not in R._CORR:
        Ps = R.surface(src)[0]
        Pt = R.surface(tgt)[0]
        UV, part, _ = R.surface_uv(src)
        f, bu, miss = uv_locate(UV, part, Ps, src, tgt)
        ok = miss < UV_TOL
        Tuv = (bu[..., None] * Pt[f]).sum(1)
        stree = cKDTree(Ps)
        Dnn = R.skin_height(Ps, tgt)[1] - Ps  # nearest point of the target's skin
        nb = stree.query(Ps, k=6)[1]
        for _ in range(2):
            Dnn = Dnn[nb].mean(1)
        # a lone wrong hit (toes: stacked UV charts): far off what its neighbours do -> treated as no match
        ok &= np.linalg.norm(Tuv - Ps - np.median((Tuv - Ps)[nb], axis=1), axis=1) < 0.02
        w = skeleton_vote(src, tgt, f, bu, Ps)
        if not ok.all():
            w = np.minimum(w, np.clip(cKDTree(Ps[~ok]).query(Ps)[0] / 0.02, 0, 1))
        D = np.where(ok[:, None], w[:, None] * (Tuv - Ps), 0) + (1 - w)[:, None] * Dnn
        R._CORR[ck] = dict(T=Ps + D, ok=ok, w=w, D=D, Dnn=Dnn, tree=stree, miss=miss)
    return R._CORR[ck]


def rim_vertices(spec):
    """Per vertex of surface(spec): True on an open edge of the body shape (an edge with one triangle, after
    welding the coincident borders of its parts)."""
    ck = ("rim", spec)
    if ck not in R._CORR:
        P, _, F, _ = R.surface(spec)
        _, inv = np.unique(np.round(P / 1e-5).astype(np.int64), axis=0, return_inverse=True)
        W = inv.ravel()[F]
        E = np.sort(np.vstack([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]]), axis=1)
        E = E[E[:, 0] != E[:, 1]]
        u, cnt = np.unique(E, axis=0, return_counts=True)
        on = np.zeros(inv.max() + 1, bool)
        on[u[cnt == 1].ravel()] = True
        R._CORR[ck] = on[inv.ravel()]
    return R._CORR[ck]


def bind(X, spec, k=32):
    """Closest point of body surface `spec` for points X: -> dict h (signed height), q (skin point), fv (triangle
    corners), bu (barycentric weights), n (interpolated unit normal at q), amb (True where a second, separate part of
    the skin is almost as close: between the breasts, in the armpit, between the legs)."""
    P, N, F, tree = R.surface(spec)
    edge = rim_vertices(spec)
    n = len(X)
    out = dict(
        h=np.zeros(n),
        q=np.zeros((n, 3)),
        fv=np.zeros((n, 3), np.int64),
        bu=np.zeros((n, 3)),
        n=np.zeros((n, 3)),
        amb=np.zeros(n, bool),
    )
    for s in range(0, n, 10000):
        x = X[s : s + 10000]
        sl = slice(s, s + len(x))
        f = F[tree.query(x, k=k)[1]]
        U = R.closest_on_triangles(x[:, None], P[f[..., 0]], P[f[..., 1]], P[f[..., 2]])
        c = (U[..., None] * P[f]).sum(-2)
        dd = np.linalg.norm(x[:, None] - c, axis=-1)
        best = dd.argmin(1)
        r = np.arange(len(x))
        cq, u, fb, d0 = c[r, best], U[r, best], f[r, best], dd[r, best]
        nn = _unit((u[:, :, None] * N[fb]).sum(1))
        sg = np.sign(((x - cq) * nn).sum(1))
        sg[sg == 0] = 1
        # a second sheet: a candidate skin point well away from the closest one (further than the point is from the
        # skin, at least 1.5 cm), facing another way, and not much further from the point than the closest
        # a second sheet: a candidate skin point away from the closest one (at least 1.5 cm, and further than the
        # point is from the skin) that is clearly nearer to the point than it would be on flat skin, and not much
        # further from the point than the closest
        gap = np.linalg.norm(c - cq[:, None], axis=-1)
        other = (gap > np.maximum(1.5 * d0, 0.015)[:, None]) & (dd < 0.8 * np.sqrt(d0[:, None] ** 2 + gap**2))
        d2 = np.where(other, dd, np.inf).min(1)
        # beyond an open edge of the body shape (the neck: the head is another mesh): the closest point is on
        # the rim, so the offset is no height and has no sign
        rim = ((u * edge[fb]).sum(1) > 0.999) & (d0 > 0.002)
        sg[rim] = 1
        out["amb"][sl] = (d2 < 2.0 * d0 + 0.004) | rim
        out["h"][sl], out["q"][sl], out["fv"][sl], out["bu"][sl], out["n"][sl] = (
            d0 * sg,
            cq,
            fb,
            u,
            nn,
        )
    return out


def height(X, spec):
    """refit.skin_height with the open rims of the body shape handled: -> (height, skin point, direction away from
    the skin, on the rim). Past a rim (the neck opening) the distance has no sign: it counts as above the skin."""
    h, q, n, fv, bu = R.skin_height(X, spec, faces=True)
    rim = ((bu * rim_vertices(spec)[fv]).sum(1) > 0.999) & (np.abs(h) > 0.002)
    flip = rim & (h < 0)
    h[flip], n[flip] = -h[flip], -n[flip]
    return h, q, n, rim


def rotate(v, a, b):
    """Vectors v turned by the smallest rotation that takes unit vectors a onto b (row-wise, Rodrigues)."""
    k = np.cross(a, b)
    s = np.linalg.norm(k, axis=1, keepdims=True)
    c = (a * b).sum(1, keepdims=True)
    k = k / np.maximum(s, 1e-12)
    out = v * c + np.cross(k, v) * s + k * (k * v).sum(1, keepdims=True) * (1 - c)
    return np.where(s > 1e-9, out, v)


def carry(X, src, tgt, b=None):
    """Garment points X bound to body src (b = bind(X, src)) -> (their place on body tgt: the same anatomical skin
    point, the same offset from it turned with the skin's normal; target skin point; target normal)."""
    b = b or bind(X, src)
    c = corr(src, tgt)
    Pt, Nt, _, _ = R.surface(tgt)
    UV, part, _ = R.surface_uv(src)
    fv, bu = b["fv"], b["bu"]
    uv = (bu[..., None] * UV[fv]).sum(1)
    f, tb, miss = uv_locate(uv, part[fv[:, 0]], b["q"], src, tgt)
    w = (bu * c["w"][fv]).sum(1)
    w[miss >= UV_TOL] = 0
    p = (tb[..., None] * Pt[f]).sum(1)
    n = (tb[..., None] * Nt[f]).sum(1)
    fb = np.nonzero(w < 1)[0]
    if len(fb):  # no UV match here: the nearest-3D field, then onto the target's skin
        g = bind(b["q"][fb] + (bu[fb][..., None] * c["Dnn"][fv[fb]]).sum(1), tgt)
        p[fb] = w[fb, None] * p[fb] + (1 - w[fb, None]) * g["q"]
        n[fb] = w[fb, None] * n[fb] + (1 - w[fb, None]) * g["n"]
    n = _unit(n)
    return p + rotate(X - b["q"], b["n"], n), p, n


def field(X, src, tgt):
    """X moved by the body displacement field (inverse-distance weights of the 8 nearest source body vertices)."""
    c = corr(src, tgt)
    dist, nb = c["tree"].query(X, k=8)
    w = 1.0 / np.maximum(dist, 1e-4) ** 2
    w /= w.sum(1, keepdims=True)
    return X + (c["D"][nb] * w[..., None]).sum(1)


# ---------------- 3. the garment's own connectivity ----------------


def weld(X, F):
    """Coincident vertices as one: -> (welded positions, vertex -> welded index, unique welded edges)."""
    _, first, inv = np.unique(
        np.round(X / 1e-5).astype(np.int64),
        axis=0,
        return_index=True,
        return_inverse=True,
    )
    inv = inv.ravel()
    if F is None or not len(F):
        return X[first], inv, np.zeros((0, 2), np.int64)
    W = inv[F]
    E = np.vstack([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]])
    E = np.unique(np.sort(E[E[:, 0] != E[:, 1]], axis=1), axis=0)
    return X[first], inv, E


def laplacian(n, E, w=None):
    """(graph Laplacian, adjacency) of the welded mesh; w: weight per edge (default 1)."""
    i, j = E[:, 0], E[:, 1]
    A = sp.coo_matrix((np.ones(len(E)) if w is None else w, (i, j)), shape=(n, n))
    A = (A + A.T).tocsr()
    return sp.diags(np.asarray(A.sum(1)).ravel()) - A, A


def solve(X, E, target, weight, prior):
    """Positions that follow `target` where `weight` is high and elsewhere keep the garment's shape: minimises
    sum over edges |d_i - d_j|^2 + weight_i |d_i - (target_i - X_i)|^2 + PRIOR |d_i - (prior_i - X_i)|^2 over the
    displacement d (one sparse solve, three right-hand sides)."""
    n = len(X)
    if not len(E):
        a = (HARD * weight)[:, None]
        return X + (a * (target - X) + PRIOR * (prior - X)) / (a + PRIOR)
    # short edges are stiffer (weight = median edge length / length, 0.2 .. 20): the rim between the inner and the
    # outer face of a thick part, seams and small details keep their size while the long edges take the change
    le = np.linalg.norm(X[E[:, 0]] - X[E[:, 1]], axis=1)
    L, _ = laplacian(n, E, np.clip(np.median(le) / np.maximum(le, 1e-9), 0.2, 20.0))
    c = HARD * weight
    A = (L + sp.diags(c + PRIOR)).tocsc()
    rhs = c[:, None] * (target - X) + PRIOR * (prior - X)
    lu = spl.splu(A)
    return X + np.column_stack([lu.solve(rhs[:, k]) for k in range(3)])


def unfold(X, Y, F, A, weight, rounds=12):
    """Iron out triangles that turned over (Y in place): where a part is squeezed together (a top bridging the
    cleavage of a body whose breasts sit closer), its small folds would overlap. The vertices of turned triangles and
    their neighbours move toward the middle of their own neighbours until nothing is turned any more; vertices held
    by their height over the skin (weight > 0.5) stay. F: triangles in welded indices. -> triangles turned before."""
    n0 = np.cross(X[F[:, 1]] - X[F[:, 0]], X[F[:, 2]] - X[F[:, 0]])
    deg = np.maximum(np.asarray(A.sum(1)).ravel(), 1)[:, None]
    first = None
    for _ in range(rounds):
        n1 = np.cross(Y[F[:, 1]] - Y[F[:, 0]], Y[F[:, 2]] - Y[F[:, 0]])
        bad = (n0 * n1).sum(1) < 0
        first = int(bad.sum()) if first is None else first
        if not bad.any():
            break
        m = np.zeros(len(X), bool)
        m[F[bad].ravel()] = True
        m = (A @ m.astype(float)) > 0  # and their neighbours
        m &= weight < 0.5
        if not m.any():
            break
        Y[m] = 0.5 * Y[m] + 0.5 * ((A @ Y) / deg)[m]
    return first


def slide(X, Y, E, he, weight, tgt):
    """Let the garment slide along the skin to even out its own stretch (Y in place). Skin is not cloth: the skin of
    a bigger breast is stretched very unevenly against the smaller one's, and a garment locked to the same skin
    spots inherits that (a fishnet pattern pulled to 0.5x here and 2.4x there, where its author's own refit has
    1.05x .. 1.36x). As-rigid-as-possible solve on the welded mesh: every edge wants its original length and its
    direction turned with the surface (rotations from the edge fans); a vertex held by its height (weight > 0.5)
    may move freely along the skin but stays on the plane of that height over it; a weak pull (SLIDE_ANCHOR) keeps
    every vertex near its anatomical place, so pasties stay on the nipple. Planes and rotations are renewed each
    round; at the end the held vertices go exactly back to their heights.
    ponytail: one sparse solve of 3 x vertices unknowns per round; skipped above SLIDE_MAX welded vertices.
    -> False when it was skipped."""
    n = len(X)
    if not SLIDE_ROUNDS or n > SLIDE_MAX or not len(E):
        return False
    i, j = E[:, 0], E[:, 1]
    L3 = sp.kron(laplacian(n, E)[0], sp.identity(3), format="csr")
    d = X[i] - X[j]
    Y0 = Y.copy()
    held = np.nonzero(weight > 0.5)[0]
    rows = (3 * np.arange(n)[:, None, None] + np.arange(3)[None, :, None] + np.zeros((1, 1, 3), np.int64)).ravel()
    cols = (3 * np.arange(n)[:, None, None] + np.arange(3)[None, None, :] + np.zeros((1, 3, 1), np.int64)).ravel()
    eye = SLIDE_ANCHOR * sp.identity(3 * n, format="csr")

    def planes():
        N, P = np.zeros((n, 3)), np.zeros((n, 3))
        if len(held):
            h, q, nrm, rim = height(Y[held], tgt)
            k = held[~rim]
            N[k], P[k] = nrm[~rim], q[~rim] + nrm[~rim] * he[k, None]
        return N, P

    for _ in range(SLIDE_ROUNDS):
        N, P = planes()
        C = sp.coo_matrix(((SLIDE_PLANE * N[:, :, None] * N[:, None, :]).ravel(), (rows, cols)), shape=(3 * n, 3 * n))
        Rm = rotations(X, Y, E)
        rd = np.einsum("nij,nj->ni", 0.5 * (Rm[i] + Rm[j]), d)
        b = np.zeros((n, 3))
        np.add.at(b, i, rd)
        np.add.at(b, j, -rd)
        b += SLIDE_PLANE * N * (N * P).sum(1, keepdims=True) + SLIDE_ANCHOR * Y0
        Y[:] = spl.spsolve((L3 + C + eye).tocsc(), b.ravel()).reshape(n, 3)
    N, P = planes()
    k = np.abs(N).sum(1) > 0
    Y[k] = P[k]
    return True


def kabsch(A, B, w=None):
    """Best rigid transform (rotation R, translation t) taking points A onto B: B ~ A @ R.T + t."""
    w = np.ones(len(A)) if w is None else w
    ca, cb = (A * w[:, None]).sum(0) / w.sum(), (B * w[:, None]).sum(0) / w.sum()
    H = ((A - ca) * w[:, None]).T @ (B - cb)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    Rm = Vt.T @ np.diag([1, 1, d]) @ U.T
    return Rm, cb - ca @ Rm.T


def rigid_parts(n, E, sig):
    """Connected parts of the welded mesh that move as one stiff piece in game: every vertex has the same skin
    weights (sig: one row per vertex that is equal for equal weights; None: no skin). A whole garment hanging on one
    bone counts too. -> (part label per vertex, [labels of the rigid parts])."""
    G = sp.coo_matrix((np.ones(len(E)), (E[:, 0], E[:, 1])), shape=(n, n))
    ncomp, lab = connected_components(G, directed=False)
    if sig is None:
        return lab, []
    order = np.argsort(lab, kind="stable")
    starts = np.searchsorted(lab[order], np.arange(ncomp))
    first = sig[order[starts]]
    same = np.ones(ncomp, bool)
    np.logical_and.at(same, lab, (np.abs(sig - first[lab]) < 0.02).all(1))
    cnt = np.bincount(lab, minlength=ncomp)
    return lab, [int(c) for c in np.nonzero(same & (cnt >= 4))[0]]


def rotations(X, Y, E):
    """Per vertex the rotation of the deformation X -> Y, from its edge fan (3x3 matrices; identity where a vertex
    has fewer than two edges)."""
    n = len(X)
    Rm = np.tile(np.eye(3), (n, 1, 1))
    if not len(E):
        return Rm
    i = np.concatenate([E[:, 0], E[:, 1]])
    j = np.concatenate([E[:, 1], E[:, 0]])
    a, b = X[j] - X[i], Y[j] - Y[i]
    H = np.zeros((n, 3, 3))
    np.add.at(H, i, a[:, :, None] * b[:, None, :])
    H += 1e-12 * np.eye(3)
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(
        np.linalg.det(
            np.einsum("nij,njk->nik", Vt.transpose(0, 2, 1), U.transpose(0, 2, 1))
        )
    )
    D = np.tile(np.eye(3), (n, 1, 1))
    D[:, 2, 2] = d
    Rm = Vt.transpose(0, 2, 1) @ D @ U.transpose(0, 2, 1)
    Rm[np.bincount(i, minlength=n) < 2] = np.eye(3)
    return Rm


# ---------------- 5. collision safety net ----------------


def floors(h0, hvis, minvis=MINVIS):
    """The least height over the shown target skin for vertices with original height h0 over the source body and hvis
    over the source body as the game showed it under the garment: (want, visible).
    - at least KEEP in the original: KEEP (plus up to 1 mm by the original height, so stacked layers keep their order)
    - between MINVIS and KEEP: the author's own height
    - below minvis but visible in game (the body was crushed under the garment): minvis/2 .. minvis, by height
    - hidden (inside the body as shown): no floor, it stays hidden."""
    vis = hvis > -0.0005
    low = minvis * (0.5 + 0.5 * np.clip((h0 + 0.01) / (0.01 + minvis), 0, 1))
    want = np.where(
        h0 >= KEEP,
        KEEP + 0.2 * np.clip(h0 - KEEP, 0, 0.005),
        np.where(h0 >= minvis, h0, low),
    )
    return want, vis


def clear(Y, A, shown, want, vis, lab=None, rigid=(), spread=(4, 0.6)):
    """Lift vertices of Y (in place) that are lower than `want` over one of the body shapes `shown`, straight away
    from the skin; the offsets are spread over the mesh neighbours (adjacency A) so no single vertex sticks out or
    dents in (spread: rings of neighbours, share passed on per ring). A rigid part moves as a whole.
    -> mask of the vertices that were too low."""
    hit = np.zeros(len(Y), bool)
    for _ in range(3):
        moved = False
        for s in shown:
            h, q, n, rim = height(Y, s)
            m = vis & ~rim & (h < want - 5e-5) & (np.linalg.norm(Y - q, axis=1) < 0.06)
            if not m.any():
                continue
            moved = True
            hit |= m
            O = np.zeros_like(Y)
            O[m] = n[m] * (want[m] - h[m])[:, None]
            req = O.copy()
            if A is not None:
                deg = np.maximum(np.asarray(A.sum(1)).ravel(), 1)[:, None]
                for _ in range(spread[0]):
                    S = spread[1] * (A @ O) / deg
                    keep = np.linalg.norm(req, axis=1) >= np.linalg.norm(S, axis=1)
                    O = np.where(keep[:, None], req, S)
                O[~vis] = 0
            for c in rigid:
                k = lab == c
                if m[k].any():
                    O[k] = req[k][np.linalg.norm(req[k], axis=1).argmax()]
            Y += O
        if not moved:
            break
    for s in list(shown) * 2:  # what the spread-out passes left too low: straight up, vertex by vertex
        h, q, n, rim = height(Y, s)
        m = vis & ~rim & (h < want - 5e-5) & (np.linalg.norm(Y - q, axis=1) < 0.06)
        for c in rigid:
            m[lab == c] = False
        Y[m] = q[m] + n[m] * want[m, None]
        hit |= m
    return hit


# ---------------- one LOD of one mesh ----------------


def chest_zone(Q, tgt, inner=0.09, outer=0.14):
    """How much of the chest zone each skin point Q of body tgt is in: 1 within `inner` m of a breast tip, falling
    smoothly to 0 at `outer` m."""
    tips = R.breast_tips(base(tgt))
    d = np.linalg.norm(Q[:, None] - tips[None], axis=2).min(1)
    t = np.clip((outer - d) / (outer - inner), 0, 1)
    return t * t * (3 - 2 * t)


def chest_room(Y, tgt, extra, shown=None, lab=None, rigid=(), A=None, E=None, weight=None, Fw=None):
    """'Adjust room at the chest' (Y in place): every vertex over the chest zone moves `extra` m further from the
    skin (negative: closer), straight along the direction of its closest skin point, fading out at the zone's
    border. Every layer over the same skin spot gets the same change, so their order and thickness stay; outside
    the zone nothing moves (the direction is evened out over the mesh, see below). Closer never means inside: over every shown shape a vertex stops at MINOWN (0.2 mm)
    plus a twentieth of what it had above that (so stacked layers keep their order down there too), and a vertex
    that was lower than that stays where it is. A rigid part moves as a whole (the mean change of its vertices)."""
    bt = base(tgt)
    h, q, n, rim = height(Y, bt)
    d = extra * chest_zone(q, tgt) * ~rim
    if extra < 0:
        for sh in shown or [tgt]:
            hs = h if sh == bt else height(Y, sh)[0]
            lo = np.minimum(hs, MINOWN) + 0.05 * np.maximum(hs - MINOWN, 0)
            d = np.minimum(0, np.maximum(d, lo - hs))
    if extra > 0:
        # between the breasts the skin normals of the two slopes point at each other: lifted along them, the two
        # sides of a garment would cross. Near the body's midline (within 3 cm, fading out to 6 cm) the sideways part
        # of the direction is dropped, so the garment moves forward out of the valley instead
        mid = np.clip((0.06 - np.abs(q[:, 0])) / 0.03, 0, 1)
        n = n.copy()
        n[:, 0] *= 1 - mid
        n = _unit(n)
    D = n * d[:, None]
    if A is not None:
        # between the breasts the closest skin is one slope for one vertex and the other slope for its neighbour:
        # the direction is evened out over the mesh neighbours (the amount per vertex stays)
        deg = np.maximum(np.asarray(A.sum(1)).ravel(), 1)[:, None]
        for _ in range(8):
            D = 0.5 * D + 0.5 * (A @ D) / deg
        D = _unit(D) * np.abs(d)[:, None]
        if extra > 0 and E is not None and len(E) and weight is not None:
            # looser: the lift is solved on the mesh. A vertex with one clear skin point under it (weight) takes
            # its lift; one that bridges a valley (the knot of a top between the breasts) follows its neighbours
            # instead of the nearest slope, so the lift folds nothing (twist top +6 mm: +179 turned triangles
            # before); outside the zone nothing moves
            D = (solve(Y, E, Y + D, weight, Y + D) - Y) * (d > 0)[:, None]
    if extra > 0 and A is not None and Fw is not None:
        # in a crease of the body (under the breasts) the skin normals of the two sides run together: where the
        # lift would turn a triangle over, it is halved around that triangle (3 times, then dropped there)
        n0 = np.cross(Y[Fw[:, 1]] - Y[Fw[:, 0]], Y[Fw[:, 2]] - Y[Fw[:, 0]])
        for r in range(10):
            Z = Y + D
            new = (n0 * np.cross(Z[Fw[:, 1]] - Z[Fw[:, 0]], Z[Fw[:, 2]] - Z[Fw[:, 0]])).sum(1) < 0
            if not new.any():
                break
            m = np.zeros(len(Y))
            m[Fw[new].ravel()] = 1
            m = np.maximum(m, (A @ m > 0).astype(float))
            D *= (1 - (0.5 if r < 3 else 1.0) * m)[:, None]
    for c in rigid:
        k = lab == c
        # the whole part moves by one vector; tighter: by the vertex that may move least (none ends below its minimum)
        D[k] = D[k].mean(0) if extra > 0 else D[k][np.abs(d[k]).argmin()]
    Y += D


def chest_pieces(Y, tgt, reach=0.10, size=0.12):
    """The small chest pieces of a mesh (pasties, nipple covers, tape): -> [vertex mask per breast side], or [] when
    the mesh is anything else. The WHOLE mesh must lie over the chest (the closest skin point of every vertex
    within `reach` m of a breast tip) and be small on each side (box diagonal under `size` m): a top or bra never
    qualifies, however small its separate parts are."""
    bt = base(tgt)
    tips = R.breast_tips(bt)
    q = height(Y, bt)[1]
    d = np.linalg.norm(q[:, None] - tips[None], axis=2)
    if not len(Y) or d.min(1).max() > reach:
        return []
    side = d.argmin(1)
    out = []
    for k in range(len(tips)):
        m = side == k
        if m.any():
            if np.linalg.norm(np.ptp(Y[m], axis=0)) > size:
                return []
            out.append(m)
    return out


def shift_pieces(Y, tgt, shift, pieces):
    """'Move pasties up / down' (Y in place): each small chest piece slides `shift` m up (negative: down) ALONG the
    skin. The skin point under its centre walks up the surface in 1 mm steps (along the surface direction that
    points up, back onto the skin after each step); the piece follows as a rigid body (moved to the new point,
    turned with the skin's normal); then every vertex gets back its own height over the skin, so the piece is as
    far from the skin as before and its layers keep their order. Both sides move the same amount."""
    bt = base(tgt)
    up = np.array([0.0, 1.0, 0.0])
    steps = int(round(abs(shift) / 0.001))
    for m in pieces:
        h = height(Y[m], bt)[0]
        b0 = bind(Y[m].mean(0)[None], bt)
        p, n0 = b0["q"][0], b0["n"][0]
        nrm = n0
        for _ in range(steps):
            t = up - (up @ nrm) * nrm
            ln = np.linalg.norm(t)
            if ln < 1e-6:
                break
            b = bind((p + np.sign(shift) * 0.001 * t / ln)[None], bt)
            p, nrm = b["q"][0], b["n"][0]
        Z = rotate(Y[m] - b0["q"][0], np.tile(n0, (int(m.sum()), 1)), np.tile(nrm, (int(m.sum()), 1))) + p
        _, q, n, _ = height(Z, bt)
        Y[m] = q + n * h[:, None]


def squashed(X, Y, dW, ns, nt, bs, shown, A, lab, rigid, Fw=None):
    """The squashed (GarmentSupport) shape of a refit mesh, made from its rest shape: X source rest positions, Y refit
    rest positions, dW the author's squash delta per vertex, ns / nt the skin normal under each vertex on the
    source / target body. Every vertex gets the author's delta, turned as the skin under it turned (a smooth field,
    unlike the per-vertex rotation of the garment's own edges: 2,376 instead of 574 triangles turned over between rest
    and squashed shape on the Shoulder twist top), so the squash is as smooth as the author made
    it and shares every decision of the rest shape: same welding, sliding, rigid parts). The game shows the squashed
    shape over the body with its GarmentSupport key applied; where the full delta would take a vertex the game
    showed below its least height over that body, the delta is shortened (the same share for its neighbours, so no
    vertex is left behind), and where even the rest shape is too low there, the vertex is lifted like in clear().
    -> squashed positions."""
    Xs = X + dW
    hs0 = height(Xs, bs)[0]
    hsv = height(Xs, R.gs_spec(bs))[0]
    relied = ((hsv > -0.0005) & (hs0 < -0.0002)).mean() > 0.002
    want, vis = floors(hs0, hsv, MINVIS if relied else MINOWN)
    d = rotate(dW, ns, nt)
    if A is not None:
        # where the refit made the garment smaller (edges shorter), the same delta would fold it: every edge of the
        # squashed shape is the refit rest edge plus the delta's difference along it, scaled by how much that edge
        # was stretched; one sparse solve, weakly held to rest + delta (twist top: 749 -> 638 turned triangles)
        E = np.array(sp.triu(A).nonzero()).T
        i, j = E[:, 0], E[:, 1]
        e0, e1 = X[j] - X[i], Y[j] - Y[i]
        k = np.clip(np.linalg.norm(e1, axis=1) / np.maximum(np.linalg.norm(e0, axis=1), 1e-9), 0.3, 3.0)[:, None]
        g = e1 + k * (d[j] - d[i])
        rhs = 0.05 * (Y + d)
        np.add.at(rhs, j, g)
        np.add.at(rhs, i, -g)
        lu = spl.splu((laplacian(len(X), E)[0] + 0.05 * sp.identity(len(X))).tocsc())
        d = np.column_stack([lu.solve(rhs[:, c]) for c in range(3)]) - Y
        d[np.abs(dW).sum(1) == 0] = 0  # a vertex the author did not squash stays on the rest shape
    moving = np.abs(dW).sum(1) > 0
    shapes = [R.gs_spec(sh) for sh in shown]
    t = np.ones(len(X))
    ok = lambda tt: np.all([~vis | ~moving | (height(Y + tt[:, None] * d, sh)[0] >= want - 5e-5) for sh in shapes], axis=0)
    bad = ~ok(t)
    if bad.any():
        lo, hi = np.zeros(len(X)), np.ones(len(X))
        for _ in range(6):
            mid = np.where(bad, 0.5 * (lo + hi), 1.0)
            good = ok(mid)
            lo, hi = np.where(bad & good, mid, lo), np.where(bad & ~good, mid, hi)
        t = np.where(bad, lo, 1.0)
        if A is not None:
            deg = np.maximum(np.asarray(A.sum(1)).ravel(), 1)
            for _ in range(3):
                t = np.minimum(t, (A @ t) / deg)
        for c in rigid:
            t[lab == c] = t[lab == c].min()
    if Fw is not None and A is not None:
        # the squash must not fold the garment more than the author's did: where a triangle turns over between rest
        # and squashed shape that did not in the original, the squash is halved around it (3 times, then dropped there)
        tn = lambda P0, P1: (
            np.cross(P0[Fw[:, 1]] - P0[Fw[:, 0]], P0[Fw[:, 2]] - P0[Fw[:, 0]])
            * np.cross(P1[Fw[:, 1]] - P1[Fw[:, 0]], P1[Fw[:, 2]] - P1[Fw[:, 0]])
        ).sum(1) < 0
        was = tn(X, Xs)
        for r in range(10):
            new = tn(Y, Y + t[:, None] * d) & ~was
            if not new.any():
                break
            m = np.zeros(len(X))
            m[Fw[new].ravel()] = 1
            m = np.maximum(m, (A @ m > 0).astype(float))
            t = t * (1 - (0.5 if r < 3 else 1.0) * m)
        for c in rigid:
            t[lab == c] = t[lab == c].min()
    Ys = Y + t[:, None] * d
    # also where nothing was squashed: the crushed body can be up to 1 cm higher than the one the rest shape fits
    # (Angel's lower breast), so the lift is spread wider than for the rest shape
    clear(Ys, A, shapes, want, vis, lab, rigid, SQ_SPREAD)
    return Ys


def fit(Ps, Fs, src, tgt, shown=None, src_shown=None, sig=None, extra=0.0, shift=0.0, deltas=None, slide_ok=None):
    """Refit the primitives of one LOD of one mesh. Ps: [positions], Fs: [triangles (m, 3) per primitive or None],
    src / tgt: body specs (the shapes the garment was made for / is made for), shown: target body shapes the
    garment must clear (default: tgt), src_shown: [per primitive: height of each vertex over the source body as
    the game showed it] or None (= over src), sig: [per primitive: skin weight signature rows] or None.
    extra: 'Adjust room at the chest' in m (chest_room; negative = tighter), 0 = none. shift: 'Move pasties up /
    down' in m (shift_pieces; only when the mesh is a small chest piece, chest_pieces), 0 = none.
    deltas: [per primitive: the GarmentSupport morph (squashed shape minus rest shape) or None]: the squashed shape
    is made from the rest result with the same decisions (squashed, below). slide_ok: None = decide here whether the
    mesh slides (slide + fold guard); True / False = as decided for another LOD of the same mesh.
    -> ([new positions], info: dict with 'lifted' (mask per primitive), 'rot' (rotation per vertex, per primitive),
    'rigid' (number of rigid parts), 'bound' (vertices placed by their own height), 'slid' (True, False, or
    'too big' when the sliding solve was skipped), 'squashed' ([squashed positions per primitive] or None))."""
    cut = np.cumsum([len(P) for P in Ps])[:-1]
    X0 = np.vstack(Ps)
    F = None
    if Fs and all(f is not None for f in Fs):
        F = np.vstack([f + o for f, o in zip(Fs, np.concatenate([[0], cut]))])
    X, inv, E = weld(X0, F)
    first = np.zeros(len(X), np.int64)
    first[inv[::-1]] = np.arange(len(X0))[::-1]
    bs, bt = base(src), base(tgt)
    b = bind(X, bs)
    h0 = b["h"]
    target, pt, nt = carry(X, bs, bt, b)
    hvis = h0 if src_shown is None else np.concatenate(src_shown)[first]
    # a garment with visible vertices inside the body as authored was made for a body that shrinks under it: on
    # a body that doesn't, everything below 1 mm is raised; any other garment keeps its author's heights
    relied = ((hvis > -0.0005) & (h0 < -0.0002)).mean() > 0.002
    want, vis = floors(h0, hvis, MINVIS if relied else MINOWN)
    # a vertex the game showed although it is inside the body as authored (the body was crushed under the garment)
    # goes straight to its least height over the target skin: never a negative height onto a visible surface
    up = vis & (h0 < want)
    target[up] = pt[up] + nt[up] * want[up, None]
    he = np.where(up, want, h0)  # the height the vertex is placed at
    weight = np.clip((FAR - np.abs(he)) / (FAR - NEAR), 0, 1) ** 2 * ~b["amb"]
    prior = field(X, bs, bt)
    # the carry is only a constraint where it really keeps the height (in a narrowing valley - cleavage, armpit,
    # crotch - a point carried along one slope's normal ends up near the other slope) ...
    tb = bind(target, bt)
    weight[(np.abs(tb["h"] - he) > 0.25 * np.abs(he) + 0.001) | tb["amb"]] = 0
    # ... and where it doesn't tear or fold the garment: an edge whose carried ends are much further apart than
    # before, or the other way round compared with the smooth field, loses both ends (and their neighbours)
    if len(E):
        i, j = E[:, 0], E[:, 1]
        e0, e1, ep = X[j] - X[i], target[j] - target[i], prior[j] - prior[i]
        l0, l1 = np.linalg.norm(e0, axis=1), np.linalg.norm(e1, axis=1)
        bad = (weight[i] > 0) & (weight[j] > 0) & ((l1 > 2.5 * l0 + 0.001) | ((e1 * ep).sum(1) < 0) | ((e1 * e0).sum(1) < 0))
        off = np.zeros(len(X), bool)
        off[i[bad]] = off[j[bad]] = True
        off[i[off[j]]] = True
        off[j[off[i]]] = True
        weight[off] = 0
    Y = solve(X, E, target, weight, prior)
    lab, rig = np.zeros(len(X), np.int64), []
    A = None
    if len(E):
        A = laplacian(len(X), E)[1]
        # sliding must not fold the garment: gathered cloth, ruffles and stacked layers can turn triangles over
        # when they slide. If it turns over more than 3 in 1,000 triangles that were fine before (at least 20), the
        # mesh keeps its anatomical placement. ponytail: all or nothing per mesh; a per-part decision if needed
        Fw = inv[F]
        turned = lambda Z: (np.cross(X[Fw[:, 1]] - X[Fw[:, 0]], X[Fw[:, 2]] - X[Fw[:, 0]]) * np.cross(Z[Fw[:, 1]] - Z[Fw[:, 0]], Z[Fw[:, 2]] - Z[Fw[:, 0]])).sum(1) < 0
        slid = False
        if slide_ok is not False:
            before, Ypre = turned(Y), Y.copy()
            if not slide(X, Y, E, he, weight, bt):
                slid = "too big" if len(X) > SLIDE_MAX else False
            else:  # the guard decides once per mesh (its first LOD); the other LODs follow that decision
                slid = slide_ok is True or int((turned(Y) & ~before).sum()) <= max(20, 3 * len(Fw) // 1000)
            if slid is not True:
                Y[:] = Ypre
        unfold(X, Y, inv[F], A, weight)
        lab, rig = rigid_parts(
            len(X), E, None if sig is None else np.vstack(sig)[first]
        )
        # a part with the same skin weights everywhere moves as one stiff piece in game. It is refit rigidly when
        # it is small (a button, a buckle) or when its soft fit is rigid anyway (a knife, a bag: nothing pulls it
        # out of shape). A bra hanging on one bone is neither: it must take the shape of the new chest.
        Ysoft, keep = Y.copy(), []
        for c in rig:
            k = lab == c
            Rm, t = kabsch(X[k], Y[k])
            Yr = X[k] @ Rm.T + t
            if np.linalg.norm(np.ptp(X[k], axis=0)) < RIGID_SIZE or np.sqrt(((Yr - Y[k]) ** 2).sum(1).mean()) < RIGID_RMS:
                Y[k] = Yr
                keep.append(c)
        rig = keep
    hit = clear(Y, A, shown or [tgt], want, vis, lab, rig)
    # ... and never at the price of clipping: a rigid part that still has visible vertices inside a shown shape
    # gets the soft fit after all
    back = []
    for c in rig:
        k = np.nonzero((lab == c) & vis)[0]
        if len(k) and any((height(Y[k], sh)[0] < -0.0001).any() for sh in shown or [tgt]):
            Y[lab == c] = Ysoft[lab == c]
            back.append(c)
    if back:
        rig = [c for c in rig if c not in back]
        hit |= clear(Y, A, shown or [tgt], want, vis, lab, rig)
    if len(E) and unfold(X, Y, inv[F], A, weight):  # the lift can fold a triangle too: iron out, lift again
        hit |= clear(Y, A, shown or [tgt], want, vis, lab, rig)
    if extra:
        chest_room(Y, bt, extra, shown or [tgt], lab, rig, A, E, weight, inv[F] if len(E) else None)
    pieces = chest_pieces(Y, bt) if shift else []
    if pieces:
        shift_pieces(Y, bt, shift, pieces)
        # the piece kept its height over the plain body; every other shown shape (the bare chest, the nipple
        # patch) must be cleared at its new place too
        hit |= clear(Y, A, shown or [tgt], want, vis, lab, rig)
    rot = rotations(X, Y, E)
    for c in rig:
        rot[lab == c] = kabsch(X[lab == c], Y[lab == c])[0]
    sq = None
    if deltas is not None and any(d is not None for d in deltas):
        dW = np.vstack([np.zeros_like(P) if d is None else d for P, d in zip(Ps, deltas)])[first]
        sq = np.split(squashed(X, Y, dW, b["n"], nt, bs, shown or [tgt], A, lab, rig, inv[F] if len(E) else None)[inv], cut)
    info = dict(
        lifted=np.split(hit[inv], cut),
        rot=np.split(rot[inv], cut),
        rigid=len(rig),
        bound=int((weight[inv] > 0.5).sum()),
        slid=bool(len(E)) and slid,
        shifted=bool(pieces),
        squashed=sq,
        weight=weight[inv],
        h0=np.split(h0[inv], cut),
    )
    return np.split(Y[inv], cut), info


# ---------------- 12. fit report ----------------


def report(Ps, Qs, Fs, h0s, shown):
    """What a refit did to one LOD (original positions, refit positions, triangles, original heights over the source
    skin, target body shapes shown): dict with
    inside: % of the vertices that were above the source skin and are now inside a shown target shape (> 0.1 mm),
    min / med: lowest and median clearance (mm) of the vertices within 3 cm of the skin (over the highest shape),
    stretch: the worst edge length ratio (edges of at least 1 mm), flips: triangles turned over,
    order: stacked vertex pairs over the same skin spot whose height order changed."""
    P, Q, h0 = np.vstack(Ps), np.vstack(Qs), np.concatenate(h0s)
    h = np.min([height(Q, s)[0] for s in shown], axis=0)
    was = h0 > 0.0001
    near = was & (h < 0.03)
    out = dict(
        inside=100.0 * (was & (h < -0.0001)).mean(),
        min=float(h[near].min() * 1e3) if near.any() else 0.0,
        med=float(np.median(h[near]) * 1e3) if near.any() else 0.0,
        stretch=1.0,
        flips=0,
        order=0,
    )
    if Fs and all(f is not None for f in Fs):
        off = np.cumsum([0] + [len(p) for p in Ps])[:-1]
        F = np.vstack([f + o for f, o in zip(Fs, off)])
        E = np.unique(np.sort(np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1), axis=0)
        l0 = np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)
        l1 = np.linalg.norm(Q[E[:, 0]] - Q[E[:, 1]], axis=1)
        k = l0 > 1e-3
        if k.any():
            out["stretch"] = float((l1[k] / l0[k]).max())
        n0 = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
        n1 = np.cross(Q[F[:, 1]] - Q[F[:, 0]], Q[F[:, 2]] - Q[F[:, 0]])
        out["flips"] = int(((n0 * n1).sum(1) < 0).sum())
    # stacked layers: pairs of vertices close together whose height order over the skin turned round
    # (each vertex with its 8 nearest within 2 mm: bounded memory also for dense hardware with thousands of
    # vertices in one spot)
    cl = np.nonzero(np.abs(h0) < 0.02)[0]
    if len(cl) > 9:
        d, nb = cKDTree(P[cl]).query(P[cl], k=9, distance_upper_bound=0.002)
        i = np.repeat(np.arange(len(cl)), 8)
        j = nb[:, 1:].ravel()
        k = (d[:, 1:].ravel() < np.inf) & (i < j)
        a_, b_ = cl[i[k]], cl[j[k]]
        d0, d1 = h0[a_] - h0[b_], h[a_] - h[b_]
        out["order"] = int(((np.abs(d0) > 0.0003) & (d0 * d1 < 0)).sum())
    return out


def report_text(r):
    return "fit: %.1f %% inside, clearance min %.1f / median %.1f mm, worst stretch %.1fx, %d triangles turned, %d layer flips, %d rigid part(s)" % (
        r["inside"], r["min"], r["med"], r["stretch"], r["flips"], r["order"], r.get("rigid", 0))
