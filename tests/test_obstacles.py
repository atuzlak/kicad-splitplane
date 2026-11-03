"""Pads and vias with their real size, and other nets' copper as obstacles."""
import os, sys
HERE = os.path.dirname(__file__)
sys.path[:0] = [os.path.join(HERE, ".."), HERE]
import kicad_splitplane as sp
import stress_board as sb
from shapely.geometry import Point, box

LAYER = "In2.Cu"
# A VDDA via right next to the 3.3V pin of the header (the case from the course project).
CLOSE = sb.board_text().replace("(via (at 12 26)", "(via (at 6.2 31.9)")


def test_holes_have_real_shapes():
    holes = sp.collect_holes(sp.parse_sexpr(sb.board_text()), LAYER)
    pin1 = next(h for h in holes if h.net == "3.3V" and abs(h.x - 8) < 1e-6 and abs(h.y - 30) < 1e-6)
    assert abs(pin1.shape.area - 1.7 * 1.7) < 1e-6           # rect pad, not a point
    assert sum(h.net and h.net.startswith("SIG") for h in holes) == 23


def test_stress_board_is_clean():
    r = sp.compute(sb.board_text(), LAYER, sb.NETS)
    assert r.ok, r.log


def test_no_zone_runs_over_another_supplys_pad():
    r = sp.compute(CLOSE, LAYER, sb.NETS)
    assert r.ok, r.log
    for net, region in r.regions.items():
        for h in r.holes:
            if h.net in r.regions and h.net != net:
                assert not region.intersects(h.shape), (net, h)


def test_header_row_does_not_split_the_copper():
    r = sp.compute(sb.board_text(), LAYER, sb.NETS)
    for net, region in r.regions.items():
        assert len(sp.simulate_fill(region, net, r.holes, 0.25)) == 1, net


def test_check_reports_zone_over_foreign_pad():
    holes = [sp.Hole(0, 0, Point(0, 0).buffer(0.85), "B"), sp.Hole(7, 7, Point(7, 7).buffer(0.3), "A")]
    regions = {"A": box(-5, -5, 9, 9).difference(box(5.5, 5.5, 10, 10)).union(box(6, 6, 9, 9)),
               "B": box(10, 10, 12, 12)}
    log = []
    assert not sp.check(regions, [(7, 7, "A"), (0, 0, "B")], 1.0, log, holes)
    assert any("runs over a B pad" in line for line in log), log
