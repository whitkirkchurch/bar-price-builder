# Bar Price List Generator

This repository contains all the code to produce the price list for the bar at St Mary's Church Whitkirk Community Centre.

## Updating price data

All the price data lives in the `data/prices.yaml` file.

## Generating price lists

Run

```
poetry run python app.py build
```

to spit out PDF files into the `outputs` folder.
