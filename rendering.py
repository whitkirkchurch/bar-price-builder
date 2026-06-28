from datetime import datetime

import pytz
from jinja2 import Environment, FileSystemLoader, select_autoescape
from ruamel.yaml import YAML
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

from config import DATA_DIR, OUTPUT_DIR, STYLES_DIR, TEMPLATES_DIR
from price_list import format_currency, is_list

YAML_SAFE = YAML(typ="safe")


def write_html_to_pdf_with_styles(html: str, output_filename: str) -> None:
    font_config = FontConfiguration()

    with (STYLES_DIR / "styles.css").open() as css_file:
        css = CSS(string=css_file.read(), font_config=font_config)

    pdf = HTML(string=html)
    pdf.write_pdf(
        OUTPUT_DIR / output_filename,
        stylesheets=[css],
        font_config=font_config,
    )


def build_price_list_pdfs() -> None:
    with (DATA_DIR / "prices.yaml").open() as prices_file:
        data = {
            "generated_time": datetime.now(tz=pytz.timezone("Europe/London")).strftime("%Y-%m-%d %H:%M"),
            "prices_data": YAML_SAFE.load(prices_file),
        }

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=select_autoescape())
    env.filters["format_currency"] = format_currency
    env.filters["is_list"] = is_list

    a3_template = env.get_template("A3.jinja")
    a3_html = a3_template.render(**data)
    write_html_to_pdf_with_styles(a3_html, "A3.pdf")

    a5_template = env.get_template("A5.jinja")
    a5_html = a5_template.render(**data)
    write_html_to_pdf_with_styles(a5_html, "A5.pdf")
