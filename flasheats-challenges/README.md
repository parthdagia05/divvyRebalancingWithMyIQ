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

## What we concluded

**Our decisions (used in all three classes):** late means more than 10 min past the promised ETA.
Cancelled orders and orders with no delivery time are left out of the rate but counted.
Duplicate orders are dropped (first copy kept). Only case and spacing fixes are applied to categories.

| Class | Main finding |
|:-|:-|
| **5** | **23.3% of orders are late** (349 of 1,495). Late orders lose **+17 min before pickup** but only +4 min on the road, so traffic is not the main cause. Half of all complaints are about the pickup stage. The API failed twice (500, 429); both were retried and all 1,600 records verified. There is no "driver arrived" event. **Do not build the AI predictor yet.** |
| **6** | "56% late" is correct arithmetic but uses the strictest definition (any delay), with no owner. The same data gives 23.3% to 56.4%. Restaurant status covers only 31% of orders and is stale. **Do not publish 56%** until the VP Operations signs one definition. |
| **7** | An order-centred model (customer, order, interaction, intervention, outcome). **Pickup overrun predicts lateness almost perfectly** (0% late within 5 min, 97% late at 20+ min). 74% of frustrated customers got no intervention, and interventions show no measurable benefit. The intervention log disagrees with dispatch (8 of 155 reassignments match). |

**Data traps found:** 3 conflicting duplicate orders, 4 ETAs before the order existed, 5 deliveries before pickup,
806 orders with a default 18 km distance, 163 corrupt GPS pings, and `order_outcomes.late_flag` using the 56% rule.

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
