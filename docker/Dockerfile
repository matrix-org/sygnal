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
FROM python:3.10-slim as builder

# Install git; Sygnal uses it to obtain the package version from the state of the
# git repository.
RUN apt-get update && apt-get install -y git

# install sygnal and all of the python deps to /install.

COPY . /sygnal/

RUN pip install --prefix="/install" --no-warn-script-location /sygnal

###
### Stage 1: runtime
###

FROM python:3.10-slim
COPY --from=builder /install /usr/local

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]
