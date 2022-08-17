FROM debian:bullseye-slim AS build

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /expander

RUN apt-get -y update \
	&& apt-get install -y \
		python3-virtualenv

COPY . /expander/
RUN virtualenv /opt/expander \
	&& /opt/expander/bin/pip3 install . \
	&& find /opt/expander/lib -name "*.so" | xargs strip \
	&& find /opt/expander/lib -name "*.c" -delete

FROM debian:bullseye-slim
COPY --from=build /opt/expander/ /opt/expander/

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y \
	&& apt-get install -y --no-install-suggests \
		python3-minimal  \
		python3-distutils \
	&& apt-get clean all \
	&& find /var/lib/apt/lists -type f -delete

RUN groupadd -g 1000 expander
RUN useradd -g 1000 -u 1000 -m -d /var/lib/expander expander

USER expander
ENTRYPOINT ["/opt/expander/bin/expander-api"]
