"""A collection of tools used by several classes"""

def convert_recordset(recordset):
    """ Converts netbox RedcordSet to list of dicts. """
    recordlist = []
    for record in recordset:
        recordlist.append(record.__dict__)
    return recordlist

def build_path(endpoint, list_of_dicts):
    """
    Builds a path list of related parent/child items.
    This can be used to generate a joinable list to
    be used in hostgroups.
    """
    item_path = []
    itemlist = [i for i in list_of_dicts if i['name'] == endpoint]
    item = itemlist[0] if len(itemlist) == 1 else None
    item_path.append(item['name'])
    while item['_depth'] > 0:
        itemlist = [i for i in list_of_dicts if i['name'] == str(item['parent'])]
        item = itemlist[0] if len(itemlist) == 1 else None
        item_path.append(item['name'])
    item_path.reverse()
    return item_path

def proxy_prepper(proxy_list, proxy_group_list):
    """
    Function that takes 2 lists and converts them using a
    standardized format for further processing.
    """
    output = []
    for proxy in proxy_list:
        proxy["type"] = "proxy"
        proxy["id"] = proxy["proxyid"]
        proxy["idtype"] = "proxyid"
        proxy["monitored_by"] = 1
        output.append(proxy)
    for group in proxy_group_list:
        group["type"] = "proxy_group"
        group["id"] = group["proxy_groupid"]
        group["idtype"] = "proxy_groupid"
        group["monitored_by"] = 2
        output.append(group)
    return output

def field_mapper(host, mapper, nbdevice, logger):
    """
    Maps NetBox field data to Zabbix properties.
    Used for Inventory, Usermacros and Tag mappings.
    """
    data={}
    # Let's build an dict for each property in the map
    for nb_field, zbx_field in mapper.items():
        field_list = nb_field.split("/") # convert str to list based on delimiter
        # start at the base of the dict...
        value = nbdevice
        # ... and step through the dict till we find the needed value
        for item in field_list:
            value = value[item] if value else None
        # Check if the result is usable and expected
        # We want to apply any int or float 0 values,
        # even if python thinks those are empty.
        if ((value and isinstance(value, int | float | str )) or
             (isinstance(value, int | float) and int(value) ==0)):
            data[zbx_field] = str(value)
        elif not value:
            # empty value should just be an empty string for API compatibility
            logger.debug(f"Host {host}: NetBox lookup for "
                              f"'{nb_field}' returned an empty value")
            data[zbx_field] = ""
        else:
            # Value is not a string or numeral, probably not what the user expected.
            logger.error(f"Host {host}: Lookup for '{nb_field}'"
                              " returned an unexpected type: it will be skipped.")
    logger.debug(f"Host {host}: Field mapping complete. "
                     f"Mapped {len(list(filter(None, data.values())))} field(s)")
    return data
