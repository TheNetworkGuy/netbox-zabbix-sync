#!/usr/bin/env python3
# pylint: disable=too-many-instance-attributes, too-many-arguments, too-many-positional-arguments, logging-fstring-interpolation
"""
All of the Zabbix Usermacro related configuration
"""
from logging import getLogger
from re import match

from modules.tools import field_mapper, sanatize_log_output


class ZabbixUsermacros:
    """Class that represents Zabbix usermacros."""

    def __init__(self, nb, usermacro_map, usermacro_sync, logger=None, host=None):
        self.nb = nb
        self.name = host if host else nb.name
        self.usermacro_map = usermacro_map
        self.logger = logger if logger else getLogger(__name__)
        self.usermacros = {}
        self.usermacro_sync = usermacro_sync
        self.sync = False
        self.force_sync = False
        self._set_config()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    def _set_config(self):
        """
        Setup class
        """
        if str(self.usermacro_sync).lower() == "full":
            self.sync = True
            self.force_sync = True
        elif self.usermacro_sync:
            self.sync = True
        return True

    def validate_macro(self, macro_name):
        """
        Validates usermacro name
        """
        pattern = r"\{\$[A-Z0-9\._]*(\:.*)?\}"
        return match(pattern, macro_name)

    def render_macro(self, macro_name, macro_properties):
        """
        Renders a full usermacro from partial input
        """
        macro = {}
        macrotypes = {"text": 0, "secret": 1, "vault": 2}
        if self.validate_macro(macro_name):
            macro["macro"] = str(macro_name)
            if isinstance(macro_properties, dict):
                if not "value" in macro_properties:
                    self.logger.warning(f"Host {self.name}: Usermacro {macro_name} has "
                                        "no value in Netbox, skipping.")
                    return False
                macro["value"] = macro_properties["value"]

                if (
                    "type" in macro_properties
                    and macro_properties["type"].lower() in macrotypes
                ):
                    macro["type"] = str(macrotypes[macro_properties["type"]])
                else:
                    macro["type"] = str(0)

                if "description" in macro_properties and isinstance(
                    macro_properties["description"], str
                ):
                    macro["description"] = macro_properties["description"]
                else:
                    macro["description"] = ""

            elif isinstance(macro_properties, str) and macro_properties:
                macro["value"] = macro_properties
                macro["type"] = str(0)
                macro["description"] = ""

            else:
                self.logger.warning(f"Host {self.name}: Usermacro {macro_name} "
                                    "has no value, skipping.")
                return False
        else:
            self.logger.error(
                f"Host {self.name}: Usermacro {macro_name} is not a valid usermacro name, skipping."
            )
            return False
        return macro

    def generate(self):
        """
        Generate full set of Usermacros
        """
        macros = []
        data={}
        # Parse the field mapper for usermacros
        if self.usermacro_map:
            self.logger.debug(f"Host {self.nb.name}: Starting usermacro mapper")
            field_macros = field_mapper(
                self.nb.name, self.usermacro_map, self.nb, self.logger
            )
            for macro, value in field_macros.items():
                m = self.render_macro(macro, value)
                if m:
                    macros.append(m)
        # Parse NetBox config context for usermacros
        if (
            "zabbix" in self.nb.config_context
            and "usermacros" in self.nb.config_context["zabbix"]
        ):
            for macro, properties in self.nb.config_context["zabbix"][
                "usermacros"
            ].items():
                m = self.render_macro(macro, properties)
                if m:
                    macros.append(m)
        data={'macros': macros}
        self.logger.debug(f"Host {self.name}: Resolved macros: {sanatize_log_output(data)}")
        return macros
