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
        self, nb, devices, zabbix, zabbix_backgrounds, bgid, iconid, iconmapid, 
        netbox, nb_journal_class, nb_version, journal=None, logger=None
    ):
        self.nb = nb
        self.id = nb.id
        self.devices = devices
        self.edges = []
        self.edges_ints = []
        self.name = str(config['map_name_prefix']) + nb.name + str(config['map_name_suffix'])
        self.graph = None
        self.layout = config['map_layout']
        self.width = int(config['map_width'])
        self.height = int(config['map_height'])
        self.border = int(config['map_border'])
        self.header_size = int(config['map_header_size']) if config['map_header_size'] else 0
        self.bbox = None
        self.visible_name = None
        self.status = nb.status.label
        self.zabbix = zabbix
        self.zabbix_backgrounds = zabbix_backgrounds
        self.bgid = bgid
        self.iconid = iconid
        self.iconmapid = iconmapid
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
            
        self.bbox = (self.width - (self.border * 2), self.height - (self.border * 2) - self.header_size)

        # Generate plot using igraph
        self.buildEdges(self.findConnections())
        self.buildGraph()
        
        # Generate Zabbix Map
        self.setBackground()
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
            int(coord[0]) + self.border/2 for coord in layout.coords
        ]
        self.graph.vs['y'] = [
            int(coord[1]) + self.border/2 + self.header_size for coord in layout.coords
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
        self.map['backgroundid'] = self.bgid
        self.map['iconmapid'] = self.iconmapid
        if self.zabbix_id:
            self.map['sysmapid'] = self.zabbix_id

        # Add map elements
        self.map['selements'] = self.generateElements()
        if not self.map['selements']:
            logger.error("Site '%s' map does not contain any host elements.", self.nb.name)
            return False

        # Add element links
        self.map['links'] = self.generateLinks()
    
        # Add header
        self.map['shapes'] = self.setHeader()

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
                element['iconid_off'] = self.iconid
                element['elements'] =  [{'hostid': e['zabbix_id']}]  
                element['label'] = '{HOST.NAME}\nping: {?last(//icmppingsec)}'
                element['elementtype'] = 0
                element['x'] = e['x'] 
                element['y'] = e['y'] 
                selements.append(element)
            self.logger.debug("Added %s host elements to Zabbix map.", len(selements))
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
                    idx = None
                    for i,e in enumerate(links):
                        if (l.source+1 == e['selementid1'] and 
                            l.target+1 == e['selementid2']):
                           self.logger.debug("Found duplicate link between elements %s and %s.", l.source+1, l.target+1)
                           idx = i
                           break
                    if idx is None: 
                        link['selementid1'] = l.source+1
                        link['selementid2'] = l.target+1
                        link['color'] = config['map_link_uni']
                        pprint(link)
                        links.append(link)
                    else:
                        links[idx]['drawtype'] = 2
                        links[idx]['color'] = config['map_link_multi']
                        links[idx]['label'] = "Multi"
                         
                self.logger.debug("Added %s links to Zabbix map.", len(links))
                #pprint(links)
                return links
            else:
                self.logger.error("Site map '%s' has no elements, cannot create links.", self.name)
        else:
            self.logger.info("Site map '%s' has no element links, make some connections in NetBox.", self.name)
        return []

    def setBackground(self):
        """
        Resolve dynamic map backgrounds based on site name.
        """
        if config['map_dynamic_bg']:
            dynbgid = (next(filter(lambda x: x['name'] == self.nb.name, self.zabbix_backgrounds), None))
            if dynbgid:
                self.bgid = dynbgid['imageid']
                self.logger.info("Site map '%s' is using dynamic background '%s'. (ID:%s)", 
                                 self.name, dynbgid['name'], dynbgid['imageid'])
            else:
                self.logger.debug("No dynamic background found for site map '%s'.", self.name)
        return self.bgid

    def setHeader(self):
        """
        Render map header.
        """
        shapes=[]
        if self.header_size:
            self.logger.debug("Adding header to map '%s'.", self.name)
            shape = {
                "text": self.nb.name,
                "font_size": int(self.header_size/2),
                "width": int(self.width),
                "height": int(self.header_size),
                "x": 0,
                "y": 0,
                "border_type": 0,
                "type": 0,
            } 
            shapes.append(shape)
        return shapes