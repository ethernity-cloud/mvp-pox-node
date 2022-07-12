#!/bin/bash
configfile="config"
nodefolder="/home/rkubiv/codemotion/eth-node/mvp-pox-node"

check_wallets(){
#checking if wallets are valid and how much bergs there are in the wallets
local address=$1
addrbergshexa=`curl --silent --data '{"method":"eth_getBalance","params":["'$address'"],"id":0,"jsonrpc":"2.0"}' -H "Content-Type: application/json" -X POST https://blockexplorer.bloxberg.org/api/eth_rpc | awk -F"," '{print $2}' | awk -F":" '{print $2}' | sed 's/"//g' | cut -c 3-`
case $addrbergshexa in
	*"invalid"*) echo "Invalid wallet address. Please fix..." && check_wallet_result=1;;
	*"not found"* | 0) echo "0 bergs. Please get bergs from https://faucet.bloxberg.org/ and run the installer again." && check_wallet_result=1;;
	[a-z0-9]*) 
	var=`bc <<<"scale=10; $(( 16#$addrbergshexa )) / 1000000000000000000"` 
	[[ $var = .* ]] | echo "0"$var "bergs. Continuing..." || echo $var "bergs. Continuing..."&& check_wallet_result=0;;
	*)	echo "Couldn't determine the number of bergs. Internet issue? Exiting..." && check_wallet_result=1;;
esac
}

start(){
#if the config file doesn't exist we offer the either generate one with random wallets or we get the wallets from input
echo "1) Generate config file with random wallets." 
echo "2) Type wallets. "
echo -n "[Type your choice to continue]:" && read choice
case "$choice" in 
	1) 
		echo "Generating config file..."
		./roles/generate_config_file/files/ethkey generate random | awk '!/public:/' | awk '{gsub("secret:","PRIVATE_KEY="); print}' | awk '{gsub("address:","ADDRESS="); print}' | awk '{ gsub(/ /,""); print }' | sed -n 'h;n;p;g;p' > $configfile
		./roles/generate_config_file/files/ethkey generate random | awk '!/public:/' | awk '{gsub("secret:","RESULT_PRIVATE_KEY="); print}' | awk '{gsub("address:","RESULT_ADDRESS="); print}' | awk '{ gsub(/ /,""); print }' | sed -n 'h;n;p;g;p' >> $configfile
		if [ -f $nodefolder/$configfile ]
		then 
			echo "Config file generated successfully. Continuing..." 
			echo -e '\033[1mMAKE SURE YOU REQUEST BERGS FROM https://faucet.bloxberg.org/ FOR THE WALLETS BELOW BEFORE CONTINUING\033[0m'
			cat $configfile | grep "^ADDRESS=" | awk -F"=" '{print $2}'
			cat $configfile | grep "RESULT_ADDRESS=" | awk -F"=" '{print $2}'
			echo -e '\033[1mWallet addresses can also be seen in the config file.\033[0m'
			read -rsn1 -p"Press any key to continue...";echo
			exit
		else echo "Something went wrong. Seek Help!" && exit
		fi
	;;
	2) 
		echo "Type/Paste wallet details below..."
		nodeaddr=("Node Address: " "Node Private Key: " "Result Address: " "Result Private Key: ")
		IFS=""
		for address in ${nodeaddr[@]}; do
			case $address in
				${nodeaddr[0]})
				while true
				do
					echo -n $address && read nodeaddress
					if [[ $nodeaddress = "" ]]; then echo "Node address cannot be empty."; else check_wallets $nodeaddress; fi
					if [[ $check_wallet_result = 0 ]]; then break; fi
				done;;
				${nodeaddr[2]})
					while true
					do
						echo -n $address && read resultaddress
						if [[ $nodeaddress = $resultaddress ]]
						then 
							echo "Result address must be different than the node address. Try a different address..."
						else
							check_wallets $resultaddress
							if [[ $check_wallet_result = 0 ]]; then break; fi
						fi
					done;;
				${nodeaddr[1]})
					while true
					do
						echo -n $address && read nodeprivatekey
						if [[ ${#nodeprivatekey} = 64 && $nodeprivatekey =~ ^[a-zA-Z0-9]*$ ]]
						then
							break
						else echo "Invalid result private key. Please try again..."
						fi
					done;;
				${nodeaddr[3]})
					while true
					do
						echo -n $address && read resultprivatekey
						if [[ ${#resultprivatekey} = 64 && $resultprivatekey =~ ^[a-zA-Z0-9]*$ ]]
						then
							if [[ $nodeprivatekey = $resultprivatekey ]]
							then
								echo "Result private key must be different than the node private key. Try a different private key..."
							else
								break
							fi
						else echo "Invalid result private key. Please try again..."
						fi
					done;;

			esac
		done
		echo "ADDRESS="$nodeaddress >> ~/$nodefolder/$configfile
		echo "PRIVATE_KEY="$nodeprivatekey >> ~/$nodefolder/$configfile
		echo "RESULT_ADDRESS="$resultaddress >> ~/$nodefolder/$configfile
		echo "RESULT_PRIVATE_KEY="$resultprivatekey >> ~/$nodefolder/$configfile
		if [ -f ~/$nodefolder/$configfile ]; then echo "Config file generated successfully. Continuing..." && ubuntu_20_04_kernel_check; else echo "Something went wrong. Seek Help!" && exit; fi
	;;
	*) echo "Invalid choice. Please choose an option below..." && start;;
esac
}



start
