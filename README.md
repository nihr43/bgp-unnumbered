# bgp-unnumbered

This is a reference implementation of bgp routing over unnumbered network interfaces using Debian and FRRouting.  The architecture is a clos spine-leaf network, where the leafs are kubernetes/ceph nodes and the spines are x86 Debian systems with additional network interfaces.

The primary motivation for implementing a routing protocol such as BGP on compute or storage hosts is to maximize the utilization and fault tolerance of the network - while ultimately simplifying the datacenter as a whole.  BGP gives us equal-cost-multipathing, shortest path calculation, and autonomous failover - all the while we have eliminated a layer from the network - aggregation leaf switches running proprietary mlag.

The [BGP-unnumbered](https://www.oreilly.com/library/view/bgp-in-the/9781491983416/ch04.html) technique makes this very easy to implement and manage, once you've grokked the initial configuration.  The fundamental concept that allows this to work is the advertisement of ipv4 prefixes over ipv6 link-local next-hops.  Ipv6 link-local addresses are auto-configured on most modern systems, so the result is "as easy" to use as a standard unmanaged ethernet switch.  Of course, this means physical security is important - and if you were doing this in a real datacenter, you would want to add authentication for bgp peering, and prefix rules / clever firewalling to ensure guest VMs dont somehow poison the datacenter routing table.

From a high-level management aspect, this is an attractive concept because the tools and processes (ansible in this example) to configure the network infrastructure are the same as for the hypervisors.  Consider the following: these linux routers have the same upgrade path as the rest of the infrastructure, the same install image, the same monitoring system, the same package management, potentially the same or very similar hardware...  In my mind, open source whitebox routing is the ultimate evolution ( and perhap irony ) of "software defined networking".

I am far from the first person to build such a thing, though this is sort of a darker magic that is difficult to piece together.  [Vincent Bernat](https://vincent.bernat.ch/en/blog/2017-vxlan-bgp-evpn) has excellent content that helped me figure this out.

## usage

*jan 2023*

To enable development and testing independent of the physical network, a reproducable virtual environment is provided via `test.py`.
This environment uses lxd virtual machines with point-to-point linux bridges behaving as virtual 'cables' between individual routers.  Each leaf is programmatically connected to each spine.

```
$ ./test.py -h
usage: test.py [-h] [--create] [--cleanup] [--spines SPINES] [--leafs LEAFS] [--image IMAGE]

options:
  -h, --help            show this help message and exit
  --create
  --cleanup
  --spines SPINES, -s SPINES
                        Number of spines to provision
  --leafs LEAFS, -l LEAFS
                        Number of leafs to provision
  --image IMAGE
```

To provision 2 spines and 3 leafs:

```
$ ./test.py --create -s 2 -l 3
create_node(): creating node bgp-unnumbered-spine-ebb40
wait_until_ready(): waiting for lxd agent to become ready on bgp-unnumbered-spine-ebb40
...
create_bridge(): creating network ebb40-15492
create_bridge(): creating network bd5c2-15492
create_bridge(): creating network ebb40-2542b
create_bridge(): creating network bd5c2-2542b
create_bridge(): creating network ebb40-6ea34
create_bridge(): creating network bd5c2-6ea34
wait_until_ready(): waiting for lxd agent to become ready on bgp-unnumbered-spine-ebb40
wait_until_ready(): waiting for lxd agent to become ready on bgp-unnumbered-spine-bd5c2
wait_until_ready(): waiting for lxd agent to become ready on bgp-unnumbered-leaf-15492
wait_until_ready(): waiting for lxd agent to become ready on bgp-unnumbered-leaf-2542b
wait_until_ready(): waiting for lxd agent to become ready on bgp-unnumbered-leaf-6ea34
privileged_main(): environment created.  to finish provisioning routers run the following:
privileged_main(): ANSIBLE_HOST_KEY_CHECKING=false ansible-playbook main.yml -i virtual.inventory -u root
```

The tool creates an ansible inventory file ready for us to use:

```
$ cat virtual.inventory 
[spine]
10.139.0.137 router_ip=10.0.254.25 router_advertise='[]' reserved_ports='["enp5s0"]' l2_access=false
10.139.0.121 router_ip=10.0.254.139 router_advertise='[]' reserved_ports='["enp5s0"]' l2_access=false

[leaf]
10.139.0.180 router_ip=10.0.200.81 router_advertise='[]' reserved_ports='["enp5s0"]' l2_access=false
10.139.0.222 router_ip=10.0.200.34 router_advertise='[]' reserved_ports='["enp5s0"]' l2_access=false
10.139.0.160 router_ip=10.0.200.165 router_advertise='[]' reserved_ports='["enp5s0"]' l2_access=false
```

Currently the tool does not provision the ansible side of things on its own, but it did print a command for us to copy-paste:

```
$ ANSIBLE_HOST_KEY_CHECKING=false ansible-playbook main.yml -i virtual.inventory -u root

PLAY [spine] ****************************************************************************************************************************************

TASK [Gathering Facts] ******************************************************************************************************************************
ok: [10.139.0.121]
ok: [10.139.0.137]

TASK [frr : install tools] **************************************************************************************************************************
changed: [10.139.0.121]
changed: [10.139.0.137]

TASK [frr : ensure absence of classic /etc/network/interfaces] **************************************************************************************
changed: [10.139.0.137]
changed: [10.139.0.121]

TASK [frr : set loopback ip] ************************************************************************************************************************
changed: [10.139.0.137]
changed: [10.139.0.121]
...
```

When ansible is finished, our virtual routers should be peering with eachother:

```
$ lxc exec bgp-unnumbered-leaf-2542b -- vtysh <<<'show ip bgp'
...
BGP table version is 6, local router ID is 10.0.200.34, vrf id 0
Default local pref 100, local AS 65238
Status codes:  s suppressed, d damped, h history, * valid, > best, = multipath,
               i internal, r RIB-failure, S Stale, R Removed
Nexthop codes: @NNN nexthop's vrf id, < announce-nh-self
Origin codes:  i - IGP, e - EGP, ? - incomplete
RPKI validation codes: V valid, I invalid, N Not found

   Network          Next Hop            Metric LocPrf Weight Path
*> 10.0.200.34/32   0.0.0.0(bgp-unnumbered-leaf-2542b)
                                             0         32768 ?
*> 10.0.200.81/32   bgpenp7s0                              0 64760 64791 ?
*=                  bgpenp6s0                              0 65009 64791 ?
*> 10.0.200.165/32  bgpenp7s0                              0 64760 64600 ?
*=                  bgpenp6s0                              0 65009 64600 ?
*> 10.0.254.25/32   bgpenp7s0                0             0 64760 ?
*> 10.0.254.139/32  bgpenp6s0                0             0 65009 ?
*> 10.139.0.0/24    0.0.0.0(bgp-unnumbered-leaf-2542b)
                                          1024         32768 ?

Displayed  6 routes and 8 total paths
```

And here are the installed routes:

```
$ lxc exec bgp-unnumbered-leaf-2542b -- ip route
default via 10.139.0.1 dev enp5s0 proto dhcp src 10.139.0.222 metric 1024
10.0.200.81 nhid 39 proto bgp metric 20
	nexthop via inet6 fe80::216:3eff:fe7e:9c73 dev bgpenp6s0 weight 1
	nexthop via inet6 fe80::216:3eff:fed6:14bb dev bgpenp7s0 weight 1
10.0.200.165 nhid 39 proto bgp metric 20
	nexthop via inet6 fe80::216:3eff:fe7e:9c73 dev bgpenp6s0 weight 1
	nexthop via inet6 fe80::216:3eff:fed6:14bb dev bgpenp7s0 weight 1
10.0.254.25 nhid 32 via inet6 fe80::216:3eff:fed6:14bb dev bgpenp7s0 proto bgp metric 20
10.0.254.139 nhid 24 via inet6 fe80::216:3eff:fe7e:9c73 dev bgpenp6s0 proto bgp metric 20
```

Delete the environment with `--clean`:

```
$ ./test.py --clean
cleanup(): bgp-unnumbered-spine-821a1 deleted
cleanup(): bgp-unnumbered-leaf-ee4e0 deleted
cleanup(): bgp-unnumbered-spine-020cd deleted
cleanup(): bgp-unnumbered-spine-2cf9d deleted
cleanup(): 2cf9d-c253e deleted
cleanup(): 4cf58-2ace1 deleted
cleanup(): 5a73e-770c2 deleted
cleanup(): ebb40-15492 deleted
cleanup(): ebb40-2542b deleted
cleanup(): ebb40-6ea34 deleted
```

## Components

This example uses Debian and FRR.  The basic components follow.

A loopback /32 ip, and one or more unnumbered "up" interfaces:

```
auto lo
iface lo inet loopback

auto lo:10
iface lo:10 inet static
    address 10.0.254.254
    netmask 255.255.255.255

auto enp5s0
allow-hotplug enp5s0
iface enp5s0 inet manual
```

frr configured to peer using interfaces rather than ips:

```
router bgp 64513
  neighbor enp5s0 interface remote-as external
  ...
```

routing enabled in the kernel:

```
net.ipv4.conf.all.forwarding=1
net.ipv6.conf.all.forwarding=1
```

Ansible is used to demonstrate the ease of templating such a configuration.  We can easily loop through a list of interfaces and enable bgp:

```
router bgp {{ router_as }}
  bgp router-id {{ router_ip }}
{% for i in ansible_interfaces|sort %}
{% if i.startswith('enp') or i.startswith('eno') or i.startswith('eth') %}
  neighbor {{i}} interface remote-as external
{% endif %}
{% endfor %}
  address-family ipv4 unicast
    network {{ router_net }}
```

Resulting in a device where I can simply plug-and-play on any port:

```
 router bgp 64513
   bgp router-id 10.0.254.254
+  neighbor enp2s4f0 interface remote-as external
+  neighbor enp2s4f1 interface remote-as external
+  neighbor enp3s6f0 interface remote-as external
+  neighbor enp3s6f1 interface remote-as external
+  neighbor enp3s8f0 interface remote-as external
+  neighbor enp3s8f1 interface remote-as external
+  neighbor enp4s0 interface remote-as external
   neighbor enp5s0 interface remote-as external
   address-family ipv4 unicast
     network 10.0.254.254/32
```

## BGP over an 802.1q tagged link layer

Dec. 2022

Even in the presence of the "perfect" datacenter fabric - its still nice to have basic dhcp-enabled l2 access in a rack.  Most commonly known as the "management network".
For example, with the way I use the debian preseeder - unpovisioned hosts need to be able to access apt repositories to download frr in the first place.

In order to implement a "dumb" management network while still meeting the goal of "no broadcast domains spanning more than one rack", I've elected to move all l3 bgp-enabled links to an 802.1q tagged namespace.  (I use this language because it wouldn't quite be accurate to think of these links as members of "a vlan").
Doing so allows top-of-rack spine router ports to serve dual-purpose - from the untagged perspective, each port is running its own dhcpd service, for its own psuedo-random rfc1918 network, of which it is the default gateway.  From the tagged perspective - frr its configured to listen on vlan 10 tagged vitual interfaces.

This is indeed one more layer of complexity, but it is very easy to template this all out with systemd-networkd and ansible.

The end result - unconfigured or otherwise "dumb" devices can be plugged into any spine router port and get an ip, while configured servers and routers can use the same physical ports to participate in the multi-path routed "fabric".

Note, it was a quite intentional decision not to simply bridge all the untagged ports, but to provision 'n' throwaway dhcpd intances and networks - in order to keep them all isolated from a link-layer perspective.  Bridging them would un-solve the l2 problems we're trying to avoid in the first place - for example making the datacenter fabric susceptible to broadcast storms happening in the management network.

Also note, our untagged physical ports are technically no longer "unnumbered".  Though, there isn't really a reason to know or care what these IPs actually are - other than the range that is being used.
With the range in mind, we can go template out any firewall rules deemed appropriate to keep managment traffic out of dc fabric ranges, and deploy that to the routers.
Admittedly, my logic doesn't check for collisions when coming up with IPs to provision, but this is a solvable problem.

The next step is to integrate pxe infrastructure with the dhcpd servers on the spines, allowing a fresh server to be plugged into each spine, boot installation media from pxe, perform a self-install, and then reboot and begin bgp peering.

This method of using the untagged vlan as l2 access in a bgp-to-the-host environment was not an idea of my own, but came from an anecdote i read on a network engineering forum years ago - i dont remember exactly where.

## L2 overlays with BGP EVPN

BGP is capable of dynamically propogating vni endponts for establishing unicast vxlans.

In this unnumbered environment, we simply activate the protocol on each interface neighbor:

```
  address-family l2vpn evpn
    neighbor eno1 activate
    neighbor eno2 activate
    advertise-all-vni
```

The same is done on the spines.

Then bring up a vxlan interface and bridge using our existing loopback address for the local tunnel ip:

```
ip link add vxlan100 type vxlan id 100 dstport 4789 local 10.0.200.3 nolearning
brctl addbr br100
brctl addif br100 vxlan100
ip link set up dev br100
ip link set up dev vxlan10
```

Once this is set up on some hypervisors we want to participate in the vxlan, they should be able to see prefixes to the other vxlan endpoints:

```
root@apollolake-6e10:~# vtysh <<< "show bgp evpn route"

Hello, this is FRRouting (version 7.5.1).
Copyright 1996-2005 Kunihiro Ishiguro, et al.

apollolake-6e10# show bgp evpn route
BGP table version is 1, local router ID is 10.0.200.2
Status codes: s suppressed, d damped, h history, * valid, > best, i - internal
Origin codes: i - IGP, e - EGP, ? - incomplete
EVPN type-1 prefix: [1]:[ESI]:[EthTag]:[IPlen]:[VTEP-IP]
EVPN type-2 prefix: [2]:[EthTag]:[MAClen]:[MAC]:[IPlen]:[IP]
EVPN type-3 prefix: [3]:[EthTag]:[IPlen]:[OrigIP]
EVPN type-4 prefix: [4]:[ESI]:[IPlen]:[OrigIP]
EVPN type-5 prefix: [5]:[EthTag]:[IPlen]:[IP]

   Network          Next Hop            Metric LocPrf Weight Path
                    Extended Community
Route Distinguisher: 10.0.200.2:2
*> [3]:[0]:[32]:[10.0.200.2]
                    10.0.200.2(apollolake-6e10)
                                                       32768 i
                    ET:8 RT:64791:100
Route Distinguisher: 10.0.200.2:3
*> [3]:[0]:[32]:[10.0.200.2]
                    10.0.200.2(apollolake-6e10)
                                                       32768 i
                    ET:8 RT:64791:200
Route Distinguisher: 10.0.200.3:2
*> [3]:[0]:[32]:[10.0.200.3]
                    10.0.200.3(apollolake-179e)
                                                           0 65451 i
                    RT:65451:100 ET:8
Route Distinguisher: 10.0.200.3:3
*> [3]:[0]:[32]:[10.0.200.3]
                    10.0.200.3(apollolake-179e)
                                                           0 65451 i
                    RT:65451:200 ET:8

Displayed 4 prefixes (4 paths)
```

And if we ping broadcast from one node, we should be able to tcpdump it from other:

```
root@apollolake-179e:~# ping -b 255.255.255.255 -I br100
WARNING: pinging broadcast address
```

...

```
root@apollolake-6e10:~# tcpdump -i br100
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on br100, link-type EN10MB (Ethernet), snapshot length 262144 bytes
02:19:41.766268 IP apollolake-179e > 255.255.255.255: ICMP echo request, id 29320, seq 1, length 64
02:19:42.798323 IP apollolake-179e > 255.255.255.255: ICMP echo request, id 29320, seq 2, length 64
02:19:49.565221 IP apollolake-179e > 255.255.255.255: ICMP echo request, id 30512, seq 1, length 64
```

And now we have a purely programmable layer 2 segment running over our ideal L3 substrate!

## Booting VMs on the vxlan

Now the we have a distributed L2 segment, lets connect some VMs, get them addressed, talking to eachother, and talking to the internet.

We'll use LXD.

I've given each hypervisor's br100 an address in the 172.16.0.0/24 space.  172.16.0.1 looks like this:

```
4: vxlan100: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue master br100 state UNKNOWN group default qlen 1000
    link/ether 72:94:76:9c:3d:2a brd ff:ff:ff:ff:ff:ff
    inet6 fe80::7094:76ff:fe9c:3d2a/64 scope link 
       valid_lft forever preferred_lft forever
5: br100: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    link/ether 3e:17:ca:87:9c:02 brd ff:ff:ff:ff:ff:ff
    inet 172.16.0.1/24 scope global br100
       valid_lft forever preferred_lft forever
    inet6 fe80::3c17:caff:fe87:9c02/64 scope link 
       valid_lft forever preferred_lft forever
```

Thanks to the `advertise all connected` frr configuration, the bgp deployment learns that the hypervisors all have paths into 172.16.0.0/24:

```
vtysh <<< "show ip bgp"
...
*  172.16.0.0/24    enp3s6f0                 0             0 65451 ?
*>                  enp3s8f0                 0             0 65489 ?
```

Now we tell lxd to use this bridge for our default 'profile':

```
lxc profile edit default
...
    parent: br100
...
```

I've created a quick dhcp server on the segment with a static ip:

```
lxc launch images:debian/12 vxlan100-dhcp
...
apt isntall isc-dchp-server
...
root@vxlan100-dhcp:~# cat /etc/systemd/network/eth0.network 
[Match]
Name=eth0
[Network]
Address=172.16.0.10/24
Gateway=172.16.0.1
DNS=1.1.1.1
root@vxlan100-dhcp:~# cat /etc/dhcp/dhcpd.conf
...
subnet 172.16.0.0 netmask 255.255.255.0 {
  range 172.16.0.11 172.16.0.254;
  option domain-name-servers 1.1.1.1;
  option routers 172.16.0.1;
  default-lease-time 600;
  max-lease-time 7200;
}
```

Notice we're using one of the hypervisor's /24 bridge addresses for the default gateway.  We'll discuss the issue with this later.

With dhcp in place, we can launch arbitrary instances:

```
lxc launch images:debian/12 iperf1
lxc launch images:debian/12 iperf2
```

Note they end up on random nodes, but they do indeed get addresses from vxlan100-dhcp:

```
~# lxc ls
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
|     NAME      |  STATE  |        IPV4        | IPV6 |      TYPE       | SNAPSHOTS |    LOCATION     |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
| iperf1        | RUNNING | 172.16.0.15 (eth0) |      | CONTAINER       | 0         | apollolake-179e |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
| iperf2        | RUNNING | 172.16.0.14 (eth0) |      | CONTAINER       | 0         | apollolake-6e10 |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
| vxlan100-dhcp | RUNNING | 172.16.0.10 (eth0) |      | CONTAINER       | 0         | apollolake-179e |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
```

They can access the internet:

```
root@iperf1:~# apt install iperf
...
Unpacking iperf (2.1.7+dfsg1-1) ...
Setting up iperf (2.1.7+dfsg1-1) ...
```

And for the grand reveal, lets check the inter-node bandwidth of the overlay:

```
$ lxc exec iperf1 -- iperf -s
...
$ lxc exec iperf2 -- iperf -c 172.16.0.15
------------------------------------------------------------
Client connecting to 172.16.0.15, TCP port 5001
TCP window size: 16.0 KByte (default)
------------------------------------------------------------
[  1] local 172.16.0.14 port 52832 connected with 172.16.0.15 port 5001 (icwnd/mss/irtt=14/1448/510)
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-10.0261 sec  1.06 GBytes   908 Mbits/sec
```

908 Mbits/sec from VM to VM, between physical nodes, over the vxlan.

_note on guest nics regarding gateways_

I found that LXD's default macvlan nic type confused arp when the vxlan's default gateway happened to coexist on the same node as a guest.  Other guests would function as expected but not guests co-located with the current 'addressed' default gateway bridge.

arp errors:

```
~# arp
Address                  HWtype  HWaddress           Flags Mask            Iface
172.16.0.15                      (incomplete)                              br100  <- this is a local guest in br100.  we cant ping this.
172.16.0.14              ether   00:16:3e:ec:52:35   C                     br100  <- this is a functional guest on a different hypervisor.
```

The solution was to set `nictype: bridged` using `lxc profile edit default`.  This creates veth devices which are added to the linux bridge.  Using this mode, everything works exactly as one would expect:

```
~# brctl show br100
bridge name	bridge id		STP enabled	interfaces
br100		8000.9ecb5becbcf8	no		veth619ff68e
							veth8ded2ea2
							vxlan100
~# arp
Address                  HWtype  HWaddress           Flags Mask            Iface
172.16.0.20              ether   00:16:3e:9b:d3:4c   C                     br100  <- local guest
172.16.0.21              ether   00:16:3e:06:07:3c   C                     br100  <- remote guest
```

Correctly learned gateway from guests' prespective:

```
~# lxc exec iperf1 -- arp
Address                  HWtype  HWaddress           Flags Mask            Iface
_gateway                 ether   9e:cb:5b:ec:bc:f8   C                     eth0
vxlan100-dhcp            ether   00:16:3e:4c:b1:32   C                     eth0
```

## North/South vxlan traffic

North / South traffic in and out of the vxlan is something I havent yet found a good solution for.  Since we're using only one of the hypervisors' /24 bridge addresses for the default gateway of the virtual network, if this node is rebooted, the entire segment will lose internet access.

I tried using keepalived on the bridges, but this didn't initially seem to work well with bgp.  I have yet to troubleshoot why.  I may or may not continue to investigate this.

Even if keepalived did work, we have an issue with less-than-ideal routes in and out of the segment.  If gateway 172.16.0.1 happens to exist on hypervisor-0 at a given time, and a vm on hypervisor-1 wants to talk to the internet, the traffic will have to make an extra hop from hypervisor-1, through hypervisor-0, and then to the upstream router.  Perhaps some of this can be alleviated by placing the vxlan gateways on the spines instead of the hypervisors, since all the traffic flows through them anyway.  Even then, we will be limited by the capacity of a single spine for north/south traffic.  It stands to reason that we can't expect to solve all L2 problems, hence the motivation to implement a pure L3 network in the first place.

Perhaps instead of hacking L2 technologies to build some sort of pseudo-highly-available north/south vxlan gateways, we take a software-operator approach.  Consider the following:

- a python or golang daemon on each hypervisor that watches LXD and brings up an ip in the vxlan address space whenever "i become the database-leader".
- this will take advantage of LXD's dqlite CP properties; we will not enter a split brain.
- this will take advantage of our BGP configuration; when a node assumes an address, it will start advertising that network.
- when a node that previously had the gateway address finds it is no longer the raft master, it will drop the address.
- tie directly into dqlite on disk, skipping the LXD api.  this will hopefully improve stability and compute efficiency.
- if dealing with many L2 segments/gateways, hash-map gateways accross online cluster members rather than using the "database-leader".
- going a step farther, incorporate addresses and physical locations of known instances while choosing a target cluster node for a slight "free" improvement in locality

This approach would afford us a kubernetes-like "eventual availability" quality for our vxlan default gateways, while likely being a bit more robust and fault tolerant than some sort of glued-together assortment of keepalived and virtual routers.

The only thing left would be to implement redundant dhcpd servers, which is an easy task.

_a day later_

Having read the [nvidia/cumulus document](https://docs.nvidia.com/networking-ethernet-software/cumulus-linux-37/Network-Virtualization/Ethernet-Virtual-Private-Network-EVPN/#enable-evpn-between-bgp-neighbors) on evpn implementation, i've learned of the anycast gateway approach.  Upon initial investigation, this appears to sort of work, but I'll have to learn more about how to avoid mac/arp confusion.

Implementing the gateways ad-hoc, on each leaf:

```
ip link add svi100 type veth
ip link set veth0 up
ip link set dev svi100 address aa:bb:ba:cf:8c:88
ip link set svi100 up
ip ad add 172.16.0.254/24 dev svi100
brctl addif br100 veth0
```

## Performance

Test router is a Supermicro Atom D525 system with integrated 1gb interfaces.  I tested latency and bandwidth through a standard switch, and then the D525 linux router, using similarly specced systems as the client and server.

pure l2:

```
client---hp_procurve---server
```

```
943 Mbits/sec
```

```
10 packets transmitted, 10 received, 0% packet loss, time 9219ms
rtt min/avg/max/mdev = 0.195/0.240/0.383/0.050 ms
```

pure l3:

```
client---D525_router---server
```

```
941 Mbits/sec
```

```
10 packets transmitted, 10 received, 0% packet loss, time 9112ms
rtt min/avg/max/mdev = 0.429/0.520/0.578/0.055 ms
```

While routing iperf traffic, the D525 reaches a maximum utilization of roughly 10% of one cpu core.

The x86 router appears to offer similar bandwidth to the switch, and about double the latency.  Packets per second will suffer compared to a router with purpose-built hardware.
