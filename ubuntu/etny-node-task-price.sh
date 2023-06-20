#!/bin/bash

nodefolder=$(pwd)
configfile="config"

task_price_prompt() {
    if grep -q "TASK_EXECUTION_PRICE" "$nodefolder/$configfile"; then
        current_price=$(grep "TASK_EXECUTION_PRICE" "$nodefolder/$configfile" | cut -d'=' -f2)
        echo "Task execution price already exists in the config file and is currently set to $current_price ETNY/hour."
        echo "Would you like to modify it? (Y/n)"
        read modify
        if [[ "$modify" =~ ^[Yy]$ ]]; then
            set_task_price
        fi
    else
        set_task_price
    fi
}

set_task_price() {
    while true; do
        echo -n "Enter the Task Execution Price (Recommended price for executing a task/hour: 0.001 ETNY - 10.00 ETNY): "
        read taskprice
        if [[ $taskprice =~ ^[0-9]+(\.[0-9]+)?$ ]] && (( $(echo "$taskprice >= 0.001 && $taskprice <= 10" | bc -l) )); then
            break
        else
            echo "Invalid task execution price. Please enter a valid price within the recommended range (0.001 ETNY - 10.00 ETNY per hour)..."
        fi
    done
    sed -i "/TASK_EXECUTION_PRICE/d" "$nodefolder/$configfile"
    echo "TASK_EXECUTION_PRICE=$taskprice" >> "$nodefolder/$configfile"
}
