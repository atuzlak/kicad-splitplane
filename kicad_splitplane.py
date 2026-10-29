#!/usr/bin/env python3
"""
kicad-splitplane - split a KiCad power layer into one continuous zone per net.

Every via / through-hole pad of the selected power nets is a seed. The tool
partitions the layer so that

  * every net gets exactly ONE continuous copper region (connected mode), and
  * each region contains all of its net's vias and none of the other nets',
  * borders lie as far as possible from foreign copper.

It reads a .kicad_pcb file (KiCad 7-10 format), writes the zones into a copy
of the board and renders a preview image. No KiCad installation is needed.
The same code also runs as a KiCad 9+ plugin (see plugin/).

CLI
---
  pip install "shapely>=2.0" numpy scipy matplotlib
  python kicad_splitplane.py board.kicad_pcb --layer In2.Cu --nets 5V 3.3V VBUS

Library
-------
  import kicad_splitplane as sp
  result = sp.process(open("board.kicad_pcb").read(), "In2.Cu", ["5V", "3.3V"])
  result.board_text   # modified board
  result.log          # list of messages / warnings
  sp.render_png(result, "preview.png")

Author: Aleksandar Tuzlak - MIT License
"""
from __future__ import annotations

import argparse
import itertools
import math
import re
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from shapely import contains_xy
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import nearest_points, polygonize, unary_union, voronoi_diagram

__version__ = "0.3.0"


# =============================================================================
# 1. Minimal S-expression parser (.kicad_pcb is one big S-expression)
# =============================================================================
_TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()"]+')


def parse_sexpr(text: str):
    stack, cur = [], []
    for m in _TOKEN.finditer(text):
        t = m.group(0)
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        else:
            cur.append(t[1:-1] if t.startswith('"') else t)
    return cur[0]


def children(node, name):
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def child(node, name):
    c = children(node, name)
    return c[0] if c else None


# =============================================================================
# 2. Board data
# =============================================================================
def net_table(board) -> dict:
    """Top-level (net <num> "<name>") entries (KiCad <= 9). Empty in KiCad 10."""
    return {n[1]: n[2] for n in children(board, "net") if len(n) >= 3}


def net_name(net_node, table):
    """Handles (net 3 "+5V"), (net 3) + lookup table, and KiCad 10's (net "+5V")."""
    if net_node is None or len(net_node) < 2:
        return None
    if len(net_node) >= 3:
        return net_node[2]
    return table.get(net_node[1], net_node[1])


def _pad_abs(fp_at, pad_at):
    """Absolute pad position. KiCad: y down, positive angle = CCW on screen."""
    fx, fy = float(fp_at[1]), float(fp_at[2])
    rot = math.radians(float(fp_at[3])) if len(fp_at) > 3 else 0.0
    px, py = float(pad_at[1]), float(pad_at[2])
    return (fx + px * math.cos(rot) + py * math.sin(rot),
            fy - px * math.sin(rot) + py * math.cos(rot))


def copper_layers(board) -> list[str]:
    lay = child(board, "layers")
    return [l[1] for l in lay[1:] if isinstance(l, list) and l[1].endswith(".Cu")] if lay else []


def collect_seeds(board, layer, nets, include_smd=False):
    """(x, y, net) for every via / THT pad of `nets` that reaches `layer`.
    include_smd=True also uses SMD pads as stand-ins for vias not yet placed."""
    table, seeds = net_table(board), []
    for v in children(board, "via"):
        if "micro" in v:
            continue
        name = net_name(child(v, "net"), table)
        lay = child(v, "layers")
        through = "blind" not in v
        if name in nets and (through or (lay and (layer in lay or "*.Cu" in lay))):
            at = child(v, "at")
            seeds.append((float(at[1]), float(at[2]), name))
    for fp in children(board, "footprint"):
        fp_at = child(fp, "at")
        for pad in children(fp, "pad"):
            if len(pad) < 3 or pad[2] not in ("thru_hole", "smd"):
                continue
            if pad[2] == "smd" and not include_smd:
                continue
            name = net_name(child(pad, "net"), table)
            if name in nets:
                seeds.append((*_pad_abs(fp_at, child(pad, "at")), name))
    return seeds


def list_power_candidates(text: str) -> list[tuple[str, int, int]]:
    """Nets that look like supplies: (name, #vias/THT pads, #SMD pads), sorted."""
    pat = re.compile(r"(^[+-]?\d+(\.\d+)?V\d*$)|(^[+-]?\d+V\d+$)|VCC|VDD|VBUS|VBAT|VIN|PWR|^V[A-Z]*$", re.I)
    return [t for t in list_nets(text) if pat.search(t[0])]


def list_nets(text: str) -> list[tuple[str, int, int]]:
    """All named nets with pads/vias: (name, #vias/THT pads, #SMD pads), sorted."""
    board = parse_sexpr(text)
    table = net_table(board)
    tht, smd = defaultdict(int), defaultdict(int)
    for v in children(board, "via"):
        tht[net_name(child(v, "net"), table)] += 1
    for fp in children(board, "footprint"):
        for pad in children(fp, "pad"):
            if len(pad) < 3:
                continue
            n = net_name(child(pad, "net"), table)
            if pad[2] == "thru_hole":
                tht[n] += 1
            elif pad[2] == "smd":
                smd[n] += 1
    names = {n for n in set(tht) | set(smd)
             if n and not n.lower().startswith(("net-", "unconnected"))}
    return sorted(((n, tht[n], smd[n]) for n in names), key=lambda t: -(t[1] + t[2]))


def board_outline(board):
    """Polygon from Edge.Cuts (lines, rects, polys, arcs approximated by 2 chords)."""
    def xy(n):
        return float(n[1]), float(n[2])
    segs = []
    for kind in ("gr_rect", "gr_poly", "gr_line", "gr_arc"):
        for g in children(board, kind):
            if (child(g, "layer") or [None, None])[1] != "Edge.Cuts":
                continue
            if kind == "gr_rect":
                (x0, y0), (x1, y1) = xy(child(g, "start")), xy(child(g, "end"))
                return box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            if kind == "gr_poly":
                return Polygon([xy(p) for p in children(child(g, "pts"), "xy")])
            if kind == "gr_line":
                segs.append(LineString([xy(child(g, "start")), xy(child(g, "end"))]))
            else:
                segs.append(LineString([xy(child(g, k)) for k in ("start", "mid", "end")]))
    polys = list(polygonize(unary_union(segs))) if segs else []
    if not polys:
        raise ValueError("Could not build a closed board outline from Edge.Cuts.")
    return max(polys, key=lambda p: p.area)


def _parts(geom):
    return [g for g in getattr(geom, "geoms", [geom]) if not g.is_empty]


# =============================================================================
# 3a. Plain Voronoi (fast, may create islands)
# =============================================================================
def voronoi_partition(seeds, outline, gap, edge, log):
    cells = voronoi_diagram(MultiPoint([(x, y) for x, y, _ in seeds]),
                            envelope=outline.buffer(50))
    by_net = defaultdict(list)
    for cell in cells.geoms:
        for x, y, net in seeds:
            if cell.contains(Point(x, y)):
                by_net[net].append(cell)
                break
    usable = outline.buffer(-edge)
    return {n: unary_union(cs).intersection(usable).buffer(-gap / 2, join_style=2)
            for n, cs in by_net.items()}


# =============================================================================
# 3b. Connected partition: exactly ONE continuous region per net
# =============================================================================
# Step 1 "spines": per net, join all its vias by a tree of 45-degree grid paths
#   (Prim + Dijkstra). Paths keep out of other nets' vias and of spines routed
#   earlier and prefer the middle of free space (cost ~ 1/distance).
# Step 2: split the board by nearest spine. A region grown from a connected
#   spine is itself connected (star-shaped w.r.t. the spine), so every net
#   ends up as exactly one zone.
# All routing orders of the nets are tried; the best (fewest trapped vias,
# then shortest spines) wins. A trapped via is topologically enclosed by other
# nets and has to be moved; a target position is suggested.
def connected_partition(seeds, outline, gap, edge, log, res=0.5, keepout=0.35,
                        max_orders=24, progress=None):
    usable = outline.buffer(-edge)
    x0, y0, x1, y1 = outline.bounds
    W, H = int(math.ceil((x1 - x0) / res)), int(math.ceil((y1 - y0) / res))
    cx = x0 + (np.arange(W) + 0.5) * res
    cy = y0 + (np.arange(H) + 0.5) * res
    XX, YY = np.meshgrid(cx, cy)
    inside = contains_xy(usable, XX, YY)

    def cell(x, y):
        return (min(H - 1, max(0, int((y - y0) / res))),
                min(W - 1, max(0, int((x - x0) / res))))

    nets = sorted({n for *_, n in seeds})
    seed_cells = {n: sorted({cell(x, y) for x, y, m in seeds if m == n}) for n in nets}
    block_r = (gap + keepout) / res
    idx = np.arange(H * W).reshape(H, W)
    steps = [(0, 1, 1.0), (1, 0, 1.0), (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2))]

    def route_net(net, blocked, cost):
        free = inside & ~blocked
        for r, c in seed_cells[net]:
            free[r, c] = True
        rows, cols, w = [], [], []
        for dr, dc, L in steps:
            a = idx[max(0, -dr):H - max(0, dr), max(0, -dc):W - max(0, dc)].ravel()
            b = idx[max(0, dr):, max(0, dc):][:H - abs(dr), :W - abs(dc)].ravel()
            ok = free.flat[a] & free.flat[b]
            a, b = a[ok], b[ok]
            rows.append(a)
            cols.append(b)
            w.append(L * (cost.flat[a] + cost.flat[b]) / 2)
        g = coo_matrix((np.concatenate(w), (np.concatenate(rows), np.concatenate(cols))),
                       shape=(H * W, H * W)).tocsr()
        targets = [r * W + c for r, c in seed_cells[net]]
        tree, todo, failed, total = {targets[0]}, set(targets[1:]), [], 0.0
        spine = np.zeros((H, W), bool)
        spine.flat[targets[0]] = True
        while todo:
            dist, pred, _ = dijkstra(g, directed=False, indices=list(tree),
                                     min_only=True, return_predecessors=True)
            reach = [t for t in todo if np.isfinite(dist[t])]
            if not reach:
                failed += list(todo)
                break
            t = min(reach, key=lambda k: dist[k])
            total += dist[t]
            while t >= 0 and t not in tree:
                tree.add(t)
                spine.flat[t] = True
                t = pred[t]
            todo -= tree
        return spine, failed, total

    def attempt(order):
        spines, failed, total = {}, [], 0.0
        for net in order:
            foreign = np.zeros((H, W), bool)
            for m in nets:
                if m != net:
                    for r, c in seed_cells[m]:
                        foreign[r, c] = True
            for sp in spines.values():
                foreign |= sp
            d = ndimage.distance_transform_edt(~foreign)
            sp, f, t = route_net(net, d <= block_r, 1.0 + 4.0 / np.maximum(d, 0.5))
            spines[net] = sp
            total += t
            failed += [(net, divmod(k, W)) for k in f]
        return spines, failed, total

    orders = list(itertools.permutations(nets))
    if len(orders) > max_orders:
        import random
        random.Random(0).shuffle(orders)
        orders = orders[:max_orders]
    best = None
    for i, order in enumerate(orders):
        if progress:
            progress(i, len(orders))
        r = attempt(order)
        key = (len(r[1]), r[2])
        if best is None or key < best[0]:
            best = (key, order, r)
        if key[0] == 0 and len(nets) > 4:
            break
    _, order, (spines, failed, _) = best
    log.append(f"routing order: {' -> '.join(order)}")

    srcs = np.zeros((H, W), bool)
    owner = np.full((H, W), -1)
    for i, n in enumerate(nets):
        srcs |= spines[n]
        owner[spines[n]] = i
    for n, (r, c) in failed:
        srcs[r, c] = True
        owner[r, c] = nets.index(n)
    _, (ir, ic) = ndimage.distance_transform_edt(~srcs, return_indices=True)
    label = owner[ir, ic]

    regions = {}
    for i, n in enumerate(nets):
        m = label == i
        boxes = []
        for r in range(H):
            run = np.flatnonzero(np.diff(np.r_[0, m[r].astype(np.int8), 0]))
            for a, b in zip(run[::2], run[1::2]):
                boxes.append(box(x0 + a * res, y0 + r * res, x0 + b * res, y0 + (r + 1) * res))
        poly = unary_union(boxes).simplify(res * 1.2)
        regions[n] = poly.intersection(usable).buffer(-gap / 2, join_style=2)

    for n, (r, c) in failed:
        p = Point(cx[c], cy[r])
        main = max(_parts(regions[n]), key=lambda q: q.area).buffer(-0.6)
        msg = f"! {n}: via at ({p.x:.2f}, {p.y:.2f}) is trapped by other nets"
        if not main.is_empty:
            q = nearest_points(main, p)[0]
            msg += f" -> move it to ~({q.x:.2f}, {q.y:.2f}) ({p.distance(q):.1f} mm fanout)"
        log.append(msg)
    return regions


# =============================================================================
# 4. Checks and output
# =============================================================================
def check(regions, seeds, min_neck, log):
    ok = True
    for net, r in sorted(regions.items()):
        parts = _parts(r)
        if len(parts) > 1:
            ok = False
            log.append(f"! {net}: split into {len(parts)} islands on this layer")
        if min_neck:
            opened = r.buffer(-min_neck / 2).buffer(min_neck / 2)
            if len(_parts(opened)) > len(parts):
                ok = False
                log.append(f"! {net}: has a neck narrower than {min_neck} mm")
    if ok:
        log.append("OK: one connected zone per net, no thin necks")
    return ok


def strip_old_zones(text, layer, nets):
    """Remove existing zones of `nets` on `layer` (returns new text, count)."""
    out, i, removed = [], 0, 0
    for m in re.finditer(r"\(zone\b", text):
        if m.start() < i:
            continue
        depth, j = 0, m.start()
        while True:
            ch = text[j]
            if ch == '"':
                j = text.index('"', j + 1)
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        z = parse_sexpr(text[m.start():j + 1])
        lay = child(z, "layer")
        if lay and lay[1] == layer and net_name(child(z, "net"), {}) in nets:
            out.append(text[i:m.start()])
            i = j + 1
            removed += 1
    out.append(text[i:])
    return "".join(out), removed


def _zone_sexpr(poly, net, layer, table, gap, prio):
    num = next((k for k, v in table.items() if v == net), None)
    net_tok = f'(net {num}) (net_name "{net}")' if num is not None else f'(net "{net}")'
    pts = " ".join(f"(xy {x:.4f} {y:.4f})" for x, y in list(poly.exterior.coords)[:-1])
    return (f'\t(zone {net_tok} (layer "{layer}") (uuid "{uuid.uuid4()}")\n'
            f'\t\t(name "splitplane_{net}") (priority {prio}) (hatch edge 0.5)\n'
            f'\t\t(connect_pads yes (clearance {gap / 2:.3f}))\n'
            f'\t\t(min_thickness 0.25) (filled_areas_thickness no)\n'
            f'\t\t(fill (thermal_gap 0.5) (thermal_bridge_width 0.5))\n'
            f'\t\t(polygon (pts {pts}))\n\t)\n')


def zone_parts(regions):
    """[(polygon, net, priority)] to write as zones. Holes are not written;
    smaller zones get higher priority so that an island enclosed by a bigger
    zone always wins the fill."""
    parts = [(p, n) for n, r in regions.items() for p in _parts(r) if p.area > 0.01]
    parts.sort(key=lambda t: t[0].area, reverse=True)
    return [(p.simplify(0.05), n, k) for k, (p, n) in enumerate(parts)]


def insert_zones(text, regions, layer, table, gap):
    zones = "".join(_zone_sexpr(p, n, layer, table, gap, k) for p, n, k in zone_parts(regions))
    end = text.rstrip().rfind(")")
    return text[:end] + zones + ")\n"


@dataclass
class Result:
    board_text: str
    regions: dict
    seeds: list
    outline: object
    layer: str
    ok: bool
    log: list = field(default_factory=list)


def compute(text, layer, nets, gap=0.5, edge=0.5, min_neck=1.0, res=0.5,
            include_smd=False, plain=False, progress=None) -> Result:
    """Partition only; result.board_text is None. Raises ValueError on unusable input."""
    log = []
    nets = list(dict.fromkeys(nets))
    if len(nets) < 2:
        raise ValueError("Select at least two nets.")
    board = parse_sexpr(text)
    if layer not in copper_layers(board):
        raise ValueError(f"Layer {layer} not in board ({', '.join(copper_layers(board))}).")
    seeds = collect_seeds(board, layer, set(nets), include_smd)
    counts = {n: sum(s[2] == n for s in seeds) for n in nets}
    log.append(f"{len(seeds)} seeds on {layer}: " + ", ".join(f"{n}={c}" for n, c in counts.items()))
    missing = [n for n, c in counts.items() if c == 0]
    if missing:
        hint = "" if include_smd else " (no vias yet? try 'use SMD pads as stand-in vias')"
        raise ValueError(f"No vias/THT pads found for: {', '.join(missing)}{hint}")
    outline = board_outline(board)
    if plain:
        regions = voronoi_partition(seeds, outline, gap, edge, log)
    else:
        regions = connected_partition(seeds, outline, gap, edge, log, res=res, progress=progress)
    ok = check(regions, seeds, min_neck, log)
    return Result(None, regions, seeds, outline, layer, ok, log)


def process(text, layer, nets, gap=0.5, edge=0.5, min_neck=1.0, res=0.5,
            include_smd=False, plain=False, keep_old=False, progress=None) -> Result:
    """Full pipeline on board text: compute() + write the zones into the text."""
    r = compute(text, layer, nets, gap, edge, min_neck, res, include_smd, plain, progress)
    if not keep_old:
        text, n_old = strip_old_zones(text, layer, set(nets))
        if n_old:
            r.log.append(f"removed {n_old} existing zone(s) of these nets on {layer}")
    r.board_text = insert_zones(text, r.regions, layer, net_table(parse_sexpr(text)), gap)
    return r


PALETTE = ["#2a6fdb", "#e8833a", "#3aa856", "#d64545", "#8a5cc7", "#c2a12b", "#2ab3b3", "#c25599"]


def render_png(result: Result, path_or_buffer, title=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 6))
    draw(result, ax, title)
    fig.tight_layout()
    fig.savefig(path_or_buffer, dpi=130, format="png")
    plt.close(fig)


def draw(result: Result, ax, title=None):
    """Plot zones and seeds onto a matplotlib Axes."""
    ax.plot(*result.outline.exterior.xy, color="#222", lw=1.5)
    for i, (net, r) in enumerate(sorted(result.regions.items())):
        c = PALETTE[i % len(PALETTE)]
        for k, p in enumerate(_parts(r)):
            ax.fill(*p.exterior.xy, color=c, alpha=0.45, lw=0, label=net if k == 0 else None)
            for hole in p.interiors:
                ax.fill(*hole.xy, color="white")
        pts = [(x, y) for x, y, n in result.seeds if n == net]
        ax.plot(*zip(*pts), "o", color=c, mec="#222", ms=4.5)
    ax.legend(loc="upper right", framealpha=0.9)
    ax.set_aspect("equal")
    ax.invert_yaxis()
    ax.set_title(title or f"{result.layer}: one zone per net")
    ax.set_xlabel("x [mm]")
    ax.set_ylabel("y [mm]")


# =============================================================================
# 5. CLI
# =============================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="kicad_splitplane",
        description="Split a KiCad power layer into one continuous zone per net.")
    ap.add_argument("board", help=".kicad_pcb file")
    ap.add_argument("--layer", default="In2.Cu", help="target copper layer (default In2.Cu)")
    ap.add_argument("--nets", nargs="+", help="power nets to place on the layer")
    ap.add_argument("--list-nets", action="store_true", help="show supply-like nets and exit")
    ap.add_argument("--gap", type=float, default=0.5, help="gap between zones [mm]")
    ap.add_argument("--edge", type=float, default=0.5, help="clearance to board edge [mm]")
    ap.add_argument("--min-neck", type=float, default=1.0, help="warn below this copper width [mm]")
    ap.add_argument("--res", type=float, default=0.5, help="grid resolution [mm]; try 0.3-0.6 if vias get trapped")
    ap.add_argument("--include-smd", action="store_true", help="use SMD pads as stand-in vias (preview before fanout)")
    ap.add_argument("--plain", action="store_true", help="skip connecting the vias (faster, may create islands)")
    ap.add_argument("--keep-old", action="store_true", help="keep existing zones of these nets on the layer")
    ap.add_argument("--out", help="output board (default: <board>_splitplane.kicad_pcb)")
    ap.add_argument("--version", action="version", version=__version__)
    a = ap.parse_args(argv)

    text = open(a.board, encoding="utf-8").read()
    if a.list_nets:
        print(f"{'net':<20} {'vias/THT':>9} {'SMD':>5}")
        for n, t, s in list_power_candidates(text):
            print(f"{n:<20} {t:>9} {s:>5}")
        return 0
    if not a.nets:
        ap.error("--nets is required (use --list-nets to see candidates)")
    try:
        r = process(text, a.layer, a.nets, a.gap, a.edge, a.min_neck, a.res,
                    a.include_smd, a.plain, a.keep_old,
                    progress=lambda i, n: print(f"  routing order {i + 1}/{n}", end="\r", file=sys.stderr))
    except ValueError as e:
        sys.exit(f"error: {e}")
    print(" " * 40, end="\r", file=sys.stderr)
    for line in r.log:
        print(("  " if line.startswith("!") else "") + line)
    out = a.out or re.sub(r"\.kicad_pcb$", "", a.board) + "_splitplane.kicad_pcb"
    with open(out, "w", encoding="utf-8") as f:
        f.write(r.board_text)
    png = re.sub(r"\.kicad_pcb$", "", out) + ".png"
    render_png(r, png)
    print(f"wrote {out}\nwrote {png}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.exit(main())
