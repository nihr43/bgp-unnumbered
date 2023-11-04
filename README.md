# bgp-unnumbered

This repository serves as a reference implementation of BGP unnumbered routing on linux.

[BGP-unnumbered](https://www.oreilly.com/library/view/bgp-in-the/9781491983416/ch04.html) fundamentally is an implementation of [rfc5549](https://www.rfc-editor.org/rfc/rfc5549) - 'Advertising IPv4 Network Layer Reachability Information with an IPv6 Next Hop'.  In short, this approach significantly lowers the barrier to entry for building a pure l3 network.  For the uninitiated, ‘pure l3’ means our servers get true multipathing and failover without the need for l2 ‘hacks’ such as lacp, spanning tree, mclag, etc.  Top-of-rack switches become top-of-rack routers, and servers advertise their presence directly into the network.

Outside of the datacenter, this approach enables some interesting capabilities that L2 approaches dont give you.  Lets say you have a small k8s, ceph, gluster, slurm, etc. cluster for which you want ultra-high inter-node bandwidth.  With unnumbered BGP, it would be trivial to toss in some high-bandwidth network cards and implement a full mesh, or cisco 'stack' style ring network backplane.

## implementation

This work is presented as an ansible role.  The inventory and `main.yml` are highly tailored to my own environment, so you are better off forking and changing these.

A minimal `inventory.yaml` may look like this:

```
cluster:
  hosts:
    10.0.200.1:
      router_ip: 10.0.200.1
    10.0.200.2:
      router_ip: 10.0.200.2
    10.0.200.3:
      router_ip: 10.0.200.3
  vars:
    reserved_ports: '[]'
```

There is regex in the 'Reconcile interfaces' task that matches all interfaces like ^enp, ^eno, ^eth.  `reserved_ports` prevents a specific interface from being configured.

The inventory hostname and `router_ip` are two different lines because bootstrapping is a bit of a chicken-egg problem.  Currently to provision a new node, i'll use a host's current dhcp address as the inventory hostname, set the desired address as `router_ip` - run it once - then change the inventory hostname to match.

With the above inventory, `main.yml` might look like this:

```
- hosts: cluster
  serial: 1
  roles:
   - frr
```

If you're just here for the end result, here is `/etc/netplan/bgp-unnumbered.yaml` on a k8s/ceph server acting as a leaf in a spine-leaf network:

```
network:
  version: 2
  renderer: networkd
  ethernets:
    lo:
      addresses:
        - 10.0.200.2/32
        - 10.0.200.0/32
    enp35s0:
      optional: true
      mtu: 9000
      ignore-carrier: true
    enp36s0:
      optional: true
      mtu: 9000
      ignore-carrier: true
  vlans:
    bgpenp35s0:
      link: enp35s0
      id: 10
    bgpenp36s0:
      link: enp36s0
      id: 10
  tunnels:
    vxlan100:
      mode: vxlan
      id: 100
      local: 10.0.200.2
      mac-learning: false
      mtu: 8950
  bridges:
    br-vxlan100:
      interfaces:
        - vxlan100
```

Here are the important points:

- enp35s0 and enp36s0 are brought administratively 'up' with no ip
- vlan subinterfaces bgpenp35s0 and bgpenp36s0 are created on tag 10.  this is so the spines can run dhcpd on the untagged broadcast domain for provisioning.
- br-vxlan100 is brought up on vxlan vni 100.  flooding is disabled and no remote is defined - bgp evpn will enable endpoint discovery.
- lo has two /32 ips - 10.0.200.2 is the primary address for the server.  10.0.200.0/32 is duplicated across all nodes to enable anycast HA kubectl access.

`net.ipv4.conf.all.forwarding=1` of course is enabled.

Heres `/etc/frr/frr.conf`:

```
log syslog
debug bgp
debug zebra vxlan
debug zebra evpn mh es
debug zebra evpn mh mac
debug zebra evpn mh neigh
frr defaults datacenter
zebra nexthop-group keep 1

router bgp 64791
  bgp router-id 10.0.200.2
  bgp fast-convergence
  bgp bestpath compare-routerid
  bgp bestpath as-path multipath-relax
  neighbor bgpenp35s0 interface remote-as external
  neighbor bgpenp36s0 interface remote-as external
  address-family ipv4 unicast
    neighbor bgpenp35s0 route-map default in
    neighbor bgpenp36s0 route-map default in
    redistribute connected
  address-family l2vpn evpn
    neighbor bgpenp35s0 activate
    neighbor bgpenp36s0 activate
    advertise-all-vni

ip prefix-list p1 permit 10.0.0.0/24 ge 32
ip prefix-list p1 permit 192.168.1.0/24 ge 32
ip prefix-list p1 permit 10.0.100.0/24 ge 32
ip prefix-list p1 permit 10.0.200.0/24 ge 32
ip prefix-list p1 permit 172.16.0.0/16 le 26
ip prefix-list p1 permit 172.30.0.0/16 le 27
ip prefix-list p1 permit 0.0.0.0/0

route-map default permit 10
  match ip address prefix-list p1
```

Here we're telling bgp to peer on each vlan interface, advertise all routes, and accept only prefixes matching 'prefix-list'.  I should filter the advertised routes too, I haven't gotten to it.

The inventory to achieve this looks like:

```
leaf:
  hosts:
    ...
    10.0.200.2:
      router_ip: 10.0.200.2
    ...
  vars:
    reserved_ports: '[]'
    vxlans: [100]
    anycast_addresses: ['10.0.200.0/32']
```

---

- You may have noticed the `l2_access` var:  This is used to enable dhcpd servers on every port, serving random /27 networks.  Sounds silly, but this gives me quick network 'access' for unprovisioned devices plugged into spine routers, on the untagged broadcast domain.  Once provisioned, nodes peer over vlan 10.
- There is also the `vxlan_access` var, which implements [evpn-mh](https://signal.nih.earth/posts/evpn-mh/) on the the specified port and vxlan.
- asns are randomized if `asn` is not specified.  For a spine-leaf network, spines should share an asn.

## testing

Also included in this repository is a python module for developing and testing the role on lxd kvm virtual machines.  This tool will by default provision 2 spines and 3 leafs, connect them with point-to-point bridges, and provision the role.  Tests can then be run with `tox`.

To use the tool:

```
python3 testnet --clean --create
tox
```

The testnet tool generates an inventory and lands ssh keys; which can be used to run ansible without reprovisioning the test nodes:

```
ansible-playbook main.yml -i virtual.inventory
```

At which point you can re-run the tests:

```
tox
```

Perhaps some time has passed and your the VMs have been shut down:

```
python3 testnet --start
tox
```

## helpful links

[Vincent Bernat](https://vincent.bernat.ch/en/blog/2017-vxlan-bgp-evpn) has excellent content that helped me figure this out.
