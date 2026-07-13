from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from click.testing import CliRunner

import app
import rendering


class _DummyTemplate:
    def __init__(self, template_name: str) -> None:
        self.template_name = template_name

    def render(self, **data: Any) -> str:
        return f"{self.template_name}|generated={data['generated_time']}|rows={len(data['prices_data'])}"


class _DummyEnvironment:
    def __init__(self, *args, **kwargs) -> None:
        self.filters: dict[str, Any] = {}

    def get_template(self, template_name: str) -> _DummyTemplate:
        return _DummyTemplate(template_name)


def test_build_price_list_pdfs_builds_a3_and_a5(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []

    prices_yaml = tmp_path / "prices.yaml"
    prices_yaml.write_text(
        "- name: Spirits\n  items:\n    - name: Vodka\n      plus: [1001]\n      price: 1250\n",
    )
    monkeypatch.setattr(rendering, "DATA_DIR", tmp_path)

    def fake_write_html_to_pdf_with_styles(html: str, output_filename: str) -> None:
        calls.append((output_filename, html))

    monkeypatch.setattr(rendering, "Environment", _DummyEnvironment)
    monkeypatch.setattr(rendering, "write_html_to_pdf_with_styles", fake_write_html_to_pdf_with_styles)

    rendering.build_price_list_pdfs()

    assert len(calls) == 2
    assert calls[0][0] == "A3.pdf"
    assert calls[1][0] == "A5.pdf"
    assert calls[0][1].startswith("A3.jinja|generated=")
    assert calls[1][1].startswith("A5.jinja|generated=")


def test_build_command_invokes_menu_builder(monkeypatch) -> None:
    called = {"build": False}

    def fake_build_price_list_pdfs() -> None:
        called["build"] = True

    monkeypatch.setattr(app, "build_price_list_pdfs", fake_build_price_list_pdfs)

    runner = CliRunner()
    result = runner.invoke(app.cli, ["build"])

    assert result.exit_code == 0
    assert called["build"] is True
    assert "Building outputs" in result.output
    assert "Done!" in result.output
