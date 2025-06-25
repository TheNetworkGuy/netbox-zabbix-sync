# NetBox to Zabbix synchronization

A script to create, update and delete Zabbix hosts using NetBox device objects. Tested and compatible with all [currently supported Zabbix releases](https://www.zabbix.com/life_cycle_and_release_policy).

## Installation via Docker

To pull the latest stable version to your local cache, use the following docker
pull command:

```bash
docker pull ghcr.io/thenetworkguy/netbox-zabbix-sync:main
```

Make sure to specify the needed environment variables for the script to work
(see [here](#set-environment-variables)) on the command line or use an
[env file](https://docs.docker.com/reference/cli/docker/container/run/#env).

```bash
docker run -d -t -i -e ZABBIX_HOST='https://zabbix.local' \ 
-e ZABBIX_TOKEN='othersecrettoken' \
-e NETBOX_HOST='https://netbox.local' \
-e NETBOX_TOKEN='secrettoken' \
--name netbox-zabbix-sync ghcr.io/thenetworkguy/netbox-zabbix-sync:main
```

This should run a one-time sync. You can check the sync with
`docker logs netbox-zabbix-sync`.

The image uses the default `config.py` for its configuration, you can use a
volume mount in the docker run command to override with your own config file if
needed (see [config file](#config-file)):

```bash
docker run -d -t -i -v $(pwd)/config.py:/opt/netbox-zabbix/config.py ...
```

## Installation from Source

### Cloning the repository

```bash
git clone https://github.com/TheNetworkGuy/netbox-zabbix-sync.git
```

### Packages

Make sure that you have a python environment with the following packages
installed. You can also use the `requirements.txt` file for installation with
pip.

```sh
# Packages:
pynetbox
pyzabbix

# Install them through requirements.txt from a venv:
virtualenv .venv
source .venv/bin/activate
.venv/bin/pip --require-virtualenv install -r requirements.txt
```

### Config file

First time user? Copy the `config.py.example` file to `config.py`. This file is
used for modifying filters and setting variables such as custom field names.

```sh
cp config.py.example config.py
```

### Set environment variables

Set the following environment variables:

```bash
ZABBIX_HOST="https://zabbix.local"
ZABBIX_USER="username"
ZABBIX_PASS="Password"
NETBOX_HOST="https://netbox.local"
NETBOX_TOKEN="secrettoken"
```

Or, you can use a Zabbix API token to login instead of using a username and
password. In that case `ZABBIX_USER` and `ZABBIX_PASS` will be ignored.

```bash
ZABBIX_TOKEN=othersecrettoken
```

If you are using custom SSL certificates for NetBox and/or Zabbix, you can set
the following environment variable to the path of your CA bundle file:

```sh
export REQUESTS_CA_BUNDLE=/path/to/your/ca-bundle.crt
```

### NetBox custom fields

Use the following custom fields in NetBox (if you are using config context for
the template information then the zabbix_template field is not required):

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

You can make the `zabbix_hostid` field hidden or read-only to prevent human
intervention.

This is optional, but there may be cases where you want to leave it 
read-write in the UI. For example to manually change or clear the ID and re-run a sync.

## Virtual Machine (VM) Syncing

In order to use VM syncing, make sure that the `zabbix_id` custom field is also
present on Virtual machine objects in NetBox.

Use the `config.py` file and set the `sync_vms` variable to `True`.

You can set the `vm_hostgroup_format` variable to a customizable value for VM
hostgroups. The default is `cluster_type/cluster/role`.

To enable filtering for VM's, check the `nb_vm_filter` variable out. It works
the same as with the device filter (see documentation under "Hostgroup layout").
Note that not all filtering capabilities and properties of devices are
applicable to VM's and vice-versa. Check the NetBox API documentation to see
which filtering options are available for each object type.

## Config file

### Hostgroup

Setting the `create_hostgroups` variable to `False` requires manual hostgroup
creation for devices in a new category. I would recommend setting this variable
to `True` since leaving it on `False` results in a lot of manual work.

The format can be set with the `hostgroup_format` variable for devices and
`vm_hostgroup_format` for virtual machines.

Any nested parent hostgroups will also be created automatically. For instance
the region `Berlin` with parent region `Germany` will create the hostgroup
`Germany/Berlin`.

Make sure that the Zabbix user has proper permissions to create hosts. The
hostgroups are in a nested format. This means that proper permissions only need
to be applied to the site name hostgroup and cascaded to any child hostgroups.

#### Layout

The default hostgroup layout is "site/manufacturer/device_role". You can change
this behaviour with the hostgroup_format variable. The following values can be
used:

**Both devices and virtual machines**

| name          | description                                                                          |
| ------------- | ------------------------------------------------------------------------------------ |
| role          | Role name of a device or VM                                                          |
| region        | The region name                                                                      |
| site          | Site name                                                                            |
| site_group    | Site group name                                                                      |
| tenant        | Tenant name                                                                          |
| tenant_group  | Tenant group name                                                                    |
| platform      | Software platform of a device or VM                                                  |
| custom fields | See the section "Layout -> Custom Fields" to use custom fields as hostgroup variable |

**Only for devices**

| name         | description              |
| ------------ | ------------------------ |
| location     | The device location name |
| manufacturer | Device manufacturer name |
| rack         | Rack                     |

**Only for VMs**

| name         | description      |
| ------------ | ---------------  |
| cluster      | VM cluster name  |
| cluster_type | VM cluster type  |
| device       | parent device    |

You can specify the value separated by a "/" like so:

```python
hostgroup_format = "tenant/site/location/role"
```

You can also provice a list of groups like so:

```python
hostgroup_format = ["region/site_group/site", "role", "tenant_group/tenant"]
``` 


**Group traversal**

The default behaviour for `region` is to only use the directly assigned region
in the rendered hostgroup name. However, by setting `traverse_region` to `True`
in `config.py` the script will render a full region path of all parent regions
for the hostgroup name. `traverse_site_groups` controls the same behaviour for
site_groups.

**Hardcoded text** 

You can add hardcoded text in the hostgroup format by using quotes, this will
insert the literal text:

```python
hostgroup_format = "'MyDevices'/location/role"
```

In this case, the prefix MyDevices will be used for all generated groups.

**Custom fields**

You can use the value of custom fields for hostgroup generation. This allows
more freedom and even allows a full static mapping instead of a dynamic rendered
hostgroup name.

For instance a custom field with the name `mycustomfieldname` and type string
has the following values for 2 devices:

```
Device A has the value Train for custom field mycustomfieldname.
Device B has the value Bus for custom field mycustomfieldname.
Both devices are located in the site Paris.
```

With the hostgroup format `site/mycustomfieldname` the following hostgroups will
be generated:

```
Device A: Paris/Train
Device B: Paris/Bus
```

**Empty variables or hostgroups**

Should the content of a variable be empty, then the hostgroup position is
skipped.

For example, consider the following scenario with 2 devices, both the same
device type and site. One of them is linked to a tenant, the other one does not
have a relationship with a tenant.

- Device_role: PDU
- Site: HQ-AMS

```python
hostgroup_format = "site/tenant/role"
```

When running the script like above, the following hostgroup (HG) will be
generated for both hosts:

- Device A with no relationship with a tenant: HQ-AMS/PDU
- Device B with a relationship to tenant "Fork Industries": HQ-AMS/Fork
  Industries/PDU

The same logic applies to custom fields being used in the HG format:

```python
hostgroup_format = "site/mycustomfieldname"
```

For device A with the value "ABC123" in the custom field "mycustomfieldname" ->
HQ-AMS/ABC123 For a device which does not have a value in the custom field
"mycustomfieldname" -> HQ-AMS

Should there be a scenario where a custom field does not have a value under a
device, and the HG format only uses this single variable, then this will result
in an error:

```
hostgroup_format = "mycustomfieldname"

NetBox-Zabbix-sync - ERROR - ESXI1 has no reliable hostgroup. This is most likely due to the use of custom fields that are empty.
```

### Device status

By setting a status on a NetBox device you determine how the host is added (or
updated) in Zabbix. There are, by default, 3 options:

- Delete the host from Zabbix (triggered by NetBox status "Decommissioning" and
  "Inventory")
- Create the host in Zabbix but with a disabled status (Trigger by "Offline",
  "Planned", "Staged" and "Failed")
- Create the host in Zabbix with an enabled status (For now only enabled with
  the "Active" status)

You can modify this behaviour by changing the following list variables in the
script:

- `zabbix_device_removal`
- `zabbix_device_disable`

### Zabbix Inventory

This script allows you to enable the inventory on managed Zabbix hosts and sync
NetBox device properties to the specified inventory fields. To map NetBox
information to NetBox inventory fields, set `inventory_sync` to `True`.

You can set the inventory mode to "disabled", "manual" or "automatic" with the
`inventory_mode` variable. See
[Zabbix Manual](https://www.zabbix.com/documentation/current/en/manual/config/hosts/inventory#building-inventory)
for more information about the modes.

Use the `device_inventory_map` variable to map which NetBox properties are used in
which Zabbix Inventory fields. For nested properties, you can use the '/'
seperator. For example, the following map will assign the custom field
'mycustomfield' to the 'alias' Zabbix inventory field:

For Virtual Machines, use `vm_inventory_map`.

```python
inventory_sync = True
inventory_mode = "manual"
device_inventory_map = {"custom_fields/mycustomfield/name": "alias"}
vm_inventory_map = {"custom_fields/mycustomfield/name": "alias"}
```

See `config.py.example` for an extensive example map. Any Zabbix Inventory fields
that are not included in the map will not be touched by the script, so you can
safely add manual values or use items to automatically add values to other
fields.

### Template source

You can either use a NetBox device type custom field or NetBox config context
for the Zabbix template information.

Using a custom field allows for only one template. You can assign multiple
templates to one host using the config context source. Should you make use of an
advanced templating structure with lots of nesting then i would recommend
sticking to the custom field.

You can change the behaviour in the config file. By default this setting is
false but you can set it to true to use config context:

```python
templates_config_context = True
```

After that make sure that for each host there is at least one template defined
in the config context in this format:

```json
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

You can also opt for the default device type custom field behaviour but with the
added benefit of overwriting the template should a device in NetBox have a
device specific context defined. In this case the device specific context
template(s) will take priority over the device type custom field template.

```python
templates_config_context_overrule = True
```

### Tags

This script can sync host tags to your Zabbix hosts for use in filtering,
SLA calculations and event correlation.

Tags can be synced from the following sources:

1. NetBox device/vm tags
2. NetBox config context
3. NetBox fields

Syncing tags will override any tags that were set manually on the host,
making NetBox the single source-of-truth for managing tags.

To enable syncing, turn on tag_sync in the config file.
By default, this script will modify tag names and tag values to lowercase.
You can change this behaviour by setting tag_lower to False.

```python
tag_sync = True
tag_lower = True
```

#### Device tags

As NetBox doesn't follow the tag/value pattern for tags, we will need a tag
name set to register the netbox tags.

By default the tag name is "NetBox", but you can change this to whatever you want.
The value for the tag can be set to 'name', 'display', or 'slug', which refers to the property of the NetBox tag object that will be used as the value in Zabbix.

```python
tag_name = 'NetBox'
tag_value = 'name'
```

#### Config context

You can supply custom tags via config context by adding the following:

```json
{
    "zabbix": {
        "tags": [
            {
                "MyTagName": "MyTagValue"
            },
            {
                "environment": "production"
            }
        ],
    }
}
```

This will allow you to assign tags based on the config context rules.

#### NetBox Field

NetBox field can also be used as input for tags, just like inventory and usermacros.
To enable syncing from fields, make sure to configure a `device_tag_map` and/or a `vm_tag_map`.

```python
device_tag_map = {"site/name": "site",
                  "rack/name": "rack",
                  "platform/name": "target"}

vm_tag_map = {"site/name": "site",
              "cluster/name": "cluster",
              "platform/name": "target"}
```

To turn off field syncing, set the maps to empty dictionaries:

```python
device_tag_map = {}
vm_tag_map = {}
```


### Usermacros

You can choose to use NetBox as a source for Host usermacros by 
enabling the following option in the configuration file:

```python
usermacro_sync = True
```

Please be advised that enabling this option will _clear_ any usermacros
manually set on the managed hosts and override them with the usermacros
from NetBox.

There are two NetBox sources that can be used to populate usermacros:

1. NetBox config context
2. NetBox fields

#### Config context

By defining a dictionary `usermacros` within the `zabbix` key in 
config context, you can dynamically assign usermacro values based on 
anything that you can target based on 
[config contexts](https://netboxlabs.com/docs/netbox/en/stable/features/context-data/)
within NetBox.

Through this method, it is possible to define the following types of usermacros:

1. Text
2. Secret
3. Vault

The default macro type is text if no `type` and `value` have been set.
It is also possible to create usermacros with
[context](https://www.zabbix.com/documentation/7.0/en/manual/config/macros/user_macros_context).

Examples:

```json
{
    "zabbix": {
        "usermacros": {
            "{$USER_MACRO}": "test value",
            "{$CONTEXT_MACRO:\"test\"}": "test value",
            "{$CONTEXT_REGEX_MACRO:regex:\".*\"}": "test value",
            "{$SECRET_MACRO}": {
                "type": "secret",
                "value": "PaSsPhRaSe"
            },
            "{$VAULT_MACRO}": {
                "type": "vault",
                "value": "secret/vmware:password"
            },
            "{$USER_MACRO2}": {
                "type": "text",
                "value": "another test value"
            }
        }
    }
}

```

Please be aware that secret usermacros are only synced _once_ by default.
This is the default behavior because Zabbix API won't return the value of 
secrets so the script cannot compare the values with those set in NetBox.

If you update a secret usermacro value, just remove the value from the host
in Zabbix and the new value will be synced during the next run.

Alternatively, you can set the following option in the config file:

```python
usermacro_sync = "full"
```

This will force a full usermacro sync on every run on hosts that have secret usermacros set.
That way, you will know for sure the secret values are always up to date.

Keep in mind that NetBox will show your secrets in plain text. 
If true secrecy is required, consider switching to
[vault](https://www.zabbix.com/documentation/current/en/manual/config/macros/secret_macros#vault-secret) 
usermacros.

#### Netbox Fields

To use NetBox fields as a source for usermacros, you will need to set up usermacro maps
for devices and/or virtual machines in the configuration file.
This method only supports `text` type usermacros.

For example:

```python
usermacro_sync = True
device_usermacro_map = {"serial": "{$HW_SERIAL}",
                        "role/name": "{$DEV_ROLE}", 
                        "url": "{$NB_URL}",
                        "id": "{$NB_ID}"}
vm_usermacro_map = {"memory": "{$TOTAL_MEMORY}",
                    "role/name": "{$DEV_ROLE}", 
                    "url": "{$NB_URL}",
                    "id": "{$NB_ID}"}
```



## Permissions

### NetBox

Make sure that the NetBox user has proper permissions for device read and modify
(modify to set the Zabbix HostID custom field) operations. The user should also
have read-only access to the device types.

### Zabbix

Make sure that the Zabbix user has permissions to read hostgroups and proxy
servers. The user should have full rights on creating, modifying and deleting
hosts.

If you want to automatically create hostgroups then the create permission on
host-groups should also be applied.

### Custom links

To make the user experience easier you could add a custom link that redirects
users to the Zabbix latest data.

```
* Name: zabbix_latestData
* Text: {% if object.cf["zabbix_hostid"] %}Show host in Zabbix{% endif %}
* URL: http://myzabbixserver.local/zabbix.php?action=latest.view&hostids[]={{ object.cf["zabbix_hostid"] }}
```

## Running the script

```
python3 netbox_zabbix_sync.py
```

### Flags

| Flag | Option    | Description                           |
| ---- | --------- | ------------------------------------- |
| -v   | verbose   | Log with info on.                     |
| -vv  | debug     | Log with debugging on.                |
| -vvv | debug-all | Log with debugging on for all modules |

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

It is now possible to specify proxy groups with the introduction of Proxy groups
in Zabbix 7. Specifying a group in the config context on older Zabbix releases
will have no impact and the script will ignore the statement.

```json
{
    "zabbix": {
        "proxy_group": "yourawesomeproxygroup.local"
    }
}
```

The script will prefer groups when specifying both a proxy and group. This is
done with the assumption that groups are more resilient and HA ready, making it
a more logical choice to use for proxy linkage. This also makes migrating from a
proxy to proxy group easier since the group take priority over the individual
proxy.

```json
{
    "zabbix": {
        "proxy": "yourawesomeproxy.local",
        "proxy_group": "yourawesomeproxygroup.local"
    }
}
```

In the example above the host will use the group on Zabbix 7. On Zabbix 6 and
below the host will use the proxy. Zabbix 7 will use the proxy value when
omitting the proxy_group value.

### Set interface parameters within NetBox

When adding a new device, you can set the interface type with custom context. By
default, the following configuration is applied when no config context is
provided:

- SNMPv2
- UDP 161
- Bulk requests enabled
- SNMP community: {$SNMP_COMMUNITY}

Due to Zabbix limitations of changing interface type with a linked template,
changing the interface type from within NetBox is not supported and the script
will generate an error.

For example, when changing a SNMP interface to an Agent interface:

```
NetBox-Zabbix-sync - WARNING - Device: Interface OUT of sync.
NetBox-Zabbix-sync - ERROR - Device: changing interface type to 1 is not supported.
```

To configure the interface parameters you'll need to use custom context. Custom
context was used to make this script as customizable as possible for each
environment. For example, you could:

- Set the custom context directly on a device
- Set the custom context on a tag, which you would add to a device (for
  instance, SNMPv3)
- Set the custom context on a device role
- Set the custom context on a site or region

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

I would recommend using usermacros for sensitive data such as community strings
since the data in NetBox is plain-text.

> **_NOTE:_** Not all SNMP data is required for a working configuration.
> [The following parameters are allowed](https://www.zabbix.com/documentation/current/manual/api/reference/hostinterface/object#details_tag "The following parameters are allowed") but
> are not all required, depending on your environment.




