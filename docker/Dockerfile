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

# install sygnal and all of the python deps to /install.

COPY . /sygnal/

RUN pip install --prefix="/install" --no-warn-script-location /sygnal

###
### Stage 1: runtime
###

FROM python:3.7-slim
COPY --from=builder /install /usr/local

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]
