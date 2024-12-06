"""Module for all hostgroup related code"""
from logging import getLogger
from modules.exceptions import HostgroupError
from modules.tools import build_path

class Hostgroup():
    """Hostgroup class for devices and VM's
    Takes type (vm or dev) and NB object"""
    def __init__(self, obj_type, nb_obj, version, logger=None):
        self.logger = logger if logger else getLogger(__name__)
        if obj_type not in ("vm", "dev"):
            msg = f"Unable to create hostgroup with type {type}"
            self.logger.error()
            raise HostgroupError(msg)
        self.type = str(obj_type)
        self.nb = nb_obj
        self.name = self.nb.name
        self.nb_version = version
        # Used for nested data objects
        self.nested_objects = {}
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
                    format_options["region"] = self.generate_parents("region",
                                                                     str(self.nb.site.region))
                if self.nb.site.group:
                    format_options["site_group"] = self.generate_parents("site_group",
                                                                         str(self.nb.site.group))
            format_options["role"] = role
            format_options["site"] = self.nb.site.name if self.nb.site else None
            format_options["tenant"] = str(self.nb.tenant) if self.nb.tenant else None
            format_options["tenant_group"] = str(self.nb.tenant.group) if self.nb.tenant else None
            format_options["platform"] = self.nb.platform.name if self.nb.platform else None
        # Variables only applicable for devices
        if self.type == "dev":
            format_options["manufacturer"] = self.nb.device_type.manufacturer.name
            format_options["location"] = str(self.nb.location) if self.nb.location else None
        # Variables only applicable for VM's
        if self.type == "vm":
            # Check if a cluster is configured. Could also be configured in a site.
            if self.nb.cluster:
                format_options["cluster"] = self.nb.cluster.name
                format_options["cluster_type"] = self.nb.cluster.type.name

        self.format_options = format_options

    def set_nesting(self, nested_sitegroup_flag, nested_region_flag,
                    nb_groups, nb_regions):
        """Set nesting options for this Hostgroup"""
        self.nested_objects = {"site_group": {"flag": nested_sitegroup_flag, "data": nb_groups},
                               "region": {"flag": nested_region_flag, "data": nb_regions}}

    def generate(self, hg_format=None):
        """Generate hostgroup based on a provided format"""
        # Set format to default in case its not specified
        if not hg_format:
            hg_format = "site/manufacturer/role" if self.type == "dev" else "cluster/role"
        # Split all given names
        hg_output = []
        hg_items = hg_format.split("/")
        for hg_item in hg_items:
            # Check if requested data is available as option for this host
            if hg_item not in self.format_options:
                # Check if a custom field exists with this name
                cf_data = self.custom_field_lookup(hg_item)
                # CF does not exist
                if not cf_data["result"]:
                    msg = (f"Unable to generate hostgroup for host {self.name}. "
                           f"Item type {hg_item} not supported.")
                    self.logger.error(msg)
                    raise HostgroupError(msg)
                # CF data is populated
                if cf_data["cf"]:
                    hg_output.append(cf_data["cf"])
                continue
            # Check if there is a value associated to the variable.
            # For instance, if a device has no location, do not use it with hostgroup calculation
            hostgroup_value = self.format_options[hg_item]
            if hostgroup_value:
                hg_output.append(hostgroup_value)
        # Check if the hostgroup is populated with at least one item.
        if bool(hg_output):
            return "/".join(hg_output)
        msg = (f"Unable to generate hostgroup for host {self.name}."
               " Not enough valid items. This is most likely"
               " due to the use of custom fields that are empty"
               " or an invalid hostgroup format.")
        self.logger.error(msg)
        raise HostgroupError(msg)

    def list_formatoptions(self):
        """
        Function to easily troubleshoot which values
        are generated for a specific device or VM.
        """
        print(f"The following options are available for host {self.name}")
        for option_type, value in self.format_options.items():
            if value is not None:
                print(f"{option_type} - {value}")
        print("The following options are not available")
        for option_type, value in self.format_options.items():
            if value is None:
                print(f"{option_type}")

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
        if not nest_type in self.nested_objects:
            return child_object
        # If the nested flag is True, perform parent calculation
        if self.nested_objects[nest_type]["flag"]:
            final_nested_object = build_path(child_object, self.nested_objects[nest_type]["data"])
            return "/".join(final_nested_object)
        # Nesting is not allowed for this object. Return child_object
        return child_object
