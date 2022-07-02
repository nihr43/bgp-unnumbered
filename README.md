# bgp-unnumbered

A reference implementation of bgp routing over unnumbered network interfaces.

This is achieved by advertising ipv4 prefixes over ipv6 link-local nexthops.

By trading a little more day-1 complexity in setup, we gain significant day-2 operation benefits.  This architecture allows switch-like ease of configuration of individual links, while maintaining a non-blocking fault-tolerant network architecture that is ( ideally ) vendor-agnostic.

Allowing hypervisors to take part in the layer-3 topology allows an operator to achieve higher overall utilization of each individual link, more freely manipulate virtual layer-2 segments across dispersed physical infrastructre ( vxlans ), and better investigate issues when they do arise with the more robust layer-3 toolset.

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
  neighbor enp5s0 interface remote-as internal
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
  neighbor {{i}} interface remote-as internal
{% endif %}
{% endfor %}
  address-family ipv4 unicast
    network {{ router_net }}
```

Resulting in a device where I can simply plug-and-play on any port:

```
 router bgp 64513
   bgp router-id 10.0.254.254
+  neighbor enp2s4f0 interface remote-as internal
+  neighbor enp2s4f1 interface remote-as internal
+  neighbor enp3s6f0 interface remote-as internal
+  neighbor enp3s6f1 interface remote-as internal
+  neighbor enp3s8f0 interface remote-as internal
+  neighbor enp3s8f1 interface remote-as internal
+  neighbor enp4s0 interface remote-as internal
   neighbor enp5s0 interface remote-as internal
   address-family ipv4 unicast
     network 10.0.254.254/32
```

## L2 overlays with BGP EVPN

BGP is capable of dynamically propogating vni endponts for establishing unicast vxlans.

In this unnumbered environment, we simply activate the protocol on each interface neighbor:

```
  address-family l2vpn evpn
    neighbor eno1 activate
    neighbor eno2 activate
    advertise-all-vni
    advertise ipv4 unicast
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
