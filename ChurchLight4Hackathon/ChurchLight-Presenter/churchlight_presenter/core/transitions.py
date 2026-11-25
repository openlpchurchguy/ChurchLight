# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The ways one slide image can give way to the next, and the rules for choosing them.

A transition belongs to a slide deck (a PDF's pictures): every slide change into one of
its slides uses it. Only the projector output draws transitions (see `Layout.transitions`
in outputs.py); every other output changes instantly.

    none         the new slide simply appears
    dissolve     the new slide fades in over the old one
    circle_out   the new slide grows from a circle in the center
    circle_in    the old slide closes down to a circle in the center, uncovering the new one
    slide_*      the new slide moves in over the old one from an edge (slide_left comes in from the right
                 edge and moves left; slide_right, slide_up and slide_down likewise)
    split_*      the new slide opens outward from a center line: horizontal (up and down), vertical (left and
                 right), or from a diagonal line (diagonal_left "\\" or diagonal_right "/")
    wipe_*       the new slide is uncovered by an edge sweeping across (wipe_left sweeps toward the left)
    fade_black   the old slide fades to black, then the new one fades in from black
    fade_white   the same through white

The length is whole tenths of a second, from 0 to 2 seconds. Pure Python; no window.
"""
TRANSITIONS = {
    "none": "None",
    "dissolve": "Dissolve",
    "circle_out": "Circle wipe, out (grows from the center)",
    "circle_in": "Circle wipe, in (closes to the center)",
    "slide_left": "Slide left (new slide comes in from the right)",
    "slide_right": "Slide right (new slide comes in from the left)",
    "slide_up": "Slide up (new slide comes in from the bottom)",
    "slide_down": "Slide down (new slide comes in from the top)",
    "split_horizontal": "Split, horizontal (opens up and down from the middle)",
    "split_vertical": "Split, vertical (opens left and right from the middle)",
    "split_diagonal_left": "Split, diagonal left (opens from a \\ line)",
    "split_diagonal_right": "Split, diagonal right (opens from a / line)",
    "wipe_left": "Wipe left (sweeps from the right edge)",
    "wipe_right": "Wipe right (sweeps from the left edge)",
    "wipe_up": "Wipe up (sweeps from the bottom edge)",
    "wipe_down": "Wipe down (sweeps from the top edge)",
    "fade_black": "Fade to black",
    "fade_white": "Fade to white",
}
STEP_MS = 100
MAX_MS = 2000
DEFAULT_MS = 500


def check_transition(kind, milliseconds):
    """Return (kind, milliseconds) if they are acceptable; raise ValueError in plain words if not."""
    if kind not in TRANSITIONS:
        raise ValueError("The transition must be one of: " + ", ".join(TRANSITIONS))
    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int):
        raise ValueError("The transition time must be a whole number of milliseconds")
    if not 0 <= milliseconds <= MAX_MS:
        raise ValueError(f"The transition time must be between 0 and {MAX_MS / 1000:g} seconds")
    if milliseconds % STEP_MS:
        raise ValueError("The transition time goes up in steps of 0.1 second")
    return kind, milliseconds
