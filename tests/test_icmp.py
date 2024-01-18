import pylxd
import json

import testnet.testnet as testnet

def test_icmp():
    client = pylxd.Client()

    leafs = []
    for n in testnet.get_nodes(client):
        js = json.loads(n.description)
        if js["role"] == "leaf":
            leafs.append(n)

    # each leaf should be able to reach every other leaf via icmp
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
                print("icmp: {} -> {}".format(i.name, j.name))
                assert err.exit_code == 0
