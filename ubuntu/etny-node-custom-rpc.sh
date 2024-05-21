#!/bin/bash
nodefolder=$(pwd)
configfile="config"

# Default RPC URLs
DEFAULT_BLOXBERG_RPC="https://bloxberg.ethernity.cloud"
DEFAULT_TESTNET_RPC="https://bloxberg.ethernity.cloud"
DEFAULT_POLYGON_RPC="https://polygon-rpc.com"
DEFAULT_MUMBAI_RPC="https://rpc-amoy.polygon.technology"

custom_rpc() {
    echo "Use custom RPC URL? (y/N)"
    read -r use_custom_rpc
    if [[ "$use_custom_rpc" =~ ^[Yy]$ ]]; then
        for network in bloxberg testnet polygon mumbai; do
            rpc_check "$network"
        done
    else
        set_default_rpc
    fi
}

rpc_check() {
    network=$1
    var_name=$(echo "${network^^}_RPC_URL")
    current_rpc=$(grep "$var_name" "$nodefolder/$configfile" | cut -d'=' -f2)

    if [[ -n "$current_rpc" ]]; then
        echo "$network RPC URL already exists in the config file and is currently set to $current_rpc."
        export "$var_name"="$current_rpc"
        echo "Would you like to modify it? (y/N)"
        read -r modify
        if [[ "$modify" =~ ^[Yy]$ ]]; then
            set_rpc "$network"
        fi
    else
        echo "The Custom $network RPC URL is not set in the config file."
        echo "Do you want to set it? (y/N)"
        read -r change_rpc
        if [[ "$change_rpc" =~ ^[Yy]$ ]]; then
            set_rpc "$network"
        fi
    fi
}

set_rpc() {
    network=$1
    var_name=$(echo "${network^^}_RPC_URL")
    while true; do
        echo -n "Enter Custom $network RPC URL: "
        read -r custom_rpc
        if [[ -n "$custom_rpc" ]]; then
            export "$var_name"="$custom_rpc"
            break
        else
            echo "I need URL..."
        fi
    done
}

set_default_rpc() {
    export BLOXBERG_RPC_URL="$DEFAULT_BLOXBERG_RPC"
    export TESTNET_RPC_URL="$DEFAULT_TESTNET_RPC"
    export POLYGON_RPC_URL="$DEFAULT_POLYGON_RPC"
    export MUMBAI_RPC_URL="$DEFAULT_MUMBAI_RPC"

    echo "Using default RPC URLs:"
    echo "BLOXBERG_RPC_URL: $BLOXBERG_RPC_URL"
    echo "TESTNET_RPC_URL: $TESTNET_RPC_URL"
    echo "POLYGON_RPC_URL: $POLYGON_RPC_URL"
    echo "MUMBAI_RPC_URL: $MUMBAI_RPC_URL"
}

