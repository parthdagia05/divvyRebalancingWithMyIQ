# FlashEats Class Challenges (Classes 5, 6, 7)

My in-class challenge work. Data is from the course pack (`manangupta12/flasheats-classroom-pack`).

**Problem:** late deliveries are rising and the ETA is unreliable. Find out why before building an AI delay predictor.

**My main decision:** late = more than 10 min after the promised ETA. Cancelled orders and orders with no delivery time are left out but counted.

| Notebook | Main finding |
|:-|:-|
| [Class 5](FlashEats_Class5_Student.ipynb) | 23.3% late. Late orders lose 17 min before pickup vs 4 min on the road, so traffic is not the main cause. Don't build the AI predictor yet. |
| [Class 6](FlashEats_Class6_Student.ipynb) | "56% late" counts any delay and has no owner. Same data gives 23.3% to 56.4%. Don't publish 56% until VP Ops signs one definition. |
| [Class 7](FlashEats_Class7_Challenge.ipynb) | Pickup overrun predicts lateness (0% late within 5 min, 97% at 20+ min). 74% of frustrated customers got no intervention. |

## How to run

```bash
pip install -r flasheats-challenges/api/requirements.txt
cd flasheats-challenges
jupyter notebook
```
