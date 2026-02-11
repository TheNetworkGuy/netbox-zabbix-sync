"""Module for all hostgroup related code"""

from logging import getLogger

from modules.exceptions import HostgroupError
from modules.tools import build_path, cf_to_string


class Hostgroup:
    """Hostgroup class for devices and VM's
    Takes type (vm or dev) and NB object"""

    # pylint: disable=too-many-arguments, disable=too-many-positional-arguments
    # pylint: disable=logging-fstring-interpolation
    def __init__(
        self,
        obj_type,
        nb_obj,
        version,
        logger=None,
        nested_sitegroup_flag=False,
        nested_region_flag=False,
        nb_regions=None,
        nb_groups=None,
    ):
        self.logger = logger if logger else getLogger(__name__)
        if obj_type not in ("vm", "dev"):
            msg = f"Unable to create hostgroup with type {type}"
            self.logger.error(msg)
            raise HostgroupError(msg)
        self.type = str(obj_type)
        self.nb = nb_obj
        self.name = self.nb.name
        self.nb_version = version
        # Used for nested data objects
        self.set_nesting(
            nested_sitegroup_flag, nested_region_flag, nb_groups, nb_regions
        )
        self._set_format_options()

    def __str__(self):
        return f"Hostgroup for {self.type} {self.name}"

    def __repr__(self):
        return self.__str__()

    def _set_format_options(self):
        """
        Set all available variables
        for hostgroup generation
        """
        format_options = {}
        # Set variables for both type of devices
        if self.type in ("vm", "dev"):
            # Role fix for NetBox <=3
            role = None
            if self.nb_version.startswith(("2", "3")) and self.type == "dev":
                role = self.nb.device_role.name if self.nb.device_role else None
            else:
                role = self.nb.role.name if self.nb.role else None
            # Add default formatting options
            # Check if a site is configured. A site is optional for VMs
            format_options["region"] = None
            format_options["site_group"] = None
            if self.nb.site:
                if self.nb.site.region:
                    format_options["region"] = self.generate_parents(
                        "region", str(self.nb.site.region)
                    )
                if self.nb.site.group:
                    format_options["site_group"] = self.generate_parents(
                        "site_group", str(self.nb.site.group)
                    )
            format_options["role"] = role
            format_options["site"] = self.nb.site.name if self.nb.site else None
            format_options["tenant"] = str(self.nb.tenant) if self.nb.tenant else None
            format_options["tenant_group"] = (
                str(self.nb.tenant.group) if self.nb.tenant else None
            )
            format_options["platform"] = (
                self.nb.platform.name if self.nb.platform else None
            )
        # Variables only applicable for devices
        if self.type == "dev":
            format_options["manufacturer"] = self.nb.device_type.manufacturer.name
            format_options["location"] = (
                str(self.nb.location) if self.nb.location else None
            )
            format_options["rack"] = self.nb.rack.name if self.nb.rack else None
        # Variables only applicable for VM's such as clusters
        if self.type == "vm" and self.nb.cluster:
            format_options["cluster"] = self.nb.cluster.name
            format_options["cluster_type"] = self.nb.cluster.type.name
        self.format_options = format_options
        self.logger.debug(
            "Host %s: Resolved properties for use in hostgroups: %s",
            self.name,
            self.format_options,
        )

    def set_nesting(
        self, nested_sitegroup_flag, nested_region_flag, nb_groups, nb_regions
    ):
        """Set nesting options for this Hostgroup"""
        self.nested_objects = {
            "site_group": {"flag": nested_sitegroup_flag, "data": nb_groups},
            "region": {"flag": nested_region_flag, "data": nb_regions},
        }

    def generate(self, hg_format):
        """Generate hostgroup based on a provided format"""
        # Split all given names
        hg_output = []
        hg_items = hg_format.split("/")
        for hg_item in hg_items:
            # Check if requested data is available as option for this host
            if hg_item not in self.format_options:
                # If the string is between quotes, use it as a literal in the hostgroup name
                minimum_length = 2
                if (
                    len(hg_item) > minimum_length
                    and hg_item[0] == hg_item[-1]
                    and hg_item[0] in ("'", '"')
                ):
                    hg_output.append(hg_item[1:-1])
                else:
                    # Check if a custom field exists with this name
                    cf_data = self.custom_field_lookup(hg_item)
                    # CF does not exist
                    if not cf_data["result"]:
                        msg = (
                            f"Unable to generate hostgroup for host {self.name}. "
                            f"Item type {hg_item} not supported."
                        )
                        self.logger.error(msg)
                        raise HostgroupError(msg)
                    # CF data is populated
                    if cf_data["cf"]:
                        hg_output.append(cf_to_string(cf_data["cf"]))
                continue
            # Check if there is a value associated to the variable.
            # For instance, if a device has no location, do not use it with hostgroup calculation
            hostgroup_value = self.format_options[hg_item]
            if hostgroup_value:
                hg_output.append(hostgroup_value)
            else:
                self.logger.info(
                    "Host %s: Used field '%s' has no value.", self.name, hg_item
                )
        # Check if the hostgroup is populated with at least one item.
        if bool(hg_output):
            return "/".join(hg_output)
        msg = (
            f"Host {self.name}: Generating hostgroup name for '{hg_format}' failed. "
            f"This is most likely due to fields that have no value."
        )
        self.logger.warning(msg)
        return None

    def custom_field_lookup(self, hg_category):
        """
        Checks if a valid custom field is present in NetBox.
        INPUT: Custom field name
        OUTPUT: dictionary with 'result' and 'cf' keys.
        """
        # Check if the custom field exists
        if hg_category not in self.nb.custom_fields:
            return {"result": False, "cf": None}
        # Checks if the custom field has been populated
        if not bool(self.nb.custom_fields[hg_category]):
            return {"result": True, "cf": None}
        # Custom field exists and is populated
        return {"result": True, "cf": self.nb.custom_fields[hg_category]}

    def generate_parents(self, nest_type, child_object):
        """
        Generates parent objects to implement nested regions / nested site groups
        INPUT: nest_type to set which type of nesting is going to be processed
        child_object: the name of the child object (for instance the last NB region)
        OUTPUT: STRING - Either the single child name or child and parents.
        """
        # Check if this type of nesting is supported.
        if nest_type not in self.nested_objects:
            return child_object
        # If the nested flag is True, perform parent calculation
        if self.nested_objects[nest_type]["flag"]:
            final_nested_object = build_path(
                child_object, self.nested_objects[nest_type]["data"]
            )
            return "/".join(final_nested_object)
        # Nesting is not allowed for this object. Return child_object
        return child_object
