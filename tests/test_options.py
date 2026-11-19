"""Opt-in options: --rules (design rules), --keepouts, --report.
Without them the output must not change (see also test_agents.py)."""
import json, os, shutil, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
from shapely.geometry import Point, box
import kicad_splitplane as sp

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEMO = os.path.join(ROOT, "examples", "demo_board.kicad_pcb")
NETS = ["+5V", "+3V3", "+1V8"]


def _keepout(layers, pts, pour="not_allowed", name="ko"):
    xy = " ".join(f"(xy {x} {y})" for x, y in pts)
    return (f'\t(zone (net 0) (net_name "") {layers} (uuid "00000000-0000-0000-0000-000000000001")'
            f' (name "{name}") (hatch edge 0.5)\n'
            f'\t\t(keepout (tracks allowed) (vias allowed) (pads allowed) (copperpour {pour})'
            f' (footprints allowed))\n'
            f'\t\t(polygon (pts {xy}))\n\t)\n')


def _with(text, extra):
    end = text.rstrip().rfind(")")
    return text[:end] + extra + ")\n"


STRIP = [(100, 88), (123, 88), (123, 91), (100, 91)]   # cuts the +5V zone of the demo


@pytest.fixture
def demo_text():
    return open(DEMO, encoding="utf-8").read()


def _cli(*args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "kicad_splitplane.py"), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


# --- keepouts ------------------------------------------------------------------

def test_keepout_areas_filter_layer_and_kind(demo_text):
    text = _with(demo_text,
                 _keepout('(layer "In2.Cu")', [(163, 60), (180, 60), (180, 82), (163, 82)])
                 + _keepout('(layers "F.Cu" "In1.Cu" "In2.Cu" "B.Cu")', [(100, 103), (112, 103), (112, 115), (100, 115)])
                 + _keepout('(layers "*.Cu")', [(140, 60), (142, 60), (142, 62)])
                 + _keepout('(layer "In1.Cu")', [(126, 88), (146, 88), (146, 98)])           # other layer
                 + _keepout('(layer "In2.Cu")', [(126, 88), (146, 88), (146, 98)], "allowed"))  # pours allowed
    areas = sp.keepout_areas(sp.parse_sexpr(text), "In2.Cu")
    assert len(areas) == 3
    assert sp.keepout_areas(sp.parse_sexpr(text), "F.Cu") and len(sp.keepout_areas(sp.parse_sexpr(text), "In1.Cu")) == 3


def test_keepout_hole_and_footprint_zone():
    board = sp.parse_sexpr("""(kicad_pcb
      (zone (net 0) (layer "In2.Cu") (keepout (copperpour not_allowed))
        (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
        (polygon (pts (xy 4 4) (xy 6 4) (xy 6 6) (xy 4 6))))
      (footprint "x" (at 50 50)
        (zone (net 0) (layers "F&B.Cu") (keepout (copperpour not_allowed))
          (polygon (pts (xy 48 48) (xy 52 48) (xy 52 52) (xy 48 52))))))""")
    (a,) = sp.keepout_areas(board, "In2.Cu")
    assert a.area == pytest.approx(96) and not a.contains(Point(5, 5))
    (b,) = sp.keepout_areas(board, "B.Cu")
    assert b.contains(Point(50, 50))          # footprint zones are in board coordinates


def test_keepouts_off_by_default_changes_nothing(demo_text):
    text = _with(demo_text, _keepout('(layer "In2.Cu")', STRIP))
    plain = sp.compute(text, "In2.Cu", NETS)
    ref = sp.compute(demo_text, "In2.Cu", NETS)
    assert plain.keepouts is None and "keepouts" not in sp.summary(plain)
    assert {n: r.area for n, r in plain.regions.items()} == {n: r.area for n, r in ref.regions.items()}


def test_keepouts_are_left_free_and_zones_stay_connected(demo_text):
    text = _with(demo_text, _keepout('(layer "In2.Cu")', STRIP))
    r = sp.compute(text, "In2.Cu", NETS, keepouts=True)
    strip = box(100, 88, 123, 91)
    assert r.ok and len(r.keepouts) == 1 and sp.summary(r)["keepouts"] == 1
    assert all(reg.intersection(strip).area == 0 for reg in r.regions.values())
    assert all(len(sp._parts(reg)) == 1 for reg in r.regions.values())
    assert any("keepout area(s) on In2.Cu left free" in l for l in r.log)


def test_via_inside_keepout_is_a_warning(demo_text):
    # +5V via at (104, 70)
    text = _with(demo_text, _keepout('(layer "In2.Cu")', [(102, 68), (106, 68), (106, 72), (102, 72)]))
    r = sp.compute(text, "In2.Cu", NETS, keepouts=True)
    assert not r.ok
    assert any("lies in a keepout area" in w for w in sp.summary(r)["warnings"])


# --- design rules ----------------------------------------------------------------

def _project(tmp_path, data, dru=False):
    board = tmp_path / "b.kicad_pcb"
    shutil.copy(DEMO, board)
    (tmp_path / "b.kicad_pro").write_text(json.dumps(data))
    if dru:
        (tmp_path / "b.kicad_dru").write_text("(version 1)")
    return str(board)


def test_rules_patterns_assignments_and_minimum(tmp_path):
    board = _project(tmp_path, {
        "board": {"design_settings": {"rules": {"min_clearance": 0.25, "min_copper_edge_clearance": 0.3}}},
        "net_settings": {
            "classes": [{"name": "Default", "clearance": 0.2}, {"name": "Power", "clearance": 0.4},
                        {"name": "HV", "clearance": 0.9}, {"name": "Inherit", "clearance": None}],
            "netclass_assignments": {"+1V8": ["Inherit"]},
            "netclass_patterns": [{"netclass": "Power", "pattern": "+5?"}]}})
    r = sp.design_rules(board, ["+5V", "+3V3", "+1V8"])
    assert r["classes"] == {"+5V": ["Power"], "+3V3": ["Default"], "+1V8": ["Inherit"]}
    assert r["gap"] == 0.4 and r["edge"] == 0.3 and r["notes"] == []
    # min_clearance wins when it is the largest
    assert sp.design_rules(board, ["+3V3", "+1V8"])["gap"] == 0.25


def test_rules_kicad6_lists_regex_and_dru_note(tmp_path):
    board = _project(tmp_path, {"net_settings": {
        "classes": [{"name": "Default", "clearance": 0.2}, {"name": "HV", "clearance": 0.9, "nets": ["+5V"]},
                    {"name": "Low", "clearance": 0.3}],
        "netclass_patterns": [{"netclass": "Low", "pattern": r"\+[0-9]V[0-9]"}]}}, dru=True)
    r = sp.design_rules(board, ["+5V", "+3V3"])
    assert r["classes"] == {"+5V": ["HV"], "+3V3": ["Low"]} and r["gap"] == 0.9
    assert r["edge"] is None and "kicad_dru" in r["notes"][0]


def test_rules_zero_means_not_set_and_missing_file(tmp_path):
    board = _project(tmp_path, {"board": {"design_settings": {"rules": {"min_clearance": 0.0}}},
                                "net_settings": {"classes": [{"name": "Default", "clearance": 0.0}]}})
    assert sp.design_rules(board, NETS)["gap"] is None
    assert sp.apply_rules(sp.design_rules(board, NETS))[0] == 0.5        # default, not 0
    assert sp.design_rules(str(tmp_path / "none.kicad_pcb"), NETS) is None
    gap, edge, log = sp.apply_rules(None, gap=0.7)
    assert (gap, edge) == (0.7, 0.5) and "no project file" in log[0]


def test_apply_rules_explicit_values_are_minimums():
    rules = {"gap": 0.4, "edge": 0.3, "classes": {"+5V": ["Power"]}, "notes": []}
    assert sp.apply_rules(rules)[:2] == (0.4, 0.3)
    assert sp.apply_rules(rules, gap=0.6, edge=0.1)[:2] == (0.6, 0.3)


def test_cli_rules_json(tmp_path):
    board = _project(tmp_path, {"net_settings": {"classes": [{"name": "Default", "clearance": 0.8}]},
                                "board": {"design_settings": {"rules": {"min_copper_edge_clearance": 0.3}}}})
    code, out, _ = _cli(board, "--nets", *NETS, "--rules", "--json")
    d = json.loads(out)
    assert code == 0 and d["gap"] == 0.8 and d["edge"] == 0.3
    assert d["log"][0].startswith("design rules: gap 0.8 mm")
    zones = [z for z in sp.children(sp.parse_sexpr(open(d["files"]["board"]).read()), "zone")]
    assert all(sp.child(sp.child(z, "connect_pads"), "clearance")[1] == "0.400" for z in zones)


# --- report ----------------------------------------------------------------------

def test_report_html(tmp_path, demo_text):
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_with(demo_text, _keepout('(layer "In2.Cu")', STRIP)))
    code, out, _ = _cli(str(board), "--nets", *NETS, "--keepouts", "--report", "--json")
    d = json.loads(out)
    path = d["files"]["report"]
    assert code == 0 and path.endswith("b_splitplane_report.html")
    html = open(path, encoding="utf-8").read()
    assert html.startswith("<!DOCTYPE html>") and "data:image/png;base64," in html
    assert all(n in html for n in NETS) and "1 left free" in html and "OK: one connected zone" in html


def test_report_custom_path_and_text_mode(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    shutil.copy(DEMO, board)
    target = tmp_path / "my report.html"
    code, out, _ = _cli(str(board), "--nets", *NETS, "--report", str(target))
    assert code == 0 and target.is_file() and f"wrote {target}" in out


def test_no_new_keys_without_options(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    shutil.copy(DEMO, board)
    d = json.loads(_cli(str(board), "--nets", *NETS, "--json")[1])
    assert set(d) == {"ok", "layer", "nets", "zones", "warnings", "log", "files"}
    assert set(d["files"]) == {"board", "preview", "project"}


# --- MCP server and plugin ---------------------------------------------------------

import kicad_splitplane_mcp as smcp
needs_mcp = pytest.mark.skipif(smcp._Server is None, reason="MCP Python SDK not installed")


@needs_mcp
def test_mcp_options(tmp_path, demo_text):
    board = tmp_path / "b.kicad_pcb"
    board.write_text(_with(demo_text, _keepout('(layer "In2.Cu")', STRIP)))
    (tmp_path / "b.kicad_pro").write_text(json.dumps(
        {"net_settings": {"classes": [{"name": "Default", "clearance": 0.6}]}}))
    d = smcp.split_plane(str(board), NETS, use_rules=True, keepouts=True, report=True)
    assert d["ok"] and d["gap"] == 0.6 and d["keepouts"] == 1
    assert os.path.isfile(d["files"]["report"])
    plain = smcp.split_plane(str(board), NETS, out_path=str(tmp_path / "p.kicad_pcb"))
    assert "gap" not in plain and "keepouts" not in plain and "report" not in plain["files"]


@needs_mcp
def test_mcp_describes_options():
    import asyncio
    tools = {t.name: t for t in asyncio.run(smcp.build_server().list_tools())}
    desc = tools["split_plane"].description
    assert "use_rules" in desc and "keepouts" in desc and "report" in desc


def test_plugin_board_file():
    pytest.importorskip("kipy")
    sys.path.insert(0, os.path.join(ROOT, "plugin"))
    import splitplane_action as act
    from types import SimpleNamespace as NS
    board = NS(document=NS(project=NS(path="/proj"), board_filename="x.kicad_pcb"))
    assert act.board_file(board) == os.path.join("/proj", "x.kicad_pcb")
    assert act.board_file(NS()) is None
    assert act.report_path("/proj/x.kicad_pcb") == "/proj/x_splitplane_report.html"
