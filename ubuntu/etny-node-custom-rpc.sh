#!/bin/bash
nodefolder=$(pwd)
configfile="config"

custom_rpc() {
    echo "Use custom RPC URL ? (Y/n)"
    read -r use_custom_rpc
    if [[ -z "$use_custom_rpc" ]] || [[ "$use_custom_rpc" =~ ^[Yy]$ ]]; then
	rpc_check_bloxberg
	rpc_check_testnet
	rpc_check_polygon
	rpc_check_mumbai
    fi
}

rpc_check_bloxberg() {
    current_bloxberg_rpc=$(grep "BLOXBERG_RPC_URL" "$nodefolder/$configfile" | cut -d'=' -f2)
    if [ "$current_bloxberg_rpc" != "" ];
    then
        echo "Custom BLOXBERG RPC URL already exists in the config file and is currently set to $current_bloxberg_rpc ."
	export BLOXBERG_RPC_URL=$current_bloxberg_rpc
        echo "Would you like to modify it? (y/N)"
        read modify
	if [[ "$modify" =~ ^[Yy]$ ]]; then
         set_rpc_bloxberg
	fi
    else
        echo "The Custom BLOXBERG RPC URL is not set in the config file."
        echo "Do you want to set (Y/n)"
        read -r change_rpc_bloxberg
        if [[ -z "$change_rpc_bloxberg" ]] || [[ "$change_rpc_bloxberg" =~ ^[Yy]$ ]]; then
	    set_rpc_bloxberg
        fi
    fi

}

set_rpc_bloxberg() {
    while true; do
        echo -n "Enter Custom BLOXBERG RPC URL : "
        read custom_bloxberg_rpc
        if [[ $custom_bloxberg_rpc != "" ]]; then
            break
        else
            echo "I need URL ..."
        fi
    done
    export BLOXBERG_RPC_URL=$custom_bloxberg_rpc
}

rpc_check_testnet() {
    current_testnet_rpc=$(grep "TESTNET_RPC_URL" "$nodefolder/$configfile" | cut -d'=' -f2)
    if [ "$current_testnet_rpc" != "" ];
    then
        echo "Custom TESTNET RPC URL already exists in the config file and is currently set to $current_testnet_rpc ."
	export testnet_RPC_URL=$current_testnet_rpc
        echo "Would you like to modify it? (y/N)"
        read modify
	if [[ "$modify" =~ ^[Yy]$ ]]; then
         set_rpc_testnet
	fi
    else
        echo "The Custom TESTNET RPC URL is not set in the config file."
        echo "Do you want to set (Y/n)"
        read -r change_rpc_testnet
        if [[ -z "$change_rpc_testnet" ]] || [[ "$change_rpc_testnet" =~ ^[Yy]$ ]]; then
	    set_rpc_testnet
        fi
    fi

}

set_rpc_testnet() {
    while true; do
        echo -n "Enter Custom TESTNET RPC URL : "
        read custom_testnet_rpc
        if [[ $custom_testnet_rpc != "" ]]; then
            break
        else
            echo "I need URL ..."
        fi
    done
    export TESTNET_RPC_URL=$custom_testnet_rpc
}

rpc_check_polygon() {
    current_polygon_rpc=$(grep "POLYGON_RPC_URL" "$nodefolder/$configfile" | cut -d'=' -f2)
    if [ "$current_polygon_rpc" != "" ];
    then
        echo "Custom POLYGON RPC URL already exists in the config file and is currently set to $current_polygon_rpc ."
	export POLYGON_RPC_URL=$current_polygon_rpc
        echo "Would you like to modify it? (y/N)"
        read modify
	if [[ "$modify" =~ ^[Yy]$ ]]; then
         set_rpc_polygon
	fi
    else
        echo "The Custom POLYGON RPC URL is not set in the config file."
        echo "Do you want to set (Y/n)"
        read -r change_rpc_polygon
        if [[ -z "$change_rpc_polygon" ]] || [[ "$change_rpc_polygon" =~ ^[Yy]$ ]]; then
	    set_rpc_polygon
        fi
    fi

}

set_rpc_polygon() {
    while true; do
        echo -n "Enter Custom POLYGON RPC URL : "
        read custom_polygon_rpc
        if [[ $custom_polygon_rpc != "" ]]; then
            break
        else
            echo "I need URL ..."
        fi
    done
    export POLYGON_RPC_URL=$custom_polygon_rpc
}

rpc_check_mumbai() {
    current_mumbai_rpc=$(grep "MUMBAI_RPC_URL" "$nodefolder/$configfile" | cut -d'=' -f2)
    if [ "$current_mumbai_rpc" != "" ];
    then
        echo "Custom MUMBAI RPC URL already exists in the config file and is currently set to $current_mumbai_rpc ."
	export mumbai_RPC_URL=$current_mumbai_rpc
        echo "Would you like to modify it? (y/N)"
        read modify
	if [[ "$modify" =~ ^[Yy]$ ]]; then
         set_rpc_mumbai
    fi
    else
        echo "The Custom MUMBAI RPC URL is not set in the config file."
        echo "Do you want to set (Y/n)"
        read -r change_rpc_mumbai
        if [[ -z "$change_rpc_mumbai" ]] || [[ "$change_rpc_mumbai" =~ ^[Yy]$ ]]; then
	    set_rpc_mumbai
        fi
    fi

}

set_rpc_mumbai() {
    while true; do
        echo -n "Enter Custom MUMBAI RPC URL : "
        read custom_mumbai_rpc
        if [[ $custom_mumbai_rpc != "" ]]; then
            break
        else
            echo "I need URL ..."
        fi
    done
    export MUMBAI_RPC_URL=$custom_mumbai_rpc
}