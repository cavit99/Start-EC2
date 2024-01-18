# EC2 Instance Starter

# AWS EC2 Instance Initialization and Secure Connection via AWS SSM

This repository contains a Python script for automating the initialization of Amazon EC2 spot instances, providing a cost-effective solution with potential savings of up to 80% compared to on-demand prices. The script leverages the boto3 library to launch instances from a pre-existing Amazon Machine Image (AMI) using a user-defined launch template. It's particularly useful for setting up private generative AI servers, such as Stable Diffusion, while also offering significant cost savings.

## Key Features

- **Secure Connection via AWS Systems Manager (SSM)**: The script sets up a secure connection using AWS SSM, eliminating the need for traditional SSH keys and AWS security groups. This feature enhances security and simplifies access management.

- **Port Forwarding Configuration**: The script can configure port forwarding, allowing seamless interaction with AI servers as if they were local. This feature is especially beneficial for developers needing a local-like environment for testing and deployment.

- **Flexible Network Design**: The script supports the creation of a highly secure instance within a private Virtual Private Cloud (VPC) without an internet gateway using AWS PrivateLink. However, this is optional, and users can choose to have their instance within a VPC that includes an internet gateway if they prefer.

- **Optional Use of AWS PrivateLink**: AWS PrivateLink usage is optional and can be customized to meet specific security and connectivity requirements. This feature enables private service access even in environments without direct internet access.

Please refer to the documentation for detailed instructions on how to use the script and customize its features to suit your needs.

Sources
[1] Connect using EC2 Instance Connect https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-methods.html
[2] Troubleshoot connecting to your instance https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/TroubleshootingInstancesConnecting.html
[3] Connect to your Linux instance with EC2 Instance Connect https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/connect-linux-inst-eic.html
[4] Install EC2 Instance Connect on your EC2 instances https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-set-up.html
[5] Security in Amazon EC2 https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-security.html

By Perplexity at https://www.perplexity.ai/search/30ae620b-b6a8-4655-af6c-0e4d50d735b2.

## Getting Started

These instructions will guide you on how to use this script for starting and connecting to an EC2 instance.

## Prerequisites

Before you start, make sure you have:

- An AWS account
- [AWS CLI installed and configured](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- [Install AWS System Manager plug-in](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- [A predefined EC2 launch template](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/create-launch-template.html)
- An AMI you can use to launch, which already has SSM Agent
- [Ensure your launch template will attach to the instance an IAM role with SSM permissions](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-getting-started-instance-profile.html)
- Appropriate IAM permissions


## Installation

1. Clone the repo
```sh
git clone https://github.com/cavit99/Start-EC2.git
```


2. Install the required packages
```sh
pip install -r requirements.txt
```

## Usage

Run the script
```sh
python start_ec2.py
```

Follow the prompts to select your launch template and start the instance.

## Threading Logic

This script uses Python's threading module to handle the output of the SSM port forwarding session in real-time. This is achieved by creating a secondary thread that runs alongside the main thread.

1. Main Thread: This is the primary thread that runs the main function and controls the overall flow of the script.

2. Secondary Thread: This thread is created to handle the output of the SSM port forwarding session in real-time. It runs a function called handle_output, which reads the output line by line and logs it. It also sets an event when the port forwarding session is ready.

This threading logic allows the script to handle the output of the port forwarding session in real-time without blocking the main thread. It follows good practices such as separation of concerns, non-blocking I/O, error handling, and thread safety.

## Recommended Launch Template User Data
```sh
#!/bin/bash
sudo apt-get update
cd /home/ubuntu/stable-diffusion-webui && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/kohya_ss && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
cd /home/ubuntu/ComfyUI && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull
find /home/ubuntu/stable-diffusion-webui/extensions -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
find /home/ubuntu/ComfyUI/custom_nodes -maxdepth 2 -type d -exec sh -c 'cd {} && git rev-parse --is-inside-work-tree > /dev/null 2>&1 && git pull' \;
nohup fio --filename=/dev/nvme0n1 --rw=read --bs=1M --iodepth=32 --ioengine=libaio --direct=1 --name=volume-initialize &
```

## Contributing

Contributions are what make the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are greatly appreciated.

1. Fork the Project
2. Create your Feature Branch (git checkout -b feature/AmazingFeature)
3. Commit your Changes (git commit -m 'Add some AmazingFeature')
4. Push to the Branch (git push origin feature/AmazingFeature)
5. Open a Pull Request

## License

Distributed under the MIT License. See LICENSE for more information.

## Contact

Cavit Erginsoy - cavit@erginsoy.com

Project Link: https://github.com/cavit99/Start-EC2
