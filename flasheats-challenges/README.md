# FlashEats Class Challenges (Classes 5, 6, 7)

In-class challenge work from the FDE course, kept separate from the Divvy project.
The data comes unchanged from the course pack (`manangupta12/flasheats-classroom-pack`).

**Client problem:** late deliveries are rising and customers say the ETA is unreliable.
Figure out what is happening before FlashEats invests in an AI delay predictor.

| Notebook | Class | Challenges |
|:-|:-|:-|
| [FlashEats_Class5_Student.ipynb](FlashEats_Class5_Student.ipynb) | 5: Retrieve data | Size the late problem, test "traffic is the cause", support tickets, reliable API ingestion, driver events |
| [FlashEats_Class6_Student.ipynb](FlashEats_Class6_Student.ipynb) | 6: Validate data | Can we publish "56% late"?, competing late definitions, categories, cross-source integrity, freshness, validation gate |
| [FlashEats_Class7_Challenge.ipynb](FlashEats_Class7_Challenge.ipynb) | 7: Model the workflow | Order timelines, canonical model, interaction to intervention to outcome, metrics, workflow joins, KPI linkage |

## Sources

| Source | Type |
|:-|:-|
| `database/flasheats.db` | SQLite: orders, customers, drivers, restaurants |
| `data/*.csv` | CSV: support tickets, restaurant status, customer actions, interventions, outcomes |
| `data/driver_events.json` | Nested JSON: driver event streams |
| `api/mock_dispatch_api.py` | Paginated REST API (started by the Class 5 notebook) |

## How to run

From the repo root, after the main setup:

```bash
pip install -r flasheats-challenges/api/requirements.txt
cd flasheats-challenges
jupyter notebook
```

Each notebook finds `database/`, `data/` and `api/` in this folder automatically.
