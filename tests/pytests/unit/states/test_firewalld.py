"""
    :codeauthor: Hristo Voyvodov <hristo.voyvodov@redsift.io>
"""

import pytest

import salt.states.firewalld as firewalld
from tests.support.mock import MagicMock, patch


@pytest.fixture
def configure_loader_modules():
    return {firewalld: {"__opts__": {"test": False}}}


@pytest.mark.parametrize(
    "rich_rule",
    [
        (
            [
                'rule family="ipv4" source address="192.168.0.0/16" port port=22 protocol=tcp accept'
            ]
        ),
        (
            [
                'rule family="ipv4" source address="192.168.0.0/16" port port=\'22\' protocol=tcp accept'
            ]
        ),
        (
            [
                "rule family='ipv4' source address='192.168.0.0/16' port port='22' protocol=tcp accept"
            ]
        ),
    ],
)
def test_present_rich_rules_normalized(rich_rule):
    firewalld_reload_rules = MagicMock(return_value={})
    firewalld_rich_rules = [
        'rule family="ipv4" source address="192.168.0.0/16" port port="22" protocol="tcp" accept',
    ]

    firewalld_get_zones = MagicMock(
        return_value=[
            "block",
            "public",
        ]
    )
    firewalld_get_masquerade = MagicMock(return_value=False)
    firewalld_get_rich_rules = MagicMock(return_value=firewalld_rich_rules)

    __salt__ = {
        "firewalld.reload_rules": firewalld_reload_rules,
        "firewalld.get_zones": firewalld_get_zones,
        "firewalld.get_masquerade": firewalld_get_masquerade,
        "firewalld.get_rich_rules": firewalld_get_rich_rules,
    }
    with patch.dict(firewalld.__dict__, {"__salt__": __salt__}):
        ret = firewalld.present("public", rich_rules=rich_rule)
        assert ret == {
            "changes": {},
            "result": True,
            "comment": "'public' is already in the desired state.",
            "name": "public",
        }
