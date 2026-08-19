# Adds Enroot + Pyxis (`srun --container-image=... python train.py` support)
# on top of the upstream Slinky slurmd image. Neither ships in the base
# image, so both are built from source here rather than at pod startup
# (see git history on this file for the earlier initContainer approach and
# why it was dropped: rebuilding on every pod start is slower and the
# initContainer's target install path had to dodge FHS locations to avoid
# shadowing the base image's own /usr/local — building it into the image
# once removes both problems).
#
# Rebuild whenever the base image's Slurm version changes: SLURM_GIT_TAG
# must match the Slurm build baked into BASE_IMAGE (check `slurmd -V` in a
# running container), or spank_pyxis.so links against a spank.h ABI slurmd
# doesn't actually implement and the plugin fails to load.
#
# Built and pushed to GHCR by
# .github/workflows/docker-publish-slurmd-pyxis-enroot.yml on every change
# to this file. To build locally:
#   docker build -f slurmd-pyxis-enroot.Dockerfile \
#     -t ghcr.io/hrishin/slurmd-pyxis-enroot:26.05-ubuntu26.04-pyxis0.24.0-enroot4.2.1 .

ARG BASE_IMAGE=ghcr.io/slinkyproject/slurmd:26.05-ubuntu26.04
FROM ${BASE_IMAGE}

ARG ENROOT_TAG=v4.2.1
ARG PYXIS_TAG=v0.24.0
ARG SLURM_GIT_TAG=slurm-26-05-3-1

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates git curl build-essential pkg-config libtool automake \
      fuse-overlayfs squashfs-tools squashfuse parallel jq \
    && rm -rf /var/lib/apt/lists/*

# Enroot vendors musl+libbsd as git submodules and links its C helpers
# statically against them; `make install` pulls both in via its `deps`
# target. prefix=/usr (not the Makefile's own /usr/local default) so the
# installed `enroot` binary lands on the default PATH.
RUN git clone --depth 1 --branch "${ENROOT_TAG}" \
      https://github.com/NVIDIA/enroot.git /tmp/enroot \
    && make -C /tmp/enroot prefix=/usr install \
    && rm -rf /tmp/enroot

# Pyxis needs slurm/*.h headers matching this image's Slurm build — sparse
# checkout of just the public headers, not a full Slurm build. prefix=/usr
# so `make install` writes pyxis.conf to /usr/share/pyxis, matching the
# `include /usr/share/pyxis/*` plugstack.conf line in release.yaml.
RUN git clone --depth 1 --filter=blob:none --sparse \
      --branch "${SLURM_GIT_TAG}" \
      https://github.com/SchedMD/slurm.git /tmp/slurm-src \
    && git -C /tmp/slurm-src sparse-checkout set slurm \
    && git clone --depth 1 --branch "${PYXIS_TAG}" \
      https://github.com/NVIDIA/pyxis.git /tmp/pyxis \
    && export CPPFLAGS="-I/tmp/slurm-src" \
    && make -C /tmp/pyxis prefix=/usr install \
    && rm -rf /tmp/pyxis /tmp/slurm-src
