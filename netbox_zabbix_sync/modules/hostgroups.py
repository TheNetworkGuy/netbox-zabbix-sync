"""Module for all hostgroup related code"""

from difflib import get_close_matches
from logging import getLogger
from typing import ClassVar, NamedTuple

from netbox_zabbix_sync.modules.exceptions import HostgroupError
from netbox_zabbix_sync.modules.tools import build_path, cf_to_string


class HostgroupType(NamedTuple):
    """A type of object that hostgroups are generated for"""

    # How the object is called in messages
    label: str
    # The config setting that holds the hostgroup format for this type
    setting: str
    # Variables that only this type of object has
    options: tuple[str, ...]


class HostgroupFormat:
    """
    The variables that can be used in a hostgroup format, per type of object,
    and the verification of a configured format against them.

    To add a type of object, add it to TYPES. To add a variable, add it to
    COMMON (every type) or to the options of the type that has it. Also
    resolve its value in `Hostgroup._set_format_options`.
    """

    # Variables that every type of object has
    COMMON = (
        "region",
        "site_group",
        "role",
        "site",
        "tenant",
        "tenant_group",
        "platform",
    )
    # The types of object, by the name used in `Hostgroup(obj_type, ...)`
    TYPES: ClassVar[dict[str, HostgroupType]] = {
        "dev": HostgroupType(
            label="device",
            setting="hostgroup_format",
            options=("manufacturer", "location", "rack"),
        ),
        "vm": HostgroupType(
            label="virtual machine",
            setting="vm_hostgroup_format",
            options=("cluster", "cluster_type"),
        ),
    }

    @classmethod
    def valid_options(cls, hg_type):
        """All variables that are valid for hg_type"""
        if hg_type not in cls.TYPES:
            msg = f"Unknown hostgroup type '{hg_type}'. Use one of {list(cls.TYPES)}."
            raise ValueError(msg)
        return (*cls.COMMON, *cls.TYPES[hg_type].options)

    @classmethod
    def verify(cls, hg_format, custom_fields=None, hg_type="dev", logger=None):
        """
        Verifies hostgroup field format.
        Raises a HostgroupError that explains why the first invalid item is invalid.

        custom_fields: the NetBox custom fields that can be used for hg_type
        """
        allowed_options = cls.valid_options(hg_type)
        cf_names = [cf.name for cf in custom_fields or []]
        hg_formats = hg_format if isinstance(hg_format, list) else [hg_format]
        for single_format in hg_formats:
            for hg_item in single_format.split("/"):
                if (
                    hg_item in allowed_options
                    or hg_item in cf_names
                    or cls.is_literal(hg_item)
                ):
                    continue
                e = cls.unsupported_reason(hg_item, single_format, hg_type, cf_names)
                if logger:
                    logger.warning(e)
                raise HostgroupError(e)

    @staticmethod
    def is_literal(hg_item):
        """
        True if hg_item is a literal: text between matching quotes, that is
        used as is in the hostgroup name. For instance 'Datacenter'
        """
        minimum_length = 2
        return (
            len(hg_item) > minimum_length
            and hg_item[0] == hg_item[-1]
            and hg_item[0] in ("'", '"')
        )

    @classmethod
    def unsupported_reason(cls, hg_item, hg_format, hg_type, custom_fields=()):
        """
        Explain why an item in a hostgroup format is not supported.
        Each check below returns a message for one specific cause,
        so the user knows exactly where to look.

        hg_item: the item that is not supported
        hg_format: the format it is part of, for instance 'site/role'
        hg_type: "dev" or "vm"
        custom_fields: names of the custom fields that can be used
        """
        obj_label = cls.TYPES[hg_type].label
        setting = cls.TYPES[hg_type].setting
        where = f"Item {hg_item!r} in {setting} '{hg_format}'"

        # Check 1: empty item, caused by a leading, trailing or double '/'
        if not hg_item:
            return (
                f"The {setting} '{hg_format}' contains an empty item. "
                f"Check for a leading, trailing or double '/'."
            )

        # Check 2: whitespace around the item
        if hg_item != hg_item.strip():
            return (
                f"{where} has leading or trailing whitespace. "
                f"Remove the spaces around the '/'."
            )

        # Check 3: item looks like a quoted literal, but the quotes are invalid
        if hg_item[0] in ("'", '"') or hg_item[-1] in ("'", '"'):
            return (
                f"{where} looks like a quoted literal, but is not valid. "
                f"A literal must start and end with the same quote character, "
                f"contain at least one character and cannot contain a '/'."
            )

        # Check 4: variable that exists, but for another type of object
        other_types = [
            other
            for other in cls.TYPES.values()
            if other is not cls.TYPES[hg_type] and hg_item in other.options
        ]
        if other_types:
            labels = " or ".join(f"{other.label}s" for other in other_types)
            settings = " or ".join(f"'{other.setting}'" for other in other_types)
            return (
                f"{where} is a hostgroup variable that is only available for "
                f"{labels}, not for {obj_label}s. Use it in {settings} instead."
            )

        # Check 5: typo or different capitalization of a known variable / custom field
        suggestion = cls._suggest_item(
            hg_item, [*cls.valid_options(hg_type), *custom_fields]
        )
        if suggestion:
            return (
                f"{where} is not a supported hostgroup variable and no custom "
                f"field with this name exists on this {obj_label}. "
                f"Did you mean '{suggestion}'?"
            )

        # Check 6: nothing matches, most likely a custom field that is not
        # assigned to this type of object in NetBox
        return (
            f"{where} is not a supported hostgroup variable and no custom field "
            f"with this name exists on this {obj_label}. If it is a custom field, "
            f"check in NetBox that it is assigned to the {obj_label} object type "
            f"and that its type is text, select or object."
        )

    @staticmethod
    def _suggest_item(hg_item, candidates):
        """Return the closest candidate to hg_item, if any"""
        by_lowercase = {candidate.lower(): candidate for candidate in candidates}
        if hg_item.lower() in by_lowercase:
            return by_lowercase[hg_item.lower()]
        matches = get_close_matches(hg_item, candidates, n=1)
        return matches[0] if matches else None


class Hostgroup:
    """Hostgroup class for devices and VM's
    Takes type (vm or dev) and NB object"""

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
        if obj_type not in HostgroupFormat.TYPES:
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
                str(self.nb.tenant.group)
                if self.nb.tenant and self.nb.tenant.group
                else None
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
        if self.type == "vm":
            format_options["cluster"] = (
                self.nb.cluster.name if self.nb.cluster else None
            )
            format_options["cluster_type"] = (
                self.nb.cluster.type.name if self.nb.cluster else None
            )
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
                if HostgroupFormat.is_literal(hg_item):
                    hg_output.append(hg_item[1:-1])
                else:
                    # Check if a custom field exists with this name
                    cf_data = self.custom_field_lookup(hg_item)
                    # CF does not exist
                    if not cf_data["result"]:
                        reason = HostgroupFormat.unsupported_reason(
                            hg_item, hg_format, self.type, self.nb.custom_fields
                        )
                        msg = (
                            f"Unable to generate hostgroup for host {self.name}. "
                            f"{reason}"
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
