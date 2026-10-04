from app.simulation.scenarios.base import Scenario, ScenarioResult
from app.simulation.scenarios.bearing_failure import BearingFailureScenario
from app.simulation.scenarios.demand_drop import DemandDropScenario
from app.simulation.scenarios.demand_spike import DemandSpikeScenario
from app.simulation.scenarios.inventory_shortage import InventoryShortageScenario
from app.simulation.scenarios.power_instability import PowerInstabilityScenario
from app.simulation.scenarios.production_bottleneck import ProductionBottleneckScenario
from app.simulation.scenarios.sensor_failure import SensorFailureScenario
from app.simulation.scenarios.supplier_delay import SupplierDelayScenario

SCENARIOS: dict[str, type[Scenario]] = {
    "bearing_failure": BearingFailureScenario,
    "sensor_failure": SensorFailureScenario,
    "supplier_delay": SupplierDelayScenario,
    "demand_spike": DemandSpikeScenario,
    "demand_drop": DemandDropScenario,
    "inventory_shortage": InventoryShortageScenario,
    "production_bottleneck": ProductionBottleneckScenario,
    "power_instability": PowerInstabilityScenario,
}

__all__ = ["SCENARIOS", "Scenario", "ScenarioResult"]
