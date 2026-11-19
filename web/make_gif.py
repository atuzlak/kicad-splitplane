#!/usr/bin/env python3
"""Render the demo animation web/img/demo.gif (README and web page).

    python web/make_gif.py [out.gif]

Shows the real steps of the algorithm on the demo board, recorded with the
`trace` hook of connected_partition():

  1. each net's vias are connected one net at a time: a Dijkstra search
     spreads from the vias already connected until it reaches the nearest
     unconnected via, and that path is added (a Prim tree per net);
  2. the layer is split by the nearest route, measured around obstacles;
  3. the borders are cleaned up (0/45/90 degree edges, gap between zones).
"""
import io
import os
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_rgb
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import kicad_splitplane as sp  # noqa: E402

NETS = ["+5V", "+3V3", "+1V8"]
SIZE, DPI = (8, 6.2), 100
COLORS = sp.net_colors(NETS)


def record(text):
    """Run the split as compute() does and collect the traced steps."""
    board = sp.parse_sexpr(text)
    seeds = sp.collect_seeds(board, "In2.Cu", set(NETS))
    outline = sp.board_outline(board)
    holes = sp.collect_holes(board, "In2.Cu")
    trace = {}
    sp.connected_partition(seeds, outline, 0.5, 0.5, [], res=0.5, holes=holes,
                           min_neck=1.0, pretty=True, trace=lambda k, d: trace.setdefault(k, d))
    return seeds, outline, holes, trace


class Scene:
    """Draws frames: board, optional grid overlay, routes, zones, captions."""

    def __init__(self, seeds, outline, holes, trace):
        self.seeds, self.outline, self.holes, self.t = seeds, outline, holes, trace
        x0, y0, res, H, W = trace["routing"]["grid"]
        self.grid = (x0, y0, res, H, W)
        self.extent = (x0, x0 + W * res, y0 + H * res, y0)
        self.cx = x0 + (np.arange(W) + 0.5) * res
        self.cy = y0 + (np.arange(H) + 0.5) * res
        self.frames, self.durations = [], []

    def cell(self, x, y):
        x0, y0, res, H, W = self.grid
        return min(H - 1, max(0, int((y - y0) / res))) * W + min(W - 1, max(0, int((x - x0) / res)))

    def xy(self, path):
        W = self.grid[4]
        return [self.cx[k % W] for k in path], [self.cy[k // W] for k in path]

    def overlay(self, ax, masks):
        """masks: [(bool array, colour, alpha)] painted on the grid."""
        _, _, _, H, W = self.grid
        img = np.zeros((H, W, 4))
        for m, color, alpha in masks:
            img[m] = (*to_rgb(color), alpha)
        ax.imshow(img, extent=self.extent, interpolation="nearest", zorder=1)

    def add(self, ms, title, caption, masks=(), routes=None, zones=None, connected=None,
            zone_alpha=0.5):
        fig, ax = plt.subplots(figsize=SIZE, dpi=DPI)
        if masks:
            self.overlay(ax, masks)
        for net, geom in (zones or {}).items():
            for p in sp._parts(geom):
                if p.area > 0.01:
                    ax.fill(*p.exterior.xy, color=COLORS[net], alpha=zone_alpha, lw=0, zorder=2)
        ax.plot(*self.outline.exterior.xy, color="#222", lw=1.5, zorder=3)
        for h in self.holes:
            if h.net not in NETS:
                ax.fill(*h.shape.exterior.xy, color="#999", lw=0, zorder=3)
        for net, paths in (routes or {}).items():
            for path in paths:
                ax.plot(*self.xy(path), color=COLORS[net], lw=3.2, solid_capstyle="round", zorder=4)
        for net in sorted(NETS):
            pts = [(x, y) for x, y, n in self.seeds if n == net]
            done = [p for p in pts if connected is None or self.cell(*p) in connected.get(net, ())]
            todo = [p for p in pts if p not in done]
            if done:
                ax.plot(*zip(*done), "o", color=COLORS[net], mec="#222", ms=6, zorder=5)
            if todo:
                ax.plot(*zip(*todo), "o", mfc="white", mec=COLORS[net], mew=2, ms=6, zorder=5)
        ax.set_xlim(self.extent[0] - 1, self.extent[1] + 1)
        ax.set_ylim(self.extent[2] + 1, self.extent[3] - 1)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        handles = [plt.Line2D([], [], marker="o", ls="", color=COLORS[n], mec="#222", ms=7, label=n)
                   for n in sorted(NETS)]
        ax.legend(handles=handles, loc="upper right", framealpha=0.95, fontsize=10)
        fig.text(0.04, 0.955, title, fontsize=15, fontweight="bold", va="top")
        fig.text(0.04, 0.115, "\n".join(textwrap.wrap(caption, 92)), fontsize=11, va="top", color="#333")
        fig.text(0.96, 0.02, "kicad-splitplane", ha="right", fontsize=9, color="#888")
        fig.subplots_adjust(left=0.02, right=0.98, top=0.88, bottom=0.13)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI)
        plt.close(fig)
        self.frames.append(Image.open(buf).convert("RGB"))
        self.durations.append(ms)


def build(scene):
    t = scene.t
    order = t["routing"]["order"]
    rec = t["routing"]["nets"]
    routes = {n: [] for n in NETS}
    connected = {n: set() for n in NETS}
    W = scene.grid[4]

    scene.add(3200, "Three supplies share one inner layer",
              "The +5V, +3V3 and +1V8 vias all reach In2.Cu. Each supply needs one zone that "
              "contains all of its vias, none of the others', and is one continuous piece.")

    for i, net in enumerate(order):
        data = rec[net]
        blocked = data["blocked"] & t["routing"]["inside"]
        connected[net].add(data["start"][0] * W + data["start"][1])
        title = f"Step 1: connect the vias of {net}"
        if i == 0:
            caption = (f"One net at a time. Grey: where {net} may not go (other nets' vias and "
                       "copper, with clearance). Hollow vias are not connected yet.")
        else:
            caption = ("Next net. The routes drawn before are obstacles now too (grey), so "
                       f"the routes of {net} cannot cross them.")
        scene.add(3000 if i == 0 else 2200, title, caption,
                  masks=[(blocked, "#777", 0.28)], routes=routes, connected=connected)

        slow = 2 if i == 0 else 1
        for k, step in enumerate(data["steps"]):
            path = step["path"]
            if k < slow:
                # The Dijkstra search spreading from the connected vias.
                dist = step["dist"]
                reach = float(dist.flat[step["target"]])
                n = 12 if i == 0 else 8
                for j in range(1, n + 1):
                    front = np.isfinite(dist) & (dist <= reach * j / n)
                    scene.add(130 if i == 0 else 100, title,
                              "Dijkstra search: it spreads out from the vias already connected, "
                              "costs more close to obstacles (so it prefers open space) and stops "
                              "at the nearest unconnected via.",
                              masks=[(blocked, "#777", 0.28), (front, COLORS[net], 0.33)],
                              routes=routes, connected=connected)
            routes[net].append(path)
            connected[net].update(path)
            if k < slow:
                scene.add(1700 if i == 0 else 1100, title,
                          "The cheapest path to that via is added. The next search starts from "
                          "everything connected so far (a Prim tree).",
                          masks=[(blocked, "#777", 0.28)], routes=routes, connected=connected)
            else:
                scene.add(450 if i == 0 else 320, title,
                          f"Repeat until every via of {net} is connected.",
                          masks=[(blocked, "#777", 0.28)], routes=routes, connected=connected)

    scene.add(2800, "Every supply is one connected tree",
              "Each supply's vias are joined by its own routes, and routes of different supplies "
              "never cross. These trees are the backbones of the zones.",
              routes=routes, connected=connected)

    split = t["split"]
    label, dist = split["label"], split["dist"]
    reachable = np.isfinite(dist)
    far = float(np.max(dist[reachable]))
    title = "Step 2: split the layer by nearest route"
    caption = ("Every spot of the layer goes to the closest route, measured around other nets' "
               "copper, not through it. Grown from one connected tree, each region is one piece.")
    masks = []
    for j in range(1, 17):
        grown = reachable & (dist <= far * (j / 16) ** 1.15) & (label >= 0)
        masks = [(grown & (label == split["nets"].index(n)), COLORS[n], 0.45) for n in NETS]
        scene.add(140, title, caption, masks=masks, routes=routes, connected=connected)
    scene.add(1800, title, caption, masks=masks, routes=routes, connected=connected)

    title = "Step 3: clean up the borders"
    scene.add(1900, title,
              "The split is made on a 0.5 mm grid, so its borders are staircases...",
              zones=t["tidy"]["raw"], routes=routes, connected=connected, zone_alpha=0.45)
    scene.add(2600, title,
              "...so they are straightened into 0°, 45° and 90° segments, with the gap between the "
              "zones and the clearance to the board edge. Pad rings stay intact.",
              zones=t["tidy"]["regions"], connected=connected)

    scene.add(4200, "Result: one continuous zone per supply",
              "Every via inside its own zone, no islands. The zones are written into the board; "
              "in KiCad press B to fill them and run DRC.",
              zones=t["tidy"]["regions"], connected=connected, zone_alpha=0.55)


def save(scene, path):
    frames = scene.frames
    # One palette from a few typical frames keeps colours steady and the file small.
    picks = [frames[0], frames[len(frames) // 3], frames[-10], frames[-1]]
    sample = Image.new("RGB", (frames[0].width, frames[0].height * len(picks)))
    for k, f in enumerate(picks):
        sample.paste(f, (0, k * frames[0].height))
    palette = sample.quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    gif[0].save(path, save_all=True, append_images=gif[1:], duration=scene.durations, loop=0,
                optimize=True, disposal=1)
    total = sum(scene.durations) / 1000
    print(f"wrote {path} ({os.path.getsize(path) // 1024} KiB, {len(gif)} frames, {total:.0f} s)")


def main(path=os.path.join(HERE, "img", "demo.gif")):
    text = open(os.path.join(ROOT, "examples", "demo_board.kicad_pcb"), encoding="utf-8").read()
    scene = Scene(*record(text))
    build(scene)
    save(scene, path)


if __name__ == "__main__":
    main(*sys.argv[1:])
