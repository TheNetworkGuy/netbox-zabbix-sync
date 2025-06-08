# syntax=docker/dockerfile:1
FROM python:3.12-alpine
RUN mkdir -p /opt/netbox-zabbix && chown -R 1000:1000 /opt/netbox-zabbix

USER 1000:1000
COPY --chown=1000:1000 . /opt/netbox-zabbix
WORKDIR /opt/netbox-zabbix
RUN if ! [ -f ./config.py ]; then cp ./config.py.example ./config.py; fi
USER root
RUN pip install -r ./requirements.txt
USER 1000:1000
ENTRYPOINT ["python"]
CMD ["/opt/netbox-zabbix/netbox_zabbix_sync.py", "-v"]
