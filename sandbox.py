
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
from robot_framework.process import process

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



orchestrator_connection = OrchestratorConnection("Danfossdata", os.getenv('OpenOrchestratorSQL'),os.getenv('OpenOrchestratorKey'), None, None, None)
process(orchestrator_connection= orchestrator_connection)

