# Pantry pack: how to use it

The `pantry` table records what's at home, one row per item. On the board: expiring soon, running low, in the pantry, snap a receipt / add an item / shopping list, grocery spend, groceries by week.

```bash
B="python3 ~/.openmousse/repo/server/board_ctl.py"   # run it inside your own workspace, no --agent needed
$B rows query pantry --where 'qty>0' --sort category --limit 100    # what's at home now
```

## Fields

| Field | What goes in |
|---|---|
| name | What it is, in plain words ("Chicken breast mini fillets", "Greek yoghurt 0%"); expand receipt abbreviations |
| qty / unit | How many and of what: by pack (2 packs, 6 eggs, 1 L); loose food by weight (800 g) |
| category | Meat & eggs / Dairy / Fruit & veg / Staples / Snacks / Drinks / Condiments / Other |
| price | What this line cost (the receipt price, after discounts) |
| bought / expires | Receipt date / use-by. No date on the receipt: estimate (fresh meat and fish 2–3 days, milk and yoghurt 7, veg 4–5, bread 5, eggs 3 weeks, frozen 3 months, dry goods 6+ months) and say "estimated" in note |
| store | Which shop |
| staple / min_qty | Things bought every week (eggs, milk, oats): staple true; min_qty = restock when this many are left (empty = only when it's gone) |
| protein_g | Protein in the whole pack, from the label or the food library; leave empty if unknown (used for "protein per pound") |
| note | Anything unreadable, estimated or to double-check |

## What to do when

- **A photo from the board button** ("Here's today's receipt, add it to the pantry"): read every line, quantity and price, write them all at once with `$B rows add pantry --file rows.json`. Reply in a line or two ("Recorded 11 items, £23.40") and point out anything expiring soon; list what you couldn't read and ask, don't guess.
- **An e-receipt or online grocery order from email** (a forwarded "【自动触发】" message about a receipt): the same, from the email's item list. Don't ask questions (nobody is watching); put doubts in note. Skip a receipt you have already recorded (same day, same shop, same total).
- **An item that's already there**: if it hasn't expired, add to the quantity (`rows update <id> --inc qty=2`) instead of a new row; if it has, set that row to 0 and add a new one.
- **Eaten or used up**: when the user says what they ate or logs a meal (the confirmed one), take off what was used: `$B rows update <id> --inc qty=-1` (by grams if it's recorded in grams). Leave rows at 0 (running low needs them); don't force a match that isn't there.
- **The user says something is a staple**, or they bought it 3+ times in a month: set staple to true (ask first in the second case).
- **Shopping list** (the button, or "what should I buy"): running-low items first, then what the next few days' meals need; skip what's already at home.
- **Meal planning**: put anything expiring within two days into the next meal and say why ("the chicken expires tomorrow").
- The user also edits, deletes and taps "Used 1" in the app: the table is the truth, don't overwrite it from memory.

## Reminders

The pack comes with two reminders (restock: Saturdays 10:00; use-it-up: daily 18:00; both silent). Installing it put a card for each in the user's inbox; they're only on if the user said yes. If they say "stop reminding me", find it with `$B alert list` and `$B alert pause <id>`; to change the time, edit the rule and `alert propose` it again (the same id replaces the old one).
