# ChurchLight Presenter

**A free church presenter that works with OBS and XSplit.** Songs, Bible verses, slideshows, announcements and a lower third for your livestream, run by a volunteer from a single browser window. No subscription, no account, no cloud.

ChurchLight is built for small, Bible-centered churches that want a polished service and a livestream without paying monthly fees or hiring a technician. It is free software under the AGPL-3.0 license.

> **Status: alpha.** It runs real services on a church computer and is young. Back up your data, keep a copy of your slides elsewhere, and please report anything that surprises you.

## What it does

- **Songs.** Type or paste lyrics in a plain-text editor, set the order of parts, and import OpenLyrics song files in bulk.
- **Bible.** Type a reference such as `jn 3:16-18` and the verses go up, over a slide or on their own. Import Bibles from Zefania XML or CSV.
- **Slideshows.** Turn a PowerPoint, Google Slides, Keynote or Impress file into a slideshow by exporting a PDF (a step-by-step guide is built in). Add speaker notes, timing, transitions and sound, and put videos in a slideshow.
- **Several screens at once.** A projector for the room, a stage screen for the singers and speaker, and a confidence monitor, each showing what it should. Speaker notes never reach the room.
- **Livestream lower third.** The words and verses appear over your camera as a see-through web page you add to OBS (or XSplit) as a browser source. The other screens are captured from their monitors.
- **Themes and alerts.** Fonts, colors and backgrounds per screen, plus countdown timers, clocks and alert bars with their own looks.
- **Run page built for volunteers.** One service list, big Next/Back controls, a verse tray, blank and hide-the-words buttons, keyboard shortcuts, and Undo for removed items.
- **A "Ready to start?" check.** Before a service it tells you in plain words whether the screens, monitors, Bible, backup and OBS connection are in place, and gives a button to fix each one.
- **Setup guide.** A step-by-step guide for the first run: your church, your monitors, OBS, and your first songs, Bible and slides. It can make a practice service.
- **Remote and clicker options.** Trigger links for stream-deck style buttons, and an optional clicker helper for presentation remotes.
- **Backups.** One-button backup and restore of your songs and services.

## Quick start (Windows)

1. Install **Python 3.10 or newer** from python.org. On the first installer screen tick **"Add python.exe to PATH"**. (If Python is missing, the start file explains this and opens the download page.)
2. Download this repository (Code, then Download ZIP, or use a release) and unzip it somewhere permanent, for example `C:\ChurchLight`.
3. Double-click **`start_churchlight.bat`**. A black window starts minimized and the ChurchLight operator window opens.
4. Follow the **setup guide**. It walks you through choosing your monitors and connecting OBS or XSplit, and you can make a practice service to try it out.

Your songs, services and settings are kept beside the program in `churchlight.db`, and imported slide pictures in the `media` folder. Back these up. A log is written to `churchlight.log` in the same folder.

To stop ChurchLight, use the **Shut down** button in the operator window.

### Linux

```
./start_churchlight.sh
```

or run it directly: `python3 -m churchlight_presenter --db churchlight.db --media media --open`

Windows 11 is the main target and is where it has been tested on a real church computer. Linux is used for development. macOS has not been tried.

## Using it with OBS or XSplit

ChurchLight shows the projector, stage and confidence screens on your monitors by itself. Your broadcast software only needs to put them in your stream:

- **Room screens:** capture the monitor or window ChurchLight opened for them.
- **Lower third:** add a browser source with the address shown in the setup guide (Settings, Projector windows lists it). The page has a see-through background, so your camera shows behind the words.
- **If you do not use OBS:** tick "I don't need the OBS connection" in the setup guide.

## Requirements

- Python 3.10 or newer (tested on 3.11, 3.12 and 3.13). The program itself uses only the Python standard library.
- A modern browser engine (Chrome, Edge or similar) for the operator window and screens.
- One or more monitors or a projector for the room screens.
- Optional: OBS (with its WebSocket server) or XSplit for livestreaming.
- PDF import uses **pypdfium2**, which is included in the `vendor` folder.

## Keep it private

By default the operator page is only reachable from the computer it runs on. Starting the program with `--host` makes the operator page reachable from other computers on your network, and **the operator page has no login yet**, so only do that on a trusted network. Backups and the database contain the secret for the trigger links, so treat them as private. There is no automatic updating: update by downloading a new version.

## Help and feedback

Found something confusing or broken? Open an issue on the project page and say what you were doing, what you expected, and what happened.

## License

ChurchLight Presenter is released under the **GNU Affero General Public License v3.0 or later** (AGPL-3.0-or-later); see `LICENSE`. Third-party components that ship in `vendor/` keep their own licenses; see `NOTICES.md`.
