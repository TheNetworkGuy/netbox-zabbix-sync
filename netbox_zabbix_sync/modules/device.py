"""
Device specific handeling for NetBox to Zabbix
"""

from pynetbox import RequestError as NetboxRequestError

from netbox_zabbix_sync.modules.exceptions import (
    SyncExternalError,
    SyncInventoryError,
)
from netbox_zabbix_sync.modules.host import Host


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

    def adopt_cluster_host(self, members) -> bool:
        """
        After a failover the new primary has no Zabbix ID, while the former
        primary still holds the ID of the cluster host. Move the ID to this
        device so the existing host and its history are kept.
        Returns True if an ID was transferred.
        """
        cf = self.config["device_cf"]
        holders = [m for m in members if m.id != self.id and m.custom_fields.get(cf)]
        if not holders:
            return False
        if len(holders) > 1:
            self.logger.warning(
                "Host %s: multiple cluster members have a Zabbix ID. "
                "Not transferring the cluster host.",
                self.name,
            )
            return False
        former = holders[0]
        zabbix_id = former.custom_fields[cf]
        try:
            self.nb.custom_fields[cf] = zabbix_id
            self.nb.save()
            former.custom_fields[cf] = None
            former.save()
        except NetboxRequestError as e:
            message = f"Host {self.name}: unable to transfer Zabbix ID: {e}."
            self.logger.error(message)
            raise SyncExternalError(message) from e
        self.zabbix_id = zabbix_id
        self.logger.info(
            "Host %s: took over Zabbix host %s from former primary %s.",
            self.name,
            zabbix_id,
            former.name,
        )
        self.create_journal_entry(
            "info", f"Took over Zabbix host {zabbix_id} from {former.name}"
        )
        return True
