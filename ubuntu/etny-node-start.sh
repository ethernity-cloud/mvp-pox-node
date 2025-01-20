#!/bin/bash
set -e

trap 'echo "Installer status: \"${BASH_COMMAND}\"command end with exit code $?."' SIGINT SIGTERM ERR EXIT

. /home/vagrant/etny/node/config

cd /home/vagrant/etny/node/go-ipfs

if [ -v IPFS_HOSTNAME ]
then
  IPFS_HOST=${IPFS_HOSTNAME}
else
  IPFS_HOST="ipfs.ethernity.cloud";
fi

systemctl stop ipfs

if [ -v IPFS_LOCAL_CONNECT_URL ]
then
  systemctl disable ipfs
  IPFS_LOCAL=${IPFS_LOCAL_CONNECT_URL}
else
  systemctl enable ipfs
  systemctl start ipfs
  IPFS_LOCAL="/ip4/127.0.0.1/tcp/5001/http";
fi


resolve_ipfs_host () {
resolving=false
while [ $resolving == false ]
do
        IPFS_IP=`getent ahosts ${IPFS_HOST} | grep ${IPFS_HOST} | awk '{print $1}'`
        if [ "$IPFS_IP" != "" ]
	then
		IPFS_IP=$(echo ${IPFS_IP} | awk 'NR==1{print $1}')
		resolving=true
        else
		echo "Unable to resolve IPFS gateway ${IPFS_HOST}, please check DNS configuration"
        fi
done
}

if [ "$IPFS_HOSTNAME" == "ipfs.ethernity.cloud" ]
then

	resolve_ipfs_host

	until timeout 10 ./ipfs --api=${IPFS_LOCAL_CONNECT_URL} swarm connect /ip4/${IPFS_IP}/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
	do
        	echo "Unable to connect to IPFS gateway, please check IPFS configuration or restart the service"
		resolve_ipfs_host
		sleep 5
	done
	./ipfs --api=${IPFS_LOCAL_CONNECT_URL} bootstrap add /ip4/${IPFS_IP}/tcp/4001/ipfs/QmRBc1eBt4hpJQUqHqn6eA8ixQPD3LFcUDsn6coKBQtia5
fi

cd /home/vagrant/etny/node/etny-repo/node/
git fetch origin
git reset --hard origin/master
git pull

parse_config_to_args() {
    local config_file="/home/vagrant/etny/node/config"
    local args=()

    # Check if the config file exists and is readable
    if [[ ! -f "$config_file" ]]; then
        echo "Error: Config file '$config_file' not found." >&2
        return 1
    fi

    if [[ ! -r "$config_file" ]]; then
        echo "Error: Config file '$config_file' is not readable." >&2
        return 1
    fi

    # Read the config file line by line
    while IFS='=' read -r key value; do
        # Skip empty lines and lines starting with '#'
        [[ -z "$key" || "$key" =~ ^# ]] && continue

        # Trim whitespace from key and value
        key=$(echo "$key" | xargs)
        value=$(echo "$value" | xargs)

        # Ignore the NETWORK parameter
        if [[ "$key" == "NETWORK" ]] || [[ "$key" == "PRIVATE_KEY" ]]; then
            continue
        fi

        # Convert key to lowercase
        key_lower=$(echo "$key" | tr '[:upper:]' '[:lower:]')

        # Prefix with '--' and handle the value (quote if it contains spaces)
        if [[ "$value" =~ [[:space:]] ]]; then
            args+=("--$key_lower" "\"$value\"")
        else
            args+=("--$key_lower" "$value")
        fi
    done < "$config_file"

    # Join the arguments into a single string
    local args_str=""
    for arg in "${args[@]}"; do
        args_str+="$arg "
    done

    # Trim trailing space and echo the result
    echo "${args_str% }"
}

ARGS=$(parse_config_to_args)

COMMAND_LINE="/home/vagrant/etny/node/etny-repo/node/etny-node.py -k ${PRIVATE_KEY} -n ${NETWORK} ${ARGS}"

$COMMAND_LINE
