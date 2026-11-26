"""Self-test of the fit 2.0 geometry: one measurable check per fix, on garments made from the body shapes themselves
(no outfit and no WolvenKit needed). run(src cage, tgt cage) -> [(check, passed, numbers)]."""

import numpy as np

import fit2
import refit as R


def _part(spec, sub):
    """(positions, normals, triangles) of submesh `sub` of the full-body mesh of body spec."""
    P, N, F, _ = R.surface(spec)
    _, part, keys = R.surface_uv(spec)
    k = keys.index(("t0_000_pwa_base__full", sub))
    f = F[part[F[:, 0]] == k]
    ids, inv = np.unique(f, return_inverse=True)
    return P[ids], N[ids], inv.reshape(-1, 3)


def _stretch(P, Q, F):
    E = np.unique(
        np.sort(np.vstack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), 1), axis=0
    )
    l0 = np.linalg.norm(P[E[:, 0]] - P[E[:, 1]], axis=1)
    l1 = np.linalg.norm(Q[E[:, 0]] - Q[E[:, 1]], axis=1)
    return float((l1[l0 > 1e-3] / l0[l0 > 1e-3]).max())


def _flips(P, Q, F):
    n0 = np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]])
    n1 = np.cross(Q[F[:, 1]] - Q[F[:, 0]], Q[F[:, 2]] - Q[F[:, 0]])
    return int(((n0 * n1).sum(1) < 0).sum())


def run(src, tgt):
    out = []
    add = lambda name, ok, txt: out.append((name, bool(ok), txt))
    mm = 1000.0

    # 1. correspondence by UV: the mapped source body has no tears, no folds, and (almost) every vertex a match
    c = fit2.corr(src, tgt)
    P, _, F, _ = R.surface(src)
    Tm = c["T"]
    from scipy.spatial import cKDTree

    pr = cKDTree(P).query_pairs(1e-5, output_type="ndarray")
    gap = np.linalg.norm(Tm[pr[:, 0]] - Tm[pr[:, 1]], axis=1) * mm
    fl = _flips(P, Tm, F)
    hi = (
        P[:, 1] > 0.35
    )  # above the lower legs (vanilla's feet have their own UV layout: nearest-3D there)
    add(
        "body correspondence by UV",
        c["ok"][hi].mean() > 0.95 and (gap > 2).mean() < 0.01 and fl < 0.01 * len(F),
        "UV match %.1f %% above the lower legs, seam pairs mapped > 2 mm apart %d of %d, triangles turned %d of %d"
        % (100 * c["ok"][hi].mean(), (gap > 2).sum(), len(gap), fl, len(F)),
    )

    # 2. height-preserving fit: two skins 3.0 and 3.7 mm over the chest keep their heights and their order
    Pc, Nc, Fc = _part(src, 0)
    A, B = Pc + Nc * 0.003, Pc + Nc * 0.0037
    (Qa, Qb), info = fit2.fit([A, B], [Fc, Fc], src, tgt)
    ha, hb = fit2.height(Qa, tgt)[0] * mm, fit2.height(Qb, tgt)[0] * mm
    h0a = fit2.height(A, src)[0] * mm
    err = np.abs(ha - h0a)
    order = int((hb < ha).sum())
    add(
        "height kept, stacked layers in order",
        np.percentile(err, 95) < 0.5
        and order <= 0.02 * len(A)
        and (ha < 0).sum() == 0,
        "height error median %.2f / 95 %% %.2f mm, lower layer above upper at %d of %d vertices, inside %d"
        % (np.median(err), np.percentile(err, 95), order, len(A), (ha < 0).sum()),
    )
    add(
        "second skin (1 mm) stays at its height",
        True,
        "see next line",
    )
    out.pop()
    S = Pc + Nc * 0.001
    (Qs,), _ = fit2.fit([S], [Fc], src, tgt)
    hs = fit2.height(Qs, tgt)[0] * mm
    add(
        "second skin (1 mm) stays at its height",
        np.percentile(hs, 1) > 0.5 and abs(np.median(hs) - 1.0) < 0.2,
        "height over the target: 1 %% %.2f, median %.2f, 99 %% %.2f mm"
        % tuple(np.percentile(hs, [1, 50, 99])),
    )

    # 2b. sliding solve: the garment evens out its own stretch along the skin (a pattern keeps its look)
    def uneven(Q):
        e = lambda X: np.stack([np.linalg.norm(X[Fc[:, a]] - X[Fc[:, b]], axis=1) for a, b in ((0, 1), (1, 2), (2, 0))], 1)
        r = e(Q) / np.maximum(e(A), 1e-9)
        return 100 * (r.max(1) / np.maximum(r.min(1), 1e-9) > 1.3).mean()

    rounds, fit2.SLIDE_ROUNDS = fit2.SLIDE_ROUNDS, 0
    try:
        (Qn,), _ = fit2.fit([A], [Fc], src, tgt)
    finally:
        fit2.SLIDE_ROUNDS = rounds
    u0, u1 = uneven(Qn), uneven(Qa)
    add(
        "garment slides to even out its stretch",
        u1 < 0.8 * u0 + 0.5,
        "triangles stretched unevenly (one edge 1.3x more than another): %.1f %% locked to the skin, %.1f %% after sliding" % (u0, u1),
    )

    # 2c. 'Adjust room at the chest' -2 / +2 / +6 mm on the two skins (3.0 and 3.7 mm): the chest zone changes by
    #     that much, nothing moves outside it, the layers keep their order, no triangles turn over, nothing inside
    za = fit2.chest_zone(fit2.height(Qa, tgt)[1], tgt)
    full, out0 = za > 0.999, za == 0
    f0 = _flips(A, Qa, Fc)
    for val in (-2.0, 2.0, 6.0):
        (Xa, Xb), _ = fit2.fit([A, B], [Fc, Fc], src, tgt, extra=val / mm)
        hx = fit2.height(Xa, tgt)[0]
        rise = (hx - fit2.height(Qa, tgt)[0])[full] * mm
        outside = np.linalg.norm(Xa - Qa, axis=1)[out0].max() * mm if out0.any() else 0.0
        order2 = int((fit2.height(Xb, tgt)[0] < hx).sum())
        f1 = _flips(A, Xa, Fc)
        add(
            "adjust room at the chest (%+.0f mm)" % val,
            full.sum() > 20
            and abs(np.median(rise) - val) < 0.3
            and outside < 0.01
            and order2 <= order + 0.01 * len(A)  # up to 1 %: a skin pressed into a tight cleavage folds there when
            and f1 <= f0 + 0.01 * len(Fc)  # it is lifted 6 mm (Angel: 0.6 %)
            and (hx < 0).sum() == 0,
            "chest zone changes by %+.2f mm median (%d vertices; lowest height there %.2f mm), largest move outside the "
            "zone %.4f mm, lower layer above upper at %d vertices (without: %d), triangles turned %d (without: %d)"
            % (np.median(rise), full.sum(), hx[full].min() * mm, outside, order2, order, f1, f0),
        )
    # ... and tighter never means inside: a second skin at 1 mm with -2 mm stops at the hard minimum
    (Xs,), _ = fit2.fit([Pc + Nc * 0.001], [Fc], src, tgt, extra=-0.002)
    hs2 = fit2.height(Xs, tgt)[0] * mm
    add(
        "tighter stops at the hard minimum",
        hs2[full].min() > 0.15 and (hs2 < 0).sum() == 0 and np.median(hs2[full]) < 0.5,
        "second skin at 1 mm with -2 mm: heights in the chest zone min %.2f / median %.2f mm, inside %d"
        % (hs2[full].min(), np.median(hs2[full]), (hs2 < 0).sum()),
    )

    # 2d. 'Move pasties up / down': two small round patches 1 mm over the breast tips slide 10 mm up / down along
    #     the skin at their own height; a whole chest skin (a top) is left exactly as it is
    tips_s = R.breast_tips(src)
    near = np.linalg.norm(Pc[:, None] - tips_s[None], axis=2).min(1) < 0.025
    fk = Fc[near[Fc].all(1)]
    ids, inv2 = np.unique(fk, return_inverse=True)
    Pp, Fp = Pc[ids] + Nc[ids] * 0.001, inv2.reshape(-1, 3)
    (Q0,), _ = fit2.fit([Pp], [Fp], src, tgt)
    hq0 = fit2.height(Q0, tgt)[0]
    okp, txt = len(Pp) > 6, []
    for val in (10.0, -10.0):
        (Q1,), inf1 = fit2.fit([Pp], [Fp], src, tgt, shift=val / mm)
        hq1 = fit2.height(Q1, tgt)[0]
        for side in (Q0[:, 0] > 0, Q0[:, 0] < 0):
            if not side.any():
                continue
            mv = Q1[side].mean(0) - Q0[side].mean(0)
            dh = np.abs(hq1 - hq0)[side].max() * mm
            good = abs(np.linalg.norm(mv) * mm - 10.0) < 1.0 and mv[1] * val > 0 and dh < 0.2 and (hq1[side] < 0).sum() == 0
            okp = okp and good and inf1["shifted"]
            txt.append("%+.0f mm: centre moved %.1f mm (up %+.1f), height change max %.2f mm, inside %d" % (val, np.linalg.norm(mv) * mm, mv[1] * mm, dh, (hq1[side] < 0).sum()))
    (Qt,), inft = fit2.fit([A], [Fc], src, tgt, shift=0.010)
    (Qr,), _ = fit2.fit([A], [Fc], src, tgt)
    top = float(np.abs(Qt - Qr).max() * mm)
    add(
        "move pasties up / down",
        okp and top == 0.0 and not inft["shifted"],
        "; ".join(txt[:2] + txt[-1:]) + "; a whole chest skin with the option on: changed by %.4f mm" % top,
    )

    # 3. the body as shown: a skin 4 mm INSIDE the body as authored. Shown by the game (the body was crushed under
    #    it) -> it must come out above the target skin; hidden -> it stays inside
    I = Pc - Nc * 0.004
    hin = fit2.height(I, src)[0]
    deep = hin < -0.002
    (Qv,), _ = fit2.fit([I], [Fc], src, tgt, src_shown=[np.full(len(I), 0.005)])
    (Qh,), _ = fit2.fit([I], [Fc], src, tgt)
    hv, hh = fit2.height(Qv, tgt)[0] * mm, fit2.height(Qh, tgt)[0] * mm
    add(
        "negative height never onto a visible surface; hidden parts stay hidden",
        (hv[deep] > 0.3).mean() > 0.995 and (hh[deep] < 0).mean() > 0.98,
        "shown in game: %.1f %% above the target skin (min %.2f mm); hidden: %.1f %% still inside"
        % (100 * (hv[deep] > 0).mean(), hv[deep].min(), 100 * (hh[deep] < 0).mean()),
    )

    # 4. mesh-aware: a tube hanging from the hips (a skirt) keeps its shape - no pinch where the legs' displacement
    #    differs from the hips'
    Ph = R.surface(src)[0]
    ring = Ph[(Ph[:, 1] > 0.99) & (Ph[:, 1] < 1.01)]
    cx, cz = ring[:, 0].mean(), ring[:, 2].mean()
    rx, rz = (
        np.abs(ring[:, 0] - cx).max() + 0.006,
        np.abs(ring[:, 2] - cz).max() + 0.006,
    )
    n, rows = 64, 24
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ys = np.linspace(1.0, 0.55, rows)
    G = np.array(
        [
            [
                cx + rx * (1 + 0.5 * k / rows) * np.cos(a),
                y,
                cz + rz * (1 + 0.5 * k / rows) * np.sin(a),
            ]
            for k, y in enumerate(ys)
            for a in t
        ]
    )
    Fg = np.array(
        [
            tri
            for k in range(rows - 1)
            for i in range(n)
            for tri in (
                (k * n + i, k * n + (i + 1) % n, (k + 1) * n + i),
                (k * n + (i + 1) % n, (k + 1) * n + (i + 1) % n, (k + 1) * n + i),
            )
        ]
    )
    (Qg,), _ = fit2.fit([G], [Fg], src, tgt)
    st, flg = _stretch(G, Qg, Fg), _flips(G, Qg, Fg)
    hem0, hem1 = G[-n:], Qg[-n:]
    w = lambda X: np.ptp(X[:, 0]) * np.ptp(X[:, 2])
    add(
        "loose parts keep their shape (hanging tube)",
        st < 1.6 and flg == 0 and 0.85 < w(hem1) / w(hem0) < 1.3,
        "worst edge stretch %.2fx, triangles turned %d, hem outline %.0f %% of the original"
        % (st, flg, 100 * w(hem1) / w(hem0)),
    )

    # 5. rigid parts: a small stiff piece (same skin weights everywhere) on the chest keeps every distance
    tip = R.breast_tips(src)[0]
    q, nn = (
        R.skin_height(tip[None], src)[1][0],
        R.skin_height(tip[None] + [0, 0, -0.01], src)[2][0],
    )
    cube = (
        np.array(
            [[x, y, z] for x in (0, 0.012) for y in (0, 0.012) for z in (0, 0.004)]
        )
        + q
        + nn * 0.004
        + [0.02, 0.02, 0]
    )
    Fq = np.array(
        [
            [0, 1, 3],
            [0, 3, 2],
            [4, 5, 7],
            [4, 7, 6],
            [0, 1, 5],
            [0, 5, 4],
            [2, 3, 7],
            [2, 7, 6],
            [0, 2, 6],
            [0, 6, 4],
            [1, 3, 7],
            [1, 7, 5],
        ]
    )
    sig = np.tile([3.0, 0, 0, 0, 1.0, 0, 0, 0], (8, 1))
    (Qc,), inf = fit2.fit([cube], [Fq], src, tgt, sig=[sig])
    d0 = np.linalg.norm(cube[:, None] - cube[None], axis=2)
    d1 = np.linalg.norm(Qc[:, None] - Qc[None], axis=2)
    add(
        "rigid parts stay rigid",
        inf["rigid"] == 1 and np.abs(d1 - d0).max() < 1e-5,
        "%d rigid part, largest change of a distance inside it %.4f mm"
        % (inf["rigid"], np.abs(d1 - d0).max() * mm),
    )

    # 6. normals: the rotation given for each vertex turns the old surface normal onto the new one
    rot = info["rot"][0]

    def vnorm(X):
        fn = np.cross(X[Fc[:, 1]] - X[Fc[:, 0]], X[Fc[:, 2]] - X[Fc[:, 0]])
        vn = np.zeros_like(X)
        for k in range(3):
            np.add.at(vn, Fc[:, k], fn)
        return vn / np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-20)

    n0, n1 = vnorm(A), vnorm(Qa)
    ang = lambda a, b: np.degrees(np.arccos(np.clip((a * b).sum(1), -1, 1)))
    before, after = ang(n0, n1), ang(np.einsum("nij,nj->ni", rot, n0), n1)
    add(
        "normals turn with the surface",
        np.median(after) < 2.0
        and np.percentile(after, 95) < 0.5 * max(np.percentile(before, 95), 1e-6) + 3,
        "normal error without turning: median %.1f / 95 %% %.1f deg; with: median %.1f / 95 %% %.1f deg"
        % (
            np.median(before),
            np.percentile(before, 95),
            np.median(after),
            np.percentile(after, 95),
        ),
    )

    # 7. the rim of the body shape (neck): a point above it counts as above the skin, not inside
    top = P[P[:, 1] > P[:, 1].max() - 0.002].mean(0) + [0, 0.03, 0]
    h, _, _, rim = fit2.height(top[None], src)
    add(
        "open rim of the body (neck)",
        h[0] > 0 and rim[0],
        "height %.1f mm, on the rim: %s" % (h[0] * mm, bool(rim[0])),
    )

    # ---- checks that fail when one part of the engine is taken out (each was proven with that part removed) ----
    crushed = R.gs_spec(tgt)  # the target as it shows crushed: Angel's bare chest, up to 1 cm over the normal one
    two = [tgt, crushed]

    # 9. the correspondence itself: the same spot of the body carries the same bone weights (one skeleton for all
    #    bodies). Fails for a nearest-point mapping (chest: 0.2 instead of 0.02).
    try:
        na, Ja, Wa, _ = R.body_skin(src)
        nb, Jb, Wb, _ = R.body_skin(tgt)
        names = sorted(set(na) | set(nb))

        def dense(nm, J, W):
            col = np.array([names.index(x) for x in nm])
            D = np.zeros((len(J), len(names)))
            np.add.at(D, (np.repeat(np.arange(len(J)), J.shape[1]), col[J].ravel()), W.ravel())
            return D

        Wsrc, Wtgt = dense(na, Ja, Wa), dense(nb, Jb, Wb)
        chest = (P[:, 1] > 1.22) & (P[:, 1] < 1.45) & (P[:, 2] < 0) & (np.abs(P[:, 0]) < 0.2)
        fv, bn = R.skin_height(Tm[chest], tgt, faces=True)[3:]
        mis = np.abs((bn[..., None] * Wtgt[fv]).sum(1) - Wsrc[chest]).sum(1).mean()
        add(
            "correspondence keeps the bone weights (chest)",
            mis < 0.08,
            "mean difference of the bone weights between a chest vertex and the point it is sent to: %.3f (0 = same, 2 = "
            "nothing in common)" % mis,
        )
    except (KeyError, IndexError, ValueError) as e:
        add("correspondence keeps the bone weights (chest)", False, "no skin weights in the body shapes: %r" % e)

    # 9b. left stays left: where both body halves share their UVs (toes, fingers on the dense bodies) the nearer half
    #     must win; no vertex away from the middle may be sent to the other side
    side = np.abs(P[:, 0]) > 0.03
    swapped = int((side & (np.sign(Tm[:, 0]) != np.sign(P[:, 0])) & (np.abs(Tm[:, 0]) > 0.03)).sum())
    add("correspondence keeps left and right", swapped == 0, "body vertices sent to the other side: %d of %d" % (swapped, int(side.sum())))

    # 10. a garment bridging the cleavage: a band from breast to breast, 4 mm over the breasts and straight across the
    #     gap. Its carried points cross between the breasts; the valley rule, the mesh-aware solve and the unfold pass
    #     keep it from folding.
    front = (P[:, 1] > 1.15) & (P[:, 1] < 1.5) & (P[:, 2] < 0) & (np.abs(P[:, 0]) < 0.18)
    tree = cKDTree(P[front][:, :2])
    tx = float(np.abs(tips_s[:, 0]).mean())
    ty = float(tips_s[:, 1].mean())

    def band(gap, half, ruffle=0.0, nx=49, down=0.0):
        """A band from breast to breast: `gap` m over the breasts, straight across between them, `half` m up and
        `half + down` m down from the tips, with ruffles `ruffle` m high."""
        xs = np.linspace(-tx - 0.03, tx + 0.03, nx)
        ys = np.linspace(ty - half - down, ty + half, int(round((2 * half + down) / 0.0045)) + 1)
        zs = np.array([[P[front][tree.query([x, y])[1], 2] for x in xs] for y in ys])
        for r in range(len(ys)):  # across the gap: the straight line between the two breasts' most forward points
            il, ir = np.argmin(zs[r, : nx // 2]), nx // 2 + np.argmin(zs[r, nx // 2 :])
            zs[r, il : ir + 1] = np.linspace(zs[r, il], zs[r, ir], ir - il + 1)
        B_ = np.array(
            [[x, y, zs[r, c] - gap - ruffle * 0.5 * (1 + np.sin(x * 520) * np.sin(y * 520))] for r, y in enumerate(ys) for c, x in enumerate(xs)]
        )
        F_ = np.array(
            [t for r in range(len(ys) - 1) for c in range(nx - 1) for t in ((r * nx + c, r * nx + c + 1, (r + 1) * nx + c), (r * nx + c + 1, (r + 1) * nx + c + 1, (r + 1) * nx + c))]
        )
        return B_, F_

    Bd, Fb = band(0.004, 0.035)
    (Qbd,), _ = fit2.fit([Bd], [Fb], src, tgt)
    fb, sb = _flips(Bd, Qbd, Fb), _stretch(Bd, Qbd, Fb)
    add(
        "band across the cleavage does not fold",
        fb == 0 and sb < 2.5,
        "triangles turned %d of %d, worst edge stretch %.2fx, lowest height %.2f mm"
        % (fb, len(Fb), sb, fit2.height(Qbd, tgt)[0].min() * mm),
    )

    # 10b. the same band wider and with ruffles 4 mm high: carried point by point (no solve on the mesh) its
    #      ruffles fold
    Br, Fr = band(0.002, 0.06, 0.004)
    (Qbr,), _ = fit2.fit([Br], [Fr], src, tgt)
    fr = _flips(Br, Qbr, Fr)
    add(
        "ruffled band across the cleavage (mesh-aware solve)",
        fr <= 6,
        "triangles turned %d of %d, worst edge stretch %.2fx" % (fr, len(Fr), _stretch(Br, Qbr, Fr)),
    )
    # 10c. a fine band that also reaches under the bust (the crease): the unfold pass and the valley rule keep the
    #      turned triangles low
    Bu, Fu = band(0.003, 0.035, 0.0, 97, 0.07)
    (Qbu,), _ = fit2.fit([Bu], [Fu], src, tgt)
    fu = _flips(Bu, Qbu, Fu)
    add(
        "fine band down to under the bust (unfold, valley rule)",
        fu <= 0.013 * len(Fu),
        "triangles turned %d of %d (limit %d)" % (fu, len(Fu), int(0.013 * len(Fu))),
    )

    # 11. gathered cloth in two layers: ruffles 2 cm wide and 3 mm high, a second layer 1 mm above the first
    ruf = 0.003 + 0.0015 * (1 + np.sin(Pc[:, 0] * 314) * np.sin(Pc[:, 1] * 314))
    G1, G2 = Pc + Nc * ruf[:, None], Pc + Nc * (ruf + 0.001)[:, None]
    (Q1g, Q2g), _ = fit2.fit([G1, G2], [Fc, Fc], src, tgt)
    fg = _flips(G1, Q1g, Fc) + _flips(G2, Q2g, Fc)
    og = int((fit2.height(Q2g, tgt)[0] < fit2.height(Q1g, tgt)[0]).sum())
    add(
        "gathered cloth in two layers",
        fg <= 0.02 * len(Fc) and og <= 0.02 * len(G1) and (fit2.height(Q1g, tgt)[0] < 0).sum() == 0,
        "triangles turned %d of %d, lower layer above upper at %d of %d vertices, inside %d"
        % (fg, 2 * len(Fc), og, len(G1), (fit2.height(Q1g, tgt)[0] < 0).sum()),
    )

    # 12. two shown shapes (the normal chest and the crushed one): a 1 mm skin must end above both, to the last vertex
    (Qw,), _ = fit2.fit([S], [Fc], src, tgt, shown=two)
    lows = [fit2.height(Qw, sh)[0] * mm for sh in two]
    add(
        "every vertex above every shown shape",
        all(h.min() > 0.5 for h in lows),
        "1 mm skin with two shapes shown: lowest height %.2f mm over the normal body, %.2f mm over the crushed one"
        % (lows[0].min(), lows[1].min()),
    )

    # 13. a rigid part that must be lifted (a stiff block 0.5 mm over the lower breast, where the crushed chest is
    #     higher): it is lifted as a whole, not vertex by vertex
    low = R.skin_height((tips_s[0] + [0, -0.04, 0.0])[None], src)
    qb, nb_ = low[1][0], low[2][0]
    blk = np.array([[x, y, z] for x in (0, 0.012) for y in (0, 0.012) for z in (0, 0.004)])
    ex, ey = np.cross(nb_, [0, 1.0, 0]), None
    ex /= np.linalg.norm(ex)
    ey = np.cross(nb_, ex)
    Bk = qb + nb_ * 0.0005 + blk[:, [0]] * ex + blk[:, [1]] * ey + blk[:, [2]] * nb_
    (Qk,), infk = fit2.fit([Bk], [Fq], src, tgt, shown=two, sig=[sig])
    dk0 = np.linalg.norm(Bk[:, None] - Bk[None], axis=2)
    dk1 = np.linalg.norm(Qk[:, None] - Qk[None], axis=2)
    add(
        "a rigid part is lifted as a whole",
        infk["rigid"] == 1 and np.abs(dk1 - dk0).max() < 1e-5,
        "%d rigid part, largest change of a distance inside it %.4f mm, lowest height over the crushed body %.2f mm"
        % (infk["rigid"], np.abs(dk1 - dk0).max() * mm, fit2.height(Qk, crushed)[0].min() * mm),
    )

    # 14. the squashed (GarmentSupport) shape comes out of the rest shape's fit: a skin at 3 mm that its author
    #     squashes by up to 2.5 mm. The squashed shape may not fold against the rest shape, may not end inside the
    #     crushed body, and keeps most of the squash.
    dA = -Nc * (0.00125 * (1 + np.sin(Pc[:, 1] * 60)))[:, None]
    (Qr,), infs = fit2.fit([A], [Fc], src, tgt, deltas=[dA])
    Qs = infs["squashed"][0]
    f_o, f_n = _flips(A, A + dA, Fc), _flips(Qr, Qs, Fc)
    kept = np.median(np.linalg.norm(Qs - Qr, axis=1)) / np.median(np.linalg.norm(dA, axis=1))
    ins = int((fit2.height(Qs, crushed)[0] < -0.0001).sum())
    add(
        "squashed shape made with the rest shape",
        f_n <= f_o + 0.002 * len(Fc) and ins == 0 and kept > 0.4,
        "triangles turned between rest and squashed %d (author's: %d), squashed shape inside the crushed body %d, "
        "median squash %.0f %% of the author's" % (f_n, f_o, ins, 100 * kept),
    )

    # 15. a soft garment hanging on ONE bone (the same skin weights everywhere) is not a rigid part: it takes the
    #     shape of the new chest
    one = np.tile([3.0, 0, 0, 0, 1.0, 0, 0, 0], (len(A), 1))
    (Qo,), info1 = fit2.fit([A], [Fc], src, tgt, sig=[one])
    ho = fit2.height(Qo, tgt)[0] * mm
    add(
        "a soft garment on one bone is not rigid",
        info1["rigid"] == 0 and (ho < 0).sum() == 0 and np.percentile(np.abs(ho - h0a), 95) < 0.5,
        "%d rigid part(s), inside %d of %d, height error 95 %% %.2f mm"
        % (info1["rigid"], (ho < 0).sum(), len(A), np.percentile(np.abs(ho - h0a), 95)),
    )

    # 16. pasties moved 30 mm down with two shapes shown: above both at the new place
    (Qm,), infm = fit2.fit([Pp], [Fp], src, tgt, shown=two, shift=-0.030)
    lowm = [fit2.height(Qm, sh)[0].min() * mm for sh in two]
    add(
        "moved pasties clear every shown shape",
        infm["shifted"] and min(lowm) > 0.15,
        "moved 30 mm down: lowest height %.2f mm over the normal body, %.2f mm over the crushed one" % tuple(lowm),
    )

    # 8. fit report
    r = fit2.report([A, B], [Qa, Qb], [Fc, Fc], info["h0"], [tgt])
    add(
        "fit report",
        r["inside"] == 0
        and r["order"] <= 0.02 * len(A)
        and r["flips"] < 0.01 * len(Fc),
        fit2.report_text(dict(r, rigid=0)),
    )
    return out
