import uuid
import time


class Router:
    def __init__(self, client, role, sshkey):
        rnd = str(uuid.uuid4())[0:5]
        self.name = "bgp-{}-{}".format(role, rnd)
        image = "debian/12"
        config = {
            "name": self.name,
            "description": '{"bgp-unnumbered": true, "role": "%s"}' % role,
            "source": {
                "type": "image",
                "mode": "pull",
                "server": "https://images.linuxcontainers.org",
                "protocol": "simplestreams",
                "alias": image,
            },
            "config": {"limits.cpu": "2", "limits.memory": "1GB"},
            "type": "container",
        }

        self.inst = client.instances.create(config, wait=True)
        self.inst.start(wait=True)
        self.wait_until_ready()

        if "rocky" in image.lower():
            pkgm = "yum"
        elif "debian" or "ubuntu" in image.lower():
            pkgm = "apt"

        err = self.inst.execute(
            [pkgm, "install", "python3", "openssh-server", "ca-certificates", "-y"]
        )
        if err.exit_code != 0:
            raise RuntimeError(err.stderr)
        err = self.inst.execute(["mkdir", "-p", "/root/.ssh"])
        if err.exit_code != 0:
            raise RuntimeError(err.stderr)

        self.inst.files.put("/root/.ssh/authorized_keys", sshkey.exportKey("OpenSSH"))
        # wow! subsequent reboots in network configuration were borking our ssh installation/configuration
        self.inst.execute(["sync"])

    def wait_until_ready(self):
        """
        waits until an instance is executable
        """
        print("waiting for lxd agent to become ready on " + self.name)
        count = 30
        for i in range(count):
            try:
                exit_code = self.inst.execute(["hostname"]).exit_code
            except BrokenPipeError:
                continue

            if exit_code == 0:
                break
            if i == count - 1:
                raise TimeoutError("timed out waiting")
            time.sleep(1)
