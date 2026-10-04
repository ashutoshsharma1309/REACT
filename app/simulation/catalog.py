"""Static catalogue describing Astra Manufacturing. Everything here is fictional.

The catalogue is data, not behaviour: the generator turns it into rows, and the
scenario engine uses the same codes to pick deterministic targets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

COMPANY = {
    "name": "Astra Manufacturing",
    "industry": "Industrial Components Manufacturing",
    "headquarters": "Bangalore, Karnataka, India",
    "currency": "INR",
}

PLANT = {"name": "Bangalore Manufacturing Plant", "location": "Peenya Industrial Area, Bangalore, Karnataka", "region": "South India"}


@dataclass(frozen=True)
class LineSpec:
    code: str
    name: str
    capacity: int  # units / day
    description: str
    base_defect_rate: float
    promised_lead_days: int
    production_lead_days: int


LINES: list[LineSpec] = [
    LineSpec("LINE-A", "Valve Assembly Line", 1200, "Machining and assembly of industrial valves", 0.016, 14, 9),
    LineSpec("LINE-B", "Precision Bearings & Gears Line", 2000, "Grinding, heat treatment and gear cutting", 0.012, 12, 7),
    LineSpec("LINE-C", "Hydraulic Fittings & Pump Components Line", 1600, "Forging, turning and finishing of fittings and pump parts", 0.019, 11, 6),
]


@dataclass(frozen=True)
class MachineSpec:
    code: str
    line: str
    name: str
    machine_type: str
    manufacturer: str
    model: str
    install_year: int
    criticality: str
    rated_power_kw: float
    components: tuple[tuple[str, str, int], ...]  # (name, component_type, expected_life_hours)
    sensors: tuple[str, ...]  # sensor types
    temp_profile: tuple[float, float, float, float] = (48.0, 66.0, 78.0, 90.0)  # normal_min, normal_max, warn, crit
    vib_profile: tuple[float, float, float, float] = (1.2, 4.2, 6.0, 9.0)
    pressure_profile: tuple[float, float, float, float] = (150.0, 210.0, 235.0, 260.0)
    rpm_profile: tuple[float, float, float, float] = (2400.0, 4200.0, 4800.0, 5400.0)


_CNC_COMPONENTS = (("Spindle Bearing", "BEARING", 20000), ("Spindle Motor", "MOTOR", 40000), ("Coolant Pump", "PUMP", 15000), ("Ball Screw Drive", "DRIVE", 30000))
_PRESS_COMPONENTS = (("Hydraulic Pump", "PUMP", 18000), ("Main Cylinder Seal Kit", "SEAL", 8000), ("Drive Motor", "MOTOR", 40000), ("Crown Bearing", "BEARING", 25000))
_FURNACE_COMPONENTS = (("Heating Element Bank", "HEATER", 12000), ("Blower Bearing", "BEARING", 20000), ("Control Thermocouple", "THERMOCOUPLE", 9000))
_ROBOT_COMPONENTS = (("Axis-2 Servo Motor", "MOTOR", 35000), ("Harmonic Drive Gearbox", "GEARBOX", 30000), ("Motion Controller", "CONTROLLER", 50000))
_GRINDER_COMPONENTS = (("Wheel Spindle Bearing", "BEARING", 18000), ("Wheel Motor", "MOTOR", 40000), ("Coolant Pump", "PUMP", 15000), ("Dressing Unit", "DRIVE", 22000))
_HOB_COMPONENTS = (("Hob Spindle Bearing", "BEARING", 20000), ("Index Gearbox", "GEARBOX", 30000), ("Servo Motor", "MOTOR", 35000))
_FINISH_COMPONENTS = (("Vibratory Drive Motor", "MOTOR", 30000), ("Media Pump", "PUMP", 12000), ("Drum Bearing", "BEARING", 22000))
_PKG_COMPONENTS = (("Conveyor Belt", "BELT", 10000), ("Conveyor Drive Motor", "MOTOR", 35000), ("Vision Inspection Camera", "CAMERA", 40000))

MACHINES: list[MachineSpec] = [
    MachineSpec("CNC-A1", "LINE-A", "CNC Lathe A1", "CNC Lathe", "Takumi Machine Works", "TL-450", 2019, "HIGH", 30, _CNC_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "RPM")),
    MachineSpec("VMC-A2", "LINE-A", "Vertical Machining Center A2", "Vertical Machining Center", "Nordwerk GmbH", "VMC-850", 2020, "HIGH", 35, _CNC_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "RPM")),
    MachineSpec("HPR-A3", "LINE-A", "Hydraulic Press A3", "Hydraulic Press", "Bharat Precision Tools", "HP-400T", 2017, "CRITICAL", 55, _PRESS_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "PRESSURE"), (42.0, 62.0, 74.0, 85.0), (1.5, 4.8, 6.5, 9.5)),
    MachineSpec("ROB-A4", "LINE-A", "Assembly Robot A4", "Assembly Robot", "Helios Automation", "HR-6X", 2021, "MEDIUM", 12, _ROBOT_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER"), (35.0, 52.0, 62.0, 72.0), (0.6, 2.4, 3.6, 5.5)),
    MachineSpec("GRD-B1", "LINE-B", "CNC Grinding Machine B1", "CNC Grinder", "Takumi Machine Works", "TG-320", 2018, "CRITICAL", 28, _GRINDER_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "RPM")),
    MachineSpec("CNC-B2", "LINE-B", "CNC Lathe B2", "CNC Lathe", "Takumi Machine Works", "TL-450", 2019, "HIGH", 30, _CNC_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "RPM")),
    MachineSpec("FUR-B3", "LINE-B", "Heat Treatment Furnace B3", "Heat Treatment Furnace", "Agni Thermal Systems", "HT-1200", 2016, "CRITICAL", 120, _FURNACE_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER"), (820.0, 880.0, 905.0, 950.0), (0.5, 2.0, 3.0, 4.5)),
    MachineSpec("HOB-B4", "LINE-B", "Gear Hobbing Machine B4", "Gear Hobbing Machine", "Nordwerk GmbH", "GH-300", 2020, "HIGH", 25, _HOB_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER")),
    MachineSpec("CNC-C1", "LINE-C", "CNC Turning Center C1", "CNC Turning Center", "Bharat Precision Tools", "TC-300", 2018, "HIGH", 28, _CNC_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "RPM")),
    MachineSpec("FRG-C2", "LINE-C", "Forging Press C2", "Forging Press", "Bharat Precision Tools", "FP-630T", 2015, "CRITICAL", 75, _PRESS_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER", "PRESSURE"), (45.0, 68.0, 80.0, 92.0), (2.0, 5.5, 7.5, 10.5), (170.0, 240.0, 265.0, 290.0)),
    MachineSpec("SFU-C3", "LINE-C", "Surface Finishing Unit C3", "Surface Finishing Unit", "Helios Automation", "SF-200", 2019, "MEDIUM", 18, _FINISH_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER")),
    MachineSpec("PKG-C4", "LINE-C", "Packaging & Test Station C4", "Packaging Station", "Helios Automation", "PT-100", 2021, "LOW", 8, _PKG_COMPONENTS, ("TEMPERATURE", "VIBRATION", "POWER"), (30.0, 45.0, 55.0, 65.0), (0.4, 1.8, 3.0, 4.5)),
]

SENSOR_UNITS = {"TEMPERATURE": "°C", "VIBRATION": "mm/s", "POWER": "kW", "PRESSURE": "bar", "RPM": "rpm"}


@dataclass(frozen=True)
class ProductSpec:
    sku: str
    name: str
    category: str
    line: str
    unit_cost: float
    selling_price: float
    target_margin: float
    base_daily_demand: float  # units/day across all customers at baseline
    typical_order_qty: int  # median line-item quantity (~1/3 of base daily demand => ~3 order lines/day)
    trend_pct: float = 0.0  # total demand growth over the history window (baseline story)
    region_bias: dict[str, float] = field(default_factory=dict)  # region -> multiplier
    components: tuple[str, ...] = ()  # component types consumed (see BOM)


PRODUCTS: list[ProductSpec] = [
    # Industrial Valves (LINE-A)
    ProductSpec("AV-GV50", "Gate Valve GV-50", "Industrial Valves", "LINE-A", 2100, 3150, 0.33, 166, 55),
    ProductSpec("AV-BV25", "Ball Valve BV-25", "Industrial Valves", "LINE-A", 1450, 2250, 0.35, 251, 84),
    ProductSpec("AV-GL40", "Globe Valve GL-40", "Industrial Valves", "LINE-A", 2600, 3900, 0.33, 125, 42),
    ProductSpec("AV-BF100", "Butterfly Valve BF-100", "Industrial Valves", "LINE-A", 3200, 4750, 0.32, 98, 33),
    ProductSpec("AV-CV65", "Check Valve CV-65", "Industrial Valves", "LINE-A", 1700, 2550, 0.33, 208, 69),
    ProductSpec("AV-CX80", "Industrial Valve X", "Industrial Valves", "LINE-A", 5400, 8900, 0.40, 153, 51, trend_pct=22.0, region_bias={"South India": 1.6, "Middle East": 1.3}),
    ProductSpec("AV-PRV32", "Pressure Relief Valve PRV-32", "Industrial Valves", "LINE-A", 2900, 4500, 0.36, 118, 39),
    # Precision Bearings (LINE-B)
    ProductSpec("PB-6205", "Deep Groove Ball Bearing 6205", "Precision Bearings", "LINE-B", 210, 320, 0.34, 481, 160),
    ProductSpec("PB-30205", "Tapered Roller Bearing 30205", "Precision Bearings", "LINE-B", 480, 720, 0.33, 277, 92),
    ProductSpec("PB-22210", "Spherical Roller Bearing 22210", "Precision Bearings", "LINE-B", 1350, 1990, 0.32, 129, 43),
    ProductSpec("PB-51105", "Thrust Bearing 51105", "Precision Bearings", "LINE-B", 260, 410, 0.37, 204, 68),
    ProductSpec("PB-NK20", "Needle Roller Bearing NK20", "Precision Bearings", "LINE-B", 160, 245, 0.35, 240, 80),
    # Gear Assemblies (LINE-B)
    ProductSpec("GA-HG120", "Helical Gear Set HG-120", "Gear Assemblies", "LINE-B", 4100, 6300, 0.35, 42, 14, trend_pct=8.0),
    ProductSpec("GA-SG80", "Spur Gear SG-80", "Gear Assemblies", "LINE-B", 1250, 1900, 0.34, 102, 34),
    ProductSpec("GA-WG40", "Worm Gear Reducer WG-40", "Gear Assemblies", "LINE-B", 6800, 10200, 0.33, 23, 8),
    ProductSpec("GA-PG200", "Planetary Gearbox PG-200", "Gear Assemblies", "LINE-B", 14500, 22500, 0.36, 12, 5, trend_pct=12.0),
    # Hydraulic Fittings (LINE-C)
    ProductSpec("HF-HC12", "Hydraulic Hose Coupling HC-12", "Hydraulic Fittings", "LINE-C", 320, 470, 0.32, 345, 115),
    ProductSpec("HF-FA32", "Flange Adapter FA-32", "Hydraulic Fittings", "LINE-C", 540, 800, 0.33, 190, 63),
    ProductSpec("HF-QC16", "Quick Connect Coupler QC-16", "Hydraulic Fittings", "LINE-C", 610, 940, 0.35, 217, 72, trend_pct=6.0),
    ProductSpec("HF-HPE20", "High-Pressure Elbow HPE-20", "Hydraulic Fittings", "LINE-C", 290, 420, 0.31, 236, 79),
    ProductSpec("HF-MB4", "Manifold Block MB-4", "Hydraulic Fittings", "LINE-C", 2300, 3400, 0.32, 36, 12),
    # Pump Components (LINE-C)
    ProductSpec("PC-IMP150", "Impeller IMP-150", "Pump Components", "LINE-C", 3600, 5300, 0.32, 50, 17, trend_pct=-9.0),
    ProductSpec("PC-MS45", "Mechanical Seal MS-45", "Pump Components", "LINE-C", 1150, 1650, 0.30, 109, 36, trend_pct=-7.0),
    ProductSpec("PC-PC200", "Pump Casing PC-200", "Pump Components", "LINE-C", 7200, 10400, 0.31, 20, 7, trend_pct=-10.0),
    ProductSpec("PC-SS30", "Shaft Sleeve SS-30", "Pump Components", "LINE-C", 680, 980, 0.31, 82, 27),
]

CATEGORIES = sorted({p.category for p in PRODUCTS})

# Bill of materials: component units consumed per finished unit, by product SKU prefix / category.
BOM_BY_CATEGORY: dict[str, dict[str, float]] = {
    "Industrial Valves": {"STEEL_CASTING": 1.0, "SEAL_KIT": 1.0, "FASTENERS": 6.0, "PACKAGING_MATERIAL": 1.0},
    "Precision Bearings": {"BEARING_STEEL": 0.6, "COATING_CHEMICALS": 0.05, "PACKAGING_MATERIAL": 0.5},
    "Gear Assemblies": {"GEAR_BLANK": 1.0, "BEARING_STEEL": 0.3, "FASTENERS": 4.0, "COATING_CHEMICALS": 0.1, "PACKAGING_MATERIAL": 1.0},
    "Hydraulic Fittings": {"FORGED_BILLET": 1.0, "HYDRAULIC_HOSE": 0.5, "SEAL_KIT": 0.8, "COATING_CHEMICALS": 0.1, "PACKAGING_MATERIAL": 0.5},
    "Pump Components": {"STEEL_CASTING": 0.8, "FORGED_BILLET": 0.4, "SEAL_KIT": 0.6, "FASTENERS": 3.0, "PACKAGING_MATERIAL": 1.0},
}
# Product-specific extra components
BOM_BY_SKU: dict[str, dict[str, float]] = {"AV-CX80": {"ELECTRONIC_ACTUATOR": 1.0}, "AV-PRV32": {"ELECTRONIC_ACTUATOR": 0.5}}


@dataclass(frozen=True)
class SupplierSpec:
    name: str
    component_type: str
    location: str
    country: str
    reliability: float
    lead_time_days: int
    unit_cost: float
    is_primary: bool = True
    batch_size: int = 5000


SUPPLIERS: list[SupplierSpec] = [
    SupplierSpec("Deccan Foundry Works", "STEEL_CASTING", "Coimbatore, Tamil Nadu", "India", 0.93, 9, 640.0, True, 6000),
    SupplierSpec("Vizag Cast Metals", "STEEL_CASTING", "Visakhapatnam, Andhra Pradesh", "India", 0.86, 11, 610.0, False, 4000),
    SupplierSpec("Kaveri Forgings Pvt Ltd", "FORGED_BILLET", "Pune, Maharashtra", "India", 0.91, 8, 210.0, True, 8000),
    SupplierSpec("Sakura Alloy Steels", "BEARING_STEEL", "Osaka", "Japan", 0.96, 24, 95.0, True, 12000),
    SupplierSpec("Bokaro Special Steels", "BEARING_STEEL", "Bokaro, Jharkhand", "India", 0.84, 12, 88.0, False, 8000),
    SupplierSpec("Sealtech Polymers", "SEAL_KIT", "Chennai, Tamil Nadu", "India", 0.9, 6, 145.0, True, 9000),
    SupplierSpec("Helios Controls GmbH", "ELECTRONIC_ACTUATOR", "Stuttgart", "Germany", 0.94, 21, 1850.0, True, 1500),
    SupplierSpec("Ganga Fasteners", "FASTENERS", "Ludhiana, Punjab", "India", 0.95, 5, 4.5, True, 60000),
    SupplierSpec("Flexo Hydraulics", "HYDRAULIC_HOSE", "Ahmedabad, Gujarat", "India", 0.88, 7, 120.0, True, 6000),
    SupplierSpec("Meridian Gear Blanks", "GEAR_BLANK", "Rajkot, Gujarat", "India", 0.89, 10, 780.0, True, 2500),
    SupplierSpec("AquaChem Surface Solutions", "COATING_CHEMICALS", "Mumbai, Maharashtra", "India", 0.92, 6, 320.0, True, 2000),
    SupplierSpec("Suraksha Packaging", "PACKAGING_MATERIAL", "Bangalore, Karnataka", "India", 0.97, 3, 18.0, True, 20000),
]

COMPONENT_TYPES = sorted({s.component_type for s in SUPPLIERS})

WAREHOUSES = {"finished": "Bangalore Central Warehouse", "components": "Bangalore Plant Store"}

REGIONS: dict[str, dict] = {
    "South India": {"weight": 0.34, "cities": ["Bangalore, Karnataka", "Chennai, Tamil Nadu", "Hyderabad, Telangana", "Coimbatore, Tamil Nadu", "Kochi, Kerala"]},
    "West India": {"weight": 0.26, "cities": ["Pune, Maharashtra", "Mumbai, Maharashtra", "Ahmedabad, Gujarat", "Surat, Gujarat", "Vadodara, Gujarat"]},
    "North India": {"weight": 0.17, "cities": ["Gurugram, Haryana", "Noida, Uttar Pradesh", "Ludhiana, Punjab", "Jaipur, Rajasthan", "Faridabad, Haryana"]},
    "East India": {"weight": 0.08, "cities": ["Kolkata, West Bengal", "Jamshedpur, Jharkhand", "Bhubaneswar, Odisha"]},
    "Middle East": {"weight": 0.09, "cities": ["Dubai, UAE", "Abu Dhabi, UAE", "Muscat, Oman", "Dammam, Saudi Arabia"]},
    "Southeast Asia": {"weight": 0.06, "cities": ["Singapore", "Bangkok, Thailand", "Kuala Lumpur, Malaysia", "Jakarta, Indonesia"]},
}

INDUSTRIES: dict[str, dict[str, float]] = {
    # industry -> category affinity multipliers
    "Automotive": {"Precision Bearings": 1.8, "Gear Assemblies": 1.5, "Hydraulic Fittings": 0.8, "Industrial Valves": 0.4, "Pump Components": 0.5},
    "Oil & Gas": {"Industrial Valves": 2.0, "Hydraulic Fittings": 1.4, "Pump Components": 1.3, "Precision Bearings": 0.5, "Gear Assemblies": 0.4},
    "Power Generation": {"Industrial Valves": 1.6, "Pump Components": 1.4, "Precision Bearings": 0.9, "Gear Assemblies": 0.8, "Hydraulic Fittings": 0.7},
    "Pharmaceuticals": {"Industrial Valves": 1.4, "Pump Components": 1.2, "Hydraulic Fittings": 0.6, "Precision Bearings": 0.5, "Gear Assemblies": 0.3},
    "Construction Equipment": {"Hydraulic Fittings": 2.0, "Gear Assemblies": 1.2, "Precision Bearings": 1.1, "Pump Components": 0.6, "Industrial Valves": 0.5},
    "Aerospace": {"Precision Bearings": 1.6, "Gear Assemblies": 1.2, "Hydraulic Fittings": 1.1, "Industrial Valves": 0.6, "Pump Components": 0.4},
    "Food Processing": {"Industrial Valves": 1.3, "Pump Components": 1.3, "Precision Bearings": 0.7, "Hydraulic Fittings": 0.6, "Gear Assemblies": 0.6},
    "Chemicals": {"Industrial Valves": 1.7, "Pump Components": 1.5, "Hydraulic Fittings": 0.8, "Precision Bearings": 0.5, "Gear Assemblies": 0.4},
    "Mining": {"Hydraulic Fittings": 1.5, "Gear Assemblies": 1.4, "Precision Bearings": 1.2, "Pump Components": 1.0, "Industrial Valves": 0.8},
    "Water Treatment": {"Industrial Valves": 1.8, "Pump Components": 1.6, "Hydraulic Fittings": 0.7, "Precision Bearings": 0.4, "Gear Assemblies": 0.4},
}

SEGMENTS = {"ENTERPRISE": {"weight": 0.2, "qty_mult": 1.6, "discount": 0.08, "orders_mult": 1.6}, "MID_MARKET": {"weight": 0.45, "qty_mult": 1.1, "discount": 0.04, "orders_mult": 1.0}, "SMB": {"weight": 0.35, "qty_mult": 0.8, "discount": 0.0, "orders_mult": 0.6}}

CUSTOMER_NAME_PARTS = {
    "prefix": ["Vardhan", "Meghna", "Trident", "Suryodaya", "Kalinga", "Nirmal", "Arka", "Shakti", "Prithvi", "Vikram", "Zenith", "Omega", "Sagar", "Indus", "Pinnacle", "Aditya", "Bluewave", "Ironclad", "Everest", "Lakshya", "Marut", "Nova", "Orbit", "Quantum", "Rudra", "Sigma", "Titan", "Ujjwal", "Vector", "Yantra"],
    "industry_word": {
        "Automotive": ["Auto Systems", "Motors", "Drivelines", "Auto Components"],
        "Oil & Gas": ["Petrochem", "Energy", "Offshore Services", "Refining"],
        "Power Generation": ["Power", "Energy Systems", "Turbines", "Utilities"],
        "Pharmaceuticals": ["Pharma", "Life Sciences", "Biotech", "Labs"],
        "Construction Equipment": ["Earthmovers", "Construction Machinery", "Infra Equipment", "Cranes"],
        "Aerospace": ["Aerospace", "Aviation Systems", "Avionics", "Aero Structures"],
        "Food Processing": ["Foods", "Agro Processing", "Dairy", "Beverages"],
        "Chemicals": ["Chemicals", "Specialty Chem", "Polymers", "Fertilizers"],
        "Mining": ["Minerals", "Mining Corp", "Ores", "Quarries"],
        "Water Treatment": ["Water Solutions", "Aqua Systems", "Envirotech", "Hydro Works"],
    },
    "suffix": ["Pvt Ltd", "Ltd", "Industries", "LLP", "Corporation", "Group", "FZE", "Sdn Bhd", "Pte Ltd"],
}

TECHNICIANS = ["R. Nagaraj", "S. Priya", "M. Iqbal", "K. Venkatesh", "A. D'Souza", "P. Lakshmi", "T. Bhaskar", "J. Mathew"]

COMPETITORS = ["Meridian Industrial Components", "Vulcan Precision Works", "Orion Flow Systems"]

MAINTENANCE_COST_BY_TYPE = {"PREVENTIVE": 18000.0, "INSPECTION": 6000.0, "PREDICTIVE": 25000.0, "CORRECTIVE": 95000.0}
