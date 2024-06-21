# Dockerfile to build the matrixdotorg/sygnal docker images.
#
# To build the image, run `docker build` command from the root of the
# sygnal repository:
#
#    docker build -f docker/Dockerfile .
#

ARG PYTHON_VERSION=3.11

###
### Stage 0: generate requirements.txt
###
# We hardcode the use of Debian bookworm here because this could change upstream.
FROM docker.io/library/python:${PYTHON_VERSION}-slim-bookworm as requirements

# We install poetry in its own build stage to avoid its dependencies conflicting with
# sygnal's dependencies.
RUN --mount=type=cache,target=/root/.cache/pip \
  pip install --user "poetry==1.8.3"

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

# Export the dependencies, but only if we're actually going to use the Poetry lockfile.
# Otherwise, just create an empty requirements file so that the Dockerfile can
# proceed.
RUN if [ -z "$TEST_ONLY_IGNORE_POETRY_LOCKFILE" ]; then \
  /root/.local/bin/poetry export -o /sygnal/requirements.txt ${TEST_ONLY_SKIP_DEP_HASH_VERIFICATION:+--without-hashes}; \
  else \
  touch /sygnal/requirements.txt; \
  fi

###
### Stage 1: builder
###
FROM docker.io/library/python:${PYTHON_VERSION}-slim-bookworm as builder

# To speed up rebuilds, install all of the dependencies before we copy over
# the whole sygnal project, so that this layer in the Docker cache can be
# used while you develop on the source.
#
# This is aiming at installing the `[tool.poetry.depdendencies]` from pyproject.toml.
COPY --from=requirements /sygnal/requirements.txt /sygnal/
RUN --mount=type=cache,target=/root/.cache/pip \
  pip install --prefix="/install" --no-deps --no-warn-script-location -r /sygnal/requirements.txt

# Copy over the rest of the sygnal source code.
COPY sygnal /sygnal/sygnal/
# ... and what we need to `pip install`.
COPY pyproject.toml README.md /sygnal/

# Repeat of earlier build argument declaration, as this is a new build stage.
ARG TEST_ONLY_IGNORE_POETRY_LOCKFILE

# Install the sygnal package itself.
# If we have populated requirements.txt, we don't install any dependencies
# as we should already have those from the previous `pip install` step.
RUN --mount=type=cache,target=/sygnal/target,sharing=locked \
  --mount=type=cache,target=${CARGO_HOME}/registry,sharing=locked \
  if [ -z "$TEST_ONLY_IGNORE_POETRY_LOCKFILE" ]; then \
  pip install --prefix="/install" --no-deps --no-warn-script-location /sygnal; \
  else \
  pip install --prefix="/install" --no-warn-script-location /sygnal; \
  fi

###
### Stage 2: runtime
###

FROM docker.io/library/python:${PYTHON_VERSION}-slim-bookworm

COPY --from=builder /install /usr/local

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]
