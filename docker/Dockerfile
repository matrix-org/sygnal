# Dockerfile to build the matrixdotorg/sygnal docker images.
#
# To build the image, run `docker build` command from the root of the
# sygnal repository:
#
#    docker build -f docker/Dockerfile .
#
# There is an optional PYTHON_VERSION build argument which sets the
# version of python to build against: for example:
#
#    docker build -f docker/Dockerfile --build-arg PYTHON_VERSION=3.6 .
#

ARG PYTHON_VERSION=3.7

###
### Stage 0: builder
###
FROM python:3.7-slim as builder

# build things which have slow build steps, before we copy sygnal, so that
# the layer can be cached.
#
# (we really just care about caching a wheel here, as the "pip install" below
# will install them again.)

RUN pip install --prefix="/install" --no-warn-script-location cryptography

# now install sygnal and all of the python deps to /install.

COPY sygnal /sygnal/sygnal/
COPY MANIFEST.in README.rst VERSION setup.py synctl /sygnal/

RUN pip install --prefix="/install" --no-warn-script-location \
        /sygnal[all]

###
### Stage 1: runtime
###

FROM python:3.7-slim

# xmlsec is required for saml support
RUN apk add --no-cache --virtual .runtime_deps \
        libffi \
        libjpeg-turbo \
        libressl \
        libxslt \
        libpq \
        zlib \
        su-exec \
        tzdata \
        xmlsec

COPY --from=builder /install /usr/local
COPY ./docker/start.py /start.py
COPY ./docker/conf /conf

VOLUME ["/data"]

EXPOSE 8008/tcp 8009/tcp 8448/tcp

ENTRYPOINT ["/start.py"]
