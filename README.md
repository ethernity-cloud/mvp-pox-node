# Ethernity NODE

This repository provides requirements and setup instructions to enable Ethernity NODE capabilities on a regular Linux system.


Currently the following operating systems are suported:
```
Ubuntu 18.04
```

We are planning support for the following operating systems:
```
Debian 10*
Fedora 30*
CentOS 7*
RHEL 8*
```

# 1. Clone the repository

```
$ git clone https://github.com/ethernity-cloud/mvp-pox-node.git
```

# 2. Install ansible


```
$ sudo apt update
$ sudo apt -y install software-properties-common
$ sudo apt-add-repository --yes --update ppa:ansible/ansible
$ sudo apt -y install ansible
```

# 3. Install the ansible virtualization roles

```
$ sudo ansible-galaxy collection install crivetimihai.virtualization
```

# 4. Create config file:

```
$ cd mvp-pox-node
$ cat << EOF > config
PUBLIC_KEY=0x0123456789abcdef0123456789abcdef01234567
PRIVATE_KEY=0x0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF
RESULT_PUBLIC_KEY=0x0123456789abcdef0123456789abcdef01234567
RESULT_PRIVATE_KEY=0x0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF
EOF
$
```


# 5. Run the playbook

```
$ sudo ansible-playbook -i localhost, playbook.yml \
  -e "ansible_python_interpreter=/usr/bin/python3"
```



