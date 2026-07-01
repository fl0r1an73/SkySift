# SkySift

`SkySift` is a lightweight Python bridge that reads local `readsb` JSON files, enriches aircraft data from the local `tar1090` DB, and publishes a clean MQTT + HTTP view focused on actually interesting aircraft.

## Why it exists

- lightweight enough for a Raspberry Pi
- no external API dependency for the core pipeline
- clean MQTT topics for Home Assistant, Glance, or custom consumers
- optional local enrichment from the `tar1090` aircraft DB
- a spotter-oriented `interesting` view instead of "every fresh aircraft is interesting"

## What SkySift does

- reads `/run/readsb/aircraft.json` and `/run/readsb/stats.json`
- normalizes and enriches aircraft state
- publishes per-aircraft MQTT state + events
- publishes a retained `interesting/list`
- optionally writes the same `interesting` snapshot to HTTP-friendly JSON
- supports a local watchlist to force specific aircraft into view

## Topics

- `adsb/status`
- `adsb/aircraft/<HEX>/state`
- `adsb/aircraft/<HEX>/event`
- `adsb/interesting/list`
- `adsb/interesting/<HEX>/event`
- `adsb/stats/receiver`

## Payload notes

`adsb/aircraft/<HEX>/state` contains normalized fields such as:

- `hex`
- `callsign`
- `registration` when found in local `tar1090` DB
- `operator_name` when inferred from callsign prefix
- `altitude_baro`
- `ground_speed_kt`
- `track_deg`
- `lat` / `lon`
- `distance_km` when receiver coordinates are configured
- `category`
- `interesting`
- `interesting_reasons`

`adsb/interesting/list` is a retained summary ready for UI consumption:

- `count`
- `timestamp`
- `aircraft` array with compact entries

Each interesting aircraft may include reasons such as:

- `emergency:7700`
- `military`
- `helicopter`
- `bizjet`
- `rare_iconic`
- `notable_heavy`
- `watchlist:local_heavy`

## Configuration

Copy `config.env.example` to `config.env` and fill the values you need.

Main variables:

- `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASSWORD`
- `MQTT_TOPIC_PREFIX`
- `ADSB_JSON_PATH`, `ADSB_STATS_PATH`
- `ADSB_DB_PATH`
- `WATCHLIST_PATH`
- `PUBLIC_INTERESTING_PATH`
- `INTERESTING_*` filters

## Watchlist

If you want specific aircraft to always bubble up into `adsb/interesting/list`, create a `watchlist.json` file.

Supported match keys:

- `hex`
- `registration`
- `callsign`
- `operator`
- `operator_name`
- `operator_code`
- `aircraft_type`
- `category`

Rules are AND between fields and OR inside each field list.

Example:

```json
{
  "rules": [
    {
      "name": "local_heavy",
      "registration": ["N123AB"]
    },
    {
      "name": "big_ba_airbus",
      "operator_code": ["BAW"],
      "aircraft_type": ["A388"]
    }
  ]
}
```

If a rule matches, the aircraft stays in `interesting` even if it is only interesting because of that watchlist entry.
The file is reloaded automatically when changed.

## V2 spotter heuristics

The default `interesting` selection is no longer the old broad "any fresh airborne aircraft" view.
An aircraft now needs to pass the freshness / position filters and then match at least one spotter-oriented heuristic:

- emergency squawks `7500`, `7600`, `7700`
- military traffic
- helicopters
- bizjets / private aviation
- rare or iconic aircraft
- notable heavy widebodies

Watchlist matches are still forced into `interesting`, even when none of the default V2 heuristics matched.

## Optional HTTP snapshot

If `PUBLIC_INTERESTING_PATH` is set, the bridge also writes the latest `interesting/list` payload to a JSON file.
This is handy for dashboards like Glance that can read HTTP but not MQTT directly.

## Local DB enrichment

If `ADSB_DB_PATH` points to a `tar1090` DB directory such as:

`/usr/local/share/tar1090/html`

the bridge will try to enrich aircraft with:

- registration
- ICAO aircraft type
- aircraft description
- operator info inferred from callsign prefix

No network lookups are required.

## Deploy on Linux / Raspberry Pi

1. Install dependency:

```bash
python3 -m pip install --break-system-packages -r requirements.txt
```

2. Copy the project to `/opt/skysift`
3. Create `/opt/skysift/config.env`
4. Optionally create `/opt/skysift/watchlist.json`
5. Install the systemd unit:

```bash
sudo cp skysift.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now skysift.service
```

6. Check logs:

```bash
journalctl -u skysift -f
```
