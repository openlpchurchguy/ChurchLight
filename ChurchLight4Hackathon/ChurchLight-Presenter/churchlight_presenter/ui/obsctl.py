# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""The operator's OBS actions: test the connection, make the scenes, open the projectors.

These talk over the network, so they never hold the controller's lock while they wait for OBS: they read the
saved settings (and write the result) under the lock, and do the talking outside it."""
import ipaddress
import json
import logging
import threading

from ..core import obssetup
from ..core.obsws import ObsClient, ObsError

log = logging.getLogger("churchlight_presenter.obs")

KEY = "obs"
DEFAULTS = {"host": "127.0.0.1", "port": 4455, "password": "", "monitors": {}, "auto": False, "remember": True}


def check_host(host):
    """Only this computer or a computer on the home/church network: the program never reaches out further."""
    host = (host or "").strip()
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as err:
        raise ObsError("Type OBS's address as numbers, such as 127.0.0.1 for this computer.") from err
    if not (address.is_loopback or address.is_private):
        raise ObsError("ChurchLight only connects to OBS on this computer or your own network.")
    return str(address)


def check_port(port):
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ObsError("The port must be a number from 1 to 65535 (OBS usually uses 4455).")
    return port


def load(c):
    with c.lock:
        try:
            raw = json.loads(c.db.get_setting(KEY) or "{}")
        except ValueError:
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    for name in DEFAULTS:
        if name in raw:
            out[name] = raw[name]
    if not isinstance(out["monitors"], dict):
        out["monitors"] = {}
    return out


def save(c, settings):
    with c.lock:
        c.db.set_setting(KEY, json.dumps({k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}))


def view(c):
    """What the page shows. The password never leaves the program."""
    s = load(c)
    return {"host": s["host"], "port": s["port"], "has_password": bool(s["password"]), "remember": bool(s["remember"]),
            "monitors": s["monitors"], "auto": bool(s["auto"])}


def _base_url(c):
    server = c._server
    if server is None:
        raise ObsError("The program's web server is not running")
    host, port = server.address
    return f"http://127.0.0.1:{port}"          # OBS reads the pages from this same computer


def _client(host, port, password):
    return ObsClient(check_host(host), check_port(port), password)


def connect(c, host, port, password=None):
    """Test the connection. Returns {"obs_version", "monitors", "scenes": the ChurchLight scenes already in OBS}."""
    saved = load(c)
    password = saved["password"] if password is None else password
    with _client(host, port, password) as client:
        scenes = [s["sceneName"] for s in client.request("GetSceneList").get("scenes", [])
                  if s["sceneName"].startswith(obssetup.PREFIX)]
        found = obssetup.monitors(client)
        return {"obs_version": client.version, "monitors": found, "scenes": scenes}


def setup(c, host, port, password, wanted, remember=True):
    """Make the scenes in OBS (safe to repeat) and remember how to reach OBS. Returns {"report", "monitors", "scenes"}."""
    saved = load(c)
    password = saved["password"] if password is None else password
    wanted = [k for k in (wanted or []) if k in obssetup.SCENE_KEYS]
    with c.lock:
        outputs = list(c.outputs)
        base = _base_url(c)
        positions = c.screen_positions()
    with _client(host, port, password) as client:
        report = obssetup.build_scenes(client, outputs, base, wanted or obssetup.SCENE_KEYS, positions)
        found = obssetup.monitors(client)
        scenes = [r[0] for r in obssetup.plan(outputs, wanted or obssetup.SCENE_KEYS)]
    saved.update({"host": check_host(host), "port": check_port(port),
                  "password": password if remember else "", "remember": bool(remember)})
    save(c, saved)
    return {"report": report, "monitors": found, "scenes": scenes}


def project(c, assignments, host=None, port=None, password=None, remember=None):
    """Open projectors. `assignments` is {scene: monitor index}; it is remembered for next time.

    A password given here (the one just typed in the guide) is used instead of the saved one, and replaces it when the
    church keeps its OBS password on this computer; so changing the password in OBS never needs the old one."""
    saved = load(c)
    clean = {}
    for scene, index in (assignments or {}).items():
        if isinstance(scene, str) and scene.startswith(obssetup.PREFIX) and isinstance(index, int) \
                and not isinstance(index, bool) and 0 <= index < 16:
            clean[scene] = index
    if not clean:
        raise ObsError("Choose a monitor for at least one screen.")
    host = saved["host"] if host is None else host
    port = saved["port"] if port is None else port
    typed = password is not None
    password = saved["password"] if password is None else password
    with _client(host, port, password) as client:
        report = obssetup.open_projectors(client, clean)
    saved["monitors"] = clean
    if typed:
        keep = saved["remember"] if remember is None else bool(remember)
        saved.update({"host": check_host(host), "port": check_port(port), "password": password if keep else "", "remember": keep})
    save(c, saved)
    return {"report": report}


def set_auto(c, on):
    saved = load(c)
    if on and not saved["monitors"]:
        raise ObsError("Choose and open the projectors once first, so ChurchLight knows which monitors to use.")
    saved["auto"] = bool(on)
    save(c, saved)


def auto_project(c):
    """At program start, when the church asked for it: project the saved scenes. Quiet if OBS is not open."""
    saved = load(c)
    if not saved["auto"] or not saved["monitors"]:
        return False
    try:
        project(c, saved["monitors"])
    except ObsError as err:
        log.info("OBS projectors were not opened at start: %s", err)
        return False
    return True


def auto_project_later(c, tries=6, wait=5.0):
    """Try in the background (OBS may still be starting); never holds up the program."""
    def run():
        import time
        for _ in range(tries):
            if auto_project(c):
                return
            time.sleep(wait)
    threading.Thread(target=run, name="obs-projectors", daemon=True).start()
