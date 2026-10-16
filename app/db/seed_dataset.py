"""Deterministic synthetic dataset for VoltCart, a fictional electronics retailer.

The structure is fixed; dates are expressed relative to an *anchor* (the seed
time) so that return / damage windows stay meaningful whenever the demo is
seeded. A few orders use absolute historic dates on purpose to exercise
policy-version selection (policy v1 era) and the "no applicable policy" path.

The same dataset seeds the application database (`scripts/seed_data.py`) and
the mock payment / shipping services, so both sides agree on references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

PRODUCTS: dict[str, tuple[str, str, int]] = {
    # id: (name, category, price in paise)
    "P-HEAD-01": ("AuraSound ANC Headphones", "audio", 899_900),
    "P-EARB-02": ("PulseBuds Wireless Earbuds", "audio", 349_900),
    "P-SPKR-03": ("BoomBox Mini Bluetooth Speaker", "audio", 249_900),
    "P-WATCH-04": ("FitTrack Smartwatch", "wearables", 1_299_900),
    "P-PHONE-05": ("Nova X5 Smartphone", "phones", 5_499_900),
    "P-LAPT-06": ("VoltBook Air 14 Laptop", "computers", 7_499_900),
    "P-TAB-07": ("SlateTab 11 Tablet", "tablets", 2_899_900),
    "P-CHRG-08": ("65W GaN Charger", "accessories", 219_900),
    "P-CABL-09": ("Braided USB-C Cable", "accessories", 69_900),
    "P-KEYB-10": ("Mechanical Keyboard K87", "accessories", 549_900),
    "P-MOUS-11": ("Ergo Wireless Mouse", "accessories", 179_900),
    "P-MON-12": ('UltraView 27" Monitor', "computers", 2_199_900),
}

CUSTOMERS: list[tuple[str, str, str]] = [
    # id, name, email
    ("C-1001", "Aarav Sharma", "aarav.sharma@example.com"),
    ("C-1002", "Priya Nair", "priya.nair@example.com"),
    ("C-1003", "Rohan Mehta", "rohan.mehta@example.com"),
    ("C-1004", "Ananya Iyer", "ananya.iyer@example.com"),
    ("C-1005", "Vikram Singh", "vikram.singh@example.com"),
    ("C-1006", "Kavya Reddy", "kavya.reddy@example.com"),
    ("C-1007", "Arjun Patel", "arjun.patel@example.com"),
    ("C-1008", "Meera Joshi", "meera.joshi@example.com"),
    ("C-1009", "Siddharth Rao", "siddharth.rao@example.com"),
    ("C-1010", "Isha Kapoor", "isha.kapoor@example.com"),
    ("C-1011", "Neel Banerjee", "neel.banerjee@example.com"),
    ("C-1012", "Zoya Khan", "zoya.khan@example.com"),
]

STAFF: list[tuple[str, str, str, str, int]] = [
    # id, name, email, role, approval limit (paise)
    ("S-01", "Riya Desai", "riya.desai@voltcart.example", "reviewer", 3_000_000),
    ("S-02", "Kabir Malhotra", "kabir.malhotra@voltcart.example", "senior_reviewer", 20_000_000),
]


def customer_subject(customer_id: str) -> str:
    return f"demo|customer|{customer_id}"


def staff_subject(staff_id: str) -> str:
    return f"demo|staff|{staff_id}"


@dataclass(frozen=True)
class D:
    """A date: either `days` relative to the anchor (negative = past) or absolute."""

    days: float | None = None
    absolute: datetime | None = None

    def at(self, anchor: datetime) -> datetime:
        if self.absolute is not None:
            return self.absolute
        assert self.days is not None
        return anchor + timedelta(days=self.days)


def ago(days: float) -> D:
    return D(days=-days)


def ahead(days: float) -> D:
    return D(days=days)


def on(year: int, month: int, day: int) -> D:
    return D(absolute=datetime(year, month, day, 10, 0, tzinfo=UTC))


@dataclass(frozen=True)
class ReturnSpec:
    status: str
    requested: D
    received: D | None = None
    inspection: str = "not_started"
    reason: str = "Changed my mind"


@dataclass(frozen=True)
class ShipmentSpec:
    carrier: str
    tracking_status: str  # label_created | in_transit | out_for_delivery | delivered | exception
    eta: D | None
    events: tuple[tuple[str, D, str], ...] = ()  # (status, when, location)


@dataclass(frozen=True)
class HistoricRefund:
    status: str  # succeeded | processing
    created: D


@dataclass(frozen=True)
class OrderSpec:
    id: str
    customer_id: str
    items: tuple[tuple[str, int], ...]  # (product_id, quantity)
    status: str
    placed: D
    delivered: D | None = None
    ret: ReturnSpec | None = None
    shipment: ShipmentSpec | None = None
    historic_refund: HistoricRefund | None = None
    note: str = ""
    method: str = field(default="upi")

    @property
    def paid_amount_minor(self) -> int:
        return sum(PRODUCTS[p][2] * q for p, q in self.items)


def _delivered_shipment(placed: float, delivered: float, carrier: str = "BlueDart") -> ShipmentSpec:
    return ShipmentSpec(
        carrier=carrier,
        tracking_status="delivered",
        eta=ago(delivered),
        events=(
            ("label_created", ago(placed - 0.2), "Bengaluru FC"),
            ("in_transit", ago(placed - 1), "Bengaluru Hub"),
            ("out_for_delivery", ago(delivered + 0.3), "Local Delivery Centre"),
            ("delivered", ago(delivered), "Customer address"),
        ),
    )


ORDERS: list[OrderSpec] = [
    # --- C-1001 Aarav: the headline example ("I returned my headphones last week...")
    OrderSpec(
        "ORD-5001",
        "C-1001",
        (("P-HEAD-01", 1),),
        "delivered",
        ago(16),
        ago(12),
        ReturnSpec("received", ago(8), ago(3), "passed", "Sound quality not as expected"),
        _delivered_shipment(16, 12),
        note="eligible refund",
    ),
    OrderSpec(
        "ORD-5002",
        "C-1001",
        (("P-CABL-09", 1),),
        "shipped",
        ago(3),
        None,
        None,
        ShipmentSpec(
            "Delhivery",
            "in_transit",
            ahead(2),
            (("label_created", ago(2.8), "Mumbai FC"), ("in_transit", ago(1.5), "Pune Hub")),
        ),
        note="in transit",
    ),
    OrderSpec(
        "ORD-5027",
        "C-1001",
        (("P-MOUS-11", 1),),
        "delivered",
        ago(13),
        ago(9),
        ReturnSpec("received", ago(6), ago(2), "passed", "Too small for my hand"),
        _delivered_shipment(13, 9),
        note="eligible refund (second refundable order)",
    ),
    # --- C-1002 Priya
    OrderSpec(
        "ORD-5003",
        "C-1002",
        (("P-WATCH-04", 1),),
        "delivered",
        ago(24),
        ago(20),
        ReturnSpec("received", ago(12), ago(5), "failed", "Screen flickers"),
        _delivered_shipment(24, 20),
        note="inspection failed",
    ),
    OrderSpec(
        "ORD-5004",
        "C-1002",
        (("P-EARB-02", 1),),
        "delivered",
        ago(6),
        ago(3),
        None,
        _delivered_shipment(6, 3),
        note="damaged item within window",
    ),
    # --- C-1003 Rohan
    OrderSpec(
        "ORD-5005",
        "C-1003",
        (("P-PHONE-05", 1),),
        "delivered",
        ago(14),
        ago(10),
        ReturnSpec("received", ago(6), ago(2), "passed", "Battery drains quickly"),
        _delivered_shipment(14, 10),
        note="eligible high value (senior reviewer)",
        method="card",
    ),
    OrderSpec(
        "ORD-5006",
        "C-1003",
        (("P-CHRG-08", 1),),
        "placed",
        ago(1),
        None,
        None,
        ShipmentSpec("BlueDart", "label_created", ahead(4), (("label_created", ago(0.5), "Chennai FC"),)),
        note="not yet shipped",
    ),
    OrderSpec(
        "ORD-5028",
        "C-1003",
        (("P-EARB-02", 1),),
        "delivered",
        ago(12),
        ago(8),
        ReturnSpec("received", ago(5), ago(1), "passed", "Connectivity drops"),
        _delivered_shipment(12, 8),
        note="eligible refund",
    ),
    # --- C-1004 Ananya
    OrderSpec(
        "ORD-5007",
        "C-1004",
        (("P-SPKR-03", 1),),
        "delivered",
        ago(13),
        ago(9),
        ReturnSpec("in_transit", ago(4), None, "not_started", "Too quiet"),
        _delivered_shipment(13, 9),
        note="return not yet received",
    ),
    OrderSpec(
        "ORD-5008",
        "C-1004",
        (("P-KEYB-10", 1),),
        "delivered",
        ago(19),
        ago(15),
        ReturnSpec("received", ago(10), ago(5), "passed", "Keys too loud"),
        _delivered_shipment(19, 15),
        historic_refund=HistoricRefund("succeeded", ago(2)),
        note="already refunded",
    ),
    OrderSpec(
        "ORD-5031",
        "C-1004",
        (("P-LAPT-06", 1),),
        "shipped",
        ago(4),
        None,
        None,
        ShipmentSpec(
            "BlueDart",
            "in_transit",
            ahead(1),
            (
                ("label_created", ago(3.8), "Delhi FC"),
                ("in_transit", ago(2), "Nagpur Hub"),
                ("in_transit", ago(0.5), "Hyderabad Hub"),
            ),
        ),
        note="in transit, arriving tomorrow",
    ),
    # --- C-1005 Vikram
    OrderSpec(
        "ORD-5009",
        "C-1005",
        (("P-LAPT-06", 1),),
        "delivered",
        ago(10),
        ago(6),
        ReturnSpec("received", ago(4), ago(1), "pending", "Fan noise"),
        _delivered_shipment(10, 6),
        note="inspection pending",
        method="card",
    ),
    OrderSpec(
        "ORD-5010",
        "C-1005",
        (("P-MON-12", 1),),
        "shipped",
        ago(11),
        None,
        None,
        ShipmentSpec(
            "Ekart",
            "in_transit",
            ago(4),
            (
                ("label_created", ago(10.5), "Kolkata FC"),
                ("in_transit", ago(9), "Kolkata Hub"),
                ("in_transit", ago(6), "Bhubaneswar Hub"),
            ),
        ),
        note="shipping delayed beyond estimate",
    ),
    OrderSpec(
        "ORD-5029",
        "C-1005",
        (("P-CABL-09", 1),),
        "delivered",
        ago(14),
        ago(10),
        ReturnSpec("received", ago(6), ago(2), "passed", "Wrong length"),
        _delivered_shipment(14, 10),
        note="eligible refund",
    ),
    # --- C-1006 Kavya
    OrderSpec(
        "ORD-5011",
        "C-1006",
        (("P-TAB-07", 1),),
        "delivered",
        ago(18),
        ago(14),
        ReturnSpec("received", ago(9), ago(3), "passed", "Screen too reflective"),
        _delivered_shipment(18, 14),
        note="eligible refund",
    ),
    OrderSpec(
        "ORD-5012",
        "C-1006",
        (("P-MOUS-11", 1),),
        "delivered",
        ago(64),
        ago(60),
        ReturnSpec("received", ago(30), ago(25), "passed", "Stopped working"),
        _delivered_shipment(64, 60),
        note="return requested outside window",
    ),
    # --- C-1007 Arjun
    OrderSpec(
        "ORD-5013",
        "C-1007",
        (("P-EARB-02", 1), ("P-CABL-09", 1)),
        "delivered",
        ago(15),
        ago(11),
        ReturnSpec("received", ago(7), ago(2), "passed", "Ordered by mistake"),
        _delivered_shipment(15, 11),
        note="eligible multi-item whole-order return",
    ),
    OrderSpec(
        "ORD-5014",
        "C-1007",
        (("P-SPKR-03", 1),),
        "delivered",
        ago(8),
        ago(5),
        None,
        _delivered_shipment(8, 5),
        note="damaged item within window",
    ),
    # --- C-1008 Meera
    OrderSpec(
        "ORD-5015",
        "C-1008",
        (("P-WATCH-04", 1),),
        "delivered",
        ago(22),
        ago(18),
        ReturnSpec("rejected", ago(12), ago(8), "failed", "Charger missing from box"),
        _delivered_shipment(22, 18),
        note="return rejected",
    ),
    OrderSpec(
        "ORD-5016",
        "C-1008",
        (("P-HEAD-01", 1),),
        "delivered",
        ago(29),
        ago(25),
        None,
        _delivered_shipment(29, 25),
        note="damage reported outside window",
    ),
    OrderSpec(
        "ORD-5032",
        "C-1008",
        (("P-PHONE-05", 1),),
        "delivered",
        ago(5),
        ago(1),
        None,
        _delivered_shipment(5, 1),
        note="delivered yesterday",
        method="card",
    ),
    # --- C-1009 Siddharth
    OrderSpec(
        "ORD-5017",
        "C-1009",
        (("P-CHRG-08", 1),),
        "delivered",
        ago(11),
        ago(7),
        ReturnSpec("received", ago(5), ago(1), "passed", "Gets too hot"),
        _delivered_shipment(11, 7),
        note="eligible refund",
    ),
    OrderSpec(
        "ORD-5018",
        "C-1009",
        (("P-KEYB-10", 1),),
        "shipped",
        ago(3),
        None,
        None,
        ShipmentSpec(
            "Delhivery",
            "out_for_delivery",
            ahead(0.3),
            (
                ("label_created", ago(2.9), "Ahmedabad FC"),
                ("in_transit", ago(1.8), "Ahmedabad Hub"),
                ("out_for_delivery", ago(0.1), "Local Delivery Centre"),
            ),
        ),
        note="out for delivery",
    ),
    OrderSpec(
        "ORD-5019",
        "C-1009",
        (("P-MOUS-11", 1),),
        "delivered",
        ago(8),
        ago(4),
        None,
        _delivered_shipment(8, 4),
        note="delivered",
    ),
    OrderSpec(
        "ORD-5030",
        "C-1009",
        (("P-SPKR-03", 1),),
        "delivered",
        ago(16),
        ago(12),
        ReturnSpec("received", ago(7), ago(2), "passed", "Bass too weak"),
        _delivered_shipment(16, 12),
        note="eligible refund",
    ),
    # --- C-1010 Isha
    OrderSpec(
        "ORD-5020",
        "C-1010",
        (("P-HEAD-01", 1),),
        "delivered",
        ago(20),
        ago(16),
        ReturnSpec("received", ago(10), ago(4), "passed", "Uncomfortable"),
        _delivered_shipment(20, 16),
        historic_refund=HistoricRefund("processing", ago(1)),
        note="refund already processing",
    ),
    OrderSpec(
        "ORD-5021",
        "C-1010",
        (("P-CABL-09", 1),),
        "delivered",
        on(2026, 3, 2),
        on(2026, 3, 6),
        ReturnSpec("received", on(2026, 3, 20), on(2026, 3, 25), "passed", "Frayed connector"),
        None,
        note="policy v1 era, eligible under v1",
    ),
    # --- C-1011 Neel
    OrderSpec(
        "ORD-5022",
        "C-1011",
        (("P-KEYB-10", 1),),
        "delivered",
        on(2023, 11, 10),
        on(2023, 11, 14),
        ReturnSpec("received", on(2023, 11, 20), on(2023, 11, 25), "passed", "Faulty switch"),
        None,
        note="predates every policy version",
    ),
    OrderSpec(
        "ORD-5023",
        "C-1011",
        (("P-MON-12", 1),),
        "delivered",
        ago(17),
        ago(13),
        ReturnSpec("received", ago(8), ago(3), "needs_review", "Dead pixels"),
        _delivered_shipment(17, 13),
        note="inspection needs human review",
    ),
    # --- C-1012 Zoya
    OrderSpec(
        "ORD-5024",
        "C-1012",
        (("P-EARB-02", 1),),
        "delivered",
        ago(5),
        ago(2),
        None,
        _delivered_shipment(5, 2),
        note="no return started",
    ),
    OrderSpec(
        "ORD-5025",
        "C-1012",
        (("P-TAB-07", 1),),
        "shipped",
        ago(9),
        None,
        None,
        ShipmentSpec(
            "Ekart",
            "exception",
            ago(3),
            (
                ("label_created", ago(8.8), "Jaipur FC"),
                ("in_transit", ago(7), "Jaipur Hub"),
                ("exception", ago(4), "Package damaged in transit — held at hub"),
            ),
        ),
        note="tracking exception",
    ),
    OrderSpec(
        "ORD-5026",
        "C-1012",
        (("P-CHRG-08", 1),),
        "cancelled",
        ago(6),
        None,
        None,
        None,
        historic_refund=HistoricRefund("succeeded", ago(5)),
        note="cancelled and refunded",
    ),
]


def order_by_id(order_id: str) -> OrderSpec:
    for o in ORDERS:
        if o.id == order_id:
            return o
    raise KeyError(order_id)


def payment_reference(order_id: str) -> str:
    return f"pay_{order_id.replace('-', '').lower()}"


def tracking_number(order_id: str) -> str:
    return f"TRK{order_id.replace('ORD-', '')}IN"


def historic_idempotency_key(order_id: str) -> str:
    return f"historic-refund-{order_id}"


def historic_provider_reference(order_id: str) -> str:
    return f"rf_hist_{order_id.replace('-', '').lower()}"


def default_anchor() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
