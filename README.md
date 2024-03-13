
# Netbox to Zabbix synchronization

A script to create, update and delete Zabbix hosts using Netbox device objects.


## Installation via Docker

To pull the latest stable version to your local cache, use the following docker pull command:
```
docker pull ghcr.io/TheNetworkGuy/netbox-zabbix-sync:latest
```

Make sure to specify the needed environment variables for the script to work (see [here](#set-environment-variables))
on the command line or use an [env file](https://docs.docker.com/reference/cli/docker/container/run/#env).

```
docker run -d -t -i -e ZABBIX_HOST='https://zabbix.local' \ 
-e ZABBIX_TOKEN='othersecrettoken' \
-e NETBOX_HOST='https://netbox.local' \
-e NETBOX_TOKEN='secrettoken' \
--name netbox-zabbix-sync ghcr.io/TheNetworkGuy/netbox-zabbix-sync:latest
```

This should run a one-time sync, you can check the sync with `docker logs`.

The image uses the default `config.py` for it's configuration, you can use a volume mount in the docker run command 
to override with your own config file if needed (see [config file](#config-file)):
```
docker run -d -t -i -v $(pwd)/config.py:/opt/netbox-zabbix/config.py ... 
```  

## Installation from Source

### Cloning the repository
```
git clone https://github.com/TheNetworkGuy/netbox-zabbix-sync.git
```

### Packages
Make sure that you have a python environment with the following packages installed. You can also use the `requirements.txt` file for installation with pip.
```
pynetbox
pyzabbix
```

### Config file
First time user? Copy the `config.py.example` file to `config.py`. This file is used for modifying filters and setting variables such as custom field names.
```
cp config.py.example config.py
```

### Set environment variables
Set the following environment variables:
```
ZABBIX_HOST="https://zabbix.local"
ZABBIX_USER="username"
ZABBIX_PASS="Password"
NETBOX_HOST="https://netbox.local"
NETBOX_TOKEN="secrettoken"
```

Or, you can use a Zabbix API token to login instead of using a username and password.
In that case `ZABBIX_USER` and `ZABBIX_PASS` will be ignored.

```
ZABBIX_TOKEN=othersecrettoken
```

### Netbox custom fields
Use the following custom fields in Netbox (if you are using config context for the template information then the zabbix_template field is not required):
```
* Type: Integer
* Name: zabbix_hostid
* Required: False
* Default: null
* Object: dcim > device
```
```
* Type: Text
* Name: zabbix_template
* Required: False
* Default: null
* Object: dcim > device_type
```
You can make the `zabbix_hostid` field hidden or read-only to prevent human intervention.

This is optional and there is a use case for leaving it read-write in the UI to manually change the ID. For example to re-run a sync.

## Config file

### Hostgroup
Setting the `create_hostgroups` variable to `False` requires manual hostgroup creation for devices in a new category.

The format can be set with the `hostgroup_format` variable.

Make sure that the Zabbix user has proper permissions to create hosts.
The hostgroups are in a nested format. This means that proper permissions only need to be applied to the site name hostgroup and cascaded to any child hostgroups.

#### layout
The default hostgroup layout is "site/manufacturer/device_role".

**Variables**

You can change this behaviour with the hostgroup_format variable. The following values can be used:
|  name | description  |
| ------------ | ------------ |
|dev_location|The device location name|
|dev_role|The device role name|
|manufacturer|Manufacturer name|
|region|The region name of the device|
|site|Site name|
|site_group|Site group name|
|tenant|Tenant name|
|tenant_group|Tenant group name|


You can specify the value like so, sperated by a "/":
```
hostgroup_format = "tenant/site/dev_location/dev_role"
```
**custom fields**

You can also use the value of custom fields under the device object.

This allows more freedom and even allows a full static mapping instead of a dynamic rendered hostgroup name.
```
hostgroup_format = "site/mycustomfieldname"
```
**Empty variables or hostgroups**

Should the content of a variable be empty, then the hostgroup position is skipped.

For example, consider the following scenario with 2 devices, both the same device type and site. One of them is linked to a tenant, the other one does not have a relationship with a tenant.
- Device_role: PDU
- Site: HQ-AMS
```
hostgroup_format = "site/tenant/device_role"
```
When running the script like above, the following hostgroup (HG) will be generated for both hosts:
 - Device A with no relationship with a tenant: HQ-AMS/PDU
 - Device B with a relationship to tenant "Fork Industries": HQ-AMS/Fork Industries/PDU

The same logic applies to custom fields being used in the HG format:
```
hostgroup_format = "site/mycustomfieldname"
```
For device A with the value "ABC123" in the custom field "mycustomfieldname" -> HQ-AMS/ABC123
For a device which does not have a value in the custom field "mycustomfieldname" -> HQ-AMS

Should there be a scenario where a custom field does not have a value under a device, and the HG format only uses this single variable, then this will result in an error:
```
hostgroup_format = "mycustomfieldname"

Netbox-Zabbix-sync - ERROR - ESXI1 has no reliable hostgroup. This is most likely due to the use of custom fields that are empty.
```
### Device status
By setting a status on a Netbox device you determine how the host is added (or updated) in Zabbix. There are, by default, 3 options:
* Delete the host from Zabbix (triggered by Netbox status "Decommissioning" and "Inventory")
* Create the host in Zabbix but with a disabled status (Trigger by "Offline", "Planned", "Staged" and "Failed")
* Create the host in Zabbix with an enabled status (For now only enabled with the "Active" status)

You can modify this behaviour by changing the following list variables in the script:
 - `zabbix_device_removal`
 - `zabbix_device_disable`

### Template source
You can either use a Netbox device type custom field or Netbox config context for the Zabbix template information.

Using a custom field allows for only one template. You can assign multiple templates to one host using the config context source.
Should you make use of an advanced templating structure with lots of nesting then i would recommend sticking to the custom field.

You can change the behaviour in the config file. By default this setting is false but you can set it to true to use config context:
```
templates_config_context = True
```

After that make sure that for each host there is at least one template defined in the config context in this format:
```
{
    "zabbix": {
        "templates": [
            "TemplateA",
            "TemplateB",
            "TemplateC",
            "Template123"
        ]
    }
}
```

You can also opt for the default device type custom field behaviour but with the added benefit of overwriting the template should a device in Netbox have a device specific context defined. In this case the device specific context template(s) will take priority over the device type custom field template.
```
templates_config_context_overrule = True
```

## Permissions

### Netbox
Make sure that the Netbox user has proper permissions for device read and modify (modify to set the Zabbix HostID custom field) operations. The user should also have read-only access to the device types.

### Zabbix
Make sure that the Zabbix user has permissions to read hostgroups and proxy servers. The user should have full rights on creating, modifying and deleting hosts.

If you want to automatically create hostgroups then the create permission on host-groups should also be applied.

### Custom links
To make the user experience easier you could add a custom link that redirects users to the Zabbix latest data.
```
* Name: zabbix_latestData
* Text: {% if obj.cf["zabbix_hostid"] %}Show host in Zabbix{% endif %}
* URL: http://myzabbixserver.local/zabbix.php?action=latest.view&hostids[]={{ obj.cf["zabbix_hostid"] }}
```
## Running the script
```
python3 netbox_zabbix_sync.py
```
### Flags
|  Flag | Option  |  Description |
| ------------ | ------------ | ------------ |
|  -v | verbose | Log with debugging on. |

## Config context

### Zabbix proxy
You can set the proxy for a device using the 'proxy' key in config context.
```json
{
    "zabbix": {
        "proxy": "yourawesomeproxy.local"
    }
}
```
Because of the possible amount of destruction when setting up Netbox but forgetting the proxy command, the sync works a bit different. By default everything is synced except in a situation where the Zabbix host has a proxy configured but nothing is configured in Netbox. To force deletion and a full sync, set the `full_proxy_sync` variable in the config file.

### Set interface parameters within Netbox
When adding a new device, you can set the interface type with custom context. By default, the following configuration is applied when no config context is provided:

* SNMPv2
* UDP 161
* Bulk requests enabled
* SNMP community: {$SNMP_COMMUNITY}

Due to Zabbix limitations of changing interface type with a linked template, changing the interface type from within Netbox is not supported and the script will generate an error.

For example when changing a SNMP interface to an Agent interface:
```
Netbox-Zabbix-sync - WARNING - Device: Interface OUT of sync.
Netbox-Zabbix-sync - ERROR - Device: changing interface type to 1 is not supported.
```

To configure the interface parameters you'll need to use custom context. Custom context was used to make this script as customizable as posible for each environment. For example, you could:
 * Set the custom context directly on a device
 * Set the custom context on a label, which you would add to a device (for instance, SNMPv3)
 * Set the custom context on a device role
 * Set the custom context on a site or region

##### Agent interface configuration example
```json
{
    "zabbix": {
        "interface_port": 1500,
        "interface_type": 1
    }
}
```
##### SNMPv2 interface configuration example
```json
{
    "zabbix": {
        "interface_port": 161,
        "interface_type": 2,
        "snmp": {
            "bulk": 1,
            "community": "SecretCommunity",
            "version": 2
        }
    }
}
```
##### SNMPv3 interface configuration example
```json
{
    "zabbix": {
        "interface_port": 1610,
        "interface_type": 2,
        "snmp": {
            "authpassphrase": "SecretAuth",
            "bulk": 1,
            "securitylevel": 1,
            "securityname": "MySecurityName",
            "version": 3
        }
    }
}
```

I would recommend using macros for sensitive data such as community strings since the data in Netbox is plain-text.

> **_NOTE:_** Not all SNMP data is required for a working configuration. [The following parameters are allowed ](https://www.zabbix.com/documentation/current/manual/api/reference/hostinterface/object#details_tag "The following parameters are allowed ")but are not all required, depending on your environment.
