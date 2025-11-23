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
    # Dragonflight
    "2022": "DF", "2023": "DF", "2024": "DF", "2025": "DF", "2085": "DF",
    "2112": "DF", "2133": "DF", "2151": "DF", "2200": "DF", "2199": "DF", "2262": "DF", "2239": "DF",
    # The War Within
    "2248": "TWW", "2215": "TWW", "2214": "TWW", "2255": "TWW", "2213": "TWW", "2339": "TWW", "2256": "TWW",
    # Midnight
    "2552": "MD", "2553": "MD", "2554": "MD", "2555": "MD", "2556": "MD", "2557": "MD",
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
        '15947': Zone("Zul'Aman", "2554", skip_uimap_check=True),
        '15355': Zone("Harandar", "2553", skip_uimap_check=True),
        '15968': Zone("Eversong Woods", "2552", skip_uimap_check=True),
        '16194': Zone("Atal'Aman", "2555", skip_uimap_check=True),
        '15458': Zone("Voidstorm", "2556", skip_uimap_check=True),
        '15958': Zone("Masters' Perch", "2557", skip_uimap_check=True),
    }


# ========================= NODE DEFINITIONS =========================

def get_all_herbs():
    """Return all herb definitions."""
    return [
        # Vanilla
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
        WowheadObject(name="Icecap", ids=['176588'], gathermate_id='429'),
        WowheadObject(name="Black Lotus", ids=['176589'], gathermate_id='431'),
    ]


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


def merge_gathermate_data(existing_content: str, new_herbs: dict, new_ores: dict, new_fish: dict) -> str:
    """Merge new node data into existing GatherMate2.lua content."""

    # Parse existing databases
    herb_match = re.search(r'GatherMate2HerbDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)
    mine_match = re.search(r'GatherMate2MineDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)
    fish_match = re.search(r'GatherMate2FishDB\s*=\s*{(.*?)}\s*(?=GatherMate2|$)', existing_content, re.DOTALL)

    existing_herbs = parse_lua_table(herb_match.group(1)) if herb_match else {}
    existing_ores = parse_lua_table(mine_match.group(1)) if mine_match else {}
    existing_fish = parse_lua_table(fish_match.group(1)) if fish_match else {}

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
            "Fishing": tk.BooleanVar(value=False),
        }

        col = 0
        for name, var in self.node_vars.items():
            chk = tk.Checkbutton(type_frame, text=name, variable=var)
            chk.grid(row=0, column=col, sticky="w", padx=10)
            col += 1

        # Expansion Selection
        exp_frame = tk.LabelFrame(main_frame, text="Expansions", padx=10, pady=5)
        exp_frame.pack(fill="x", pady=5)

        # Active expansions (functional)
        self.expansion_vars = {
            "Dragonflight": tk.BooleanVar(value=True),
            "The War Within": tk.BooleanVar(value=True),
            "Midnight (Beta)": tk.BooleanVar(value=False),
        }

        # Disabled expansions (coming soon preview) - chronological order
        disabled_expansions = [
            ("Classic", "CL"),
            ("Burning Crusade", "TBC"),
            ("Wrath of the Lich King", "WotLK"),
            ("Cataclysm", "Cata"),
            ("Mists of Pandaria", "MoP"),
            ("Warlords of Draenor", "WoD"),
            ("Legion", "Leg"),
            ("Battle for Azeroth", "BfA"),
            ("Shadowlands", "SL"),
        ]

        # Row 0: Disabled expansions (Classic - MoP)
        tk.Label(exp_frame, text="Coming Soon:", font=("Helvetica", 8, "italic"), fg="gray").grid(row=0, column=0, sticky="w")
        col = 1
        for name, abbrev in disabled_expansions[:5]:
            chk = tk.Checkbutton(exp_frame, text=f"{abbrev}", state="disabled", fg="gray")
            chk.grid(row=0, column=col, sticky="w", padx=5)
            ToolTip(chk, f"{name}\n(Coming Soon)")
            col += 1

        # Row 1: Disabled expansions (WoD - Shadowlands)
        col = 1
        for name, abbrev in disabled_expansions[5:]:
            chk = tk.Checkbutton(exp_frame, text=f"{abbrev}", state="disabled", fg="gray")
            chk.grid(row=1, column=col, sticky="w", padx=5)
            ToolTip(chk, f"{name}\n(Coming Soon)")
            col += 1

        # Row 2: Active expansions (DF, TWW, MD)
        tk.Label(exp_frame, text="Active:", font=("Helvetica", 8, "bold"), fg="green").grid(row=2, column=0, sticky="w", pady=(5, 0))
        col = 1
        active_tooltips = {
            "Dragonflight": "Dragonflight (10.0)\nDragon Isles zones",
            "The War Within": "The War Within (11.0)\nKhaz Algar zones",
            "Midnight (Beta)": "Midnight (12.0 Beta)\nQuel'Thalas zones"
        }
        for name, var in self.expansion_vars.items():
            abbrev = {"Dragonflight": "DF", "The War Within": "TWW", "Midnight (Beta)": "MD"}.get(name, name)
            chk = tk.Checkbutton(exp_frame, text=f"{abbrev}", variable=var)
            chk.grid(row=2, column=col, sticky="w", padx=5, pady=(5, 0))
            ToolTip(chk, active_tooltips.get(name, name))
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

            # Define expansion order and their getters
            expansions = [
                ("Dragonflight", "DF", {
                    "herbs": get_dragonflight_herbs,
                    "ores": get_dragonflight_ores,
                    "fish": None,
                }),
                ("The War Within", "TWW", {
                    "herbs": get_tww_herbs,
                    "ores": get_tww_ores,
                    "fish": get_tww_fish,
                }),
                ("Midnight (Beta)", "MD", {
                    "herbs": get_midnight_herbs,
                    "ores": get_midnight_ores,
                    "fish": get_midnight_fish,
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

            # Statistics tracking
            all_stats = {}  # {zone_id: {zone_name: str, herbs: int, ores: int, fish: int}}

            # Node cache for tracking new nodes
            cache_file = os.path.join(out_dir, "node_cache.json")
            old_cache = {}
            new_cache = {}

            # Load previous cache
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cache_data = json.load(f)
                        old_cache = cache_data.get("nodes", {})
                        last_run = cache_data.get("last_run", "never")
                        self.log_write(f"Loaded previous cache from: {last_run}\n")
                        self.log_write(f"Previous total: {sum(len(v) for v in old_cache.values())} nodes\n\n")
                except Exception as e:
                    self.log_write(f"Could not load cache: {e}\n\n")

            def count_node_coords(node, node_type: str):
                """Count total coordinates for a node."""
                total = 0
                new_count = 0
                for zone, coords in node.coordinates.items():
                    total += len(coords)
                    # Track per-zone stats by type
                    if zone.id not in all_stats:
                        all_stats[zone.id] = {"name": zone.name, "herbs": 0, "ores": 0, "fish": 0}
                    all_stats[zone.id][node_type] += len(coords)

                    # Cache coordinates
                    cache_key = f"{zone.id}_{node_type}"
                    if cache_key not in new_cache:
                        new_cache[cache_key] = {}

                    for coord in coords:
                        coord_key = str(coord.as_gatherer_coord())
                        new_cache[cache_key][coord_key] = node.gathermate_id

                        # Check if this is a new node
                        old_zone_cache = old_cache.get(cache_key, {})
                        if coord_key not in old_zone_cache:
                            new_count += 1

                return total, new_count

            # Track new nodes
            total_new_herbs = 0
            total_new_ores = 0
            total_new_fish = 0

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
                    count, new_count = count_node_coords(node, type_key)
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

                    # Create backup
                    if os.path.exists(sv_path):
                        backup_path = sv_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                        import shutil
                        shutil.copy2(sv_path, backup_path)
                        self.log_write(f"Backup created: {backup_path}\n")

                    # Merge and write
                    merged_content = merge_gathermate_data(existing_content, new_herbs_dict, new_ores_dict, new_fish_dict)

                    with open(sv_path, "w", encoding="utf-8") as f:
                        f.write(merged_content)

                    total_merged = len(new_herbs_dict) + len(new_ores_dict) + len(new_fish_dict)
                    self.log_write(f"SUCCESS: Merged data into {sv_path}\n")
                    self.log_write(f"  Herb zones: {len(new_herbs_dict)}\n")
                    self.log_write(f"  Ore zones:  {len(new_ores_dict)}\n")
                    self.log_write(f"  Fish zones: {len(new_fish_dict)}\n")
                    self.log_write("=" * 70 + "\n")

                except Exception as e:
                    self.log_write(f"ERROR writing to SavedVariables: {e}\n")
                    self.log_write("Your mined Lua files are still available in the output directory.\n")

            # Print zone statistics with expansion abbreviations
            if all_stats and self.running:
                self.log_write("\n" + "=" * 78 + "\n")
                self.log_write("=== ZONE STATISTICS ===\n")
                self.log_write("=" * 78 + "\n")
                self.log_write(f"{'MapID':<8} {'Zone Name':<26} {'Exp':<5} {'Herbs':>8} {'Ores':>8} {'Fish':>8} {'Total':>8}\n")
                self.log_write("-" * 78 + "\n")

                total_herbs = 0
                total_ores = 0
                total_fish = 0

                for zone_id in sorted(all_stats.keys(), key=lambda x: int(x)):
                    info = all_stats[zone_id]
                    zone_total = info['herbs'] + info['ores'] + info['fish']
                    total_herbs += info['herbs']
                    total_ores += info['ores']
                    total_fish += info['fish']
                    exp_abbrev = ZONE_EXPANSION.get(zone_id, "???")
                    self.log_write(f"{zone_id:<8} {info['name']:<26} {exp_abbrev:<5} {info['herbs']:>8} {info['ores']:>8} {info['fish']:>8} {zone_total:>8}\n")

                self.log_write("-" * 78 + "\n")
                grand_total = total_herbs + total_ores + total_fish
                self.log_write(f"{'TOTAL':<8} {'':<26} {'':<5} {total_herbs:>8} {total_ores:>8} {total_fish:>8} {grand_total:>8}\n")
                self.log_write("=" * 70 + "\n")

            # Save cache and show new nodes summary
            if new_cache and self.running:
                # Calculate total new nodes
                total_new = total_new_herbs + total_new_ores + total_new_fish

                # Save cache to JSON
                cache_data = {
                    "last_run": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "nodes": new_cache
                }
                try:
                    with open(cache_file, "w", encoding="utf-8") as f:
                        json.dump(cache_data, f, indent=2)
                    self.log_write(f"\nCache saved to: {cache_file}\n")
                except Exception as e:
                    self.log_write(f"\nFailed to save cache: {e}\n")

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
                    total_cache_nodes = sum(len(v) for v in new_cache.values())
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
