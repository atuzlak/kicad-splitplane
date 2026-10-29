"""Plugin logic without a running KiCad (needs: pip install kicad-python)."""
import os, sys
import pytest

kipy = pytest.importorskip("kipy")
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path[:0] = [ROOT, os.path.join(ROOT, "plugin")]
import kicad_splitplane as sp
import splitplane_action as act
from kipy.board_types import Net, Zone
from kipy.proto.board.board_types_pb2 import BoardLayer, ZoneConnectionStyle
from kipy.util.units import to_mm

DEMO = os.path.join(ROOT, "examples", "demo_board.kicad_pcb")
NETS = ["+5V", "+3V3", "+1V8"]


@pytest.fixture(scope="module")
def result():
    return sp.compute(open(DEMO, encoding="utf-8").read(), "In2.Cu", NETS)


class FakeBoard:
    """Records the API calls the plugin makes."""
    def __init__(self, zones=()):
        self.zones, self.calls = list(zones), []

    def get_zones(self):
        return self.zones

    def get_nets(self):
        return [Net(name=n) for n in NETS + ["GND"]]

    def begin_commit(self):
        self.calls.append("begin")
        return "commit"

    def push_commit(self, commit, message=""):
        self.calls.append(("push", message))

    def drop_commit(self, commit):
        self.calls.append("drop")

    def remove_items(self, items):
        self.calls.append(("remove", list(items)))

    def create_items(self, items):
        self.calls.append(("create", list(items)))

    def refill_zones(self):
        self.calls.append("refill")


def _zone(net, layers):
    z = Zone()
    z.net = Net(name=net)
    z.layers = layers
    return z


def test_zones_match_cli_output(result):
    zones = act.make_zones(result, gap=0.5)
    parts = sp.zone_parts(result.regions)
    assert len(zones) == len(parts) == len(NETS)
    for z, (poly, net, prio) in zip(zones, parts):
        assert z.net.name == net and z.name == f"splitplane_{net}" and z.priority == prio
        assert list(z.layers) == [BoardLayer.BL_In2_Cu]
        assert to_mm(z.clearance) == pytest.approx(0.25)
        assert z.connection.zone_connection == ZoneConnectionStyle.ZCS_FULL
        pts = [c for n in z.outline.outline.nodes for c in (to_mm(n.point.x), to_mm(n.point.y))]
        assert pts == pytest.approx([c for xy in list(poly.exterior.coords)[:-1] for c in xy], abs=1e-5)


def test_apply_replaces_old_zones_in_one_commit(result):
    stale = _zone("+5V", [BoardLayer.BL_In2_Cu])
    keep = [_zone("+5V", [BoardLayer.BL_In1_Cu]),                         # other layer
            _zone("GND", [BoardLayer.BL_In2_Cu]),                         # other net
            _zone("+5V", [BoardLayer.BL_In1_Cu, BoardLayer.BL_In2_Cu])]   # multi-layer
    board = FakeBoard([stale] + keep)
    act.apply(board, result, gap=0.5)
    assert board.calls[0] == "begin"
    assert board.calls[1] == ("remove", [stale])
    assert board.calls[2][0] == "create" and len(board.calls[2][1]) == len(NETS)
    assert board.calls[3] == ("push", act.UNDO_MESSAGE)
    assert board.calls[4] == "refill"


def test_apply_keep_old_and_no_fill(result):
    board = FakeBoard([_zone("+5V", [BoardLayer.BL_In2_Cu])])
    act.apply(board, result, gap=0.5, keep_old=True, fill=False)
    assert [c if isinstance(c, str) else c[0] for c in board.calls] == ["begin", "create", "push"]


def test_failed_write_drops_commit(result):
    class Broken(FakeBoard):
        def create_items(self, items):
            raise RuntimeError("API error")
    board = Broken()
    with pytest.raises(RuntimeError):
        act.apply(board, result, gap=0.5)
    assert board.calls[-1] == "drop"
