"""Rig Deck as a tray application, so nobody has to keep a console open.

The icon says at a glance what the state is:

    grey    the game is not running
    amber   the game is running, no tablet has connected
    green   the game is running and the tablet is on

Right-click for the pairing page and the button sheet.

Deliberately nothing that starts itself with Windows: this machine already has enough
running at logon, and the panel is only wanted when a game is.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser

from . import config as cfgmod
from . import controls as controlsmod
from .app import RigDeck
from .wsserver import lan_addresses
from .trayicon import MF_GRAYED, TrayIcon, claim_single_instance, write_ico

GREY = (0x8C, 0x83, 0x78)
AMBER = (0xFF, 0xB4, 0x3D)
GREEN = (0x5F, 0xD3, 0x8D)

ID_PAIR, ID_PANEL, ID_SHEET, ID_QUIT, ID_RESCAN = 1, 2, 3, 4, 5
# One id per game/map the menu offers; handed out from here as the list is discovered.
ID_EXPORT_FIRST = 100

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPORT_SCRIPT = os.path.join(ROOT, "export_maps.ps1")
MAPS_CACHE = os.path.join(ROOT, "tools", "mapexport", "maps.json")

GAME_TITLES = {"ETS2": "Euro Truck Simulator 2", "ATS": "American Truck Simulator"}

# The .mbd names as the game files spell them. Anything not listed is title-cased,
# which is right often enough for a map mod nobody has met yet.
MAP_TITLES = {
    "europe": "Europe",
    "usa": "USA",
    "grandutopia": "Grand Utopia",
    "hungary": "Hungary",
    "promods": "ProMods",
}


def map_title(name: str) -> str:
    return MAP_TITLES.get(name.lower(), name.replace("_", " ").title())


class Tray:
    def __init__(self) -> None:
        self.deck = RigDeck()
        self.maps: dict = {}
        self._export_choices: dict = {}
        self._scanning = False
        self.load_maps()
        self.scan_maps()
        threading.Thread(target=self.deck._loop, daemon=True).start()
        self.deck.server.start_background()
        self.port = int(self.deck.cfg["port"])
        self.icon = TrayIcon("Rig Deck", self.command, self.menu,
                             on_tick=self.tick, on_double_click=self.open_pairing)
        self.tick()
        address = lan_addresses()[0]
        self.icon.notify("Rig Deck is running",
                         f"Panel at {address}:{self.port}. The icon is by the clock -- "
                         f"look under the arrow if you cannot see it.")

    # -- state ---------------------------------------------------------------
    def state(self):
        """A colour and a line of plain English, shared by the tooltip and the menu."""
        status = self.deck.status()
        clients = status["clients"]
        running = status.get("running")
        if not status["online"]:
            if running:
                # in a menu, so no telemetry -- but the buttons still reach the game
                return AMBER, f"{running} in a menu, no telemetry"
            return GREY, "Waiting for the game"
        game = status["game"]
        if clients:
            return GREEN, f"{game}, {clients} tablet{'s' if clients > 1 else ''} connected"
        return AMBER, f"{game}, no tablet connected"

    def tick(self) -> None:
        colour, text = self.state()
        self.icon.update(colour, f"Rig Deck -- {text}")

    # -- menu ----------------------------------------------------------------
    def menu(self) -> list:
        _, text = self.state()
        vjoy = self.deck.vjoy.status()
        if vjoy["available"]:
            vjoy_line = f"vJoy device {vjoy['device']}"
        elif self.deck.vjoy.auto_device:
            vjoy_line = "vJoy: asleep until a panel connects"
        else:
            vjoy_line = f"vJoy: {vjoy['reason']}"
        return [
            (0, text, MF_GRAYED),
            (0, vjoy_line, MF_GRAYED),
            None,
            (ID_PAIR, "Pair a tablet (QR code)", 0),
            (ID_PANEL, "Open the panel here", 0),
            (ID_SHEET, "Show the button sheet", 0),
            (self.export_menu(), "Export a map from the game files", 0),
            None,
            (ID_QUIT, "Quit", 0),
        ]

    def export_menu(self) -> list:
        """A submenu per installed game, listing the maps that game currently offers.

        Two games, and with a standalone map mod or two enabled, several maps each --
        and exporting the wrong one quietly draws the maps on top of each other. So
        the choice is made here, by name, instead of being left to a command line.
        """
        items = []
        self._export_choices = {}
        ident = ID_EXPORT_FIRST

        for key, maps in (self.maps or {}).items():
            entries = []
            for name in maps:
                self._export_choices[ident] = (key, name)
                entries.append((ident, map_title(name), 0))
                ident += 1
            if len(maps) > 1:
                self._export_choices[ident] = (key, None)
                entries.append(None)
                entries.append((ident, "All of them, merged", 0))
                ident += 1
            if entries:
                items.append((entries, GAME_TITLES.get(key, key), 0))

        if not items:
            items.append((0, "Looking for maps..." if self._scanning else "No games found",
                          MF_GRAYED))
        items.append(None)
        items.append((ID_RESCAN, "Look for maps again", MF_GRAYED if self._scanning else 0))
        return items

    def command(self, ident: int) -> None:
        if ident == ID_PAIR:
            self.open_pairing()
        elif ident == ID_PANEL:
            webbrowser.open(f"http://localhost:{self.port}/")
        elif ident == ID_SHEET:
            self.show_sheet()
        elif ident == ID_RESCAN:
            self.scan_maps()
        elif ident in self._export_choices:
            self.export_map(*self._export_choices[ident])
        elif ident == ID_QUIT:
            self.icon.quit()

    # -- maps ----------------------------------------------------------------
    def load_maps(self) -> None:
        """Last scan's answer, so the first right-click has something to show."""
        try:
            with open(MAPS_CACHE, encoding="utf-8") as fh:
                self.maps = json.load(fh)
        except (OSError, ValueError):
            self.maps = {}

    def scan_maps(self) -> None:
        """Ask the exporter which maps each game has, off the menu's thread.

        It only mounts the archives and lists one directory -- about a second -- but a
        menu that stalls even that long feels broken, and the answer changes only when
        the mods do. So it runs in the background and the menu draws what it last learned.
        """
        if self._scanning:
            return
        self._scanning = True
        threading.Thread(target=self._scan, daemon=True).start()

    def _scan(self) -> None:
        try:
            out = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", EXPORT_SCRIPT, "-ListMaps"],
                capture_output=True, text=True, timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            # Warnings about a missing game.log share this stream; the JSON is the one
            # line that starts with a brace.
            line = next(l for l in out.splitlines() if l.strip().startswith("{"))
            maps = json.loads(line)
            self.maps = {k: v for k, v in maps.items() if v}
            os.makedirs(os.path.dirname(MAPS_CACHE), exist_ok=True)
            with open(MAPS_CACHE, "w", encoding="utf-8") as fh:
                json.dump(self.maps, fh, indent=2)
        except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
            pass          # keep whatever the menu had; the rescan entry can try again
        finally:
            self._scanning = False

    def open_pairing(self) -> None:
        webbrowser.open(f"http://localhost:{self.port}/pair.html")

    def export_map(self, game: str, name: str = None) -> None:
        """Re-read the road network out of the game files, in a window you can watch.

        Not automatic and not silent. It takes minutes, it wants the game's mod list to
        have been written by a recent start, and it replaces the map the panel is
        drawing from -- none of which should happen behind a driver's back. The panel
        says when it is worth doing; this is the button for doing it.
        """
        command = ["powershell.exe", "-NoExit", "-ExecutionPolicy", "Bypass",
                   "-File", EXPORT_SCRIPT, "-Game", game]
        if name:
            command += ["-Map", name]
        subprocess.Popen(command,
                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))

    def show_sheet(self) -> None:
        """The vJoy buttons, in a text file Notepad can hold open beside the game."""
        lines = ["Rig Deck -- bind these once in Options -> Controls (vJoy device 1)",
                 "The right-hand column is the entry to look for in the game's own list.", ""]
        for row in controlsmod.binding_sheet():
            note = f"   [sent as the {row['key']} key -- no binding needed]" if row["key"] else ""
            lines.append(f"  Button {row['button']:>2}   {row['label']:<20} {row['game']}{note}")
        lines += ["", "Esc and Enter are keystrokes and appear on no button.",
                  "",
                  "Binding these by hand is thirty-two chances to slip a row, and one slip",
                  "puts every binding after it on the wrong button. With the game closed,",
                  "   python bindwrite.py --write",
                  "writes the whole list into the profile instead, and keeps a backup."]
        path = os.path.join(tempfile.gettempdir(), "rigdeck-buttons.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        subprocess.Popen(["notepad.exe", path])

    def run(self) -> None:
        try:
            self.icon.loop()
        finally:
            self.deck._stop.set()
            self.deck.vjoy.stop()
            self.deck.reader.close()


def main() -> int:
    if "--write-ico" in sys.argv:
        write_ico(sys.argv[sys.argv.index("--write-ico") + 1], AMBER)
        return 0
    # Double-clicking the shortcut twice should not leave two servers arguing over the
    # port. Held for the life of the process, released by Windows when it exits.
    global _instance
    _instance = claim_single_instance("Local\\RigDeckTray")
    if _instance is None:
        return 0
    cfgmod.load()
    Tray().run()
    return 0


_instance = None
