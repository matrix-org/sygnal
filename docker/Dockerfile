# Dockerfile to build the matrixdotorg/sygnal docker images.
#
# To build the image, run `docker build` command from the root of the
# sygnal repository:
#
#    docker build -f docker/Dockerfile .
#

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
COPY MANIFEST.in README.rst VERSION setup.py /sygnal/

RUN pip install --prefix="/install" --no-warn-script-location /sygnal[firebase]

###
### Stage 1: runtime
###

FROM python:3.7-slim

COPY --from=builder /install /usr/local

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]
