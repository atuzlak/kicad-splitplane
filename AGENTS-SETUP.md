# One-time setup for PyPI and the MCP Registry

The release workflow (`.github/workflows/release.yml`) publishes every tag
`v<version>` to three places:

1. **release**: KiCad PCM zip as a GitHub release asset (as before).
2. **pypi**: `kicad-splitplane` (the library, CLI and MCP server) and
   `kicad-splitplane-mcp` (a thin package without code that depends on
   `kicad-splitplane[mcp]` and provides the command, for `uvx kicad-splitplane-mcp`).
3. **mcp-registry**: `server.json` as `io.github.atuzlak/kicad-splitplane`.

PyPI uses trusted publishing (no API token is stored anywhere) and needs
the setup below once. The MCP Registry needs none: the job logs in with
the workflow's GitHub OIDC token, which owns the `io.github.atuzlak/*`
namespace.

## 1. GitHub environment "pypi"

Repository → **Settings → Environments → New environment**, name `pypi`.

Recommended:
- **Deployment branches and tags**: "Selected branches and tags", add the tag
  rule `v*`.
- **Required reviewers**: yourself, if you want to approve each PyPI upload.

## 2. PyPI pending publishers (one per package)

Both projects do not exist on PyPI yet, so add them as *pending* publishers:
<https://pypi.org/manage/account/publishing/> → **Add a new pending publisher** → GitHub.

| Field | kicad-splitplane | kicad-splitplane-mcp |
|---|---|---|
| PyPI Project Name | `kicad-splitplane` | `kicad-splitplane-mcp` |
| Owner | `atuzlak` | `atuzlak` |
| Repository name | `kicad-splitplane` | `kicad-splitplane` |
| Workflow name | `release.yml` | `release.yml` |
| Environment name | `pypi` | `pypi` |

The first successful upload creates each project and turns the pending
publisher into a normal one. (If a project already exists, add the publisher
under that project's **Settings → Publishing** instead.)

## 3. Releasing

1. Set the same version everywhere (the `release` job checks this and stops
   otherwise):
   - `__version__` in `kicad_splitplane.py`
   - `version` in `pyproject.toml`
   - `version` and the `kicad-splitplane[mcp]==` pin in
     `packaging/kicad-splitplane-mcp/pyproject.toml`
   - `version` and `packages[0].version` in `server.json`
2. Commit, then `git tag v<version> && git push origin v<version>`.
3. The jobs run in order: release → pypi → mcp-registry. The last one waits
   until PyPI serves the new `kicad-splitplane-mcp` version before publishing.
4. Afterwards, as before, open a merge request at
   <https://gitlab.com/kicad/addons/metadata> for the KiCad PCM listing.

## Checks

- PyPI: <https://pypi.org/project/kicad-splitplane/> and
  <https://pypi.org/project/kicad-splitplane-mcp/>
- MCP Registry:
  `curl "https://registry.modelcontextprotocol.io/v0/servers?search=io.github.atuzlak/kicad-splitplane"`
- The registry checks package ownership through the line
  `<!-- mcp-name: io.github.atuzlak/kicad-splitplane -->` in the README of
  the PyPI package (`packaging/kicad-splitplane-mcp/README.md`; the main
  README has it too). Do not remove it.
