#!/usr/bin/env python3
# pylint: disable=too-many-instance-attributes, too-many-arguments, too-many-positional-arguments, logging-fstring-interpolation
"""
All of the Zabbix Usermacro related configuration
"""
from logging import getLogger
from modules.tools import field_mapper, remove_duplicates

class ZabbixTags():
    """Class that represents a Zabbix interface."""

    def __init__(self, nb, tag_map, tag_sync, tag_lower=True,
                     tag_name=None, tag_value=None, logger=None, host=None):
        self.nb = nb
        self.name = host if host else nb.name
        self.tag_map = tag_map
        self.logger = logger if logger else getLogger(__name__)
        self.tags = {}
        self.lower = tag_lower
        self.tag_name = tag_name
        self.tag_value = tag_value
        self.tag_sync = tag_sync
        self.sync = False
        self._set_config()

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.__repr__()

    def _set_config(self):
        """
        Setup class
        """
        if self.tag_sync:
            self.sync = True

        return True

    def validate_tag(self, tag_name):
        """
        Validates tag name
        """
        if tag_name and isinstance(tag_name, str) and len(tag_name)<=256:
            return True
        return False

    def validate_value(self, tag_value):
        """
        Validates tag value
        """
        if tag_value and isinstance(tag_value, str) and len(tag_value)<=256:
            return True
        return False

    def render_tag(self, tag_name, tag_value):
        """
        Renders a tag
        """
        tag={}
        if self.validate_tag(tag_name):
            if self.lower:
                tag['tag'] = tag_name.lower()
            else:
                tag['tag'] = tag_name
        else:
            self.logger.error(f'Tag {tag_name} is not a valid tag name, skipping.')
            return False

        if self.validate_value(tag_value):
            if self.lower:
                tag['value'] = tag_value.lower()
            else:
                tag['value'] = tag_value
        else:
            self.logger.error(f'Tag {tag_name} has an invalid value: \'{tag_value}\', skipping.')
            return False
        return tag

    def generate(self):
        """
        Generate full set of Usermacros
        """
        # pylint: disable=too-many-branches
        tags=[]
        # Parse the field mapper for tags
        if self.tag_map:
            self.logger.debug(f"Host {self.nb.name}: Starting tag mapper")
            field_tags = field_mapper(self.nb.name, self.tag_map, self.nb, self.logger)
            for tag, value in field_tags.items():
                t = self.render_tag(tag, value)
                if t:
                    tags.append(t)

        # Parse NetBox config context for tags
        if ("zabbix" in self.nb.config_context and "tags" in self.nb.config_context['zabbix']
               and isinstance(self.nb.config_context['zabbix']['tags'], list)):
            for tag in self.nb.config_context['zabbix']['tags']:
                if isinstance(tag, dict):
                    for tagname, value in tag.items():
                        t = self.render_tag(tagname, value)
                        if t:
                            tags.append(t)

        # Pull in NetBox device tags if tag_name is set
        if self.tag_name and isinstance(self.tag_name, str):
            for tag in self.nb.tags:
                if self.tag_value.lower() in ['display', 'name', 'slug']:
                    value = tag[self.tag_value]
                else:
                    value = tag['name']
                t = self.render_tag(self.tag_name, value)
                if t:
                    tags.append(t)

        return remove_duplicates(tags, sortkey='tag')
