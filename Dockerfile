FROM debian:bullseye-slim AS build

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /extractor

RUN apt-get -y update \
	&& apt-get install -y \
		python3-virtualenv

COPY . /extractor/
RUN virtualenv /opt/extractor \
	&& /opt/extractor/bin/pip3 install . \
	&& find /opt/extractor/lib -name "*.so" | xargs strip \
	&& find /opt/extractor/lib -name "*.c" -delete

FROM debian:bullseye-slim
COPY --from=build /opt/extractor/ /opt/extractor/

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y \
	&& apt-get install -y --no-install-suggests \
		python3-minimal  \
		python3-distutils \
	&& apt-get clean all \
	&& find /var/lib/apt/lists -type f -delete

RUN groupadd -g 1000 extractor
RUN useradd -g 1000 -u 1000 -m -d /var/lib/extractor extractor

USER extractor
ENTRYPOINT ["/opt/extractor/bin/extractor-api"]
