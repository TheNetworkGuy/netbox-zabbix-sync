"""Custom Jinja2 filters, these will be loaded by the jinja parsers."""

import re

import dateutil


def regex_replace(s, find, replace):
    """Regex replace"""
    return re.sub(find, replace, s)


def regex_search(s, find):
    """
    Regex search - Only prints matches
    """
    match = re.search(find, s)
    if match:
        return match.group()
    return ""


def strftime(date, fmt=None):
    """Format timestrings"""
    date = dateutil.parser.parse(date)
    native = date.replace(tzinfo=None)
    format = fmt or "%Y-%m-%d %H:%M:%S"
    return native.strftime(format)
