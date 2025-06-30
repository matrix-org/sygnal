# Dockerfile to build the matrixdotorg/sygnal docker images.
#
# To build the image, run `docker build` command from the root of the
# sygnal repository:
#
#    docker build -f docker/Dockerfile .
#

ARG DEBIAN_VERSION=bookworm
ARG PYTHON_VERSION=3.12
ARG POETRY_VERSION=1.8.3

###
### Stage 0: generate requirements.txt
###
### This stage is platform-agnostic, so we can use the build platform in case of cross-compilation.
###
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-${DEBIAN_VERSION} AS requirements

WORKDIR /sygnal

# Copy just what we need to run `poetry export`...
COPY pyproject.toml poetry.lock /sygnal/

# If specified, we won't verify the hashes of dependencies.
# This is only needed if the hashes of dependencies cannot be checked for some
# reason, such as when a git repository is used directly as a dependency.
ARG TEST_ONLY_SKIP_DEP_HASH_VERIFICATION

# If specified, we won't use the Poetry lockfile.
# Instead, we'll just install what a regular `pip install` would from PyPI.
ARG TEST_ONLY_IGNORE_POETRY_LOCKFILE

# This silences a warning as uv isn't able to do hardlinks between its cache
# (mounted as --mount=type=cache) and the target directory.
ENV UV_LINK_MODE=copy

# Export the dependencies, but only if we're actually going to use the Poetry lockfile.
# Otherwise, just create an empty requirements file so that the Dockerfile can
# proceed.
ARG POETRY_VERSION
RUN --mount=type=cache,target=/root/.cache/uv \
  if [ -z "$TEST_ONLY_IGNORE_POETRY_LOCKFILE" ]; then \
    uvx --with poetry-plugin-export==1.8.0 \
        poetry@${POETRY_VERSION} export -o /sygnal/requirements.txt ${TEST_ONLY_SKIP_DEP_HASH_VERIFICATION:+--without-hashes}; \
  else \
    touch /sygnal/requirements.txt; \
  fi

###
### Stage 1: builder
###
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-${DEBIAN_VERSION} AS builder

# This silences a warning as uv isn't able to do hardlinks between its cache
# (mounted as --mount=type=cache) and the target directory.
ENV UV_LINK_MODE=copy

# To speed up rebuilds, install all of the dependencies before we copy over
# the whole sygnal project, so that this layer in the Docker cache can be
# used while you develop on the source.
#
# This is aiming at installing the `[tool.poetry.depdendencies]` from pyproject.toml.
COPY --from=requirements /sygnal/requirements.txt /sygnal/
RUN --mount=type=cache,target=/root/.cache/uv \
  uv pip install --prefix="/install" --no-deps -r /sygnal/requirements.txt

# Copy over the rest of the sygnal source code.
COPY sygnal /sygnal/sygnal/
# ... and what we need to `pip install`.
COPY pyproject.toml README.md /sygnal/

# Repeat of earlier build argument declaration, as this is a new build stage.
ARG TEST_ONLY_IGNORE_POETRY_LOCKFILE

# Install the sygnal package itself.
# If we have populated requirements.txt, we don't install any dependencies
# as we should already have those from the previous `pip install` step.
RUN \
  --mount=type=cache,target=/root/.cache/uv \
  --mount=type=cache,target=/sygnal/target,sharing=locked \
  --mount=type=cache,target=${CARGO_HOME}/registry,sharing=locked \
  if [ -z "$TEST_ONLY_IGNORE_POETRY_LOCKFILE" ]; then \
    uv pip install --prefix="/install" --no-deps /sygnal; \
  else \
    uv pip install --prefix="/install" /sygnal; \
  fi

###
### Stage 2: runtime
###

FROM docker.io/library/python:${PYTHON_VERSION}-slim-${DEBIAN_VERSION}

COPY --from=builder /install /usr/local

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]

HEALTHCHECK --start-period=25s --interval=15s --timeout=5s \
  CMD curl --fail --silent --show-error http://localhost:5000/health || exit 1
