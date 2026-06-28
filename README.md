# Bar Price List Generator

This repository contains all the code to produce the price list for the bar at St Mary's Church Whitkirk Community Centre.

## Developing

We use [pre-commit](https://pre-commit.com/) to automate some sense checks. Download this, then run

```
pre-commit install
```

to make sure you have the right commit hooks in place.

## Using this repository

```mermaid
graph TD;
    DL([Download price list report from ICRTouch])
    SC[Run sense check script]
    ER{Does sense-check report errors?}
    UPY[Resolve errors]
    CPY[Commit prices.yaml]
    GPDF[Generate PDF price lists]
    PPDF([Print PDFs and update signage])

    DL-->SC
    SC-->ER
    ER-- Yes -->UPY
    UPY-->SC
    ER-- No -->CPY
    CPY-->GPDF
    GPDF-->PPDF
```

### Checking our price list matches the till

#### Getting the source price report

You will need access to ICRTouch for this.

1. Sign in to ICRTouch
1. Go to 'Reports/View'
1. Generate a 'PLU Price List' CSV report for All Groups, All Departments and All Price Levels
1. Put it in the `data` folder

#### Running a sense check

To sense check the till's PLU report against the price list data, run

```
poetry run python app.py check FILENAME
```

### Generating updated price list documentation

#### Update the price list data

All the price data lives in the `data/prices.yaml` file.

#### Generating PDF files for print

Run

```
poetry run python app.py build
```

to spit out PDF files into the `outputs` folder.

This command also generates `outputs/prices.csv` with one row per listed item and columns for `name`, `price`, and `category`.

### Updating Loyverse costs from supplier confirmations

When you have a supplier order confirmation text file, you can calculate per-unit costs and optionally write costs to Loyverse.

1. Save the confirmation text to a local file, for example `data/supplier_confirmation.txt`
1. Update `data/supplier_data.yaml` to map each supplier code to exactly one PLU using `mapping.plu` and `mapping.servings_per_unit`
1. Run a dry run first:

```
poetry run python app.py update-costs-from-supplier data/supplier_confirmation.txt
```

This fetches all till products from Loyverse, resolves PLUs to internal item/variant IDs, and calculates costs.

The command now prints one line per mapped product showing:

- Product/PLU name
- Cost status (current and, when changed, new calculated cost)
- EAN status (current and, when changed, supplier EAN)
- `CHANGED` vs `UNCHANGED`

New costs are rounded to 2 decimal places (pounds) before comparison and API writes, matching Loyverse cost precision.

If a supplier code should be intentionally skipped (for example bulk ingredients that are costed elsewhere), set `ignore: true` on that entry in `data/supplier_data.yaml`.

Ignored entries are reported in command output as:

- `[IGNORED] Supplier <code> | <comment>`

If `comment` is missing, the command falls back to the parsed supplier description and size.

To apply changes to Loyverse via API, run:

```
poetry run python app.py update-costs-from-supplier data/supplier_confirmation.txt --apply
```

When `--apply` is used, API writes are only sent for rows where the cost has actually changed.

EAN updates are also written when changed, but only for mappings where `servings_per_unit` is exactly `1`.

You must have `LOYVERSE_PAT` set in your environment for API access.
