---
name: kicad-splitplane
description: Split one copper layer of a KiCad board (.kicad_pcb) into one continuous zone per supply net (e.g. +5V, +3V3, +1V8 sharing an inner power layer), and handle the tool's warnings. Use when the user wants a split power plane, power zones for several voltages on one layer, or asks why a supply zone is broken into islands.
---

# kicad-splitplane

kicad-splitplane computes, from the vias and through-hole pads of each supply
net, a split of one copper layer into exactly one continuous zone per net, and
writes the zones into a copy of the board. It needs no KiCad installation.

## Tools

Use the MCP server if it is connected (`list_power_nets`, `split_plane`).
Otherwise use the command line with `--json`:

```bash
pip install kicad-splitplane                                   # or: uvx --from kicad-splitplane kicad-splitplane ...
kicad-splitplane board.kicad_pcb --list-nets --json
kicad-splitplane board.kicad_pcb --layer In2.Cu --nets +5V +3V3 +1V8 --json
```

Both give the same result: `ok`, per-net `zones` (`parts`, `area_mm2`,
`seeds`), `warnings`, `log` and the written `files`. Exit code 0 = clean,
1 = warnings or error, 2 = bad arguments.

## Workflow

1. **Find the nets and the layer.** Call `list_power_nets(board_path)` (or
   `--list-nets --json`). It lists supply-like nets with their via/THT and SMD
   pad counts, and the board's copper layers. If the user's supplies have
   unusual names, use `all_nets=True`.
2. **Choose with the user.** Pick the supply nets that share one inner layer
   (usually `In2.Cu` on a 4-layer board; ask if unsure).
   - At least two nets.
   - **Never GND** and never signal nets. GND belongs on its own solid plane.
   - Nets with only a few vias are fine; nets with 0 vias/THT but SMD pads are
     not fanned out yet (see step 3).
3. **Not fanned out yet?** If a net has no vias/THT pads, run once with
   `include_smd=True` (`--include-smd`). SMD pads then stand in for vias. This
   is a **preview only**, to plan where the power vias should go. Tell the
   user to place the vias, then run again without `include_smd` for the real
   split.
4. **Split.** Call `split_plane(board_path, nets, layer)` (defaults: gap 0.5,
   edge 0.5, min_neck 1.0, res 0.5 mm). If the board has a project file
   (`<board>.kicad_pro`), add `use_rules=True` so gap and edge follow its
   design rules. If it has keepout areas (antenna, mounting holes), add
   `keepouts=True`. For a summary the user can read or print, add
   `report=True`. It writes
   `<board>_splitplane.kicad_pcb`, a preview `<board>_splitplane.png` and
   project copies (`.kicad_pro`/`.kicad_prl`). The input board is never
   changed. Show the user the preview PNG if you can.
5. **Handle the warnings** (below). Change the board or the options and run
   again until `ok` is true, or explain the remaining warnings to the user.
6. **Finish in KiCad.** You cannot fill zones or run DRC. Tell the user to
   open the new board in KiCad (close the original first), press **B** to fill
   all zones and run **DRC**.

## Warnings and what to do

Each warning names the net and usually a position in mm (KiCad coordinates).

| Warning | Meaning | What to do |
|---|---|---|
| `via at (x, y) is trapped by other nets -> move it to ~(x', y')` | The via is enclosed by other nets; no continuous zone can reach it. | Ask the user to move that via to the suggested position (fanout length is given). Retrying with `res=0.3` sometimes finds a path. |
| `split into N islands on this layer` | The net cannot be one zone with the current via positions. | Move or add vias so the net's vias are grouped, or put the net on another layer. Try `res=0.3`. |
| `other nets' pads/vias cut the copper into N pieces (e.g. no room between header pins)` | A row of foreign pads (header, BGA) splits the zone after filling. | Move vias so the zone goes around the row, or reduce `gap` if the design rules allow. |
| `has a neck narrower than W mm` | The zone gets thin somewhere (current bottleneck). | Acceptable next to dense connectors for low currents; otherwise move vias to give it room. Not an error by itself. |
| `pad/via at (x, y) is not connected to its zone` | That pad or via will not connect after filling. | Move the via, or reduce `gap`. |
| `zone runs over a NET pad/via at (x, y)` | A zone overlaps another supply's copper. | Move that via, or reduce `gap`. |
| Islands after a run with `plain=True` | Plain mode does not connect the vias. | Run again without `plain`. |
| `via/pad at (x, y) lies in a keepout area` | With `keepouts=True`: a via of the net sits where copper pours are forbidden. | Move the via out of the keepout, or check the keepout's layers in KiCad. |

Errors (no result, `ok: false` with `error`):

- `Select at least two nets.`: give two or more nets.
- `Layer X not in board (...)`: use one of the listed copper layers.
- `No vias/THT pads found for: NET`: the net is not fanned out; see step 3,
  or check the net name with `list_power_nets`.
- `Could not build a closed board outline from Edge.Cuts.`: the board outline
  is open; the user has to close it in KiCad.

## Options

| Option | Default | Use |
|---|---|---|
| `layer` | `In2.Cu` | Copper layer to split |
| `gap` | 0.5 mm | Copper gap between zones (should satisfy the board's clearance rules) |
| `edge` | 0.5 mm | Clearance to the board edge |
| `min_neck` | 1.0 mm | Warn below this copper width |
| `res` | 0.5 mm | Grid resolution; 0.3-0.6 if vias get trapped (smaller is slower) |
| `include_smd` | false | SMD pads as stand-in vias (preview before fanout) |
| `plain` | false | Fast plain Voronoi, may create islands; only for a quick look |
| `keep_old` | false | Keep existing zones of these nets on the layer (default: replaced) |
| `out_path` | `<board>_splitplane.kicad_pcb` | Output board; must differ from the input |
| `use_rules` | false | Gap and edge from the design rules in `<board>.kicad_pro` (net class clearances, minimum clearance, copper-to-edge); given gap/edge are minimums. Custom `.kicad_dru` rules are not read (the log notes it) |
| `keepouts` | false | Leave the layer's keepout areas (no copper pour) free; zones go around them |
| `report` | false | Also write `<out>_report.html` (preview, zones, warnings, settings) |

## Good to know

- Zones are written without holes; smaller zones get a higher priority so an
  enclosed island still fills.
- Signals on the neighbouring layer use the plane as return path; they should
  not cross the gaps between zones.
- Documentation: https://people.ee.ethz.ch/~atuzlak/kicad-splitplane.html
