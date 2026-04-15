"""
Device specific handeling for NetBox to Zabbix
"""

from logging import getLogger

from netbox_zabbix_sync.modules.exceptions import (
    SyncInventoryError,
)
from netbox_zabbix_sync.modules.host import Host
from netbox_zabbix_sync.modules.settings import load_config


class PhysicalDevice(Host):
    """
    Represents Network device.
    INPUT: (NetBox device class, ZabbixAPI class, journal flag, NB journal class)
    """

    def _inventory_map(self):
        """Use device inventory maps"""
        return self.config["device_inventory_map"]

    def _usermacro_map(self):
        """Use device inventory maps"""
        return self.config["device_usermacro_map"]

    def _tag_map(self):
        """Use device host tag maps"""
        return self.config["device_tag_map"]

    def is_cluster(self):
        """
        Checks if device is part of cluster.
        """
        return bool(self.nb.virtual_chassis)

    def get_cluster_master(self):
        """
        Returns chassis master ID.
        """
        if not self.is_cluster():
            e = (
                f"Unable to proces {self.name} for cluster calculation: "
                f"not part of a cluster."
            )
            self.logger.info(e)
            raise SyncInventoryError(e)
        if not self.nb.virtual_chassis.master:
            e = (
                f"{self.name} is part of a NetBox virtual chassis which does "
                "not have a master configured. Skipping for this reason."
            )
            self.logger.warning(e)
            raise SyncInventoryError(e)
        return self.nb.virtual_chassis.master.id

    def promote_primary_device(self):
        """
        If device is Primary in cluster,
        promote device name to the cluster name.
        Returns True if succesfull, returns False if device is secondary.
        """
        masterid = self.get_cluster_master()
        if masterid == self.id:
            self.logger.info(
                "Host %s is primary cluster member. Modifying hostname from %s to %s.",
                self.name,
                self.name,
                self.nb.virtual_chassis.name,
            )
            self.name = self.nb.virtual_chassis.name
            return True
        self.logger.info("Host %s is non-primary cluster member.", self.name)
        return False
