"""Metro fare calculator for a fictional public transit network.

This module demonstrates fare calculation, smart-card operations, route choice,
ticket checksums, safe XML parsing, and validation for gate-upload metadata.
"""

import hashlib
import math
import os
import re
from typing import Iterable, Optional, Sequence, Tuple

from defusedxml import ElementTree
import paramiko

DISTANCE_SLABS = ((2, 10), (5, 20), (12, 30), (21, 40), (32, 50))
MAX_FARE = 60.0
DAILY_CAP = 150.0
OFF_PEAK_DISCOUNT = 0.10
CONCESSIONS = {"student": 0.25, "senior": 0.50}

# Each card stores a balance and a list of charged journeys.
cards = {}


def base_of(km: float) -> float:
    """Return the standard fare for the given distance."""
    return fare_for_distance(km)


def fare_for_distance(km: float) -> float:
    """Return the fare band for a journey length in kilometres."""
    if not isinstance(km, (int, float)) or isinstance(km, bool):
        raise TypeError("Distance must be a number")
    if not math.isfinite(km) or km < 0:
        raise ValueError("Distance must be finite and non-negative")
    for limit, fare in DISTANCE_SLABS:
        if km < limit:
            return float(fare)
    return MAX_FARE


def is_peak(time_hhmm: str) -> bool:
    """Return True when a trip takes place in the busy period."""
    if not isinstance(time_hhmm, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_hhmm):
        raise ValueError("Time must be in HH:MM 24-hour format")
    hour = int(time_hhmm.split(":", maxsplit=1)[0])
    return 8 <= hour <= 10 or 17 <= hour <= 20


def journey_fare(km: float, time_hhmm: str, categories: Optional[Sequence[str]] = None) -> float:
    """Calculate the fare for a single journey."""
    fare = base_of(km)
    if not is_peak(time_hhmm):
        fare *= 1 - OFF_PEAK_DISCOUNT
    for category in categories or ():
        discount = CONCESSIONS.get(str(category).strip().lower())
        if discount is None:
            raise ValueError(f"Unknown concession category: {category}")
        fare *= 1 - discount
    return round(fare, 2)


def daily_total(journeys: Iterable[Tuple[float, str, Sequence[str]]]) -> float:
    """Return the day's total with the daily fare cap applied."""
    total = sum(journey_fare(km, time_hhmm, categories)
                for km, time_hhmm, categories in journeys)
    return round(min(total, DAILY_CAP), 2)


def issue_card(card_id: str, balance: float = 0.0) -> dict:
    """Create a new smart card or reset an existing one."""
    if not isinstance(card_id, str) or not card_id.strip():
        raise ValueError("Card ID must be a non-empty string")
    if not isinstance(balance, (int, float)) or isinstance(balance, bool):
        raise TypeError("Balance must be numeric")
    if not math.isfinite(balance) or balance < 0:
        raise ValueError("Balance must be finite and non-negative")
    cards[card_id] = {"balance": round(float(balance), 2), "trips": []}
    return cards[card_id]


def tap_out(card_id: str, km: float, time_hhmm: str,
            categories: Optional[Sequence[str]] = None) -> float:
    """Charge a trip to the card and record the trip."""
    if card_id not in cards:
        raise KeyError(f"Unknown card: {card_id}")
    fare = journey_fare(km, time_hhmm, categories)
    cards[card_id]["balance"] = round(cards[card_id]["balance"] - fare, 2)
    cards[card_id]["trips"].append((km, time_hhmm, fare))
    return cards[card_id]["balance"]


def top_up(card_id: str, amount: float) -> float:
    """Add credit to an existing card."""
    if card_id not in cards:
        raise KeyError(f"Unknown card: {card_id}")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        raise TypeError("Top-up amount must be numeric")
    if not math.isfinite(amount) or amount <= 0:
        raise ValueError("Top-up amount must be finite and positive")
    cards[card_id]["balance"] = round(cards[card_id]["balance"] + amount, 2)
    return cards[card_id]["balance"]


def cheapest_route(routes: Sequence[float]) -> float:
    """Return the route distance with the lowest fare."""
    if not routes:
        raise ValueError("No routes were supplied")
    return min(routes, key=base_of)


def ticket_checksum(ticket_text: str) -> str:
    """Create a SHA-256 checksum for the ticket text."""
    return hashlib.sha256(ticket_text.encode("utf-8")).hexdigest()


class StopHandler:
    """Collect valid stop names from parsed XML."""

    def __init__(self) -> None:
        self.stops = []

    def add_stop(self, stop_name: Optional[str]) -> None:
        """Store a non-empty stop name."""
        if stop_name and stop_name.strip():
            self.stops.append(stop_name.strip())


def load_stops(xml_text: str) -> list:
    """Parse stop names using a hardened XML parser."""
    root = ElementTree.fromstring(xml_text)
    handler = StopHandler()
    for stop in root.findall(".//stop"):
        handler.add_stop(stop.get("name"))
    return handler.stops


def push_fares_to_gate(gate_host: str, fare_file: str) -> dict:
    """Validate gate-upload metadata and enforce strict SSH host checking.

    This function intentionally prepares and validates metadata only. It does
    not execute a remote command or transfer a file.
    """
    if not isinstance(gate_host, str) or not gate_host.strip():
        raise ValueError("Gate host is required")
    if not isinstance(fare_file, str) or not fare_file.strip():
        raise ValueError("Fare file is required")

    host = gate_host.strip()
    if host.startswith("ssh://"):
        host = host[len("ssh://"):]
    if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", host):
        raise ValueError("Gate host contains unsupported characters")
    if ".." in host or host.startswith(".") or host.endswith("."):
        raise ValueError("Invalid gate hostname")

    safe_file = os.path.basename(fare_file.strip())
    if safe_file in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", safe_file):
        raise ValueError("Fare filename contains unsupported characters")

    # If a connection is added later, use known_hosts and reject unknown keys.
    # No network connection is opened in this lab implementation.
    _ = paramiko.RejectPolicy
    return {"host": host, "file": safe_file, "status": "validated"}


def main() -> None:
    """Demonstrate the fare calculator behaviour."""
    print("Fare for exactly 2 km:", fare_for_distance(2))
    print("Fare for exactly 32 km:", fare_for_distance(32))
    print("Is 20:45 peak?", is_peak("20:45"))
    print("Student + senior, 10 km off-peak:",
          journey_fare(10, "14:00", ["student", "senior"]))
    day = [
        (30, "08:15", []),
        (30, "18:30", []),
        (40, "21:00", []),
        (35, "22:30", []),
    ]
    print("Daily total (cap 150):", daily_total(day))
    issue_card("C1", 25)
    print("Balance after a 30 km trip with Rs.25:", tap_out("C1", 30, "09:00"))
    issue_card("C2", 0)
    print("Top up 0.1 then 0.2:", top_up("C2", 0.1), top_up("C2", 0.2))
    print("Cheapest route among 12, 5, 21 km:", cheapest_route([12, 5, 21]))
    print("Ticket checksum:", ticket_checksum("C1|30km|09:00"))
    print("Stops:", load_stops("<line><stop name='Central'/><stop name='Market'/></line>"))


if __name__ == "__main__":
    main()
