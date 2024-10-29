# Ansible libvirt (OpenStack ready)

[![Build Status](https://travis-ci.org/uoi-io/ansible-libvirt.svg?branch=master)](https://travis-ci.org/uoi-io/ansible-libvirt) [![Ansible Galaxy](https://img.shields.io/badge/galaxy-uoi.libvirt-green.svg?style=flat)](https://galaxy.ansible.com/uoi-io/libvirt/)

Install and configure libvirt service on many distributions.

Supported distributions:

- CentOS
- RedHat
- Debian
- Ubuntu
- Suse
- OpenSuse

Supported functionalities:

- Firewalld *(iptables and firewalld packages are needed on the server)*
- SELinux

## Requirements
This module needs at least Ansible 2.x.

## Role Variables

```
# file: roles/libvirt/defaults/main.yml
libvirt_firewalld: true
libvirt_selinux: true
libvirt_port: 16509
libvirt_bind_address: 0.0.0.0
libvirt_config: []
libvirt_qemu_config: []
```

### VARIABLES
Because the module support RedHat and Debian distributions like, we have to define some values depending of the OS family.
```
### REDHAT
# file: roles/libvirt/vars/RedHat.yml
libvirt_packages:
  - libvirt-daemon-kvm
libvirt_daemon_config_file: /etc/sysconfig/libvirtd
libvirt_config_file: /etc/libvirt/libvirtd.conf
libvirt_qemu_config_file: /etc/libvirt/qemu.conf
libvirt_daemon_config:
  - { option: 'LIBVIRTD_ARGS', value: 'LIBVIRTD_ARGS="--listen"' }
```
```
### DEBIAN
# file: roles/libvirt/vars/Debian.yml
libvirt_packages:
  - libvirt-bin
libvirt_daemon_config_file: /etc/default/libvirtd 
libvirt_config_file: /etc/libvirt/libvirtd.conf
libvirt_qemu_config_file: /etc/libvirt/qemu.conf
libvirt_daemon_config:
  - { option: 'libvirtd_opts', value: 'libvirtd_opts="-l"' }
```
## Dependencies
None.

## Example Playbook
```
---
libvirt_bind_address: 10.10.150.23
libvirt_config:
  - { option: 'listen_tls', value: 'listen_tls = 0' }
  - { option: 'listen_tcp', value: 'listen_tcp = 1' }
  - { option: 'listen_addr', value: 'listen_addr = "{{ libvirt_bind_address }}"' }
  - { option: 'tcp_port', value: 'tcp_port = "{{ libvirt_port }}"' }
  - { option: 'auth_tcp', value: 'auth_tcp = "none" '}

libvirt_qemu_config:
  - { option: 'user', value: 'user = "nova"' }
  - { option: 'group', value: 'group = "nova"' }
  - { option: 'dynamic_ownership', value: 'dynamic_ownership = 0' }
```

## License
Apache

## Author Information
This role was created in 2016 by Gaëtan Trellu (goldyfruit).
