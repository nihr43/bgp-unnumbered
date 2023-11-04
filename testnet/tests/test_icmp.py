import pylxd
import logging
import json

import testnet


def test_icmp():
    log = logging.getLogger(__name__)
    client = pylxd.Client()

    spines = []
    leafs = []
    for n in testnet.get_nodes(client, log):
        js = json.loads(n.description)
        if js["role"] == "spine":
            spines.append(n)
        elif js["role"] == "leaf":
            leafs.append(n)

    for i in spines + leafs:
        log.info(
            "found router {} ip {}".format(
                i.name, i.state().network["lo"]["addresses"][1]["address"]
            )
        )

    # each router should be able to reach every other router via icmp
    for i in leafs:
        for j in leafs:
            if j != i:
                err = i.execute(
                    [
                        "ping",
                        "-c1",
                        "-W1",
                        j.state().network["lo"]["addresses"][1]["address"],
                    ]
                )
                log.info("icmp: " + i.name + " -> " + j.name)
                assert err.exit_code == 0
