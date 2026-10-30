#!/usr/bin/env python3
"""
kicad-splitplane as a KiCad 9+ IPC API plugin.

KiCad starts this script from the PCB editor toolbar. It reads the open board
through the API, runs the same partition as the CLI and writes the zones
straight into the board as one undo step (Ctrl+Z removes them again).

Development: with KiCad running and the API enabled (Preferences > Plugins),
`python plugin/splitplane_action.py` connects to the open board as well.

Author: Aleksandar Tuzlak - MIT License
"""
from __future__ import annotations

import os
import sys
import threading
import traceback

HERE = os.path.dirname(os.path.realpath(__file__))
# Bundled copy of the core in the installed package, or the repo checkout during development.
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import kicad_splitplane as sp  # noqa: E402
from kipy import KiCad  # noqa: E402
from kipy.board_types import Net, Zone  # noqa: E402
from kipy.geometry import PolygonWithHoles, PolyLine, PolyLineNode, Vector2  # noqa: E402
from kipy.proto.board.board_types_pb2 import ZoneConnectionStyle, ZoneType  # noqa: E402
from kipy.util.board_layer import layer_from_canonical_name  # noqa: E402
from kipy.util.units import from_mm  # noqa: E402

UNDO_MESSAGE = "kicad-splitplane"


# =============================================================================
# Board access (no GUI; also used by the tests)
# =============================================================================
def make_zones(result: sp.Result, gap: float, nets_by_name=None) -> list[Zone]:
    """KiCad zones for a computed partition, with the same settings the CLI writes."""
    layer = layer_from_canonical_name(result.layer)
    nets_by_name = nets_by_name or {}
    zones = []
    for poly, net, prio in sp.zone_parts(result.regions):
        outline = PolyLine()
        for x, y in list(poly.exterior.coords)[:-1]:
            outline.append(PolyLineNode.from_point(Vector2.from_xy_mm(x, y)))
        outline.closed = True
        shape = PolygonWithHoles()
        shape.outline = outline
        z = Zone()
        z.layers = [layer]
        # The board's own Net object also carries the net code KiCad 9 needs.
        z.net = nets_by_name.get(net) or Net(name=net)
        z.outline = shape
        z.name = f"splitplane_{net}"
        z.priority = prio
        z.clearance = from_mm(gap / 2)
        z.connection.zone_connection = ZoneConnectionStyle.ZCS_FULL
        zones.append(z)
    return zones


def old_zones(board, layer: str, nets) -> list[Zone]:
    """Copper zones of `nets` that lie only on `layer` (what the CLI replaces too)."""
    lid = layer_from_canonical_name(layer)
    return [z for z in board.get_zones()
            if z.type == ZoneType.ZT_COPPER and list(z.layers) == [lid] and z.net.name in nets]


def apply(board, result: sp.Result, gap: float, keep_old=False, fill=True) -> list[str]:
    """Write the zones into the open board as a single undo step."""
    log = []
    nets = set(result.regions)
    commit = board.begin_commit()
    try:
        if not keep_old:
            old = old_zones(board, result.layer, nets)
            if old:
                board.remove_items(old)
                log.append(f"removed {len(old)} existing zone(s) of these nets on {result.layer}")
        zones = make_zones(result, gap, {n.name: n for n in board.get_nets() if n.name in nets})
        board.create_items(zones)
    except Exception:
        board.drop_commit(commit)
        raise
    board.push_commit(commit, UNDO_MESSAGE)
    log.append(f"added {len(zones)} zone(s) on {result.layer}")
    if fill:
        board.refill_zones()
        log.append("zones filled")
    return log


# =============================================================================
# Dialog
# =============================================================================
def run_dialog(board):
    # wxPython ships with KiCad, and KiCad creates plugin environments with
    # --system-site-packages, so it is available without installing anything.
    import wx
    import numpy as np
    # Not the WXAgg backend: it imports wx.svg, which KiCad's wxPython lacks.
    # The figure is rendered with Agg and painted as a bitmap instead.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    text = board.get_as_string()
    layers = sp.copper_layers(sp.parse_sexpr(text))
    supply = sp.list_power_candidates(text)
    every = sp.list_nets(text)
    inner = [l for l in layers if l.startswith("In")]

    app = wx.App()
    frame = wx.Frame(None, title=f"kicad-splitplane {sp.__version__}", size=(1050, 680))
    panel = wx.Panel(frame)
    side = wx.BoxSizer(wx.VERTICAL)

    def label(text_, top=8):
        side.Add(wx.StaticText(panel, label=text_), 0, wx.TOP, top)

    # --- layer and nets
    label("Layer", 0)
    layer = wx.Choice(panel, choices=layers)
    layer.SetStringSelection("In2.Cu" if "In2.Cu" in layers else (inner or layers or [""])[0])
    side.Add(layer, 0, wx.EXPAND)

    label("Nets (vias/THT, SMD)")
    box = wx.CheckListBox(panel, size=(240, 200))
    side.Add(box, 1, wx.EXPAND)
    show_all = wx.CheckBox(panel, label="show all nets")
    show_all.SetValue(not supply)
    side.Add(show_all)
    shown: list[str] = []

    def fill_list(_=None):
        keep = {shown[i] for i in box.GetCheckedItems()}
        shown[:] = [n for n, *_ in (every if show_all.GetValue() else supply)]
        box.Set([f"{n}   ({t}, {s_})" for n, t, s_ in (every if show_all.GetValue() else supply)])
        box.SetCheckedItems([i for i, n in enumerate(shown) if n in keep])

    show_all.Bind(wx.EVT_CHECKBOX, fill_list)
    fill_list()

    # --- parameters
    grid = wx.FlexGridSizer(2, 4, 6)
    grid.AddGrowableCol(0)
    params = {}
    for key, text_, default in [
            ("gap", "Gap between zones [mm]", 0.5),
            ("edge", "Clearance to edge [mm]", 0.5),
            ("min_neck", "Warn below width [mm]", 1.0),
            ("res", "Grid resolution [mm]", 0.5)]:
        grid.Add(wx.StaticText(panel, label=text_), 0, wx.ALIGN_CENTER_VERTICAL)
        params[key] = wx.TextCtrl(panel, value=str(default), size=(60, -1))
        grid.Add(params[key])
    side.Add(grid, 0, wx.EXPAND | wx.TOP, 8)

    flags = {}
    for key, text_, default in [
            ("include_smd", "Use SMD pads as stand-in vias", False),
            ("plain", "Plain Voronoi (may create islands)", False),
            ("keep_old", "Keep existing zones of these nets", False),
            ("fill", "Fill zones after applying", True)]:
        flags[key] = wx.CheckBox(panel, label=text_)
        flags[key].SetValue(default)
        side.Add(flags[key], 0, wx.TOP, 4)

    buttons = wx.BoxSizer(wx.HORIZONTAL)
    b_preview, b_apply, b_close = (wx.Button(panel, label=l) for l in ("Preview", "Apply", "Close"))
    for b in (b_preview, b_apply, b_close):
        buttons.Add(b, 0, wx.RIGHT, 4)
    side.Add(buttons, 0, wx.TOP, 10)
    status = wx.StaticText(panel, label="Select at least two nets, then Preview.")
    side.Add(status, 0, wx.TOP | wx.EXPAND, 6)

    # --- preview and log
    fig = Figure(figsize=(6, 4.5), dpi=100)
    agg = FigureCanvasAgg(fig)
    canvas = wx.Panel(panel, size=(600, 450))
    canvas.SetBackgroundColour(wx.WHITE)
    view = {"bmp": None}

    def render(_=None):
        w, h = canvas.GetClientSize()
        if w < 10 or h < 10:
            return
        fig.set_size_inches(w / fig.dpi, h / fig.dpi)
        if fig.axes:
            fig.tight_layout()
        agg.draw()
        rgba = np.asarray(agg.buffer_rgba())
        view["bmp"] = wx.Bitmap.FromBufferRGBA(rgba.shape[1], rgba.shape[0], rgba.tobytes())
        canvas.Refresh()

    def paint(_):
        dc = wx.PaintDC(canvas)
        if view["bmp"]:
            dc.DrawBitmap(view["bmp"], 0, 0)

    def resize(event):
        wx.CallAfter(render)
        event.Skip()

    canvas.Bind(wx.EVT_PAINT, paint)
    canvas.Bind(wx.EVT_SIZE, resize)
    log =wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY, size=(-1, 120))
    log.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    right = wx.BoxSizer(wx.VERTICAL)
    right.Add(canvas, 1, wx.EXPAND)
    right.Add(log, 0, wx.EXPAND)

    outer = wx.BoxSizer(wx.HORIZONTAL)
    outer.Add(side, 0, wx.EXPAND | wx.ALL, 8)
    outer.Add(right, 1, wx.EXPAND | wx.TOP | wx.RIGHT | wx.BOTTOM, 8)
    panel.SetSizer(outer)

    def set_status(msg):
        status.SetLabel(msg)
        status.Wrap(240)

    def error(msg):
        wx.MessageBox(msg, "kicad-splitplane", wx.OK | wx.ICON_ERROR, frame)

    state = {"key": None, "result": None, "busy": False}

    def settings():
        nets = [shown[i] for i in box.GetCheckedItems()]
        try:
            num = {k: float(v.GetValue()) for k, v in params.items()}
        except ValueError:
            raise ValueError("Parameters must be numbers.")
        return (layer.GetStringSelection(), nets, num,
                flags["include_smd"].GetValue(), flags["plain"].GetValue())

    def set_busy(busy):
        state["busy"] = busy
        b_preview.Enable(not busy)
        b_apply.Enable(not busy)

    def compute(then=None):
        if state["busy"]:
            return
        try:
            key = settings()
        except ValueError as e:
            error(str(e))
            return
        lay, nets, num, smd, plain = key
        # Re-read the board each time: vias may have moved since the dialog opened.
        board_text = board.get_as_string()

        def progress(i, n):
            wx.CallAfter(set_status, f"Routing order {i + 1}/{n}...")

        def work():
            try:
                r = sp.compute(board_text, lay, nets, num["gap"], num["edge"], num["min_neck"],
                               num["res"], smd, plain, progress=progress)
            except Exception as e:  # shown in the dialog
                wx.CallAfter(done, None, e)
            else:
                wx.CallAfter(done, r, None)

        def done(r, e):
            set_busy(False)
            if e is not None:
                set_status("Failed.")
                error(str(e) if isinstance(e, ValueError) else "".join(traceback.format_exception(e)))
                return
            state.update(key=repr(key), result=r)
            fig.clear()
            sp.draw(r, fig.add_subplot(111))
            render()
            log.SetValue("\n".join(r.log))
            set_status("OK: one zone per net." if r.ok else "Warnings, see log below.")
            if then:
                then(r)

        set_busy(True)
        set_status("Computing...")
        threading.Thread(target=work, daemon=True).start()

    def write(r):
        if not r.ok and wx.MessageBox("The split has warnings (see log). Apply anyway?",
                                      "kicad-splitplane", wx.YES_NO | wx.ICON_WARNING, frame) != wx.YES:
            return
        set_status("Writing zones...")
        wx.SafeYield()
        try:
            lines = apply(board, r, float(params["gap"].GetValue()),
                          flags["keep_old"].GetValue(), flags["fill"].GetValue())
        except Exception as e:
            set_status("Failed.")
            error("".join(traceback.format_exception(e)))
            return
        log.SetValue("\n".join(r.log + lines + ["", "Undo in KiCad with Ctrl+Z. Run DRC to check."]))
        set_status("Zones added to the board.")
        state["key"] = None  # the board changed

    def do_apply(_=None):
        try:
            current = repr(settings())
        except ValueError as e:
            error(str(e))
            return
        if state["key"] == current:
            write(state["result"])
        else:
            compute(then=write)

    b_preview.Bind(wx.EVT_BUTTON, lambda _: compute())
    b_apply.Bind(wx.EVT_BUTTON, do_apply)
    b_close.Bind(wx.EVT_BUTTON, lambda _: frame.Close())

    frame.Show()
    app.MainLoop()


def main():
    try:
        # Large boards take a while to serialise; the default 2 s timeout is too short.
        board = KiCad(timeout_ms=60000).get_board()
    except Exception as e:
        sys.exit(f"kicad-splitplane: no open board found via the KiCad API ({e}).\n"
                 "Open a board in the PCB editor and enable Preferences > Plugins > Enable KiCad API.")
    run_dialog(board)


if __name__ == "__main__":
    main()
