"""Merchant business templates (SYNTHETIC).

A template describes a kind of small Indian retail business: its product categories, product families (expanded into
SKUs), suppliers, customer segments and buying patterns. The generator turns a template plus a merchant profile into a
coherent operating history. All brands, products, suppliers and people are fictional.

Velocities are units per day for the whole family at a single-store business on an average day; the generator spreads
them across variants (a long tail), weekdays, channels and city zones.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CategorySpec:
    code: str
    name: str
    target_margin: float  # gross margin the merchant aims for in this category
    description: str
    online_share: float = 1.0  # relative likelihood of being bought online / on WhatsApp (phones are mostly bought in store)


@dataclass(frozen=True)
class FamilySpec:
    code: str
    category: str
    name: str  # "{v}" is replaced by the variant label
    brand: str
    price: float  # list (selling) price of the base variant, INR
    margin: float  # gross margin at list price
    daily_units: float  # family velocity, units/day
    variants: tuple[tuple[str, float], ...]  # (label, price multiplier)
    supplier: str
    lifecycle: str = "steady"  # steady | fading (demand drifting down, used for slow-moving stock)


@dataclass(frozen=True)
class SupplierSpec:
    code: str
    name: str
    city: str
    supplies: str  # what the merchant buys from them (shown to the merchant)
    lead_time_days: int
    on_time_rate: float  # share of deliveries that arrive on the expected date
    order_weekdays: tuple[int, ...]  # days the merchant places orders with this supplier (0 = Monday)


@dataclass(frozen=True)
class SegmentSpec:
    code: str
    label: str
    share: float
    affinity: dict[str, float] = field(default_factory=dict)  # category code -> relative preference
    qty_boost: float = 0.0  # extra chance of buying 2+ units (small businesses buy in multiples)


@dataclass(frozen=True)
class BusinessTemplate:
    key: str
    label: str
    categories: tuple[CategorySpec, ...]
    families: tuple[FamilySpec, ...]
    suppliers: tuple[SupplierSpec, ...]
    segments: tuple[SegmentSpec, ...]
    roles: dict[str, tuple[str, ...]]  # scenario role -> preferred family / category / supplier codes, in order
    orders_per_day_hint: float = 38.0
    expenses_monthly: dict[str, float] = field(default_factory=dict)


# ---- shared geography --------------------------------------------------------------------------------------
ZONE_DIRECTIONS = ("South", "Central", "East", "West", "North")
CITY_LOCALITIES: dict[str, dict[str, tuple[str, ...]]] = {
    "Bengaluru": {"South": ("Jayanagar", "JP Nagar", "BTM Layout", "Banashankari", "Basavanagudi"), "Central": ("MG Road", "Shivajinagar", "Richmond Town"), "East": ("Indiranagar", "Whitefield", "Marathahalli", "HSR Layout"), "West": ("Rajajinagar", "Vijayanagar", "Malleshwaram"), "North": ("Hebbal", "Yelahanka", "RT Nagar")},
    "Mumbai": {"South": ("Colaba", "Worli", "Dadar"), "Central": ("Kurla", "Ghatkopar", "Sion"), "East": ("Chembur", "Mulund", "Vikhroli"), "West": ("Andheri", "Bandra", "Goregaon"), "North": ("Borivali", "Kandivali", "Malad")},
    "Delhi": {"South": ("Saket", "Lajpat Nagar", "Hauz Khas"), "Central": ("Connaught Place", "Karol Bagh", "Paharganj"), "East": ("Laxmi Nagar", "Preet Vihar", "Mayur Vihar"), "West": ("Rajouri Garden", "Janakpuri", "Punjabi Bagh"), "North": ("Model Town", "Rohini", "Pitampura")},
    "Chennai": {"South": ("Adyar", "Velachery", "Tambaram"), "Central": ("T Nagar", "Egmore", "Nungambakkam"), "East": ("Mylapore", "Besant Nagar", "Thiruvanmiyur"), "West": ("Anna Nagar", "Vadapalani", "Porur"), "North": ("Perambur", "Tondiarpet", "Madhavaram")},
    "Hyderabad": {"South": ("Mehdipatnam", "Attapur", "Rajendranagar"), "Central": ("Abids", "Koti", "Nampally"), "East": ("Uppal", "LB Nagar", "Dilsukhnagar"), "West": ("Gachibowli", "Madhapur", "Kondapur"), "North": ("Secunderabad", "Kompally", "Alwal")},
    "Pune": {"South": ("Kothrud", "Katraj", "Sinhagad Road"), "Central": ("Deccan", "Camp", "Shivajinagar"), "East": ("Kharadi", "Viman Nagar", "Hadapsar"), "West": ("Baner", "Aundh", "Wakad"), "North": ("Pimpri", "Chinchwad", "Bhosari")},
}
# Which zone a walk-in customer of a store in a given zone usually comes from (store channel)
STORE_ZONE_MIX = {"same": 0.62, "neighbour": 0.28, "far": 0.10}
ONLINE_ZONE_MIX = {"South": 0.26, "Central": 0.16, "East": 0.24, "West": 0.18, "North": 0.16}
NEIGHBOURS = {"South": ("Central", "West"), "Central": ("South", "East", "West", "North"), "East": ("Central", "South"), "West": ("Central", "North"), "North": ("Central", "West")}

CHANNEL_MIX = {"STORE": 0.74, "ONLINE": 0.14, "WHATSAPP": 0.12}
RETURNING_SHARE = {"STORE": 0.34, "ONLINE": 0.45, "WHATSAPP": 0.78}  # share of orders placed by an existing customer
PAYMENT_MIX = {"STORE": {"UPI": 0.62, "CASH": 0.18, "CARD": 0.20}, "ONLINE": {"UPI": 0.70, "CARD": 0.30}, "WHATSAPP": {"UPI": 0.85, "CASH": 0.15}}
WEEKDAY_FACTOR = (0.86, 0.84, 0.90, 0.95, 1.05, 1.30, 1.24)  # Monday..Sunday (weekends are busier)
PAYDAY_FACTOR = 1.08  # first five days of the month
BASKET_SIZES = ((1, 0.60), (2, 0.28), (3, 0.12))
DELIVERY_FEE_COST = 55.0  # what a delivery partner charges the merchant per online / WhatsApp order
PACKAGING_RATE = 0.012  # packaging cost as a share of online order value

FIRST_NAMES = ("Aarav", "Vihaan", "Aditya", "Arjun", "Sai", "Rohan", "Karthik", "Pranav", "Rahul", "Nikhil", "Siddharth", "Varun", "Manoj", "Suresh", "Ravi", "Kiran", "Ananya", "Diya", "Isha", "Kavya", "Meera", "Priya", "Sneha", "Pooja", "Divya", "Lakshmi", "Shreya", "Nandini", "Asha", "Bhavana", "Deepa", "Harini", "Farhan", "Imran", "Ayesha", "Zoya", "John", "Mary", "Joseph", "Fatima")
LAST_NAMES = ("Rao", "Reddy", "Sharma", "Iyer", "Nair", "Gowda", "Shetty", "Kumar", "Patel", "Menon", "Hegde", "Pai", "Bhat", "Naidu", "Singh", "Gupta", "Joshi", "Kulkarni", "Das", "Khan", "Fernandes", "Mathew", "Pillai", "Murthy", "Prasad")
SMALL_BUSINESS_NAMES = ("Cafe", "Tutorials", "Clinic", "Studio", "Travels", "Enterprises", "Associates", "Coworking", "Boutique", "Print Shop")


# ---- electronics & mobile accessories -------------------------------------------------------------------------
PHONE_MODELS = (
    "Nova X1", "Orbit 5G", "Pixo Lite", "Zentra Z9", "Aurio A15", "Aurio S23", "Kiro Note 13", "Kiro 13C", "Nordik CE3", "Vela T2", "Narza 60", "Motiv G54", "Nova X2",
    "Orbit Neo", "Pixo Max", "Zentra Z7", "Aurio M34", "Kiro Note 12", "Vela Y28", "Narza 70", "Motiv E13", "Lumo P5", "Tecra Spark", "Infini Hot 40", "Kiro 12", "Vela V30",
)


def _models(n: int) -> tuple[tuple[str, float], ...]:
    return tuple((m, 1.0) for m in PHONE_MODELS[:n])


def _colors(*names: str) -> tuple[tuple[str, float], ...]:
    return tuple((n, 1.0) for n in names)


ELECTRONICS = BusinessTemplate(
    key="electronics",
    label="Consumer electronics & mobile accessories",
    categories=(
        CategorySpec("PHONES", "Smartphones", 0.08, "Entry and mid-range smartphones", online_share=0.25),
        CategorySpec("CASES", "Cases & screen guards", 0.52, "Phone cases, tempered glass, guards and grips"),
        CategorySpec("CHARGE", "Chargers & cables", 0.40, "Wall and car chargers, cables, adapters"),
        CategorySpec("AUDIO", "Wireless audio", 0.27, "Bluetooth speakers, earbuds, neckbands, headphones"),
        CategorySpec("WIRED", "Wired audio", 0.44, "Wired earphones, AUX cables and audio adapters"),
        CategorySpec("POWER", "Power banks", 0.23, "Portable power banks"),
        CategorySpec("WEAR", "Smart wearables", 0.24, "Smartwatches, fitness bands and straps"),
        CategorySpec("COMP", "Computer accessories", 0.32, "Mice, keyboards, hubs, pen drives, webcams"),
        CategorySpec("STORAGE", "Memory cards & storage", 0.20, "microSD cards, SSDs and card readers"),
        CategorySpec("HOME", "Smart home & electricals", 0.32, "Smart bulbs, plugs, extension boards"),
    ),
    families=(
        # smartphones (high value, low margin, bought in store)
        FamilySpec("NOVA", "PHONES", "Nova X1 {v}", "Nova", 12999, 0.07, 0.9, (("4GB/64GB Black", 1.0), ("4GB/64GB Blue", 1.0), ("6GB/128GB Black", 1.15), ("6GB/128GB Green", 1.15)), "PHONIX"),
        FamilySpec("ORBIT", "PHONES", "Orbit 5G {v}", "Orbit", 18999, 0.08, 0.6, (("6GB/128GB Black", 1.0), ("8GB/128GB Silver", 1.1), ("8GB/256GB Silver", 1.2)), "PHONIX"),
        FamilySpec("PIXO", "PHONES", "Pixo Lite {v}", "Pixo", 8499, 0.06, 0.7, (("4GB/64GB Grey", 1.0), ("4GB/128GB Grey", 1.12)), "PHONIX"),
        FamilySpec("ZEN", "PHONES", "Zentra Z9 Pro {v}", "Zentra", 24999, 0.09, 0.25, (("8GB/256GB Black", 1.0), ("12GB/256GB Blue", 1.1)), "PHONIX"),
        # cases & guards (long tail across phone models)
        FamilySpec("CLR", "CASES", "Shieldo Clear Case for {v}", "Shieldo", 349, 0.56, 3.0, _models(26), "METRO"),
        FamilySpec("ARM", "CASES", "Shieldo Armor Case for {v}", "Shieldo", 599, 0.52, 1.6, _models(26), "METRO"),
        FamilySpec("FLIP", "CASES", "Shieldo Flip Wallet Case for {v}", "Shieldo", 499, 0.54, 0.9, _models(26), "METRO"),
        FamilySpec("GLS", "CASES", "Tempered Glass for {v}", "Shieldo", 249, 0.62, 4.0, _models(26), "METRO"),
        FamilySpec("PRV", "CASES", "Privacy Glass for {v}", "Shieldo", 399, 0.58, 1.0, _models(26), "METRO"),
        FamilySpec("CAM", "CASES", "Camera Lens Guard for {v}", "Shieldo", 199, 0.58, 0.8, _models(26), "METRO"),
        FamilySpec("SKIN", "CASES", "Matte Back Skin for {v}", "Shieldo", 299, 0.6, 0.5, _models(26), "METRO"),
        FamilySpec("RING", "CASES", "Shieldo Ring Holder {v}", "Shieldo", 149, 0.6, 0.6, _colors("Black", "Silver", "Rose Gold", "Blue", "Red", "Green"), "METRO"),
        FamilySpec("GRIP", "CASES", "Shieldo Pop Grip {v}", "Shieldo", 199, 0.6, 0.5, _colors("Marble", "Galaxy", "Floral", "Matte Black", "Pastel", "Cricket", "Tiger", "Mandala"), "METRO"),
        FamilySpec("STAND", "CASES", "Shieldo Desk Phone Stand {v}", "Shieldo", 299, 0.5, 0.35, _colors("Black", "White"), "METRO"),
        FamilySpec("LAN", "CASES", "Shieldo Phone Lanyard {v}", "Shieldo", 249, 0.58, 0.3, _colors("Black", "Beige", "Pink", "Olive"), "METRO"),
        # chargers & cables
        FamilySpec("C20", "CHARGE", "Voltix 20W USB-C Charger {v}", "Voltix", 699, 0.40, 2.0, _colors("White", "Black"), "POWERHUB"),
        FamilySpec("C33", "CHARGE", "Voltix 33W Fast Charger {v}", "Voltix", 999, 0.36, 1.4, _colors("White", "Black"), "POWERHUB"),
        FamilySpec("C65", "CHARGE", "Voltix 65W GaN Charger {v}", "Voltix", 2199, 0.32, 0.45, _colors("White"), "POWERHUB"),
        FamilySpec("CBC", "CHARGE", "Voltix USB-C Cable {v}", "Voltix", 299, 0.55, 3.4, (("1m White", 1.0), ("1m Black", 1.0), ("2m White", 1.3), ("Braided 1m Red", 1.4), ("Braided 1m Blue", 1.4), ("Braided 2m Grey", 1.7)), "METRO"),
        FamilySpec("CBL", "CHARGE", "Voltix Lightning Cable {v}", "Voltix", 399, 0.50, 1.2, (("1m", 1.0), ("2m", 1.3)), "METRO"),
        FamilySpec("CBM", "CHARGE", "Voltix Micro-USB Cable 1m", "Voltix", 149, 0.55, 0.8, (("", 1.0),), "METRO"),
        FamilySpec("CAR", "CHARGE", "Voltix Dual-port Car Charger {v}", "Voltix", 549, 0.42, 0.6, _colors("Black", "Silver"), "POWERHUB"),
        FamilySpec("WLC", "CHARGE", "Voltix 15W Wireless Charging Pad", "Voltix", 1299, 0.34, 0.4, (("", 1.0),), "POWERHUB"),
        FamilySpec("TRV", "CHARGE", "Voltix Universal Travel Adapter", "Voltix", 499, 0.48, 0.45, (("", 1.0),), "POWERHUB"),
        # wireless audio
        FamilySpec("SPKX", "AUDIO", "Wireless Speaker X", "Wavio", 2499, 0.28, 2.4, (("", 1.0),), "SOUNDWAVE"),
        FamilySpec("SPKM", "AUDIO", "Wavio Mini Speaker {v}", "Wavio", 1299, 0.30, 1.2, _colors("Black", "Blue", "Red"), "SOUNDWAVE"),
        FamilySpec("SPKP", "AUDIO", "Wavio Party Speaker Max", "Wavio", 6999, 0.24, 0.25, (("", 1.0),), "DECCAN"),
        FamilySpec("BUDL", "AUDIO", "Wavio Buds Lite {v}", "Wavio", 1099, 0.20, 2.2, _colors("Black", "White"), "DECCAN"),
        FamilySpec("BUDP", "AUDIO", "Wavio Buds Pro ANC {v}", "Wavio", 2999, 0.27, 1.3, _colors("Black", "White", "Teal"), "DECCAN"),
        FamilySpec("NECK", "AUDIO", "Wavio Neckband 200 {v}", "Wavio", 899, 0.32, 1.6, _colors("Black", "Blue"), "DECCAN"),
        FamilySpec("HP5", "AUDIO", "Wavio Over-ear Headphones H5 {v}", "Wavio", 2499, 0.28, 0.5, _colors("Black", "Beige"), "DECCAN"),
        FamilySpec("SBAR", "AUDIO", "Wavio Soundbar S2", "Wavio", 5499, 0.22, 0.15, (("", 1.0),), "DECCAN"),
        # wired audio
        FamilySpec("E1", "WIRED", "Wavio Wired Earphones E1 {v}", "Wavio", 249, 0.45, 1.5, _colors("Black", "White"), "DECCAN"),
        FamilySpec("EC", "WIRED", "Wavio USB-C Earphones {v}", "Wavio", 449, 0.42, 0.9, _colors("Black", "White"), "DECCAN"),
        FamilySpec("AUX", "WIRED", "3.5mm AUX Cable 1m", "Voltix", 149, 0.55, 0.4, (("", 1.0),), "METRO"),
        FamilySpec("A35", "WIRED", "USB-C to 3.5mm Adapter", "Voltix", 199, 0.50, 0.5, (("", 1.0),), "METRO"),
        FamilySpec("GH3", "WIRED", "Kalpa Gaming Headset G3", "Kalpa", 1499, 0.33, 0.3, (("", 1.0),), "TECHLINE"),
        # power banks
        FamilySpec("PB10", "POWER", "Voltix 10000mAh Power Bank {v}", "Voltix", 1199, 0.24, 1.5, _colors("Black", "White", "Blue"), "POWERHUB"),
        FamilySpec("PB20", "POWER", "Voltix 20000mAh Power Bank {v}", "Voltix", 1899, 0.22, 0.9, _colors("Black", "Grey"), "POWERHUB"),
        FamilySpec("PBM", "POWER", "Voltix Magnetic Power Bank 5000 {v}", "Voltix", 1599, 0.25, 0.35, _colors("Black", "Lavender"), "POWERHUB"),
        FamilySpec("PBS", "POWER", "Voltix Slim 5000mAh {v}", "Voltix", 899, 0.26, 0.6, _colors("Black", "White"), "POWERHUB"),
        # wearables
        FamilySpec("WF", "WEAR", "Zentra Fit Smartwatch {v}", "Zentra", 1999, 0.22, 1.0, _colors("Black", "Rose", "Blue"), "WEARIX"),
        FamilySpec("WP", "WEAR", "Zentra Pro Smartwatch {v}", "Zentra", 3999, 0.21, 0.4, _colors("Black", "Silver"), "WEARIX"),
        FamilySpec("BAND", "WEAR", "Zentra Fitness Band 4 {v}", "Zentra", 1299, 0.24, 0.5, _colors("Black", "Teal"), "WEARIX"),
        FamilySpec("STRP", "WEAR", "Zentra Silicone Strap 22mm {v}", "Zentra", 299, 0.55, 0.6, _colors("Black", "Navy", "Olive", "Orange"), "WEARIX"),
        # computer accessories
        FamilySpec("MW", "COMP", "Kalpa Wireless Mouse M2 {v}", "Kalpa", 599, 0.35, 1.0, _colors("Black", "Grey"), "TECHLINE"),
        FamilySpec("MG", "COMP", "Kalpa Gaming Mouse X7", "Kalpa", 1299, 0.30, 0.25, (("", 1.0),), "TECHLINE"),
        FamilySpec("KB", "COMP", "Kalpa Wireless Keyboard K3", "Kalpa", 1199, 0.30, 0.35, (("", 1.0),), "TECHLINE"),
        FamilySpec("KMC", "COMP", "Kalpa Keyboard & Mouse Combo", "Kalpa", 1499, 0.30, 0.3, (("", 1.0),), "TECHLINE"),
        FamilySpec("HUB", "COMP", "Kalpa 4-port USB Hub", "Kalpa", 499, 0.40, 0.35, (("", 1.0),), "TECHLINE"),
        FamilySpec("PD", "COMP", "Storix Pen Drive {v}", "Storix", 449, 0.25, 1.2, (("32GB", 1.0), ("64GB", 1.4), ("128GB", 2.1)), "KAVERI"),
        FamilySpec("PAD", "COMP", "Kalpa Mouse Pad {v}", "Kalpa", 249, 0.5, 0.4, (("Standard", 1.0), ("XL Desk Mat", 2.4), ("Gel Wrist Rest", 1.6)), "TECHLINE"),
        FamilySpec("LST", "COMP", "Kalpa Aluminium Laptop Stand", "Kalpa", 999, 0.38, 0.2, (("", 1.0),), "TECHLINE"),
        FamilySpec("CAMW", "COMP", "Kalpa HD Webcam C1", "Kalpa", 1799, 0.28, 0.3, (("", 1.0),), "TECHLINE", lifecycle="fading"),
        # storage
        FamilySpec("MSD", "STORAGE", "Storix microSD Card {v}", "Storix", 399, 0.20, 1.4, (("32GB", 1.0), ("64GB", 1.5), ("128GB", 2.5), ("256GB", 4.5)), "KAVERI"),
        FamilySpec("SSD", "STORAGE", "Storix Portable SSD {v}", "Storix", 5499, 0.15, 0.12, (("500GB", 1.0), ("1TB", 1.6)), "KAVERI"),
        FamilySpec("CRD", "STORAGE", "Storix USB Card Reader", "Storix", 249, 0.45, 0.4, (("", 1.0),), "KAVERI"),
        # smart home & electricals
        FamilySpec("BULB", "HOME", "Lumo Smart Bulb 9W {v}", "Lumo", 699, 0.30, 0.5, _colors("White", "RGB"), "TECHLINE"),
        FamilySpec("EXT", "HOME", "Voltix 4-socket Extension Board {v}", "Voltix", 549, 0.35, 0.6, (("1.5m", 1.0), ("3m", 1.25)), "POWERHUB"),
        FamilySpec("LED", "HOME", "Lumo LED Strip 5m", "Lumo", 899, 0.35, 0.3, (("", 1.0),), "TECHLINE"),
        FamilySpec("PLUG", "HOME", "Lumo Smart Plug 16A", "Lumo", 799, 0.30, 0.25, (("", 1.0),), "TECHLINE"),
    ),
    suppliers=(
        SupplierSpec("METRO", "Metro Mobile Distributors", "Bengaluru", "Cases, screen guards and cables", 2, 0.94, (0, 3)),
        SupplierSpec("SOUNDWAVE", "SoundWave Wholesale", "Chennai", "Portable Bluetooth speakers", 6, 0.92, (1, 4)),
        SupplierSpec("DECCAN", "Deccan Audio Traders", "Bengaluru", "Earbuds, headphones, party speakers and soundbars", 3, 0.93, (0, 3)),
        SupplierSpec("POWERHUB", "PowerHub India", "Mumbai", "Chargers, power banks and extension boards", 5, 0.92, (1, 4)),
        SupplierSpec("TECHLINE", "Techline Computer Supplies", "Bengaluru", "Computer accessories and smart home", 3, 0.95, (2,)),
        SupplierSpec("WEARIX", "Wearix Distribution", "Delhi", "Smartwatches and fitness bands", 6, 0.91, (1,)),
        SupplierSpec("KAVERI", "Kaveri Storage Solutions", "Hyderabad", "Memory cards, pen drives and SSDs", 4, 0.94, (2,)),
        SupplierSpec("PHONIX", "Phonix Mobiles (authorised distributor)", "Bengaluru", "Smartphones", 2, 0.96, (0, 2, 4)),
    ),
    segments=(
        SegmentSpec("STUDENT", "Students", 0.25, {"AUDIO": 1.5, "WIRED": 1.4, "CASES": 1.2, "POWER": 1.2, "STORAGE": 1.1, "COMP": 1.1, "PHONES": 0.6, "HOME": 0.5}),
        SegmentSpec("YOUNG_PROFESSIONAL", "Young professionals", 0.35, {"AUDIO": 1.2, "WEAR": 1.4, "COMP": 1.3, "CHARGE": 1.1, "PHONES": 1.1, "HOME": 0.9}),
        SegmentSpec("FAMILY", "Families", 0.30, {"PHONES": 1.3, "CASES": 1.1, "HOME": 1.6, "POWER": 0.9, "WIRED": 0.8, "COMP": 0.7}),
        SegmentSpec("SMALL_BUSINESS", "Small businesses", 0.10, {"COMP": 1.5, "STORAGE": 1.5, "CHARGE": 1.3, "HOME": 1.2, "PHONES": 0.8, "WEAR": 0.5}, qty_boost=0.25),
    ),
    roles={
        "stockout_product": ("SPKX", "PB10", "C33"),
        "opportunity_category": ("AUDIO", "WEAR", "POWER"),
        "delay_supplier": ("POWERHUB", "DECCAN", "TECHLINE"),
        "margin_product": ("BUDL", "NECK", "PB10"),
        "excess_product": ("CAMW", "GH3", "SBAR"),
    },
    orders_per_day_hint=38.0,
    expenses_monthly={"RENT": 85_000.0, "SALARIES": 160_000.0, "UTILITIES": 11_500.0, "MARKETING": 9_000.0, "OTHER": 6_000.0},
)


TEMPLATES: dict[str, BusinessTemplate] = {ELECTRONICS.key: ELECTRONICS}


def get_template(key: str | None) -> BusinessTemplate:
    return TEMPLATES.get(key or ELECTRONICS.key, ELECTRONICS)


def localities(city: str, direction: str) -> tuple[str, ...]:
    table = CITY_LOCALITIES.get(city)
    if table:
        return table[direction]
    return tuple(f"{direction} {city} {i}" for i in (1, 2, 3))
