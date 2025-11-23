#!/usr/bin/env python3
import requests
import re
import json
from dataclasses import dataclass
import typing
import math
import html
import csv
import time

# Headers to avoid being blocked by Wowhead
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


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

        # Use beta URL for Midnight expansion nodes
        base_url = 'https://www.wowhead.com/beta/object=' if use_beta else 'https://www.wowhead.com/object='

        for object_id in self.ids:
            time.sleep(0.5)  # Rate limiting to avoid being blocked
            result = requests.get(f'{base_url}{object_id}', headers=HEADERS)
            title = html.unescape(re.search(r'<meta property="og:title" content="(.*)">', result.text).group(1))
            data = re.search(r'var g_mapperData = (.*);', result.text)
            zones = re.findall(r'myMapper.update\({\s+zone: (\d+),\s+level: \d+,\s+}\);\s+WH.setSelectedLink\(this, \'mapper\'\);\s+return false;\s+" onmousedown="return false">([^<]+)</a>', result.text, re.M)
            zonemap = dict(zones)
            try:
                data_parsed = json.loads(data.group(1))
            except AttributeError:
                print(f"No locations for {object_id} ({self.name})")
                continue
            for zone in data_parsed:
                wow_zone = WOWHEAD_ZONE_MAP.get(zone)
                if wow_zone is None:
                    if zone not in WOWHEAD_ZONE_SUPPRESSION:
                        print(f"Found unlisted zone: {zone}")
                    continue
                if wow_zone.name != zonemap[zone]:
                    print(f"Zone name mismatch on {zone}: {wow_zone.name} != {zonemap[zone]}")
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
        if self.name != title:
            print(f"Finished processing {self.name}, but name mismatched! ({title})")
        else:
            print(f"Finished processing {self.name}")


@dataclass(eq=True, unsafe_hash=True)
class Zone:
    name: str
    id: str

    def __init__(self, name: str, id: str, skip_uimap_check: bool = False):
        self.name = name
        self.id = id
        if not skip_uimap_check and id in UIMAP and UIMAP[id] != name:
            print(f"UIMap ID <> Name mismatch for {id} ({name} <> {UIMAP[id]})")


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

UIMAP = {}
with open('uimap.csv', newline='') as uimapcsv:
    reader = csv.reader(uimapcsv)
    for row in reader:
        UIMAP[row[1]] = row[0]

# Dungeons and other odd maps
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
    '14030', # Vault of the Incarnates (Raid)
    '14643', # Amirdrassil, the Dream's Hope (Raid)

    # The War Within
    '14999', # Earthcrawl Mines(Delve)
    '15002', # The Waterworks (Delve)
    '15000', # Kriegval's Rest (Delve)
    '14998', # Mycomancer Cavern (Delve)
    '15175', # The Sinkhole (Delve)
    #'14753', # City of Threads
    #'14771', # Dornogal (City)
    '14957', # Fungal Folly (Delve)
    '15003', # The Dread Pit (Delve)
    '15008', # Tak-Rethan Abyss (Delve)
    '15005', # Nightfall Sanctum (Delve)
    '15004', # Skittering Breach (Delve)
    '15009', # The Underkeep (Delve)
]

WOWHEAD_ZONE_MAP = {
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
    # '4706': Zone("Ruins of Gilneas", "217"),
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

    # Midnight (Beta) - Zone IDs corrected based on Wowhead data
    '15947': Zone("Zul'Aman", "2554", skip_uimap_check=True),
    '15355': Zone("Harandar", "2553", skip_uimap_check=True),
    '15968': Zone("Eversong Woods", "2552", skip_uimap_check=True),
    '16194': Zone("Atal'Aman", "2555", skip_uimap_check=True),
    '15458': Zone("Voidstorm", "2556", skip_uimap_check=True),
}

HERBS = [
    # Vanilla
    WowheadObject(name="Peacebloom", ids=['1618'], gathermate_id='401'),
    WowheadObject(name="Silverleaf", ids=['1617'], gathermate_id='402'),
    WowheadObject(name="Earthroot", ids=['1619'], gathermate_id='403'),
    WowheadObject(name="Mageroyal", ids=['1620'], gathermate_id='404'),
    WowheadObject(name="Briarthorn", ids=['1621'], gathermate_id='405'),
    # WowheadObject(name="Swiftthistle", ids=[], gathermate_id='406'),
    WowheadObject(name="Stranglekelp", ids=['2045'], gathermate_id='407'),
    WowheadObject(name="Bruiseweed", ids=['1622'], gathermate_id='408'),
    WowheadObject(name="Wild Steelbloom", ids=['1623'], gathermate_id='409'),
    WowheadObject(name="Grave Moss", ids=['1628'], gathermate_id='410'),
    WowheadObject(name="Kingsblood", ids=['1624'], gathermate_id='411'),
    WowheadObject(name="Liferoot", ids=['2041'], gathermate_id='412'),
    WowheadObject(name="Fadeleaf", ids=['2042'], gathermate_id='413'),
    WowheadObject(name="Goldthorn", ids=['2046'], gathermate_id='414'),
    WowheadObject(name="Khadgar's Whisker", ids=['2043'], gathermate_id='415'),
    # WowheadObject(name="Wintersbite", ids=['2044'], gathermate_id='416'),
    WowheadObject(name="Firebloom", ids=['2866'], gathermate_id='417'),
    WowheadObject(name="Purple Lotus", ids=['142140'], gathermate_id='418'),
    # WowheadObject(name="Wildvine", ids=[], gathermate_id='419'),
    WowheadObject(name="Arthas' Tears", ids=['142141'], gathermate_id='420'),
    WowheadObject(name="Sungrass", ids=['142142'], gathermate_id='421'),
    WowheadObject(name="Blindweed", ids=['142143'], gathermate_id='422'),
    WowheadObject(name="Ghost Mushroom", ids=['142144'], gathermate_id='423'),
    WowheadObject(name="Gromsblood", ids=['142145'], gathermate_id='424'),
    WowheadObject(name="Golden Sansam", ids=['176583'], gathermate_id='425'),
    WowheadObject(name="Dreamfoil", ids=['176584'], gathermate_id='426'),
    WowheadObject(name="Mountain Silversage", ids=['176586'], gathermate_id='427'),
    # WowheadObject(name="Plaguebloom", ids=['176587', '176641'], gathermate_id='428'),
    WowheadObject(name="Icecap", ids=['176588'], gathermate_id='429'),
    # WowheadObject(name="Bloodvine", ids=[], gathermate_id='430'),
    WowheadObject(name="Black Lotus", ids=['176589'], gathermate_id='431'),

    # Burning Crusade
    WowheadObject(name="Felweed", ids=['181270'], gathermate_id='432'),
    WowheadObject(name="Dreaming Glory", ids=['181271'], gathermate_id='433'),
    WowheadObject(name="Terocone", ids=['181277'], gathermate_id='434'),
    # WowheadObject(name="Ancient Lichen", ids=['181278'], gathermate_id='435'),
    WowheadObject(name="Bloodthistle", ids=['181166'], gathermate_id='436'),
    WowheadObject(name="Mana Thistle", ids=['181281'], gathermate_id='437'),
    WowheadObject(name="Netherbloom", ids=['181279'], gathermate_id='438'),
    WowheadObject(name="Nightmare Vine", ids=['181280'], gathermate_id='439'),
    WowheadObject(name="Ragveil", ids=['181275'], gathermate_id='440'),
    WowheadObject(name="Flame Cap", ids=['181276'], gathermate_id='441'),
    WowheadObject(name="Netherdust Bush", ids=['185881'], gathermate_id='442'),

    # Wrath of the Lich King
    WowheadObject(name="Adder's Tongue", ids=['191019'], gathermate_id='443'),
    # WowheadObject(name="Constrictor Grass", ids=[], gathermate_id='444'),
    # WowheadObject(name="Deadnettle", ids=[], gathermate_id='445'),
    WowheadObject(name="Goldclover", ids=['189973'], gathermate_id='446'),
    WowheadObject(name="Icethorn", ids=['190172'], gathermate_id='447'),
    WowheadObject(name="Lichbloom", ids=['190171'], gathermate_id='448'),
    WowheadObject(name="Talandra's Rose", ids=['190170'], gathermate_id='449'),
    WowheadObject(name="Tiger Lily", ids=['190169'], gathermate_id='450'),
    WowheadObject(name="Firethorn", ids=['191303'], gathermate_id='451'),
    WowheadObject(name="Frozen Herb", ids=['190173', '190175'], gathermate_id='452'),
    WowheadObject(name="Frost Lotus", ids=['190176'], gathermate_id='453'),

    # Cataclysm
    WowheadObject(name="Dragon's Teeth", ids=['2044'], gathermate_id='454'),
    WowheadObject(name="Sorrowmoss", ids=['176587'], gathermate_id='455'),
    WowheadObject(name="Azshara's Veil", ids=['202749'], gathermate_id='456'),
    WowheadObject(name="Cinderbloom", ids=['202747'], gathermate_id='457'),
    WowheadObject(name="Stormvine", ids=['202748'], gathermate_id='458'),
    WowheadObject(name="Heartblossom", ids=['202750'], gathermate_id='459'),
    WowheadObject(name="Twilight Jasmine", ids=['202751'], gathermate_id='460'),
    WowheadObject(name="Whiptail", ids=['202752'], gathermate_id='461'),

    # Mists of Pandaria
    WowheadObject(name="Golden Lotus", ids=['209354', '221545'], gathermate_id='462'),
    WowheadObject(name="Fool's Cap", ids=['209355', '221547'], gathermate_id='463'),
    WowheadObject(name="Snow Lily", ids=['209351'], gathermate_id='464'),
    WowheadObject(name="Silkweed", ids=['209350', '221544'], gathermate_id='465'),
    WowheadObject(name="Green Tea Leaf", ids=['209349', '221542'], gathermate_id='466'),
    WowheadObject(name="Rain Poppy", ids=['209353', '221543'], gathermate_id='467'),
    WowheadObject(name="Sha-Touched Herb", ids=['214510'], gathermate_id='468'),

    # Draenor
    WowheadObject(name="Talador Orchid", ids=['228576', '237400'], gathermate_id='469'),
    WowheadObject(name="Nagrand Arrowbloom", ids=['228575', '237406'], gathermate_id='470'),
    WowheadObject(name="Starflower", ids=['228574', '237404'], gathermate_id='471'),
    WowheadObject(name="Gorgrond Flytrap", ids=['228573', '237402'], gathermate_id='472'),
    WowheadObject(name="Fireweed", ids=['228572', '237396'], gathermate_id='473'),
    WowheadObject(name="Frostweed", ids=['233117', '228571', '237398'], gathermate_id='474'),
    WowheadObject(name="Withered Herb", ids=['243334'], gathermate_id='475'),

    # Legion
    WowheadObject(name="Aethril", ids=['244774'], gathermate_id='476'),
    WowheadObject(name="Dreamleaf", ids=['244775'], gathermate_id='477'),
    # WowheadObject(name="Felwort", ids=[], gathermate_id='478'),
    WowheadObject(name="Fjarnskaggl", ids=['244777'], gathermate_id='479'),
    WowheadObject(name="Foxflower", ids=['241641'], gathermate_id='480'),
    WowheadObject(name="Starlight Rose", ids=['244778'], gathermate_id='481'),
    WowheadObject(name="Fel-Encrusted Herb", ids=['269278', '273052'], gathermate_id='482'),
    WowheadObject(name="Fel-Encrusted Herb Cluster", ids=['269887', '273053'], gathermate_id='483'),
    WowheadObject(name="Astral Glory", ids=['272782'], gathermate_id='484'),

    # Battle for Azeroth
    WowheadObject(name="Akunda's Bite", ids=['276237'], gathermate_id='485'),
    WowheadObject(name="Anchor Weed", ids=['276242', '294125'], gathermate_id='486'),
    WowheadObject(name="Riverbud", ids=['276234', '281870'], gathermate_id='487'),
    WowheadObject(name="Sea Stalks", ids=['276240', '281872'], gathermate_id='488'),
    WowheadObject(name="Siren's Sting", ids=['276239', '281869'], gathermate_id='489'),
    WowheadObject(name="Star Moss", ids=['276236', '281868'], gathermate_id='490'),
    WowheadObject(name="Winter's Kiss", ids=['276238'], gathermate_id='491'),
    WowheadObject(name="Zin'anthid", ids=['326598'], gathermate_id='492'),

    # Shadowlands
    WowheadObject(name="Death Blossom", ids=['336686', '351469', '351470', '351471'], gathermate_id='493'),
    WowheadObject(name="Nightshade", ids=['336691', '356537'], gathermate_id='494'),
    WowheadObject(name="Marrowroot", ids=['336689'], gathermate_id='495'),
    WowheadObject(name="Vigil's Torch", ids=['336688'], gathermate_id='496'),
    WowheadObject(name="Rising Glory", ids=['336690'], gathermate_id='497'),
    WowheadObject(name="Widowbloom", ids=['336433'], gathermate_id='498'),
    WowheadObject(name="First Flower", ids=['370398'], gathermate_id='499'),
    WowheadObject(name="Lush Nightshade", ids=['375071'], gathermate_id='1401'),
    WowheadObject(name="Elusive Nightshade", ids=['375338'], gathermate_id='1402'),
    WowheadObject(name="Lush First Flower", ids=['370397'], gathermate_id='1403'),
    WowheadObject(name="Elusive First Flower", ids=['375337'], gathermate_id='1404'),
    WowheadObject(name="Lush Elusive First Flower", ids=['375340'], gathermate_id='1405'),
    WowheadObject(name="Lush Elusive Nightshade", ids=['375341'], gathermate_id='1406'),

    #Dragonflight
    WowheadObject(name="Hochenblume", ids=['381209', '407703', '398757'], gathermate_id='1407'),
    WowheadObject(name="Lush Hochenblume", ids=['381960', '407705', '398753'], gathermate_id='1408'),
    WowheadObject(name="Frigid Hochenblume", ids=['381214'], gathermate_id='1409'),
    WowheadObject(name="Decayed Hochenblume", ids=['381212'], gathermate_id='1410'),
    WowheadObject(name="Windswept Hochenblume", ids=['381213'], gathermate_id='1411'),
    WowheadObject(name="Infurious Hochenblume", ids=['381211', '409408', '398766'], gathermate_id='1412'),
    WowheadObject(name="Titan-Touched Hochenblume", ids=['381210', '398761'], gathermate_id='1413'),
    WowheadObject(name="Lambent Hochenblume", ids=['390139'], gathermate_id='1435'),

    WowheadObject(name="Bubble Poppy", ids=['375241', '407685', '398755'], gathermate_id='1414'),
    WowheadObject(name="Lush Bubble Poppy", ids=['381957', '407686', '398751'], gathermate_id='1415'),
    WowheadObject(name="Frigid Bubble Poppy", ids=['375244'], gathermate_id='1416'),
    WowheadObject(name="Decayed Bubble Poppy", ids=['375246'], gathermate_id='1417'),
    WowheadObject(name="Windswept Bubble Poppy", ids=['375245'], gathermate_id='1418'),
    WowheadObject(name="Infurious Bubble Poppy", ids=['375243', '398764'], gathermate_id='1419'),
    WowheadObject(name="Titan-Touched Bubble Poppy", ids=['375242', '398759'], gathermate_id='1420'),
    WowheadObject(name="Lambent Bubble Poppy", ids=['390142'], gathermate_id='1436'),

    WowheadObject(name="Saxifrage", ids=['381207', '407701', '398758'], gathermate_id='1421'),
    WowheadObject(name="Lush Saxifrage", ids=['407706', '398754', '381959'], gathermate_id='1422'),
    WowheadObject(name="Frigid Saxifrage", ids=['381201'], gathermate_id='1423'),
    WowheadObject(name="Decayed Saxifrage", ids=['381203'], gathermate_id='1424'),
    WowheadObject(name="Windswept Saxifrage", ids=['381202'], gathermate_id='1425'),
    WowheadObject(name="Infurious Saxifrage", ids=['381204', '398767', '409407'], gathermate_id='1426'),
    WowheadObject(name="Titan-Touched Saxifrage", ids=['381205', '398762'], gathermate_id='1427'),
    WowheadObject(name="Lambent Saxifrage", ids=['390140'], gathermate_id='1437'),

    WowheadObject(name="Writhebark", ids=['381154', '407702', '398756'], gathermate_id='1428'),
    WowheadObject(name="Lush Writhebark", ids=['381958', '407707', '398752'], gathermate_id='1429'),
    WowheadObject(name="Frigid Writhebark", ids=['381200'], gathermate_id='1430'),
    WowheadObject(name="Decayed Writhebark", ids=['381198'], gathermate_id='1431'),
    WowheadObject(name="Windswept Writhebark", ids=['381199'], gathermate_id='1432'),
    WowheadObject(name="Infurious Writhebark", ids=['381197', '409405', '398765'], gathermate_id='1433'),
    WowheadObject(name="Titan-Touched Writhebark", ids=['381196', '398760'], gathermate_id='1434'),
    WowheadObject(name="Lambent Writhebark", ids=['390141'], gathermate_id='1438'),

    #WowheadObject(name="Frozen Herb", ids=['382075'], gathermate_id=''),

    # The War Within
    WowheadObject(name="Mycobloom", ids=['454063', '414315', '454071', '454076'], gathermate_id='1439'),
    WowheadObject(name="Lush Mycobloom", ids=['454062', '454075', '414320', '454070'], gathermate_id='1440'),
    WowheadObject(name="Irradiated Mycobloom", ids=['414335', '454069', '454066', '454074'], gathermate_id='1441'),
    WowheadObject(name="Sporefused Mycobloom", ids=['454072', '423367', '454064', '454067'], gathermate_id='1442'),
    WowheadObject(name="Sporelusive Mycobloom", ids=['429647', '429642'], gathermate_id='1443'),
    WowheadObject(name="Crystallized Mycobloom", ids=['414325'], gathermate_id='1444'),
    WowheadObject(name="Altered Mycobloom", ids=['414330'], gathermate_id='1445'),
    WowheadObject(name="Camouflaged Mycobloom", ids=['414340', '454073', '454065', '454068'], gathermate_id='1446'),

    WowheadObject(name="Blessing Blossom", ids=['454086', '414318', '454081'], gathermate_id='1447'),
    WowheadObject(name="Lush Blessing Blossom", ids=['414323', '454080', '454085'], gathermate_id='1448'),
    WowheadObject(name="Irradiated Blessing Blossom", ids=['414338', '454079', '454084'], gathermate_id='1449'),
    WowheadObject(name="Sporefused Blessing Blossom", ids=['454077', '454082'], gathermate_id='1450'),
    WowheadObject(name="Sporelusive Blessing Blossom", ids=['429645', '429640'], gathermate_id='1451'),
    WowheadObject(name="Crystallized Blessing Blossom", ids=['414328'], gathermate_id='1452'),
    #WowheadObject(name="Altered Blessing Blossom", ids=[''], gathermate_id='1453'), # Doesn't exist or hasn't been seen/uploaded to Wowhead yet.
    WowheadObject(name="Camouflaged Blessing Blossom", ids=['414343', '454083', '454078'], gathermate_id='1454'),

    WowheadObject(name="Luredrop", ids=['454010', '454055', '414316'], gathermate_id='1455'),
    WowheadObject(name="Lush Luredrop", ids=['414321', '454009', '454054'], gathermate_id='1456'),
    WowheadObject(name="Irradiated Luredrop", ids=['414336', '454053'], gathermate_id='1457'),
    WowheadObject(name="Sporefused Luredrop", ids=['454050', '423366', '454006'], gathermate_id='1458'),
    WowheadObject(name="Sporelusive Luredrop", ids=['429641', '429646'], gathermate_id='1459'),
    WowheadObject(name="Crystallized Luredrop", ids=['414326'], gathermate_id='1460'),
    WowheadObject(name="Altered Luredrop", ids=['414331'], gathermate_id='1461'),
    WowheadObject(name="Camouflaged Luredrop", ids=['414341', '454007', '454051'], gathermate_id='1462'),

    WowheadObject(name="Orbinid", ids=['414317'], gathermate_id='1463'),
    WowheadObject(name="Lush Orbinid", ids=['414322'], gathermate_id='1464'),
    WowheadObject(name="Irradiated Orbinid", ids=['414337'], gathermate_id='1465'),
    WowheadObject(name="Sporefused Orbinid", ids=['423368'], gathermate_id='1466'),
    WowheadObject(name="Sporelusive Orbinid", ids=['429643', '429648'], gathermate_id='1467'),
    WowheadObject(name="Crystallized Orbinid", ids=['414327'], gathermate_id='1468'),
    WowheadObject(name="Altered Orbinid", ids=['414332'], gathermate_id='1469'),
    WowheadObject(name="Camouflaged Orbinid", ids=['414342'], gathermate_id='1470'),

    WowheadObject(name="Arathor's Spear", ids=['414319'], gathermate_id='1471'),
    WowheadObject(name="Lush Arathor's Spear", ids=['414324'], gathermate_id='1472'),
    WowheadObject(name="Irradiated Arathor's Spear", ids=['414339'], gathermate_id='1473'),
    WowheadObject(name="Sporefused Arathor's Spear", ids=['423363'], gathermate_id='1474'),
    WowheadObject(name="Sporelusive Arathor's Spear", ids=['429639', '429644'], gathermate_id='1475'),
    WowheadObject(name="Crystallized Arathor's Spear", ids=['414329'], gathermate_id='1476'),
    #WowheadObject(name="Altered Arathor's Spear", ids=[''], gathermate_id='1477'), # Doesn't exist or hasn't been seen/uploaded to Wowhead yet.
    WowheadObject(name="Camouflaged Arathor's Spear", ids=['414344'], gathermate_id='1478'),

    # Midnight (Beta)
    WowheadObject(name="Argentleaf", ids=['516936'], gathermate_id='1481', use_beta=True),
    WowheadObject(name="Wild Argentleaf", ids=['516971'], gathermate_id='1482', use_beta=True),
    WowheadObject(name="Lush Argentleaf", ids=['516985'], gathermate_id='1483', use_beta=True),
    WowheadObject(name="Voidbound Argentleaf", ids=['516982'], gathermate_id='1484', use_beta=True),
    WowheadObject(name="Lightfused Argentleaf", ids=['516964'], gathermate_id='1485', use_beta=True),
    WowheadObject(name="Primal Argentleaf", ids=['516976'], gathermate_id='1486', use_beta=True),

    WowheadObject(name="Mana Lily", ids=['516937'], gathermate_id='1487', use_beta=True),
    WowheadObject(name="Wild Mana Lily", ids=['516972'], gathermate_id='1488', use_beta=True),
    WowheadObject(name="Lush Mana Lily", ids=['516984'], gathermate_id='1489', use_beta=True),
    WowheadObject(name="Voidbound Mana Lily", ids=['516983'], gathermate_id='1490', use_beta=True),
    WowheadObject(name="Lightfused Mana Lily", ids=['516963'], gathermate_id='1491', use_beta=True),
    #WowheadObject(name="Primal Mana Lily", ids=[''], gathermate_id='1492', use_beta=True),  # Not found on Wowhead yet

    WowheadObject(name="Tranquility Bloom", ids=['516932'], gathermate_id='1493', use_beta=True),
    WowheadObject(name="Wild Tranquility Bloom", ids=['516968'], gathermate_id='1494', use_beta=True),
    WowheadObject(name="Lush Tranquility Bloom", ids=['516988'], gathermate_id='1495', use_beta=True),
    WowheadObject(name="Voidbound Tranquility Bloom", ids=['516979'], gathermate_id='1496', use_beta=True),
    WowheadObject(name="Lightfused Tranquility Bloom", ids=['516967'], gathermate_id='1497', use_beta=True),
    WowheadObject(name="Primal Tranquility Bloom", ids=['516973'], gathermate_id='1498', use_beta=True),

    WowheadObject(name="Sanguithorn", ids=['516934'], gathermate_id='1499', use_beta=True),
    WowheadObject(name="Wild Sanguithorn", ids=['516969'], gathermate_id='1500', use_beta=True),
    WowheadObject(name="Lush Sanguithorn", ids=['516987'], gathermate_id='1501', use_beta=True),
    WowheadObject(name="Voidbound Sanguithorn", ids=['516980'], gathermate_id='1502', use_beta=True),
    WowheadObject(name="Lightfused Sanguithorn", ids=['516966'], gathermate_id='1503', use_beta=True),
    WowheadObject(name="Primal Sanguithorn", ids=['516974'], gathermate_id='1504', use_beta=True),

    WowheadObject(name="Azeroot", ids=['516935'], gathermate_id='1505', use_beta=True),
    WowheadObject(name="Wild Azeroot", ids=['516970'], gathermate_id='1506', use_beta=True),
    WowheadObject(name="Lush Azeroot", ids=['516986'], gathermate_id='1507', use_beta=True),
    WowheadObject(name="Voidbound Azeroot", ids=['516981'], gathermate_id='1508', use_beta=True),
    WowheadObject(name="Lightfused Azeroot", ids=['516965'], gathermate_id='1509', use_beta=True),
    WowheadObject(name="Primal Azeroot", ids=['516975'], gathermate_id='1510', use_beta=True),
]

ORES = [
    # Vanilla
    WowheadObject(name="Copper Vein", ids=['1731', '181248'], gathermate_id='201'),
    WowheadObject(name="Tin Vein", ids=['1732', '181249'], gathermate_id='202'),
    WowheadObject(name="Iron Deposit", ids=['1735'], gathermate_id='203'),
    WowheadObject(name="Silver Vein", ids=['1733'], gathermate_id='204'),
    WowheadObject(name="Gold Vein", ids=['1734'], gathermate_id='205'),
    WowheadObject(name="Mithril Deposit", ids=['2040'], gathermate_id='206'),
    WowheadObject(name="Ooze Covered Mithril Deposit", ids=['123310'], gathermate_id='207'),
    WowheadObject(name="Truesilver Deposit", ids=['2047', '181108'], gathermate_id='208'),
    WowheadObject(name="Ooze Covered Silver Vein", ids=['73940'], gathermate_id='209'),
    WowheadObject(name="Ooze Covered Gold Vein", ids=['73941'], gathermate_id='210'),
    WowheadObject(name="Ooze Covered Truesilver Deposit", ids=['123309'], gathermate_id='211'),
    WowheadObject(name="Ooze Covered Rich Thorium Vein", ids=['177388'], gathermate_id='212'),
    WowheadObject(name="Ooze Covered Thorium Vein", ids=['123848'], gathermate_id='213'),
    WowheadObject(name="Small Thorium Vein", ids=['324'], gathermate_id='214'),
    WowheadObject(name="Rich Thorium Vein", ids=['175404'], gathermate_id='215'),
    WowheadObject(name="Dark Iron Deposit", ids=['165658'], gathermate_id='217'),
    WowheadObject(name="Lesser Bloodstone Deposit", ids=['2653'], gathermate_id='218'),
    WowheadObject(name="Incendicite Mineral Vein", ids=['1610'], gathermate_id='219'),
    WowheadObject(name="Indurium Mineral Vein", ids=['19903'], gathermate_id='220'),

    # Burning Crusade
    WowheadObject(name="Fel Iron Deposit", ids=['181555'], gathermate_id='221'),
    WowheadObject(name="Adamantite Deposit", ids=['181556'], gathermate_id='222'),
    WowheadObject(name="Rich Adamantite Deposit", ids=['181569'], gathermate_id='223'),
    WowheadObject(name="Khorium Vein", ids=['181557'], gathermate_id='224'),
    # WowheadObject(name="Large Obsidian Chunk", ids=[], gathermate_id='225'),
    # WowheadObject(name="Small Obsidian Chunk", ids=[], gathermate_id='226'),
    WowheadObject(name="Nethercite Deposit", ids=['185877'], gathermate_id='227'),

    # Wrath of the Lich King
    WowheadObject(name="Cobalt Deposit", ids=['189978'], gathermate_id='228'),
    WowheadObject(name="Rich Cobalt Deposit", ids=['189979'], gathermate_id='229'),
    WowheadObject(name="Titanium Vein", ids=['191133'], gathermate_id='230'),
    WowheadObject(name="Saronite Deposit", ids=['189980'], gathermate_id='231'),
    WowheadObject(name="Rich Saronite Deposit", ids=['189981'], gathermate_id='232'),

    # Cataclysm
    WowheadObject(name="Obsidium Deposit", ids=['202736'], gathermate_id='233'),
    # WowheadObject(name="Huge Obsidian Slab", ids=[], gathermate_id='234'),
    # WowheadObject(name="Pure Saronite Deposit", ids=[], gathermate_id='235'),
    WowheadObject(name="Elementium Vein", ids=['202738'], gathermate_id='236'),
    WowheadObject(name="Rich Elementium Vein", ids=['202741'], gathermate_id='237'),
    WowheadObject(name="Pyrite Deposit", ids=['202737'], gathermate_id='238'),
    WowheadObject(name="Rich Obsidium Deposit", ids=['202739'], gathermate_id='239'),
    WowheadObject(name="Rich Pyrite Deposit", ids=['202740'], gathermate_id='240'),

    # Mists of Pandaria
    WowheadObject(name="Ghost Iron Deposit", ids=['209311', '221538'], gathermate_id='241'),
    WowheadObject(name="Rich Ghost Iron Deposit", ids=['209328', '221539'], gathermate_id='242'),
    # WowheadObject(name="Black Trillium Deposit", ids=[], gathermate_id='243'),
    # WowheadObject(name="White Trillium Deposit", ids=[], gathermate_id='244'),
    WowheadObject(name="Kyparite Deposit", ids=['209312'], gathermate_id='245'),
    WowheadObject(name="Rich Kyparite Deposit", ids=['209329'], gathermate_id='246'),
    WowheadObject(name="Trillium Vein", ids=['209313', '221541'], gathermate_id='247'),
    WowheadObject(name="Rich Trillium Vein", ids=['209330', '221540'], gathermate_id='248'),

    # Draenor
    WowheadObject(name="True Iron Deposit", ids=['228493', '243314', '237358'], gathermate_id='249'),
    WowheadObject(name="Rich True Iron Deposit", ids=['228510', '243315', '237357'], gathermate_id='250'),
    WowheadObject(name="Blackrock Deposit", ids=['228563', '243313', '237359'], gathermate_id='251'),
    WowheadObject(name="Rich Blackrock Deposit", ids=['228564', '237360', '243312'], gathermate_id='252'),

    # Legion
    WowheadObject(name="Leystone Deposit", ids=['241726'], gathermate_id='253'),
    WowheadObject(name="Rich Leystone Deposit", ids=['245324'], gathermate_id='254'),
    WowheadObject(name="Leystone Seam", ids=['253280'], gathermate_id='255'),
    WowheadObject(name="Felslate Deposit", ids=['241743'], gathermate_id='256'),
    WowheadObject(name="Rich Felslate Deposit", ids=['245325'], gathermate_id='257'),
    WowheadObject(name="Felslate Seam", ids=['255344'], gathermate_id='258'),
    WowheadObject(name="Empyrium Deposit", ids=['272768'], gathermate_id='259'),
    WowheadObject(name="Rich Empyrium Deposit", ids=['272778'], gathermate_id='260'),
    WowheadObject(name="Empyrium Seam", ids=['272780'], gathermate_id='261'),

    # Battle for Azeroth
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

    # Shadowlands
    WowheadObject(name="Laestrite Deposit", ids=['349898'], gathermate_id='273'),
    WowheadObject(name="Rich Laestrite Deposit", ids=['349899'], gathermate_id='274'),
    WowheadObject(name="Phaedrum Deposit", ids=['355508', '349982'], gathermate_id='275'),
    WowheadObject(name="Rich Phaedrum Deposit", ids=['350087', '355507'], gathermate_id='276'),
    WowheadObject(name="Oxxein Deposit", ids=['349981'], gathermate_id='277'),
    WowheadObject(name="Rich Oxxein Deposit", ids=['350085'], gathermate_id='278'),
    # WowheadObject(name="Monolithic Oxxein Deposit", ids=['356401'], gathermate_id='279'),
    WowheadObject(name="Elethium Deposit", ids=['349900'], gathermate_id='280'),
    WowheadObject(name="Rich Elethium Deposit", ids=['350082'], gathermate_id='281'),
    WowheadObject(name="Solenium Deposit", ids=['349980'], gathermate_id='282'),
    WowheadObject(name="Rich Solenium Deposit", ids=['350086'], gathermate_id='283'),
    WowheadObject(name="Sinvyr Deposit", ids=['349983'], gathermate_id='284'),
    WowheadObject(name="Rich Sinvyr Deposit", ids=['350084'], gathermate_id='285'),
    # WowheadObject(name="Menacing Sinvyr Deposit", ids=['356402'], gathermate_id='286'),
    WowheadObject(name="Progenium Deposit", ids=['370400'], gathermate_id='287'),
    WowheadObject(name="Rich Progenium Deposit", ids=['370399'], gathermate_id='288'),
    WowheadObject(name="Elusive Progenium Deposit", ids=['375331'], gathermate_id='289'),
    WowheadObject(name="Elusive Rich Progenium Deposit", ids=['375332'], gathermate_id='290'),
    # WowheadObject(name="Elusive Elethium Deposit", ids=[''], gathermate_id='291'),
    WowheadObject(name="Elusive Rich Elethium Deposit", ids=['375333'], gathermate_id='292'),

    # Dragonflight
    WowheadObject(name="Serevite Seam", ids=['381106'], gathermate_id='1200'),
    WowheadObject(name="Serevite Deposit", ids=['381102', '407677', '381103'], gathermate_id='1201'),
    WowheadObject(name="Rich Serevite Deposit", ids=['381104', '407678', '381105'], gathermate_id='1202'),
    WowheadObject(name="Primal Serevite Deposit", ids=['381518'], gathermate_id='1203'),
    WowheadObject(name="Molten Serevite Deposit", ids=['381516'], gathermate_id='1204'),
    WowheadObject(name="Hardened Serevite Deposit", ids=['381515'], gathermate_id='1205'),
    WowheadObject(name="Infurious Serevite Deposit", ids=['381519'], gathermate_id='1206'),
    WowheadObject(name="Titan-Touched Serevite Deposit", ids=['381517'], gathermate_id='1207'),
    WowheadObject(name="Metamorphic Serevite Deposit", ids=['390137'], gathermate_id='1216'),

    WowheadObject(name="Draconium Seam", ids=['379272'], gathermate_id='1208'),
    WowheadObject(name="Draconium Deposit", ids=['379252', '407679', '379248'], gathermate_id='1209'),
    WowheadObject(name="Rich Draconium Deposit", ids=['407681', '379267', '379263'], gathermate_id='1210'),
    WowheadObject(name="Primal Draconium Deposit", ids=['375239'], gathermate_id='1211'),
    WowheadObject(name="Molten Draconium Deposit", ids=['375235'], gathermate_id='1212'),
    WowheadObject(name="Hardened Draconium Deposit", ids=['375234'], gathermate_id='1213'),
    WowheadObject(name="Infurious Draconium Deposit", ids=['375240'], gathermate_id='1214'),
    WowheadObject(name="Titan-Touched Draconium Deposit", ids=['375238'], gathermate_id='1215'),
    WowheadObject(name="Metamorphic Draconium Deposit", ids=['390138'], gathermate_id='1217'),

    #WowheadObject(name="Metamorphic Spire", ids=['398747'], gathermate_id=''),

    # The War Within
    WowheadObject(name="Bismuth", ids=['413046'], gathermate_id='1218'),
    WowheadObject(name="Rich Bismuth", ids=['413874'], gathermate_id='1219'),
    WowheadObject(name="Camouflaged Bismuth", ids=['413889'], gathermate_id='1220'),
    WowheadObject(name="Crystallized Bismuth", ids=['413883'], gathermate_id='1221'),
    WowheadObject(name="Weeping Bismuth", ids=['413884'], gathermate_id='1222'),
    #WowheadObject(name="Webbed Bismuth", ids=[''], gathermate_id='1223'),
    WowheadObject(name="EZ-Mine Bismuth", ids=['413886'], gathermate_id='1224'),
    WowheadObject(name="Bismuth Seam", ids=['413880'], gathermate_id='1225'),

    WowheadObject(name="Aqirite", ids=['413047'], gathermate_id='1226'),
    WowheadObject(name="Rich Aqirite", ids=['413875'], gathermate_id='1227'),
    WowheadObject(name="Camouflaged Aqirite", ids=['413897'], gathermate_id='1228'),
    WowheadObject(name="Crystallized Aqirite", ids=['413890'], gathermate_id='1229'),
    WowheadObject(name="Weeping Aqirite", ids=['413892'], gathermate_id='1230'),
    #WowheadObject(name="Webbed Aqirite", ids=[''], gathermate_id='1231'),
    WowheadObject(name="EZ-Mine Aqirite", ids=['413895'], gathermate_id='1232'),
    WowheadObject(name="Aqirite Seam", ids=['413881'], gathermate_id='1233'),

    WowheadObject(name="Ironclaw", ids=['413049'], gathermate_id='1234'),
    WowheadObject(name="Rich Ironclaw", ids=['413877'], gathermate_id='1235'),
    WowheadObject(name="Camouflaged Ironclaw", ids=['413907'], gathermate_id='1236'),
    WowheadObject(name="Crystallized Ironclaw", ids=['413900'], gathermate_id='1237'),
    WowheadObject(name="Weeping Ironclaw", ids=['413902'], gathermate_id='1238'),
    #WowheadObject(name="Webbed Ironclaw", ids=[''], gathermate_id='1239'),
    WowheadObject(name="EZ-Mine Ironclaw", ids=['413905'], gathermate_id='1240'),
    WowheadObject(name="Ironclaw Seam", ids=['413882'], gathermate_id='1241'),

    WowheadObject(name="Webbed Ore Deposit", ids=['430335', '430351', '430352'], gathermate_id='1242'),

    # Midnight (Beta)
    WowheadObject(name="Refulgent Copper", ids=['523281'], gathermate_id='1245', use_beta=True),
    WowheadObject(name="Refulgent Copper Seam", ids=['523283'], gathermate_id='1246', use_beta=True),
    WowheadObject(name="Voidbound Refulgent Copper", ids=['523287'], gathermate_id='1247', use_beta=True),
    WowheadObject(name="Lightfused Refulgent Copper", ids=['523284'], gathermate_id='1248', use_beta=True),
    WowheadObject(name="Rich Refulgent Copper", ids=['523282'], gathermate_id='1249', use_beta=True),
    WowheadObject(name="Primal Refulgent Copper", ids=['523285'], gathermate_id='1250', use_beta=True),
    WowheadObject(name="Wild Refulgent Copper", ids=['523286'], gathermate_id='1263', use_beta=True),

    WowheadObject(name="Umbral Tin", ids=['523288'], gathermate_id='1251', use_beta=True),
    WowheadObject(name="Umbral Tin Seam", ids=['523290'], gathermate_id='1252', use_beta=True),
    WowheadObject(name="Voidbound Umbral Tin", ids=['523293'], gathermate_id='1253', use_beta=True),
    WowheadObject(name="Lightfused Umbral Tin", ids=['523294'], gathermate_id='1254', use_beta=True),
    WowheadObject(name="Rich Umbral Tin", ids=['523289'], gathermate_id='1255', use_beta=True),
    #WowheadObject(name="Primal Umbral Tin", ids=[''], gathermate_id='1256', use_beta=True),  # Not found on Wowhead yet
    WowheadObject(name="Wild Umbral Tin", ids=['523292'], gathermate_id='1264', use_beta=True),

    WowheadObject(name="Brilliant Silver", ids=['523295'], gathermate_id='1257', use_beta=True),
    WowheadObject(name="Brilliant Silver Seam", ids=['523298'], gathermate_id='1258', use_beta=True),
    WowheadObject(name="Voidbound Brilliant Silver", ids=['523301'], gathermate_id='1259', use_beta=True),
    WowheadObject(name="Lightfused Brilliant Silver", ids=['523303'], gathermate_id='1260', use_beta=True),
    WowheadObject(name="Rich Brilliant Silver", ids=['523297'], gathermate_id='1261', use_beta=True),
    WowheadObject(name="Primal Brilliant Silver", ids=['523299'], gathermate_id='1262', use_beta=True),
    WowheadObject(name="Wild Brilliant Silver", ids=['523300'], gathermate_id='1265', use_beta=True),
]

TREASURES = [
    #WowheadObject(name="Giant Clam", ids=['2744', '19017', '19018', '179244'], gathermate_id='501'),
    #WowheadObject(name="Battered Chest", ids=['2843', '106319', '106318', '2849'], gathermate_id='502'),
    #WowheadObject(name="Tattered Chest", ids=['2845', '105571', '2846', '2847', '2844'], gathermate_id='503'),
    #WowheadObject(name="Solid Chest", ids=['2850', '4149', '153453', '2857', '153451', '153454', '2855', '2852'], gathermate_id='504'),
    #WowheadObject(name="Large Iron Bound Chest", ids=['74447', '75297', '75296', '75295'], gathermate_id='505'),
    #WowheadObject(name="Large Solid Chest", ids=['74448', '75300', '153464', '153463', '153462', '75298', '75299', '153461'], gathermate_id='506'),
    #WowheadObject(name="Large Battered Chest", ids=['75293'], gathermate_id='507'),
    #WowheadObject(name="Buccaneer's Strongbox", ids=['123330', '123331', '123333', '123332'], gathermate_id='508'),
    #WowheadObject(name="Large Mithril Bound Chest", ids=['153468', '131978', '153469', '153465'], gathermate_id='509'),
    #WowheadObject(name="Large Darkwood Chest", ids=['131979'], gathermate_id='510'),
    #WowheadObject(name="Un'Goro Dirt Pile", ids=['157936'], gathermate_id='511'),
    #WowheadObject(name="Bloodpetal Sprout", ids=['164958'], gathermate_id='512'),
    #WowheadObject(name="Blood of Heroes", ids=['176213'], gathermate_id='513'),
    #WowheadObject(name="Practice Lockbox", ids=['178244', '178245', '178246'], gathermate_id='514'),
    #WowheadObject(name="Battered Footlocker", ids=['179488', '179490', '179486'], gathermate_id='515'),
    #WowheadObject(name="Waterlogged Footlocker", ids=['179487', '179491', '179489'], gathermate_id='516'),
    #WowheadObject(name="Dented Footlocker", ids=['179492', '179494', '179496'], gathermate_id='517'),
    #WowheadObject(name="Mossy Footlocker", ids=['179493', '179497', '179495'], gathermate_id='518'),
    #WowheadObject(name="Scarlet Footlocker", ids=['179498'], gathermate_id='519'),
]

FISHES = [
    # Vanilla
    WowheadObject(name="Floating Wreckage", ids=['180751'], gathermate_id='101'),
    # WowheadObject(name="Patch of Elemental Water", ids=[], gathermate_id='102'),
    WowheadObject(name="Floating Debris", ids=['180655'], gathermate_id='103'),
    # WowheadObject(name="Oil Spill", ids=['180661'], gathermate_id='104'),
    WowheadObject(name="Firefin Snapper School", ids=['180683'], gathermate_id='105'),
    WowheadObject(name="Greater Sagefish School", ids=['180684'], gathermate_id='106'),
    WowheadObject(name="Oily Blackmouth School", ids=['180682'], gathermate_id='107'),
    WowheadObject(name="Sagefish School", ids=['216764'], gathermate_id='108'),
    WowheadObject(name="School of Deviate Fish", ids=['180658'], gathermate_id='109'),
    WowheadObject(name="Stonescale Eel Swarm", ids=['180712'], gathermate_id='110'),
    # WowheadObject(name="Muddy Churning Water", ids=[], gathermate_id='111'),

    # Burning Crusade
    WowheadObject(name="Highland Mixed School", ids=['182957'], gathermate_id='112'),
    WowheadObject(name="Pure Water", ids=['182951'], gathermate_id='113'),
    WowheadObject(name="Bluefish School", ids=['182959'], gathermate_id='114'),
    WowheadObject(name="Brackish Mixed School", ids=['182954'], gathermate_id='115'),
    WowheadObject(name="Mudfish School", ids=['182958'], gathermate_id='116'),
    WowheadObject(name="School of Darter", ids=['182956'], gathermate_id='117'),
    WowheadObject(name="Sporefish School", ids=['182953'], gathermate_id='118'),
    WowheadObject(name="Steam Pump Flotsam", ids=['182952'], gathermate_id='119'),
    # WowheadObject(name="School of Tastyfish", ids=[], gathermate_id='120'),

    # Wrath of the Lich King
    WowheadObject(name="Borean Man O' War School", ids=['192051'], gathermate_id='121'),
    WowheadObject(name="Deep Sea Monsterbelly School", ids=['192053'], gathermate_id='122'),
    WowheadObject(name="Dragonfin Angelfish School", ids=['192048'], gathermate_id='123'),
    WowheadObject(name="Fangtooth Herring School", ids=['192049'], gathermate_id='124'),
    # WowheadObject(name="Floating Wreckage Pool", ids=[], gathermate_id='125'),
    WowheadObject(name="Glacial Salmon School", ids=['192050'], gathermate_id='126'),
    WowheadObject(name="Glassfin Minnow School", ids=['192059'], gathermate_id='127'),
    WowheadObject(name="Imperial Manta Ray School", ids=['192052'], gathermate_id='128'),
    WowheadObject(name="Moonglow Cuttlefish School", ids=['192054'], gathermate_id='129'),
    WowheadObject(name="Musselback Sculpin School", ids=['192046'], gathermate_id='130'),
    WowheadObject(name="Nettlefish School", ids=['192057'], gathermate_id='131'),
    # WowheadObject(name="Strange Pool", ids=[], gathermate_id='132'),

    # Cataclysm
    WowheadObject(name="Schooner Wreckage", ids=['180662'], gathermate_id='133'),
    # WowheadObject(name="Waterlogged Wreckage Pool", ids=[], gathermate_id='134'),
    # WowheadObject(name="Bloodsail Wreckage Pool", ids=[], gathermate_id='135'),
    WowheadObject(name="Mixed Ocean School", ids=['216761'], gathermate_id='136'),
    ## skipped unused entries
    WowheadObject(name="Albino Cavefish School", ids=['202778'], gathermate_id='149'),
    # WowheadObject(name="Algaefin Rockfish School", ids=[], gathermate_id='150'),
    WowheadObject(name="Blackbelly Mudfish School", ids=['202779'], gathermate_id='151'),
    WowheadObject(name="Fathom Eel Swarm", ids=['202780'], gathermate_id='152'),
    WowheadObject(name="Highland Guppy School", ids=['202777'], gathermate_id='153'),
    WowheadObject(name="Mountain Trout School", ids=['202776'], gathermate_id='154'),
    WowheadObject(name="Pool of Fire", ids=['207734'], gathermate_id='155'),
    # WowheadObject(name="Shipwreck Debris", ids=[], gathermate_id='156'),
    WowheadObject(name="Deepsea Sagefish School", ids=['208311'], gathermate_id='157'),

    # Mists of Pandaria
    WowheadObject(name="Emperor Salmon School", ids=['212163'], gathermate_id='158'),
    WowheadObject(name="Giant Mantis Shrimp Swarm", ids=['212169'], gathermate_id='159'),
    # WowheadObject(name="Golden Carp School", ids=[], gathermate_id='160'),
    WowheadObject(name="Jade Lungfish School", ids=['212171'], gathermate_id='161'),
    WowheadObject(name="Krasarang Paddlefish School", ids=['212172'], gathermate_id='162'),
    WowheadObject(name="Redbelly Mandarin School", ids=['221549'], gathermate_id='163'),
    WowheadObject(name="Reef Octopus Swarm", ids=['212174'], gathermate_id='164'),
    # WowheadObject(name="Floating Shipwreck Debris", ids=[], gathermate_id='165'),
    WowheadObject(name="Jewel Danio School", ids=['221548'], gathermate_id='166'),
    WowheadObject(name="Spinefish School", ids=['212177'], gathermate_id='167'),
    WowheadObject(name="Tiger Gourami School", ids=['212175'], gathermate_id='168'),

    # Draenor
    WowheadObject(name="Abyssal Gulper School", ids=['229072'], gathermate_id='169'),
    # WowheadObject(name="Oily Abyssal Gulper School", ids=[], gathermate_id='170'),
    WowheadObject(name="Blackwater Whiptail School", ids=['229073'], gathermate_id='171'),
    WowheadObject(name="Blind Lake Sturgeon School", ids=['229069'], gathermate_id='172'),
    WowheadObject(name="Fat Sleeper School", ids=['229068'], gathermate_id='173'),
    WowheadObject(name="Fire Ammonite School", ids=['229070'], gathermate_id='174'),
    WowheadObject(name="Jawless Skulker School", ids=['229067'], gathermate_id='175'),
    WowheadObject(name="Sea Scorpion School", ids=['229071'], gathermate_id='176'),
    # WowheadObject(name="Oily Sea Scorpion School", ids=[], gathermate_id='177'),
    WowheadObject(name="Savage Piranha Pool", ids=['237342'], gathermate_id='178'),
    # WowheadObject(name="Lagoon Pool", ids=[], gathermate_id='179'),
    # WowheadObject(name="Sparkling Pool", ids=[], gathermate_id='180'),
    WowheadObject(name="Felmouth Frenzy School", ids=['243325'], gathermate_id='181'),

    # WoWHead stops providing fishing pool locations after this point. Bug?

    # Legion
    WowheadObject(name="Black Barracuda School", ids=['246493'], gathermate_id='182'),
    WowheadObject(name="Cursed Queenfish School", ids=['246488'], gathermate_id='183'),
    WowheadObject(name="Runescale Koi School", ids=['246492'], gathermate_id='184'),
    WowheadObject(name="Fever of Stormrays", ids=['246491'], gathermate_id='185'),
    WowheadObject(name="Highmountain Salmon School", ids=['246490'], gathermate_id='186'),
    WowheadObject(name="Mossgill Perch School", ids=['246489'], gathermate_id='187'),

    # Battle for Azeroth
    WowheadObject(name="Frenzied Fangtooth School", ids=['278405'], gathermate_id='188'),
    WowheadObject(name="Great Sea Catfish School", ids=['278399'], gathermate_id='189'),
    WowheadObject(name="Lane Snapper School", ids=['278406'], gathermate_id='190'),
    WowheadObject(name="Rasboralus School", ids=['293749'], gathermate_id='191'),
    WowheadObject(name="Redtail Loach School", ids=['278404'], gathermate_id='192'),
    WowheadObject(name="Sand Shifter School", ids=['278401'], gathermate_id='193'),
    WowheadObject(name="Slimy Mackerel School", ids=['278403'], gathermate_id='194'),
    WowheadObject(name="Tiragarde Perch School", ids=['278402'], gathermate_id='195'),
    WowheadObject(name="U'taka School", ids=['293750'], gathermate_id='196'),
    WowheadObject(name="Mauve Stinger School", ids=['327162'], gathermate_id='197'),
    WowheadObject(name="Viper Fish School", ids=['327161'], gathermate_id='198'),
    WowheadObject(name="Ionized Minnows", ids=['323370'], gathermate_id='199'),
    WowheadObject(name="Sentry Fish School", ids=['326054'], gathermate_id='1101'),

    # Shadowlands
    WowheadObject(name="Iridescent Amberjack School", ids=['349083'], gathermate_id='1102'),
    WowheadObject(name="Pocked Bonefish School", ids=['349086'], gathermate_id='1103'),
    WowheadObject(name="Silvergill Pike School", ids=['349084'], gathermate_id='1104'),
    WowheadObject(name="Elysian Thade School", ids=['349088'], gathermate_id='1105'),
    WowheadObject(name="Lost Sole School", ids=['349082'], gathermate_id='1106'),
    WowheadObject(name="Spinefin Piranha School", ids=['349087'], gathermate_id='1107'),

    # Dragonflight
    WowheadObject(name="Islefin Dorado Pool", ids=['381101'], gathermate_id='1109'),
    WowheadObject(name="Shimmering Treasure Pool", ids=['382090'], gathermate_id='1110'),
    WowheadObject(name="Temporal Dragonhead Pool", ids=['381100'], gathermate_id='1111'),
    WowheadObject(name="Thousandbite Piranha Swarm", ids=['381097'], gathermate_id='1112'),
    WowheadObject(name="Aileron Seamoth School", ids=['381098'], gathermate_id='1113'),
    WowheadObject(name="Cerulean Spinefish School", ids=['381099'], gathermate_id='1114'),
    WowheadObject(name="Rimefin Tuna Pool", ids=['378271'], gathermate_id='1115'),
    WowheadObject(name="Magma Thresher Pool", ids=['377957'], gathermate_id='1116'),
    WowheadObject(name="Prismatic Leaper School", ids=['377938'], gathermate_id='1117'),

    # The War Within
    WowheadObject(name="Calm Surfacing Ripple", ids=['451670'], gathermate_id='1118'),
    WowheadObject(name="River Bass Pool", ids=['451674'], gathermate_id='1119'),
    WowheadObject(name="Glimmerpool", ids=['451669'], gathermate_id='1120'),
    WowheadObject(name="Bloody Perch Swarm", ids=['451671'], gathermate_id='1121'),
    WowheadObject(name="Swarm of Slum Sharks", ids=['451681'], gathermate_id='1122'),
    WowheadObject(name="Blood in the Water", ids=['451678'], gathermate_id='1123'),
    WowheadObject(name="Festering Rotpool", ids=['451677'], gathermate_id='1124'),
    WowheadObject(name="Infused Ichor Spill", ids=['451679'], gathermate_id='1125'),
    WowheadObject(name="Stargazer Swarm", ids=['451672'], gathermate_id='1126'),
    WowheadObject(name="Anglerseeker Torrent", ids=['451675'], gathermate_id='1127'),
    WowheadObject(name="Royal Ripple", ids=['451680'], gathermate_id='1128'),
    WowheadObject(name="Shore Treasure", ids=['451673'], gathermate_id='1129'),
    WowheadObject(name="Floating Deep Treasure", ids=['451676'], gathermate_id='1130'),
    #WowheadObject(name="Shadowblind Grouper School", ids=['414622'], gathermate_id=''),
    #WowheadObject(name="Whispers of the Deep", ids=['451682'], gathermate_id=''),

    # Midnight (Beta)
    WowheadObject(name="Hunter Surge", ids=['570491'], gathermate_id='1131', use_beta=True),
    WowheadObject(name="Surface Ripple", ids=['570488'], gathermate_id='1132', use_beta=True),
    WowheadObject(name="Bubbling Bloom", ids=['540485'], gathermate_id='1133', use_beta=True),
    WowheadObject(name="Lost Treasures", ids=['570492'], gathermate_id='1134', use_beta=True),
    WowheadObject(name="Sunwell Swarm", ids=['547481'], gathermate_id='1135', use_beta=True),
    WowheadObject(name="Song Swarm", ids=['570487'], gathermate_id='1136', use_beta=True),
]

if __name__ == '__main__':
    with open("../DATA/Mined_HerbalismData.lua", "w") as file:
        print(Aggregate("Herb", HERBS), file=file)
    with open("../DATA/Mined_MiningData.lua", "w") as file:
        print(Aggregate("Mine", ORES), file=file)
#    with open("../DATA/Mined_TreasureData.lua", "w") as file:
#        print(Aggregate("Treasure", TREASURES), file=file)
    with open("../DATA/Mined_FishData.lua", "w") as file:
        print(Aggregate("Fish", FISHES), file=file)
