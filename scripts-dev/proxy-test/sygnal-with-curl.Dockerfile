FROM localhost/sygnal

# add curl as we use it for our testing
RUN apt-get update -yqq && apt-get install curl -yqq && rm -rf /var/lib/apt/lists/*
