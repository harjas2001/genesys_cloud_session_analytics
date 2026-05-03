# Genesys Cloud — Session Analytics

A data pipeline for transforming raw data from chat and voice channels exports into structured, PowerBI-ready analytics. Built from production work on enterprise-scale conversational AI deployments.

Processes session data to classify bot vs non-bot conversations, determine containment and escalation, identify exactly where customers abandon the info-collecting bot flow, and output week-over-week metrics for trend reporting.

---

## Background

Built to solve a specific observability gap: standard contact centre reporting shows overall containment and abandonment rates, but gives no visibility into *where within the bot flow* customers are dropping off. A 15% abandonment rate in the identity verification flow is not actionable — knowing that 70% of those abandonments happen at the date-of-birth question is.

This pipeline was run against weekly session exports from enterprise conversational AI deployments, producing structured CSVs appended to a master dataset consumed by a PowerBI dashboard. The week-ending column enables PowerBI to group metrics by reporting week without any additional transformation.

The abandonment question mapping (`config/question_map.json`) converts raw platform attribute values into business-readable labels — separating the analytics logic from brand-specific configuration so the same pipeline works across different agents and deployments.

---

## Pipeline

```
Raw session export (CSV)
        │
        ▼
process_session_data.py       — classify sessions, extract metrics, standardise dates
        │
        ▼
session_metrics.csv           — structured output → PowerBI / BigQuery
        │
        ├── abandonment_metrics.py   — ranked abandonment question frequency table
        └── analyse_segment_abandonment.py — segment-specific drop-off analysis
```

---

## Scripts

### `process_session_data.py`
Core pipeline. Reads the raw session export and produces a structured CSV with one row per conversation.

```bash
# Using .env config
python process_session_data.py

# Or pass files directly
python process_session_data.py data/sessions.csv output/metrics.csv
```

Key output columns: `is_bot_chat`, `is_contained`, `is_escalated`, `proceeded_to_info_bot_both`, `abandoned_in_info_bot`, `abandoned_I_bot_question`, `week_ending`, `is_return_chat`

---

### `abandonment_metrics.py`
Reads the structured output and produces a ranked frequency table of abandonment points — which question drives the most drop-off.

```bash
python abandonment_metrics.py
```

---

### `analyse_segment_abandonment.py`
Segment-specific abandonment analysis. Filters to a specific customer cohort (e.g. new customers) and shows where they abandon within the bot flow.

```bash
python analyse_segment_abandonment.py
```

Configure `SEGMENT_STEP_VALUES` in the script to define which attribute values identify your target segment.

---

## Setup

```bash
git clone https://github.com/your-username/conversational-ai-session-analytics.git
cd conversational-ai-session-analytics

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your paths, bot intent prefix, and GCP config

cp config/bot_flows.example.txt config/bot_flows.txt
# Add your info-collecting bot flow names

cp config/question_map.example.json config/question_map.json
# Map your platform's last-successful-step values to question labels
```

---

## Private Configuration

Two files are gitignored and must be created locally:

**`config/bot_flows.txt`** — the flow names that identify your info-collecting bot. Any session that passes through one of these flows is flagged as `proceeded_to_info_bot`.

**`config/question_map.json`** — maps the raw attribute value (last successful step in the bot flow) to the human-readable question the customer failed on next. Example:

```json
{
  "none":    "existing customer question",
  "name":    "date of birth",
  "dob":     "phone number (existing)",
  "step_x":  "some other question",
  "_default": "different question",
  "_missing": "missing value"
}
```

This design keeps all brand/platform-specific logic out of the codebase.

---

## GCP Integration

```python
# Download this week's export from shared bucket
from gcp.storage_utils import download_input
download_input("sessions_week_ending_04052026.csv", local_path="data/sessions.csv")

# Process
import subprocess
subprocess.run(["python", "process_session_data.py"])

# Push results to BigQuery
import pandas as pd
from gcp.bigquery_utils import push_session_metrics, push_abandonment_metrics
push_session_metrics(pd.read_csv("output/session_metrics.csv"))

# Upload output back to GCS for PowerBI Direct Query
from gcp.storage_utils import upload_output
upload_output("output/session_metrics.csv")
```

---

## PowerBI Notes

- `week_ending` (DD/MM/YYYY) is the primary grouping key — no transformation needed in Power Query
- All dates are pre-standardised to ISO format (`YYYY-MM-DD HH:MM:SS`) to prevent Power Query auto-detection issues
- All boolean columns are serialised as strings (`"True"` / `"False"`) to prevent type conflicts on import
- The output CSV is designed to be appended week-over-week — PowerBI reads the full file and slices by `week_ending`

---

## Stack

Python · pandas · python-dotenv · google-cloud-storage · google-cloud-bigquery
