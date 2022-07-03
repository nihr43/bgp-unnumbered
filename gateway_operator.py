import os
import random
import netifaces
import pylxd
from pylxd import Client
from time import sleep
from functools import partial


# returns list of cluster "members"
# members defined here: https://github.com/lxc/pylxd/blob/master/pylxd/models/cluster.py#L45
def get_members(client):
    cluster = client.cluster.get()
    nodes = cluster.members.all()
    return nodes


def get_address_state(dev, address):
    addrs = netifaces.ifaddresses(dev)[netifaces.AF_INET]
    parsed_addresses = []

    for i in addrs:
        for a, b in i.items():
            if a == 'addr':
                parsed_addresses.append(b)

    if address in parsed_addresses:
        return True
    else:
        return False


def apply_address(dev, address):
    address_without_net = address[:-3]
    if get_address_state(dev, address_without_net) == True:
        print('already assigned')
    else:
        os.system('ip address add ' + address_without_net + ' dev ' + dev)


def enforce_no_address(dev, address):
    address_without_net = address[:-3]
    if get_address_state(dev, address_without_net) == True:
        os.system('ip address del ' + address_without_net + ' dev ' + dev)


def main():
    endpoint = 'https://10.0.200.1:8443'
    address = '172.16.0.100/24'
    dev = 'br100'

    client_factory = partial(Client, cert=('/var/snap/lxd/common/lxd/server.crt',
                                           '/var/snap/lxd/common/lxd/server.key'),
                             verify='/var/snap/lxd/common/lxd/cluster.crt')
 
    while True:
        sleep(random.randrange(1,10))

        try:
            cluster_client = client_factory(endpoint=endpoint)
            cluster = get_members(cluster_client)

            for i in cluster:
                print(i.url)
                print(i.roles)
                if i.url==endpoint and 'database-leader' in i.roles:
                    print('i will assume address')
                    apply_address(dev, address)
                elif i.url==endpoint and 'database-leader' not in i.roles:
                    enforce_no_address(dev, address)
        except:
            print('issue connecting to LXD')
            enforce_no_address(dev, address)


if __name__ == '__main__':
    main()
