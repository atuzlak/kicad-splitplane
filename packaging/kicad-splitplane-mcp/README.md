# kicad-splitplane-mcp

MCP server for [kicad-splitplane](https://github.com/atuzlak/kicad-splitplane):
lets AI agents (Claude, Cursor, VS Code, ...) split a KiCad power layer into one
continuous copper zone per supply net.

This package has no code of its own. It installs `kicad-splitplane[mcp]` and
provides the `kicad-splitplane-mcp` command, so you can run the server with

```
uvx kicad-splitplane-mcp
```

Client configuration (stdio):

```json
{
  "mcpServers": {
    "kicad-splitplane": { "command": "uvx", "args": ["kicad-splitplane-mcp"] }
  }
}
```

Tools:

- `list_power_nets(board_path, all_nets=False)`: the supply-like nets of a
  `.kicad_pcb` file, with their via/THT and SMD pad counts, and its copper layers.
- `split_plane(board_path, nets, layer="In2.Cu", ...)`: one zone per net, written
  to a new board (`<board>_splitplane.kicad_pcb`) with a preview PNG. The input
  board is never overwritten.

Afterwards open the new board in KiCad, fill all zones (B) and run DRC.

Documentation: https://people.ee.ethz.ch/~atuzlak/kicad-splitplane.html

MIT License, Aleksandar Tuzlak

<!-- mcp-name: io.github.atuzlak/kicad-splitplane -->
