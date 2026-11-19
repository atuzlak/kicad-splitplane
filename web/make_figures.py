#!/usr/bin/env python3
"""Render the figures of the web page into web/img/.

    python web/make_figures.py
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path[:0] = [ROOT, os.path.join(ROOT, "tests")]
import kicad_splitplane as sp  # noqa: E402
import stress_board  # noqa: E402

IMG = os.path.join(HERE, "img")


def side_by_side(path, panels, size=(12, 4.8)):
    fig, axes = plt.subplots(1, len(panels), figsize=size)
    for ax, (result, title) in zip(axes, panels):
        sp.draw(result, ax, title)
        ax.legend(loc="upper right", fontsize=8, framealpha=0.9)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    print("wrote", os.path.relpath(path, ROOT))


def main():
    os.makedirs(IMG, exist_ok=True)
    demo = open(os.path.join(ROOT, "examples", "demo_board.kicad_pcb"), encoding="utf-8").read()
    nets = ["+5V", "+3V3", "+1V8"]
    side_by_side(os.path.join(IMG, "comparison.png"), [
        (sp.compute(demo, "In2.Cu", nets, plain=True), "Plain Voronoi: nets break into islands"),
        (sp.compute(demo, "In2.Cu", nets), "kicad-splitplane: one zone per net"),
    ])

    board = stress_board.board_text()
    side_by_side(os.path.join(IMG, "obstacles.png"), [
        (sp.compute(board, "In2.Cu", stress_board.NETS, smooth=False), "Raw grid outlines"),
        (sp.compute(board, "In2.Cu", stress_board.NETS), "Smoothed: 0°/45°/90° edges"),
    ])

    keepouts(demo, nets)


KEEPOUTS = """
	(zone (net 0) (net_name "") (layer "In2.Cu") (uuid "00000000-0000-0000-0000-0000000000a1")
		(name "keepout") (keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
		(polygon (pts (xy 100 88) (xy 123 88) (xy 123 91) (xy 100 91))))
	(zone (net 0) (net_name "") (layer "In2.Cu") (uuid "00000000-0000-0000-0000-0000000000a2")
		(name "antenna") (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) (copperpour not_allowed) (footprints allowed))
		(polygon (pts (xy 163 60) (xy 180 60) (xy 180 80) (xy 163 80))))
"""


def keepouts(demo, nets):
    """The demo board with two keepout areas, planned without and with --keepouts."""
    end = demo.rstrip().rfind(")")
    board = demo[:end] + KEEPOUTS + ")\n"
    without = sp.compute(board, "In2.Cu", nets)
    without.keepouts = sp.keepout_areas(sp.parse_sexpr(board), "In2.Cu")  # only drawn
    side_by_side(os.path.join(IMG, "keepouts.png"), [
        (without, "Without --keepouts: zones run through them"),
        (sp.compute(board, "In2.Cu", nets, keepouts=True), "With --keepouts: zones go around them"),
    ])


if __name__ == "__main__":
    main()
