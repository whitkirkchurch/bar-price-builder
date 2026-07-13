from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

import app
from product_images import ProductImageSyncSummary

if TYPE_CHECKING:
    from pathlib import Path


def test_build_product_images_command_dry_run(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_run_product_image_sync(products_file: Path, write: bool) -> ProductImageSyncSummary:
        called["products_file"] = products_file
        called["write"] = write
        return ProductImageSyncSummary(
            built_images=10,
            uploaded_images=0,
            upload_failures=0,
            new_product_ids_added=4,
        )

    monkeypatch.setattr(app, "run_product_image_sync", fake_run_product_image_sync)

    products_file = tmp_path / "products.yaml"
    products_file.write_text("defaults: {}\n")

    runner = CliRunner()
    result = runner.invoke(app.cli, ["build-product-images", "--products-file", str(products_file)])

    assert result.exit_code == 0
    assert called == {"products_file": products_file, "write": False}
    assert "Building product images" in result.output
    assert "Dry-run complete" in result.output
    assert "New product IDs added to products.yaml: 4" in result.output


def test_build_product_images_command_write(monkeypatch, tmp_path: Path) -> None:
    called: dict[str, object] = {}

    def fake_run_product_image_sync(products_file: Path, write: bool) -> ProductImageSyncSummary:
        called["products_file"] = products_file
        called["write"] = write
        return ProductImageSyncSummary(
            built_images=3,
            uploaded_images=3,
            upload_failures=0,
            new_product_ids_added=0,
        )

    monkeypatch.setattr(app, "run_product_image_sync", fake_run_product_image_sync)

    products_file = tmp_path / "products.yaml"
    products_file.write_text("defaults: {}\n")

    runner = CliRunner()
    result = runner.invoke(
        app.cli,
        ["build-product-images", "--products-file", str(products_file), "--write"],
    )

    assert result.exit_code == 0
    assert called == {"products_file": products_file, "write": True}
    assert "Uploaded images: 3" in result.output
