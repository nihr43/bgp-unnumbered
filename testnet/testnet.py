import logging
import argparse
import uuid
import time
import random
import json
from os import chmod
from Crypto.PublicKey import RSA
from jinja2 import Environment, FileSystemLoader

import ansible_runner
import pylxd


def get_nodes(client, log):
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
        log.info("no nodes found")
    return members


def start(client, log):
    for i in get_nodes(client, log):
        i.start(wait=True)


def stop(client, log):
    for i in get_nodes(client, log):
        i.stop(wait=True)


def cleanup(client, log, pylxd):
    instances_to_delete = get_nodes(client, log)

    for i in instances_to_delete:
        try:
            i.stop(wait=True)
        except pylxd.exceptions.LXDAPIException as lxdapi_exception:
            if str(lxdapi_exception) == "The instance is already stopped":
                pass
            else:
                raise Exception(lxdapi_exception)
        i.delete(wait=True)
        log.info(i.name + " deleted")

    networks = client.networks.all()
    for n in networks:
        try:
            if n.description == "bgp-unnumbered":
                n.delete(wait=True)
                log.info(n.name + " deleted")
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


def create_node(client, role, image, pubkey, log):
    name = "bgp-" + role + "-" + str(uuid.uuid4())[0:5]
    config = {
        "name": name,
        "description": "bgp-unnumbered",
        "source": {
            "type": "image",
            "mode": "pull",
            "server": "https://images.linuxcontainers.org",
            "protocol": "simplestreams",
            "alias": image,
        },
        "config": {"limits.cpu": "2", "limits.memory": "1GB"},
        "type": "virtual-machine",
    }
    log.info("creating node " + name)
    inst = client.instances.create(config, wait=True)
    inst.description = '{"bgp-unnumbered": true, "role": "%s"}' % role
    inst.save(wait=True)
    inst.start(wait=True)
    wait_until_ready(inst, log)

    if "rocky" in image:
        pkgm = "yum"
    elif "debian" or "ubuntu" in image:
        pkgm = "apt"

    err = inst.execute(
        [pkgm, "install", "python3", "openssh-server", "ca-certificates", "-y"]
    )
    log.info(err.stdout)
    if err.exit_code != 0:
        raise RuntimeError(err.stderr)
    err = inst.execute(["mkdir", "-p", "/root/.ssh"])
    log.info(err.stdout)
    if err.exit_code != 0:
        log.info("failed to mkdir /root/.ssh")
        raise RuntimeError(err.stderr)

    inst.files.put("/root/.ssh/authorized_keys", pubkey.exportKey("OpenSSH"))
    # wow! subsequent reboots in network configuration were borking our ssh installation/configuration
    inst.execute(["sync"])
    return inst


def wait_until_ready(instance, log):
    """
    waits until an instance is executable
    """
    log.info("waiting for lxd agent to become ready on " + instance.name)
    count = 30
    for i in range(count):
        try:
            exit_code = instance.execute(["hostname"]).exit_code
        except BrokenPipeError:
            continue

        if exit_code == 0:
            break
        if i == count - 1:
            raise TimeoutError("timed out waiting")
        time.sleep(1)


def create_bridge(client, inst_a, inst_b, log):
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
    log.info("creating network " + name)
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
    inst_a.devices[ethname] = {
        "name": ethname,
        "network": name,
        "type": "nic",
        "vlan.tagged": "10",
    }
    inst_b.devices[ethname] = {
        "name": ethname,
        "network": name,
        "type": "nic",
        "vlan.tagged": "10",
    }
    inst_a.save(wait=True)
    inst_b.save(wait=True)


def main():
    logging.basicConfig(format="%(funcName)s(): %(message)s")
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)
    client = pylxd.Client()

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
        cleanup(client, log, pylxd)

    if args.create:
        pubkey = create_keypair(RSA)

        spines = [
            create_node(client, "spine", args.image, pubkey, log)
            for i in range(args.spines)
        ]
        leafs = [
            create_node(client, "leaf", args.image, pubkey, log)
            for i in range(args.leafs)
        ]

        all_routers = spines + leafs
        for r in all_routers:
            r.stop(wait=True)

        for leaf in leafs:
            for spine in spines:
                create_bridge(client, leaf, spine, log)

        for r in all_routers:
            r.start(wait=True)
            wait_until_ready(r, log)

        env = Environment(loader=FileSystemLoader("templates"))

        template = env.get_template("virtual-inventory.j2")
        with open("virtual.inventory", "w") as inventory:
            inventory.truncate()
            inventory.write(template.render(spines=spines, leafs=leafs))

        ansible_runner.run(
            private_data_dir="./", inventory="virtual.inventory", playbook="testnet.yml"
        )

        log.info("environment created.  follow-up configuration can be performed with:")
        print("ansible-playbook testnet.yml -i virtual.inventory")

    if args.start:
        start(client, log)

    if args.stop:
        stop(client, log)


if __name__ == "__main__":
    import sys

    sys.exit(main())
