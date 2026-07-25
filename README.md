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
1. Map each supplier code to exactly one PLU in the Airtable **Products** base, **Supplier mapping** table (`PLU` and `Servings per Unit` columns)
1. Set `AIRTABLE_PAT`, `AIRTABLE_BASE_ID`, and `AIRTABLE_SUPPLIER_MAPPING_TABLE_ID` in your environment
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

If a supplier code should be intentionally skipped (for example bulk ingredients that are costed elsewhere), set **Ignore** on that row in Airtable.

Ignored entries are reported in command output as:

- `[IGNORED] Supplier <code> | <comment>`

If `comment` is missing, the command falls back to the parsed supplier description and size.

To apply changes to Loyverse via API, run:

```
poetry run python app.py update-costs-from-supplier data/supplier_confirmation.txt --apply
```

When `--apply` is used, API writes are only sent for rows where the cost has actually changed.

EAN updates are also written when changed, but only for mappings where `servings_per_unit` is exactly `1`.

You must have `LOYVERSE_PAT`, `AIRTABLE_PAT`, `AIRTABLE_BASE_ID`, and `AIRTABLE_SUPPLIER_MAPPING_TABLE_ID` set in your environment for API access.

#### Processing forwarded supplier emails (AWS Lambda)

You can also forward supplier confirmation emails to a dedicated inbound address. SES stores the raw message in S3 and invokes a Lambda that:

1. Accepts messages only when the outer From/Reply-To is on an approved domain (default `whitkirkchurch.org.uk`)
2. Extracts the confirmation table from the email body or `.txt` attachment
3. Seeds any new supplier codes and product labels in Airtable
4. Applies Loyverse cost/EAN updates (`--apply` behaviour)
5. Replies to the sender with a plain-text report, including any unmapped supplier codes and newly seeded products

Unapproved senders are ignored (no processing, no reply).

For local debugging without deploying, use:

```
poetry run python app.py parse-supplier-email path/to/message.eml
poetry run python app.py parse-supplier-email path/to/message.eml --extract-only
```

##### Deploying the Lambda with GitHub Actions

Deployment uses **GitHub OIDC** (no AWS access keys in GitHub). Setup is two stages:

1. **Bootstrap** (once, locally): creates Terraform state storage and the GitHub Actions IAM role — see [`terraform/bootstrap/README.md`](terraform/bootstrap/README.md)
2. **Application** (ongoing): GitHub Actions builds the Lambda and runs Terraform on push to `main`

```bash
# One-time bootstrap (admin AWS credentials required)
cp terraform/bootstrap/terraform.tfvars.example terraform/bootstrap/terraform.tfvars
# edit terraform/bootstrap/terraform.tfvars
terraform -chdir=terraform/bootstrap init
terraform -chdir=terraform/bootstrap apply
```

Then add the bootstrap outputs and `TF_VAR_*` values as GitHub repository secrets (and the `AWS_REGION` variable), as documented in [`terraform/README.md`](terraform/README.md).

### Building product images for Loyverse

Generate local PNG images for all on-sale products from Loyverse.

1. Configure styling defaults and per-product ID overrides in `data/products.yaml` (for example `background_color`)
   - You can define reusable colours under `palette` and reference them by name (e.g. `slate_night`) or `$name` (e.g. `$slate_night`)
2. Build local images first (dry-run):

```
poetry run python app.py build-product-images
```

This writes PNG files to `outputs/product-images` by default.

During each run, the command fetches all items from Loyverse (same bulk item fetch pattern as other operations), filters to on-sale products for image generation, and seeds any new on-sale product IDs into `data/products.yaml` under `product_id_overrides` with inline comments for item names. Existing YAML entries are kept even if a product is no longer on sale; off-sale products that are not already in the YAML are not added.

To upload generated images to Loyverse, run with `--write`:

```
poetry run python app.py build-product-images --write
```

In `--write` mode, images are uploaded directly to each Loyverse item ID.
`LOYVERSE_PAT` must be set for uploads.
