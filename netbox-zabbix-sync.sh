#!/bin/bash
export ZABBIX_HOST="https://zabbix.local"
export ZABBIX_USER="username"
export ZABBIX_PASS="Password"
export NETBOX_HOST="https://netbox.local"
export NETBOX_TOKEN="secrettoken"

python3 netbox_zabbix_sync.py