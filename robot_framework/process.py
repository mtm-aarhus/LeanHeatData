"""This module contains the main process of the robot.

Fetches yesterday's series data from Leanheat Monitor, reduces it to mean values per interval
and appends it to a CSV file on SharePoint.
"""

import csv
import os
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from OpenOrchestrator.orchestrator_connection.connection import OrchestratorConnection
from OpenOrchestrator.database.queues import QueueElement

from robot_framework.exceptions import BusinessError
from robot_framework.leanheat_api import LeanheatClient
from robot_framework.sharepoint import sharepoint_client, download_file, upload_file

CSV_COLUMNS = ["dato", "device_id", "device_name", "subject", "interval_start_utc", "interval_start_lokal", "vaerdi", "antal_punkter"]

# Days are cut at local midnight
LOCAL_TZ = ZoneInfo("Europe/Copenhagen")

# Limits on /devices/data/series/q per request (from the API documentation)
MAX_DEVICES_PER_CALL = 8
MAX_SUBJECTS_PER_CALL = 10

# How many missed days the robot catches up on in one run
MAX_CATCHUP_DAYS = 7

# Raw data is typically every 10 minutes. Each row in the CSV is the mean over this interval.
INTERVAL_MINUTES = 30


# pylint: disable-next=unused-argument
def process(orchestrator_connection: OrchestratorConnection, queue_element: QueueElement | None = None) -> None:
    """Do the primary process of the robot."""
    orchestrator_connection.log_trace("Running process.")
    leanheat = orchestrator_connection.get_credential("DanfossAPINøgle")
    api_key = leanheat.password     
    api_url = leanheat.username
    api = orchestrator_connection.get_credential("SharePointAPI")
    certification = orchestrator_connection.get_credential("SharePointCert")
    tenant = api.username
    client_id = api.password
    thumbprint = certification.username
    cert_path = certification.password

    sharepoint_site_url = orchestrator_connection.get_constant("PowerBiSharepoint").value
    folder_path = "Delte dokumenter/Ekstern adgang/Danfoss Data"
    file_name = "leanheat_maaledata.csv"

    # The measurements to fetch for every device. Run tools/list_subjects.py to see which exist.
    subjects = [
        "Outdoor_Temperature",
        "Wind_Speed",
        "Supply_Temperature",
        # Heating circuit 1
        "HC1_FlowTemperature",
        "HC1_FlowTemperature_Ref",
        "HC1_ReturnTemperature",
        "HC1_ReturnTemperature_Ref",
        "HC1_RoomTemperature",
        "HC1_RoomTemperature_Ref",
        # Heating circuit 2
        "HC2_FlowTemperature",
        "HC2_FlowTemperature_Ref",
        "HC2_ReturnTemperature",
        "HC2_ReturnTemperature_Ref",
        "HC2_RoomTemperature",
        "HC2_RoomTemperature_Ref",
        # Domestic hot water
        "DHW1_Lower_Temperature",
        "DHW1_Upper_Temperature",
        "DHW1_Upper_Temperature_Ref",
        "DHW3_FlowTemperature",
        "DHW3_FlowTemperature_Ref",
        "DHW3_ReturnTemperature",
        "DHW3_ReturnTemperature_Ref",
        # Energy meter
        "EM12_Flow_Temperature",
        "EM12_Return_Temperature",
        "EM12_Temperature_Difference",
        "EM12_Flow",
        "EM12_Power",
        # Valves
        "Valve_Position_V1",
        "Valve_Position_V2",
        "Valve_Position_V3",
    ]

    client = sharepoint_client(tenant, client_id, thumbprint, cert_path, sharepoint_site_url, orchestrator_connection)
    local_path = os.path.join(tempfile.gettempdir(), file_name)

    file_exists = download_file(client, f"{folder_path}/{file_name}", local_path)
    last_date = find_last_date(local_path) if file_exists else None
    days = days_to_fetch(last_date, datetime.now(LOCAL_TZ).date())
    today = datetime.now(LOCAL_TZ).date()
    days = days_to_fetch(last_date, today)
    orchestrator_connection.log_info(f"DEBUG: today={today}, last_date={last_date}, days={days}")

    if not days:
        orchestrator_connection.log_info(f"Filen indeholder allerede data til og med {last_date}. Intet at hente.")
        os.remove(local_path)
        return

    orchestrator_connection.log_info(f"Henter data for {days[0]} til {days[-1]}. Filen findes{'' if file_exists else ' ikke og oprettes'}.")

    if not file_exists:
        with open(local_path, "w", newline="", encoding="utf-8-sig") as file:
            csv.writer(file, delimiter=";").writerow(CSV_COLUMNS)

    leanheat = LeanheatClient(api_key, api_url)
    devices = select_devices(leanheat.get_devices(), day_bounds(days[0])[0])
    if not devices:
        raise BusinessError("API'et returnerede ingen tilknyttede enheder.")
    orchestrator_connection.log_info(f"Antal enheder: {len(devices)}. Antal subjects: {len(subjects)}.")

    with open(local_path, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter=";")
        for day in days:
            rows = fetch_day(leanheat, devices, subjects, day)
            writer.writerows(rows)
            orchestrator_connection.log_info(f"{day}: {len(rows)} rækker tilføjet.")

    upload_file(client, folder_path, local_path, file_name, orchestrator_connection)
    orchestrator_connection.log_info(f"Filen er uploadet til {folder_path}/{file_name}.")
    os.remove(local_path)


def find_last_date(csv_path: str) -> date | None:
    """Find the latest date in the 'dato' column of an existing CSV file."""
    last = None
    with open(csv_path, newline="", encoding="utf-8-sig") as file:
        reader = csv.reader(file, delimiter=";")
        next(reader, None)
        for row in reader:
            if row and (last is None or row[0] > last):
                last = row[0]
    return date.fromisoformat(last) if last else None


def days_to_fetch(last_date: date | None, today: date) -> list[date]:
    """The days after last_date up to and including yesterday, at most MAX_CATCHUP_DAYS days."""
    yesterday = today - timedelta(days=1)
    start = last_date + timedelta(days=1) if last_date else yesterday
    start = max(start, yesterday - timedelta(days=MAX_CATCHUP_DAYS - 1))
    return [start + timedelta(days=i) for i in range((yesterday - start).days + 1)]


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Local midnight to next local midnight, in UTC. Handles daylight saving time."""
    start = datetime.combine(day, time.min, LOCAL_TZ)
    end = datetime.combine(day + timedelta(days=1), time.min, LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def select_devices(devices: list[dict], period_start: datetime) -> dict[str, str]:
    """Select the devices that were connected in the period.

    Returns:
        A dict of device id -> device name.
    """
    selected = {}
    for device in devices:
        device_id = str(device.get("id", ""))
        if not device_id:
            continue
        disconnected_at = device.get("disconnected_at")
        if disconnected_at and datetime.fromisoformat(disconnected_at) < period_start:
            continue
        selected[device_id] = device.get("name", "")
    return selected


def chunks(items: list, size: int) -> list[list]:
    """Split a list into chunks of at most size items."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def fetch_day(leanheat: LeanheatClient, devices: dict[str, str], subjects: list[str], day: date) -> list[list]:
    """Fetch series data for one day for all devices and subjects, and reduce it to CSV rows."""
    from_utc, to_utc = day_bounds(day)
    rows = []
    for device_batch in chunks(list(devices), MAX_DEVICES_PER_CALL):
        for subject_batch in chunks(subjects, MAX_SUBJECTS_PER_CALL):
            data = leanheat.get_series(device_batch, subject_batch, from_utc, to_utc)
            for device_id, subject, interval_start, value, count in aggregate_series(data, from_utc, to_utc):
                rows.append([
                    day.isoformat(),
                    device_id,
                    devices.get(device_id, ""),
                    subject,
                    interval_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    interval_start.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M"),
                    value,
                    count,
                ])
    return rows


def aggregate_series(data: dict, from_utc: datetime, to_utc: datetime) -> list[tuple]:
    """Reduce series data to one value per device, subject and interval of INTERVAL_MINUTES.
    Numeric values are averaged. Non-numeric values (booleans, text) use the last value in the interval.

    Args:
        data: The 'data' object from the series response: {"subjects": ["ts", ...], "values": {device_id: [[ts_ms, v1, ...], ...]}}

    Returns:
        A sorted list of (device_id, subject, interval_start_utc, value, number_of_points).
    """
    subjects = data["subjects"][1:]
    interval_ms = INTERVAL_MINUTES * 60 * 1000
    from_ms = int(from_utc.timestamp() * 1000)
    to_ms = int(to_utc.timestamp() * 1000)

    buckets = defaultdict(list)
    for device_id, points in data["values"].items():
        # A device with no data at all comes back as a single timestamped array of nulls
        if points and not isinstance(points[0], list):
            points = [points]
        for point in sorted(points, key=lambda p: p[0] or 0):
            ts = point[0]
            if not isinstance(ts, (int, float)) or not from_ms <= ts < to_ms:
                continue
            interval_start = int(ts // interval_ms * interval_ms)
            for subject, value in zip(subjects, point[1:]):
                if value is not None:
                    buckets[(device_id, subject, interval_start)].append(value)

    result = []
    for (device_id, subject, interval_start), values in sorted(buckets.items()):
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        value = round(sum(numbers) / len(numbers), 3) if numbers else values[-1]
        result.append((
            device_id,
            subject,
            datetime.fromtimestamp(interval_start / 1000, timezone.utc),
            value,
            len(numbers) if numbers else len(values),
        ))
    return result
