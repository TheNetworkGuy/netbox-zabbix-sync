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

