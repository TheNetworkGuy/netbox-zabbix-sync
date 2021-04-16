A script to sync the Netbox device inventory to Zabbix.

## Requires pyzabbix and pynetbox.

### Script settings
#### Enviroment variables

* ZABBIX_HOST="https://zabbix.local"
* ZABBIX_USER="username"
* ZABBIX_PASS="Password"
* NETBOX_HOST="https://netbox.local"
* NETBOX_TOKEN="secrettoken"

#### Flags
|  Flag | Option  |  Description |
| ------------ | ------------ | ------------ |
|  -c | cluster | For clustered devices: only add the primary node of a cluster and use the cluster name as hostname. |
|  -H | hostgroup | Create non-existing hostgroups in Zabbix. Usefull for a first run to add all required hostgroups. |
|  -t | tenant | Add the tenant name to the hostgroup format (Tenant/Site/Manufacturer/Role) |
|  -v | Verbose | Log with debugging on. |


#### Logging
Logs are generated under sync.log, set the script for debugging / info options etc.

#### Hostgroups: manual mode

In case of omitting the -H flag, manual hostgroup creation is required for devices in a new category.

This is in the format:
{Site name}/{Manufacturer name}/{Device role name}
And with tenants (-t flag):
{Tenant name}/{Site name}/{Manufacturer name}/{Device role name}

Make sure that the Zabbix user has proper permissions to create hosts.
The hostgroups are in a nested format. This means that proper permissions only need to be applied to the site name hostgroup and cascaded to any child hostgroups.

### Netbox settings
#### Custom fields
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

#### Permissions
Make sure that the user has proper permissions for device read and modify (modify to set the Zabbix HostID custom field) operations.

#### Custom links
To make the user experience easier you could add a custom link that redirects users to the Zabbix latest data.

* Name: zabbix_latestData
* Text: {% if obj.cf["zabbix_hostid"] %}Show host in Zabbix{% endif %}
* URL: {ZABBIX_URL} /zabbix.php?action=latest.view&filter_hostids[]={{ obj.cf["zabbix_hostid"] }}&filter_application=&filter_select=&filter_set=1
