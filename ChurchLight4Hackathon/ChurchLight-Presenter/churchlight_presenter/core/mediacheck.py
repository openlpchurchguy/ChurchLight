# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) ChurchLight Presenter contributors
"""Look inside an MP4 before it is accepted, so a video that browsers, OBS or XSplit cannot show never gets on screen.

ChurchLight plays video in a web page. Pages play H.264 video (8-bit) with AAC sound reliably; other formats that hide inside
an ".mp4" name (H.265/HEVC, AV1, 10-bit video, ProRes ...) may play in one program and freeze or stay black in another.
Only the movie header is read (a few kilobytes), never the picture data. Anything that cannot be read as an MP4 is let through
here: the checks only ever turn away a file that is known to be a problem.
"""
import struct
from pathlib import Path

H264 = (b"avc1", b"avc3")
OTHER_PLAYABLE = (b"vp09", b"vp08")                      # VP8/VP9 play in Edge, Chrome and the OBS/XSplit browser
PLAYABLE_PROFILES = {66: "Baseline", 77: "Main", 88: "Extended", 100: "High"}      # 110 (10-bit), 122 (4:2:2), 244 (4:4:4) are not
PLAYABLE_SOUND = (b"mp4a", b"Opus", b"fLaC", b".mp3")
CODEC_NAMES = {
    b"hvc1": "H.265 (HEVC)", b"hev1": "H.265 (HEVC)", b"dvh1": "H.265 (HEVC, Dolby Vision)", b"dvhe": "H.265 (HEVC, Dolby Vision)",
    b"av01": "AV1", b"mp4v": "older MPEG-4", b"s263": "H.263",
    b"apch": "ProRes", b"apcn": "ProRes", b"apcs": "ProRes", b"apco": "ProRes", b"ap4h": "ProRes", b"ap4x": "ProRes",
    b"jpeg": "Motion JPEG", b"mjp2": "Motion JPEG 2000",
}
SOUND_NAMES = {b"ac-3": "Dolby AC-3", b"ec-3": "Dolby E-AC-3", b"dtsc": "DTS", b"dtsh": "DTS", b"alac": "Apple Lossless (ALAC)",
               b"samr": "AMR", b"sowt": "uncompressed", b"twos": "uncompressed", b"lpcm": "uncompressed"}

FIX = ("To fix it: install HandBrake (free, at handbrake.fr), open this video in it, choose the \"Fast 1080p30\" preset, "
       "check that Format says MP4, press Start, then add the new file here instead.")


def _boxes(data, start=0, end=None):
    """The (kind, body start, body end) of every box in data[start:end]."""
    end = len(data) if end is None else end
    position = start
    while position + 8 <= end:
        size, kind = struct.unpack(">I4s", data[position:position + 8])
        header = 8
        if size == 1 and position + 16 <= end:
            size, header = struct.unpack(">Q", data[position + 8:position + 16])[0], 16
        elif size == 0:
            size = end - position
        if size < header:
            return
        yield kind, position + header, min(position + size, end)
        position += size


def _find(data, start, end, *path):
    for kind, body, stop in _boxes(data, start, end):
        if kind == path[0]:
            if len(path) == 1:
                return body, stop
            found = _find(data, body, stop, *path[1:])
            if found:
                return found
    return None


def _movie_header(path, limit=64 * 1024 ** 2):
    """The bytes of the file's "moov" box, wherever it sits (the start for a web-ready file, the end for many cameras)."""
    size_of_file = Path(path).stat().st_size
    with open(path, "rb") as handle:
        position = 0
        while position + 8 <= size_of_file:
            handle.seek(position)
            header = handle.read(8)
            size, kind = int.from_bytes(header[:4], "big"), header[4:8]
            if size == 1:
                size = int.from_bytes(handle.read(8), "big")
            elif size == 0:
                size = size_of_file - position
            if size < 8:
                return None
            if kind == b"moov":
                handle.seek(position + 8)
                return handle.read(min(size - 8, limit))
            position += size
    return None


def inspect_mp4(path):
    """{"video": {...} or None, "sound": {...} or None} from the movie header; None when it cannot be read as an MP4."""
    try:
        moov = _movie_header(path)
    except (OSError, struct.error, ValueError):
        return None
    if not moov:
        return None
    info = {"video": None, "sound": None}
    try:
        for kind, body, stop in _boxes(moov):
            if kind != b"trak":
                continue
            handler = _find(moov, body, stop, b"mdia", b"hdlr")
            stsd = _find(moov, body, stop, b"mdia", b"minf", b"stbl", b"stsd")
            if not handler or not stsd:
                continue
            what = moov[handler[0] + 8:handler[0] + 12]
            entries = list(_boxes(moov, stsd[0] + 8, stsd[1]))          # skips the version/flags and the entry count
            if not entries:
                continue
            entry, e_body, e_stop = entries[0]
            if what == b"vide" and info["video"] is None:
                video = {"codec": entry, "width": 0, "height": 0, "profile": None, "fps": None}
                if e_body + 28 <= e_stop:
                    video["width"], video["height"] = struct.unpack(">HH", moov[e_body + 24:e_body + 28])
                if entry in H264:
                    for sub, s_body, s_stop in _boxes(moov, e_body + 78, e_stop):
                        if sub == b"avcC" and s_body + 2 <= s_stop:
                            video["profile"] = moov[s_body + 1]
                mdhd = _find(moov, body, stop, b"mdia", b"mdhd")
                stsz = _find(moov, body, stop, b"mdia", b"minf", b"stbl", b"stsz")
                if mdhd and stsz:
                    version = moov[mdhd[0]]
                    scale, length = (struct.unpack(">II", moov[mdhd[0] + 12:mdhd[0] + 20]) if version == 0
                                     else (struct.unpack(">I", moov[mdhd[0] + 20:mdhd[0] + 24])[0],
                                           struct.unpack(">Q", moov[mdhd[0] + 24:mdhd[0] + 32])[0]))
                    count = struct.unpack(">I", moov[stsz[0] + 8:stsz[0] + 12])[0]
                    if scale and length and count:
                        video["fps"] = count * scale / length
                info["video"] = video
            elif what == b"soun" and info["sound"] is None:
                info["sound"] = {"codec": entry}
    except (struct.error, IndexError):
        return None
    return info


def check_video(path, seconds=None):
    """(problem, warnings) for a video file: `problem` is a plain sentence naming what is wrong and how to fix it, or None.

    `warnings` are things that will probably still play but may stutter on a busy computer."""
    info = inspect_mp4(path)
    if info is None:
        return None, []
    video, sound = info["video"], info["sound"]
    if video is None:
        if sound is None:
            return None, []                     # the header was not understood: let the file through rather than turn away a good one
        return "This file has no picture in it. Use an MP4 video (H.264).", []
    if video["codec"] not in H264 + OTHER_PLAYABLE:
        name = CODEC_NAMES.get(video["codec"]) or "a format other than H.264"
        return (f"This video is {name}. Most browsers, OBS and XSplit cannot show it, so the picture would freeze or stay "
                f"black. {FIX}"), []
    if video["profile"] is not None and video["profile"] not in PLAYABLE_PROFILES:
        return ("This video is H.264 but in a 10-bit or high-colour version that many browsers, OBS and XSplit cannot show, so "
                f"the picture would freeze or stay black. {FIX}"), []
    if sound is not None and sound["codec"] not in PLAYABLE_SOUND:
        name = SOUND_NAMES.get(sound["codec"]) or "a format other than AAC"
        return f"The sound in this video is {name}, which browsers cannot play, so it would play with no sound. {FIX}", []
    warnings = []
    if video["width"] > 1920 or video["height"] > 1088:
        warnings.append(f"This video is larger than 1080p ({video['width']}×{video['height']}) and may stutter on a busy computer. "
                        "Re-saving it at 1080p with HandBrake's \"Fast 1080p30\" preset makes it smoother.")
    if video["fps"] and video["fps"] > 30.5:
        warnings.append(f"This video runs at {video['fps']:.0f} frames per second and may stutter on a busy computer. "
                        "Re-saving it with HandBrake's \"Fast 1080p30\" preset makes it smoother.")
    mbps = _megabits(path, seconds)
    if mbps and mbps > HIGH_MBPS:
        warnings.append(f"This video uses a lot of data ({mbps:.0f} Mbit/s) and may stutter on an older computer. "
                        "Re-saving it with HandBrake's \"Fast 1080p30\" preset usually makes it several times lighter.")
    return None, warnings


HIGH_MBPS = 20          # a 1080p video re-saved with HandBrake's "Fast 1080p30" is roughly 5 to 10


def _megabits(path, seconds):
    """Average data rate in megabits a second, from the file's size and length (None when the length is not known)."""
    try:
        if seconds and seconds > 0:
            return Path(path).stat().st_size * 8 / seconds / 1_000_000
    except OSError:
        pass
    return None


def describe_video(path, seconds=None):
    """A short line about a video for the library, such as "1920×1080 · 30 fps · H.264 · 8 Mbit/s" ("" if unreadable)."""
    info = inspect_mp4(path)
    video = info and info["video"]
    if not video:
        return ""
    codec = ("H.264" if video["codec"] in H264 else CODEC_NAMES.get(video["codec"])
             or {b"vp09": "VP9", b"vp08": "VP8"}.get(video["codec"]) or video["codec"].decode("latin-1", "replace").strip())
    parts = []
    if video["width"] and video["height"]:
        parts.append(f"{video['width']}×{video['height']}")
    if video["fps"]:
        parts.append(f"{video['fps']:.0f} fps")
    parts.append(codec)
    mbps = _megabits(path, seconds)
    if mbps:
        parts.append(f"{mbps:.0f} Mbit/s")
    return " · ".join(parts)
