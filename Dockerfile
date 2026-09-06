ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.11
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8

# Install build dependencies
RUN apk add --no-cache \
    curl \
    jq

WORKDIR /opt/open-hems

# Install python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy HEMS application layers and models
COPY . /opt/open-hems/

# Copy and prepare entrypoint
COPY run.sh /run.sh
RUN chmod +x /run.sh

EXPOSE 8099

CMD [ "/run.sh" ]
