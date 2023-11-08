import pylxd
import logging
import json

import testnet


def test_ecmp():
    log = logging.getLogger(__name__)
    client = pylxd.Client()

    leafs = []
    spines = []
    for n in testnet.get_nodes(client, log):
        js = json.loads(n.description)
        if js["role"] == "leaf":
            leafs.append(n)
        elif js["role"] == "spine":
            spines.append(n)

    n_spines = len(spines)

    # the number of routes each leaf has to each other leaf should match the number of spines
    for i in leafs:
        for j in leafs:
            if j is not i:
                err = i.execute(
                    [
                        "ip",
                        "route",
                        "show",
                        j.state().network["lo"]["addresses"][1]["address"],
                    ]
                )
                if err.exit_code != 0:
                    raise RuntimeError(err.stderr)
                routes = err.stdout.count("nexthop via inet6")
                print("{} has {} routes to {}".format(i.name, routes, j.name))
                assert routes == n_spines
