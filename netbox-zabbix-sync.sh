#!/bin/bash

# Set environment variables for Zabbix and Netbox URLs and authentication tokens
export ZABBIX_HOST="https://zabbix.local"
export ZABBIX_USER="username"
export ZABBIX_PASS="Password"
export NETBOX_HOST="https://netbox.local"
export NETBOX_TOKEN="secrettoken"

# Call the Python script to sync Netbox devices with Zabbix
python3 netbox_zabbix_sync.py