Overview
This script is designed to automate the process of launching an Stable Diffusion server on EC2.
- Updates an AWS security group with the user's current public IP address
- Spins up a spot instance g5.4xlarge
- Initializes the EBS volume
- Updates various Git repositories on the instance.
  
Features
Logging: Captures all actions in a log file.
AWS CLI Check: Verifies if AWS CLI is properly configured before proceeding.
IP Management: Retrieves the local public IP and updates the AWS security group if the IP has changed.
Instance Initialization: Runs user data script to update Git repositories and initialize EBS volume.
Instance Management: Launches an EC2 instance and waits until it's in a running state with a public IP.
SSH Configuration: Updates the SSH config file with the new instance's IP for easy access.

Prerequisites
AWS CLI installed and configured with the necessary permissions.
jq installed for JSON parsing.
curl installed for IP retrieval.
An existing AWS security group and launch template.

Usage
Set up the script:
Place the script in a secure directory.
Ensure the script has execute permissions: chmod +x start-sd.sh.
Run the script:
Execute the script: ./start-sd.sh.
Monitor the log start-sd.log file for progress and errors.
SSH into the instance:
Once the script has completed, use SSH to connect to the instance: ssh sd.

Configuration
Modify the logfile variable to change the log file location.
Update the security group ID and region as per your AWS setup.
Adjust the launch template ID to match your configuration.

Troubleshooting
Check the log file for detailed error messages.
Ensure all prerequisites are met and correctly configured.
Verify that your AWS user has the necessary permissions to perform the actions in the script.
