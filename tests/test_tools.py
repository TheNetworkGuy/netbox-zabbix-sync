from netbox_zabbix_sync.modules.tools import sanatize_log_output


def test_sanatize_log_output_secrets():
    data = {
        "macros": [
            {"macro": "{$SECRET}", "type": "1", "value": "supersecret"},
            {"macro": "{$PLAIN}", "type": "0", "value": "notsecret"},
        ]
    }
    sanitized = sanatize_log_output(data)
    assert sanitized["macros"][0]["value"] == "********"
    assert sanitized["macros"][1]["value"] == "notsecret"


def test_sanatize_log_output_interface_secrets():
    data = {
        "interfaceid": 123,
        "details": {
            "authpassphrase": "supersecret",
            "privpassphrase": "anothersecret",
            "securityname": "sensitiveuser",
            "community": "public",
            "other": "normalvalue",
        },
    }
    sanitized = sanatize_log_output(data)
    # Sensitive fields should be sanitized
    assert sanitized["details"]["authpassphrase"] == "********"
    assert sanitized["details"]["privpassphrase"] == "********"
    assert sanitized["details"]["securityname"] == "********"
    # Non-sensitive fields should remain
    assert sanitized["details"]["community"] == "********"
    assert sanitized["details"]["other"] == "normalvalue"
    # interfaceid should be removed
    assert "interfaceid" not in sanitized


def test_sanatize_log_output_interface_macros():
    data = {
        "interfaceid": 123,
        "details": {
            "authpassphrase": "{$SECRET_MACRO}",
            "privpassphrase": "{$SECRET_MACRO}",
            "securityname": "{$USER_MACRO}",
            "community": "{$SNNMP_COMMUNITY}",
        },
    }
    sanitized = sanatize_log_output(data)
    # Macro values should not be sanitized
    assert sanitized["details"]["authpassphrase"] == "{$SECRET_MACRO}"
    assert sanitized["details"]["privpassphrase"] == "{$SECRET_MACRO}"
    assert sanitized["details"]["securityname"] == "{$USER_MACRO}"
    assert sanitized["details"]["community"] == "{$SNNMP_COMMUNITY}"
    assert "interfaceid" not in sanitized


def test_sanatize_log_output_plain_data():
    data = {"foo": "bar", "baz": 123}
    sanitized = sanatize_log_output(data)
    assert sanitized == data


def test_sanatize_log_output_non_dict():
    data = [1, 2, 3]
    sanitized = sanatize_log_output(data)
    assert sanitized == data
