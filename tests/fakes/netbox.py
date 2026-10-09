"""An in-memory NetBox that hands out real pynetbox Records.

The sync reads NetBox objects every way a Record allows: attribute access,
`record["key"]`, `dict(record)`, `str(record)`, and lazy loading of fields a
nested object does not carry. A MagicMock or a dict only fakes some of those,
and silently answers the rest, so a test passes against behaviour NetBox does
not have. The objects here are the real pynetbox model classes built from
payloads shaped like the NetBox 4.x REST API, so every access path behaves as
it does in production.

What makes them real:

- Related objects are nested in their *brief* form, as NetBox serialises them.
  `device.site` carries id/url/display/name/slug/description, not `region` or
  `custom_fields`. Reading those triggers pynetbox's lazy `full_details()`,
  which is served from this fake, exactly like the extra GET in production.
- `Record.save()` goes through pynetbox's own diffing and PATCHes this fake,
  which applies it and logs it in `requests`. Assert on what was sent, not on
  whether a mock method was called.
- Anything this fake does not know about is a 404, which pynetbox raises as a
  `RequestError`. A test can never get a value NetBox would not have returned.

Typical use::

    netbox = FakeNetBox()
    device = netbox.device(
        "sw01",
        site=netbox.site("AMS-01", region=netbox.region("EU")),
        custom_fields={"zabbix_hostid": 42},
    )

Builders return the full payload (a dict) for related objects, which can be
handed to other builders or tweaked first. `device()` and `virtual_machine()`
return the Record the sync consumes.
"""

from collections import defaultdict
from copy import deepcopy
from functools import cached_property
from itertools import count
from json import dumps
from typing import Any, cast
from unittest.mock import create_autospec

import pynetbox
from pynetbox.core.endpoint import Endpoint
from pynetbox.core.response import Record
from pynetbox.models.dcim import Devices
from pynetbox.models.ipam import IpAddresses
from pynetbox.models.virtualization import VirtualMachines

BASE_URL = "http://netbox.local"
TOKEN = "0123456789abcdef0123456789abcdef01234567"  # noqa: S105
DEFAULT_VERSION = "4.4"
TIMESTAMP = "2026-01-01T00:00:00.000000Z"
HTTP_ERROR = 400
IPV4, IPV6 = 4, 6

# Marks "use the builder's default", where None means "this field is empty".
DEFAULT: Any = object()

# The brief (nested) representation NetBox 4.x uses for each endpoint. A
# related object is reduced to these keys before it is nested in another one.
BRIEF_FIELDS = {
    "dcim/devices": ("id", "url", "display", "name", "description"),
    "dcim/device-roles": ("id", "url", "display", "name", "slug", "description"),
    "dcim/device-types": (
        "id",
        "url",
        "display",
        "manufacturer",
        "model",
        "slug",
        "description",
    ),
    "dcim/locations": ("id", "url", "display", "name", "slug", "description", "_depth"),
    "dcim/manufacturers": ("id", "url", "display", "name", "slug", "description"),
    "dcim/platforms": ("id", "url", "display", "name", "slug", "description"),
    "dcim/racks": ("id", "url", "display", "name", "description"),
    "dcim/regions": ("id", "url", "display", "name", "slug", "description", "_depth"),
    "dcim/site-groups": (
        "id",
        "url",
        "display",
        "name",
        "slug",
        "description",
        "_depth",
    ),
    "dcim/sites": ("id", "url", "display", "name", "slug", "description"),
    "dcim/virtual-chassis": ("id", "url", "display", "name", "master", "description"),
    "extras/tags": ("id", "url", "display", "name", "slug", "color"),
    "ipam/ip-addresses": ("id", "url", "display", "family", "address", "description"),
    "tenancy/tenant-groups": (
        "id",
        "url",
        "display",
        "name",
        "slug",
        "description",
        "_depth",
    ),
    "tenancy/tenants": ("id", "url", "display", "name", "slug", "description"),
    "users/owners": ("id", "url", "display", "name", "description"),
    "virtualization/cluster-types": (
        "id",
        "url",
        "display",
        "name",
        "slug",
        "description",
    ),
    "virtualization/clusters": ("id", "url", "display", "name", "description"),
    "virtualization/virtual-machines": ("id", "url", "display", "name", "description"),
}

MODELS = {
    "dcim/devices": Devices,
    "ipam/ip-addresses": IpAddresses,
    "virtualization/virtual-machines": VirtualMachines,
}


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def choice(value: str) -> dict:
    """A NetBox choice field, e.g. `status`: `{"value": "active", "label": "Active"}`."""
    value = value.lower()
    return {"value": value, "label": value.capitalize()}


def endpoint_of(url: str) -> str:
    """`http://netbox.local/api/dcim/sites/3/` -> `dcim/sites`."""
    app, name = url.removeprefix(f"{BASE_URL}/api/").split("/")[:2]
    return f"{app}/{name}"


class FakeResponse:
    """The parts of `requests.Response` pynetbox reads."""

    def __init__(self, method: str, url: str, status_code: int, body: Any):
        self.status_code = status_code
        self.ok = status_code < HTTP_ERROR
        self.reason = "OK" if self.ok else "Not Found"
        self.url = url
        self._body = body
        self.text = dumps(body)
        self.request = type("PreparedRequest", (), {"method": method, "body": None})()

    def json(self):
        return deepcopy(self._body)


class FakeSession:
    """Stands in for the `requests.Session` pynetbox talks through.

    Serves GETs for registered objects and applies PATCHes to them. Every other
    verb fails the test: the sync is not expected to create or delete NetBox
    objects through a Record.
    """

    def __init__(self, netbox: "FakeNetBox"):
        self.netbox = netbox

    def get(self, url, headers=None, params=None, json=None):
        self.netbox.requests.append(("GET", url, None))
        obj = self.netbox.objects.get(url)
        if obj is None:
            return FakeResponse("GET", url, 404, {"detail": "Not found."})
        return FakeResponse("GET", url, 200, obj)

    def patch(self, url, headers=None, params=None, json=None):
        self.netbox.requests.append(("PATCH", url, deepcopy(json)))
        obj = self.netbox.objects.get(url)
        if obj is None:
            return FakeResponse("PATCH", url, 404, {"detail": "Not found."})
        for key, value in (json or {}).items():
            # NetBox merges custom_fields, it does not replace the whole dict.
            if key == "custom_fields":
                obj["custom_fields"].update(value)
            else:
                obj[key] = value
        return FakeResponse("PATCH", url, 200, obj)

    def _unsupported(self, verb):
        def call(url, *args, **kwargs):
            raise AssertionError(f"Unexpected NetBox {verb} {url}")

        return call

    def __getattr__(self, verb):
        if verb in ("post", "put", "delete", "options"):
            return self._unsupported(verb.upper())
        raise AttributeError(verb)


class FakeNetBox:
    """In-memory NetBox. Holds the full payload of every object by URL."""

    def __init__(self, version: str = DEFAULT_VERSION):
        self.version = version
        self.api = pynetbox.api(BASE_URL, token=TOKEN)
        self.api.http_session = FakeSession(self)
        self.objects: dict[str, dict] = {}
        # (method, url, json body) of every request a Record made.
        self.requests: list[tuple[str, str, dict | None]] = []
        # Journal entries are created through the endpoint, never a Record.
        self.journal_entries = create_autospec(Endpoint, instance=True)
        self._ids: dict[str, count] = defaultdict(lambda: count(1))

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    @property
    def patches(self) -> list[tuple[str, dict | None]]:
        """(url, body) of every PATCH, i.e. every `Record.save()` that sent data."""
        return [(url, body) for method, url, body in self.requests if method == "PATCH"]

    def add(self, endpoint: str, obj_id: int | None = None, **fields) -> dict:
        """Register a full object on `endpoint` and return its payload."""
        if obj_id is None:
            obj_id = next(self._ids[endpoint])
            while self._url(endpoint, obj_id) in self.objects:
                obj_id = next(self._ids[endpoint])
        payload: dict[str, Any] = {
            "id": obj_id,
            "url": self._url(endpoint, obj_id),
            **fields,
        }
        payload.setdefault(
            "display",
            fields.get("name") or fields.get("address") or fields.get("model"),
        )
        # The object's web UI page, returned since NetBox 4.1
        if version_tuple(self.version) >= (4, 1):
            payload.setdefault("display_url", f"{BASE_URL}/{endpoint}/{obj_id}/")
        self.objects[payload["url"]] = payload
        return payload

    def record(self, payload: dict) -> Record:
        """Build the pynetbox Record for a payload, as the API would return it."""
        model = MODELS.get(endpoint_of(payload["url"]), Record)
        return model(deepcopy(payload), self.api, None)

    def all(self, endpoint: str) -> list[Record]:
        """Every registered object on `endpoint`, as `.all()` returns them."""
        return [
            self.record(obj)
            for url, obj in self.objects.items()
            if endpoint_of(url) == endpoint
        ]

    def brief(self, obj):
        """Reduce a related object to the nested form NetBox serialises.

        Accepts a payload, a Record, None, or a list of those.
        """
        if obj is None:
            return None
        if isinstance(obj, list):
            return [self.brief(item) for item in obj]
        if isinstance(obj, Record):
            obj = self.objects[obj.url]
        fields = BRIEF_FIELDS[endpoint_of(obj["url"])]
        return deepcopy({k: obj[k] for k in fields if k in obj})

    def _url(self, endpoint: str, obj_id: int) -> str:
        return f"{BASE_URL}/api/{endpoint}/{obj_id}/"

    def _resolve(self, value, factory):
        """A related field given as a name (str) is created with `factory`."""
        if isinstance(value, str):
            value = factory(value)
        return self.brief(value)

    # ------------------------------------------------------------------
    # Organisation
    # ------------------------------------------------------------------

    def region(self, name: str, parent=None, **fields) -> dict:
        parent = self._resolve(parent, self.region)
        return self.add(
            "dcim/regions",
            name=name,
            slug=slugify(name),
            parent=parent,
            description="",
            _depth=parent["_depth"] + 1 if parent else 0,
            **fields,
        )

    def site_group(self, name: str, parent=None, **fields) -> dict:
        parent = self._resolve(parent, self.site_group)
        return self.add(
            "dcim/site-groups",
            name=name,
            slug=slugify(name),
            parent=parent,
            description="",
            _depth=parent["_depth"] + 1 if parent else 0,
            **fields,
        )

    def site(
        self, name: str, *, region=None, group=None, tenant=None, **fields
    ) -> dict:
        return self.add(
            "dcim/sites",
            name=name,
            slug=slugify(name),
            status=choice("active"),
            region=self._resolve(region, self.region),
            group=self._resolve(group, self.site_group),
            tenant=self._resolve(tenant, self.tenant),
            facility="",
            time_zone=None,
            description="",
            comments="",
            tags=[],
            custom_fields=fields.pop("custom_fields", {}),
            created=TIMESTAMP,
            last_updated=TIMESTAMP,
            **fields,
        )

    def location(self, name: str, *, site=DEFAULT, parent=None, **fields) -> dict:
        site = self.default_site if site is DEFAULT else site
        parent = self._resolve(parent, self.location)
        return self.add(
            "dcim/locations",
            name=name,
            slug=slugify(name),
            site=self._resolve(site, self.site),
            parent=parent,
            status=choice("active"),
            description="",
            _depth=parent["_depth"] + 1 if parent else 0,
            **fields,
        )

    def rack(self, name: str, *, site=DEFAULT, **fields) -> dict:
        site = self.default_site if site is DEFAULT else site
        return self.add(
            "dcim/racks",
            name=name,
            site=self._resolve(site, self.site),
            status=choice("active"),
            description="",
            **fields,
        )

    def tenant_group(self, name: str, parent=None, **fields) -> dict:
        parent = self._resolve(parent, self.tenant_group)
        return self.add(
            "tenancy/tenant-groups",
            name=name,
            slug=slugify(name),
            parent=parent,
            description="",
            _depth=parent["_depth"] + 1 if parent else 0,
            **fields,
        )

    def tenant(self, name: str, *, group=None, **fields) -> dict:
        return self.add(
            "tenancy/tenants",
            name=name,
            slug=slugify(name),
            group=self._resolve(group, self.tenant_group),
            description="",
            custom_fields=fields.pop("custom_fields", {}),
            **fields,
        )

    def owner(self, name: str, **fields) -> dict:
        """NetBox 4.5+ object ownership."""
        return self.add("users/owners", name=name, description="", **fields)

    def tag(self, name: str, **fields) -> dict:
        return self.add(
            "extras/tags",
            name=name,
            slug=slugify(name),
            color="9e9e9e",
            description="",
            **fields,
        )

    # ------------------------------------------------------------------
    # Device building blocks
    # ------------------------------------------------------------------

    def manufacturer(self, name: str, **fields) -> dict:
        return self.add(
            "dcim/manufacturers",
            name=name,
            slug=slugify(name),
            description="",
            **fields,
        )

    def device_type(
        self, model: str, *, manufacturer=DEFAULT, custom_fields=None, **fields
    ) -> dict:
        if manufacturer is DEFAULT:
            manufacturer = self.default_manufacturer
        return self.add(
            "dcim/device-types",
            manufacturer=self._resolve(manufacturer, self.manufacturer),
            model=model,
            slug=slugify(model),
            part_number="",
            u_height=1.0,
            description="",
            custom_fields=custom_fields if custom_fields is not None else {},
            **fields,
        )

    def role(self, name: str, **fields) -> dict:
        return self.add(
            "dcim/device-roles",
            name=name,
            slug=slugify(name),
            color="9e9e9e",
            vm_role=True,
            description="",
            **fields,
        )

    def platform(self, name: str, **fields) -> dict:
        return self.add(
            "dcim/platforms", name=name, slug=slugify(name), description="", **fields
        )

    def ip_address(self, address: str, *, dns_name: str = "", **fields) -> dict:
        family = IPV6 if ":" in address else IPV4
        return self.add(
            "ipam/ip-addresses",
            address=address,
            family={"value": family, "label": f"IPv{family}"},
            status=choice("active"),
            dns_name=dns_name,
            description="",
            **fields,
        )

    def virtual_chassis(self, name: str, *, master=None, **fields) -> dict:
        """`master` is a device payload/Record, or a device ID not built yet.

        A chassis and its master reference each other, so a test usually
        builds the chassis first with the ID its master device will get.
        """
        if isinstance(master, int):
            master = {
                "id": master,
                "url": self._url("dcim/devices", master),
                "display": None,
                "name": None,
                "description": "",
            }
        else:
            master = self.brief(master)
        return self.add(
            "dcim/virtual-chassis",
            name=name,
            master=master,
            domain="",
            description="",
            **fields,
        )

    def cluster_type(self, name: str, **fields) -> dict:
        return self.add(
            "virtualization/cluster-types",
            name=name,
            slug=slugify(name),
            description="",
            **fields,
        )

    def cluster(self, name: str, *, type=DEFAULT, group=None, **fields) -> dict:
        if type is DEFAULT:
            type = self.default_cluster_type
        return self.add(
            "virtualization/clusters",
            name=name,
            type=self._resolve(type, self.cluster_type),
            group=group,
            status=choice("active"),
            description="",
            custom_fields=fields.pop("custom_fields", {}),
            **fields,
        )

    # Shared defaults, created on first use so a test only registers what it
    # touches. Names match the hostgroups the existing tests expect.

    @cached_property
    def default_site(self) -> dict:
        return self.site("TestSite")

    @cached_property
    def default_manufacturer(self) -> dict:
        return self.manufacturer("TestManufacturer")

    @cached_property
    def default_device_type(self) -> dict:
        return self.device_type(
            "Test Model", custom_fields={"zabbix_template": "TestTemplate"}
        )

    @cached_property
    def default_device_role(self) -> dict:
        return self.role("Switch")

    @cached_property
    def default_vm_role(self) -> dict:
        return self.role("Server")

    @cached_property
    def default_cluster_type(self) -> dict:
        return self.cluster_type("TestClusterType")

    @cached_property
    def default_cluster(self) -> dict:
        return self.cluster("TestCluster")

    # ------------------------------------------------------------------
    # Hosts
    # ------------------------------------------------------------------

    def _ips(self, primary_ip, fields) -> dict:
        """primary_ip/primary_ip4/primary_ip6 the way NetBox derives them."""
        if primary_ip is False:
            primary_ip = None
        if isinstance(primary_ip, str):
            primary_ip = self.ip_address(primary_ip)
        primary = self.brief(primary_ip)
        family = primary["family"]["value"] if primary else None
        return {
            "primary_ip": primary,
            "primary_ip4": self.brief(fields.pop("primary_ip4", None))
            or (primary if family == IPV4 else None),
            "primary_ip6": self.brief(fields.pop("primary_ip6", None))
            or (primary if family == IPV6 else None),
        }

    def _related(self, fields: dict) -> dict:
        """Nest the related objects in `fields`, creating named ones."""
        factories = {
            "location": self.location,
            "owner": self.owner,
            "platform": self.platform,
            "rack": self.rack,
            "tenant": self.tenant,
        }
        resolved = {}
        for key, value in fields.items():
            if key == "tags":
                tags = [self.tag(t) if isinstance(t, str) else t for t in value]
                resolved[key] = self.brief(tags)
            elif key == "oob_ip" and isinstance(value, str):
                resolved[key] = self.brief(self.ip_address(value))
            elif key in factories:
                resolved[key] = self._resolve(value, factories[key])
            elif isinstance(value, Record) or (
                isinstance(value, dict) and "url" in value
            ):
                resolved[key] = self.brief(value)
            else:
                resolved[key] = value
        return resolved

    def device(
        self,
        name: str = "test-device",
        *,
        status: str = "active",
        site=DEFAULT,
        role=DEFAULT,
        device_type=DEFAULT,
        primary_ip=DEFAULT,
        custom_fields: dict | None = None,
        config_context: dict | None = None,
        **fields,
    ) -> Devices:
        """A device as `nb.dcim.devices.filter()` returns it.

        `custom_fields` is merged over `{"zabbix_hostid": None}`, the field the
        sync requires. `primary_ip` takes an address string, an IP payload, or
        None. Any other device field can be passed as a keyword; related
        objects are nested in their brief form, and `tenant`, `platform`,
        `location`, `rack`, `owner` and `tags` also accept plain names.
        """
        role = self.default_device_role if role is DEFAULT else role
        role_field = "role" if version_tuple(self.version) >= (4, 0) else "device_role"
        payload: dict[str, Any] = {
            "name": name,
            "device_type": self._resolve(
                self.default_device_type if device_type is DEFAULT else device_type,
                self.device_type,
            ),
            role_field: self._resolve(role, self.role),
            "tenant": None,
            "platform": None,
            "serial": "",
            "asset_tag": None,
            "site": self._resolve(
                self.default_site if site is DEFAULT else site, self.site
            ),
            "location": None,
            "rack": None,
            "position": None,
            "face": None,
            "latitude": None,
            "longitude": None,
            "parent_device": None,
            "status": choice(status),
            "airflow": None,
            **self._ips(
                "192.168.1.1/24" if primary_ip is DEFAULT else primary_ip, fields
            ),
            "oob_ip": None,
            "cluster": None,
            "virtual_chassis": None,
            "vc_position": None,
            "vc_priority": None,
            "description": "",
            "comments": "",
            "config_template": None,
            "config_context": config_context if config_context is not None else {},
            "local_context_data": None,
            "tags": [],
            "custom_fields": {"zabbix_hostid": None, **(custom_fields or {})},
            "created": TIMESTAMP,
            "last_updated": TIMESTAMP,
        }
        if version_tuple(self.version) >= (4, 5):
            payload["owner"] = None
        payload.update(self._related(fields))
        obj_id = payload.pop("id", None)
        return cast(Devices, self.record(self.add("dcim/devices", obj_id, **payload)))

    def virtual_machine(
        self,
        name: str = "test-vm",
        *,
        status: str = "active",
        site=DEFAULT,
        cluster=DEFAULT,
        role=DEFAULT,
        primary_ip=DEFAULT,
        custom_fields: dict | None = None,
        config_context: dict | None = None,
        **fields,
    ) -> VirtualMachines:
        """A VM as `nb.virtualization.virtual_machines.filter()` returns it.

        Same conventions as `device()`. Note the default config context is
        empty, as in NetBox: a VM only gets templates from its config context,
        so a test that needs it synced has to pass one.
        """
        payload: dict[str, Any] = {
            "name": name,
            "status": choice(status),
            "site": self._resolve(
                self.default_site if site is DEFAULT else site, self.site
            ),
            "cluster": self._resolve(
                self.default_cluster if cluster is DEFAULT else cluster, self.cluster
            ),
            "device": None,
            "serial": "",
            "role": self._resolve(
                self.default_vm_role if role is DEFAULT else role, self.role
            ),
            "tenant": None,
            "platform": None,
            **self._ips(
                "192.168.1.1/24" if primary_ip is DEFAULT else primary_ip, fields
            ),
            "vcpus": None,
            "memory": None,
            "disk": None,
            "description": "",
            "comments": "",
            "config_template": None,
            "config_context": config_context if config_context is not None else {},
            "local_context_data": None,
            "tags": [],
            "custom_fields": {"zabbix_hostid": None, **(custom_fields or {})},
            "created": TIMESTAMP,
            "last_updated": TIMESTAMP,
        }
        if version_tuple(self.version) >= (4, 5):
            payload["owner"] = None
        payload.update(self._related(fields))
        obj_id = payload.pop("id", None)
        return cast(
            VirtualMachines,
            self.record(self.add("virtualization/virtual-machines", obj_id, **payload)),
        )
