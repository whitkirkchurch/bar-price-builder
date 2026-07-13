#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/build/lambda"
DIST_DIR="${ROOT_DIR}/dist"
ZIP_PATH="${DIST_DIR}/lambda.zip"

rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}" "${DIST_DIR}"

python3 -m pip install \
  --requirement "${ROOT_DIR}/lambda/requirements.txt" \
  --target "${BUILD_DIR}" \
  --upgrade

cp "${ROOT_DIR}/lambda_handler.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/supplier_data.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/supplier_updates.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/supplier_email.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/loyverse.py" "${BUILD_DIR}/"
cp "${ROOT_DIR}/config.py" "${BUILD_DIR}/"

mkdir -p "${BUILD_DIR}/data"
cp "${ROOT_DIR}/data/supplier_data.yaml" "${BUILD_DIR}/data/"

(
  cd "${BUILD_DIR}"
  zip -qr "${ZIP_PATH}" .
)

echo "Built ${ZIP_PATH}"
