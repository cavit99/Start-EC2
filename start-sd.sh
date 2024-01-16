#!/bin/bash

# Define a log file
logfile="start-sd.log"

# Function to log messages
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" | tee -a $logfile
}

# Check if AWS CLI is configured
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    log "AWS CLI is not configured. Please configure it and try again."
    exit 1
fi

# Get the local public IP
local_ip=$(curl -s ifconfig.me)

# Check if local_ip is null or empty
if [ -z "$local_ip" ]; then
    log "Failed to get local public IP."
    exit 1
fi

# Check if the IP already exists in the security group
ip_exists=$(aws ec2 describe-security-groups --group-ids sg-0a2f9548b660b5d0a --region eu-north-1 | jq -r ".SecurityGroups[0].IpPermissions[] | select(.FromPort == 0 and .ToPort == 65535) | .IpRanges[] | select(.CidrIp == \"$local_ip/32\")")

if [ -z "$ip_exists" ]; then
    # Add the local public IP to the security group
    aws ec2 authorize-security-group-ingress --group-id sg-0a2f9548b660b5d0a --protocol tcp --port 0-65535 --cidr $local_ip/32 --region eu-north-1 > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        log "Failed to add IP to security group."
        exit 1
    else
        log "Successfully added IP to security group."
    fi

    # Add ICMP rule to the security group for IPv4
    aws ec2 authorize-security-group-ingress --group-id sg-0a2f9548b660b5d0a --protocol icmp --icmp-type-code "echo-request" --cidr $local_ip/32 --region eu-north-1 > /dev/null 2>&1
    if [ $? -ne 0 ]; then
        log "Failed to add ICMP rule to security group."
        exit 1
    else
        log "Successfully added ICMP rule to security group."
    fi
fi

# User data script to initialize the EBS volume
user_data=$(cat <<EOF
#!/bin/bash
cd /home/ubuntu/stable-diffusion-webui && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/kohya_ss && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/ComfyUI && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
find /home/ubuntu/stable-diffusion-webui/extensions -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
find /home/ubuntu/ComfyUI/custom_nodes -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
sudo apt-get update
nohup fio --filename=/dev/nvme0n1 --rw=read --bs=1M --iodepth=32 --ioengine=libaio --direct=1 --name=volume-initialize &
EOF
)

# Check if an instance with the tag 'sd' is already running
existing_instance_id=$(aws ec2 describe-instances \
    --region eu-north-1 \
    --filters "Name=tag:sd,Values=*" \
    | jq -r '.Reservations[].Instances[] | select(.State.Name=="running") | .InstanceId' | head -n 1)

if [ $? -ne 0 ]; then
    log "Failed to describe instances. Quitting"
    exit 1
fi

if [ -n "$existing_instance_id" ]; then
    log "An instance from the launch template is already running. Instance ID: $existing_instance_id"
    instance_id=$existing_instance_id
    # Retrieve the public IP address of the existing instance
    ip_address=$(aws ec2 describe-instances \
        --region eu-north-1 \
        --instance-ids $instance_id \
        | jq -r '.Reservations[0].Instances[0].PublicIpAddress')
    if [ "$ip_address" == "null" ]; then
        log "Existing instance does not have a public IP address. Exiting."
        exit 1
    fi
else
    # Run the instance and get the instance ID
    instance_id=$(aws ec2 run-instances \
        --region eu-north-1 \
        --launch-template LaunchTemplateId=lt-0e096fddfc03f20b6 \
        --user-data "$user_data" \
        | jq -r '.Instances[0].InstanceId')

    # Check if instance_id is null or empty
    if [ -z "$instance_id" ]; then
        log "Failed to create instance."
        exit 1
    fi

    # Wait for the instance to be in a running state
    log "Waiting for the instance to be in a running state..."

    counter=0
    while true; do
        instance_state=$(aws ec2 describe-instances \
            --region eu-north-1 \
            --instance-ids $instance_id \
            | jq -r '.Reservations[0].Instances[0].State.Name')

        if [ "$instance_state" == "running" ]; then
            log "Instance is running."
            
            # Check instance status
            instance_status=$(aws ec2 describe-instance-status \
                --region eu-north-1 \
                --instance-ids $instance_id \
                | jq -r '.InstanceStatuses[0].InstanceStatus.Status')

            if [ $? -ne 0 ]; then
                log "Failed to describe instance status. Quitting"
                exit 1
            fi

            log "Instance status: $instance_status"
            break
        elif [ "$instance_state" == "pending" ]; then
            sleep 10
            counter=$((counter+1))
            if [ $counter -gt 30 ]; then
                log "Instance is not in running state after 5 minutes. Exiting."
                exit 1
            fi
        else
            log "Instance is in an unexpected state: $instance_state"
            exit 1
        fi
    done

    # Wait for the public IP address to become available
    counter=0
    while true; do
        ip_address=$(aws ec2 describe-instances \
            --region eu-north-1 \
            --instance-ids $instance_id \
            | jq -r '.Reservations[0].Instances[0].PublicIpAddress')

        if [ "$ip_address" != "null" ]; then
            log "Public IP address is available."
            break
        fi

        sleep 10
        counter=$((counter+1))
        if [ $counter -gt 30 ]; then
            log "Public IP address still not available. Exiting."
            exit 1
        fi
    done
fi

# Update the SSH config file
if sed -i '' "/^Host sd$/,/^$/s/HostName .*/HostName $ip_address/" ~/.ssh/config; then
    log "SSH config file updated successfully."
else
    log "Failed to update SSH config file."
    exit 1
fi

# Connect to the instance
counter=0
while true; do
    if ping -c 1 $ip_address &> /dev/null; then
        if ssh sd -L 7860:localhost:7860; then
            log "Successfully connected to the instance."
            break
        else
            log "Failed to connect to the instance."
            exit 1
        fi
    else
        counter=$((counter+1))
        if [ $counter -gt 3 ]; then
            log "Failed to connect to the instance after 3 attempts. Please check your network and try again."
            exit 1
        fi
        log "Ping unsuccessful. Waiting for 20 seconds before trying again."
        sleep 20
    fi
done