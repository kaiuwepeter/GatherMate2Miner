#!/usr/bin/env python3
"""
GatherMate2 Miner GUI
A graphical interface for mining node data from Wowhead for GatherMate2.
"""

import requests
import re
import json
from dataclasses import dataclass
import typing
import math
import html
import csv
import time
import os
import sys
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext


class ToolTip:
    """Simple tooltip class for tkinter widgets."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tipwindow = None
        widget.bind("<Enter>", self.show_tip)
        widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        if self.tipwindow or not self.text:
            return
        x, y, _, cy = self.widget.bbox("insert") if hasattr(self.widget, 'bbox') else (0, 0, 0, 0)
        x = x + self.widget.winfo_rootx() + 25
        y = y + cy + self.widget.winfo_rooty() + 25
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background="#ffffe0", relief=tk.SOLID, borderwidth=1,
                         font=("Helvetica", 9))
        label.pack(ipadx=3, ipady=2)

    def hide_tip(self, event=None):
        if self.tipwindow:
            self.tipwindow.destroy()
            self.tipwindow = None


# Headers to avoid being blocked by Wowhead
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# Global variable for logging callback
_log_callback = None

def set_log_callback(callback):
    global _log_callback
    _log_callback = callback

def log(message):
    global _log_callback
    if _log_callback:
        _log_callback(message + "\n")
    else:
        print(message)


# ========================= CORE CLASSES =========================

@dataclass
class WowheadObject:
    name: str
    ids: typing.List[str]
    coordinates: dict
    gathermate_id: str
    use_beta: bool = False

    def __init__(self, name: str, ids: typing.List[str], gathermate_id: str, use_beta: bool = False):
        self.name = name
        self.ids = ids
        self.coordinates = dict()
        self.gathermate_id = gathermate_id
        self.use_beta = use_beta

    def fetch_data(self, zone_map, zone_suppression):
        """Fetch coordinate data from Wowhead."""
        base_url = 'https://www.wowhead.com/beta/object=' if self.use_beta else 'https://www.wowhead.com/object='

        for object_id in self.ids:
            time.sleep(0.5)  # Rate limiting
            try:
                result = requests.get(f'{base_url}{object_id}', headers=HEADERS, timeout=30)
                result.raise_for_status()
            except requests.RequestException as e:
                log(f"  Failed to fetch {object_id}: {e}")
                continue

            title_match = re.search(r'<meta property="og:title" content="(.*)">', result.text)
            if not title_match:
                log(f"  No title found for {object_id}")
                continue
            title = html.unescape(title_match.group(1))

            data = re.search(r'var g_mapperData = (.*);', result.text)
            zones = re.findall(r'myMapper.update\({\s+zone: (\d+),\s+level: \d+,\s+}\);\s+WH.setSelectedLink\(this, \'mapper\'\);\s+return false;\s+" onmousedown="return false">([^<]+)</a>', result.text, re.M)
            zonemap = dict(zones)

            try:
                data_parsed = json.loads(data.group(1))
            except (AttributeError, json.JSONDecodeError):
                log(f"  No locations for {object_id} ({self.name})")
                continue

            for zone in data_parsed:
                wow_zone = zone_map.get(zone)
                if wow_zone is None:
                    if zone not in zone_suppression:
                        log(f"  Found unlisted zone: {zone}")
                    continue
                if wow_zone.name != zonemap.get(zone, ""):
                    log(f"  Zone name mismatch on {zone}: {wow_zone.name} != {zonemap.get(zone, '')}")
                coords = list()
                try:
                    for coord in data_parsed[zone][0]["coords"]:
                        coords.append(Coordinate(coord[0], coord[1]))
                except KeyError:
                    continue
                if self.coordinates.get(wow_zone) is None:
                    self.coordinates[wow_zone] = coords
                else:
                    self.coordinates[wow_zone] += coords

        if self.name != title if 'title' in dir() else True:
            log(f"Finished processing {self.name}")
        return self


@dataclass(eq=True, unsafe_hash=True)
class Zone:
    name: str
    id: str

    def __init__(self, name: str, id: str, skip_uimap_check: bool = False):
        self.name = name
        self.id = id


@dataclass
class Coordinate:
    x: float
    y: float
    coord: int = 0

    def __repr__(self):
        return str(self.as_gatherer_coord())

    def as_gatherer_coord(self):
        if self.coord == 0:
            self.coord = math.floor((self.x/100)*10000+0.5)*1000000+math.floor((self.y/100)*10000+0.5)*100
        return self.coord


@dataclass
class GathererEntry:
    coordinate: Coordinate
    entry_id: str

    def __repr__(self):
        return f"		[{self.coordinate}] = {self.entry_id},"

    def __lt__(self, other):
        return self.coordinate.as_gatherer_coord() < other.coordinate.as_gatherer_coord()


@dataclass
class GathererZone:
    zone: Zone
    entries: typing.List[GathererEntry]

    def __repr__(self):
        output = f'	[{self.zone.id}] = {{\n'
        for entry in sorted(self.entries):
            output += f'{str(entry)}\n'
        output += '	},\n'
        return output

    def __lt__(self, other):
        return int(self.zone.id) < int(other.zone.id)


@dataclass
class Aggregate:
    type: str
    zones: typing.List[GathererZone]

    def __init__(self, type, objects):
        self.type = type
        self.zones = []
        for object in objects:
            for zone in object.coordinates:
                for coord in object.coordinates[zone]:
                    self.add(zone, GathererEntry(coord, object.gathermate_id))

    def __repr__(self):
        output = f"GatherMate2{self.type}DB = {{\n"
        for zone in sorted(self.zones):
            output += f'{str(zone)}'
        output += '}'
        return output

    def add(self, zone: Zone, entry: GathererEntry):
        for gatherer_zone in self.zones:
            if gatherer_zone.zone == zone:
                while entry.coordinate in [x.coordinate for x in gatherer_zone.entries]:
                    entry.coordinate.coord = entry.coordinate.as_gatherer_coord() + 1
                gatherer_zone.entries.append(entry)
                return
        self.zones.append(GathererZone(zone, [entry]))


# ========================= DATA DEFINITIONS =========================

def load_uimap(csv_path):
    """Load UIMap data from CSV file."""
    uimap = {}
    if os.path.exists(csv_path):
        with open(csv_path, newline='', encoding='utf-8') as uimapcsv:
            reader = csv.reader(uimapcsv)
            for row in reader:
                if len(row) >= 2:
                    uimap[row[1]] = row[0]
    return uimap


# Dungeons and other odd maps to suppress
WOWHEAD_ZONE_SUPPRESSION = [
    # Vanilla
    '6511', '718', '457', '3455', '5339', '1581', '36', '796', '6040', '722', '719', '2100', '2557', '6510', '6514', '1584', '2717', '3479',
    # Burning Crusade
    '3716', '3717', '3790', '3791',
    # Wrath of the Lich King
    '206', '1196', '4196', '4228', '4265', '4273', '4277', '4416', '4494', '4812', '5786',
    # Cataclysm
    '6109', '5035',
    # Mists of Pandaria
    '5918', '5956', '6052', '6109', '6214',
    # Draenor
    '6967', '7078', '7004',
    # Legion
    '7877',
    # Battle for Azeroth
    '8956', '9562',
    # Dragonflight
    '14030', '14643',
    # The War Within (Delves and Scenarios)
    '14999', '15002', '15000', '14998', '15175', '14957', '15003', '15008', '15005', '15004', '15009',
    '14776', '15836', '15990', '16427',  # Proscenium, Excavation Site 9, Sidestreet Sluice, Archival Assault
]

# Zone to Expansion mapping for statistics
ZONE_EXPANSION = {
    # Classic - Kalimdor
    "63": "CL", "76": "CL", "462": "CL", "62": "CL", "89": "CL", "66": "CL", "1": "CL", "70": "CL",
    "77": "CL", "69": "CL", "80": "CL", "7": "CL", "10": "CL", "85": "CL", "81": "CL", "199": "CL",
    "65": "CL", "71": "CL", "57": "CL", "64": "CL", "88": "CL", "78": "CL", "461": "CL", "83": "CL",
    # Classic - Eastern Kingdoms
    "14": "CL", "15": "CL", "17": "CL", "36": "CL", "42": "CL", "27": "CL", "47": "CL", "23": "CL",
    "37": "CL", "25": "CL", "87": "CL", "48": "CL", "469": "CL", "50": "CL", "49": "CL", "32": "CL",
    "21": "CL", "84": "CL", "51": "CL", "210": "CL", "26": "CL", "18": "CL", "90": "CL", "22": "CL",
    "52": "CL", "56": "CL",
    # Burning Crusade
    "97": "TBC", "105": "TBC", "106": "TBC", "94": "TBC", "95": "TBC", "100": "TBC", "122": "TBC",
    "107": "TBC", "109": "TBC", "104": "TBC", "111": "TBC", "110": "TBC", "108": "TBC", "103": "TBC", "102": "TBC",
    # Wrath of the Lich King
    "114": "WotLK", "127": "WotLK", "125": "WotLK", "115": "WotLK", "116": "WotLK", "117": "WotLK",
    "170": "WotLK", "118": "WotLK", "119": "WotLK", "120": "WotLK", "123": "WotLK", "121": "WotLK",
    # Cataclysm
    "204": "Cata", "207": "Cata", "217": "Cata", "218": "Cata", "198": "Cata", "201": "Cata",
    "194": "Cata", "205": "Cata", "174": "Cata", "244": "Cata", "245": "Cata", "241": "Cata",
    "249": "Cata", "1527": "Cata",
    # Mists of Pandaria
    "422": "MoP", "418": "MoP", "379": "MoP", "507": "MoP", "504": "MoP", "371": "MoP",
    "433": "MoP", "378": "MoP", "554": "MoP", "388": "MoP", "390": "MoP", "376": "MoP", "1530": "MoP",
    # Warlords of Draenor
    "588": "WoD", "525": "WoD", "534": "WoD", "535": "WoD", "539": "WoD", "542": "WoD", "543": "WoD", "550": "WoD",
    # Legion
    "630": "Leg", "634": "Leg", "641": "Leg", "646": "Leg", "650": "Leg", "680": "Leg", "628": "Leg", "750": "Leg",
    "830": "Leg", "882": "Leg", "885": "Leg",  # Argus zones (Krokuun, Mac'Aree, Antoran Wastes)
    # Battle for Azeroth
    "1161": "BfA", "1165": "BfA", "862": "BfA", "863": "BfA", "864": "BfA", "895": "BfA", "896": "BfA", "942": "BfA",
    "1355": "BfA", "1462": "BfA",  # Nazjatar, Mechagon
    # Shadowlands
    "1525": "SL", "1533": "SL", "1536": "SL", "1543": "SL", "1565": "SL", "1670": "SL",
    "1961": "SL", "1970": "SL",  # Korthia, Zereth Mortis
    # Dragonflight
    "2022": "DF", "2023": "DF", "2024": "DF", "2025": "DF", "2085": "DF",
    "2112": "DF", "2133": "DF", "2151": "DF", "2200": "DF", "2199": "DF", "2262": "DF", "2239": "DF",
    # The War Within
    "2248": "TWW", "2215": "TWW", "2214": "TWW", "2255": "TWW", "2213": "TWW", "2339": "TWW", "2256": "TWW",
    # Midnight
    "2393": "MD", "2395": "MD", "2405": "MD", "2413": "MD", "2437": "MD", "2444": "MD", "2536": "MD", "2537": "MD", "2557": "MD",
}


def get_zone_map():
    """Return the Wowhead zone ID to GatherMate zone mapping."""
    return {
        # Vanilla Kalimdor
        '331': Zone("Ashenvale", "63"),
        '16' : Zone("Azshara", "76"),
        '6452': Zone("Camp Narache", "462"),
        '148': Zone("Darkshore", "62"),
        '1657': Zone("Darnassus", "89"),
        '405': Zone("Desolace", "66"),
        '14' : Zone("Durotar", "1"),
        '15' : Zone("Dustwallow Marsh", "70"),
        '361': Zone("Felwood", "77"),
        '357': Zone("Feralas", "69"),
        '493': Zone("Moonglade", "80"),
        '215': Zone("Mulgore", "7"),
        '17' : Zone("Northern Barrens", "10"),
        '1637': Zone("Orgrimmar", "85"),
        '1377': Zone("Silithus", "81"),
        '4709': Zone("Southern Barrens", "199"),
        '406': Zone("Stonetalon Mountains", "65"),
        '440': Zone("Tanaris", "71"),
        '141': Zone("Teldrassil", "57"),
        '400': Zone("Thousand Needles", "64"),
        '1638': Zone("Thunder Bluff", "88"),
        '490': Zone("Un'Goro Crater", "78"),
        '6451': Zone("Valley of Trials", "461"),
        '618': Zone("Winterspring", "83"),

        # Vanilla EK
        '45' : Zone("Arathi Highlands", "14"),
        '3'  : Zone("Badlands", "15"),
        '4'  : Zone("Blasted Lands", "17"),
        '46' : Zone("Burning Steppes", "36"),
        '41' : Zone("Deadwind Pass", "42"),
        '1'  : Zone("Dun Morogh", "27"),
        '10' : Zone("Duskwood", "47"),
        '139': Zone("Eastern Plaguelands", "23"),
        '12' : Zone("Elwynn Forest", "37"),
        '267': Zone("Hillsbrad Foothills", "25"),
        '1537': Zone("Ironforge", "87"),
        '38' : Zone("Loch Modan", "48"),
        '6457': Zone("New Tinkertown", "469"),
        '33' : Zone("Northern Stranglethorn", "50"),
        '44' : Zone("Redridge Mountains", "49"),
        '51' : Zone("Searing Gorge", "32"),
        '130': Zone("Silverpine Forest", "21"),
        '1519': Zone("Stormwind City", "84"),
        '8'  : Zone("Swamp of Sorrows", "51"),
        '5287': Zone("The Cape of Stranglethorn", "210"),
        '47' : Zone("The Hinterlands", "26"),
        '85' : Zone("Tirisfal Glades", "18"),
        '1497': Zone("Undercity", "90"),
        '28' : Zone("Western Plaguelands", "22"),
        '40' : Zone("Westfall", "52"),
        '11' : Zone("Wetlands", "56"),

        # Burning Crusade
        '3524': Zone("Azuremyst Isle", "97"),
        '3522': Zone("Blade's Edge Mountains", "105"),
        '3525': Zone("Bloodmyst Isle", "106"),
        '3430': Zone("Eversong Woods", "94"),
        '3433': Zone("Ghostlands", "95"),
        '3483': Zone("Hellfire Peninsula", "100"),
        '4080': Zone("Isle of Quel'Danas", "122"),
        '3518': Zone("Nagrand", "107"),
        '3523': Zone("Netherstorm", "109"),
        '3520': Zone("Shadowmoon Valley", "104"),
        '3703': Zone("Shattrath City", "111"),
        '3487': Zone("Silvermoon City", "110"),
        '3519': Zone("Terokkar Forest", "108"),
        '3557': Zone("The Exodar", "103"),
        '3521': Zone("Zangarmarsh", "102"),

        # Wrath of the Lich King
        '3537': Zone("Borean Tundra", "114"),
        '2817': Zone("Crystalsong Forest", "127"),
        '4395': Zone("Dalaran", "125"),
        '65'  : Zone("Dragonblight", "115"),
        '394' : Zone("Grizzly Hills", "116"),
        '495' : Zone("Howling Fjord", "117"),
        '4742': Zone("Hrothgar's Landing", "170"),
        '210' : Zone("Icecrown", "118"),
        '3711': Zone("Sholazar Basin", "119"),
        '67'  : Zone("The Storm Peaks", "120"),
        '4197': Zone("Wintergrasp", "123"),
        '66'  : Zone("Zul'Drak", "121"),

        # Cataclysm
        '5145': Zone("Abyssal Depths", "204"),
        '5042': Zone("Deepholm", "207"),
        '4714': Zone("Gilneas", "217"),
        '4755': Zone("Gilneas City", "218"),
        '616' : Zone("Mount Hyjal", "198"),
        '4815': Zone("Kelp'thar Forest", "201"),
        '4737': Zone("Kezan", "194"),
        '5144': Zone("Shimmering Expanse", "205"),
        '4720': Zone("The Lost Isles", "174"),
        '5095': Zone("Tol Barad", "244"),
        '5389': Zone("Tol Barad Peninsula", "245"),
        '4922': Zone("Twilight Highlands", "241"),
        '5034': Zone("Uldum", "249"),
        '10833': Zone("Uldum", "1527"),

        # Mists of Pandaria
        '6138': Zone("Dread Wastes", "422"),
        '6134': Zone("Krasarang Wilds", "418"),
        '5841': Zone("Kun-Lai Summit", "379"),
        '6661': Zone("Isle of Giants", "507"),
        '6507': Zone("Isle of Thunder", "504"),
        '5785': Zone("The Jade Forest", "371"),
        '6006': Zone("The Veiled Stair", "433"),
        '5736': Zone("The Wandering Isle", "378"),
        '6757': Zone("Timeless Isle", "554"),
        '5842': Zone("Townlong Steppes", "388"),
        '5840': Zone("Vale of Eternal Blossoms", "390"),
        '5805': Zone("Valley of the Four Winds", "376"),
        '9105': Zone("Vale of Eternal Blossoms", "1530"),

        # Draenor
        '6941': Zone("Ashran", "588"),
        '6720': Zone("Frostfire Ridge", "525"),
        '6721': Zone("Gorgrond", "543"),
        '6755': Zone("Nagrand", "550"),
        '6719': Zone("Shadowmoon Valley", "539"),
        '6722': Zone("Spires of Arak", "542"),
        '6662': Zone("Talador", "535"),
        '6723': Zone("Tanaan Jungle", "534"),

        # Legion
        '8899': Zone("Antoran Wastes", "885"),
        '7334': Zone("Azsuna", "630"),
        '7543': Zone("Broken Shore", "646"),
        '7502': Zone("Dalaran", "628"),
        '7503': Zone("Highmountain", "650"),
        '8574': Zone("Krokuun", "830"),
        '8701': Zone("Eredath", "882"),
        '7541': Zone("Stormheim", "634"),
        '7637': Zone("Suramar", "680"),
        '7731': Zone("Thunder Totem", "750"),
        '7558': Zone("Val'sharah", "641"),

        # Battle for Azeroth
        '8568' : Zone("Boralus", "1161"),
        '8670' : Zone("Dazar'alor", "1165"),
        '8721' : Zone("Drustvar", "896"),
        '10290': Zone("Mechagon", "1462"),
        '10052': Zone("Nazjatar", "1355"),
        '8500' : Zone("Nazmir", "863"),
        '9042' : Zone("Stormsong Valley", "942"),
        '8567' : Zone("Tiragarde Sound", "895"),
        '8501' : Zone("Vol'dun", "864"),
        '8499' : Zone("Zuldazar", "862"),

        # Shadowlands
        '11510': Zone("Ardenweald", "1565"),
        '10534': Zone("Bastion", "1533"),
        '11462': Zone("Maldraxxus", "1536"),
        '10565': Zone("Oribos", "1670"),
        '10413': Zone("Revendreth", "1525"),
        '11400': Zone("The Maw", "1543"),
        '13570': Zone("Korthia", "1961"),
        '13536': Zone("Zereth Mortis", "1970"),

        # Dragonflight
        '13644': Zone("The Waking Shores", "2022"),
        '13645': Zone("Ohn'ahran Plains", "2023"),
        '13646': Zone("The Azure Span", "2024"),
        '13647': Zone("Thaldraszus", "2025"),
        '13862': Zone("Valdrakken", "2112"),
        '14433': Zone("The Forbidden Reach", "2151"),
        '14022': Zone("Zaralek Cavern", "2133"),
        '14529': Zone("Emerald Dream", "2200"),
        '15105': Zone("Amirdrassil", "2239"),
        '13844': Zone("Traitor's Rest", "2262"),
        '13802': Zone("Tyrhold Reservoir", "2199"),
        '13992': Zone("The Primalist Future", "2085"),

        # The War Within
        '14717': Zone("Isle of Dorn", "2248"),
        '14838': Zone("Hallowfall", "2215"),
        '14795': Zone("The Ringing Deeps", "2214"),
        '14752': Zone("Azj-Kahet", "2255"),
        '14753': Zone("City of Threads", "2213"),
        '14771': Zone("Dornogal", "2339"),

        # The War Within - New Zones
        '15347': Zone("Undermine", "2256", skip_uimap_check=True),

        # Midnight (Beta) - Zone IDs corrected based on Wowhead data
        '15947': Zone("Zul'Aman", "2437"),
        '15355': Zone("Harandar", "2413"),
        '15968': Zone("Eversong Woods", "2395"),
        '16194': Zone("Atal'Aman", "2536"),
        '15458': Zone("Voidstorm", "2405"),
        '15958': Zone("Masters' Perch", "2557", skip_uimap_check=True),
    }


# ========================= NODE DEFINITIONS =========================

# ========================= CLASSIC NODES =========================

def get_classic_herbs():
    """Return Classic (Vanilla) herb definitions."""
    return [
        WowheadObject(name="Peacebloom", ids=['1618'], gathermate_id='401'),
        WowheadObject(name="Silverleaf", ids=['1617'], gathermate_id='402'),
        WowheadObject(name="Earthroot", ids=['1619'], gathermate_id='403'),
        WowheadObject(name="Mageroyal", ids=['1620'], gathermate_id='404'),
        WowheadObject(name="Briarthorn", ids=['1621'], gathermate_id='405'),
        WowheadObject(name="Stranglekelp", ids=['2045'], gathermate_id='407'),
        WowheadObject(name="Bruiseweed", ids=['1622'], gathermate_id='408'),
        WowheadObject(name="Wild Steelbloom", ids=['1623'], gathermate_id='409'),
        WowheadObject(name="Grave Moss", ids=['1628'], gathermate_id='410'),
        WowheadObject(name="Kingsblood", ids=['1624'], gathermate_id='411'),
        WowheadObject(name="Liferoot", ids=['2041'], gathermate_id='412'),
        WowheadObject(name="Fadeleaf", ids=['2042'], gathermate_id='413'),
        WowheadObject(name="Goldthorn", ids=['2046'], gathermate_id='414'),
        WowheadObject(name="Khadgar's Whisker", ids=['2043'], gathermate_id='415'),
        WowheadObject(name="Firebloom", ids=['2866'], gathermate_id='417'),
        WowheadObject(name="Purple Lotus", ids=['142140'], gathermate_id='418'),
        WowheadObject(name="Arthas' Tears", ids=['142141'], gathermate_id='420'),
        WowheadObject(name="Sungrass", ids=['142142'], gathermate_id='421'),
        WowheadObject(name="Blindweed", ids=['142143'], gathermate_id='422'),
        WowheadObject(name="Ghost Mushroom", ids=['142144'], gathermate_id='423'),
        WowheadObject(name="Gromsblood", ids=['142145'], gathermate_id='424'),
        WowheadObject(name="Golden Sansam", ids=['176583'], gathermate_id='425'),
        WowheadObject(name="Dreamfoil", ids=['176584'], gathermate_id='426'),
        WowheadObject(name="Mountain Silversage", ids=['176586'], gathermate_id='427'),
        WowheadObject(name="Plaguebloom", ids=['176587'], gathermate_id='428'),
        WowheadObject(name="Icecap", ids=['176588'], gathermate_id='429'),
        WowheadObject(name="Black Lotus", ids=['176589'], gathermate_id='431'),
    ]


def get_classic_ores():
    """Return Classic (Vanilla) ore definitions."""
    return [
        WowheadObject(name="Copper Vein", ids=['1731'], gathermate_id='201'),
        WowheadObject(name="Tin Vein", ids=['1732'], gathermate_id='202'),
        WowheadObject(name="Iron Deposit", ids=['1735'], gathermate_id='203'),
        WowheadObject(name="Silver Vein", ids=['1733'], gathermate_id='204'),
        WowheadObject(name="Gold Vein", ids=['1734'], gathermate_id='205'),
        WowheadObject(name="Mithril Deposit", ids=['2040'], gathermate_id='206'),
        WowheadObject(name="Truesilver Deposit", ids=['2047'], gathermate_id='208'),
        WowheadObject(name="Small Thorium Vein", ids=['324'], gathermate_id='214'),
        WowheadObject(name="Rich Thorium Vein", ids=['175404'], gathermate_id='215'),
        WowheadObject(name="Dark Iron Deposit", ids=['165658'], gathermate_id='217'),
    ]


# ========================= TBC NODES =========================

def get_tbc_herbs():
    """Return Burning Crusade herb definitions."""
    return [
        WowheadObject(name="Felweed", ids=['181270'], gathermate_id='432'),
        WowheadObject(name="Dreaming Glory", ids=['181271'], gathermate_id='433'),
        WowheadObject(name="Terocone", ids=['181277'], gathermate_id='434'),
        WowheadObject(name="Mana Thistle", ids=['181281'], gathermate_id='437'),
        WowheadObject(name="Netherbloom", ids=['181279'], gathermate_id='438'),
        WowheadObject(name="Nightmare Vine", ids=['181280'], gathermate_id='439'),
        WowheadObject(name="Ragveil", ids=['181275'], gathermate_id='440'),
        WowheadObject(name="Flame Cap", ids=['181276'], gathermate_id='441'),
    ]


def get_tbc_ores():
    """Return Burning Crusade ore definitions."""
    return [
        WowheadObject(name="Fel Iron Deposit", ids=['181555'], gathermate_id='221'),
        WowheadObject(name="Adamantite Deposit", ids=['181556'], gathermate_id='222'),
        WowheadObject(name="Rich Adamantite Deposit", ids=['181569'], gathermate_id='223'),
        WowheadObject(name="Khorium Vein", ids=['181557'], gathermate_id='224'),
    ]


# ========================= WOTLK NODES =========================

def get_wotlk_herbs():
    """Return Wrath of the Lich King herb definitions."""
    return [
        WowheadObject(name="Adder's Tongue", ids=['191019'], gathermate_id='443'),
        WowheadObject(name="Goldclover", ids=['189973'], gathermate_id='446'),
        WowheadObject(name="Icethorn", ids=['190172'], gathermate_id='447'),
        WowheadObject(name="Lichbloom", ids=['190171'], gathermate_id='448'),
        WowheadObject(name="Talandra's Rose", ids=['190170'], gathermate_id='449'),
        WowheadObject(name="Tiger Lily", ids=['190169'], gathermate_id='450'),
        WowheadObject(name="Frost Lotus", ids=['190176'], gathermate_id='453'),
    ]


def get_wotlk_ores():
    """Return Wrath of the Lich King ore definitions."""
    return [
        WowheadObject(name="Cobalt Deposit", ids=['189978'], gathermate_id='228'),
        WowheadObject(name="Rich Cobalt Deposit", ids=['189979'], gathermate_id='229'),
        WowheadObject(name="Titanium Vein", ids=['191133'], gathermate_id='230'),
        WowheadObject(name="Saronite Deposit", ids=['189980'], gathermate_id='231'),
        WowheadObject(name="Rich Saronite Deposit", ids=['189981'], gathermate_id='232'),
    ]


# ========================= CATACLYSM NODES =========================

def get_cata_herbs():
    """Return Cataclysm herb definitions."""
    return [
        WowheadObject(name="Azshara's Veil", ids=['202749'], gathermate_id='456'),
        WowheadObject(name="Cinderbloom", ids=['202747'], gathermate_id='457'),
        WowheadObject(name="Stormvine", ids=['202748'], gathermate_id='458'),
        WowheadObject(name="Heartblossom", ids=['202750'], gathermate_id='459'),
        WowheadObject(name="Twilight Jasmine", ids=['202751'], gathermate_id='460'),
        WowheadObject(name="Whiptail", ids=['202752'], gathermate_id='461'),
    ]


def get_cata_ores():
    """Return Cataclysm ore definitions."""
    return [
        WowheadObject(name="Obsidium Deposit", ids=['202736'], gathermate_id='233'),
        WowheadObject(name="Rich Obsidium Deposit", ids=['202739'], gathermate_id='239'),
        WowheadObject(name="Elementium Vein", ids=['202738'], gathermate_id='236'),
        WowheadObject(name="Rich Elementium Vein", ids=['202741'], gathermate_id='237'),
        WowheadObject(name="Pyrite Deposit", ids=['202737'], gathermate_id='238'),
        WowheadObject(name="Rich Pyrite Deposit", ids=['202740'], gathermate_id='240'),
    ]


# ========================= MOP NODES =========================

def get_mop_herbs():
    """Return Mists of Pandaria herb definitions."""
    return [
        WowheadObject(name="Golden Lotus", ids=['209354'], gathermate_id='462'),
        WowheadObject(name="Fool's Cap", ids=['209355'], gathermate_id='463'),
        WowheadObject(name="Snow Lily", ids=['209351'], gathermate_id='464'),
        WowheadObject(name="Silkweed", ids=['209350'], gathermate_id='465'),
        WowheadObject(name="Green Tea Leaf", ids=['209349'], gathermate_id='466'),
        WowheadObject(name="Rain Poppy", ids=['209353'], gathermate_id='467'),
    ]


def get_mop_ores():
    """Return Mists of Pandaria ore definitions."""
    return [
        WowheadObject(name="Ghost Iron Deposit", ids=['209311'], gathermate_id='241'),
        WowheadObject(name="Rich Ghost Iron Deposit", ids=['209328'], gathermate_id='242'),
        WowheadObject(name="Kyparite Deposit", ids=['209312'], gathermate_id='245'),
        WowheadObject(name="Rich Kyparite Deposit", ids=['215416'], gathermate_id='246'),
        WowheadObject(name="Trillium Vein", ids=['209313'], gathermate_id='247'),
        WowheadObject(name="Rich Trillium Vein", ids=['209330'], gathermate_id='248'),
    ]


# ========================= WOD NODES =========================

def get_wod_herbs():
    """Return Warlords of Draenor herb definitions."""
    return [
        WowheadObject(name="Frostweed", ids=['233117'], gathermate_id='474'),
        WowheadObject(name="Fireweed", ids=['235387'], gathermate_id='473'),
        WowheadObject(name="Gorgrond Flytrap", ids=['235388'], gathermate_id='472'),
        WowheadObject(name="Starflower", ids=['228574'], gathermate_id='471'),
        WowheadObject(name="Nagrand Arrowbloom", ids=['228575'], gathermate_id='470'),
        WowheadObject(name="Talador Orchid", ids=['237400'], gathermate_id='469'),
        WowheadObject(name="Withered Herb", ids=['243334'], gathermate_id='475'),
    ]


def get_wod_ores():
    """Return Warlords of Draenor ore definitions."""
    return [
        WowheadObject(name="True Iron Deposit", ids=['228493'], gathermate_id='249'),
        WowheadObject(name="Rich True Iron Deposit", ids=['232545'], gathermate_id='250'),
        WowheadObject(name="Blackrock Deposit", ids=['237359'], gathermate_id='251'),
        WowheadObject(name="Rich Blackrock Deposit", ids=['232543'], gathermate_id='252'),
    ]


# ========================= LEGION NODES =========================

def get_legion_herbs():
    """Return Legion herb definitions."""
    return [
        WowheadObject(name="Aethril", ids=['244774'], gathermate_id='476'),
        WowheadObject(name="Dreamleaf", ids=['244776'], gathermate_id='477'),
        WowheadObject(name="Felwort", ids=['252404'], gathermate_id='478'),
        WowheadObject(name="Fjarnskaggl", ids=['244777'], gathermate_id='479'),
        WowheadObject(name="Foxflower", ids=['241641'], gathermate_id='480'),
        WowheadObject(name="Starlight Rose", ids=['244778'], gathermate_id='481'),
        WowheadObject(name="Fel-Encrusted Herb", ids=['273052'], gathermate_id='482'),
        WowheadObject(name="Astral Glory", ids=['272782'], gathermate_id='484'),
    ]


def get_legion_ores():
    """Return Legion ore definitions."""
    return [
        WowheadObject(name="Leystone Deposit", ids=['241726'], gathermate_id='253'),
        WowheadObject(name="Rich Leystone Deposit", ids=['245324'], gathermate_id='254'),
        WowheadObject(name="Leystone Seam", ids=['253280'], gathermate_id='255'),
        WowheadObject(name="Felslate Deposit", ids=['241743'], gathermate_id='256'),
        WowheadObject(name="Rich Felslate Deposit", ids=['245325'], gathermate_id='257'),
        WowheadObject(name="Felslate Seam", ids=['255344'], gathermate_id='258'),
        WowheadObject(name="Empyrium Deposit", ids=['272768'], gathermate_id='259'),
        WowheadObject(name="Rich Empyrium Deposit", ids=['272778'], gathermate_id='260'),
        WowheadObject(name="Empyrium Seam", ids=['272780'], gathermate_id='261'),
    ]


# ========================= BFA NODES =========================

def get_bfa_herbs():
    """Return Battle for Azeroth herb definitions."""
    return [
        WowheadObject(name="Akunda's Bite", ids=['276237'], gathermate_id='485'),
        WowheadObject(name="Anchor Weed", ids=['276242'], gathermate_id='486'),
        WowheadObject(name="Riverbud", ids=['276234'], gathermate_id='487'),
        WowheadObject(name="Sea Stalks", ids=['276240'], gathermate_id='488'),
        WowheadObject(name="Siren's Sting", ids=['276239'], gathermate_id='489'),
        WowheadObject(name="Star Moss", ids=['276236'], gathermate_id='490'),
        WowheadObject(name="Winter's Kiss", ids=['276238'], gathermate_id='491'),
        WowheadObject(name="Zin'anthid", ids=['326598'], gathermate_id='492'),
    ]


def get_bfa_ores():
    """Return Battle for Azeroth ore definitions."""
    return [
        WowheadObject(name="Monelite Deposit", ids=['276616'], gathermate_id='262'),
        WowheadObject(name="Rich Monelite Deposit", ids=['276621'], gathermate_id='263'),
        WowheadObject(name="Monelite Seam", ids=['276619'], gathermate_id='264'),
        WowheadObject(name="Platinum Deposit", ids=['276618'], gathermate_id='265'),
        WowheadObject(name="Rich Platinum Deposit", ids=['276623'], gathermate_id='266'),
        WowheadObject(name="Storm Silver Deposit", ids=['276617'], gathermate_id='267'),
        WowheadObject(name="Rich Storm Silver Deposit", ids=['276622'], gathermate_id='268'),
        WowheadObject(name="Storm Silver Seam", ids=['276620'], gathermate_id='269'),
        WowheadObject(name="Osmenite Deposit", ids=['325875'], gathermate_id='270'),
        WowheadObject(name="Rich Osmenite Deposit", ids=['325873'], gathermate_id='271'),
        WowheadObject(name="Osmenite Seam", ids=['325874'], gathermate_id='272'),
    ]


# ========================= SHADOWLANDS NODES =========================

def get_sl_herbs():
    """Return Shadowlands herb definitions."""
    return [
        WowheadObject(name="Death Blossom", ids=['351470'], gathermate_id='493'),
        WowheadObject(name="Nightshade", ids=['336691'], gathermate_id='494'),
        WowheadObject(name="Lush Nightshade", ids=['375071'], gathermate_id='1401'),
        WowheadObject(name="Elusive Nightshade", ids=['375338'], gathermate_id='1402'),
        WowheadObject(name="Marrowroot", ids=['336689'], gathermate_id='495'),
        WowheadObject(name="Vigil's Torch", ids=['336688'], gathermate_id='496'),
        WowheadObject(name="Rising Glory", ids=['336690'], gathermate_id='497'),
        WowheadObject(name="Widowbloom", ids=['336433'], gathermate_id='498'),
        WowheadObject(name="First Flower", ids=['370398'], gathermate_id='499'),
        WowheadObject(name="Lush First Flower", ids=['370397'], gathermate_id='1403'),
        WowheadObject(name="Elusive First Flower", ids=['375337'], gathermate_id='1404'),
    ]


def get_sl_ores():
    """Return Shadowlands ore definitions."""
    return [
        WowheadObject(name="Laestrite Deposit", ids=['349898'], gathermate_id='273'),
        WowheadObject(name="Rich Laestrite Deposit", ids=['349899'], gathermate_id='274'),
        WowheadObject(name="Phaedrum Deposit", ids=['349982'], gathermate_id='275'),
        WowheadObject(name="Rich Phaedrum Deposit", ids=['350087'], gathermate_id='276'),
        WowheadObject(name="Oxxein Deposit", ids=['349981'], gathermate_id='277'),
        WowheadObject(name="Rich Oxxein Deposit", ids=['350085'], gathermate_id='278'),
        WowheadObject(name="Elethium Deposit", ids=['349900'], gathermate_id='280'),
        WowheadObject(name="Rich Elethium Deposit", ids=['350082'], gathermate_id='281'),
        WowheadObject(name="Elusive Elethium Deposit", ids=['375333'], gathermate_id='291'),
        WowheadObject(name="Solenium Deposit", ids=['349980'], gathermate_id='282'),
        WowheadObject(name="Rich Solenium Deposit", ids=['350086'], gathermate_id='283'),
        WowheadObject(name="Sinvyr Deposit", ids=['349983'], gathermate_id='284'),
        WowheadObject(name="Rich Sinvyr Deposit", ids=['350084'], gathermate_id='285'),
        WowheadObject(name="Progenium Deposit", ids=['370400'], gathermate_id='287'),
        WowheadObject(name="Rich Progenium Deposit", ids=['370399'], gathermate_id='288'),
        WowheadObject(name="Elusive Progenium Deposit", ids=['375332'], gathermate_id='289'),
    ]


# ========================= DRAGONFLIGHT NODES =========================

def get_dragonflight_herbs():
    """Return Dragonflight herb definitions."""
    return [
        WowheadObject(name="Hochenblume", ids=['381209', '407703', '398757'], gathermate_id='1407'),
        WowheadObject(name="Lush Hochenblume", ids=['381960', '407705', '398753'], gathermate_id='1408'),
        WowheadObject(name="Bubble Poppy", ids=['375241', '407685', '398755'], gathermate_id='1414'),
        WowheadObject(name="Lush Bubble Poppy", ids=['381957', '407686', '398751'], gathermate_id='1415'),
        WowheadObject(name="Saxifrage", ids=['381207', '407701', '398758'], gathermate_id='1421'),
        WowheadObject(name="Lush Saxifrage", ids=['407706', '398754', '381959'], gathermate_id='1422'),
        WowheadObject(name="Writhebark", ids=['381154', '407702', '398756'], gathermate_id='1428'),
        WowheadObject(name="Lush Writhebark", ids=['381958', '407707', '398752'], gathermate_id='1429'),
    ]


def get_tww_herbs():
    """Return The War Within herb definitions."""
    return [
        WowheadObject(name="Mycobloom", ids=['454063', '414315', '454071', '454076'], gathermate_id='1439'),
        WowheadObject(name="Lush Mycobloom", ids=['454062', '454075', '414320', '454070'], gathermate_id='1440'),
        WowheadObject(name="Blessing Blossom", ids=['454086', '414318', '454081'], gathermate_id='1447'),
        WowheadObject(name="Lush Blessing Blossom", ids=['414323', '454080', '454085'], gathermate_id='1448'),
        WowheadObject(name="Luredrop", ids=['454010', '454055', '414316'], gathermate_id='1455'),
        WowheadObject(name="Lush Luredrop", ids=['414321', '454009', '454054'], gathermate_id='1456'),
        WowheadObject(name="Orbinid", ids=['414317'], gathermate_id='1463'),
        WowheadObject(name="Lush Orbinid", ids=['414322'], gathermate_id='1464'),
        WowheadObject(name="Arathor's Spear", ids=['414319'], gathermate_id='1471'),
        WowheadObject(name="Lush Arathor's Spear", ids=['414324'], gathermate_id='1472'),
    ]


def get_midnight_herbs():
    """Return Midnight (Beta) herb definitions."""
    return [
        # Argentleaf (1481) + variants
        WowheadObject(name="Argentleaf", ids=['516936'], gathermate_id='1481', use_beta=True),
        WowheadObject(name="Wild Argentleaf", ids=['516971'], gathermate_id='1482', use_beta=True),
        WowheadObject(name="Lush Argentleaf", ids=['516985'], gathermate_id='1483', use_beta=True),
        WowheadObject(name="Voidbound Argentleaf", ids=['516982'], gathermate_id='1484', use_beta=True),
        WowheadObject(name="Lightfused Argentleaf", ids=['516964'], gathermate_id='1485', use_beta=True),
        WowheadObject(name="Primal Argentleaf", ids=['516976'], gathermate_id='1486', use_beta=True),

        # Mana Lily (1487) + variants
        WowheadObject(name="Mana Lily", ids=['516937'], gathermate_id='1487', use_beta=True),
        WowheadObject(name="Wild Mana Lily", ids=['516972'], gathermate_id='1488', use_beta=True),
        WowheadObject(name="Lush Mana Lily", ids=['516984'], gathermate_id='1489', use_beta=True),
        WowheadObject(name="Voidbound Mana Lily", ids=['516983'], gathermate_id='1490', use_beta=True),
        WowheadObject(name="Lightfused Mana Lily", ids=['516963'], gathermate_id='1491', use_beta=True),
        # Primal Mana Lily (1492) - Not on Wowhead yet

        # Tranquility Bloom (1493) + variants
        WowheadObject(name="Tranquility Bloom", ids=['516932'], gathermate_id='1493', use_beta=True),
        WowheadObject(name="Wild Tranquility Bloom", ids=['516968'], gathermate_id='1494', use_beta=True),
        WowheadObject(name="Lush Tranquility Bloom", ids=['516988'], gathermate_id='1495', use_beta=True),
        WowheadObject(name="Voidbound Tranquility Bloom", ids=['516979'], gathermate_id='1496', use_beta=True),
        WowheadObject(name="Lightfused Tranquility Bloom", ids=['516967'], gathermate_id='1497', use_beta=True),
        WowheadObject(name="Primal Tranquility Bloom", ids=['516973'], gathermate_id='1498', use_beta=True),

        # Sanguithorn (1499) + variants
        WowheadObject(name="Sanguithorn", ids=['516934'], gathermate_id='1499', use_beta=True),
        WowheadObject(name="Wild Sanguithorn", ids=['516969'], gathermate_id='1500', use_beta=True),
        WowheadObject(name="Lush Sanguithorn", ids=['516987'], gathermate_id='1501', use_beta=True),
        WowheadObject(name="Voidbound Sanguithorn", ids=['516980'], gathermate_id='1502', use_beta=True),
        WowheadObject(name="Lightfused Sanguithorn", ids=['516966'], gathermate_id='1503', use_beta=True),
        WowheadObject(name="Primal Sanguithorn", ids=['516974'], gathermate_id='1504', use_beta=True),

        # Azeroot (1505) + variants
        WowheadObject(name="Azeroot", ids=['516935'], gathermate_id='1505', use_beta=True),
        WowheadObject(name="Wild Azeroot", ids=['516970'], gathermate_id='1506', use_beta=True),
        WowheadObject(name="Lush Azeroot", ids=['516986'], gathermate_id='1507', use_beta=True),
        WowheadObject(name="Voidbound Azeroot", ids=['516981'], gathermate_id='1508', use_beta=True),
        WowheadObject(name="Lightfused Azeroot", ids=['516965'], gathermate_id='1509', use_beta=True),
        WowheadObject(name="Primal Azeroot", ids=['516975'], gathermate_id='1510', use_beta=True),

        # Transplanted variants (special farming plots)
        WowheadObject(name="Transplanted Lush Argentleaf", ids=['612111'], gathermate_id='1511', use_beta=True),
        WowheadObject(name="Transplanted Lush Mana Lily", ids=['612113'], gathermate_id='1512', use_beta=True),
        WowheadObject(name="Transplanted Lush Azeroot", ids=['612114'], gathermate_id='1513', use_beta=True),
        WowheadObject(name="Transplanted Lush Sanguithorn", ids=['612115'], gathermate_id='1514', use_beta=True),
    ]


def get_dragonflight_ores():
    """Return Dragonflight ore definitions."""
    return [
        WowheadObject(name="Serevite Seam", ids=['381106'], gathermate_id='1200'),
        WowheadObject(name="Serevite Deposit", ids=['381102', '407677', '381103'], gathermate_id='1201'),
        WowheadObject(name="Rich Serevite Deposit", ids=['381104', '407678', '381105'], gathermate_id='1202'),
        WowheadObject(name="Draconium Seam", ids=['379272'], gathermate_id='1208'),
        WowheadObject(name="Draconium Deposit", ids=['379252', '407679', '379248'], gathermate_id='1209'),
        WowheadObject(name="Rich Draconium Deposit", ids=['407681', '379267', '379263'], gathermate_id='1210'),
    ]


def get_tww_ores():
    """Return The War Within ore definitions."""
    return [
        WowheadObject(name="Bismuth", ids=['413046'], gathermate_id='1218'),
        WowheadObject(name="Rich Bismuth", ids=['413874'], gathermate_id='1219'),
        WowheadObject(name="Bismuth Seam", ids=['413880'], gathermate_id='1225'),
        WowheadObject(name="Aqirite", ids=['413047'], gathermate_id='1226'),
        WowheadObject(name="Rich Aqirite", ids=['413875'], gathermate_id='1227'),
        WowheadObject(name="Aqirite Seam", ids=['413881'], gathermate_id='1233'),
        WowheadObject(name="Ironclaw", ids=['413049'], gathermate_id='1234'),
        WowheadObject(name="Rich Ironclaw", ids=['413877'], gathermate_id='1235'),
        WowheadObject(name="Ironclaw Seam", ids=['413882'], gathermate_id='1241'),
    ]


def get_midnight_ores():
    """Return Midnight (Beta) ore definitions."""
    return [
        # Refulgent Copper (1245) + variants
        WowheadObject(name="Refulgent Copper", ids=['523281'], gathermate_id='1245', use_beta=True),
        WowheadObject(name="Refulgent Copper Seam", ids=['523283'], gathermate_id='1246', use_beta=True),
        WowheadObject(name="Voidbound Refulgent Copper", ids=['523287'], gathermate_id='1247', use_beta=True),
        WowheadObject(name="Lightfused Refulgent Copper", ids=['523284'], gathermate_id='1248', use_beta=True),
        WowheadObject(name="Rich Refulgent Copper", ids=['523282'], gathermate_id='1249', use_beta=True),
        WowheadObject(name="Primal Refulgent Copper", ids=['523285'], gathermate_id='1250', use_beta=True),
        WowheadObject(name="Wild Refulgent Copper", ids=['523286'], gathermate_id='1263', use_beta=True),

        # Umbral Tin (1251) + variants
        WowheadObject(name="Umbral Tin", ids=['523288'], gathermate_id='1251', use_beta=True),
        WowheadObject(name="Umbral Tin Seam", ids=['523290'], gathermate_id='1252', use_beta=True),
        WowheadObject(name="Voidbound Umbral Tin", ids=['523293'], gathermate_id='1253', use_beta=True),
        WowheadObject(name="Lightfused Umbral Tin", ids=['523294'], gathermate_id='1254', use_beta=True),
        WowheadObject(name="Rich Umbral Tin", ids=['523289'], gathermate_id='1255', use_beta=True),
        # Primal Umbral Tin (1256) - Not on Wowhead yet
        WowheadObject(name="Wild Umbral Tin", ids=['523292'], gathermate_id='1264', use_beta=True),

        # Brilliant Silver (1257) + variants
        WowheadObject(name="Brilliant Silver", ids=['523295'], gathermate_id='1257', use_beta=True),
        WowheadObject(name="Brilliant Silver Seam", ids=['523298'], gathermate_id='1258', use_beta=True),
        WowheadObject(name="Voidbound Brilliant Silver", ids=['523301'], gathermate_id='1259', use_beta=True),
        WowheadObject(name="Lightfused Brilliant Silver", ids=['523303'], gathermate_id='1260', use_beta=True),
        WowheadObject(name="Rich Brilliant Silver", ids=['523297'], gathermate_id='1261', use_beta=True),
        WowheadObject(name="Primal Brilliant Silver", ids=['523299'], gathermate_id='1262', use_beta=True),
        WowheadObject(name="Wild Brilliant Silver", ids=['523300'], gathermate_id='1265', use_beta=True),
    ]


def get_tww_fish():
    """Return The War Within fishing pool definitions."""
    return [
        WowheadObject(name="Calm Surfacing Ripple", ids=['451670'], gathermate_id='1118'),
        WowheadObject(name="River Bass Pool", ids=['451674'], gathermate_id='1119'),
        WowheadObject(name="Glimmerpool", ids=['451669'], gathermate_id='1120'),
        WowheadObject(name="Bloody Perch Swarm", ids=['451671'], gathermate_id='1121'),
        WowheadObject(name="Swarm of Slum Sharks", ids=['451681'], gathermate_id='1122'),
    ]


def get_tww_treasures():
    """Return The War Within treasure definitions."""
    return [
        WowheadObject(name="Disturbed Earth", ids=['422531'], gathermate_id='566'),
    ]


def get_midnight_fish():
    """Return Midnight (Beta) fishing pool definitions."""
    return [
        WowheadObject(name="Hunter Surge", ids=['570491'], gathermate_id='1131', use_beta=True),
        WowheadObject(name="Surface Ripple", ids=['570488'], gathermate_id='1132', use_beta=True),
        WowheadObject(name="Bubbling Bloom", ids=['540485'], gathermate_id='1133', use_beta=True),
        WowheadObject(name="Lost Treasures", ids=['570492'], gathermate_id='1134', use_beta=True),
        WowheadObject(name="Sunwell Swarm", ids=['547481'], gathermate_id='1135', use_beta=True),
        WowheadObject(name="Song Swarm", ids=['570487'], gathermate_id='1136', use_beta=True),
    ]


# ========================= SAVEDVARIABLES MERGER =========================

def parse_lua_table(content: str) -> dict:
    """Parse a simple Lua table into Python dict (supports nested tables with numeric keys)."""
    result = {}

    # Find all [key] = value pairs
    # Pattern matches: [number] = number, or [number] = { ... }
    pattern = r'\[(\d+)\]\s*=\s*(\d+|{[^{}]*})'

    for match in re.finditer(pattern, content):
        key = int(match.group(1))
        value_str = match.group(2)

        if value_str.startswith('{'):
            # Nested table - parse recursively
            inner_content = value_str[1:-1]  # Remove { }
            inner_dict = {}
            for inner_match in re.finditer(r'\[(\d+)\]\s*=\s*(\d+)', inner_content):
                inner_key = int(inner_match.group(1))
                inner_value = int(inner_match.group(2))
                inner_dict[inner_key] = inner_value
            result[key] = inner_dict
        else:
            result[key] = int(value_str)

    return result


def serialize_lua_table(data: dict, table_name: str) -> str:
    """Serialize Python dict back to Lua table format."""
    lines = [f"{table_name} = {{"]

    for zone_id in sorted(data.keys()):
        zone_data = data[zone_id]
        lines.append(f"[{zone_id}] = {{")
        for coord in sorted(zone_data.keys()):
            node_id = zone_data[coord]
            lines.append(f"[{coord}] = {node_id},")
        lines.append("},")

    lines.append("}")
    return "\n".join(lines)


def merge_gathermate_data(existing_content: str, new_herbs: dict, new_ores: dict, new_fish: dict, new_treasures: dict = None) -> str:
    """Merge new node data into existing GatherMate2.lua content."""

    # Parse existing databases
    herb_match = re.search(r'GatherMate2HerbDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)
    mine_match = re.search(r'GatherMate2MineDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)
    fish_match = re.search(r'GatherMate2FishDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)
    treasure_match = re.search(r'GatherMate2TreasureDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)

    existing_herbs = parse_lua_table(herb_match.group(1)) if herb_match else {}
    existing_ores = parse_lua_table(mine_match.group(1)) if mine_match else {}
    existing_fish = parse_lua_table(fish_match.group(1)) if fish_match else {}
    existing_treasures = parse_lua_table(treasure_match.group(1)) if treasure_match else {}

    # Merge new data (new data overwrites conflicts)
    def merge_db(existing: dict, new: dict) -> dict:
        merged = dict(existing)
        for zone_id, coords in new.items():
            if zone_id not in merged:
                merged[zone_id] = {}
            merged[zone_id].update(coords)
        return merged

    merged_herbs = merge_db(existing_herbs, new_herbs)
    merged_ores = merge_db(existing_ores, new_ores)
    merged_fish = merge_db(existing_fish, new_fish)
    merged_treasures = merge_db(existing_treasures, new_treasures) if new_treasures else existing_treasures

    # Find GatherMate2DB section (settings)
    db_match = re.search(r'(GatherMate2DB\s*=\s*{.*?}\s*\n)', existing_content, re.DOTALL)
    settings_section = db_match.group(1) if db_match else "GatherMate2DB = {\n}\n"

    # Build new file content
    output_parts = [settings_section]

    if merged_herbs:
        output_parts.append(serialize_lua_table(merged_herbs, "GatherMate2HerbDB"))
    if merged_ores:
        output_parts.append(serialize_lua_table(merged_ores, "GatherMate2MineDB"))
    if merged_fish:
        output_parts.append(serialize_lua_table(merged_fish, "GatherMate2FishDB"))
    if merged_treasures:
        output_parts.append(serialize_lua_table(merged_treasures, "GatherMate2TreasureDB"))

    return "\n".join(output_parts)


def aggregate_to_dict(aggregate_str: str) -> dict:
    """Convert Aggregate string output to dict format for merging."""
    result = {}
    # Parse the generated Lua table
    zone_pattern = r'\[(\d+)\]\s*=\s*{([^}]*)}'

    for zone_match in re.finditer(zone_pattern, aggregate_str):
        zone_id = int(zone_match.group(1))
        zone_content = zone_match.group(2)

        coords = {}
        for coord_match in re.finditer(r'\[(\d+)\]\s*=\s*(\d+)', zone_content):
            coord = int(coord_match.group(1))
            node_id = int(coord_match.group(2))
            coords[coord] = node_id

        if coords:
            result[zone_id] = coords

    return result


# ========================= GUI APPLICATION =========================

def get_progress_color(percent: float) -> str:
    """Calculate color from red (0%) through yellow (50%) to green (100%)."""
    percent = max(0, min(100, percent))

    if percent <= 50:
        # Red to Yellow: R stays 255, G increases
        r = 255
        g = int(255 * (percent / 50))
    else:
        # Yellow to Green: G stays 255, R decreases
        r = int(255 * (1 - (percent - 50) / 50))
        g = 255

    return f'#{r:02x}{g:02x}00'


class GatherMateMinerApp:
    def __init__(self, master: tk.Tk) -> None:
        self.master = master
        master.title("GatherMate2 Miner - GUI Edition by KUP")
        master.geometry("800x600")
        master.minsize(700, 500)

        # Setup progress bar style
        self.style = ttk.Style()
        self.style.theme_use('default')
        self.style.configure("color.Horizontal.TProgressbar",
                            troughcolor='#e0e0e0',
                            background='#ff0000',
                            thickness=25)

        # Branding Header
        header = tk.Label(master, text="GatherMate2 Miner - GUI Edition by KUP", font=("Helvetica", 18, "bold"))
        header.pack(pady=5)
        tagline = tk.Label(master, text="Mining Wowhead data for GatherMate2")
        tagline.pack(pady=2)

        # Main frame
        main_frame = tk.Frame(master)
        main_frame.pack(pady=10, padx=10, fill="x")

        # Node Type Selection
        type_frame = tk.LabelFrame(main_frame, text="Node Types", padx=10, pady=5)
        type_frame.pack(fill="x", pady=5)

        self.node_vars = {
            "Herbs": tk.BooleanVar(value=True),
            "Mining": tk.BooleanVar(value=True),
            "Treasures": tk.BooleanVar(value=False),
            "Fishing": tk.BooleanVar(value=False),
        }

        col = 0
        for name, var in self.node_vars.items():
            if name == "Fishing":
                # Fishing is disabled - Wowhead doesn't provide coordinate data for fish pools
                chk = tk.Checkbutton(type_frame, text=name, variable=var, state="disabled", fg="gray")
                chk.grid(row=0, column=col, sticky="w", padx=10)
                ToolTip(chk, "Fishing disabled\nWowhead does not provide\ncoordinate data for fishing pools")
            else:
                chk = tk.Checkbutton(type_frame, text=name, variable=var)
                chk.grid(row=0, column=col, sticky="w", padx=10)
            col += 1

        # Expansion Selection
        exp_frame = tk.LabelFrame(main_frame, text="Expansions", padx=10, pady=5)
        exp_frame.pack(fill="x", pady=5)

        # All expansions (functional) - in chronological order
        self.expansion_vars = {
            "Classic": tk.BooleanVar(value=False),
            "Burning Crusade": tk.BooleanVar(value=False),
            "Wrath of the Lich King": tk.BooleanVar(value=False),
            "Cataclysm": tk.BooleanVar(value=False),
            "Mists of Pandaria": tk.BooleanVar(value=False),
            "Warlords of Draenor": tk.BooleanVar(value=False),
            "Legion": tk.BooleanVar(value=False),
            "Battle for Azeroth": tk.BooleanVar(value=False),
            "Shadowlands": tk.BooleanVar(value=False),
            "Dragonflight": tk.BooleanVar(value=True),
            "The War Within": tk.BooleanVar(value=True),
            "Midnight (Beta)": tk.BooleanVar(value=False),
        }

        # Expansion tooltips and abbreviations
        expansion_info = {
            "Classic": ("CL", "Classic (1.0)\nAzeroth zones"),
            "Burning Crusade": ("TBC", "Burning Crusade (2.0)\nOutland zones"),
            "Wrath of the Lich King": ("WotLK", "Wrath of the Lich King (3.0)\nNorthrend zones"),
            "Cataclysm": ("Cata", "Cataclysm (4.0)\nCataclysm zones"),
            "Mists of Pandaria": ("MoP", "Mists of Pandaria (5.0)\nPandaria zones"),
            "Warlords of Draenor": ("WoD", "Warlords of Draenor (6.0)\nDraenor zones"),
            "Legion": ("Leg", "Legion (7.0)\nBroken Isles zones"),
            "Battle for Azeroth": ("BfA", "Battle for Azeroth (8.0)\nZandalar & Kul Tiras"),
            "Shadowlands": ("SL", "Shadowlands (9.0)\nShadowlands zones"),
            "Dragonflight": ("DF", "Dragonflight (10.0)\nDragon Isles zones"),
            "The War Within": ("TWW", "The War Within (11.0)\nKhaz Algar zones"),
            "Midnight (Beta)": ("MD", "Midnight (12.0 Beta)\nQuel'Thalas zones"),
        }

        # Row 0: Classic expansions (CL - MoP)
        tk.Label(exp_frame, text="Classic:", font=("Helvetica", 8, "bold"), fg="darkblue").grid(row=0, column=0, sticky="w")
        col = 1
        classic_exps = ["Classic", "Burning Crusade", "Wrath of the Lich King", "Cataclysm", "Mists of Pandaria"]
        for name in classic_exps:
            abbrev, tooltip = expansion_info[name]
            chk = tk.Checkbutton(exp_frame, text=f"{abbrev}", variable=self.expansion_vars[name])
            chk.grid(row=0, column=col, sticky="w", padx=5)
            ToolTip(chk, tooltip)
            col += 1

        # Row 1: Modern expansions (WoD - MD)
        tk.Label(exp_frame, text="Modern:", font=("Helvetica", 8, "bold"), fg="green").grid(row=1, column=0, sticky="w", pady=(5, 0))
        col = 1
        modern_exps = ["Warlords of Draenor", "Legion", "Battle for Azeroth", "Shadowlands", "Dragonflight", "The War Within", "Midnight (Beta)"]
        for name in modern_exps:
            abbrev, tooltip = expansion_info[name]
            chk = tk.Checkbutton(exp_frame, text=f"{abbrev}", variable=self.expansion_vars[name])
            chk.grid(row=1, column=col, sticky="w", padx=5, pady=(5, 0))
            ToolTip(chk, tooltip)
            col += 1

        # Output directory selector
        dir_frame = tk.LabelFrame(main_frame, text="Output", padx=10, pady=5)
        dir_frame.pack(fill="x", pady=5)

        self.out_dir_var = tk.StringVar(value=os.path.join(os.getcwd(), "DATA"))
        tk.Label(dir_frame, text="Output Directory:").grid(row=0, column=0, sticky="w")
        out_entry = tk.Entry(dir_frame, textvariable=self.out_dir_var, width=50)
        out_entry.grid(row=0, column=1, sticky="we", padx=5)
        tk.Button(dir_frame, text="Browse", command=self.choose_dir).grid(row=0, column=2, sticky="w")
        dir_frame.columnconfigure(1, weight=1)

        # SavedVariables auto-write section
        sv_frame = tk.LabelFrame(main_frame, text="GatherMate2 SavedVariables (Auto-Import)", padx=10, pady=5)
        sv_frame.pack(fill="x", pady=5)

        # Auto-write checkbox
        self.auto_write_var = tk.BooleanVar(value=False)
        auto_write_chk = tk.Checkbutton(sv_frame, text="Write to SavedVariables after mining",
                                         variable=self.auto_write_var, command=self._toggle_sv_path)
        auto_write_chk.grid(row=0, column=0, columnspan=3, sticky="w")

        # SavedVariables path
        tk.Label(sv_frame, text="GatherMate2.lua:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.sv_path_var = tk.StringVar(value="")
        self.sv_entry = tk.Entry(sv_frame, textvariable=self.sv_path_var, width=50, state="disabled")
        self.sv_entry.grid(row=1, column=1, sticky="we", padx=5, pady=(5, 0))
        self.sv_browse_btn = tk.Button(sv_frame, text="Browse", command=self.choose_sv_file, state="disabled")
        self.sv_browse_btn.grid(row=1, column=2, sticky="w", pady=(5, 0))
        sv_frame.columnconfigure(1, weight=1)

        # Warning label
        self.sv_warning = tk.Label(sv_frame, text="WARNING: Game must NOT be running when writing to SavedVariables!",
                                   fg="red", font=("Helvetica", 9, "bold"))
        self.sv_warning.grid(row=2, column=0, columnspan=3, sticky="w", pady=(5, 0))
        self.sv_warning.grid_remove()  # Hidden by default

        # Hint label
        self.sv_hint = tk.Label(sv_frame,
                                text="TIP: Enable 'Storage' for DF/TWW/MD in GatherMate2 options to separate expansion data",
                                fg="blue", font=("Helvetica", 8))
        self.sv_hint.grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        self.sv_hint.grid_remove()  # Hidden by default

        # Buttons
        btn_frame = tk.Frame(master)
        btn_frame.pack(pady=10)

        self.run_btn = tk.Button(btn_frame, text="Start Mining", command=self.run,
                                  font=("Helvetica", 12, "bold"), bg="#4CAF50", fg="white",
                                  padx=20, pady=5)
        self.run_btn.pack(side="left", padx=5)

        self.stop_btn = tk.Button(btn_frame, text="Stop", command=self.stop,
                                   font=("Helvetica", 12), state="disabled",
                                   padx=20, pady=5)
        self.stop_btn.pack(side="left", padx=5)

        # Progress bar with color gradient
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(master, variable=self.progress_var, maximum=100,
                                        style="color.Horizontal.TProgressbar")
        self.progress.pack(fill="x", padx=10, pady=5)

        # Trace progress changes to update color
        self.progress_var.trace_add("write", self._on_progress_change)

        # Status label
        self.status_var = tk.StringVar(value="Ready")
        status_label = tk.Label(master, textvariable=self.status_var, anchor="w")
        status_label.pack(fill="x", padx=10)

        # Log window
        log_frame = tk.LabelFrame(master, text="Log", padx=5, pady=5)
        log_frame.pack(padx=10, pady=5, fill="both", expand=True)

        self.log = scrolledtext.ScrolledText(log_frame, width=80, height=15, state="disabled")
        self.log.pack(fill="both", expand=True)

        # Worker thread control
        self.running = False
        self.worker_thread = None

        # Zone map
        self.zone_map = get_zone_map()

    def _on_progress_change(self, *args) -> None:
        """Update progress bar color based on current value."""
        percent = self.progress_var.get()
        color = get_progress_color(percent)
        self.style.configure("color.Horizontal.TProgressbar", background=color)

    def log_write(self, text: str) -> None:
        """Write to the log widget (thread-safe)."""
        self.master.after(0, self._log_write_impl, text)

    def _log_write_impl(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.yview("end")
        self.log.configure(state="disabled")

    def choose_dir(self) -> None:
        path = filedialog.askdirectory(initialdir=self.out_dir_var.get())
        if path:
            self.out_dir_var.set(path)

    def _toggle_sv_path(self) -> None:
        """Toggle SavedVariables path input based on checkbox."""
        if self.auto_write_var.get():
            self.sv_entry.configure(state="normal")
            self.sv_browse_btn.configure(state="normal")
            self.sv_warning.grid()
            self.sv_hint.grid()
        else:
            self.sv_entry.configure(state="disabled")
            self.sv_browse_btn.configure(state="disabled")
            self.sv_warning.grid_remove()
            self.sv_hint.grid_remove()

    def choose_sv_file(self) -> None:
        """Browse for GatherMate2.lua SavedVariables file."""
        initial_dir = ""
        # Try to find WoW WTF folder
        for drive in ["C:", "D:", "E:", "F:"]:
            for folder in ["World of Warcraft", "Blizz/World of Warcraft", "Games/World of Warcraft"]:
                for variant in ["_retail_", "_beta_", "_classic_", "_classic_era_"]:
                    test_path = os.path.join(drive, folder, variant, "WTF", "Account")
                    if os.path.exists(test_path):
                        initial_dir = test_path
                        break

        path = filedialog.askopenfilename(
            title="Select GatherMate2.lua SavedVariables file",
            initialdir=initial_dir or os.path.expanduser("~"),
            filetypes=[("Lua files", "*.lua"), ("All files", "*.*")],
            defaultextension=".lua"
        )
        if path:
            if "GatherMate2.lua" in path:
                self.sv_path_var.set(path)
            else:
                messagebox.showwarning("Wrong file",
                    "Please select a GatherMate2.lua file from:\n"
                    "WTF/Account/ACCOUNTNAME/SavedVariables/GatherMate2.lua")

    def stop(self) -> None:
        self.running = False
        self.status_var.set("Stopping...")

    def run(self) -> None:
        """Start the mining process in a background thread."""
        self.running = True
        self.run_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress_var.set(0)

        # Clear log
        self.log.configure(state="normal")
        self.log.delete(1.0, "end")
        self.log.configure(state="disabled")

        # Set global log callback
        set_log_callback(self.log_write)

        # Start worker thread
        self.worker_thread = threading.Thread(target=self.mining_worker, daemon=True)
        self.worker_thread.start()

    def mining_worker(self) -> None:
        """Background worker that performs the actual mining."""
        try:
            out_dir = self.out_dir_var.get()
            os.makedirs(out_dir, exist_ok=True)

            # Build processing queue: list of (expansion_name, node_type, nodes_list, type_key)
            processing_queue = []

            # Define expansion order and their getters (chronological)
            expansions = [
                ("Classic", "CL", {
                    "herbs": get_classic_herbs,
                    "ores": get_classic_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Burning Crusade", "TBC", {
                    "herbs": get_tbc_herbs,
                    "ores": get_tbc_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Wrath of the Lich King", "WotLK", {
                    "herbs": get_wotlk_herbs,
                    "ores": get_wotlk_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Cataclysm", "Cata", {
                    "herbs": get_cata_herbs,
                    "ores": get_cata_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Mists of Pandaria", "MoP", {
                    "herbs": get_mop_herbs,
                    "ores": get_mop_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Warlords of Draenor", "WoD", {
                    "herbs": get_wod_herbs,
                    "ores": get_wod_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Legion", "Leg", {
                    "herbs": get_legion_herbs,
                    "ores": get_legion_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Battle for Azeroth", "BfA", {
                    "herbs": get_bfa_herbs,
                    "ores": get_bfa_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Shadowlands", "SL", {
                    "herbs": get_sl_herbs,
                    "ores": get_sl_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("Dragonflight", "DF", {
                    "herbs": get_dragonflight_herbs,
                    "ores": get_dragonflight_ores,
                    "fish": None,
                    "treasures": None,
                }),
                ("The War Within", "TWW", {
                    "herbs": get_tww_herbs,
                    "ores": get_tww_ores,
                    "fish": get_tww_fish,
                    "treasures": get_tww_treasures,
                }),
                ("Midnight (Beta)", "MD", {
                    "herbs": get_midnight_herbs,
                    "ores": get_midnight_ores,
                    "fish": get_midnight_fish,
                    "treasures": None,
                }),
            ]

            # Build queue in order: DF herbs, DF ores, TWW herbs, TWW ores, MD herbs, MD ores, etc.
            for exp_name, exp_short, getters in expansions:
                if not self.expansion_vars[exp_name].get():
                    continue

                if self.node_vars["Herbs"].get() and getters["herbs"]:
                    nodes = getters["herbs"]()
                    if nodes:
                        processing_queue.append((exp_name, exp_short, "Herbs", nodes, "herbs"))

                if self.node_vars["Mining"].get() and getters["ores"]:
                    nodes = getters["ores"]()
                    if nodes:
                        processing_queue.append((exp_name, exp_short, "Mining", nodes, "ores"))

                if self.node_vars["Treasures"].get() and getters["treasures"]:
                    nodes = getters["treasures"]()
                    if nodes:
                        processing_queue.append((exp_name, exp_short, "Treasures", nodes, "treasures"))

                if self.node_vars["Fishing"].get() and getters["fish"]:
                    nodes = getters["fish"]()
                    if nodes:
                        processing_queue.append((exp_name, exp_short, "Fishing", nodes, "fish"))

            # Calculate total nodes
            total_nodes = sum(len(item[3]) for item in processing_queue)
            if total_nodes == 0:
                self.log_write("No nodes selected!\n")
                self.master.after(0, self.mining_complete, False)
                return

            processed = 0

            # Collect all processed nodes by type for final Lua output
            all_herbs = []
            all_ores = []
            all_fish = []
            all_treasures = []

            # Statistics tracking
            all_stats = {}  # {zone_id: {zone_name: str, herbs: int, ores: int, fish: int, treasures: int}}

            # Node cache for tracking new nodes - PER EXPANSION
            # Expansion abbreviation mapping
            expansion_abbrevs = {
                "Classic": "CL",
                "Burning Crusade": "TBC",
                "Wrath of the Lich King": "WotLK",
                "Cataclysm": "Cata",
                "Mists of Pandaria": "MoP",
                "Warlords of Draenor": "WoD",
                "Legion": "Leg",
                "Battle for Azeroth": "BfA",
                "Shadowlands": "SL",
                "Dragonflight": "DF",
                "The War Within": "TWW",
                "Midnight (Beta)": "MD",
            }

            # Load all expansion caches and merge into old_cache
            old_cache = {}
            expansion_caches = {}  # Store loaded caches per expansion
            selected_expansions = set()  # Track which expansions are being processed

            self.log_write("Loading expansion caches...\n")
            for exp_name, exp_short, getters in expansions:
                if not self.expansion_vars[exp_name].get():
                    continue
                selected_expansions.add(exp_short)

                # Load cache for this expansion
                cache_file = os.path.join(out_dir, f"node_cache_{exp_short}.json")
                if os.path.exists(cache_file):
                    try:
                        with open(cache_file, "r", encoding="utf-8") as f:
                            cache_data = json.load(f)
                            exp_cache = cache_data.get("nodes", {})
                            expansion_caches[exp_short] = exp_cache
                            old_cache.update(exp_cache)
                            last_run = cache_data.get("last_run", "never")
                            node_count = sum(len(v) for v in exp_cache.values())
                            self.log_write(f"  {exp_short}: {node_count} nodes (last: {last_run})\n")
                    except Exception as e:
                        self.log_write(f"  {exp_short}: Could not load cache: {e}\n")
                        expansion_caches[exp_short] = {}
                else:
                    self.log_write(f"  {exp_short}: No cache found (first run)\n")
                    expansion_caches[exp_short] = {}

            if old_cache:
                total_cached = sum(len(v) for v in old_cache.values())
                self.log_write(f"Total cached nodes: {total_cached}\n\n")
            else:
                self.log_write("No previous caches found\n\n")

            # New caches per expansion
            new_caches = {exp_short: {} for exp_short in selected_expansions}

            def count_node_coords(node, node_type: str, expansion_short: str):
                """Count total coordinates for a node."""
                total = 0
                new_count = 0
                for zone, coords in node.coordinates.items():
                    total += len(coords)
                    # Track per-zone stats by type
                    if zone.id not in all_stats:
                        all_stats[zone.id] = {"name": zone.name, "herbs": 0, "ores": 0, "fish": 0, "treasures": 0}
                    all_stats[zone.id][node_type] += len(coords)

                    # Cache coordinates in the expansion-specific cache
                    cache_key = f"{zone.id}_{node_type}"
                    if cache_key not in new_caches[expansion_short]:
                        new_caches[expansion_short][cache_key] = {}

                    for coord in coords:
                        coord_key = str(coord.as_gatherer_coord())
                        new_caches[expansion_short][cache_key][coord_key] = node.gathermate_id

                        # Check if this is a new node (check against ALL old caches)
                        old_zone_cache = old_cache.get(cache_key, {})
                        if coord_key not in old_zone_cache:
                            new_count += 1

                return total, new_count

            # Track new nodes
            total_new_herbs = 0
            total_new_ores = 0
            total_new_fish = 0
            total_new_treasures = 0

            # Process queue in order (DF herbs, DF ores, TWW herbs, TWW ores, MD herbs, MD ores)
            for exp_name, exp_short, node_type_name, nodes_list, type_key in processing_queue:
                if not self.running:
                    break

                self.master.after(0, self.status_var.set, f"Processing {exp_short} {node_type_name}...")
                self.log_write(f"\n=== [{exp_short}] Processing {len(nodes_list)} {node_type_name.lower()} types ===\n")

                type_total = 0
                type_new = 0

                for node in nodes_list:
                    if not self.running:
                        break
                    self.log_write(f"Fetching: {node.name}")
                    node.fetch_data(self.zone_map, WOWHEAD_ZONE_SUPPRESSION)
                    count, new_count = count_node_coords(node, type_key, exp_short)
                    type_total += count
                    type_new += new_count

                    if new_count > 0:
                        self.log_write(f" -> {count} nodes ({new_count} NEW)\n")
                    else:
                        self.log_write(f" -> {count} nodes\n")

                    processed += 1
                    self.master.after(0, self.progress_var.set, (processed / total_nodes) * 100)

                # Collect nodes for final output
                if type_key == "herbs":
                    all_herbs.extend(nodes_list)
                    total_new_herbs += type_new
                elif type_key == "ores":
                    all_ores.extend(nodes_list)
                    total_new_ores += type_new
                elif type_key == "treasures":
                    all_treasures.extend(nodes_list)
                    total_new_treasures += type_new
                elif type_key == "fish":
                    all_fish.extend(nodes_list)
                    total_new_fish += type_new

                self.log_write(f"[{exp_short}] {node_type_name}: {type_total} nodes, {type_new} new\n")

            # Save Lua files
            self.log_write("\n" + "=" * 70 + "\n")
            self.log_write("=== SAVING LUA FILES ===\n")
            self.log_write("=" * 70 + "\n")

            if all_herbs and self.running:
                herb_file = os.path.join(out_dir, "Mined_HerbalismData.lua")
                with open(herb_file, "w", encoding="utf-8") as f:
                    f.write(str(Aggregate("Herb", all_herbs)))
                herb_count = sum(len(h.coordinates.get(z, [])) for h in all_herbs for z in h.coordinates)
                self.log_write(f"Saved: {herb_file} ({herb_count} herbs, {total_new_herbs} new)\n")

            if all_ores and self.running:
                ore_file = os.path.join(out_dir, "Mined_MiningData.lua")
                with open(ore_file, "w", encoding="utf-8") as f:
                    f.write(str(Aggregate("Mine", all_ores)))
                ore_count = sum(len(o.coordinates.get(z, [])) for o in all_ores for z in o.coordinates)
                self.log_write(f"Saved: {ore_file} ({ore_count} ores, {total_new_ores} new)\n")

            if all_fish and self.running:
                fish_file = os.path.join(out_dir, "Mined_FishData.lua")
                with open(fish_file, "w", encoding="utf-8") as f:
                    f.write(str(Aggregate("Fish", all_fish)))
                fish_count = sum(len(fp.coordinates.get(z, [])) for fp in all_fish for z in fp.coordinates)
                self.log_write(f"Saved: {fish_file} ({fish_count} pools, {total_new_fish} new)\n")

            if all_treasures and self.running:
                treasure_file = os.path.join(out_dir, "Mined_TreasureData.lua")
                with open(treasure_file, "w", encoding="utf-8") as f:
                    f.write(str(Aggregate("Treasure", all_treasures)))
                treasure_count = sum(len(t.coordinates.get(z, [])) for t in all_treasures for z in t.coordinates)
                self.log_write(f"Saved: {treasure_file} ({treasure_count} treasures, {total_new_treasures} new)\n")

            # Write to SavedVariables if enabled
            if self.auto_write_var.get() and self.sv_path_var.get() and self.running:
                sv_path = self.sv_path_var.get()
                self.log_write("\n" + "=" * 70 + "\n")
                self.log_write("=== WRITING TO SAVEDVARIABLES ===\n")
                self.log_write("=" * 70 + "\n")

                try:
                    # Read existing SavedVariables
                    if os.path.exists(sv_path):
                        with open(sv_path, "r", encoding="utf-8") as f:
                            existing_content = f.read()
                        self.log_write(f"Read existing: {sv_path}\n")
                    else:
                        existing_content = "GatherMate2DB = {\n}\n"
                        self.log_write("Creating new SavedVariables file\n")

                    # Convert aggregates to dict format
                    new_herbs_dict = aggregate_to_dict(str(Aggregate("Herb", all_herbs))) if all_herbs else {}
                    new_ores_dict = aggregate_to_dict(str(Aggregate("Mine", all_ores))) if all_ores else {}
                    new_fish_dict = aggregate_to_dict(str(Aggregate("Fish", all_fish))) if all_fish else {}
                    new_treasures_dict = aggregate_to_dict(str(Aggregate("Treasure", all_treasures))) if all_treasures else {}

                    # Create backup
                    if os.path.exists(sv_path):
                        backup_path = sv_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        import shutil
                        shutil.copy2(sv_path, backup_path)
                        self.log_write(f"Backup created: {backup_path}\n")

                    # Merge and write
                    merged_content = merge_gathermate_data(existing_content, new_herbs_dict, new_ores_dict, new_fish_dict, new_treasures_dict)

                    with open(sv_path, "w", encoding="utf-8") as f:
                        f.write(merged_content)

                    total_merged = len(new_herbs_dict) + len(new_ores_dict) + len(new_fish_dict) + len(new_treasures_dict)
                    self.log_write(f"SUCCESS: Merged data into {sv_path}\n")
                    self.log_write(f"  Herb zones:     {len(new_herbs_dict)}\n")
                    self.log_write(f"  Ore zones:      {len(new_ores_dict)}\n")
                    self.log_write(f"  Fish zones:     {len(new_fish_dict)}\n")
                    self.log_write(f"  Treasure zones: {len(new_treasures_dict)}\n")
                    self.log_write("=" * 70 + "\n")

                except Exception as e:
                    self.log_write(f"ERROR writing to SavedVariables: {e}\n")
                    self.log_write("Your mined Lua files are still available in the output directory.\n")

            # Print zone statistics with expansion abbreviations
            if all_stats and self.running:
                self.log_write("\n" + "=" * 86 + "\n")
                self.log_write("=== ZONE STATISTICS ===\n")
                self.log_write("=" * 86 + "\n")
                self.log_write(f"{'MapID':<8} {'Zone Name':<26} {'Exp':<5} {'Herbs':>8} {'Ores':>8} {'Treas':>8} {'Fish':>8} {'Total':>8}\n")
                self.log_write("-" * 86 + "\n")

                total_herbs = 0
                total_ores = 0
                total_treasures = 0
                total_fish = 0

                for zone_id in sorted(all_stats.keys(), key=lambda x: int(x)):
                    info = all_stats[zone_id]
                    zone_total = info['herbs'] + info['ores'] + info['treasures'] + info['fish']
                    total_herbs += info['herbs']
                    total_ores += info['ores']
                    total_treasures += info['treasures']
                    total_fish += info['fish']
                    exp_abbrev = ZONE_EXPANSION.get(zone_id, "???")
                    self.log_write(f"{zone_id:<8} {info['name']:<26} {exp_abbrev:<5} {info['herbs']:>8} {info['ores']:>8} {info['treasures']:>8} {info['fish']:>8} {zone_total:>8}\n")

                self.log_write("-" * 86 + "\n")
                grand_total = total_herbs + total_ores + total_treasures + total_fish
                self.log_write(f"{'TOTAL':<8} {'':<26} {'':<5} {total_herbs:>8} {total_ores:>8} {total_treasures:>8} {total_fish:>8} {grand_total:>8}\n")
                self.log_write("=" * 86 + "\n")

            # Save per-expansion caches and show new nodes summary
            if new_caches and self.running:
                # Calculate total new nodes
                total_new = total_new_herbs + total_new_ores + total_new_treasures + total_new_fish

                # Save each expansion cache separately
                self.log_write("\n" + "=" * 70 + "\n")
                self.log_write("=== SAVING EXPANSION CACHES ===\n")
                self.log_write("=" * 70 + "\n")

                for exp_short, exp_cache in new_caches.items():
                    if not exp_cache:
                        continue

                    cache_file = os.path.join(out_dir, f"node_cache_{exp_short}.json")
                    cache_data = {
                        "expansion": exp_short,
                        "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "nodes": exp_cache
                    }
                    try:
                        with open(cache_file, "w", encoding="utf-8") as f:
                            json.dump(cache_data, f, indent=2)
                        node_count = sum(len(v) for v in exp_cache.values())
                        self.log_write(f"  {exp_short}: {node_count} nodes saved\n")
                    except Exception as e:
                        self.log_write(f"  {exp_short}: Failed to save cache: {e}\n")

                # Show new nodes summary
                self.log_write("\n" + "=" * 70 + "\n")
                self.log_write("=== NEW NODES SUMMARY ===\n")
                self.log_write("=" * 70 + "\n")
                if old_cache:
                    self.log_write(f"New Herbs:        {total_new_herbs:>6}\n")
                    self.log_write(f"New Ores:         {total_new_ores:>6}\n")
                    self.log_write(f"New Fishing:      {total_new_fish:>6}\n")
                    self.log_write("-" * 30 + "\n")
                    self.log_write(f"TOTAL NEW NODES:  {total_new:>6}\n")
                    self.log_write("=" * 70 + "\n")

                    if total_new > 0:
                        self.log_write(f"\n*** {total_new} NEW NODES FOUND SINCE LAST RUN! ***\n")
                    else:
                        self.log_write("\nNo new nodes since last run.\n")
                else:
                    self.log_write("First run - all nodes are considered new.\n")
                    total_cache_nodes = sum(sum(len(v) for v in cache.values()) for cache in new_caches.values())
                    self.log_write(f"Total nodes cached: {total_cache_nodes}\n")
                    self.log_write("=" * 70 + "\n")

            self.master.after(0, self.mining_complete, self.running)

        except Exception as e:
            self.log_write(f"\nError: {e}\n")
            self.master.after(0, self.mining_complete, False)

    def mining_complete(self, success: bool) -> None:
        """Called when mining is complete."""
        self.running = False
        self.run_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.progress_var.set(100 if success else 0)

        if success:
            self.status_var.set("Mining complete!")
            self.log_write("\n=== Mining complete! ===\n")
            messagebox.showinfo("GatherMate2 Miner",
                               f"Mining complete!\n\nFiles saved to:\n{self.out_dir_var.get()}")
        else:
            self.status_var.set("Mining stopped or failed")
            self.log_write("\n=== Mining stopped ===\n")


def main():
    root = tk.Tk()
    app = GatherMateMinerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
