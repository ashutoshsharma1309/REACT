"""Enumerations shared by models and schemas. Stored as VARCHAR (non-native) for portability."""
from __future__ import annotations

import enum


class OperationalStatus(str, enum.Enum):
    OPERATIONAL = "OPERATIONAL"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    MAINTENANCE = "MAINTENANCE"


class LineStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    BOTTLENECKED = "BOTTLENECKED"
    DOWN = "DOWN"


class MachineStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    IDLE = "IDLE"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    MAINTENANCE = "MAINTENANCE"


class Criticality(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SensorType(str, enum.Enum):
    TEMPERATURE = "TEMPERATURE"
    VIBRATION = "VIBRATION"
    POWER = "POWER"
    PRESSURE = "PRESSURE"
    RPM = "RPM"


class SensorStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    FAULTY = "FAULTY"
    OFFLINE = "OFFLINE"


class ComponentCondition(str, enum.Enum):
    GOOD = "GOOD"
    WORN = "WORN"
    CRITICAL = "CRITICAL"
    REPLACED = "REPLACED"


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    IN_PRODUCTION = "IN_PRODUCTION"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"


class PaymentStatus(str, enum.Enum):
    PAID = "PAID"
    PENDING = "PENDING"
    OVERDUE = "OVERDUE"


class CustomerSegment(str, enum.Enum):
    # manufacturing (B2B)
    ENTERPRISE = "ENTERPRISE"
    MID_MARKET = "MID_MARKET"
    SMB = "SMB"
    # merchant (retail)
    STUDENT = "STUDENT"
    YOUNG_PROFESSIONAL = "YOUNG_PROFESSIONAL"
    FAMILY = "FAMILY"
    SMALL_BUSINESS = "SMALL_BUSINESS"


class InventoryItemType(str, enum.Enum):
    PRODUCT = "PRODUCT"
    COMPONENT = "COMPONENT"


class SupplierStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    AT_RISK = "AT_RISK"
    SUSPENDED = "SUSPENDED"


class BatchStatus(str, enum.Enum):
    ORDERED = "ORDERED"
    IN_TRANSIT = "IN_TRANSIT"
    DELAYED = "DELAYED"
    RECEIVED = "RECEIVED"
    REJECTED = "REJECTED"


class MaintenanceType(str, enum.Enum):
    PREVENTIVE = "PREVENTIVE"
    CORRECTIVE = "CORRECTIVE"
    PREDICTIVE = "PREDICTIVE"
    INSPECTION = "INSPECTION"


class Severity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, enum.Enum):
    # Phase 1 uses DETECTED and RESOLVED. The remaining states are reserved for the agent pipeline.
    DETECTED = "DETECTED"
    INVESTIGATING = "INVESTIGATING"
    AWAITING_DECISION = "AWAITING_DECISION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    RESOLVED = "RESOLVED"


class IncidentType(str, enum.Enum):
    ABNORMAL_TEMPERATURE = "ABNORMAL_TEMPERATURE"
    ABNORMAL_VIBRATION = "ABNORMAL_VIBRATION"
    ABNORMAL_POWER = "ABNORMAL_POWER"
    ABNORMAL_PRESSURE = "ABNORMAL_PRESSURE"
    PRODUCTION_DROP = "PRODUCTION_DROP"
    DEFECT_SPIKE = "DEFECT_SPIKE"
    MACHINE_DOWNTIME = "MACHINE_DOWNTIME"
    MACHINE_DEGRADATION = "MACHINE_DEGRADATION"  # correlated multi-sensor anomaly
    SENSOR_FAULT = "SENSOR_FAULT"


class RiskType(str, enum.Enum):
    INVENTORY_SHORTAGE = "INVENTORY_SHORTAGE"
    SUPPLIER_DELAY = "SUPPLIER_DELAY"
    PRODUCTION_BOTTLENECK = "PRODUCTION_BOTTLENECK"
    REVENUE_DECLINE = "REVENUE_DECLINE"
    MARGIN_DECLINE = "MARGIN_DECLINE"
    DELIVERY_DELAY = "DELIVERY_DELAY"
    DEMAND_SUPPLY_MISMATCH = "DEMAND_SUPPLY_MISMATCH"
    # merchant
    STOCKOUT_RISK = "STOCKOUT_RISK"
    DEMAND_DECLINE = "DEMAND_DECLINE"
    SUPPLIER_RELIABILITY = "SUPPLIER_RELIABILITY"
    PRICE_ANOMALY = "PRICE_ANOMALY"
    DISCOUNT_LEAKAGE = "DISCOUNT_LEAKAGE"
    EXCESS_INVENTORY = "EXCESS_INVENTORY"
    SLOW_MOVING_INVENTORY = "SLOW_MOVING_INVENTORY"
    REPEAT_CUSTOMER_DECLINE = "REPEAT_CUSTOMER_DECLINE"


class RiskStatus(str, enum.Enum):
    OPEN = "OPEN"
    MITIGATING = "MITIGATING"
    RESOLVED = "RESOLVED"


class OpportunityType(str, enum.Enum):
    DEMAND_OPPORTUNITY = "DEMAND_OPPORTUNITY"
    UNDERSERVED_REGION = "UNDERSERVED_REGION"
    HIGH_MARGIN_GROWTH = "HIGH_MARGIN_GROWTH"
    SEGMENT_GROWTH = "SEGMENT_GROWTH"
    EXCESS_INVENTORY = "EXCESS_INVENTORY"
    AVAILABLE_CAPACITY = "AVAILABLE_CAPACITY"
    PRICING_OPPORTUNITY = "PRICING_OPPORTUNITY"
    # merchant
    CATEGORY_OPPORTUNITY = "CATEGORY_OPPORTUNITY"
    REGIONAL_OPPORTUNITY = "REGIONAL_OPPORTUNITY"


class OpportunityStatus(str, enum.Enum):
    NEW = "NEW"
    REVIEWED = "REVIEWED"
    DISMISSED = "DISMISSED"


class Confidence(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class DetectionSource(str, enum.Enum):
    OPERATIONAL_ANOMALY_ENGINE = "OPERATIONAL_ANOMALY_ENGINE"
    BUSINESS_RISK_ENGINE = "BUSINESS_RISK_ENGINE"
    OPPORTUNITY_ENGINE = "OPPORTUNITY_ENGINE"
    HISTORICAL_SEED = "HISTORICAL_SEED"


class SignalType(str, enum.Enum):
    MARKET_DEMAND = "MARKET_DEMAND"
    PRODUCT_DEMAND_GROWTH = "PRODUCT_DEMAND_GROWTH"
    REGIONAL_DEMAND = "REGIONAL_DEMAND"
    CATEGORY_GROWTH = "CATEGORY_GROWTH"
    SEASONAL_DEMAND = "SEASONAL_DEMAND"


class CompetitorSignalType(str, enum.Enum):
    PRICE_CHANGE = "PRICE_CHANGE"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    MARKET_ACTIVITY = "MARKET_ACTIVITY"
    AVAILABILITY = "AVAILABILITY"


class ScenarioType(str, enum.Enum):
    BEARING_FAILURE = "bearing_failure"
    SENSOR_FAILURE = "sensor_failure"
    SUPPLIER_DELAY = "supplier_delay"
    DEMAND_SPIKE = "demand_spike"
    DEMAND_DROP = "demand_drop"
    INVENTORY_SHORTAGE = "inventory_shortage"
    PRODUCTION_BOTTLENECK = "production_bottleneck"
    POWER_INSTABILITY = "power_instability"
    # merchant (supplier_delay is shared by name)
    STOCKOUT_CRISIS = "stockout_crisis"
    DEMAND_OPPORTUNITY = "demand_opportunity"
    MARGIN_LEAKAGE = "margin_leakage"
    EXCESS_INVENTORY = "excess_inventory"
    MERCHANT_SETUP = "merchant_setup"
    RESET = "reset"


class RunStatus(str, enum.Enum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# ---- Phase 2: investigation -------------------------------------------------------------
class InvestigationStatus(str, enum.Enum):
    CREATED = "CREATED"
    COLLECTING_EVIDENCE = "COLLECTING_EVIDENCE"
    ANALYZING = "ANALYZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InvestigationSubject(str, enum.Enum):
    INCIDENT = "INCIDENT"
    OPPORTUNITY = "OPPORTUNITY"
    RISK = "RISK"


class HypothesisStatus(str, enum.Enum):
    CANDIDATE = "CANDIDATE"
    SUPPORTED = "SUPPORTED"
    WEAKLY_SUPPORTED = "WEAKLY_SUPPORTED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


# ---- Phase 3: agentic layer -----------------------------------------------------------------
class AgentRunStatus(str, enum.Enum):
    CREATED = "CREATED"
    CLASSIFYING = "CLASSIFYING"
    INVESTIGATING = "INVESTIGATING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class AgentName(str, enum.Enum):
    DETECTION = "detection_agent"
    INVESTIGATION = "investigation_agent"
    EVIDENCE = "evidence_agent"
    DECISION = "decision_agent"
    ORCHESTRATOR = "orchestrator"


class AgentResultStatus(str, enum.Enum):
    CONCLUDED = "CONCLUDED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    FAILED = "FAILED"


class ToolCallStatus(str, enum.Enum):
    OK = "OK"
    DENIED = "DENIED"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
    ERROR = "ERROR"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"


# ---- Phase 4: decision, approval, action --------------------------------------------------------
class DecisionStatus(str, enum.Enum):
    DECISION_READY = "DECISION_READY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class ApprovalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActionStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DecisionReadiness(str, enum.Enum):
    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


# ---- REACT Merchant ---------------------------------------------------------------------------------------
class SalesChannel(str, enum.Enum):
    STORE = "STORE"
    ONLINE = "ONLINE"
    WHATSAPP = "WHATSAPP"


class PaymentMethod(str, enum.Enum):
    UPI = "UPI"
    CASH = "CASH"
    CARD = "CARD"


class StoreType(str, enum.Enum):
    PHYSICAL = "PHYSICAL"
    ONLINE = "ONLINE"


class PurchaseOrderStatus(str, enum.Enum):
    OPEN = "OPEN"
    PARTIALLY_RECEIVED = "PARTIALLY_RECEIVED"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class ExpenseCategory(str, enum.Enum):
    RENT = "RENT"
    SALARIES = "SALARIES"
    UTILITIES = "UTILITIES"
    MARKETING = "MARKETING"
    DELIVERY = "DELIVERY"
    PACKAGING = "PACKAGING"
    OTHER = "OTHER"


class DiscountScope(str, enum.Enum):
    STORE_WIDE = "STORE_WIDE"
    CATEGORY = "CATEGORY"
    PRODUCT = "PRODUCT"
    AD_HOC = "AD_HOC"


class PriceChangeReason(str, enum.Enum):
    INITIAL = "INITIAL"
    COST_REVISION = "COST_REVISION"
    PRICE_CHANGE = "PRICE_CHANGE"
