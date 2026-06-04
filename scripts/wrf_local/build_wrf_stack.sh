#!/usr/bin/env bash
set -euo pipefail

WRF_VERSION="${WRF_VERSION:-v4.5.2}"
WPS_VERSION="${WPS_VERSION:-v4.5}"
WORK_ROOT="${WORK_ROOT:-/work}"
FIXED_DIR="${FIXED_DIR:-${WORK_ROOT}/fixed}"
SRC_DIR="${SRC_DIR:-${FIXED_DIR}/src}"
BIN_DIR="${BIN_DIR:-${FIXED_DIR}/bin}"
WRF_CONFIGURE_OPTION="${WRF_CONFIGURE_OPTION:-34}"
WRF_NESTING_OPTION="${WRF_NESTING_OPTION:-1}"
WPS_CONFIGURE_OPTION="${WPS_CONFIGURE_OPTION:-3}"
JASPER_VERSION="${JASPER_VERSION:-2.0.33}"
BUILD_JOBS="${BUILD_JOBS:-3}"

mkdir -p "${SRC_DIR}" "${BIN_DIR}" "${FIXED_DIR}/lib"

log() {
  printf '\n[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

download_tarball() {
  local url="$1"
  local out="$2"
  if [[ -s "${out}" ]]; then
    log "reuse ${out}"
    return
  fi
  log "download ${url}"
  curl -L --retry 5 --retry-delay 5 -o "${out}.tmp" "${url}"
  mv "${out}.tmp" "${out}"
}

build_jasper() {
  if [[ -f "${FIXED_DIR}/lib/lib/libjasper.a" || -f "${FIXED_DIR}/lib/lib/libjasper.so" ]]; then
    log "Jasper already built"
    return
  fi
  local tar="${SRC_DIR}/jasper-${JASPER_VERSION}.tar.gz"
  download_tarball "https://github.com/jasper-software/jasper/archive/refs/tags/version-${JASPER_VERSION}.tar.gz" "${tar}"
  rm -rf "${SRC_DIR}/jasper-version-${JASPER_VERSION}" "${SRC_DIR}/jasper-build"
  tar -xzf "${tar}" -C "${SRC_DIR}"
  cmake -S "${SRC_DIR}/jasper-version-${JASPER_VERSION}" -B "${SRC_DIR}/jasper-build" \
    -DCMAKE_INSTALL_PREFIX="${FIXED_DIR}/lib" \
    -DJAS_ENABLE_DOC=false \
    -DJAS_ENABLE_PROGRAMS=false \
    -DJAS_ENABLE_SHARED=true
  cmake --build "${SRC_DIR}/jasper-build" -j "${BUILD_JOBS}"
  cmake --install "${SRC_DIR}/jasper-build"
}

build_wrf() {
  if [[ -x "${BIN_DIR}/real.exe" && -x "${BIN_DIR}/wrf.exe" && -d "${FIXED_DIR}/WRF/run" ]]; then
    log "WRF already built"
    return
  fi
  if [[ ! -d "${FIXED_DIR}/WRF/.git" ]]; then
    rm -rf "${FIXED_DIR}/WRF"
    log "clone WRF ${WRF_VERSION} with submodules"
    git clone --depth 1 --branch "${WRF_VERSION}" --recursive https://github.com/wrf-model/WRF.git "${FIXED_DIR}/WRF"
  else
    log "reuse WRF source tree"
  fi

  pushd "${FIXED_DIR}/WRF" >/dev/null
  export NETCDF=/usr
  export HDF5=/usr
  export WRFIO_NCD_LARGE_FILE_SUPPORT=1
  printf '%s\n%s\n' "${WRF_CONFIGURE_OPTION}" "${WRF_NESTING_OPTION}" | ./configure
  python3 - <<'PY'
from pathlib import Path

configure = Path("configure.wrf")
configure_text = configure.read_text()
for token in ("-flto=auto", "-ffat-lto-objects"):
    configure_text = configure_text.replace(token, "")
configure.write_text(configure_text)

path = Path("external/io_int/Makefile")
text = path.read_text()
needle = "#makefile to build io_int that does binary i/o\n"
patch = "#makefile to build io_int that does binary i/o\n# Some WRF configure paths pass an internal placeholder AR into this submake.\noverride AR := ar\n"
if "override AR := ar" not in text:
    path.write_text(text.replace(needle, patch, 1))
PY
  ./compile -j "${BUILD_JOBS}" em_real | tee compile.log
  test -x main/real.exe
  test -x main/wrf.exe
  cp -f main/real.exe main/wrf.exe main/ndown.exe main/tc.exe "${BIN_DIR}/"
  popd >/dev/null
}

build_wps() {
  if [[ -x "${BIN_DIR}/geogrid.exe" && -x "${BIN_DIR}/ungrib.exe" && -x "${BIN_DIR}/metgrid.exe" && -d "${FIXED_DIR}/WPS" ]]; then
    log "WPS already built"
    return
  fi
  rm -rf "${FIXED_DIR}/WPS"
  log "clone WPS ${WPS_VERSION}"
  git clone --depth 1 --branch "${WPS_VERSION}" https://github.com/wrf-model/WPS.git "${FIXED_DIR}/WPS"

  pushd "${FIXED_DIR}/WPS" >/dev/null
  export WRF_DIR="${FIXED_DIR}/WRF"
  export NETCDF=/usr
  export JASPERLIB="${FIXED_DIR}/lib/lib"
  export JASPERINC="${FIXED_DIR}/lib/include"
  printf '%s\n' "${WPS_CONFIGURE_OPTION}" | ./configure
  python3 - <<'PY'
from pathlib import Path

path = Path("configure.wps")
text = path.read_text()
text = text.replace("-L$(NETCDF)/lib  -lnetcdf", "-L$(NETCDF)/lib -L/usr/lib/x86_64-linux-gnu -lnetcdff -lnetcdf")
path.write_text(text)
PY
  {
    ./compile geogrid
    ./compile ungrib
    ./compile metgrid
  } | tee compile.log
  test -x geogrid.exe
  test -x ungrib.exe
  test -x metgrid.exe
  cp -f geogrid.exe ungrib.exe metgrid.exe link_grib.csh "${BIN_DIR}/"
  popd >/dev/null
}

log "build settings: WRF=${WRF_VERSION}, WPS=${WPS_VERSION}, jobs=${BUILD_JOBS}"
build_jasper
build_wrf
build_wps
log "WRF/WPS stack is ready in ${FIXED_DIR}"
