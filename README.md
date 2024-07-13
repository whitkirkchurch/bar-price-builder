# Bar Price List Generator

This repository contains all the code to produce the price list for the bar at St Mary's Church Whitkirk Community Centre.

## Updating price data

### Getting the source price report

1. Sign in to ICRTouch
1. Go to 'Reports/View'
1. Generate a 'PLU Price List' CSV report for All Groups, All Departments and All Price Levels
1. Put it in the `data` folder

### Running a sense check

To sense check the till's PLU report against the price list data, run

```
poetry run python app.py check FILENAME
```

### Updating the consolidated price list

All the price data lives in the `data/prices.yaml` file.

## Generating price lists

Run

```
poetry run python app.py build
```

to spit out PDF files into the `outputs` folder.
