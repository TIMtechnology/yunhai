FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV WRF_ROOT=/opt/wrf
ENV WRF_WORK=/work
ENV PATH=/opt/wrf/bin:${PATH}

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    bison \
    cmake \
    csh \
    curl \
    file \
    flex \
    g++ \
    gcc \
    gfortran \
    git \
    libhdf5-dev \
    libjpeg-dev \
    libmpich-dev \
    libnetcdf-dev \
    libnetcdff-dev \
    libpng-dev \
    libxml2-dev \
    m4 \
    make \
    mpich \
    netcdf-bin \
    perl \
    python3 \
    python3-pip \
    tar \
    tcsh \
    time \
    unzip \
    wget \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

RUN for libdir in /usr/lib/*-linux-gnu /usr/lib/*-linux-gnu/hdf5/serial; do \
      if [ -e "$libdir/libhdf5hl_fortran.so" ] && [ ! -e "$libdir/libhdf5_hl_fortran.so" ]; then \
        ln -s "$libdir/libhdf5hl_fortran.so" "$libdir/libhdf5_hl_fortran.so"; \
      fi; \
      if [ -e "$libdir/libhdf5hl_fortran.a" ] && [ ! -e "$libdir/libhdf5_hl_fortran.a" ]; then \
        ln -s "$libdir/libhdf5hl_fortran.a" "$libdir/libhdf5_hl_fortran.a"; \
      fi; \
    done

RUN python3 -m pip install --no-cache-dir \
    netCDF4==1.7.2 \
    numpy==2.0.2 \
    xarray==2024.7.0

RUN mkdir -p ${WRF_ROOT} ${WRF_WORK}

WORKDIR ${WRF_WORK}

CMD ["/bin/bash"]
