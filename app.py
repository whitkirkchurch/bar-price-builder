from weasyprint import HTML, CSS
import os
from weasyprint.text.fonts import FontConfiguration
import click
from jinja2 import Environment, PackageLoader, select_autoescape
from datetime import datetime
import yaml

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")


def format_currency(value) -> str:
    return "£{:,.2f}".format(value / 100)


def is_list(value) -> bool:
    return isinstance(value, list)


@click.command()
def build():
    with open(
        os.path.join(os.path.dirname(__file__), "data", "prices.yaml")
    ) as prices_file:
        data = {
            "generated_time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "prices_data": yaml.safe_load(prices_file),
        }

    env = Environment(loader=PackageLoader("app"), autoescape=select_autoescape())

    env.filters["format_currency"] = format_currency
    env.filters["is_list"] = is_list

    font_config = FontConfiguration()

    a3_template = env.get_template("A3.jinja")
    a3_html = a3_template.render(**data)

    pdf = HTML(string=a3_html)

    with open(
        os.path.join(os.path.dirname(__file__), "styles", "styles.css"), "r"
    ) as css_file:
        css = CSS(string=css_file.read(), font_config=font_config)

    pdf.write_pdf(
        os.path.join(OUTPUT_DIR, "A3.pdf"),
        stylesheets=[css],
        font_config=font_config,
    )

    a5_template = env.get_template("A5.jinja")
    a5_html = a5_template.render(**data)

    pdf = HTML(string=a5_html)

    with open(
        os.path.join(os.path.dirname(__file__), "styles", "styles.css"), "r"
    ) as css_file:
        css = CSS(string=css_file.read(), font_config=font_config)

    pdf.write_pdf(
        os.path.join(OUTPUT_DIR, "A5.pdf"),
        stylesheets=[css],
        font_config=font_config,
    )


if __name__ == "__main__":
    build()
