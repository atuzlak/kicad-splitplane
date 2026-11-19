#!/usr/bin/env python3
"""
kicad-splitplane-mcp - MCP server that lets AI agents split KiCad power planes.

A thin wrapper around the kicad_splitplane library with two tools:

  list_power_nets(board_path)        which supply nets the board has
  split_plane(board_path, nets, ...) one zone per net, written to a NEW board

It never overwrites the input board. Run it over stdio:

  pip install "kicad-splitplane[mcp]"
  kicad-splitplane-mcp                 # or: uvx kicad-splitplane-mcp

Works with the MCP Python SDK 2.x (MCPServer) and 1.x (FastMCP).

Author: Aleksandar Tuzlak - MIT License
"""
import os
import re
import sys
from typing import List, Optional

import kicad_splitplane as sp

try:  # MCP Python SDK 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:
    try:  # MCP Python SDK 1.x
        from mcp.server.fastmcp import FastMCP as _Server
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:
        _Server = None

        class ToolError(Exception):
            """Stand-in so the tool functions work without the MCP SDK."""

__version__ = sp.__version__

INSTRUCTIONS = """\
kicad-splitplane splits one copper layer of a KiCad board (.kicad_pcb) into
one continuous zone per supply net, computed from the nets' vias and
through-hole pads. Use it when several supply nets (e.g. +5V, +3V3, +1V8)
share one power-plane layer. Never put GND on a split plane; GND gets its own
solid plane.

Workflow: list_power_nets -> pick the supply nets with the user ->
split_plane -> handle the warnings -> the user opens the new board in KiCad,
fills all zones (B) and runs DRC. The input board is never modified."""

LIST_DESCRIPTION = """\
List the supply-like nets of a KiCad board (.kicad_pcb) and its copper layers.

Call this first, before split_plane, to find out which nets can share a
power-plane layer. For each net it returns the number of vias/through-hole
pads (the seeds split_plane uses) and SMD pads. A net with 0 vias/THT but SMD
pads has not been fanned out yet: preview it with split_plane(include_smd=True).
Supply-like means the name looks like a voltage or rail (+5V, 3V3, VCC, VDD,
VBUS, VIN, ...); set all_nets=True if the user's supply nets have other names.
GND and signal nets are not candidates. Read-only; changes nothing."""

SPLIT_DESCRIPTION = """\
Split one copper layer of a KiCad board into one continuous zone per supply
net and write the result to a NEW board file (the input is never overwritten).

Use it when two or more supply nets (e.g. +5V, +3V3, +1V8) share one plane
layer. Never include GND or signal nets. Get the net names and the layer from
list_power_nets first and confirm them with the user if unsure.

Writes <out_path> (default <board>_splitplane.kicad_pcb), a preview PNG next
to it and copies of the project files (.kicad_pro/.kicad_prl, nets coloured as
in the preview). Returns ok, per-net zones (parts, area_mm2, seeds), warnings,
the log and the written file paths.

Warnings and what to do:
- "via ... is trapped by other nets -> move it to ~(x, y)": move that via to
  the suggested spot (or retry with a smaller res, e.g. 0.3).
- "split into N islands" / "cut the copper into N pieces": the net cannot be
  one zone with the current vias; move or add vias, or try another layer.
- "has a neck narrower than ... mm": a thin spot; acceptable next to dense
  connectors, otherwise move vias to give it room.
- "pad/via ... is not connected to its zone" / "zone runs over a ... pad/via":
  move the named via, or reduce gap.
- No vias/THT pads for a net: the board is not fanned out yet. Run again with
  include_smd=True for a PREVIEW only (SMD pads stand in for vias), place vias,
  then run without include_smd for the real split.

ok=True means one connected zone per net. Afterwards tell the user to open the
new board in KiCad, fill all zones (B) and run DRC; the tool cannot do that.

Parameters (mm): gap between zones (0.5), edge clearance to the board edge
(0.5), min_neck warning width (1.0), res grid resolution (0.5; 0.3-0.6 if vias
get trapped). plain=True skips connecting the vias (faster, may give islands).
keep_old=True keeps existing zones of these nets on the layer (default: they
are replaced).

Optional (all off by default):
- use_rules=True takes gap and edge from the board's design rules
  (<board>.kicad_pro: net class clearances, minimum clearance, copper-to-edge
  clearance); gap/edge, if given, are then minimums. Recommended whenever the
  board has a project file.
- keepouts=True leaves the layer's keepout areas (rule areas that forbid copper
  pours) free, so zones route around them. Recommended if the board has
  keepouts (antennas, mounting holes); otherwise KiCad cuts the zones there
  when filling and a net can lose its connection.
- report=True also writes <out>_report.html: preview, zones, warnings and
  settings on one page, for the user to read or print."""


def _board(board_path: str) -> str:
    path = os.path.abspath(os.path.expanduser(board_path))
    if not path.endswith(".kicad_pcb"):
        raise ToolError(f"Not a KiCad board (.kicad_pcb): {board_path}")
    if not os.path.isfile(path):
        raise ToolError(f"Board not found: {path}")
    return path


def list_power_nets(board_path: str, all_nets: bool = False) -> dict:
    path = _board(board_path)
    text = open(path, encoding="utf-8").read()
    try:
        nets = sp.list_nets(text) if all_nets else sp.list_power_candidates(text)
        layers = sp.copper_layers(sp.parse_sexpr(text))
    except ValueError as e:
        raise ToolError(str(e)) from e
    return {"board": path, "copper_layers": layers,
            "nets": [{"name": n, "vias_tht": t, "smd": s} for n, t, s in nets]}


def split_plane(board_path: str, nets: List[str], layer: str = "In2.Cu",
                gap: Optional[float] = None, edge: Optional[float] = None, min_neck: float = 1.0,
                res: float = 0.5, include_smd: bool = False, plain: bool = False,
                keep_old: bool = False, out_path: Optional[str] = None, use_rules: bool = False,
                keepouts: bool = False, report: bool = False) -> dict:
    path = _board(board_path)
    out = (os.path.abspath(os.path.expanduser(out_path)) if out_path
           else re.sub(r"\.kicad_pcb$", "", path) + "_splitplane.kicad_pcb")
    if not out.endswith(".kicad_pcb"):
        raise ToolError(f"out_path must end in .kicad_pcb: {out_path}")
    if os.path.realpath(out) == os.path.realpath(path):
        raise ToolError("out_path must differ from board_path: the input board is never overwritten.")
    if not os.path.isdir(os.path.dirname(out)):
        raise ToolError(f"Output folder does not exist: {os.path.dirname(out)}")
    text = open(path, encoding="utf-8").read()
    notes = []
    if use_rules:
        gap, edge, notes = sp.apply_rules(sp.design_rules(path, nets), gap, edge)
    gap = 0.5 if gap is None else gap
    edge = 0.5 if edge is None else edge
    try:
        r = sp.process(text, layer, nets, gap, edge, min_neck, res, include_smd, plain, keep_old,
                       keepouts=keepouts)
    except ValueError as e:
        raise ToolError(str(e)) from e
    r.log[:0] = notes
    result = {**sp.summary(r), **({"gap": gap, "edge": edge} if use_rules else {}),
              "files": sp.write_outputs(path, out, r, report=report)}
    if include_smd:
        result["note"] = ("Preview only: SMD pads stood in for vias. Place the vias, then run "
                          "again without include_smd.")
    return result


def build_server():
    """The MCP server with both tools registered (needs the MCP SDK)."""
    if _Server is None:
        raise ImportError("the MCP Python SDK is not installed")
    server = _Server("kicad-splitplane", instructions=INSTRUCTIONS)
    server.tool(description=LIST_DESCRIPTION)(list_power_nets)
    server.tool(description=SPLIT_DESCRIPTION)(split_plane)
    return server


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv and argv[0] == "--version":
        print(__version__)
        return 0
    if _Server is None:
        sys.exit("kicad-splitplane-mcp needs the MCP Python SDK. Install it with\n"
                 '  pip install "kicad-splitplane[mcp]"\n'
                 "or run the server with: uvx kicad-splitplane-mcp")
    build_server().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
