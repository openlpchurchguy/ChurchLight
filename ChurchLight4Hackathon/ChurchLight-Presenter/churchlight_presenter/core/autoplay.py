# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Timed slide advance for decks: how long each slide stays, and what happens after the last one.

A deck has a default (`auto_seconds`, `end_action`); a service item may override both.
`auto_seconds` 0 means manual: the operator presses Next. Pure Python; no timers here (the
controller owns those), just the rules.

    stop       stay on the last slide
    loop       go back to the deck's first slide and keep going
    blank      blank the public screens
    next_item  carry on into whatever follows in the service (stops if nothing follows)
"""
END_ACTIONS = {
    "stop": "Stay on the last slide",
    "loop": "Loop back to the first slide",
    "blank": "Blank the screens",
    "next_item": "Move on to the next service item",
}
MAX_SECONDS = 600
OVERRIDE_KEYS = ("auto_seconds", "end_action")


def check_autoplay(seconds, end_action):
    """Return (seconds, end_action) if acceptable; raise ValueError in plain words if not."""
    if isinstance(seconds, bool) or not isinstance(seconds, int):
        raise ValueError("The time per slide must be a whole number of seconds (0 for manual)")
    if not 0 <= seconds <= MAX_SECONDS:
        raise ValueError(f"The time per slide must be between 0 and {MAX_SECONDS} seconds")
    if end_action not in END_ACTIONS:
        raise ValueError("The ending must be one of: " + ", ".join(END_ACTIONS))
    return seconds, end_action


def timing_for(deck_seconds, deck_end, overrides):
    """The timing a deck item uses: its own override if it has one, else the deck's setting."""
    seconds, end = deck_seconds, deck_end
    own = overrides or {}
    try:
        seconds, end = check_autoplay(own.get("auto_seconds", seconds), own.get("end_action", end))
    except ValueError:
        seconds, end = deck_seconds, deck_end          # a damaged override is ignored
    return seconds, end


def next_step(index, count, same_item_after, end_action):
    """What the timer does when the delay on slide `index` runs out.

    `same_item_after` says whether the next slide belongs to the same deck item. Returns one of
    "next" (advance one slide), "first" (jump to the deck's first slide), "blank" or "stop".
    """
    if index + 1 < count and same_item_after:
        return "next"
    if end_action == "loop":
        return "first"
    if end_action == "blank":
        return "blank"
    if end_action == "next_item" and index + 1 < count:
        return "next"
    return "stop"
