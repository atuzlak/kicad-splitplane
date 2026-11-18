"""Agent integration: --json, summary() and the MCP server's tools.
The MCP tests are skipped when the MCP Python SDK is not installed."""
import asyncio, json, os, shutil, subprocess, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import kicad_splitplane as sp

ROOT = os.path.join(os.path.dirname(__file__), "..")
DEMO = os.path.join(ROOT, "examples", "demo_board.kicad_pcb")
NETS = ["+5V", "+3V3", "+1V8"]


@pytest.fixture
def board(tmp_path):
    """A copy of the demo board, so outputs land in tmp_path."""
    path = tmp_path / "demo.kicad_pcb"
    shutil.copy(DEMO, path)
    return str(path)


def _cli(*args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, "kicad_splitplane.py"), *args],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


# --- summary() ---------------------------------------------------------------

def test_summary_fields():
    r = sp.process(open(DEMO, encoding="utf-8").read(), "In2.Cu", NETS)
    s = sp.summary(r)
    assert s["ok"] is True and s["layer"] == "In2.Cu"
    assert s["nets"] == sorted(NETS)
    assert set(s["zones"]) == set(NETS)
    for net, z in s["zones"].items():
        assert z["parts"] == 1 and z["area_mm2"] > 0
        assert z["seeds"] == sum(n == net for *_, n in r.seeds)
    assert s["warnings"] == [] and s["log"] == r.log
    json.dumps(s)  # serialisable


def test_summary_warnings_are_the_bang_lines():
    r = sp.process(open(DEMO, encoding="utf-8").read(), "In2.Cu", NETS, plain=True)
    s = sp.summary(r)
    bang = [l for l in r.log if l.startswith("!")]
    assert s["ok"] is False and bang
    assert s["warnings"] == [l[1:].strip() for l in bang]


# --- --json --------------------------------------------------------------------

def test_json_split(board):
    code, out, err = _cli(board, "--nets", *NETS, "--json")
    assert code == 0, err
    d = json.loads(out)
    assert d["ok"] is True and set(d["zones"]) == set(NETS)
    base = board[:-len(".kicad_pcb")]
    assert d["files"]["board"] == base + "_splitplane.kicad_pcb"
    assert d["files"]["preview"] == base + "_splitplane.png"
    for f in [d["files"]["board"], d["files"]["preview"], *d["files"]["project"]]:
        assert os.path.isfile(f)
    assert err == ""  # no progress output in JSON mode


def test_json_list_nets(board):
    code, out, _ = _cli(board, "--list-nets", "--json")
    d = json.loads(out)
    assert code == 0 and d["ok"] is True
    assert {n["name"] for n in d["nets"]} >= set(NETS)
    text_code, text_out, _ = _cli(board, "--list-nets")
    assert text_code == 0 and len(text_out.splitlines()) == len(d["nets"]) + 1


def test_json_exit_code_1_with_warnings(board):
    code, out, _ = _cli(board, "--nets", *NETS, "--plain", "--json")
    d = json.loads(out)
    assert code == 1 and d["ok"] is False and d["warnings"]


@pytest.mark.parametrize("args, code", [
    (["--nets", "+5V"], 1),                    # ValueError: at least two nets
    (["--nets", "+5V", "+3V3", "--layer", "In9.Cu"], 1),
    ([], 2),                                   # --nets missing: usage error
    (["--nets", "+5V", "+3V3", "--bogus"], 2),
])
def test_json_errors(board, args, code):
    got, out, _ = _cli(board, *args, "--json")
    d = json.loads(out)
    assert got == code and d["ok"] is False and d["error"]


def test_json_missing_board(tmp_path):
    code, out, _ = _cli(str(tmp_path / "nope.kicad_pcb"), "--nets", "+5V", "+3V3", "--json")
    assert code == 1 and json.loads(out)["ok"] is False


def test_text_output_unchanged_without_json(board):
    code, out, _ = _cli(board, "--nets", *NETS)
    assert code == 0 and not out.lstrip().startswith("{")
    assert "OK: one connected zone per net" in out and "wrote " in out


# --- MCP server ----------------------------------------------------------------

import kicad_splitplane_mcp as smcp  # imports without the SDK too
HAVE_MCP = smcp._Server is not None
needs_mcp = pytest.mark.skipif(not HAVE_MCP, reason="MCP Python SDK not installed")


@needs_mcp
def test_mcp_lists_both_tools():
    tools = asyncio.run(smcp.build_server().list_tools())
    names = {t.name for t in tools}
    assert names == {"list_power_nets", "split_plane"}
    desc = {t.name: t.description for t in tools}
    assert "GND" in desc["split_plane"] and "include_smd" in desc["split_plane"]
    assert "DRC" in desc["split_plane"]


@needs_mcp
def test_mcp_list_power_nets(board):
    d = smcp.list_power_nets(board)
    assert {n["name"] for n in d["nets"]} >= set(NETS)
    assert "In2.Cu" in d["copper_layers"]
    assert len(smcp.list_power_nets(board, all_nets=True)["nets"]) >= len(d["nets"])


@needs_mcp
def test_mcp_split_plane_writes_new_board(board):
    before = open(board, encoding="utf-8").read()
    d = smcp.split_plane(board, NETS)
    assert d["ok"] is True and set(d["zones"]) == set(NETS)
    assert d["files"]["board"].endswith("demo_splitplane.kicad_pcb")
    assert os.path.isfile(d["files"]["board"]) and os.path.isfile(d["files"]["preview"])
    assert open(board, encoding="utf-8").read() == before  # input untouched


@needs_mcp
def test_mcp_never_overwrites_input(board):
    before = open(board, encoding="utf-8").read()
    with pytest.raises(smcp.ToolError, match="never overwritten"):
        smcp.split_plane(board, NETS, out_path=board)
    assert open(board, encoding="utf-8").read() == before


@needs_mcp
@pytest.mark.parametrize("kwargs, match", [
    ({"nets": ["+5V"]}, "at least two nets"),
    ({"nets": NETS, "layer": "In9.Cu"}, "not in board"),
    ({"nets": ["+5V", "NOPE"]}, "No vias"),
])
def test_mcp_value_errors_become_tool_errors(board, kwargs, match):
    with pytest.raises(smcp.ToolError, match=match):
        smcp.split_plane(board, **kwargs)


@needs_mcp
def test_mcp_bad_paths(tmp_path, board):
    with pytest.raises(smcp.ToolError, match="not found"):
        smcp.list_power_nets(str(tmp_path / "nope.kicad_pcb"))
    with pytest.raises(smcp.ToolError, match=r"\.kicad_pcb"):
        smcp.list_power_nets(str(tmp_path / "board.txt"))
    with pytest.raises(smcp.ToolError, match=r"\.kicad_pcb"):
        smcp.split_plane(board, NETS, out_path=str(tmp_path / "out.txt"))


@needs_mcp
def test_mcp_include_smd_is_marked_preview(board):
    d = smcp.split_plane(board, NETS, include_smd=True)
    assert "Preview only" in d["note"]


def test_mcp_main_without_sdk_gives_help(monkeypatch):
    import kicad_splitplane_mcp as m
    monkeypatch.setattr(m, "_Server", None)
    with pytest.raises(SystemExit) as e:
        m.main([])
    assert "kicad-splitplane[mcp]" in str(e.value.code)
