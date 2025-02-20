# syntax=docker/dockerfile:1
FROM python:3.12-alpine
LABEL org.opencontainers.image.source=https://github.com/TheNetworkGuy/netbox-zabbix-sync
LABEL org.opencontainers.image.title="NetBox-Zabbix-Sync"
LABEL org.opencontainers.image.description="Python script to synchronise NetBox devices to Zabbix."
LABEL org.opencontainers.image.documentation=https://github.com/TheNetworkGuy/netbox-zabbix-sync/
LABEL org.opencontainers.image.licenses=MIT
LABEL org.opencontainers.image.authors="Twan Kamans"

RUN mkdir -p /opt/netbox-zabbix
COPY . /opt/netbox-zabbix
WORKDIR /opt/netbox-zabbix
RUN if ! [ -f ./config.py ]; then cp ./config.py.example ./config.py; fi
RUN pip install -r ./requirements.txt
ENTRYPOINT ["python"]
CMD ["/opt/netbox-zabbix/netbox_zabbix_sync.py", "-v"]
