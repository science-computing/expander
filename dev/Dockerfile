FROM debian:bullseye-slim AS build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get -y update \
	&& apt-get install -y \
		python3-virtualenv

RUN virtualenv /opt/peekaboo \
	&& /opt/peekaboo/bin/pip3 install sanic schema \
	&& find /opt/peekaboo/lib -name "*.so" | xargs strip \
	&& find /opt/peekaboo/lib -name "*.c" -delete

COPY dummy_peekaboo_api.py /opt/peekaboo/bin/

FROM debian:bullseye-slim
COPY --from=build /opt/peekaboo/ /opt/peekaboo/

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -y \
	&& apt-get install -y --no-install-suggests \
		python3-minimal  \
		python3-distutils \
	&& apt-get clean all \
	&& find /var/lib/apt/lists -type f -delete

RUN groupadd -g 1000 peekaboo
RUN useradd -g 1000 -u 1000 -m -d /var/lib/peekaboo peekaboo

EXPOSE 8100

USER peekaboo
ENTRYPOINT ["/opt/peekaboo/bin/python3", "/opt/peekaboo/bin/dummy_peekaboo_api.py"]
CMD ["--host", "0.0.0.0"]
