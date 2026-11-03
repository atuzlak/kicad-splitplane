"""Synthetic board with the situations that broke 0.3.1 on a real course
project: a pin header across the board (signal pins 2.54 mm apart leave no
room for copper between them), a supply pin inside that header with another
net's vias right next to it, and five supply nets on one layer."""

NETS = ["5V", "3.3V", "VUSB", "VDDA", "+4.096V"]


def _header(x, y, nets, ref):
    pads = []
    for i, net in enumerate(nets):
        shape = "rect" if i == 0 else "circle"
        net_tok = f' (net "{net}")' if net else ""
        pads.append(f'(pad "{i + 1}" thru_hole {shape} (at {i * 2.54:.2f} 0) (size 1.7 1.7) '
                    f'(drill 1) (layers "*.Cu" "*.Mask"){net_tok})')
    return f'(footprint "Header_{ref}" (layer "F.Cu") (at {x} {y} 0)\n  ' + "\n  ".join(pads) + ")\n"


def _via(x, y, net):
    return f'(via (at {x} {y}) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net "{net}"))\n'


def board_text():
    parts = ['(kicad_pcb (version 20240108) (generator "stress_board")\n',
             '(layers (0 "F.Cu" signal) (1 "In1.Cu" power) (2 "In2.Cu" power) '
             '(31 "B.Cu" signal) (44 "Edge.Cuts" user))\n',
             '(gr_rect (start 0 0) (end 80 60) (layer "Edge.Cuts"))\n']
    # Header across the middle: pin 1 is 3.3V, the rest are signals.
    parts.append(_header(8, 30, ["3.3V"] + [f"SIG{i}" for i in range(2, 22)], "J1"))
    # A second, short header with a 5V pin among signals.
    parts.append(_header(55, 12, ["5V", "SIG30", "SIG31", "SIG32"], "J2"))
    vias = {
        # VDDA on both sides of the long header, right next to its 3.3V pin.
        "VDDA": [(12, 26), (12, 34), (20, 25), (20, 35), (30, 24), (30, 36)],
        "3.3V": [(6, 24), (4, 36), (8, 44), (40, 45), (70, 40), (60, 50)],
        "5V": [(50, 6), (62, 6), (72, 20), (74, 30)],
        "VUSB": [(45, 20), (50, 22), (38, 18)],
        "+4.096V": [(10, 10), (20, 8), (30, 12)],
    }
    for net, pts in vias.items():
        parts += [_via(x, y, net) for x, y in pts]
    # A few GND vias spread over the plane.
    parts += [_via(x, y, "GND") for x, y in [(25, 50), (45, 32), (65, 28), (15, 18), (35, 6)]]
    parts.append(")\n")
    return "".join(parts)
