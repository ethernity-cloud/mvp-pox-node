#!/bin/bash

# Define minimum and recommended settings
MIN_MEMORY=3 # In GB
MIN_CPUS=2
MIN_DISK=128

REC_MEMORY=7 # In GB
REC_CPUS=2
REC_DISK=256

# Function to get available resources
get_available_resources() {
    total_memory=$(free -g | awk '/Mem:/ {print $2}')
    available_memory=$((total_memory - 1))
    available_cpus=$(nproc)
    available_disk_size=$(df -BG / | awk '/\// {print $4}' | sed 's/G//')
}

# Function to modify the Vagrantfile
modify_vagrantfile() {
    sed -i -E "s/(domain.memory[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$new_memory_MB/" Vagrantfile
    sed -i -E "s/(domain.cpus[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$new_cpus/" Vagrantfile
    sed -i -E "s/(domain.machine_virtual_size[[:space:]]*=[[:space:]]*)[[:digit:]]+/\1$new_machine_virtual_size/" Vagrantfile
}

# Get available resources
get_available_resources

# Ask the user for input
echo "Available memory: $available_memory GB, available CPUs: $available_cpus, available disk size: $available_disk_size GB"

read -p "Enter new memory size in GB (minimum $MIN_MEMORY, recommended $REC_MEMORY): " new_memory
read -p "Enter new CPU count (minimum $MIN_CPUS, recommended $REC_CPUS): " new_cpus
read -p "Enter new machine virtual size in MB (minimum $MIN_DISK, recommended $REC_DISK): " new_machine_virtual_size

new_memory_MB=$((new_memory * 1024))

if [[ $new_memory -lt $MIN_MEMORY || $new_cpus -lt $MIN_CPUS || $new_machine_virtual_size -lt $MIN_DISK ]]; then
    echo "Error: Entered settings do not meet the minimum requirements."
    exit 1
fi

modify_vagrantfile

echo "Vagrantfile updated successfully!"

echo "Reloading Vagrant..."
vagrant reload
echo "Vagrant reloaded successfully!"
