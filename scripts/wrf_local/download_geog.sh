#!/usr/bin/env bash
set -euo pipefail

WORK_ROOT="${WORK_ROOT:-/work}"
FIXED_DIR="${FIXED_DIR:-${WORK_ROOT}/fixed}"
GEOG_DIR="${GEOG_DIR:-${FIXED_DIR}/geog}"
GEOG_URL="${GEOG_URL:-https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_high_res_mandatory.tar.gz}"
ARCHIVE="${FIXED_DIR}/$(basename "${GEOG_URL}")"

mkdir -p "${FIXED_DIR}"

if [[ -d "${GEOG_DIR}" && -f "${GEOG_DIR}/index" ]]; then
  echo "GEOG already exists: ${GEOG_DIR}"
  exit 0
fi

echo "Download WPS GEOG mandatory data:"
echo "${GEOG_URL}"
echo "Target: ${GEOG_DIR}"

if [[ ! -s "${ARCHIVE}" ]]; then
  curl -L --fail --retry 5 --retry-delay 10 -o "${ARCHIVE}.tmp" "${GEOG_URL}"
  mv "${ARCHIVE}.tmp" "${ARCHIVE}"
else
  echo "reuse archive: ${ARCHIVE}"
fi

rm -rf "${GEOG_DIR}.tmp"
mkdir -p "${GEOG_DIR}.tmp"
tar -xzf "${ARCHIVE}" -C "${GEOG_DIR}.tmp" --strip-components=1
rm -rf "${GEOG_DIR}"
mv "${GEOG_DIR}.tmp" "${GEOG_DIR}"
echo "GEOG ready: ${GEOG_DIR}"
