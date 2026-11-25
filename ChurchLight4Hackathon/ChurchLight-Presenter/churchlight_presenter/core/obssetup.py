# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Makes the scenes ChurchLight needs in OBS, and opens full-screen projectors on the chosen monitors.

Everything is named "ChurchLight ..." and is safe to run again: a scene or source that is already there is
kept (a browser source gets its address brought up to date), nothing of the church's own is touched or removed."""
import logging

from .obsws import ObsError
from .outputs import OVERLAY_LAYOUTS

log = logging.getLogger("churchlight_presenter.obs")

PREFIX = "ChurchLight "
SCENE_KEYS = ("projector", "livestream", "stage", "confidence", "home")
STAGE_LAYOUTS = ("stage", "ppt_notes")
PROJECTOR_LAYOUTS = ("projector", "ppt_verse", "song_full", "verse_full")
CAMERA_KINDS = ("dshow_input", "av_capture_input_v2", "av_capture_input", "v4l2_input")
COLOR_KINDS = ("color_source_v3", "color_source_v2", "color_source")
DISPLAY_KINDS = ("monitor_capture", "display_capture")      # Windows, macOS
BLACK = 0xFF000000          # OBS colors are ABGR


def source_name(output):
    return f"{PREFIX}{output.name}"


def plan(outputs, wanted=SCENE_KEYS):
    """[(scene name, key, [outputs shown in it])] for the scenes to make, from the configured outputs.

    Projector: the room projector. Livestream: the lower thirds over the camera. Stage: the stage screens. Confidence:
    the confidence monitor, a scene of its own. Home: a plain scene for before and after the service."""
    chosen = [k for k in SCENE_KEYS if k in wanted]
    rows = []
    if "projector" in chosen:
        shown = [o for o in outputs if o.layout in PROJECTOR_LAYOUTS][:1] or []
        if shown:
            rows.append((PREFIX + "Projector", "projector", shown))
    if "livestream" in chosen:
        shown = [o for o in outputs if o.layout in OVERLAY_LAYOUTS]
        if shown:
            rows.append((PREFIX + "Livestream", "livestream", shown))
    if "stage" in chosen:
        shown = [o for o in outputs if o.layout in STAGE_LAYOUTS]
        if shown:
            rows.append((PREFIX + "Stage", "stage", shown))
    if "confidence" in chosen:
        for o in outputs:
            if o.layout == "confidence":
                rows.append((PREFIX + "Confidence", "confidence", [o]))
                break
    if "home" in chosen:
        rows.append((PREFIX + "Home", "home", []))
    return rows


def _pick(kinds, preferred):
    return next((k for k in preferred if k in kinds), None)


def _scene_item_names(client, scene):
    items = client.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])
    return {i["sourceName"]: i for i in items}


def build_scenes(client, outputs, base_url, wanted=SCENE_KEYS, positions=None):
    """Make the scenes. Only the livestream scene uses a browser source (the lower third over the camera); the room,
    stage and confidence scenes capture the monitor ChurchLight put that screen on. `positions` is {output name: (x, y)}. Returns a list of plain-language lines saying what was done."""
    report = []
    base_url = base_url.rstrip("/")
    video = client.request("GetVideoSettings")
    width, height = int(video.get("baseWidth") or 1920), int(video.get("baseHeight") or 1080)
    scenes = {s["sceneName"] for s in client.request("GetSceneList").get("scenes", [])}
    inputs = {i["inputName"]: i.get("inputKind", "") for i in client.request("GetInputList").get("inputs", [])}
    kinds = client.request("GetInputKindList").get("inputKinds", [])
    rows = plan(outputs, wanted)
    if not rows:
        raise ObsError("There is nothing to make: choose at least one scene.")
    for scene, key, shown in rows:
        if scene in scenes:
            report.append(f"Scene \"{scene}\" was already there.")
        else:
            client.request("CreateScene", {"sceneName": scene})
            scenes.add(scene)
            report.append(f"Made scene \"{scene}\".")
        present = _scene_item_names(client, scene)
        if key == "livestream":                                     # the camera goes underneath the lower third
            camera_kind = _pick(kinds, CAMERA_KINDS)
            name = PREFIX + "Camera"
            if camera_kind and name not in present:
                try:
                    if name not in inputs:
                        client.request("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": camera_kind,
                                                       "inputSettings": {}, "sceneItemEnabled": True})
                        inputs[name] = camera_kind
                    else:
                        client.request("CreateSceneItem", {"sceneName": scene, "sourceName": name, "sceneItemEnabled": True})
                    report.append(f"Added \"{name}\" to {scene}. Open its properties in OBS and choose your camera.")
                except ObsError as err:
                    report.append(f"Could not add a camera source ({err}). Add your camera to {scene} yourself.")
                present = _scene_item_names(client, scene)
        if key == "home" and PREFIX + "Black" not in present:
            color = _pick(kinds, COLOR_KINDS)
            if color:
                name = PREFIX + "Black"
                settings = {"color": BLACK, "width": width, "height": height}
                if name in inputs:
                    client.request("CreateSceneItem", {"sceneName": scene, "sourceName": name, "sceneItemEnabled": True})
                else:
                    client.request("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": color,
                                                   "inputSettings": settings, "sceneItemEnabled": True})
                    inputs[name] = color
                report.append(f"Added a black background to {scene}. Put your logo on it if you like.")
        for output in shown:
            if key != "livestream":
                report.extend(_capture_screen(client, scene, output, present, inputs, kinds, positions or {}, width, height))
                continue
            name = source_name(output)
            settings = {"url": f"{base_url}/view/{output.name}", "width": width, "height": height,
                        "shutdown": False, "restart_when_active": False}
            if name in inputs:
                if inputs[name] != "browser_source":
                    report.append(f"\"{name}\" already exists in OBS and is not a browser source, so it was left alone.")
                    continue
                client.request("SetInputSettings", {"inputName": name, "inputSettings": settings, "overlay": True})
                if name not in present:
                    client.request("CreateSceneItem", {"sceneName": scene, "sourceName": name, "sceneItemEnabled": True})
                    report.append(f"Added \"{name}\" to {scene}.")
                else:
                    report.append(f"\"{name}\" was already in {scene}; its address is up to date.")
            else:
                client.request("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": "browser_source",
                                               "inputSettings": settings, "sceneItemEnabled": True})
                inputs[name] = "browser_source"
                report.append(f"Added the browser source \"{name}\" to {scene}.")
        if key == "livestream":
            report.extend(_lower_third_on_top(client, scene, [source_name(o) for o in shown]))
    return report


def _capture_screen(client, scene, output, present, inputs, kinds, positions, width, height):
    """Put a capture of the monitor ChurchLight shows this screen on into the scene (the screen is a window of ChurchLight's
    own, not a browser source). Returns report lines."""
    name = f"{PREFIX}{output.name} screen"
    kind = _pick(kinds, DISPLAY_KINDS)
    if kind is None:
        return [f"This OBS has no display capture, so \"{output.name}\" was not added to {scene}. Add a Display Capture of that monitor yourself."]
    if output.name not in positions:
        return [f"\"{output.name}\" has no monitor yet, so {scene} has no screen in it. Choose its monitor in the step before this one, then make the scenes again."]
    if name in inputs and inputs[name] != kind:
        return [f"\"{name}\" already exists in OBS and is not a display capture, so it was left alone."]
    created = name not in inputs
    if created:
        reply = client.request("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": kind,
                                               "inputSettings": {"capture_cursor": False}, "sceneItemEnabled": True})
        inputs[name] = kind
    elif name not in present:
        reply = client.request("CreateSceneItem", {"sceneName": scene, "sourceName": name, "sceneItemEnabled": True})
    else:
        reply = None
    lines = []
    if created or reply is not None:
        lines.append(f"Added a capture of the {output.name} screen to {scene}.")
    else:
        lines.append(f"The {output.name} screen capture was already in {scene}; it points at the right monitor again.")
    _point_capture_at(client, name, kind, positions[output.name])
    item_id = (reply or {}).get("sceneItemId")
    if item_id is not None:
        try:
            client.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": item_id, "sceneItemTransform": {
                "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": width, "boundsHeight": height,
                "positionX": 0, "positionY": 0, "boundsAlignment": 0}})
        except ObsError:
            pass
    return lines


def _point_capture_at(client, name, kind, point):
    """Choose the monitor whose top-left corner is `point` in the capture's own list ("... @ 1920,0"). Quiet if OBS cannot."""
    try:
        items = client.request("GetInputPropertiesListPropertyItems",
                               {"inputName": name, "propertyName": "monitor_id"}).get("propertyItems", [])
        mark = f"@ {int(point[0])},{int(point[1])}"
        found = next((i for i in items if mark in str(i.get("itemName", ""))), None)
        if found is not None:
            client.request("SetInputSettings", {"inputName": name, "inputSettings": {"monitor_id": found["itemValue"]}, "overlay": True})
    except (ObsError, KeyError, TypeError, ValueError):
        log.debug("Could not pick the monitor for %s", name, exc_info=True)


def _lower_third_on_top(client, scene, overlays):
    """Make sure the lower third sits on a layer above the camera, even if the scene was arranged differently before.
    In OBS the item with the highest index is on top. Only the lower third moves; nothing else is touched."""
    items = client.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])
    camera = next((i for i in items if i["sourceName"] == PREFIX + "Camera"), None)
    report = []
    if camera is None:
        return report
    for name in overlays:
        items = client.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])
        camera = next((i for i in items if i["sourceName"] == PREFIX + "Camera"), camera)
        mine = next((i for i in items if i["sourceName"] == name), None)
        if mine and mine["sceneItemIndex"] < camera["sceneItemIndex"]:
            client.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": mine["sceneItemId"],
                                                 "sceneItemIndex": len(items) - 1})
            report.append(f"Moved \"{name}\" above the camera in {scene}.")
    return report


def monitors(client):
    """[{"index", "name", "width", "height", "x", "y"}] of the screens OBS can project on (x and y are None if OBS does not say)."""
    out = []
    for m in client.request("GetMonitorList").get("monitors", []):
        out.append({"index": int(m.get("monitorIndex", 0)),
                    "name": str(m.get("monitorName") or f"Screen {m.get('monitorIndex', 0) + 1}"),
                    "width": int(m.get("monitorWidth") or 0), "height": int(m.get("monitorHeight") or 0),
                    "x": m.get("monitorPositionX"), "y": m.get("monitorPositionY")})
    return out


def open_projectors(client, assignments):
    """Open a full-screen OBS projector of each scene on its monitor. `assignments` is {scene name: monitor index}.
    Returns lines saying what was done. A scene OBS does not have is skipped with a reason."""
    scenes = {s["sceneName"] for s in client.request("GetSceneList").get("scenes", [])}
    report = []
    for scene, index in assignments.items():
        if scene not in scenes:
            report.append(f"Scene \"{scene}\" is not in OBS yet, so it was not projected. Make the scenes first.")
            continue
        client.request("OpenSourceProjector", {"sourceName": scene, "monitorIndex": int(index)})
        report.append(f"Projected \"{scene}\" full screen on monitor {int(index) + 1}.")
    return report
