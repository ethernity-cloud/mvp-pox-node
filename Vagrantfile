#
# Copyright (C) 2018, 2019 Ethernity HODL UG
#
# This file is part of ETHERNITY NODE.
#
# ETHERNITY SC is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#

Vagrant.configure("2") do |config|
  interface = `ip a | grep '2:' | head -1 | awk '{print $2}' | awk -F ':' '{print $1}'`
  
  config.vm.define :etnyvm1 do |etnyvm1|
    etnyvm1.vm.box = "generic/ubuntu2204"
    etnyvm1.vm.provider :libvirt do |domain|
      domain.memory = 2048
      domain.cpus = 2 
      domain.cpu_mode = 'host-passthrough'
      domain.nested = true
      domain.kvm_hidden = false
      domain.machine_virtual_size = 128
      domain.disk_driver :cache => 'none'
      domain.qemuargs :value => '-cpu'
      domain.qemuargs :value => 'host,+sgx'
      domain.qemuargs :value => '-object'
      domain.qemuargs :value => 'memory-backend-epc,id=mem1,size=64M,prealloc=on'
      domain.qemuargs :value => '-M'
      domain.qemuargs :value => 'sgx-epc.0.memdev=mem1'
    end
    etnyvm1.vm.network :public_network,
      :dev => interface.strip!,
      :mode => 'vepa',
      :type => 'direct'
    etnyvm1.vm.provision "file", source: "./ubuntu/etny-node-provision-docker.sh", destination: "~/etny/node/etny-node-provision-docker.sh"
    etnyvm1.vm.provision "file", source: "./ubuntu/etny-node-provision-sgx-ubuntu22.sh", destination: "~/etny/node/etny-node-provision-sgx.sh"
    etnyvm1.vm.provision "file", source: "./ubuntu/etny-node-provision-python.sh", destination: "~/etny/node/etny-node-provision-python.sh"
    etnyvm1.vm.provision "file", source: "./ubuntu/etny-node-provision-ipfs.sh", destination: "~/etny/node/etny-node-provision-ipfs.sh"
    etnyvm1.vm.provision "file", source: "./ubuntu/etny-node-provision-etny.sh", destination: "~/etny/node/etny-node-provision-etny.sh"
    etnyvm1.vm.provision "file", source: "./ubuntu/etny-node-start.sh", destination: "~/etny/node/etny-node-start.sh"
    etnyvm1.vm.provision "file", source: "./ubuntu/etc/systemd/system/etny-node.service", destination: "~/etny/node/etny-node.service"
    etnyvm1.vm.provision "file", source: "./config", destination: "~/etny/node/config"
    etnyvm1.vm.provision "shell",
      inline: "/bin/bash /home/vagrant/etny/node/etny-node-provision-docker.sh"
    etnyvm1.vm.provision :reload
    etnyvm1.vm.provision "shell",
      inline: "/bin/bash /home/vagrant/etny/node/etny-node-provision-sgx.sh"
    etnyvm1.vm.provision :reload
    etnyvm1.vm.provision "shell",
      inline: "/bin/bash /home/vagrant/etny/node/etny-node-provision-python.sh"
    etnyvm1.vm.provision "shell",
      inline: "/bin/bash /home/vagrant/etny/node/etny-node-provision-ipfs.sh"
    etnyvm1.vm.provision "shell",
      inline: "/bin/bash /home/vagrant/etny/node/etny-node-provision-etny.sh"
    etnyvm1.vm.provision "shell",
      inline: "/bin/mv /home/vagrant/etny/node/etny-node.service /etc/systemd/system/"
    etnyvm1.vm.provision "shell",
      inline: "/bin/systemctl start etny-node.service"
    etnyvm1.vm.provision "shell",
      inline: "/bin/systemctl enable etny-node.service"
  end

end
