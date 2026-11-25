# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Command-line walkthrough. Run it from the project folder with:

    python -m churchlight_presenter.demo

It builds a small sample service in memory, prints the slide list, then plays
the scenario this project is designed around: a slideshow slide stays where it is
while a Bible verse is shown over it and hidden again, and every output shows
only what its layout allows. Placeholder text is used throughout, not real
song lyrics or Bible text.
"""
import json
import sys

from .core.database import DatabaseManager
from .core.items import ItemRef, add_to_service
from .core.live import LiveSession
from .core.outputs import Output, build_payload
from .core.slides import BuildSettings, SlideBuilder

# A native projector, a public livestream lower third, and a stage page with notes.
DEMO_OUTPUTS = (
    Output("projector", "projector", kind="native", screen=1),
    Output("verse-lower", "verse_lower"),
    Output("stage", "ppt_notes"),
)


def _build_sample_service(db):
    song = db.create_song_with_sections(
        "Demo Song",
        [("Verse 1", "verse", "\n".join(f"Sample verse line {n}" for n in range(1, 6))),
         ("Chorus", "chorus", "Sample chorus line 1\nSample chorus line 2")],
        order=["Verse 1", "Chorus"], author="Demo Author")
    translation = db.add_translation("DEMO", "Placeholder text, not a real translation")
    db.add_verses(translation, [
        ("John", 3, 16, "[Demo text for John 3:16] This placeholder is long enough that "
                        "the builder has to split it across two slides."),
        ("John", 3, 17, "[Demo text for John 3:17] A shorter placeholder."),
    ])
    deck = db.add_presentation("Demo Sermon", [
        ("demo/1.png", "Welcome everyone."),
        ("demo/2.png", "Main point. Mention John 3:16."),
        ("demo/3.png", None),
    ])
    service = db.create_service("Sunday Demo", "2026-10-25")
    add_to_service(db, service, ItemRef.song(song))
    add_to_service(db, service, ItemRef.announcement("Welcome", "Demo announcement text."))
    add_to_service(db, service, ItemRef.presentation(deck))
    return service


def run_demo():
    """Run the scenario and return (slides, steps).

    `steps` is a list of {"step": description, "payloads": {output name: payload}}
    taken after each action, so tests and the printout use the same data.
    """
    steps = []
    with DatabaseManager(":memory:") as db:
        service = _build_sample_service(db)
        builder = SlideBuilder(db, BuildSettings(scripture_max_chars=70))
        slides = builder.build_service(service)
        session = LiveSession(slides)

        def record(description):
            state = session.state()
            steps.append({"step": description,
                          "payloads": {o.name: build_payload(state, o) for o in DEMO_OUTPUTS}})

        record("Service loaded; nothing is live yet")
        deck_start = next(i for i, s in enumerate(slides) if s.item_type == "presentation")
        session.go_to(deck_start + 1)
        record("Deck slide 2 goes live")
        verse = builder.build_item(ItemRef.from_reference(db, "DEMO", "jn 3:16-17"))
        session.show_overlay(verse)
        record("Typed 'jn 3:16-17': the verse appears over the slideshow, which does not move")
        session.overlay_next()
        record("Next piece of the long verse (still verse 16, marked continued)")
        session.overlay_next()
        record("On to verse 17")
        session.hide_overlay()
        record("Verse hidden; the slideshow slide is still there")
        session.next()
        record("The slideshow moves on to slide 3")
    return slides, steps


def _layer(layer):
    if layer is None:
        return "-"
    text = f'{layer["kind"]} "{layer["reference"] or ""}"'
    if layer["label"]:
        text += f' [{layer["label"]}]'
    extra = []
    if layer["verse_numbers"]:
        extra.append("verse " + ",".join(map(str, layer["verse_numbers"])))
    if layer["continued"]:
        extra.append("continued")
    if layer.get("notes"):
        extra.append(f'notes: "{layer["notes"]}"')
    return text + (f' ({"; ".join(extra)})' if extra else "")


def main(out=None):
    out = out or sys.stdout

    def say(text=""):
        print(text, file=out)

    slides, steps = run_demo()

    say("SLIDES BUILT FOR THE SERVICE")
    say(f'{"#":>3}  {"item":<12} {"label":<14} {"reference":<12} text / image')
    for i, s in enumerate(slides):
        detail = s.text.replace("\n", " / ") if s.text else (s.image_path or "")
        say(f"{i:>3}  {s.item_type:<12} {str(s.label or ''):<14} "
            f"{str(s.reference or ''):<12} {detail[:46]}")

    say()
    say("WHAT EACH OUTPUT RECEIVES, STEP BY STEP")
    for n, step in enumerate(steps, start=1):
        say(f"\n{n}. {step['step']}")
        for name, payload in step["payloads"].items():
            say(f"   {name:<12} base: {_layer(payload['base'])}")
            say(f"   {'':<12} overlay: {_layer(payload['overlay'])}")
            if "next" in payload:
                say(f"   {'':<12} next: {_layer(payload['next'])}")

    verse_step = steps[2]["payloads"]
    say()
    say("WHAT CROSSES THE LINE (step 3)")
    say("  projector base fields: " + ", ".join(sorted(steps[1]["payloads"]["projector"]["base"])))
    say("  stage base fields:     " + ", ".join(sorted(steps[1]["payloads"]["stage"]["base"])))
    say("  (notes appear only in the stage's list; public outputs never receive them)")
    say()
    say("THE STAGE PAYLOAD AT STEP 3, EXACTLY AS A WEB PAGE WOULD RECEIVE IT")
    say(json.dumps(verse_step["stage"], indent=2))


if __name__ == "__main__":
    main()
