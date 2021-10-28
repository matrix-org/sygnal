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

# Install git; Sygnal uses it to obtain the package version from the state of the
# git repository.
RUN apt-get update && apt-get install -y git

# install sygnal and all of the python deps to /install.

COPY . /sygnal/

RUN pip install --prefix="/install" --no-warn-script-location /sygnal

###
### Stage 1: runtime
###

FROM python:3.7-slim
COPY --from=builder /install /usr/local

# Work around 'CA MD too weak' errors when using certificates provided by
# Apple, by reducing the OpenSSL security level to 1 (from 2).
RUN sed -i 's/DEFAULT@SECLEVEL=2/DEFAULT@SECLEVEL=1/' /etc/ssl/openssl.cnf

EXPOSE 5000/tcp

ENTRYPOINT ["python", "-m", "sygnal.sygnal"]
