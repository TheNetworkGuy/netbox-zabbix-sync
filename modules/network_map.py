# pylint: disable=invalid-name, logging-not-lazy, too-many-locals, logging-fstring-interpolation, too-many-lines, too-many-public-methods, duplicate-code
"""
Device specific handeling for NetBox to Zabbix
"""

from logging import getLogger
#from operator import itemgetter
#from re import search
from typing import Any

from pprint import pprint

from pynetbox import RequestError as NetboxRequestError
from zabbix_utils import APIRequestError
import igraph as ig

from modules.config import load_config
from modules.exceptions import (
    SyncExternalError,
    SyncInventoryError,
)
#from modules.tags import ZabbixTags
#from modules.tools import (
#    cf_to_string,
#    field_mapper,
#    remove_duplicates,
#    sanatize_log_output,
#)

config = load_config()


class ZabbixMap:
    # pylint: disable=too-many-instance-attributes, too-many-arguments, too-many-positional-arguments
    """
    Represents Zabbix Network Map.
    """

    def __init__(
        self, nb, devices, zabbix, netbox, nb_journal_class, nb_version, journal=None, logger=None
    ):
        self.nb = nb
        self.id = nb.id
        self.devices = devices
        self.edges = []
        self.edges_ints = []
        self.name = nb.name
        self.graph = None
        self.layout = config['map_layout']
        self.width = config['map_width']
        self.height = config['map_height']
        self.border = config['map_border']
        self.bbox = None
        self.visible_name = None
        self.status = nb.status.label
        self.zabbix = zabbix
        self.netbox = netbox
        self.zabbix_id = None
        self.nb_api_version = nb_version
        self.tenant = nb.tenant
        self.map = {}
        self.journal = journal
        self.nb_journals = nb_journal_class
        self.logger = logger if logger else getLogger(__name__)
        self._setBasics()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    def _setBasics(self):
        """
        Sets basic map information
        """
        # Check if site has custom field for ZBX ID
        if config["map_cf"] in self.nb.custom_fields:
            self.zabbix_id = self.nb.custom_fields[config["map_cf"]]
        else:
            e = f"Site {self.name}: Custom field {config['map_cf']} not present"
            self.logger.error(e)
            raise SyncInventoryError(e)
        self.bbox = (self.width - (self.border * 2), self.height - (self.border * 2))

        # Generate plot using igraph
        self.buildEdges(self.findConnections())
        self.buildGraph()

        # Generate Zabbix Map
        if self.buildZabbixMap():
            if not self.zabbix_id:
               self.createZabbixMap()
            else:
               self.updateZabbixMap()
        return True

    def findConnections(self):
        """
        Finds connections between devices in NetBox that have been synced to Zabbix.
        """
        connections = []
        for device in self.devices:
            self.logger.info("Processing device '%s'.", device.name)
            myindex = self.devices.index(device)
            interfaces = []

            # If we honour clustering, we need to grab interfaces from the VC master,
            # directy from the device otherwise.
            if config['clustering'] and device.virtual_chassis:
                interfaces = list(self.netbox.dcim.interfaces.filter(virtual_chassis_id=device.virtual_chassis.id))
            else:
                interfaces = list(self.netbox.dcim.interfaces.filter(device_id=device.id))
            self.logger.debug("Found %s interfaces.", len(interfaces))

            # Loop through VC/device interfaces to find peers within the site that are also present in Zabbix.    
            for interface in interfaces:
                connection = {}
                peerindex = None
                # Only process connected interfaces
                if interface.connected_endpoints:
                    for peer in interface.connected_endpoints:
                        if peerindex:
                            break
                        for site_device in self.devices:
                            if site_device.id == peer.device.id:
                                peerindex = self.devices.index(site_device)
                                break
                # If we've found a connection with a Zabbix host, continue processing
                if peerindex or peerindex==0:
                    self.logger.debug("Found connection: '%s' (%s) -> '%s' (%s)", 
                                      device.name, interface.name, 
                                      peer.device.name, peer.name)
                    connection = {'a': {'name':   device.name,
                                              'id':     device.id,
                                              'int':    interface.name,
                                              'int_id': interface.id,
                                              'index':  myindex
                                              },
                                        'b': {'name':   peer.device.name,
                                              'id':     peer.device.id,
                                              'int':    peer.name,
                                              'int_id': peer.id,
                                              'index':  peerindex
                                             }
                                        }
                    # Check if we already found the reverse connection, 
                    # if so we ignore this connection.
                    rev_connection = {'a': connection['b'], 'b': connection['a']}
                    if rev_connection in connections:
                        self.logger.debug("Reverse connection was already mapped, skipping!")
                    else:
                        connections.append(connection)

        self.logger.info("Registered %s connections in this site.", len(connections))
        return connections

    def buildEdges(self, connections):
        """
        Converts connections to iGraph edges
        """ 
        for c in connections:
            self.edges.append((c['a']['index'],c['b']['index']))
            self.edges_ints.append((c['a']['int'],c['b']['int']))
        return True

    def buildGraph(self):
        """
        Build graph based on NetBox connection info.
        """
        self.logger.info("Plotting '%s' graph with %s devices and %s connections.", 
                         self.layout, len(self.devices), len(self.edges))
        self.graph = ig.Graph(len(self.devices),self.edges)
        self.graph.vs['name'] = [name for name in self.devices]
        self.graph.vs['label'] = self.graph.vs['name']
        self.graph.es['int'] = self.edges_ints
        zabbix_ids = []
        for device in self.devices:
            zabbix_ids.append(device.custom_fields[config['device_cf']])
        self.graph.vs['zabbix_id'] = zabbix_ids
        layout = self.graph.layout(self.layout)
        layout.fit_into(bbox=self.bbox)
        
        # Debug, needs to be removed at some point 
        out = ig.plot(self.graph, layout=layout)
        out.save('./debug/' + self.name + '.png')

        # Calculate X and Y coords for each device
        self.graph.vs['x'] = [
            int(coord[0]) + self.border for coord in layout.coords
        ]
        self.graph.vs['y'] = [
            int(coord[1]) + self.border for coord in layout.coords
        ]
        for d in self.graph.vs:
            self.logger.debug("Device '%s' coords: (x: %s, y: %s)", d['name'], d['x'], d['y'])
        return True    

    def buildZabbixMap(self):
        """
        Build Zabbix map from iGraph
        """
        # Set Map properties
        self.map = {}
        self.map['height'] = self.height
        self.map['width'] = self.width
        self.map['name'] = self.name
        self.map['expand_macros'] = 1
        self.map['expandproblem'] = 0
        self.map['markelements'] = 1
        self.map['severity_min'] = 2
        self.map['show_unack'] = 2
        self.map['label_type'] = 0
        self.map['backgroundid'] = None
        if self.zabbix_id:
            self.map['sysmapid'] = self.zabbix_id

        # Add map elements
        self.map['selements'] = self.generateElements()
        if not self.map['selements']:
            logger.error("Site '%s' map does not contain any host elements.", self.name)
            return False

        # add element links
        self.map['links'] = self.generateLinks()

        return True

    def createZabbixMap(self):
        """
        Create new map in Zabbix
        """
        if self.map:
            try:
                m = self.zabbix.map.create(**self.map)
                self.zabbix_id = m["sysmapids"][0]
            except APIRequestError as e:
                msg = f"Map {self.name}: Failed to create. Zabbix returned {str(e)}."
                self.logger.error(msg)
                raise SyncExternalError(msg) from e
            # Set NetBox custom field to ID value.
            self.nb.custom_fields[config["map_cf"]] = int(self.zabbix_id)
            self.nb.save()
            msg = f"Map '{self.name}': Created site map in Zabbix. (ID:{self.zabbix_id})"
            self.logger.info(msg)
#            self.create_journal_entry("success", msg)
            return True
        return False
   
    def updateZabbixMap(self):
        """
        Update existing map in Zabbix
        """
        if self.map:
            try:
                m = self.zabbix.map.update(**self.map)
            except APIRequestError as e:
                msg = f"Map {self.name}: Failed to update. Zabbix returned {str(e)}."
                self.logger.error(msg)
                raise SyncExternalError(msg) from e
            # Set NetBox custom field to ID value.
            msg = f"Map '{self.name}': Updated site map in Zabbix. (ID:{self.zabbix_id})"
            self.logger.info(msg)
#            self.create_journal_entry("success", msg)
            return True
        return False

    def generateElements(self):
        """ 
        Generates Zabbix Host elements on the map.
        """
        if self.graph.vs:
            selements=[]
            for e in self.graph.vs:
                element={}
                element['selementid'] = e.index+1
                element['iconid_off'] = 57
                element['elements'] =  [{'hostid': e['zabbix_id']}]  
                element['label'] = '{HOST.NAME}\nping: {?last(//icmppingsec)}'
                element['elementtype'] = 0
                element['x'] = e['x'] 
                element['y'] = e['y'] 
                selements.append(element)
            return selements
        return False

    def generateLinks(self):
        """
        Generates links between Zabbix map elements.
        """
        links = []
        # add element links
        if self.graph.es:
            if 'selements' in self.map and self.map['selements']:
                for l in self.graph.es:
                    link = {}
                    link['selementid1'] = l.source+1
                    link['selementid2'] = l.target+1
                    links.append(link)
                return links
            else:
                self.logger.error("Site map '%s' has no elements, cannot create links.", self.name)
        else:
            self.logger.info("Site map '%s' has no element links, make some connections in NetBox.", self.name)
        return False
#    def createInZabbix(
#        self,
#        groups,
#        templates,
#        proxies,
#        description="Host added by NetBox sync script.",
#    ):
#        """
#        Creates Zabbix host object with parameters from NetBox object.
#        """
#        # Check if hostname is already present in Zabbix
#        if not self._zabbixHostnameExists():
#            # Set group and template ID's for host
#            if not self.setZabbixGroupID(groups):
#                e = (
#                    f"Unable to find group '{self.hostgroup}' "
#                    f"for host {self.name} in Zabbix."
#                )
#                self.logger.warning(e)
#                raise SyncInventoryError(e)
#            self.zbxTemplatePrepper(templates)
#            templateids = []
#            for template in self.zbx_templates:
#                templateids.append({"templateid": template["templateid"]})
#            # Set interface, group and template configuration
#            interfaces = self.setInterfaceDetails()
#            # Set Zabbix proxy if defined
#            self._setProxy(proxies)
#            # Set basic data for host creation
#            create_data = {
#                "host": self.name,
#                "name": self.visible_name,
#                "status": self.zabbix_state,
#                "interfaces": interfaces,
#                "groups": self.group_ids,
#                "templates": templateids,
#                "description": description,
#                "inventory_mode": self.inventory_mode,
#                "inventory": self.inventory,
#                "macros": self.usermacros,
#                "tags": self.tags,
#            }
#            # If a Zabbix proxy or Zabbix Proxy group has been defined
#            if self.zbxproxy:
#                # If a lower version than 7 is used, we can assume that
#                # the proxy is a normal proxy and not a proxy group
#                if not str(self.zabbix.version).startswith("7"):
#                    create_data["proxy_hostid"] = self.zbxproxy["id"]
#                else:
#                    # Configure either a proxy or proxy group
#                    create_data[self.zbxproxy["idtype"]] = self.zbxproxy["id"]
#                    create_data["monitored_by"] = self.zbxproxy["monitored_by"]
#            # Add host to Zabbix
#            try:
#                host = self.zabbix.host.create(**create_data)
#                self.zabbix_id = host["hostids"][0]
#            except APIRequestError as e:
#                msg = f"Host {self.name}: Couldn't create. Zabbix returned {str(e)}."
#                self.logger.error(msg)
#                raise SyncExternalError(msg) from e
#            # Set NetBox custom field to hostID value.
#            self.nb.custom_fields[config["device_cf"]] = int(self.zabbix_id)
#            self.nb.save()
#            msg = f"Host {self.name}: Created host in Zabbix. (ID:{self.zabbix_id})"
#            self.logger.info(msg)
#            self.create_journal_entry("success", msg)
#        else:
#            self.logger.error(
#                "Host %s: Unable to add to Zabbix. Host already present.", self.name
#            )