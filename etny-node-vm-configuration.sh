#!/bin/bash

# Define minimum and recommended settings
MIN_MEMORY_MB=2048
MIN_CPUS=2
MIN_DISK=80

REC_MEMORY_MB=7168
REC_CPUS=2
REC_DISK=200

# Function to get available resources
get_available_resources() {
    total_memory=$(free -m | awk '/Mem:/ {print $2 - 1024}')
    available_memory=$((total_memory - 1))
    available_cpus=$(nproc)

    # Calculate available storage by adding the current Vagrant disk image size
    vagrant_disk_size=$(sudo fdisk -l /var/lib/libvirt/images/mvp-pox-node_etnyvm1.img | grep 'Disk /' | awk '{print $3}')
    available_disk_size=$(df -BG / | awk '/\// {print $4}' | sed 's/G//')
    available_disk_size=$(echo "$available_disk_size + $vagrant_disk_size" | bc)
}

# Function to modify the Vagrantfile
modify_vagrantfile() {
    sed -i.bak -E "s/(domain.memory[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$new_memory_MB/" Vagrantfile
}

# Get available resources
get_available_resources

echo "Available memory: $available_memory MB, available CPUs: $available_cpus, available disk size: $available_disk_size GB"

read -p "Enter allocated memory size (RAM MB) (minimum $MIN_MEMORY_MB, recommended $REC_MEMORY_MB): " new_memory_MB

# Check for available memory
if (( $new_memory_MB < $MIN_MEMORY_MB )); then
    echo "Error: Entered memory is less than the minimum required."
    exit 1
fi

if (( $new_memory_MB > $available_memory )); then
    echo "Error: Entered memory is greater than available memory."
    exit 1
fi

read -p "Enter allocated CPU count (minimum $MIN_CPUS, recommended $REC_CPUS): " new_cpus

# Check for available CPUs
if (( $new_cpus < $MIN_CPUS )); then
    echo "Error: Entered CPU count is less than the minimum required."
    exit 1
fi

if (( $new_cpus > $available_cpus )); then
    echo "Error: Entered CPU count is greater than available CPUs."
    exit 1
fi

# Check for available storage
read -p "Enter additional allocated storage (GB) (minimum $MIN_DISK, recommended $REC_DISK): " additional_storage

# Check for minimum storage requirement
if (( $additional_storage < $MIN_DISK )); then
    echo "Error: Entered additional storage is less than the minimum required."
    exit 1
fi

# Calculate the new total storage size
new_total_storage=$(echo "$available_disk_size + $additional_storage" | bc)

if (( $(awk -v a="$new_total_storage" -v b="$new_total_storage" 'BEGIN {print (a > b)}') )); then
    echo "Error: Entered additional storage is greater than the available storage."
    exit 1
fi

# Provide feedback before modifying Vagrantfile
echo "Calculating new storage configuration..."
echo "New total storage: $new_total_storage GB"
echo "Please wait, updating Vagrantfile..."

# Modify Vagrantfile
modify_vagrantfile

echo "Backup of the original Vagrantfile created: Vagrantfile.bak"
echo "Vagrantfile updated successfully!"

echo "Reloading Vagrant..."
vagrant reload
echo "Vagrant reloaded successfully!"

# Resize VM disk
vagrant ssh -c "sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv"
vagrant ssh -c "sudo resize2fs /dev/mapper/ubuntu--vg-ubuntu--lv"
echo "Disk resized successfully!"
echo "Configuration done"
