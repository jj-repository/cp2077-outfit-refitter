r"""Cyberpunk 2077 .archive (RDAR v12) and CR2W buffer helpers, plus the big-buffer safety check.

Oodle: the game's own oo2ext_7_win64.dll is loaded from the user's game folder (init_oodle). It is never shipped.

RDAR v12 layout (as WolvenKit writes it):
  0   header 40 B: 'RDAR', u32 version=12, u64 indexPos, u32 indexSize, u64 debugPos, u32 debugSize, u64 fileSize
  40  u32 customDataLength, zero pad to 0xAC
  0xAC LXRS block (file-name list): 'LXRS', u32 ver, i32 size, i32 zsize, i32 count, Kraken data
  indexPos: u32 8, u32 indexSize-8, u64 crc64/xz(index[16:]), u32 nFiles, u32 nSegs, u32 nDeps,
      files 56 B (u64 hash, u64 timestamp, u32 nInline, u32 segStart, u32 segEnd, u32 depStart, u32 depEnd, sha1),
      segments 16 B (u64 offset, u32 zsize, u32 size), deps 8 B.
A wrong index CRC crashes the game at startup, so every output is checked (Rdar.crc_ok).
"""

import ctypes, os, re, struct

_dec = _cmp = None


def init_oodle(game_dir):
    """Load the game's Oodle DLL (decompress + compress) once."""
    global _dec, _cmp
    if _dec:
        return
    dll = ctypes.WinDLL(os.path.join(game_dir, "bin", "x64", "oo2ext_7_win64.dll"))
    _dec = dll.OodleLZ_Decompress
    _dec.restype = ctypes.c_int64
    _dec.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_int64,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int64,
        ctypes.c_int,
    ]
    _cmp = dll.OodleLZ_Compress
    _cmp.restype = ctypes.c_int64
    _cmp.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int64,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int64,
    ]


def oodle_dec(comp, size):
    out = ctypes.create_string_buffer(size + 64)
    n = _dec(comp, len(comp), out, size, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != size:
        raise RuntimeError("oodle decompress failed (%d != %d)" % (n, size))
    return out.raw[:size]


def oodle_kraken(raw):
    out = ctypes.create_string_buffer(len(raw) + 65536)
    n = _cmp(
        8, raw, len(raw), out, 4, None, None, None, None, 0
    )  # 8 = Kraken, level 4 = Normal
    if n <= 0:
        raise RuntimeError("oodle compress failed")
    return out.raw[:n]


def seg_raw(seg, size):
    """Decompressed bytes of one stored segment (raw / KARK-wrapped / bare Kraken)."""
    if len(seg) == size:
        return seg
    if seg[:4] == b"KARK":
        return oodle_dec(seg[8:], struct.unpack_from("<I", seg, 4)[0])
    return oodle_dec(seg, size)


def game_ok(comp, msz):
    try:
        seg_raw(comp, msz)
        return True
    except RuntimeError:
        return False


_T = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xC96C5795D7870F42 if _c & 1 else _c >> 1
    _T.append(_c)


def crc64(b):  # CRC-64/XZ, what the archive index stores
    c = 0xFFFFFFFFFFFFFFFF
    for x in b:
        c = _T[(c ^ x) & 0xFF] ^ (c >> 8)
    return c ^ 0xFFFFFFFFFFFFFFFF


_LOW = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def low(s):
    """Lower case as the game hashes depot paths: A-Z only, every other character as it is."""
    return s.translate(_LOW)


def find_file(archives, path):
    """The CR2W objects part of depot file `path` from the first of the archives that holds it -> (bytes, archive),
    or (None, None). An archive that can't be read is passed over."""
    h = fnv(path)
    for a in archives:
        try:
            r = Rdar(a)
            for f in r.files:
                if f[0] == h:
                    off, zs, size = r.segs[f[3]]
                    with open(a, "rb") as fh:
                        fh.seek(off)
                        return seg_raw(fh.read(zs), size), a
        except (OSError, ValueError, RuntimeError, IndexError, struct.error):
            continue
    return None, None


def fnv(s):
    h = 0xCBF29CE484222325
    for b in low(s).replace("/", "\\").encode():
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


class Rdar:
    """Header, file names and index of one archive; data is read lazily."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            h = f.read(0xAC)
            if h[:4] != b"RDAR":
                raise ValueError("not a Cyberpunk archive: " + path)
            self.version, self.ipos, self.isz, _, _, self.fsz = struct.unpack_from(
                "<IQIQIQ", h, 4
            )
            cust_len = struct.unpack_from("<I", h, 40)[0]
            self.cust = f.read(cust_len) if cust_len else b""
            f.seek(self.ipos)
            idx = f.read(self.isz)
        self.crc = struct.unpack_from("<Q", idx, 8)[0]
        nf, ns, nd = struct.unpack_from("<III", idx, 16)
        self.files = [
            struct.unpack_from("<QQIIIII20s", idx, 28 + i * 56) for i in range(nf)
        ]
        sb = 28 + nf * 56
        self.segs = [struct.unpack_from("<QII", idx, sb + j * 16) for j in range(ns)]
        self.crc_ok = crc64(idx[16:]) == self.crc
        self._names = None

    @property
    def names(self):  # needs Oodle when the name list is compressed
        if self._names is None:
            c = self.cust
            if not c or c[:4] != b"SRXL":
                self._names = []
            else:
                ver, size, zsize, count = struct.unpack_from("<Iiii", c, 4)
                body = c[20 : 20 + zsize]
                raw = body if zsize == size else oodle_dec(body, size)
                self._names = [
                    n.decode("utf-8", "replace") for n in raw.split(b"\0")[:count]
                ]
        return self._names

    def hashes(self):
        return {f[0] for f in self.files}


# ---- CR2W buffers and the big-buffer check (WolvenKit 9.1.1 can write big buffers the game can't decode) ----

ZRUN = re.compile(rb"\x00{4096,}")


def buf_table(head):
    """CR2W buffer table entries [flags, index, offset, diskSize, memSize, crc]."""
    assert head[:4] == b"CR2W", "not CR2W"
    toff, tcnt, _ = struct.unpack_from("<III", head, 40 + 5 * 12)
    return [
        list(struct.unpack_from("<IIIIII", head, toff + i * 24)) for i in range(tcnt)
    ]


def cr2w_bufs(d):
    oend = struct.unpack_from("<I", d, 24)[0]
    toff = struct.unpack_from("<I", d, 40 + 5 * 12)[0]
    return oend, toff, buf_table(d)


def zero_runs(b):
    """(longest zero run, trailing zero run), counting runs >= 4096 only."""
    longest = max((m.end() - m.start() for m in ZRUN.finditer(b)), default=0)
    tail = len(b) - len(b.rstrip(b"\0"))
    return longest, tail if tail >= 4096 else 0


def stats(head, segs):
    """segs: [(stored bytes, size)] -> [(memSize, size, decoded_ok, longest0, tail0)] per buffer."""
    out = []
    tab = buf_table(head)
    for i, (b, size) in enumerate(segs):
        msz = tab[i][4] if i < len(tab) else -1
        try:
            raw = seg_raw(b, size)
            ok = len(raw) == size
        except RuntimeError:
            raw, ok = b"", False
        lz, tz = zero_runs(raw) if ok else (0, 0)
        out.append((msz, size, ok, lz, tz))
    return out


def flagged(st, sst):
    """Buffer indexes the game can't decode, or that end in >= 16 KB of zeros the source buffer doesn't."""
    bad = []
    for i, (msz, size, ok, lz, tz) in enumerate(st):
        s = sst[i] if sst and i < len(sst) else None
        if not ok or (16384 <= tz < size and not (s and s[4] * 2 >= tz)):
            bad.append(i)
    return bad


def archive_meshes(path):
    """yield (name, header bytes or None, [(stored, size)]) for every .mesh in an archive."""
    r = Rdar(path)
    names = {fnv(n): n for n in r.names}
    with open(path, "rb") as f:
        for h, ts, ninl, s0, s1, d0, d1, sha in r.files:
            n = names.get(h, "%016x" % h)
            if not n.lower().endswith(".mesh"):
                continue
            segs = []
            for off, zs, size in r.segs[s0:s1]:
                f.seek(off)
                segs.append((f.read(zs), size))
            try:
                head = seg_raw(*segs[0])
            except RuntimeError:
                yield n, None, segs
                continue
            if head[:4] == b"CR2W":
                yield n, head, segs[1:]


def loose_mesh(path):
    d = open(path, "rb").read()
    oend = struct.unpack_from("<I", d, 24)[0]
    return d[:oend], [(d[e[2] : e[2] + e[3]], e[4]) for e in buf_table(d)]


def bad_buffers(head, segs, src_mesh):
    """Buffers of a refit mesh that are broken compared with its source mesh file."""
    return flagged(stats(head, segs), stats(*loose_mesh(src_mesh)))
