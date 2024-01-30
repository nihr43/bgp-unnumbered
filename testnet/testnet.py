import argparse
import random
import json
from os import chmod
from Crypto.PublicKey import RSA
from jinja2 import Environment, FileSystemLoader

import ansible_runner
import pylxd

try:
    from router import Router
except ImportError:
    from testnet.router import Router


def get_nodes(client):
    """
    find instances
    """
    members = []
    for i in client.instances.all():
        try:
            js = json.loads(i.description)
            if js["bgp-unnumbered"]:
                members.append(i)
        except json.decoder.JSONDecodeError:
            continue

    if len(members) == 0:
        print("no nodes found")
    return members


def start(client):
    for i in get_nodes(client):
        i.start(wait=True)


def stop(client):
    for i in get_nodes(client):
        i.stop(wait=True)


def cleanup(client, pylxd):
    instances_to_delete = get_nodes(client)

    for i in instances_to_delete:
        try:
            i.stop(wait=True)
        except pylxd.exceptions.LXDAPIException as lxdapi_exception:
            if str(lxdapi_exception) == "The instance is already stopped":
                pass
            else:
                raise Exception(lxdapi_exception)
        i.delete(wait=True)
        print("{} deleted".format(i.name))

    networks = client.networks.all()
    for n in networks:
        try:
            if n.description == "bgp-unnumbered":
                n.delete(wait=True)
                print("{} deleted".format(n.name))
        except pylxd.exceptions.NotFound:
            pass


def create_keypair(RSA):
    """
    creates ssh keypair for use with ansible
    returns public key
    """
    key = RSA.generate(4096)
    with open("./private.key", "wb") as content_file:
        chmod("./private.key", 0o600)
        content_file.write(key.exportKey("PEM"))
    pubkey = key.publickey()
    with open("./public.key", "wb") as content_file:
        content_file.write(pubkey.exportKey("OpenSSH"))
    return pubkey


def create_bridge(client, inst_a, inst_b):
    """
    creates an l2 bridge linking two lxd instances
    """
    config = {
        "ipv4.dhcp": "false",
        "ipv4.nat": "false",
        "ipv6.dhcp": "false",
        "ipv6.nat": "false",
        "ipv4.address": "none",
        "ipv6.address": "none",
        "bridge.mtu": "9000",
    }
    name = inst_a.name[-5:] + "-" + inst_b.name[-5:]
    print("creating network {}".format(name))
    client.networks.create(name, description="bgp-unnumbered", config=config)
    """
    qemu appears to use this 'eth' number to enumerate the pci ids.
    if 'eth' is undefined or conflicting, the error looks like this:
    pylxd.exceptions.LXDAPIException: Failed to start device "eth5201": Failed setting up device "eth5201": Failed adding NIC device: PCI: slot 0 function 0 not available for virtio-net-pci, in use by virtio-net-pci,id=dev-lxd_eth6961

    we'll punt this issue (sorry) with random ids as follows:
    """
    eth_id = random.randint(10, 9999)
    ethname = "eth" + str(eth_id)
    # lxd manipulates linux bridges on our behalf.
    # for linux bridges to behave as 802.1q trunks, vlan_filtering needs
    # enabled and desired vids need added to the bridge and the taps.
    # vlan.tagged does this for us.  you can check its effect with `bridge vlan show`
    inst_a.inst.devices[ethname] = {
        "name": ethname,
        "network": name,
        "type": "nic",
        "vlan.tagged": "10",
    }
    inst_b.inst.devices[ethname] = {
        "name": ethname,
        "network": name,
        "type": "nic",
        "vlan.tagged": "10",
    }
    inst_a.inst.save(wait=True)
    inst_b.inst.save(wait=True)


def main():
    client = pylxd.Client(endpoint="/var/lib/incus/unix.socket")

    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument(
        "--spines",
        "-s",
        type=int,
        default=2,
        help="Number of spines to provision. Defaults to 2.",
    )
    parser.add_argument(
        "--leafs",
        "-l",
        type=int,
        default=3,
        help="Number of leafs to provision. Defaults to 3.",
    )
    parser.add_argument("--image", type=str, default="debian/12")
    parser.add_argument(
        "--start",
        action="store_true",
        help="Discover and start an existing topology.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Stop the network.",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup(client, pylxd)

    if args.create:
        pubkey = create_keypair(RSA)

        spines = [
            Router(client, "spine", pubkey, args.image) for i in range(args.spines)
        ]
        leafs = [Router(client, "leaf", pubkey, args.image) for i in range(args.leafs)]

        all_routers = spines + leafs
        for r in all_routers:
            r.inst.stop(wait=True)

        for leaf in leafs:
            for spine in spines:
                create_bridge(client, leaf, spine)

        for r in all_routers:
            r.inst.start(wait=True)
            r.wait_until_ready()

        env = Environment(loader=FileSystemLoader("templates"))

        template = env.get_template("virtual-inventory.j2")
        with open("virtual.inventory", "w") as inventory:
            inventory.truncate()
            inventory.write(template.render(spines=spines, leafs=leafs))

        ansible_runner.run(
            private_data_dir="./", inventory="virtual.inventory", playbook="testnet.yml"
        )

        for r in all_routers:
            r.wait_until_ready()
            r.get_valid_ipv4("eth0")

        print("environment created.  follow-up configuration can be performed with:")
        print("ansible-playbook testnet.yml -i virtual.inventory")

    if args.start:
        start(client)

    if args.stop:
        stop(client)


if __name__ == "__main__":
    import sys

    sys.exit(main())
