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

# we need dependencies for postgresql
RUN apt-get update && apt-get install -y gcc git libpq-dev

RUN pip install --prefix="/install" --no-warn-script-location cryptography

# now install sygnal and all of the python deps to /install.

COPY . /sygnal/

RUN pip install --prefix="/install" --no-warn-script-location /sygnal

###
### Stage 1: runtime
###

FROM python:3.7-slim
RUN apt-get update && apt-get install -y libpq5 && apt-get clean
COPY --from=builder /install /usr/local

# copy the root certificate used by APNs, since Debian removed it
COPY docker/GeoTrust_Global_CA.crt /usr/local/share/ca-certificates/GeoTrust_Global_CA.crt
RUN update-ca-certificates

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]
