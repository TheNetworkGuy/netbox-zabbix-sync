"""
Modules that set description of a host in Zabbix
"""

from datetime import datetime
from logging import getLogger
from re import findall as re_findall


class Description:
    """
    Class that generates the description for a host in Zabbix based on the configuration provided.

    INPUT:
    - netbox_object: The NetBox object that is being synced.
    - configuration: configuration of the syncer.
    Required keys in configuration:
        description: Can be "static", "dynamic" or a custom description with macros.
    - nb_version: The version of NetBox that is being used.
    """

    def __init__(self, netbox_object, configuration, nb_version, logger=None):
        self.netbox_object = netbox_object
        self.name = self.netbox_object.name
        self.configuration = configuration
        self.nb_version = nb_version
        self.logger = logger or getLogger(__name__)
        self._set_default_macro_values()
        self._set_defaults()

    def _set_default_macro_values(self):
        """
        Sets the default macro values for the description.
        """
        # Get the datetime format from the configuration,
        # or use the default format if not provided
        dt_format = self.configuration.get("description_dt_format", "%Y-%m-%d %H:%M:%S")
        # Set the datetime macro
        try:
            datetime_value = datetime.now().strftime(dt_format)
        except (ValueError, TypeError) as e:
            self.logger.warning(
                "Host %s: invalid datetime format '%s': %s. Using default format.",
                self.name,
                dt_format,
                e,
            )
            datetime_value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Set the owner macro
        owner = self.netbox_object.owner if self.nb_version >= "4.5" else ""
        # Set the macro list
        self.macros = {"{datetime}": datetime_value, "{owner}": owner}

    def _resolve_macros(self, description):
        """
        Takes a description and resolves the macro's in it.
        Returns the description with the macro's resolved.
        """
        # Find all macros in the description
        provided_macros = re_findall(r"\{\w+\}", description)
        # Go through all macros provided in the NB description
        for macro in provided_macros:
            # If the macro is in the list of default macro values
            if macro in self.macros:
                # Replace the macro in the description with the value of the macro
                description = description.replace(macro, str(self.macros[macro]))
            else:
                # One of the macro's is invalid.
                self.logger.warning(
                    "Host %s: macro %s is not valid. Failing back to default.",
                    self.name,
                    macro,
                )
                return False
        return description

    def _set_defaults(self):
        """
        Sets the default descriptions for the host.
        """
        self.defaults = {
            "static": "Host added by NetBox sync script.",
            "dynamic": (
                "Host by owner {owner} added by NetBox sync script on {datetime}."
            ),
        }

    def _custom_override(self):
        """
        Checks if the description is mentioned in the config context.
        """
        zabbix_config = self.netbox_object.config_context.get("zabbix")
        if zabbix_config and "description" in zabbix_config:
            return zabbix_config["description"]
        return False

    def generate(self):
        """
        Generates the description for the host.
        """
        # First: check if an override is present.
        config_context_description = self._custom_override()
        if config_context_description is not False:
            resolved = self._resolve_macros(config_context_description)
            return resolved if resolved else self.defaults["static"]
        # Override is not present: continue with config description
        description = ""
        if "description" not in self.configuration:
            # If no description config is provided, use default static
            return self.defaults["static"]
        if not self.configuration["description"]:
            # The configuration is set to False, meaning an empty description
            return description
        if self.configuration["description"] in self.defaults:
            # The description is one of the default options
            description = self.defaults[self.configuration["description"]]
        else:
            # The description is set to a custom description
            description = self.configuration["description"]
        # Resolve the macro's in the description
        final_description = self._resolve_macros(description)
        if final_description:
            return final_description
        return self.defaults["static"]
