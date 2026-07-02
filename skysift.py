#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import logging
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt


COUNTRY_FLAGS = {
    "Austria": "AT",
    "Belgium": "BE",
    "Canada": "CA",
    "France": "FR",
    "Germany": "DE",
    "Hungary": "HU",
    "Ireland": "IE",
    "Italy": "IT",
    "Morocco": "MA",
    "Netherlands": "NL",
    "Portugal": "PT",
    "Spain": "ES",
    "Switzerland": "CH",
    "Ukraine": "UA",
    "United Arab Emirates": "AE",
    "United Kingdom": "GB",
    "United States": "US",
}

MILITARY_TYPE_CODES = {
    "A400",
    "ATLA",
    "C130",
    "C17",
    "C27J",
    "C295",
    "E3CF",
    "E3TF",
    "K35R",
    "P8",
    "R135",
}

HELICOPTER_TYPE_PREFIXES = (
    "A109",
    "A139",
    "A169",
    "A189",
    "AS3",
    "AS5",
    "B06",
    "B212",
    "B407",
    "B429",
    "B505",
    "B47",
    "EC1",
    "EC2",
    "EC3",
    "EC4",
    "EC5",
    "H47",
    "H60",
    "R44",
    "R66",
    "S76",
)

BIZJET_TYPE_PREFIXES = (
    "BE40",
    "C25",
    "C27",
    "C28",
    "C30",
    "C310",
    "C32",
    "C337",
    "C340",
    "C35",
    "C36",
    "C38",
    "C40",
    "C414",
    "C421",
    "C425",
    "C441",
    "C46",
    "C48",
    "C50",
    "C510",
    "C525",
    "C55",
    "C56",
    "C650",
    "C68",
    "CL30",
    "CL35",
    "CL60",
    "E35",
    "E45",
    "EA50",
    "E550",
    "F2",
    "F50",
    "F900",
    "FA7",
    "FA8",
    "GL5",
    "GL6",
    "H25",
    "HDJT",
    "LJ",
    "PC12",
    "PRM1",
)

RARE_ICONIC_TYPE_CODES = {
    "A124",
    "A225",
    "A342",
    "A343",
    "A345",
    "A346",
    "A388",
    "A3ST",
    "AN12",
    "AN22",
    "AN24",
    "AN26",
    "AN72",
    "B703",
    "B712",
    "B721",
    "B722",
    "B741",
    "B742",
    "B743",
    "B744",
    "B748",
    "B74D",
    "B74R",
    "BLCF",
    "C5M",
    "CONC",
    "DC10",
    "IL76",
    "L101",
    "MD11",
}

NOTABLE_HEAVY_TYPE_CODES = {
    "A345",
    "A346",
    "A388",
    "B742",
    "B743",
    "B744",
    "B748",
    "BLCF",
    "MD11",
}

MILITARY_TEXT_MARKERS = (
    "AIR FORCE",
    "AIRFRCE",
    "AIRFORCE",
    "ARMEE DE L AIR",
    "ARMY",
    "COAST GUARD",
    "DEFENSE",
    "DEFENCE",
    "FRENCH NAVY",
    "GENDARMERIE",
    "LUFTWAFFE",
    "MILITARY",
    "NAVY",
    "NATO",
    "PATROL",
    "RAF",
    "ROYAL AIR FORCE",
    "ROYAL NAVY",
    "US AIR FORCE",
    "US NAVY",
)

MILITARY_CALLSIGN_PREFIXES = (
    "ASCOT",
    "BAF",
    "CTM",
    "FAF",
    "MMF",
    "NATO",
    "RCH",
    "RRR",
)

HELICOPTER_TEXT_MARKERS = (
    "HELICOPTER",
    "HELICOPTERE",
    "ROTORCRAFT",
)

BIZJET_TEXT_MARKERS = (
    "BEECHJET",
    "BUSINESS JET",
    "CHALLENGER",
    "CITATION",
    "FALCON",
    "GLOBAL",
    "GULFSTREAM",
    "HAWKER",
    "LEARJET",
    "PHENOM",
    "PILATUS",
)

RARE_ICONIC_TEXT_MARKERS = (
    "ANTONOV",
    "BELUGA",
    "CONCORDE",
    "DREAMLIFTER",
)

REASON_PRIORITY = {
    "emergency": 2000,
    "watchlist": 1000,
    "military": 500,
    "helicopter": 400,
    "rare_iconic": 300,
    "notable_heavy": 200,
    "bizjet": 100,
}


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    return float(raw)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def env_list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip().upper() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class Config:
    json_path: Path
    stats_path: Path
    mqtt_host: str
    mqtt_port: int
    mqtt_user: str | None
    mqtt_password: str | None
    mqtt_topic_prefix: str
    mqtt_client_id: str
    poll_interval: float
    stale_timeout: float
    db_path: Path | None
    watchlist_path: Path | None
    public_interesting_path: Path | None
    require_position: bool
    max_seen: float
    max_seen_pos: float
    altitude_min: float | None
    altitude_max: float | None
    receiver_lat: float | None
    receiver_lon: float | None
    radius_km: float | None
    callsign_prefixes: list[str]
    categories: list[str]
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        json_path = Path(os.getenv("ADSB_JSON_PATH", "/run/readsb/aircraft.json"))
        stats_path = Path(os.getenv("ADSB_STATS_PATH", "/run/readsb/stats.json"))
        db_raw = os.getenv("ADSB_DB_PATH", "/usr/local/share/tar1090/html").strip()
        return cls(
            json_path=json_path,
            stats_path=stats_path,
            mqtt_host=os.getenv("MQTT_HOST", "127.0.0.1"),
            mqtt_port=env_int("MQTT_PORT", 1883),
            mqtt_user=os.getenv("MQTT_USER") or None,
            mqtt_password=os.getenv("MQTT_PASSWORD") or None,
            mqtt_topic_prefix=os.getenv("MQTT_TOPIC_PREFIX", "adsb").strip("/"),
            mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "skysift"),
            poll_interval=float(os.getenv("POLL_INTERVAL_SEC", "1.0")),
            stale_timeout=float(os.getenv("STALE_TIMEOUT_SEC", "15.0")),
            db_path=Path(db_raw) if db_raw else None,
            watchlist_path=Path(os.getenv("WATCHLIST_PATH", "/opt/skysift/watchlist.json")),
            public_interesting_path=Path(os.getenv("PUBLIC_INTERESTING_PATH", "/opt/skysift/public/interesting.json")),
            require_position=env_bool("INTERESTING_REQUIRE_POSITION", True),
            max_seen=float(os.getenv("INTERESTING_MAX_SEEN_SEC", "15.0")),
            max_seen_pos=float(os.getenv("INTERESTING_MAX_SEEN_POS_SEC", "15.0")),
            altitude_min=env_float("INTERESTING_ALTITUDE_MIN"),
            altitude_max=env_float("INTERESTING_ALTITUDE_MAX"),
            receiver_lat=env_float("RECEIVER_LAT"),
            receiver_lon=env_float("RECEIVER_LON"),
            radius_km=env_float("INTERESTING_RADIUS_KM"),
            callsign_prefixes=env_list("INTERESTING_CALLSIGN_PREFIXES"),
            categories=env_list("INTERESTING_CATEGORIES"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def json_payload(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def is_ground_value(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "ground"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def country_to_flag(country_name: str | None) -> str | None:
    if not country_name:
        return None
    code = COUNTRY_FLAGS.get(country_name)
    if not code:
        return None
    return "".join(chr(127397 + ord(char)) for char in code.upper())


def normalize_text(value: Any) -> str:
    return str(value or "").strip().upper()


def starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def contains_any(value: str, markers: tuple[str, ...]) -> bool:
    return any(marker in value for marker in markers)


class AircraftDb:
    def __init__(self, base_path: Path | None) -> None:
        self.base_path = self._resolve_base_path(base_path)
        self._available_prefixes: list[str] = []
        self._chunk_cache: dict[str, dict[str, Any]] = {}
        self._operators: dict[str, dict[str, Any]] = {}
        if not self.base_path:
            return
        try:
            self._available_prefixes = self._load_gzip_json(self.base_path / "files.js")
            self._operators = self._load_gzip_json(self.base_path / "operators.js")
        except Exception as exc:  # pragma: no cover - defensive init
            logging.warning("Aircraft DB disabled: %s", exc)
            self.base_path = None
            self._available_prefixes = []
            self._operators = {}

    def _resolve_base_path(self, base_path: Path | None) -> Path | None:
        if not base_path:
            return None
        if (base_path / "files.js").exists():
            return base_path
        candidates = sorted(base_path.glob("db-*"))
        for candidate in candidates:
            if (candidate / "files.js").exists():
                return candidate
        return None

    def _load_gzip_json(self, path: Path) -> Any:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)

    def _load_chunk(self, prefix: str) -> dict[str, Any]:
        if prefix not in self._chunk_cache:
            if not self.base_path:
                return {}
            self._chunk_cache[prefix] = self._load_gzip_json(self.base_path / f"{prefix}.js")
        return self._chunk_cache[prefix]

    def _lookup_aircraft_entry(self, hex_code: str) -> list[Any] | None:
        if not hex_code or not self.base_path:
            return None

        icao = hex_code.upper()
        if icao.startswith("~"):
            return None

        level = 1
        while level <= len(icao):
            prefix = icao[:level]
            if prefix not in self._available_prefixes:
                return None

            chunk = self._load_chunk(prefix)
            suffix = icao[level:]
            raw = chunk.get(suffix) or chunk.get(suffix.lower()) or chunk.get(suffix.upper())
            if isinstance(raw, list):
                return raw

            children = chunk.get("children")
            next_prefix = icao[: level + 1]
            if not isinstance(children, list) or next_prefix not in children:
                return None

            level += 1

        return None

    def lookup(self, hex_code: str, callsign: str | None) -> dict[str, Any]:
        if not self.base_path:
            return {}

        info: dict[str, Any] = {}
        raw = self._lookup_aircraft_entry(hex_code)
        if isinstance(raw, list):
            registration, aircraft_type, db_flags, description = (raw + [None, None, None, None])[:4]
            info.update(
                {
                    "registration": registration,
                    "aircraft_type": aircraft_type,
                    "aircraft_description": description,
                    "db_flags": db_flags,
                }
            )

        if callsign:
            operator_code = callsign[:3].upper()
            operator = self._operators.get(operator_code)
            if operator:
                info.update(
                    {
                        "operator_code": operator_code,
                        "operator_name": operator.get("n"),
                        "operator_country": operator.get("c"),
                        "operator_callsign": operator.get("r"),
                    }
                )

        return {key: value for key, value in info.items() if value not in (None, "")}


class Watchlist:
    FIELD_MAP = {
        "hex": "hex",
        "registration": "registration",
        "callsign": "callsign",
        "operator": "operator_name",
        "operator_name": "operator_name",
        "operator_code": "operator_code",
        "aircraft_type": "aircraft_type",
        "category": "category",
    }

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._mtime_ns: int | None = None
        self._rules: list[dict[str, Any]] = []
        self.reload_if_needed(force=True)

    def reload_if_needed(self, *, force: bool = False) -> None:
        if not self.path:
            return
        if not self.path.exists():
            if self._rules:
                logging.info("Watchlist file missing, disabling loaded rules: %s", self.path)
            self._mtime_ns = None
            self._rules = []
            return

        stat = self.path.stat()
        if not force and self._mtime_ns == stat.st_mtime_ns:
            return

        payload = load_json(self.path)
        raw_rules = payload["rules"] if isinstance(payload, dict) else payload
        loaded_rules: list[dict[str, Any]] = []
        for index, raw_rule in enumerate(raw_rules):
            if not isinstance(raw_rule, dict) or raw_rule.get("enabled", True) is False:
                continue
            name = str(raw_rule.get("name") or f"rule_{index + 1}").strip()
            normalized: dict[str, Any] = {"name": name}
            for raw_key, state_key in self.FIELD_MAP.items():
                if raw_key not in raw_rule:
                    continue
                values = raw_rule[raw_key]
                if not isinstance(values, list):
                    values = [values]
                normalized[state_key] = [str(value).strip().upper() for value in values if str(value).strip()]
            if any(key != "name" for key in normalized):
                loaded_rules.append(normalized)

        self._mtime_ns = stat.st_mtime_ns
        self._rules = loaded_rules
        logging.info("Loaded %s watchlist rule(s) from %s", len(self._rules), self.path)

    def match_reasons(self, state: dict[str, Any]) -> list[str]:
        self.reload_if_needed()
        reasons: list[str] = []
        for rule in self._rules:
            matched = True
            for field_name, expected_values in rule.items():
                if field_name == "name":
                    continue
                actual = str(state.get(field_name) or "").strip().upper()
                if actual not in expected_values:
                    matched = False
                    break
            if matched:
                reasons.append(f"watchlist:{rule['name']}")
        return reasons


class Bridge:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.stop_requested = False
        self.db = AircraftDb(config.db_path)
        self.watchlist = Watchlist(config.watchlist_path)
        self.mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=config.mqtt_client_id,
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        if config.mqtt_user:
            self.mqtt_client.username_pw_set(config.mqtt_user, config.mqtt_password)
        self.mqtt_client.will_set(f"{config.mqtt_topic_prefix}/status", payload="offline", retain=True, qos=0)
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_disconnect = self.on_disconnect

        self.last_aircraft_payloads: dict[str, str] = {}
        self.last_present_at: dict[str, float] = {}
        self.last_interesting: dict[str, bool] = {}
        self.last_interesting_list = ""
        self.last_stats_payload = ""

    def request_stop(self, *_args: Any) -> None:
        self.stop_requested = True

    def on_connect(self, _client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        logging.info("MQTT connected: %s", reason_code)

    def on_disconnect(self, _client: mqtt.Client, _userdata: Any, _flags: Any, reason_code: Any, _properties: Any) -> None:
        logging.info("MQTT disconnected: %s", reason_code)

    def topic(self, suffix: str) -> str:
        return f"{self.config.mqtt_topic_prefix}/{suffix}"

    def connect(self) -> None:
        logging.info("Connecting to MQTT %s:%s", self.config.mqtt_host, self.config.mqtt_port)
        self.mqtt_client.connect(self.config.mqtt_host, self.config.mqtt_port, keepalive=30)
        self.mqtt_client.loop_start()
        self.mqtt_client.publish(self.topic("status"), "online", retain=True, qos=0)

    def disconnect(self) -> None:
        try:
            self.mqtt_client.publish(self.topic("status"), "offline", retain=True, qos=0)
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        except Exception:
            pass

    def publish(self, topic: str, payload: str, *, retain: bool) -> None:
        self.mqtt_client.publish(topic, payload, qos=0, retain=retain)

    def publish_json(self, topic: str, payload_obj: Any, *, retain: bool) -> str:
        payload = json_payload(payload_obj)
        self.publish(topic, payload, retain=retain)
        return payload

    def clear_topic(self, topic: str) -> None:
        self.publish(topic, "", retain=True)

    def normalize_aircraft(self, raw: dict[str, Any], now_ts: float) -> dict[str, Any] | None:
        hex_code = str(raw.get("hex", "")).strip().upper()
        if not hex_code:
            return None

        callsign = str(raw.get("flight", "")).strip().upper() or None
        lat = safe_float(raw.get("lat"))
        lon = safe_float(raw.get("lon"))
        seen = float(raw.get("seen", 9999))
        seen_pos = float(raw.get("seen_pos", 9999)) if raw.get("seen_pos") is not None else None
        alt_baro = raw.get("alt_baro")
        alt_value = safe_float(alt_baro)
        ground_speed = safe_float(raw.get("gs"))
        track = safe_float(raw.get("track"))
        distance_km = None
        if (
            lat is not None
            and lon is not None
            and self.config.receiver_lat is not None
            and self.config.receiver_lon is not None
        ):
            distance_km = haversine_km(self.config.receiver_lat, self.config.receiver_lon, lat, lon)

        state: dict[str, Any] = {
            "hex": hex_code,
            "source_type": raw.get("type"),
            "callsign": callsign,
            "registration": None,
            "operator_name": None,
            "operator_code": None,
            "on_ground": bool(raw.get("gnd")) or is_ground_value(alt_baro) or is_ground_value(raw.get("alt_geom")),
            "altitude_baro": alt_value if alt_value is not None else alt_baro,
            "altitude_geom": safe_float(raw.get("alt_geom")),
            "ground_speed_kt": ground_speed,
            "track_deg": track,
            "lat": lat,
            "lon": lon,
            "distance_km": round(distance_km, 2) if distance_km is not None else None,
            "seen_sec": round(seen, 2),
            "seen_pos_sec": round(seen_pos, 2) if seen_pos is not None else None,
            "squawk": raw.get("squawk"),
            "category": raw.get("category"),
            "messages": raw.get("messages"),
            "rssi": safe_float(raw.get("rssi")),
            "timestamp": int(now_ts),
        }

        state.update(self.db.lookup(hex_code, callsign))
        state["operator_country_flag"] = country_to_flag(state.get("operator_country"))
        reasons = self.interesting_reasons(state)
        state["interesting_reasons"] = reasons
        state["interesting_score"] = self.interesting_score(reasons)
        state["interesting"] = bool(reasons)
        return {key: value for key, value in state.items() if value is not None}

    def passes_freshness(self, state: dict[str, Any]) -> bool:
        if float(state.get("seen_sec", 9999)) > self.config.max_seen:
            return False
        return True

    def passes_base_rules(self, state: dict[str, Any]) -> bool:
        if not self.passes_freshness(state):
            return False
        if state.get("on_ground"):
            return False
        if self.config.require_position and (state.get("lat") is None or state.get("lon") is None):
            return False
        seen_pos = state.get("seen_pos_sec")
        if self.config.require_position and seen_pos is None:
            return False
        if self.config.require_position and seen_pos is not None and float(seen_pos) > self.config.max_seen_pos:
            return False

        altitude = state.get("altitude_baro")
        if isinstance(altitude, (int, float)):
            if self.config.altitude_min is not None and altitude < self.config.altitude_min:
                return False
            if self.config.altitude_max is not None and altitude > self.config.altitude_max:
                return False

        distance_km = state.get("distance_km")
        if self.config.radius_km is not None:
            if distance_km is None or float(distance_km) > self.config.radius_km:
                return False

        if self.config.callsign_prefixes:
            callsign = str(state.get("callsign") or "")
            if not any(callsign.startswith(prefix) for prefix in self.config.callsign_prefixes):
                return False

        if self.config.categories:
            category = str(state.get("category") or "").upper()
            if category not in self.config.categories:
                return False

        return True

    def classify_interesting_reasons(self, state: dict[str, Any]) -> list[str]:
        if not self.passes_base_rules(state):
            return []

        aircraft_type = normalize_text(state.get("aircraft_type"))
        description = normalize_text(state.get("aircraft_description"))
        operator_name = normalize_text(state.get("operator_name"))
        operator_callsign = normalize_text(state.get("operator_callsign"))
        callsign = normalize_text(state.get("callsign"))
        category = normalize_text(state.get("category"))
        searchable_text = " ".join(
            value for value in (description, operator_name, operator_callsign) if value
        )
        helicopter_text = " ".join(value for value in (description,) if value)

        reasons: list[str] = []

        squawk = str(state.get("squawk") or "").strip()
        if squawk in {"7500", "7600", "7700"}:
            reasons.append(f"emergency:{squawk}")

        is_military = (
            aircraft_type in MILITARY_TYPE_CODES
            or contains_any(searchable_text, MILITARY_TEXT_MARKERS)
            or starts_with_any(callsign, MILITARY_CALLSIGN_PREFIXES)
        )
        if is_military:
            reasons.append("military")

        is_helicopter = (
            category == "A7"
            or starts_with_any(aircraft_type, HELICOPTER_TYPE_PREFIXES)
            or contains_any(helicopter_text, HELICOPTER_TEXT_MARKERS)
        )
        if is_helicopter:
            reasons.append("helicopter")

        is_bizjet = (
            starts_with_any(aircraft_type, BIZJET_TYPE_PREFIXES)
            or contains_any(searchable_text, BIZJET_TEXT_MARKERS)
        )
        if is_bizjet and not is_military:
            reasons.append("bizjet")

        is_rare_iconic = (
            aircraft_type in RARE_ICONIC_TYPE_CODES
            or contains_any(searchable_text, RARE_ICONIC_TEXT_MARKERS)
        )
        if is_rare_iconic:
            reasons.append("rare_iconic")

        if aircraft_type in NOTABLE_HEAVY_TYPE_CODES:
            reasons.append("notable_heavy")

        return reasons

    def interesting_score(self, reasons: list[str]) -> int:
        score = 0
        for reason in reasons:
            base_reason = str(reason).split(":", 1)[0]
            score += REASON_PRIORITY.get(base_reason, 0)
        return score

    def interesting_reasons(self, state: dict[str, Any]) -> list[str]:
        reasons = self.classify_interesting_reasons(state)
        if self.passes_freshness(state):
            reasons.extend(self.watchlist.match_reasons(state))
        return reasons

    def publish_event(self, topic: str, event_type: str, state: dict[str, Any]) -> None:
        payload = {
            "event": event_type,
            "hex": state["hex"],
            "callsign": state.get("callsign"),
            "interesting": state.get("interesting", False),
            "timestamp": int(time.time()),
        }
        if "registration" in state:
            payload["registration"] = state["registration"]
        if "operator_name" in state:
            payload["operator_name"] = state["operator_name"]
        if "interesting_reasons" in state:
            payload["interesting_reasons"] = state["interesting_reasons"]
        self.publish_json(topic, payload, retain=False)

    def process_aircraft(self, now_ts: float) -> list[dict[str, Any]]:
        data = load_json(self.config.json_path)
        interesting_list: list[dict[str, Any]] = []
        current_hexes: set[str] = set()

        for raw_aircraft in data.get("aircraft", []):
            state = self.normalize_aircraft(raw_aircraft, now_ts)
            if not state:
                continue

            hex_code = state["hex"]
            current_hexes.add(hex_code)
            self.last_present_at[hex_code] = now_ts

            topic = self.topic(f"aircraft/{hex_code}/state")
            payload = json_payload(state)
            if self.last_aircraft_payloads.get(hex_code) != payload:
                self.publish(topic, payload, retain=True)
                if hex_code not in self.last_aircraft_payloads:
                    self.publish_event(self.topic(f"aircraft/{hex_code}/event"), "enter", state)
                self.last_aircraft_payloads[hex_code] = payload

            previous_interesting = self.last_interesting.get(hex_code)
            current_interesting = bool(state.get("interesting"))
            if previous_interesting is None or previous_interesting != current_interesting:
                event_name = "interesting_enter" if current_interesting else "interesting_exit"
                self.publish_event(self.topic(f"interesting/{hex_code}/event"), event_name, state)
            self.last_interesting[hex_code] = current_interesting

            if current_interesting:
                interesting_list.append(
                    {
                        "hex": state["hex"],
                        "callsign": state.get("callsign"),
                        "registration": state.get("registration"),
                        "operator_name": state.get("operator_name"),
                        "operator_country": state.get("operator_country"),
                        "operator_country_flag": state.get("operator_country_flag"),
                        "aircraft_type": state.get("aircraft_type"),
                        "aircraft_description": state.get("aircraft_description"),
                        "altitude_baro": state.get("altitude_baro"),
                        "ground_speed_kt": state.get("ground_speed_kt"),
                        "distance_km": state.get("distance_km"),
                        "category": state.get("category"),
                        "interesting_reasons": state.get("interesting_reasons", []),
                        "interesting_score": state.get("interesting_score", 0),
                    }
                )

        stale_before = now_ts - self.config.stale_timeout
        stale_hexes = [
            hex_code
            for hex_code, last_present in self.last_present_at.items()
            if hex_code not in current_hexes and last_present < stale_before
        ]
        for hex_code in stale_hexes:
            previous_state = self.last_aircraft_payloads.get(hex_code)
            if previous_state:
                state = json.loads(previous_state)
                self.publish_event(self.topic(f"aircraft/{hex_code}/event"), "exit", state)
                if self.last_interesting.get(hex_code):
                    self.publish_event(self.topic(f"interesting/{hex_code}/event"), "interesting_exit", state)
                self.clear_topic(self.topic(f"aircraft/{hex_code}/state"))
            self.last_aircraft_payloads.pop(hex_code, None)
            self.last_present_at.pop(hex_code, None)
            self.last_interesting.pop(hex_code, None)

        interesting_list.sort(
            key=lambda item: (
                not any(str(reason).startswith("watchlist:") for reason in item.get("interesting_reasons", [])),
                -(item.get("interesting_score") or 0),
                item.get("distance_km") is None,
                item.get("distance_km") if item.get("distance_km") is not None else 999999,
                item["hex"],
            )
        )
        return interesting_list

    def publish_interesting_list(self, interesting_list: list[dict[str, Any]]) -> None:
        payload_obj = {
            "count": len(interesting_list),
            "timestamp": int(time.time()),
            "aircraft": interesting_list,
        }
        payload = json_payload(payload_obj)
        if payload != self.last_interesting_list:
            self.publish(self.topic("interesting/list"), payload, retain=True)
            if self.config.public_interesting_path:
                write_text(self.config.public_interesting_path, payload + "\n")
            self.last_interesting_list = payload

    def publish_stats(self) -> None:
        stats = load_json(self.config.stats_path)
        payload_obj = {
            "timestamp": int(stats.get("now", time.time())),
            "gain_db": stats.get("gain_db"),
            "estimated_ppm": stats.get("estimated_ppm"),
            "aircraft_with_pos": stats.get("aircraft_with_pos"),
            "aircraft_without_pos": stats.get("aircraft_without_pos"),
            "aircraft_count_by_type": stats.get("aircraft_count_by_type"),
            "last1min": {
                "messages_valid": stats.get("last1min", {}).get("messages_valid"),
                "tracks_all": stats.get("last1min", {}).get("tracks", {}).get("all"),
                "signal": stats.get("last1min", {}).get("local", {}).get("signal"),
                "noise": stats.get("last1min", {}).get("local", {}).get("noise"),
                "strong_signals": stats.get("last1min", {}).get("local", {}).get("strong_signals"),
            },
        }
        payload = json_payload(payload_obj)
        if payload != self.last_stats_payload:
            self.publish(self.topic("stats/receiver"), payload, retain=True)
            self.last_stats_payload = payload

    def run(self) -> int:
        self.connect()
        while not self.stop_requested:
            loop_started = time.time()
            try:
                interesting_list = self.process_aircraft(loop_started)
                self.publish_interesting_list(interesting_list)
                self.publish_stats()
            except FileNotFoundError as exc:
                logging.error("Missing file: %s", exc)
            except json.JSONDecodeError as exc:
                logging.warning("Skipping malformed JSON: %s", exc)
            except Exception as exc:  # pragma: no cover - runtime safety
                logging.exception("Unexpected bridge error: %s", exc)

            elapsed = time.time() - loop_started
            sleep_for = max(0.1, self.config.poll_interval - elapsed)
            time.sleep(sleep_for)
        self.disconnect()
        return 0


def main() -> int:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    bridge = Bridge(config)
    signal.signal(signal.SIGINT, bridge.request_stop)
    signal.signal(signal.SIGTERM, bridge.request_stop)
    return bridge.run()


if __name__ == "__main__":
    sys.exit(main())
