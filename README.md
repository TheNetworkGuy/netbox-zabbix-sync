A script to sync the Netbox inventory to Zabbix.

Requires pyzabbix and pynetbox

Use the following variables for the environment:

* ZABBIX_HOST="https://zabbix.local"
* ZABBIX_USER="username"
* ZABBIX_PASS="Password"
* NETBOX_HOST="https://netbox.local"
* NETBOX_TOKEN="secrettoken"

Logs are generated under sync.log, set the script for debugging / info options etc.

Important: you need to set the hostgroup in Zabbix before a sync can occur. This is in the following format:

{Site name}/{Manufacturer name}/{Device role name}

Make sure that the Zabbix user has proper permissions to create hosts

Use the following custom fields in Netbox to map the Zabbix URL:

* Type: Integer
* Name: zabbix_hostid
* Required: False
* Default: null
* Object: dcim > device

And this field for the Zabbix template

* Type: Text
* Name: zabbix_template
* Required: False
* Default: null
* Object: dcim > device_type
