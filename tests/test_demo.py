"""Run with:  python -m pytest tests/   (or simply: python tests/test_demo.py)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import kicad_splitplane as sp

DEMO = os.path.join(os.path.dirname(__file__), "..", "examples", "demo_board.kicad_pcb")
NETS = ["+5V", "+3V3", "+1V8"]


def _load():
    return open(DEMO, encoding="utf-8").read()


def test_one_zone_per_net():
    r = sp.process(_load(), "In2.Cu", NETS)
    assert r.ok, r.log
    for net in NETS:
        assert len(sp._parts(r.regions[net])) == 1


def test_no_foreign_vias_inside():
    r = sp.process(_load(), "In2.Cu", NETS)
    for net, region in r.regions.items():
        for x, y, n in r.seeds:
            if n != net:
                assert not region.contains(sp.Point(x, y))


def test_output_is_valid_sexpr_with_zones():
    r = sp.process(_load(), "In2.Cu", NETS)
    board = sp.parse_sexpr(r.board_text)
    names = [sp.child(z, "name")[1] for z in sp.children(board, "zone") if sp.child(z, "name")]
    assert sorted(names) == sorted(f"splitplane_{n}" for n in NETS)


def test_plain_mode_creates_islands_on_demo():
    r = sp.process(_load(), "In2.Cu", NETS, plain=True)
    assert any(len(sp._parts(g)) > 1 for g in r.regions.values())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok ", name)
