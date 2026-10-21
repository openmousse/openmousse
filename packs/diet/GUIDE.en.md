# Meal log pack: how to use it

The meal log lives in the server's own database (not in your data tables): one row per thing eaten, with its nutrition and where that came from; a personal food library holds per-100 g (drinks: per-100 ml) nutrition and usual portions for the foods the user eats; nutrition targets are stored by date. The diet board's Today so far, Eaten today and This week read it, and so do Today and the widgets.

```bash
D="python3 ~/.openmousse/repo/server/diet_ctl.py"   # run it in your own workspace; without a shell, use the MCP diet tool with the same arguments
$D day                 # today: what was eaten per meal (each row has an id), totals, targets, what's left
$D library --days 30   # usual foods: times, usual amount, per 100 g, food library id
```

## Logging

- **When the user asks you to log something** ("I had…", "log it"): `$D add --meal lunch --item "<food id or name>=grams"`; run it without `--commit` first to see the preview. If nothing is estimated and nothing is uncertain (amounts, which food), add `--commit`, then say in one line what was logged (grams, calories, protein per item) and the batch id. If anything is estimated (photos, takeaways, not found) or uncertain, show the preview and `--commit` once the user agrees.
- **From today's meal plan card**: `$D log-plan --meal dinner` (a meal is never logged twice; `--set meal.item=grams` changes an amount, counting from 0, and 0 = skipped; `--scale 0.8` scales the whole meal). The app's "Log this meal" button uses the same endpoint and doesn't go through you.
- **Amounts** are in grams; foods with saved portions can be logged by count: `--item "Egg:1 egg (shelled)×2"`.
- **A photo of a meal** (sent by the board's Snap a meal): recognise each item and roughly how many grams; use library nutrition where it matches and estimate the rest: `--item "Name (estimate)=grams@kcal,protein,carb,fat" --source photo`. Always show the preview for photos.
- **Wrong entry**: `$D undo <batch>` (the whole batch), `$D rm <id>`, `$D edit <id> --amount 120 --meal lunch`.
- **Things you'd like to fill in yourself** (the user didn't ask, e.g. breakfast still empty at noon): don't write it; raise an inbox card (inbox skill).

## Looking up nutrition (always in this order)

1. **The food library**: `$D foods --q name`. If it's there, use it and stop.
2. **Packaged food: Open Food Facts.** With a barcode `$D lookup --barcode <barcode>`, otherwise `$D lookup "name" --en "English name" --packaged`. When brand, flavour and size all match, save it: `$D food-save --from off:<barcode> --name "<what the user calls it>" [--portion "1 pack=140"]`.
   If the user sends a photo of the nutrition label (the board's Snap a label), read the label: `$D food-add --name … --per100 kcal,protein,carb,fat --source label --note "pack label"` (convert per-serving values to per 100 g / ml).
3. **Generic food: CoFID 2021** (the UK's official composition tables, English names only; pick raw vs cooked and with vs without skin carefully): `$D lookup banana --en "bananas flesh only"`, then `$D food-save --from cofid:<code> --name Banana [--portion "1 banana=120"]`. The first lookup downloads and imports it from GOV.UK (a few seconds).
4. **Not in CoFID: USDA FoodData Central** (only when the server's .env has `USDA_FDC_API_KEY`): `--from usda:<fdcId>`.
5. **Nothing found**: estimate from a similar food and mark it as an estimate (in the name or with `--estimated`), and say so in your reply. When a label or barcode turns up later, `$D food-update <id> --per100 …`.

`$D lookup` follows this order and stops at the first source with results; `--source off` queries one source, `--all` all of them. **Never invent numbers**: if it can't be found, it's an estimate and must be marked.

## Nutrition targets

`$D targets` shows the current targets and their history. When the user asks for a change: `$D targets set --protein 100 --carb 200 --fat 60 [--kcal 1740] [--from date]` (without kcal it's derived as 4/4/9). Do what the user says directly.

**Carb cycling** (separate training-day and rest-day targets): set one with `$D targets set --day-type train …` and one with `--day-type rest …`. Which kind a day is: a mark (`$D day-type train|rest [--date]`, `auto` removes it) > a training session in the schedule (an Agent schedule key containing `:training:`) > rest day. `$D day` and `/api/diet/day` already give that day's targets; check today is right before planning meals, and change the mark when the user adds or skips a session.

## With the pantry pack

- When a receipt goes into the pantry, add each item's nutrition to the food library (same lookup order) and compute the pantry's `protein_g` from it; if nothing is found, put "nutrition to check" in note.
- After a meal is logged, take off what was used from the pantry.

## Data and attribution

- Open Food Facts is under the Open Database License (ODbL), CoFID under the UK Open Government Licence v3.0, and USDA FoodData Central is public domain. Every food in the library records its source; when you talk about nutrition, say where it came from.
- Lookups are cached on this server only; never send the merged food library to anyone else (shares, friends and the card agent never include it).
